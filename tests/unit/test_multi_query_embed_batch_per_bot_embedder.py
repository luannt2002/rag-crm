"""[T2-CostPerf] Multi-query embed_batch — per-bot embedder binding preserved.

Verifies that ``_embed_batch_queries`` uses the resolver's per-bot embedding
spec (record_tenant_id + record_bot_id forwarded to resolve_runtime) so that
tenants with custom embedder bindings receive the correct spec on the batch
call, not a platform default.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from tests.unit._state_lift_helper import _STATE_LIFT_DEFAULT_TRACKER


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _SpecCapturingEmbedder:
    """Embedder that captures the spec.model_name forwarded by embed_batch."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.captured_specs: list[str] = []
        self.captured_tenant_ids: list[UUID | None] = []

    async def embed_batch(
        self, texts: list[str], *, spec=None, record_tenant_id=None
    ) -> list[list[float]]:
        self.captured_specs.append(str(getattr(spec, "model_name", None)))
        self.captured_tenant_ids.append(record_tenant_id)
        return [[0.7] * self.dim for _ in texts]

    async def embed_one(
        self, text: str, *, spec=None, record_tenant_id=None
    ) -> list[float]:
        return [0.7] * self.dim

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


class _RecordingVectorStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id,
        top_k: int,
        **kw,
    ) -> list[dict]:
        self.calls.append({"query_text": query_text})
        cid = f"chunk-{len(self.calls)}"
        return [{"chunk_id": cid, "text": "text", "content": "text", "score": 0.5}]

    async def search(self, **_kw) -> list:
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_embed_batch_forwards_per_bot_spec():
    """resolve_runtime must be called with the bot's record_tenant_id and
    record_bot_id so the per-bot embedding model binding is honored."""
    from ragbot.orchestration.query_graph import build_graph

    bot_tenant_id = uuid4()
    bot_record_bot_id = uuid4()
    per_bot_model = "custom/per-bot-embed"

    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = per_bot_model
    cfg.model_name = per_bot_model
    cfg.embedding_dimension = 8
    cfg.provider = MagicMock(code="custom")
    cfg.embedding_spec = None  # triggers _to_embedding_spec path
    cfg.binding_id = uuid4()
    cfg.wire_model_id = "per-bot-embed"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    resolver.resolve_embedding = AsyncMock(return_value=cfg)

    async def _complete(_cfg, messages, **kw) -> dict:
        purpose = kw.get("purpose", "")
        if purpose == "multi_query":
            return {"text": '["alt 1", "alt 2"]', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        joined = " ".join(m.get("content", "") for m in messages).lower()
        if "phân loại" in joined:
            uq = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            return {"text": f'{{"query": "{uq}", "intent": "factoid"}}', "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}
        return {"text": "Answer.", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0, "finish_reason": "stop"}

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=_complete)

    embedder = _SpecCapturingEmbedder()
    vs = _RecordingVectorStore()

    graph = build_graph(
        invocation_logger=MagicMock(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
        vector_store=vs,
        embedder=embedder,
    )

    state = {
        "tenant_id": uuid4(),
        "record_tenant_id": bot_tenant_id,
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": bot_record_bot_id,
        "channel_type": "api",
        "query": "bảo hành sản phẩm",
        "rewritten_query": None,
        # Pre-inject paraphrases to avoid LLM call + L1 router short-circuit.
        "_mq_queries": ["bảo hành sản phẩm", "thời gian bảo hành", "bao lâu bảo hành"],
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "pipeline_config": {
            "multi_query_enabled": True,
            "multi_query_n_variants": 3,
            "multi_query_max_variants": 5,
            "multi_query_timeout_s": 5,
            "multi_query_model": "mock/model",
            "pipeline_multi_query_embed_batch_enabled": True,
            "merge_condense_router": True,
            "decompose_enabled": False,
            "adaptive_router_l1_enabled": False,
            "skip_rewrite_intents": ["factoid"],
            "embedding_model": per_bot_model,
            "embedding_dimension": 8,
            "top_k": 10,
            "reranker_enabled": False,
            "rag_rrf_k": 60,
        },
        "step_tracker": _STATE_LIFT_DEFAULT_TRACKER,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }

    asyncio.run(graph.ainvoke(state, config={"recursion_limit": 30}))

    # resolve_runtime must have been called with the correct IDs at least once
    resolve_calls = resolver.resolve_runtime.call_args_list
    tenant_ids_used = [
        kw.get("record_tenant_id") or args[0] if args else kw.get("record_tenant_id")
        for args, kw in [(c.args, c.kwargs) for c in resolve_calls]
    ]
    assert bot_tenant_id in tenant_ids_used, (
        f"Expected record_tenant_id={bot_tenant_id} in resolve_runtime calls, "
        f"got: {tenant_ids_used}"
    )

    # Multi-query must have fired
    assert len(vs.calls) >= 2, f"Expected ≥2 hybrid_search calls, got {len(vs.calls)}"
