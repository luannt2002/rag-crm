"""[T1-Smartness] Stream S6 — Adaptive Router L3 decomposer unit tests.

Pure source-level unit tests. The decomposer accepts an injected
``llm_invoker`` callable so tests stub the LLM with deterministic
doubles. Default model is ``gpt-4.1-mini`` (admin override 2026-05-12).
"""

from __future__ import annotations

import json

import pytest

from ragbot.orchestration.nodes.query_decomposer import (
    DECOMPOSER_SYSTEM_PROMPT,
    decompose_query,
)
from ragbot.shared.constants import (
    DEFAULT_DECOMPOSER_ENABLED,
    DEFAULT_DECOMPOSER_MAX_SUB_QUERIES,
    DEFAULT_DECOMPOSER_MAX_TOKENS,
    DEFAULT_DECOMPOSER_MODEL,
)


_DEFAULT_GETTER_OVERRIDES: dict[str, object] = {
    "decomposer.enabled": DEFAULT_DECOMPOSER_ENABLED,
    "decomposer.model": DEFAULT_DECOMPOSER_MODEL,
    "decomposer.max_tokens": DEFAULT_DECOMPOSER_MAX_TOKENS,
    "decomposer.max_sub_queries": DEFAULT_DECOMPOSER_MAX_SUB_QUERIES,
}


def _make_getter(overrides: dict[str, object] | None = None):
    merged = dict(_DEFAULT_GETTER_OVERRIDES)
    if overrides:
        merged.update(overrides)

    def _getter(key: str, default):  # type: ignore[no-untyped-def]
        return merged.get(key, default)

    return _getter


class _Recorder:
    """Async stub LLM that records its call args + returns a canned reply."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.reply


# ---------------------------------------------------------------------------
# 1. Three-entity comma list → three sub-queries returned.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_comma_list_yields_three_sub_queries() -> None:
    """An LLM that splits "Điều 11, 33, 44" into three atomic subs MUST
    have all three subs surface to the caller."""
    payload = {"sub_queries": ["Điều 11", "Điều 33", "Điều 44"]}
    invoker = _Recorder(json.dumps(payload))
    out = await decompose_query(
        "Điều 11, 33, 44",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["Điều 11", "Điều 33", "Điều 44"]


# ---------------------------------------------------------------------------
# 2. Domain-neutral subjects work identically (product names).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_product_query_yields_two_sub_queries() -> None:
    payload = {"sub_queries": ["sản phẩm A giá?", "sản phẩm B giá?"]}
    invoker = _Recorder(json.dumps(payload))
    out = await decompose_query(
        "sản phẩm A và B giá bao nhiêu?",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert len(out) == 2
    assert out[0].endswith("?")
    assert out[1].endswith("?")


# ---------------------------------------------------------------------------
# 3. Single-intent input → one-item array passes through unchanged.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_single_intent_query_passes_through_as_one() -> None:
    payload = {"sub_queries": ["What is X?"]}
    invoker = _Recorder(json.dumps(payload))
    out = await decompose_query(
        "What is X?",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["What is X?"]


# ---------------------------------------------------------------------------
# 4. LLM returns malformed JSON → fallback to [original_query].
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_json_parse_error_falls_back_to_original_query() -> None:
    invoker = _Recorder("not json at all {")
    out = await decompose_query(
        "anything",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["anything"]


# ---------------------------------------------------------------------------
# 5. LLM raises → fallback to [original_query], no propagation.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_original_query() -> None:
    async def _raising(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated LLM outage")

    out = await decompose_query(
        "anything",
        llm_invoker=_raising,
        config_getter=_make_getter(),
    )
    assert out == ["anything"]


# ---------------------------------------------------------------------------
# 6. max_sub_queries cap honoured (default 8).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_max_sub_queries_cap_is_honoured() -> None:
    payload = {"sub_queries": [f"q{i}" for i in range(20)]}
    invoker = _Recorder(json.dumps(payload))
    out = await decompose_query(
        "many",
        llm_invoker=invoker,
        config_getter=_make_getter(),  # default cap = 8
    )
    assert len(out) == DEFAULT_DECOMPOSER_MAX_SUB_QUERIES


# ---------------------------------------------------------------------------
# 7. Empty sub_queries list → fallback.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_sub_queries_list_falls_back() -> None:
    invoker = _Recorder(json.dumps({"sub_queries": []}))
    out = await decompose_query(
        "hello",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["hello"]


# ---------------------------------------------------------------------------
# 8. decomposer.enabled=False → fallback (LLM never called).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_disabled_flag_skips_llm_call() -> None:
    invoker = _Recorder(json.dumps({"sub_queries": ["never used"]}))
    out = await decompose_query(
        "hello",
        llm_invoker=invoker,
        config_getter=_make_getter({"decomposer.enabled": False}),
    )
    assert out == ["hello"]
    assert invoker.calls == []  # LLM MUST NOT be invoked


# ---------------------------------------------------------------------------
# 9. Whitespace is stripped from sub-queries.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_whitespace_is_stripped_in_sub_queries() -> None:
    invoker = _Recorder(json.dumps({"sub_queries": ["  q1  ", "\tq2\n"]}))
    out = await decompose_query(
        "stuff",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["q1", "q2"]


# ---------------------------------------------------------------------------
# 10. Vietnamese inputs return Vietnamese sub-queries unchanged (language preserve).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_language_is_preserved_in_sub_queries() -> None:
    payload = {"sub_queries": ["Điều 11 là gì?", "Điều 33 là gì?"]}
    invoker = _Recorder(json.dumps(payload))
    out = await decompose_query(
        "Điều 11 và 33 là gì?",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert all("là gì?" in s for s in out)


# ---------------------------------------------------------------------------
# 11. Decomposer system prompt is DOMAIN-NEUTRAL (no industry literals).
# ---------------------------------------------------------------------------
def test_system_prompt_is_domain_neutral() -> None:
    """The default prompt MUST NOT name any industry / domain — it
    instructs the LLM in linguistic terms only."""
    forbidden = (
        "legal", "medical", "ecommerce", "law", "drug",
        "Điều", "Khoản", "Chương", "sản phẩm",
    )
    text = DECOMPOSER_SYSTEM_PROMPT.lower()
    for token in forbidden:
        assert token.lower() not in text, (
            f"prompt MUST be domain-neutral; forbidden token leaked: {token!r}"
        )


# ---------------------------------------------------------------------------
# 12. Admin override: default model is gpt-4.1-mini (Haiku banned).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_model_is_gpt_4_1_mini_admin_override() -> None:
    """ADMIN OVERRIDE 2026-05-12: ``decomposer.model`` defaults to
    ``gpt-4.1-mini`` (Haiku banned per user direction). The recorder
    captures the kwarg passed to the LLM so we can verify it."""
    invoker = _Recorder(json.dumps({"sub_queries": ["a", "b"]}))
    await decompose_query(
        "A và B?",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert invoker.calls, "LLM must have been invoked"
    call = invoker.calls[0]
    assert call["model"] == "gpt-4.1-mini"
    # Belt-and-braces: NEVER haiku.
    assert "haiku" not in str(call["model"]).lower()


# ---------------------------------------------------------------------------
# 13. Bot owner overrides model via config — pass-through honoured.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_model_override_is_passed_through_to_llm() -> None:
    invoker = _Recorder(json.dumps({"sub_queries": ["a", "b"]}))
    await decompose_query(
        "A và B?",
        llm_invoker=invoker,
        config_getter=_make_getter({"decomposer.model": "claude-opus-4-7"}),
    )
    assert invoker.calls[0]["model"] == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# 14. Non-dict JSON payload → fallback.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_dict_json_payload_falls_back() -> None:
    invoker = _Recorder(json.dumps(["not", "a", "dict"]))
    out = await decompose_query(
        "hello",
        llm_invoker=invoker,
        config_getter=_make_getter(),
    )
    assert out == ["hello"]
