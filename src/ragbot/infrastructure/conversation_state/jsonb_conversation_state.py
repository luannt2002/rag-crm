"""JsonbConversationState — DB-backed conversation state via ``conversations.action_state``.

Sacred-rule alignment:
- Domain-neutral: code reads/writes JSONB blob generically; the schema
  inside the blob is owner-declared via ``bots.action_config.slots_schema``.
- Multi-tenant: ``conversations`` table has RLS workspace-aware policy
  (alembic 0141). The async session lifts ``app.tenant_id`` +
  ``app.workspace_id`` GUCs (existing pattern) → policy enforces.
- Strategy + DI: registered as ``"jsonb"`` provider in registry.py.
- Graceful degradation: any DB failure logs and degrades to empty state
  rather than crashing the request hot path.

Drift detection contract
~~~~~~~~~~~~~~~~~~~~~~~~
Returns ``GuardrailHit`` so callers reuse the Phase 3 guardrail surface
(severity warn/block; ``GuardrailBlocked`` raise on block). The detector
currently checks two invariants on the proposed answer text:

1. **Service lock**: if ``prior_state["service_locked"]["name"]`` is set,
   the proposed answer must not introduce a *different* service name
   that also appears in the retrieved chunks (heuristic — exact name
   match in chunks → "real" service vs hallucinated lai).
2. **Price lock**: if ``prior_state["service_locked"]["price_buoi_le"]``
   is set, the proposed answer must not quote a *different* numeric
   price near the locked service name (regex-extracted).

Both invariants are heuristic — full enforcement uses ``severity="warn"``
default; per-bot override via ``bots.action_config.drift_detection`` can
raise to ``"block"`` once owner verifies the pattern on their corpus.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from ragbot.application.ports.conversation_state_port import ConversationStatePort
from ragbot.application.ports.guardrail_port import GuardrailHit
from ragbot.shared.constants import (
    ACTION_STATE_ALLOWED_TOP_KEYS,
    DEFAULT_CONVERSATION_STATE_TTL_HOURS,
    DEFAULT_MAX_ACTION_SLOTS,
)

logger = structlog.get_logger(__name__)


# Price extraction regex — matches Vietnamese price formats appearing in
# corpus: "199K", "199k", "199.000", "199.000đ", "199 000", "1.199.000".
# Used to spot price flip-flop drift (BP-2) in proposed answers.
_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d+)?)\s*(?:[kK]|đ|VND|đồng)?",
)


class JsonbConversationState(ConversationStatePort):
    """DB-backed state via ``conversations.action_state`` JSONB column.

    Constructor injection of ``session_factory`` keeps the strategy
    transport-agnostic. Production wires the SQLAlchemy session factory
    from :mod:`ragbot.bootstrap`; tests inject mocks.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        ttl_hours: int = DEFAULT_CONVERSATION_STATE_TTL_HOURS,
        max_slots: int = DEFAULT_MAX_ACTION_SLOTS,
    ) -> None:
        self._sf = session_factory
        self._ttl_hours = int(ttl_hours)
        self._max_slots = int(max_slots)

    async def load_state(
        self,
        *,
        conversation_id: UUID | None,
    ) -> dict[str, Any]:
        if conversation_id is None:
            return {}
        try:
            async with self._sf() as session:
                # TTL guard: a flow idle longer than ``ttl_hours`` (by
                # ``last_message_at``) is treated as expired → empty state so
                # the booking/lead flow restarts cleanly. ``0`` disables TTL.
                row = await session.execute(
                    text(
                        "SELECT action_state FROM conversations "
                        "WHERE id = :id "
                        "AND (:ttl <= 0 OR last_message_at IS NULL "
                        "     OR last_message_at > now() - make_interval(hours => :ttl)) "
                        "LIMIT 1",
                    ),
                    {"id": conversation_id, "ttl": self._ttl_hours},
                )
                value = row.scalar_one_or_none()
                if value is None:
                    return {}
                if isinstance(value, dict):
                    return value
                if isinstance(value, str):
                    return json.loads(value) if value else {}
                return {}
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.warning(
                "conversation_state_load_failed",
                conversation_id=str(conversation_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {}

    async def save_state(
        self,
        *,
        conversation_id: UUID | None,
        state: dict[str, Any],
    ) -> None:
        if conversation_id is None:
            return
        clean = self._sanitize(state)
        try:
            async with self._sf() as session:
                await session.execute(
                    text(
                        """
                        UPDATE conversations
                        SET action_state = CAST(:s AS jsonb)
                        WHERE id = :id
                        """,
                    ),
                    {"id": conversation_id, "s": json.dumps(clean, ensure_ascii=False)},
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.warning(
                "conversation_state_save_failed",
                conversation_id=str(conversation_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _sanitize(self, state: dict[str, Any]) -> dict[str, Any]:
        """Bound the persisted blob: drop unknown top-level keys, drop null
        slots, cap ``slots_filled`` to ``max_slots`` — anti-bloat / anti-garbage
        so the extractor (or a hostile payload) cannot fatten action_state.
        """
        if not isinstance(state, dict):
            return {}
        out: dict[str, Any] = {}
        for key, val in state.items():
            if key not in ACTION_STATE_ALLOWED_TOP_KEYS:
                continue  # drop garbage / runtime-only keys (e.g. __drift_severity)
            if key == "slots_filled" and isinstance(val, dict):
                # keep non-null only, deterministic order, cap to max_slots
                non_null = {k: v for k, v in val.items() if v is not None}
                if len(non_null) > self._max_slots:
                    non_null = dict(list(non_null.items())[: self._max_slots])
                out[key] = non_null
            else:
                out[key] = val
        return out

    async def detect_drift(
        self,
        *,
        prior_state: dict[str, Any],
        proposed_answer: str,
        chunks: list[dict[str, Any]],
    ) -> list[GuardrailHit]:
        hits: list[GuardrailHit] = []
        if not prior_state or not proposed_answer:
            return hits

        # Per-rule severity map injected by orchestration generate node
        # from bots.action_config.drift_detection. Default = "warn".
        sev_map = prior_state.get("__drift_severity") or {}
        default_sev = sev_map.get("default", "warn") if isinstance(sev_map, dict) else "warn"

        def _sev(rule_id: str) -> str:
            if isinstance(sev_map, dict):
                return sev_map.get(rule_id, default_sev)
            return default_sev

        locked = prior_state.get("service_locked") or {}
        locked_name = locked.get("name")
        locked_price = locked.get("price_buoi_le")
        ans = proposed_answer.lower()

        # Service drift: proposed answer mentions a different service name
        # that also exists as literal in retrieved chunks (heuristic).
        if locked_name:
            locked_lower = str(locked_name).lower()
            for ch in chunks or []:
                preview = (ch.get("content") or ch.get("preview") or "").lower()
                for token in self._candidate_service_tokens(preview):
                    if (
                        token != locked_lower
                        and token in ans
                        and locked_lower not in ans
                    ):
                        _r_id = "conversation_state_service_drift"
                        hits.append(GuardrailHit(
                            rule_id=_r_id,
                            severity=_sev(_r_id),
                            action="hitl",
                            details={
                                "locked_name": locked_name,
                                "drift_token": token,
                            },
                        ))
                        break

        # Price drift: proposed answer quotes a different price than locked.
        if locked_price:
            prices_found = self._extract_prices(proposed_answer)
            normalised_locked = self._normalise_price(locked_price)
            divergent = [
                p for p in prices_found
                if abs(p - normalised_locked) > max(normalised_locked * 0.01, 1)
            ]
            if divergent and normalised_locked not in prices_found:
                _r_id = "conversation_state_price_drift"
                hits.append(GuardrailHit(
                    rule_id=_r_id,
                    severity=_sev(_r_id),
                    action="hitl",
                    details={
                        "locked_price": locked_price,
                        "prices_in_answer": divergent,
                    },
                ))

        return hits

    # ------------------------------------------------------------------ #
    # Heuristics                                                          #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _candidate_service_tokens(text: str) -> list[str]:
        """Extract candidate service-name tokens from chunk preview.

        Heuristic: take lines that look like CSV table rows with a
        service name column. Returns lowercased tokens.
        """
        tokens: set[str] = set()
        for line in (text or "").splitlines():
            # Match patterns like "4,Chăm sóc da thải độc da,800.000"
            # or "STT,Vùng triệt,...\n7,Cả chân,699000"
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit():
                name = parts[1].strip().lower()
                if 3 < len(name) < 80:
                    tokens.add(name)
        return list(tokens)

    @staticmethod
    def _extract_prices(text: str) -> list[int]:
        """Extract integer VND prices from text. Returns deduped list."""
        out: set[int] = set()
        for m in _PRICE_RE.finditer(text or ""):
            raw = m.group(1)
            digits = re.sub(r"[^\d]", "", raw)
            if not digits:
                continue
            try:
                n = int(digits)
            except ValueError:
                continue
            # Heuristic: prices in spa range 10K - 50M VND
            # Handle "199K" → 199000 case
            if 10 <= n < 1000:
                # If "K" or "k" follows in original (within 3 chars), treat as thousand
                end = m.end()
                tail = (text or "")[end:end + 3].lower()
                if "k" in tail:
                    n *= 1000
            if 10_000 <= n <= 50_000_000:
                out.add(n)
        return sorted(out)

    @staticmethod
    def _normalise_price(value: Any) -> int:
        """Normalise price value to integer VND."""
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            digits = re.sub(r"[^\d]", "", value)
            return int(digits) if digits else 0
        return 0


__all__ = ["JsonbConversationState"]
