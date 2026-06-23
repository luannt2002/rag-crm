"""I-1 — narrate threads document language; block-prompts are not VN-hardcoded."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
from ragbot.infrastructure.narrate.llm_narrate import _BLOCK_PROMPTS, LLMNarrateGenerator
from ragbot.shared.types import TenantId, TraceId


@dataclass
class _CaptureLLM:
    reply: str = "A summary sentence."
    calls: list[list[LLMMessage]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
        response_schema: Any = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(
            content=self.reply,
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
            latency_ms=1,
        )


def _spec() -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000d01"),
        model_name="anthropic/claude-haiku-4-5",
        provider="anthropic",
        temperature=0.0,
        max_tokens=128,
        top_p=1.0,
    )


def test_block_prompts_carry_no_vietnamese_literal() -> None:
    """The hardcoded VN scaffolds are gone — prompts are language-neutral."""
    joined = " ".join(_BLOCK_PROMPTS.values())
    for vn_token in ("tiếng Việt", "Diễn giải", "câu mô tả"):
        assert vn_token not in joined
    # Each prompt must template both content + language.
    for tmpl in _BLOCK_PROMPTS.values():
        assert "{content}" in tmpl
        assert "{language}" in tmpl


@pytest.mark.asyncio
async def test_english_doc_narrate_instruction_names_english() -> None:
    llm = _CaptureLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_spec(),
        record_tenant_id=TenantId(UUID("00000000-0000-0000-0000-0000000000aa")),
        trace_id=TraceId("trace-en"),
    )
    table = "| Name | Price |\n|---|---|\n| A | 100 |"
    await strategy.narrate(table, "TABLE", language="en")

    user_msg = next(m for m in llm.calls[0] if m.role == "user")
    assert "en language" in user_msg.content
    # No Vietnamese instruction leaked into the EN-doc prompt.
    assert "tiếng Việt" not in user_msg.content
