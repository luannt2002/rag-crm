"""LLM Protocol (LiteLLM impl in infrastructure).

Ref: PLAN_06 §llm_port.py + DynamicLiteLLMRouter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.shared.types import Role, TenantId, TraceId


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: Role
    content: str
    name: str | None = None  # for tool messages
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)
    structured: BaseModel | None = None


@runtime_checkable
class LLMPort(Protocol):
    async def health_check(self) -> bool: ...

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
        response_schema: type[BaseModel] | None = None,
        draft_model: str | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> AsyncIterator[str]: ...

    async def refresh_routing(self) -> None:
        """Refresh model list from DB."""
        ...

    async def close(self) -> None: ...


__all__ = ["LLMMessage", "LLMPort", "LLMResponse"]
