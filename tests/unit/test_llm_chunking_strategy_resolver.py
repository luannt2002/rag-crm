"""AdapChunk LLM Strategy Selector — adapter contract + graceful degradation.

Locks the spec §4 behaviour at the unit level: the LLM resolver turns a
DocumentProfile into a structured ChunkingDecision, validates the strategy
against the allowed vocabulary, and — critically — DEGRADES to the deterministic
rule resolver on any LLM/parse failure so ingest never breaks. Domain-neutral
synthetic profile (no tenant vocabulary).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ragbot.application.ports.strategy_ports import ChunkingDecision
from ragbot.domain.entities.document_profile import DocumentProfile, HeadingCounts
from ragbot.infrastructure.chunking_strategy.llm_resolver import (
    LLMChunkingStrategyResolver,
)
from ragbot.infrastructure.chunking_strategy.registry import (
    build_chunking_resolver,
    list_providers,
)
from ragbot.infrastructure.chunking_strategy.rule_resolver import (
    RuleChunkingStrategyResolver,
)

_PROSE_STRATEGIES = {"hdt", "semantic", "proposition", "hybrid", "recursive"}


def _profile(**kw) -> DocumentProfile:
    base = dict(
        heading_counts=HeadingCounts(h1=1, h2=4, h3=0, h4=0),
        has_toc=True,
        table_count=0,
        table_avg_rows=0.0,
        formula_count=0,
        image_count=0,
        code_block_count=0,
        avg_text_block_length=120.0,
        heading_ratio=0.1,
        mixed_content_score=0.0,
        detected_language="vi",
        total_blocks=20,
        total_words=3000,
    )
    base.update(kw)
    return DocumentProfile(**base)


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tokens_in = 10
        self.tokens_out = 5
        self.cost_usd = 0.0


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, **_kw):
        return _Resp(self._content)


class _RaisingLLM:
    async def complete(self, **_kw):
        raise TimeoutError("simulated provider timeout")


@pytest.mark.asyncio
async def test_rule_resolver_returns_valid_decision() -> None:
    """The deterministic default resolver returns a valid in-vocab decision."""
    d = await RuleChunkingStrategyResolver().resolve_strategy(
        "bot", record_tenant_id="t", document_profile=_profile()
    )
    assert isinstance(d, ChunkingDecision)
    assert d.strategy in _PROSE_STRATEGIES
    assert 0.0 <= d.confidence <= 1.0
    assert d.forced is False


@pytest.mark.asyncio
async def test_llm_resolver_parses_structured_decision() -> None:
    """A well-formed JSON completion maps to a ChunkingDecision (strategy lowered)."""
    llm = _FakeLLM(
        'Here is my pick:\n{"strategy":"HDT","confidence":0.9,'
        '"reasoning":"clear heading tree","detected_type":"report",'
        '"risk_factors":["large tables"]}'
    )
    r = LLMChunkingStrategyResolver(
        llm=llm,
        spec=SimpleNamespace(),
        fallback=RuleChunkingStrategyResolver(),
        record_tenant_id="t",
        trace_id="trace-x",
    )
    d = await r.resolve_strategy("bot", record_tenant_id="t", document_profile=_profile())
    assert d.strategy == "hdt"
    assert d.confidence == 0.9
    assert "tree" in d.reasoning
    assert d.forced is False


class _CapturingLLM:
    """Records the user message so we can assert prompt contents (spec 4.1)."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.last_user_msg = ""

    async def complete(self, *, messages, **_kw):  # noqa: ANN001
        self.last_user_msg = next(
            (m.content for m in messages if m.role == "user"), ""
        )
        return _Resp(self._content)


@pytest.mark.asyncio
async def test_llm_resolver_includes_block_list_in_prompt() -> None:
    """Spec 4.1: the selector must SEE the block list (shape), not just profile counts."""
    llm = _CapturingLLM('{"strategy":"HDT","confidence":0.8,"reasoning":"x"}')
    r = LLMChunkingStrategyResolver(
        llm=llm, spec=SimpleNamespace(),
        fallback=RuleChunkingStrategyResolver(),
        record_tenant_id="t", trace_id="tr",
    )
    blocks = [
        {"type": "HEADING", "level": 1, "content": "Chương 1: Tổng quan"},
        {"type": "TABLE", "content": "| Model | Acc |\n| A | 0.9 |"},
        {"type": "FORMULA", "content": "E = mc^2"},
        {"type": "TEXT", "content": "Đoạn văn xuôi giải thích lý thuyết dài.", "word_count": 7},
    ]
    await r.resolve_strategy(
        "bot", record_tenant_id="t", document_profile=_profile(), blocks=blocks,
    )
    msg = llm.last_user_msg
    assert "BLOCK LIST" in msg
    assert "HEADING(H1): Chương 1" in msg
    assert "TABLE[" in msg and "Model" in msg
    assert "FORMULA: E = mc^2" in msg
    assert "TEXT[7w]" in msg


@pytest.mark.asyncio
async def test_llm_resolver_no_blocks_still_works() -> None:
    """blocks=None → no block-list section, profile-only prompt (back-compat)."""
    llm = _CapturingLLM('{"strategy":"SEMANTIC","confidence":0.7,"reasoning":"x"}')
    r = LLMChunkingStrategyResolver(
        llm=llm, spec=SimpleNamespace(),
        fallback=RuleChunkingStrategyResolver(),
        record_tenant_id="t", trace_id="tr",
    )
    d = await r.resolve_strategy("bot", record_tenant_id="t", document_profile=_profile())
    assert "BLOCK LIST" not in llm.last_user_msg
    assert d.strategy == "semantic"


@pytest.mark.asyncio
async def test_llm_resolver_degrades_to_rule_on_failure() -> None:
    """LLM transport failure → fall back to the deterministic rule resolver."""
    r = LLMChunkingStrategyResolver(
        llm=_RaisingLLM(),
        spec=SimpleNamespace(),
        fallback=RuleChunkingStrategyResolver(),
        record_tenant_id="t",
        trace_id="trace-x",
    )
    d = await r.resolve_strategy("bot", record_tenant_id="t", document_profile=_profile())
    assert d.strategy in _PROSE_STRATEGIES
    assert "rule-based" in d.reasoning  # proof the fallback ran, not the LLM path


@pytest.mark.asyncio
async def test_llm_resolver_rejects_out_of_vocab_strategy() -> None:
    """An invented strategy name is rejected → degrade to rule (not propagated)."""
    r = LLMChunkingStrategyResolver(
        llm=_FakeLLM('{"strategy":"MAGIC","confidence":0.99,"reasoning":"x"}'),
        spec=SimpleNamespace(),
        fallback=RuleChunkingStrategyResolver(),
        record_tenant_id="t",
        trace_id="trace-x",
    )
    d = await r.resolve_strategy("bot", record_tenant_id="t", document_profile=_profile())
    assert d.strategy in _PROSE_STRATEGIES
    assert "rule-based" in d.reasoning


def test_registry_default_is_rule_and_llm_registered() -> None:
    """Default provider is deterministic; llm is opt-in + registered."""
    assert list_providers() == ["llm", "null", "rule"]
    assert isinstance(build_chunking_resolver("rule"), RuleChunkingStrategyResolver)
    assert isinstance(build_chunking_resolver("null"), RuleChunkingStrategyResolver)
    with pytest.raises(ValueError, match="unknown chunking_strategy provider"):
        build_chunking_resolver("nope")
