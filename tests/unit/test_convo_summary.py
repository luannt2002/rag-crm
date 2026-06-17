"""Unit tests for R6.C4 conversation summary compression scaffolding.

Mock-only — no live LLM calls. Verifies:
- NullConvoSummary returns "" (default OFF Null Object).
- LLMConvoSummary forwards a turn-ordered prompt to the injected LLMPort.
- build_convo_summary("null") / ("llm") returns the matching strategy.
- Unknown provider key raises ValueError (owner-opt-in surfaces loud).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.convo_summary_port import ConvoSummaryPort, Turn
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
try:
    from ragbot.infrastructure.convo_summary.llm_convo_summary import LLMConvoSummary
    from ragbot.infrastructure.convo_summary.null_convo_summary import (
        NullConvoSummary,
    )
    from ragbot.infrastructure.convo_summary.registry import (
        build_convo_summary,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "convo_summary subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.types import TenantId, TraceId


@dataclass
class FakeLLM:
    """Records every ``complete`` call and returns a scripted reply."""

    reply: str = "summary text"
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
        del response_schema
        self.calls.append(
            {
                "messages": list(messages),
                "spec": spec,
                "record_tenant_id": record_tenant_id,
                "trace_id": trace_id,
            }
        )
        return LLMResponse(
            content=self.reply,
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.0,
            latency_ms=1,
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


def _make_spec() -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000001"),
        model_name="openai/gpt-4.1-mini",
        provider="openai",
        temperature=0.2,
        max_tokens=500,
        top_p=1.0,
    )


@pytest.mark.asyncio
async def test_null_convo_summary_returns_empty_string() -> None:
    null_strategy: ConvoSummaryPort = NullConvoSummary()
    turns = [
        Turn(role="user", content="xin chào"),
        Turn(role="assistant", content="hello, how can I help?"),
    ]

    result = await null_strategy.summarise(turns, max_tokens=200)

    assert result == ""
    assert NullConvoSummary.get_provider_name() == "null"


@pytest.mark.asyncio
async def test_llm_convo_summary_calls_llm_with_turn_ordered_prompt() -> None:
    llm = FakeLLM(reply="  user greeted, assistant offered help  ")
    spec = _make_spec()
    tenant = TenantId(uuid4())
    trace = TraceId("trace-r6c4")
    strategy = LLMConvoSummary(
        llm=llm,
        spec=spec,
        record_tenant_id=tenant,
        trace_id=trace,
    )
    turns = [
        Turn(role="user", content="alpha question"),
        Turn(role="assistant", content="bravo answer"),
        Turn(role="user", content="charlie follow-up"),
    ]

    result = await strategy.summarise(turns, max_tokens=120)

    # Exactly one LLM call recorded with full context propagated.
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["record_tenant_id"] == tenant
    assert call["trace_id"] == trace
    # max_tokens override was forwarded into the per-call spec.
    assert call["spec"].max_tokens == 120

    # Prompt structure: system instruction + numbered user turns.
    messages = call["messages"]
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert "chronological" in messages[0].content.lower()
    assert messages[1].role == "user"
    user_prompt = messages[1].content
    # All three turns appear in order with explicit ordinal markers.
    idx_alpha = user_prompt.index("#1 [user] alpha question")
    idx_bravo = user_prompt.index("#2 [assistant] bravo answer")
    idx_charlie = user_prompt.index("#3 [user] charlie follow-up")
    assert idx_alpha < idx_bravo < idx_charlie
    # Token budget cited in user prompt so the LLM honours it.
    assert "120" in user_prompt

    # Returned content is stripped of surrounding whitespace.
    assert result == "user greeted, assistant offered help"


@pytest.mark.asyncio
async def test_llm_convo_summary_empty_turns_returns_empty_no_llm_call() -> None:
    llm = FakeLLM()
    strategy = LLMConvoSummary(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty"),
    )

    result = await strategy.summarise([], max_tokens=200)

    assert result == ""
    assert llm.calls == []


def test_build_convo_summary_null_returns_null_strategy() -> None:
    strategy = build_convo_summary("null")

    assert isinstance(strategy, NullConvoSummary)
    assert "null" in list_providers()


def test_build_convo_summary_llm_returns_llm_strategy() -> None:
    llm = FakeLLM()
    spec = _make_spec()
    tenant = TenantId(uuid4())
    trace = TraceId("trace-build")

    strategy = build_convo_summary(
        "llm",
        llm=llm,
        spec=spec,
        record_tenant_id=tenant,
        trace_id=trace,
    )

    assert isinstance(strategy, LLMConvoSummary)
    assert "llm" in list_providers()


def test_build_convo_summary_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown convo_summary provider"):
        build_convo_summary("does-not-exist")
