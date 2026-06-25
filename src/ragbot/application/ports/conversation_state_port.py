"""ConversationStatePort — per-conversation structured state for multi-turn HALLU prevention.

Defines the contract between orchestration (`extract_and_validate_slots`
node + post-generate drift detect) and any state-tracking backend
(Null = OFF default, JSONB = DB-backed today, future Redis hot-cache etc).

Why this port exists
~~~~~~~~~~~~~~~~~~~~
Ragbot is RAG stateless: each turn LLM re-infers state from prose
``conversation_history`` → drift accross turns. A multi-turn UI test
(2026-05-30 baseline) measured a 15% HALLU rate via 3 patterns:

- BP-1 entity name fusion (fabricate a name from 2 corpus entries)
- BP-2 numeric flip-flop (same entity quoted value V1 turn 4, V2 turn 7)
- BP-3 attribute cross-entity (assign entity A's attribute to entity B)

Single-turn load test (12Q/30Q with hash-based connect_id) cannot catch
these patterns — fresh connect_id per query → 0 history → 0 drift.

The fix is architectural: persist structured state in DB, inject into
prompt via existing sysprompt rule template pattern (rule 20
STATE_ENFORCEMENT in language_packs platform tier). LLM reads state via
existing prompt render path; application code does NOT prepend text or
override LLM output (sacred rule preservation).

Drift handling reuses Phase 3 ``GuardrailHit`` + ``GuardrailBlocked`` so
existing OOS refuse flow handles block via ``bots.oos_answer_template``.

Sacred-rule alignment
~~~~~~~~~~~~~~~~~~~~~
- Domain-neutral: port surface only uses generic concepts
  (conversation_id, state dict, drift hits). No tenant/industry/booking
  text in code.
- Strategy + DI: Protocol contract; implementations live in
  ``infrastructure/conversation_state/`` (Null + Jsonb).
- Multi-tenant: per-conversation state inherits RLS via
  ``conversations.workspace_id`` from alembic 0141.
- HALLU=0 first-class: structured state survives turns; LLM cannot
  silently override via top-chunk bias.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ragbot.application.ports.guardrail_port import GuardrailHit


@runtime_checkable
class ConversationStatePort(Protocol):
    """Contract for loading/saving structured conversation state + drift detect.

    Implementations:
    - :class:`NullConversationState` — default OFF, no-op. Used when
      ``bots.action_config.enabled=false`` or registry resolves to
      ``"null"`` provider.
    - :class:`JsonbConversationState` — DB-backed via
      ``conversations.action_state`` JSONB column (alembic 0150).
    """

    async def load_state(self, *, conversation_id: UUID | None) -> dict[str, Any]:
        """Load prior state for this conversation. Empty dict if first turn / no state.

        Implementations MUST tolerate ``conversation_id is None`` (return ``{}``)
        so first-turn callers don't need to special-case.
        """
        ...

    async def save_state(
        self,
        *,
        conversation_id: UUID | None,
        state: dict[str, Any],
    ) -> None:
        """Persist updated state for this conversation.

        Implementations MUST tolerate ``conversation_id is None`` (no-op)
        so the orchestration node can call unconditionally.
        """
        ...

    async def detect_drift(
        self,
        *,
        prior_state: dict[str, Any],
        proposed_answer: str,
        chunks: list[dict[str, Any]],
    ) -> list[GuardrailHit]:
        """Inspect proposed LLM answer for drift vs prior state.

        Returns list of GuardrailHit (Phase 3 Port type). Caller handles:
        - severity="warn" → log + add to ``guardrail_flags`` (audit)
        - severity="block" → raise ``GuardrailBlocked``; existing OOS
          refuse flow substitutes ``bots.oos_answer_template`` (no
          application-side override).

        Empty list = no drift detected.
        """
        ...


__all__ = ["ConversationStatePort"]
