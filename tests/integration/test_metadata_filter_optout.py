"""Integration test — perf regression fix on metadata-aware retrieval.

Backstory
---------
Read-side ``metadata_aware_retrieval_enabled`` defaulted to ``True`` while the
write-side ``metadata_extraction_enabled`` defaulted to ``False``. Effect on
every query: an LLM intent-extraction call (~150-300ms) followed by a
filtered ``hybrid_search`` that returned 0 rows (because no chunk was
labelled) plus a relax retry with no filter. End-user visible latency
inflated by ~300-500ms for zero benefit, surfaced only by the
``metadata_filter_relaxed`` log event.

Fix shape:
1. Default ``DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED`` flipped to ``False``.
2. ``init_system_config`` seeds both ``metadata_aware_retrieval_enabled`` and
   ``metadata_fallback_relax_enabled`` so live config is explicit.
3. Runtime gate in ``query_graph.retrieve`` now requires BOTH read-side and
   write-side flags to be truthy before the LLM intent extractor is invoked
   — defense-in-depth against an accidental read flip without a re-ingest.

This test pins behaviour 1 + 3 with no DB / network — the LLM extractor is
patched and we assert call-count semantics.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---- Shared test scaffolding (mirrors test_query_graph_gaps_5_6_7) ----
from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER

class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw: Any):
        ctx = MagicMock()
        ctx.record = lambda **_: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name: str, **_kw: Any):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a: Any, **_kw: Any):
        return []

    async def check_output(self, *_a: Any, **_kw: Any):
        return []


class _FakeVectorStore:
    """Minimal stand-in that records whether ``hybrid_search`` saw a filter.

    The retrieve node detects the port shape via ``inspect.signature`` so we
    expose a ``hybrid_search`` accepting ``query_text`` (PgVectorStore variant)
    plus a ``metadata_filter`` kwarg — that signature triggers the relevant
    code branch and lets us assert the exact kwargs the node forwarded.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id: Any,
        top_k: int,
        channel_type: str = "web",
        bm25_use_cover_density: bool = True,
        bm25_normalization_flags: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict]:
        self.calls.append(
            {
                "query_text": query_text,
                "metadata_filter": metadata_filter,
            }
        )
        # Return one chunk so the graph can move forward.
        return [
            {
                "chunk_id": str(uuid4()),
                "document_id": str(uuid4()),
                "content": "Stub chunk for retrieve node smoke.",
                "text": "Stub chunk for retrieve node smoke.",
                "score": 0.9,
                "document_name": "stub",
                "chunk_index": 0,
            }
        ]

    async def search(self, *_a: Any, **_kw: Any) -> list[dict]:
        return []


def _make_resolver_llm() -> tuple[MagicMock, MagicMock]:
    """Resolver/LLM that hands back a benign answer for any purpose."""
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    async def _complete(_cfg: Any, messages: list[dict], **_kw: Any) -> dict:
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại intent" in joined or "intent" in joined:
            return {
                "text": '{"query": "stub", "intent": "factoid"}',
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        if "relevant" in joined and "irrelevant" in joined:
            return {
                "text": "Chunk 1: relevant",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            }
        return {
            "text": "stub answer",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)
    return resolver, llm


class _FakeEmbedder:
    """Embed stub — returns a 1536-dim zero vector so the retrieve node
    proceeds past the ``q_emb`` truthy check (any list with len > 0 works).
    """

    async def embed_query(self, _text: str) -> list[float]:
        return [0.001] * 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.001] * 1536 for _ in texts]


def _initial_state(pipeline_config: dict[str, Any]) -> dict[str, Any]:
    """Build a state shaped like the production retrieve entry point."""
    return {
        "tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "record_bot_id": uuid4(),
        "record_tenant_id": uuid4(),
        "bot_id": uuid4(),
        "channel_type": "web",
        "query": "giá chăm sóc da",
        "rewritten_query": "giá chăm sóc da",
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "intent": "factoid",
        "pipeline_config": pipeline_config,
    
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
}


def _run_graph_with_patch(
    monkeypatch: pytest.MonkeyPatch, pipeline_config: dict[str, Any]
) -> tuple[dict[str, Any], int, _FakeVectorStore]:
    """Run the retrieve path once and report (final_state, intent_calls, store)."""
    from ragbot.orchestration import query_graph as qg

    intent_calls = {"count": 0}

    async def _spy_extract_intent(
        _query: str,
        *,
        model_id: str | None = None,
        system_prompt: str | None = None,
        allowed_doc_types: frozenset[str] | None = None,
    ) -> dict:
        intent_calls["count"] += 1
        return {"document_type": "price_list"}

    # Patch the imported alias inside query_graph — that's the symbol the
    # retrieve node closes over.
    monkeypatch.setattr(qg, "_extract_query_intent", _spy_extract_intent)

    resolver, llm = _make_resolver_llm()
    store = _FakeVectorStore()

    graph = qg.build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        vector_store=store,
        embedder=_FakeEmbedder(),
        model_resolver=resolver,
        llm=llm,
    )
    initial = _initial_state(pipeline_config)
    final = asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 30}))
    return final, intent_calls["count"], store


# ===========================================================================
# Tests
# ===========================================================================


def test_metadata_aware_default_false_skips_intent_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With NO flags set in pipeline_config the constants defaults apply.

    ``DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED`` defaults to False, so the
    intent extractor must NOT be called even once and every
    ``hybrid_search`` call must receive ``metadata_filter=None``.
    """
    pipeline_config = {
        "merge_condense_router": True,
        "skip_rewrite_intents": ["factoid"],
        "multi_query_enabled": False,  # keep single-query path for clarity
    }
    _final, intent_count, store = _run_graph_with_patch(monkeypatch, pipeline_config)

    assert intent_count == 0, (
        f"intent extractor must NOT be invoked when read-side flag "
        f"defaults to False, got {intent_count} calls"
    )
    # Every recorded hybrid_search call should have metadata_filter unset
    # (None or empty dict — both are no-op).
    assert store.calls, "expected at least one hybrid_search call"
    for c in store.calls:
        mf = c["metadata_filter"]
        assert not mf, f"metadata_filter must be empty/None, got {mf!r}"


def test_metadata_aware_with_write_off_skips_intent_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-side flag ON but write-side OFF must still skip the extractor.

    This pins the runtime tie-break: read-side cannot run unless write-side
    is also on, even if an operator forgot to flip back ``metadata_aware``
    after disabling ``metadata_extraction``.
    """
    pipeline_config = {
        "merge_condense_router": True,
        "skip_rewrite_intents": ["factoid"],
        "multi_query_enabled": False,
        "metadata_aware_retrieval_enabled": True,  # READ ON
        "metadata_extraction_enabled": False,      # WRITE OFF — corpus unlabeled
    }
    _final, intent_count, store = _run_graph_with_patch(monkeypatch, pipeline_config)

    assert intent_count == 0, (
        f"runtime gate must skip intent extractor when write-side is off, "
        f"got {intent_count} calls"
    )
    for c in store.calls:
        assert not c["metadata_filter"], (
            f"metadata_filter must be empty when write-side is off, got "
            f"{c['metadata_filter']!r}"
        )


def test_metadata_aware_both_flags_on_runs_intent_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH flags are explicitly True (operator opted in after a
    re-ingest under metadata extraction) the intent extractor must run and
    its filter must reach the vector store."""
    pipeline_config = {
        "merge_condense_router": True,
        "skip_rewrite_intents": ["factoid"],
        "multi_query_enabled": False,
        "metadata_aware_retrieval_enabled": True,
        "metadata_extraction_enabled": True,
    }
    _final, intent_count, store = _run_graph_with_patch(monkeypatch, pipeline_config)

    assert intent_count >= 1, (
        f"intent extractor MUST run when both flags are True, got {intent_count}"
    )
    # First hybrid_search call must carry the filter the spy returned.
    assert store.calls, "expected at least one hybrid_search call"
    forwarded = store.calls[0]["metadata_filter"]
    assert forwarded == {"document_type": "price_list"}, (
        f"first hybrid_search must receive the spy's filter, got {forwarded!r}"
    )


def test_default_constant_is_false() -> None:
    """Direct guard: a future edit that flips the default back to True
    without re-evaluating the perf regression must fail this test."""
    from ragbot.shared.constants import DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED

    assert DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED is False, (
        "Read-side default MUST stay False until the write-side default "
        "is also flipped True. Re-check the perf cost."
    )
