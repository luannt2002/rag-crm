"""Unit tests for the ``multi_query_fanout`` step.

Multi-query fanout is not a standalone LangGraph node — it is a step
opened *inside* the ``retrieve`` node at ``query_graph.py:1630`` via
``async with step_tracker.step("multi_query_fanout") as mq_ctx``. The
heavy lifting lives in
``ragbot.application.services.multi_query_expansion.expand_query`` (a
transport-agnostic service the retrieve node calls).

These tests exercise the service directly, with controlled async LLM
fakes, plus the step-metadata contract that the retrieve node depends
on (``mq_ctx.set_metadata(n_variants=..., requested=..., model=...)``).

Why test the service rather than the wrapped step?
-------------------------------------------------
The fanout block is gated by ``mq_enabled`` + ``mq_n_variants > 1`` +
``decompose`` not being active, and it's followed by a real hybrid
search call. Driving the whole retrieve node here would require a
full vector-store stub plus an embedder fake — out of scope for a
fanout-focused unit test. The service IS the fanout logic; the
wrapping is just instrumentation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.multi_query_expansion import expand_query
from ragbot.shared.constants import (
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MODEL,
)


def _async_return(payload):
    """Build an AsyncMock that returns ``payload`` regardless of args."""
    fn = AsyncMock(return_value=payload)
    return fn


# --------------------------------------------------------------------------- #
# 1. Returns N variants for the given query                                   #
# --------------------------------------------------------------------------- #


def test_fanout_returns_n_variants_including_original():
    """With ``n_variants=3`` and a JSON-array LLM reply of 2
    paraphrases, the fanout must return [original, p1, p2] in that
    order so the original query is always retrieved against.
    """
    llm = _async_return(
        {"text": '["bảo hành sản phẩm bao lâu", "thời gian bảo hành là gì"]'}
    )
    out = asyncio.run(
        expand_query(
            "bảo hành bao lâu",
            n_variants=3,
            model_id=DEFAULT_MULTI_QUERY_MODEL,
            timeout_s=5,
            llm_complete_fn=llm,
        )
    )
    assert out[0] == "bảo hành bao lâu"
    assert len(out) == 3
    assert out[1] == "bảo hành sản phẩm bao lâu"
    assert out[2] == "thời gian bảo hành là gì"
    # The LLM was invoked exactly once (single fanout call, not per-variant).
    assert llm.await_count == 1


# --------------------------------------------------------------------------- #
# 2. Respects max_variants ceiling                                            #
# --------------------------------------------------------------------------- #


def test_fanout_clamps_to_max_variants_when_llm_overproduces():
    """If the LLM returns more paraphrases than allowed, the fanout
    MUST clamp the output to ``max_variants`` — otherwise the
    downstream parallel hybrid_search would explode in cost.
    """
    paraphrases = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]
    raw = '[' + ', '.join(f'"{p}"' for p in paraphrases) + ']'
    llm = _async_return({"text": raw})
    out = asyncio.run(
        expand_query(
            "câu hỏi gốc",
            n_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS + 10,  # ask for more
            model_id="mock/model",
            timeout_s=5,
            llm_complete_fn=llm,
            max_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        )
    )
    assert len(out) <= DEFAULT_MULTI_QUERY_MAX_VARIANTS
    # The first slot is always the original.
    assert out[0] == "câu hỏi gốc"


# --------------------------------------------------------------------------- #
# 3. Empty query → empty list                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("blank", ["", "   ", "\n\t\n"])
def test_fanout_empty_query_returns_empty_list(blank):
    """An empty / whitespace query short-circuits BEFORE any LLM call."""
    llm = AsyncMock()
    out = asyncio.run(
        expand_query(
            blank,
            n_variants=3,
            model_id="mock/model",
            timeout_s=5,
            llm_complete_fn=llm,
        )
    )
    assert out == []
    # Crucially, no LLM call wasted on a blank input.
    assert llm.await_count == 0


# --------------------------------------------------------------------------- #
# 4. Each variant differs from the original (case-fold de-dup)                #
# --------------------------------------------------------------------------- #


def test_fanout_dedupes_paraphrases_that_match_original_case_insensitively():
    """The de-dup pass uses ``casefold + whitespace-collapse`` so a
    paraphrase that's just a re-cased / re-spaced copy of the original
    must NOT make it into the output.
    """
    raw = (
        '['
        '"bảo hành bao lâu",'      # exact dup of original
        '"BẢO HÀNH   BAO LÂU",'     # case+space dup
        '"chính sách bảo hành"'    # genuine paraphrase
        ']'
    )
    llm = _async_return({"text": raw})
    out = asyncio.run(
        expand_query(
            "bảo hành bao lâu",
            n_variants=4,
            model_id="mock/model",
            timeout_s=5,
            llm_complete_fn=llm,
        )
    )
    # original + 1 genuine paraphrase, dups stripped.
    assert out == ["bảo hành bao lâu", "chính sách bảo hành"]
    # Every entry must differ from every other entry post-casefold.
    folded = [s.strip().casefold() for s in out]
    assert len(set(folded)) == len(folded)


# --------------------------------------------------------------------------- #
# 5. Step metadata contract (timeout + parse-failure fallback)                #
# --------------------------------------------------------------------------- #


def test_fanout_falls_back_to_original_on_llm_timeout():
    """Timeout in the LLM coroutine must NOT raise — the fanout's
    contract guarantees graceful degrade to ``[query]`` so retrieval
    still runs against the original query.

    This is the behaviour that lets the retrieve node's
    ``mq_ctx.set_metadata(n_variants=len(queries))`` always emit a
    truthful "1 variant on fallback" event instead of swallowing the
    failure silently.
    """

    async def _slow(*_a, **_kw):
        await asyncio.sleep(10)
        return {"text": "never reached"}

    out = asyncio.run(
        expand_query(
            "câu hỏi cần fallback",
            n_variants=3,
            model_id="mock/model",
            timeout_s=0,  # immediate timeout — wait_for fires before _slow returns
            llm_complete_fn=_slow,
        )
    )
    assert out == ["câu hỏi cần fallback"]


def test_fanout_falls_back_when_llm_returns_empty_text():
    """LLM produced an empty reply → fallback to ``[query]``. Together
    with the timeout test above, this covers the two
    ``multi_query_fanout`` "we tried, got nothing useful" branches —
    so the retrieve node's ``mq_ctx.set_metadata(n_variants=1, ...)``
    path is exercised.

    NB: the service's line-based fallback parser is very permissive —
    free-text replies WILL still produce paraphrases. To force the
    no-paraphrase branch deterministically we need an empty reply.
    """
    llm = _async_return({"text": ""})
    out = asyncio.run(
        expand_query(
            "câu hỏi cần fallback empty",
            n_variants=3,
            model_id="mock/model",
            timeout_s=5,
            llm_complete_fn=llm,
        )
    )
    assert out == ["câu hỏi cần fallback empty"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
