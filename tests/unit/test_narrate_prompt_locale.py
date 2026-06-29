"""Unit tests for P0-4 — per-locale Narrate prompt scaffolds.

The Narrate-then-Embed adapter used to hardcode Vietnamese output
("thành 1-2 câu tiếng Việt") for every block, contradicting its own system
instruction ("Preserve the source language exactly — do not translate").
These tests pin the fix:

- A non-VN document locale yields a SOURCE-language-preserving prompt
  (no forced Vietnamese; instructs the model NOT to translate).
- An unknown locale falls back to the language-agnostic 'default' pack,
  never the Vietnamese literals.
- The wired VN path (default locale, or explicit 'vi') stays byte-identical
  to the historical hardcoded scaffolds.

Mock-only — no live LLM. We inspect the user message the adapter actually
forwards to the injected LLMPort to assert on the prompt body.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
from ragbot.infrastructure.narrate.llm_narrate import (
    _BLOCK_PROMPTS,
    LLMNarrateGenerator,
)
from ragbot.shared.constants import (
    DEFAULT_NARRATE_MAX_TOKENS,
    DEFAULT_NARRATE_PROMPT_LANG,
    DEFAULT_NARRATE_PROMPT_TEMPLATES_BY_LANG,
    DEFAULT_NARRATE_TEMPERATURE,
)
from ragbot.shared.types import BlockType, TenantId, TraceId


@dataclass
class _RecordingLLM:
    """Records every ``complete`` call and returns a scripted reply."""

    reply: str = "A short grounded description."
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
        self.calls.append({"messages": list(messages)})
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
        binding_id=UUID("00000000-0000-0000-0000-000000000d01"),
        model_name="anthropic/claude-haiku-4-5",
        provider="anthropic",
        temperature=DEFAULT_NARRATE_TEMPERATURE,
        max_tokens=DEFAULT_NARRATE_MAX_TOKENS,
        top_p=1.0,
    )


_EN_TABLE = (
    "| Service | Price |\n"
    "|---|---|\n"
    "| Basic | 100 |\n"
    "| Premium | 350 |\n"
)


def _user_prompt(llm: _RecordingLLM) -> str:
    assert len(llm.calls) == 1, "expected exactly one LLM call"
    messages = llm.calls[0]["messages"]
    user = [m for m in messages if m.role == "user"]
    assert len(user) == 1, "expected exactly one user message"
    return user[0].content


@pytest.mark.asyncio
async def test_non_vn_locale_yields_source_language_prompt_not_forced_vietnamese() -> None:
    """An EN document locale must NOT force a Vietnamese-output prompt.

    It must produce a source-language-preserving prompt that explicitly tells
    the model to keep the input language and not translate.
    """
    llm = _RecordingLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-en"),
        narrate_lang="en",
    )

    await strategy.narrate(_EN_TABLE, "TABLE")
    prompt = _user_prompt(llm)

    # NOT forced Vietnamese.
    assert "tiếng Việt" not in prompt
    assert "Việt" not in prompt
    # Source-language-preserving intent present.
    lowered = prompt.lower()
    assert "same language" in lowered
    assert "do not translate" in lowered
    # The block content is still interpolated into the prompt.
    assert "Premium" in prompt


@pytest.mark.asyncio
async def test_unknown_locale_falls_back_to_source_language_default_not_vietnamese() -> None:
    """An unknown locale resolves to the language-agnostic 'default' pack."""
    llm = _RecordingLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-unknown"),
        narrate_lang="zz-unknown",
    )

    await strategy.narrate(_EN_TABLE, "TABLE")
    prompt = _user_prompt(llm)

    assert "tiếng Việt" not in prompt
    expected_default = DEFAULT_NARRATE_PROMPT_TEMPLATES_BY_LANG["default"]["TABLE"]
    assert prompt == expected_default.format(content=_EN_TABLE)


@pytest.mark.asyncio
async def test_default_locale_vn_prompt_is_byte_identical_to_historical_scaffold() -> None:
    """Wired default (no narrate_lang) keeps the VN prompt byte-for-byte."""
    vn_content = "| Dịch vụ | Đơn giá |\n|---|---|\n| Cơ bản | 100k |\n"
    llm = _RecordingLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-vi-default"),
    )

    await strategy.narrate(vn_content, "TABLE")
    prompt = _user_prompt(llm)

    expected = (
        "Diễn giải bảng/dòng dữ liệu dưới đây thành 1-2 câu tiếng Việt tự nhiên, "
        "nêu rõ các cột chính và nội dung dòng truyền tải. CHỈ trả về câu mô tả, "
        "không markdown, không tiền tố:\n\n" + vn_content
    )
    assert prompt == expected
    # The module-level default dict is the vi pack (byte-identical), so the
    # historical importer/test contract is preserved.
    assert _BLOCK_PROMPTS == DEFAULT_NARRATE_PROMPT_TEMPLATES_BY_LANG["vi"]
    assert DEFAULT_NARRATE_PROMPT_LANG == "vi"


@pytest.mark.asyncio
async def test_explicit_vi_matches_default_and_differs_from_non_vn() -> None:
    """Explicit 'vi' == default; non-VN locale yields a different prompt."""
    content = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    block: BlockType = "TABLE"

    llm_vi = _RecordingLLM()
    await LLMNarrateGenerator(
        llm=llm_vi,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("t-vi"),
        narrate_lang="vi",
    ).narrate(content, block)

    llm_default = _RecordingLLM()
    await LLMNarrateGenerator(
        llm=llm_default,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("t-def"),
    ).narrate(content, block)

    llm_en = _RecordingLLM()
    await LLMNarrateGenerator(
        llm=llm_en,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("t-en"),
        narrate_lang="en",
    ).narrate(content, block)

    p_vi = _user_prompt(llm_vi)
    p_default = _user_prompt(llm_default)
    p_en = _user_prompt(llm_en)

    assert p_vi == p_default  # explicit vi == wired default
    assert p_en != p_vi  # non-VN diverges
    assert "tiếng Việt" in p_vi
    assert "tiếng Việt" not in p_en
