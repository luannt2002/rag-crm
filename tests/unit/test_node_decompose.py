"""Unit tests for the ``decompose`` orchestration node.

The node lives at ``query_graph.py:1285`` and splits a multi-hop
question into 2-4 sub-queries. Contract:

- Multi-intent JSON array of length ≥ 2 → ``{"sub_queries": [...],
  "original_query": <q>}``.
- Single-intent (parsed result < 2 items, or empty, or non-JSON) →
  ``{}`` so the pipeline keeps the original query.
- Empty / non-array LLM text → ``{}``.
- Step ``decompose`` is wrapped on every invocation (the wrap happens
  unconditionally — there is no early exit).

Tests force the legacy text-parse path by disabling structured output
in ``pipeline_config`` so the assertions don't depend on a working
LiteLLM module on the test router.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _afunc(compiled):
    return node_callable(compiled, "decompose")


# Disable structured output globally so the legacy JSON-text parse path
# in ``decompose`` is exercised. The default values DEFAULT_*=True would
# otherwise force the test through ``_invoke_structured_llm_node`` which
# requires a real ``litellm`` module.
_LEGACY_PCFG = {
    "structured_output_enabled": False,
    "decompose_use_structured_output": False,
}


# --------------------------------------------------------------------------- #
# 1. Multi-intent query → list of sub-queries                                 #
# --------------------------------------------------------------------------- #


def test_decompose_returns_sub_queries_for_multi_intent_question():
    """Given an LLM JSON-array reply with ≥2 entries, the node must
    surface ``sub_queries`` AND attach the rewritten query as
    ``original_query`` (so the downstream retrieve node can fall back).
    """
    raw_array = '["Giá sản phẩm A?", "Giá sản phẩm B?"]'
    resolver, llm, _cfg = make_resolver_and_llm(text_response=raw_array)
    compiled, _tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="So sánh giá A và B",
        rewritten_query="So sánh giá sản phẩm A và sản phẩm B",
        pipeline_config=_LEGACY_PCFG,
    )
    out = asyncio.run(_afunc(compiled)(state))
    assert out["sub_queries"] == ["Giá sản phẩm A?", "Giá sản phẩm B?"]
    # ``original_query`` carries the *rewritten* query when present (so
    # the retrieve fallback uses the most-resolved form).
    assert out["original_query"] == "So sánh giá sản phẩm A và sản phẩm B"


# --------------------------------------------------------------------------- #
# 2. Single-intent → original (no decomp)                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw_text,reason",
    [
        ('["chỉ một câu hỏi"]', "single_item_array"),
        ("không phải JSON đâu", "non_json_text"),
        ('{"not": "an array"}', "json_object_not_array"),
        ("", "empty_text"),
    ],
)
def test_decompose_returns_empty_when_llm_output_not_decomposable(raw_text, reason):
    """All of these inputs must collapse to ``{}`` so the pipeline
    proceeds with the original (rewritten) query untouched.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response=raw_text)
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="câu hỏi đơn nhất",
        pipeline_config=_LEGACY_PCFG,
    )
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {}, f"failed on {reason!r}"


# --------------------------------------------------------------------------- #
# 3. Empty query — node still wraps the step but emits nothing useful         #
# --------------------------------------------------------------------------- #


def test_decompose_handles_empty_query_without_crashing():
    """Empty / whitespace query: the LLM is still invoked (the node
    doesn't gate on input length), but the parser will reject any
    non-JSON-array reply → ``{}`` returned. We additionally verify the
    step wrap happens exactly once.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response="")
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="", pipeline_config=_LEGACY_PCFG)
    out = asyncio.run(_afunc(compiled)(state))
    assert out == {}
    # The node opened the step exactly once.
    assert len(tracker.by_name("decompose")) == 1


# --------------------------------------------------------------------------- #
# 4. Step metadata captured + wrap count matches invocations                  #
# --------------------------------------------------------------------------- #


def test_decompose_wraps_step_per_invocation():
    """Every call to the node MUST open exactly one
    ``step_tracker.step('decompose')`` context, even when the parsed
    output gets rejected (single-item array). This guarantees the
    observability layer sees every decomposition attempt.
    """
    resolver, llm, _cfg = make_resolver_and_llm(text_response='["only one"]')
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="x", pipeline_config=_LEGACY_PCFG)
    asyncio.run(_afunc(compiled)(state))
    asyncio.run(_afunc(compiled)(state))
    asyncio.run(_afunc(compiled)(state))
    assert len(tracker.by_name("decompose")) == 3
    # No other step names leaked from this node.
    assert all(name == "decompose" for name in tracker.names())


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
