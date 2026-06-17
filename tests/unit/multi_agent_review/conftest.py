from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
from ragbot.shared.types import TenantId, TraceId


@dataclass
class FakeLLM:
    """Deterministic stub that replays scripted replies in call order.

    Each entry is a string body parsed by `parser.parse_agent_response`.
    Tracks each (system, user) pair so tests can assert prompt content.
    """

    replies: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def health_check(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        del record_tenant_id, trace_id, response_schema
        idx = len(self.calls)
        if idx >= len(self.replies):
            raise AssertionError(
                f"FakeLLM ran out of scripted replies (call #{idx + 1})"
            )
        body = self.replies[idx]
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        self.calls.append(
            {
                "system": system,
                "user": user,
                "spec": spec,
            }
        )
        return LLMResponse(
            content=body,
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=200,
            tokens_out=100,
            cost_usd=0.0001,
            latency_ms=42,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def refresh_routing(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_spec() -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000001"),
        model_name="openai/gpt-4.1-mini",
        provider="openai",
        temperature=0.2,
        max_tokens=600,
        top_p=1.0,
    )


@pytest.fixture
def tenant_id() -> TenantId:
    return TenantId(uuid4())


@pytest.fixture
def trace_id() -> TraceId:
    return TraceId("test-trace-0001")


def make_reply(
    *,
    summary: str,
    issues: list[str] | None = None,
    suggestions: list[str] | None = None,
    risks: list[str] | None = None,
    verdict: str = "approved_with_fix",
) -> str:
    issues_block = "\n".join(f"- {i}" for i in (issues or [])) or "- none"
    suggestions_block = "\n".join(f"- {s}" for s in (suggestions or [])) or "- none"
    risks_block = "\n".join(f"- {r}" for r in (risks or [])) or "- none"
    return (
        f"SUMMARY: {summary}\n"
        f"ISSUES:\n{issues_block}\n"
        f"SUGGESTIONS:\n{suggestions_block}\n"
        f"RISKS:\n{risks_block}\n"
        f"VERDICT: {verdict}"
    )
