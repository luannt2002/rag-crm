"""NullGuardrail — opt-out provider that allows everything.

Returned by the registry when ``system_config.guardrail_provider = "null"``
or when no provider is configured. Useful for:

- Tenants on a free tier that do not pay for the moderation step
- Local dev / CI where the guardrail repo is not provisioned
- Integration tests that exercise the rest of the pipeline without the
  moderation gate

The implementation honours :class:`GuardrailPort` exactly — every method
returns the "safe" / no-hit answer so orchestration code keeps running.
``check_input`` / ``check_output`` return empty lists (no rules fired,
nothing to persist, nothing to raise). NullGuardrail NEVER raises
:class:`GuardrailBlocked` because no rule has severity="block".

Sacred-rule alignment:
- Strategy + DI: ``NullGuardrail`` is registered as the ``"null"``
  strategy in :mod:`ragbot.infrastructure.guardrails.registry`.
- Graceful degradation: degrades silently (per memory pattern
  ``feedback_no_premature_observability``) — never logs warnings,
  never raises.
- Multi-tenant: stateless, no per-tenant config needed.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine
from uuid import UUID

from ragbot.shared.constants import DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT

from ragbot.application.ports.guardrail_port import (
    GuardrailHit,
    GuardrailPort,
    ModerationOutcome,
)
from ragbot.shared.types import TenantId


class NullGuardrail(GuardrailPort):
    """No-op guardrail. Every input / output passes."""

    # ---- legacy moderation surface --------------------------------------
    async def moderate_input(
        self,
        text: str,  # noqa: ARG002
        *,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> ModerationOutcome:
        return ModerationOutcome(kind="safe")

    async def moderate_output(
        self,
        text: str,  # noqa: ARG002
        *,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> ModerationOutcome:
        return ModerationOutcome(kind="safe")

    async def detect_prompt_injection(self, text: str) -> bool:  # noqa: ARG002
        return False

    async def check_canary_leak(self, output: str, canary: str) -> bool:  # noqa: ARG002
        return False

    # ---- orchestration-facing rule batch surface ------------------------
    async def check_input(
        self,
        text: str,  # noqa: ARG002
        *,
        tenant_id: UUID | None,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        request_id: UUID | None = None,  # noqa: ARG002
    ) -> list[GuardrailHit]:
        return []

    async def check_output(
        self,
        answer: str,  # noqa: ARG002
        *,
        system_prompt_hash: str | list[str] | None = None,  # noqa: ARG002
        shingle_size: int = 8,  # noqa: ARG002
        retrieved_chunks: list[Any] | None = None,  # noqa: ARG002
        tenant_id: UUID | None,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        request_id: UUID | None = None,  # noqa: ARG002
        grounding_check_enabled: bool = False,  # noqa: ARG002
        grounding_check_threshold: float = 0.3,  # noqa: ARG002
        citation_marker_required: bool = False,  # noqa: ARG002
        llm_complete_fn: Callable[..., Coroutine[Any, Any, dict]] | None = None,  # noqa: ARG002
        structured_judge_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,  # noqa: ARG002
        grounding_use_structured: bool = False,  # noqa: ARG002
        oos_template: str | None = None,  # noqa: ARG002
        oos_similarity_threshold: float = 0.85,  # noqa: ARG002
        leak_min_match_count: int = DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,  # noqa: ARG002
    ) -> list[GuardrailHit]:
        return []


__all__ = ["NullGuardrail"]
