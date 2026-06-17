"""Guardrail (input/output moderation) port.

Defines the contract between orchestration and any guardrail provider
(LocalGuardrail today; OpenAI Moderation / AzureContentSafety /
NullGuardrail tomorrow). Orchestration imports ONLY from this module —
never from a concrete provider in ``infrastructure.guardrails`` — so
swap-ability is preserved (CLAUDE.md Strategy + DI mindset, sacred rule).

Why ``GuardrailHit`` + ``GuardrailBlocked`` live here, not in the
infrastructure provider: orchestration needs to ``raise GuardrailBlocked``
to short-circuit the pipeline and inspect ``GuardrailHit`` fields to log
rule_id / severity / action. Pulling these symbols up to the port keeps
``query_graph`` independent of any specific provider. The legacy
``infrastructure.guardrails.local_guardrail`` module re-exports both
symbols so existing imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Protocol, runtime_checkable
from uuid import UUID

from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CHECK_THRESHOLD,
    DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
)
from ragbot.shared.types import ModerationResultKind, TenantId


@dataclass(frozen=True, slots=True)
class ModerationOutcome:
    kind: ModerationResultKind  # safe | blocked | flagged
    reason: str | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GuardrailHit:
    """One rule's verdict against an input or output."""

    rule_id: str
    severity: str  # info | warn | block
    action: str    # allow | redact | block | hitl
    details: dict[str, Any] = field(default_factory=dict)


class GuardrailBlocked(Exception):
    """Raised when any rule returns severity='block'.

    Orchestration catches this to short-circuit the pipeline and emit
    the bot's OOS template instead of running the LLM call.
    """

    def __init__(self, hits: list[GuardrailHit]) -> None:
        self.hits = hits
        rules = ",".join(h.rule_id for h in hits if h.severity == "block")
        super().__init__(f"guardrail_blocked: {rules}")


@runtime_checkable
class GuardrailPort(Protocol):
    """Public guardrail surface used by orchestration.

    The legacy ``moderate_input`` / ``moderate_output`` /
    ``detect_prompt_injection`` / ``check_canary_leak`` methods stay for
    backward compat with callers that go through the simpler
    ``ModerationOutcome`` shape (HTTP middleware, ingest pipeline).

    The ``check_input`` / ``check_output`` methods are the orchestration-
    facing surface — they iterate ALL configured rules (DB-loaded +
    static), persist per-rule audit rows, and raise
    :class:`GuardrailBlocked` when any rule's severity == "block".
    """

    # ---- legacy moderation surface --------------------------------------
    async def moderate_input(
        self, text: str, *, record_tenant_id: TenantId,
    ) -> ModerationOutcome: ...

    async def moderate_output(
        self, text: str, *, record_tenant_id: TenantId,
    ) -> ModerationOutcome: ...

    async def detect_prompt_injection(self, text: str) -> bool: ...

    async def check_canary_leak(self, output: str, canary: str) -> bool: ...

    # ---- orchestration-facing rule batch surface ------------------------
    async def check_input(
        self,
        text: str,
        *,
        tenant_id: UUID | None,
        message_id: int,
        request_id: UUID | None = None,
    ) -> list[GuardrailHit]: ...

    async def check_output(
        self,
        answer: str,
        *,
        system_prompt_hash: str | list[str] | None = None,
        shingle_size: int = 8,
        retrieved_chunks: list[Any] | None = None,
        tenant_id: UUID | None,
        message_id: int,
        request_id: UUID | None = None,
        grounding_check_enabled: bool = False,
        grounding_check_threshold: float = DEFAULT_GROUNDING_CHECK_THRESHOLD,
        citation_marker_required: bool = False,
        llm_complete_fn: Callable[..., Coroutine[Any, Any, dict]] | None = None,
        structured_judge_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        grounding_use_structured: bool = False,
        oos_template: str | None = None,
        oos_similarity_threshold: float = DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
    ) -> list[GuardrailHit]: ...


__all__ = [
    "GuardrailBlocked",
    "GuardrailHit",
    "GuardrailPort",
    "ModerationOutcome",
]
