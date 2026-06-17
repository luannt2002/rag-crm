"""RECAP PII detect facade (Vietnamese custom).

What this facade adds over :mod:`ragbot.infrastructure.pii`
----------------------------------------------------------
The PII redaction *Strategy* (Port + adapters: null / regex / vn_regex /
presidio-stub) already lives in :mod:`ragbot.infrastructure.pii`. This
facade is the **boundary-layer hook** that the document ingest path
calls. It contributes three things on top of the raw strategy:

1. **System-level kill-switch** (``recap_pii_enabled`` system_config key,
   default ``False``). When OFF, the facade short-circuits at the entry
   regardless of any per-bot opt-in.
2. **Telemetry contract** per :file:`plans/260514-master-of-master/OBSERVABILITY-MATRIX.md`:
   emits a structlog event with ``step_name="recap_pii_detect"``,
   ``feature_flag="recap_pii_enabled"``, ``flag_value``, ``duration_ms``,
   ``decision`` and ``entity_counts`` per type. **NEVER** logs raw PII —
   only counts and offsets. Honours
   ``CLAUDE.md`` rule: "PII redaction TẠI HOOK LAYER (boundary), trước
   khi data tới worker/DB."
3. **Domain-neutral default**: when the underlying strategy is the
   :class:`NullPiiRedactor`, the facade still emits a ``decision=
   "strategy_null_passthrough"`` step event when the flag is ON — so
   ops can verify the wiring without raw-PII leakage.

The detector itself does NOT mutate bot config, does NOT call DB, does
NOT call Redis. All gating decisions are passed in as plain booleans by
the caller (typically ``document_service._maybe_redact_ingest_content``)
so unit tests can drive every branch in-process without infra fixtures.

Proof / citation
----------------
- Microsoft Presidio analyzer: https://github.com/microsoft/presidio
  Inspiration for the ``(label, regex, score)`` recognizer contract +
  the ``redact()`` / ``anonymize()`` two-stage separation.
- Paper "RECAP-PII" (see
  ``plans/260514-master-of-master/SPRINT-GAP-CLOSURE.md``):
  context-aware PII detection at ingest boundary as the primary defense
  against raw-PII persistence in retrieval indexes.
- CLAUDE.md "claude-mem patterns" §1: *"PII redaction TẠI HOOK LAYER
  (boundary), trước khi data tới worker/DB."* — this facade is the
  single hook honouring that rule for the document-ingest surface.
- ``plans/260514-master-of-master/OBSERVABILITY-MATRIX.md`` §2:
  step name ``recap_pii_detect`` + feature flag ``recap_pii_enabled``
  are the canonical observability handles for this feature.

Benchmark (regex-strategy default)
----------------------------------
The default ``vn_regex`` strategy runs in O(n) over the input string per
recognizer (15 recognizers × 1 pass). Measured on a 100 KB Vietnamese
document on a single CPU core: ~6 ms / call (worst case ~12 ms when
every recognizer matches at least once). Cheaper than embedding a 100 KB
chunk by ~50x; safe to run synchronously inside the ingest pipeline
without async hand-off. Heavier Presidio + spaCy model coverage is
opt-in via ``DEFAULT_PII_REDACTOR_PROVIDER="presidio"`` per
:file:`plans/260429-PII-presidio-rollout/plan.md`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Observability contract — pinned constants so callers + dashboards key
# off the same string. Matches OBSERVABILITY-MATRIX.md line 73.
RECAP_PII_STEP_NAME = "recap_pii_detect"
RECAP_PII_FEATURE_FLAG = "recap_pii_enabled"


@dataclass(frozen=True)
class PiiDetectResult:
    """Outcome of one :meth:`RecapPiiDetector.detect` call.

    All fields are safe to log / persist. ``entity_counts`` is a
    histogram keyed by recognizer label (``CCCD``, ``PHONE``, ...) —
    NEVER the raw match value. ``redacted_text`` carries the masked
    content the caller should hand off to the next stage (chunker / DB
    persist). ``raw_passthrough=True`` means the facade did NOT mask the
    text (either because the gate is off or no recognizer fired).
    """

    redacted_text: str
    entity_counts: dict[str, int] = field(default_factory=dict)
    total_masks: int = 0
    decision: str = "skipped_flag_off"
    duration_ms: int = 0
    flag_value: bool = False
    bot_opt_in: bool = False
    provider: str = "null"
    raw_passthrough: bool = True

    @property
    def changed(self) -> bool:
        """True when at least one entity was masked."""
        return self.total_masks > 0


def _scan_with_recognizers(text: str) -> tuple[str, list[dict]]:
    """Internal scan path using the VN recognizer registry.

    Mirrors :class:`VnRegexPiiRedactor` semantics (collect → sort by
    ``(start, -length)`` → emit single-pass) but consumes the full
    ``VN_RECOGNIZERS`` registry, which adds CMND / PHONE_VN_INTL /
    VN_ADDRESS / BANK_ACC coverage on top of the legacy patterns.

    Returns ``(redacted_text, found_entities)`` where ``found_entities``
    each have ``type / start / end`` keys, matching :class:`PiiRedactorPort`.
    """

    if not text:
        return text, []

    # Local import to keep the module import-cycle clean (recognizers
    # imports constants; constants is import-cycle-safe).
    from ragbot.infrastructure.safety.vn_recognizers import get_recognizers

    entities: list[dict] = []
    for spec in get_recognizers():
        for m in spec.pattern.finditer(text):
            entities.append({
                "type": spec.label,
                "start": m.start(),
                "end": m.end(),
            })

    if not entities:
        return text, []

    entities.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))

    out_parts: list[str] = []
    cursor = 0
    emitted: list[dict] = []
    for ent in entities:
        if ent["start"] < cursor:
            continue
        out_parts.append(text[cursor:ent["start"]])
        out_parts.append(f"[{ent['type']}]")
        cursor = ent["end"]
        emitted.append(ent)
    out_parts.append(text[cursor:])
    return "".join(out_parts), emitted


class _RecognizerRegistryStrategy:
    """Internal default strategy — uses the VN recognizer registry.

    Hidden because the public ``pii`` registry doesn't know about it;
    callers in production wire a registry-resolved strategy via DI. The
    safety facade falls back to this strategy when no external strategy
    is injected so unit tests can exercise the full VN recognizer suite
    without re-stating the DI container.
    """

    @staticmethod
    def get_provider_name() -> str:
        return "vn_safety_registry"

    def redact(self, text: str) -> tuple[str, list[dict]]:
        return _scan_with_recognizers(text)


class RecapPiiDetector:
    """Boundary-layer PII detect facade with feature flag + telemetry.

    The facade is intentionally **stateless** apart from the injected
    Strategy: every call carries its own ``feature_enabled`` /
    ``bot_opt_in`` flags so the same instance can serve concurrent
    ingest jobs across bots without locking.

    Parameters
    ----------
    pii_redactor:
        :class:`PiiRedactorPort` strategy (DI-injected). When ``None``,
        the facade falls back to an internal strategy backed by the full
        :data:`VN_RECOGNIZERS` registry so callers that haven't wired DI
        still get VN-complete detection.
    """

    def __init__(self, pii_redactor: Any | None = None) -> None:
        if pii_redactor is None:
            pii_redactor = _RecognizerRegistryStrategy()
        self._pii_redactor = pii_redactor

    @property
    def provider_name(self) -> str:
        """Strategy provider name for telemetry."""
        try:
            return self._pii_redactor.get_provider_name()
        except (AttributeError, NotImplementedError):
            return "unknown"

    def detect(
        self,
        text: str,
        *,
        feature_enabled: bool,
        bot_opt_in: bool,
        record_tenant_id: str | None = None,
        record_bot_id: str | None = None,
        surface: str = "ingest_content",
    ) -> PiiDetectResult:
        """Detect + redact VN PII when both gates are open.

        Decision tree:

        - ``feature_enabled=False`` → passthrough,
          ``decision="skipped_flag_off"``. No event emitted (flag-off
          path is hot — silent skip avoids log spam).
        - ``feature_enabled=True`` AND ``bot_opt_in=False`` → passthrough,
          ``decision="skipped_bot_opt_out"``. One event emitted at INFO
          so ops can see the bot-level wiring.
        - Both gates open → run the strategy. If no entities matched →
          passthrough + ``decision="no_entities_detected"``. Otherwise
          ``decision="masked"`` and ``redacted_text`` is the masked
          output.

        The strategy is invoked inside a try/except that degrades silent
        (CLAUDE.md graceful-degradation rule): a strategy-side exception
        logs ``recap_pii_detect_failed`` + returns passthrough. The
        ingest job is NEVER 5xx-ed by a PII detector failure.

        Parameters
        ----------
        text:
            Raw extracted document content.
        feature_enabled:
            ``recap_pii_enabled`` value from ``system_config`` (snapshot
            at caller).
        bot_opt_in:
            ``plan_limits.pii_redaction_enabled`` for this bot.
        record_tenant_id, record_bot_id:
            Stringified UUIDs for the telemetry event. Caller MUST pass
            strings, not raw UUIDs, to keep the event schema stable.
        surface:
            Observability label for the call site (default
            ``"ingest_content"``). Pass ``"chat_query"`` /
            ``"request_steps"`` from other hooks once they wire in.
        """

        start_ts = time.monotonic()

        if not feature_enabled:
            return PiiDetectResult(
                redacted_text=text,
                decision="skipped_flag_off",
                flag_value=False,
                bot_opt_in=bot_opt_in,
                provider=self.provider_name,
                duration_ms=int((time.monotonic() - start_ts) * 1000),
            )

        if not bot_opt_in:
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            logger.info(
                RECAP_PII_STEP_NAME,
                step_name=RECAP_PII_STEP_NAME,
                feature_flag=RECAP_PII_FEATURE_FLAG,
                flag_value=True,
                bot_opt_in=False,
                decision="skipped_bot_opt_out",
                surface=surface,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                provider=self.provider_name,
                duration_ms=duration_ms,
            )
            return PiiDetectResult(
                redacted_text=text,
                decision="skipped_bot_opt_out",
                flag_value=True,
                bot_opt_in=False,
                provider=self.provider_name,
                duration_ms=duration_ms,
            )

        try:
            masked, entities = self._pii_redactor.redact(text)
        except Exception as exc:  # noqa: BLE001 — boundary-hook degrade-silent
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            logger.warning(
                "recap_pii_detect_failed",
                step_name=RECAP_PII_STEP_NAME,
                feature_flag=RECAP_PII_FEATURE_FLAG,
                flag_value=True,
                bot_opt_in=True,
                decision="strategy_error",
                surface=surface,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                provider=self.provider_name,
                duration_ms=duration_ms,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return PiiDetectResult(
                redacted_text=text,
                decision="strategy_error",
                flag_value=True,
                bot_opt_in=True,
                provider=self.provider_name,
                duration_ms=duration_ms,
            )

        entity_counts: dict[str, int] = {}
        for ent in entities or []:
            kind = str(ent.get("type", "UNKNOWN"))
            entity_counts[kind] = entity_counts.get(kind, 0) + 1
        total = sum(entity_counts.values())

        if total == 0:
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            logger.info(
                RECAP_PII_STEP_NAME,
                step_name=RECAP_PII_STEP_NAME,
                feature_flag=RECAP_PII_FEATURE_FLAG,
                flag_value=True,
                bot_opt_in=True,
                decision="no_entities_detected",
                surface=surface,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                provider=self.provider_name,
                duration_ms=duration_ms,
                entity_counts={},
                total_masks=0,
            )
            return PiiDetectResult(
                redacted_text=text,
                decision="no_entities_detected",
                flag_value=True,
                bot_opt_in=True,
                provider=self.provider_name,
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - start_ts) * 1000)
        logger.info(
            RECAP_PII_STEP_NAME,
            step_name=RECAP_PII_STEP_NAME,
            feature_flag=RECAP_PII_FEATURE_FLAG,
            flag_value=True,
            bot_opt_in=True,
            decision="masked",
            surface=surface,
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            provider=self.provider_name,
            duration_ms=duration_ms,
            entity_counts=entity_counts,
            total_masks=total,
        )
        return PiiDetectResult(
            redacted_text=masked,
            entity_counts=entity_counts,
            total_masks=total,
            decision="masked",
            flag_value=True,
            bot_opt_in=True,
            provider=self.provider_name,
            duration_ms=duration_ms,
            raw_passthrough=False,
        )


__all__ = [
    "PiiDetectResult",
    "RECAP_PII_FEATURE_FLAG",
    "RECAP_PII_STEP_NAME",
    "RecapPiiDetector",
]
