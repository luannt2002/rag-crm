"""Wire-format parity guard between ``/health/models`` probes and runtime.

A probe whose request shape differs from the live runtime path produces a
false-green ops signal: probe says OK while the next user request would
TypeError before reaching the upstream. These tests pin the probe call
signatures to the same Port contracts used by ``query_graph`` so probe drift
becomes a CI failure, not a 02:00 outage.

Mock-only: stub Ports record the kwargs the probe passes; we assert the
recorded shape matches the contract (``task=retrieval.query`` for the embed
probe, ``spec``/``record_tenant_id``/``trace_id`` kwargs for the LLM probe,
positional ``query``+``chunks`` plus kw ``top_n`` for the reranker probe).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec, LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
from ragbot.interfaces.http.routes.health_models import (
    STATUS_HEALTHY,
    _probe_embedding,
    _probe_llm,
    _probe_reranker,
)
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_HEALTH_PROBE_LLM_MAX_TOKENS,
    DEFAULT_HEALTH_PROBE_QUERY,
)


class _RecordingEmbedder:
    """Stub ``EmbeddingPort`` that captures ``embed_batch`` kwargs verbatim."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.calls: list[dict[str, Any]] = []

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: Any,
    ) -> list[list[float]]:
        self.calls.append(
            {
                "texts": list(texts),
                "spec": spec,
                "record_tenant_id": record_tenant_id,
            },
        )
        return [[0.0] * (spec.dimension or 1)]

    async def close(self) -> None:
        return None


class _RecordingLLMRouter:
    """Stub ``LLMPort`` that captures ``complete`` args + kwargs verbatim."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls.append({"args": args, "kwargs": kwargs})
        spec = kwargs["spec"]
        return LLMResponse(
            content="probe-ack",
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=3,
            tokens_out=1,
            cost_usd=0.0,
            latency_ms=1,
        )


class _RecordingReranker:
    """Stub ``RerankerPort`` that captures ``rerank`` args + kwargs verbatim."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def rerank(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"args": args, "kwargs": kwargs})
        # Mimic the runtime shape the probe consumes.
        return [
            {"content": "doc-a", "rerank_score": 0.91, "score": 0.91},
            {"content": "doc-b", "rerank_score": 0.42, "score": 0.42},
        ]

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. Embedding probe carries the runtime ``task=retrieval.query`` selector.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_probe_passes_query_task_matching_runtime() -> None:
    """The embed probe must propagate ``task=retrieval.query`` like
    ``query_graph._embed_query`` — without it Jina returns a passage-head
    vector and the runtime query path would silently degrade."""
    recorder = _RecordingEmbedder()
    expected_dim = 1024
    with patch(
        "ragbot.interfaces.http.routes.health_models.build_embedder",
        return_value=recorder,
    ):
        result = await _probe_embedding(
            model_name="jina_ai/jina-embeddings-v3",
            provider_code="jina",
            expected_dim=expected_dim,
        )

    assert len(recorder.calls) == 1, "probe must call embed_batch exactly once"
    call = recorder.calls[0]
    spec_used: EmbeddingSpec = call["spec"]
    assert spec_used.task == DEFAULT_EMBEDDING_TASK_QUERY
    assert spec_used.task == "retrieval.query"
    assert spec_used.model_name == "jina_ai/jina-embeddings-v3"
    assert spec_used.provider == "jina"
    assert spec_used.dimension == expected_dim
    assert call["texts"] == [DEFAULT_HEALTH_PROBE_QUERY]
    # Surface invariants — green status when dim matches DB.
    assert result["status"] == STATUS_HEALTHY
    assert result["dimension"] == expected_dim
    assert result["dim_match_db"] is True


# ---------------------------------------------------------------------------
# 2. LLM probe uses the canonical ``LLMPort.complete`` kwarg signature.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_probe_uses_port_signature_with_spec_and_trace() -> None:
    """The LLM probe must call ``complete(messages, *, spec, record_tenant_id,
    trace_id)`` — the same keyword contract every runtime caller uses. The
    pre-fix probe passed ``model=`` directly which TypeError-failed before the
    upstream and was masked by the outer fail-soft wrapper."""
    router = _RecordingLLMRouter()
    result = await _probe_llm(
        model_name="gpt-4.1-mini",
        provider_code="openai",
        llm_router=router,
    )

    assert len(router.calls) == 1
    call = router.calls[0]
    # Messages travel positional — first arg is the `[LLMMessage]` list.
    args = call["args"]
    kwargs = call["kwargs"]
    assert len(args) == 1
    messages = args[0]
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert isinstance(messages[0], LLMMessage)
    assert messages[0].role == "user"
    # Keyword contract — every kw the LLMPort declares MUST be present.
    assert set(kwargs.keys()) == {"spec", "record_tenant_id", "trace_id"}
    spec_used: LLMSpec = kwargs["spec"]
    assert isinstance(spec_used, LLMSpec)
    # Probe pins max_tokens from the constants SSoT (zero-hardcode rule).
    assert spec_used.max_tokens == DEFAULT_HEALTH_PROBE_LLM_MAX_TOKENS
    assert spec_used.provider == "openai"
    assert spec_used.model_name == "openai/gpt-4.1-mini"
    # Probe must NOT pass ``model=`` directly — that was the pre-fix drift.
    assert "model" not in kwargs
    # Result extraction wires ``LLMResponse.content`` → status, tokens_used.
    assert result["status"] == STATUS_HEALTHY
    assert result["tokens_used"] == 4  # tokens_in (3) + tokens_out (1)


# ---------------------------------------------------------------------------
# 3. Reranker probe matches runtime kw signature (``query`` + ``chunks`` + ``top_n``).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_probe_call_shape_matches_runtime() -> None:
    """The reranker probe must call with the runtime arg shape: ``query``
    first, ``chunks`` second, plus ``top_n`` kwarg — see ``query_graph``
    line 2475 (``_active_reranker.rerank(query=..., chunks=..., top_n=...,
    model=...)``). The probe omits ``model`` only because the registry-built
    instance already binds the resolved model name."""
    recorder = _RecordingReranker()
    with patch(
        "ragbot.interfaces.http.routes.health_models.build_reranker",
        return_value=recorder,
    ):
        result = await _probe_reranker(
            model_name="jina-reranker-v2-base-multilingual",
            provider_code="jina",
            api_key="probe-key-not-real",
        )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    # Either positional (current probe) or kwarg form — both honor the runtime
    # contract. Pin both forms so a refactor cannot break parity silently.
    args = call["args"]
    kwargs = call["kwargs"]
    if args:
        # Positional path — query is args[0], chunks list is args[1].
        assert len(args) >= 2
        assert args[0] == DEFAULT_HEALTH_PROBE_QUERY
        assert isinstance(args[1], list)
        chunks = args[1]
    else:
        assert kwargs["query"] == DEFAULT_HEALTH_PROBE_QUERY
        chunks = kwargs["chunks"]
    assert len(chunks) == 2
    assert all("content" in c for c in chunks)
    # ``top_n`` must travel as kw (signature is keyword-only after ``*``).
    assert kwargs.get("top_n") == 2
    # Health classification follows when results carry rerank_score.
    assert result["status"] == STATUS_HEALTHY
    assert result["test_query_score"] == pytest.approx(0.91)
