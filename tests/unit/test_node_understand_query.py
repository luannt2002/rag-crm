"""Unit tests for `understand_query` + `router` orchestration nodes.

`understand_query` (~150 LoC at `query_graph.py:1085`) merges the
condense + intent-classification calls into one. `router` (~30 LoC at
`:1188`) is the legacy single-purpose intent extractor used when the
merged path is disabled.

Both nodes return `{"intent": <str>}` and may also rewrite the query.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _uq(compiled):
    return node_callable(compiled, "understand_query")


def _router(compiled):
    return node_callable(compiled, "router")


# --------------------------------------------------------------------------- #
# understand_query                                                            #
# --------------------------------------------------------------------------- #


def test_understand_query_falls_back_to_factoid_when_structured_disabled():
    """With structured-output OFF and no special routing, fallback intent=factoid."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, audit, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành bao lâu",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
    )
    out = asyncio.run(_uq(compiled)(state))
    # The state delta carries intent_confidence — assert subset.
    assert out.get("intent") == "factoid"
    assert len(tracker.by_name("understand_query")) == 1
    # An audit event MUST fire so observability sees every intent decision.
    assert audit.by_event("intent_extracted")


def test_understand_query_falls_back_when_llm_raises_runtime_error():
    """Runtime LLM failure → factoid fallback (NOT a programmer-bug exception)."""
    resolver, llm, _cfg = make_resolver_and_llm()
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bất kỳ câu hỏi nào",
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
    )
    out = asyncio.run(_uq(compiled)(state))
    # `understand_query` swallows runtime errors and returns the safe
    # factoid intent — pipeline must never crash here. The state delta
    # also carries intent_confidence.
    assert out.get("intent") == "factoid"


def test_understand_query_propagates_attribute_error_as_programmer_bug():
    """AttributeError (programmer bug) MUST propagate, not get swallowed.

    Trigger via the structured-output path: enable both flags, then
    plant an AttributeError on the resolver so the structured-LLM
    branch raises before falling back. The except clause ordering in
    `understand_query` distinguishes (AttributeError, TypeError) from
    runtime Exception — the former re-raise, the latter degrade to
    factoid.
    """
    resolver, llm, _cfg = make_resolver_and_llm()
    resolver.resolve_runtime = AsyncMock(side_effect=AttributeError("bug"))
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="x",
        pipeline_config={
            "structured_output_enabled": True,
            "understand_use_structured_output": True,
        },
    )
    with pytest.raises(AttributeError):
        asyncio.run(_uq(compiled)(state))


def test_understand_query_emits_intent_extracted_audit_event():
    """The audit event payload must always carry `intent` + `had_history` keys."""
    resolver, llm, _cfg = make_resolver_and_llm()
    compiled, _tracker, audit, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="có gì mới không",
        history=[
            {"role": "user", "content": "x" * 60},
            {"role": "assistant", "content": "y" * 60},
            {"role": "user", "content": "z" * 60},
        ],
        pipeline_config={
            "structured_output_enabled": False,
            "understand_use_structured_output": False,
        },
    )
    asyncio.run(_uq(compiled)(state))
    payloads = audit.by_event("intent_extracted")
    assert payloads
    p = payloads[-1]
    assert "intent" in p
    assert "had_history" in p
    assert p["had_history"] is True


# --------------------------------------------------------------------------- #
# router                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw_text,expected_intent",
    [
        ("factoid", "factoid"),
        ("multi_hop", "multi_hop"),
        ("greeting", "greeting"),
        ("aggregation", "aggregation"),
    ],
)
def test_router_returns_matching_intent_token_when_present(raw_text, expected_intent):
    resolver, llm, _cfg = make_resolver_and_llm(text_response=raw_text)
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="bất kỳ")
    out = asyncio.run(_router(compiled)(state))
    assert out == {"intent": expected_intent}


def test_router_defaults_to_factoid_when_llm_returns_nonsense():
    """Router scans LLM text for a known intent token; if none found → factoid."""
    resolver, llm, _cfg = make_resolver_and_llm(
        text_response="some unrecognised babble"
    )
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="x")
    out = asyncio.run(_router(compiled)(state))
    assert out == {"intent": "factoid"}


def test_router_step_wraps_per_call():
    resolver, llm, _cfg = make_resolver_and_llm(text_response="factoid")
    compiled, tracker, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(query="x")
    asyncio.run(_router(compiled)(state))
    asyncio.run(_router(compiled)(state))
    assert len(tracker.by_name("router")) == 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
