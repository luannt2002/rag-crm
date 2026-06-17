"""Universal PII redaction helper — Phase D2 compliance coverage.

Extends the chat-query + ingest-content boundary hooks (Phase S5) to ALL
persistence surfaces:

  * ``audit_log``       — ``before`` / ``after`` / ``reason`` JSON payloads
  * ``request_steps``   — step ``metadata`` dict + ``error`` text
  * ``telemetry``       — structured event payloads emitted out-of-band

Per-bot opt-in. Two toggles compose:

  1. ``plan_limits.pii_redaction_enabled``    — global PII gate
  2. ``plan_limits.pii_redaction_universal``  — extends gate to non-chat
     surfaces; default False so existing tenants see ZERO behaviour change

The redactor is supplied by DI (``Container.pii``). Failure modes degrade
silent (CLAUDE.md graceful-degradation rule) — a misconfigured redactor
must never 5xx the producing path. Audit event ``pii_redacted`` carries
ONLY the mask_count + per-type histogram (NEVER raw PII).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from ragbot.application.ports.pii_redactor_port import PiiRedactorPort
from ragbot.shared.bot_limits import resolve_bot_limit
from ragbot.shared.constants import (
    PII_REGEX_BANK_ACC,
    PII_REGEX_CCCD,
    PII_REGEX_CMND,
    PII_REGEX_EMAIL,
    PII_REGEX_PHONE_VN,
    PII_REGEX_PHONE_VN_INTL,
)

logger = structlog.get_logger(__name__)

# Sentinel — anything larger than this is silently dropped to avoid a
# pathological regex blow-up on user-supplied free-form text. Aligns with
# the chat-worker question_text size guard (KB-level, not MB-level).
_MAX_REDACT_INPUT_CHARS: int = 200_000

# ── Surface tags ──────────────────────────────────────────────────────
# Stable string identifiers for the structured ``pii_redacted`` audit
# event. The compliance auditor slices events by ``surface`` so the
# value MUST stay snake_case + stable across releases. Adding a new
# surface = adding a new constant here (NOT inline magic strings).
PII_SURFACE_AUDIT_LOG: str = "audit_log"
PII_SURFACE_REQUEST_STEPS: str = "request_steps"
PII_SURFACE_TELEMETRY: str = "telemetry"
PII_SURFACE_CHAT_QUERY: str = "chat_query"
PII_SURFACE_INGEST_CONTENT: str = "ingest_content"

# ── Per-bot opt-in flag key ───────────────────────────────────────────
# ``plan_limits`` JSONB column key for the universal-coverage gate.
# Both this AND ``pii_redaction_enabled`` MUST be True for universal
# surfaces (audit / steps / telemetry) to mask. Defaults False so
# existing tenants see ZERO behaviour change.
PII_UNIVERSAL_FLAG_KEY: str = "pii_redaction_universal"

# Narrow exception envelope for the redactor call. Covers:
#   - re.error           (broken pattern in custom provider)
#   - TypeError          (non-string input slipped past isinstance guard)
#   - ValueError         (provider rejects input shape)
#   - AttributeError     (redactor stub missing .redact / .get_provider_name)
#   - LookupError        (custom provider walks a missing key)
#   - RuntimeError       (presidio analyzer pipeline failure)
_REDACT_FAILURES: tuple[type[BaseException], ...] = (
    re.error,
    TypeError,
    ValueError,
    AttributeError,
    LookupError,
    RuntimeError,
)


# ── DefaultRedactor — plain-regex provider for universal surfaces ─────
# Distinct from :class:`VnRegexPiiRedactor` (infrastructure adapter that
# tags spans as ``[CCCD]``, ``[PHONE]``, etc.). The universal-coverage
# surfaces (audit / steps / telemetry) need a self-describing mask
# format ``[REDACTED_<TYPE>]`` so a compliance auditor reading a JSONB
# row sees the redaction at a glance, without consulting a tag glossary.
#
# Pattern priority — higher-specificity classes come BEFORE numeric
# classes that share the same digit alphabet so the
# ``(start, -length)`` sort with stable insertion order picks the
# specific class on equal-length overlaps:
#
#   EMAIL → PHONE_VN → PHONE_VN_INTL → CCCD → CMND → BANK_ACC
#
# Test ``test_cccd_beats_bank_acc_on_overlap`` locks the CCCD-before-
# BANK_ACC rule (both 12-digit on same span); test
# ``test_phone_vn_redact`` locks PHONE-before-BANK_ACC (PHONE 10-11
# digits with leading 0 → would equal-length-overlap a 10-16 BANK_ACC
# run that happens to start with 0).
_DEFAULT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(PII_REGEX_EMAIL)),
    ("PHONE", re.compile(PII_REGEX_PHONE_VN_INTL)),
    ("PHONE", re.compile(PII_REGEX_PHONE_VN)),
    ("CCCD", re.compile(PII_REGEX_CCCD)),
    ("CMND", re.compile(PII_REGEX_CMND)),
    ("BANK_ACC", re.compile(PII_REGEX_BANK_ACC)),
)


class DefaultRedactor:
    """Plain-regex PII redactor for universal observability surfaces.

    Implements :class:`PiiRedactorPort`. Returns
    ``(redacted_text, [{"type", "start", "end"}, ...])`` per the Strategy
    contract. Mask format is ``[REDACTED_<TYPE>]`` (audit-friendly,
    self-describing) — different from :class:`VnRegexPiiRedactor` which
    uses ``[<TYPE>]`` for the chat boundary.

    Overlap resolution mirrors VnRegex: collect every match, sort by
    ``(start, -length)`` so the longer span wins; equal-length spans at
    the same offset preserve insertion order so the earlier-listed
    pattern (more specific class) wins.
    """

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "default_regex"

    def redact(self, text: str) -> tuple[str, list[dict]]:
        if not text:
            return text, []
        entities: list[dict] = []
        for kind, pat in _DEFAULT_PATTERNS:
            for m in pat.finditer(text):
                entities.append({
                    "type": kind,
                    "start": m.start(),
                    "end": m.end(),
                })
        entities.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))
        if not entities:
            return text, []
        out_parts: list[str] = []
        cursor = 0
        emitted: list[dict] = []
        for ent in entities:
            if ent["start"] < cursor:
                continue
            out_parts.append(text[cursor:ent["start"]])
            out_parts.append(f"[REDACTED_{ent['type']}]")
            cursor = ent["end"]
            emitted.append(ent)
        out_parts.append(text[cursor:])
        return "".join(out_parts), emitted


def universal_redaction_enabled(bot_cfg: Any) -> bool:
    """Return True iff this bot opted into universal PII coverage.

    Both toggles MUST be True — universal coverage extends the chat/ingest
    boundary, it does not replace it. A bot owner setting only
    ``pii_redaction_universal=True`` without enabling the base
    ``pii_redaction_enabled`` toggle is treated as "off" so the two
    columns compose monotonically.
    """
    if bot_cfg is None:
        return False
    base = resolve_bot_limit(bot_cfg, "pii_redaction_enabled", system_default=False)
    if not base:
        return False
    return bool(
        resolve_bot_limit(bot_cfg, PII_UNIVERSAL_FLAG_KEY, system_default=False),
    )


def _provider_name(redactor: Any) -> str:
    """Resolve the provider tag with a narrow-except envelope.

    A misbehaving stub MUST NOT block redaction. ``AttributeError`` is the
    expected failure (Protocol not implemented); ``TypeError`` covers
    callables that take wrong arg counts.
    """
    try:
        return str(redactor.get_provider_name())
    except (AttributeError, TypeError):
        return "unknown"


def _safe_redact(
    redactor: Any,
    text: str,
    *,
    surface: str,
) -> tuple[str, list[dict]]:
    """Run ``redactor.redact`` with graceful-degradation envelope.

    Returns ``(text, [])`` on any redactor failure so callers can keep
    the original value and continue persisting. Logs a structured
    ``pii_redaction_failed`` event so the operator can spot a broken
    provider.
    """
    if not text:
        return text, []
    if len(text) > _MAX_REDACT_INPUT_CHARS:
        # Defence vs catastrophic backtracking. Skip and log so the
        # producer still persists the row — universal coverage is
        # best-effort, NOT a hard validator.
        logger.info(
            "pii_redaction_skipped_oversize",
            surface=surface,
            text_len=len(text),
        )
        return text, []
    try:
        masked, entities = redactor.redact(text)
    except _REDACT_FAILURES as exc:
        logger.warning(
            "pii_redaction_failed",
            surface=surface,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return text, []
    return masked, entities or []


def _emit_audit(
    *,
    surface: str,
    entities: list[dict],
    record_tenant_id: Any,
    record_bot_id: Any,
    provider: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit the structured ``pii_redacted`` event.

    The event payload is the SINGLE compliance signal — it MUST carry
    only the mask count + per-type histogram so an auditor can verify
    "row N had K masks of type T" without ever seeing the raw PII.
    """
    if not entities:
        return
    mask_types: dict[str, int] = {}
    for ent in entities:
        kind = str(ent.get("type", "UNKNOWN"))
        mask_types[kind] = mask_types.get(kind, 0) + 1
    payload: dict[str, Any] = {
        "surface": surface,
        "record_tenant_id": str(record_tenant_id) if record_tenant_id is not None else None,
        "record_bot_id": str(record_bot_id) if record_bot_id is not None else None,
        "mask_count": len(entities),
        "mask_types": mask_types,
        "provider": provider,
    }
    if extra:
        payload.update(extra)
    logger.info("pii_redacted", **payload)


def redact_text(
    text: str | None,
    *,
    redactor: Any,
    bot_cfg: Any,
    surface: str,
    record_tenant_id: Any = None,
    record_bot_id: Any = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Redact a single text value if the bot opted into universal coverage.

    @param text: the raw text to mask. ``None`` / empty passes through.
    @param redactor: ``PiiRedactorPort`` (NullPiiRedactor when DI default).
    @param bot_cfg: BotConfig DTO carrying ``plan_limits``.
    @param surface: one of ``PII_SURFACE_*`` constants — used in the
        ``pii_redacted`` audit event for compliance slicing.
    @return: masked text when the toggle is on and matches found, OR
        the unchanged input otherwise.
    """
    if text is None or not text:
        return text
    if redactor is None or bot_cfg is None:
        return text
    if not universal_redaction_enabled(bot_cfg):
        return text
    masked, entities = _safe_redact(redactor, text, surface=surface)
    if not entities:
        return text
    _emit_audit(
        surface=surface,
        entities=entities,
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
        provider=_provider_name(redactor),
        extra=extra,
    )
    return masked


def redact_mapping(
    payload: dict[str, Any] | None,
    *,
    redactor: Any,
    bot_cfg: Any,
    surface: str,
    record_tenant_id: Any = None,
    record_bot_id: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Recursively redact every string value inside a JSON-ish mapping.

    Used for audit ``before`` / ``after`` JSONB and step ``metadata``
    JSONB columns where free-form user text can leak. The dict is
    rebuilt (NOT mutated in-place) so callers that pass an immutable /
    shared mapping stay safe.

    Nested ``dict`` and ``list`` values recurse; everything else is
    passed through unchanged. The audit event is emitted ONCE per
    top-level call with the aggregated entity list across all branches
    so dashboards count "row N had K masks", not "K events per row".
    """
    if payload is None:
        return payload
    if redactor is None or bot_cfg is None:
        return payload
    if not universal_redaction_enabled(bot_cfg):
        return payload

    all_entities: list[dict] = []

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            if not value:
                return value
            if len(value) > _MAX_REDACT_INPUT_CHARS:
                logger.info(
                    "pii_redaction_skipped_oversize",
                    surface=surface,
                    text_len=len(value),
                )
                return value
            try:
                masked, entities = redactor.redact(value)
            except _REDACT_FAILURES as exc:
                logger.warning(
                    "pii_redaction_failed",
                    surface=surface,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return value
            if entities:
                all_entities.extend(entities)
                return masked
            return value
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_walk(v) for v in value)
        return value

    masked_payload = _walk(payload)

    _emit_audit(
        surface=surface,
        entities=all_entities,
        record_tenant_id=record_tenant_id,
        record_bot_id=record_bot_id,
        provider=_provider_name(redactor),
        extra=extra,
    )

    return masked_payload


__all__ = [
    "DefaultRedactor",
    "PII_SURFACE_AUDIT_LOG",
    "PII_SURFACE_CHAT_QUERY",
    "PII_SURFACE_INGEST_CONTENT",
    "PII_SURFACE_REQUEST_STEPS",
    "PII_SURFACE_TELEMETRY",
    "PII_UNIVERSAL_FLAG_KEY",
    "PiiRedactorPort",
    "redact_mapping",
    "redact_text",
    "universal_redaction_enabled",
]
