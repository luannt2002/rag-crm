"""Unit tests for JinaReranker — mocked httpx, no real API calls.

Coverage:
1.  test_constructor_requires_api_key — ValueError when api_key is empty
2.  test_mode_includes_model_name — mode property format "jina:<model>"
3.  test_rerank_empty_chunks_returns_empty — short-circuit guard
4.  test_rerank_calls_jina_api_with_correct_payload — payload structure verified
5.  test_rerank_returns_top_n_with_rerank_score — output sorted by relevance
6.  test_rerank_preserves_chunk_metadata — all original fields propagated
7.  test_rerank_handles_content_or_text_key — flexible input key
8.  test_rerank_caps_at_max_docs_64 — truncate input before sending
9.  test_rerank_http_error_raises_retrieval_error — HTTPStatusError → RetrievalError
10. test_rerank_timeout_raises_retrieval_error — TimeoutException → RetrievalError
11. test_rerank_score_rounded_6_decimals — precision guarantee
12. test_health_check_returns_true_on_200 — health_check happy path
13. test_health_check_returns_false_on_error — health_check fail-soft
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.shared.constants import (
    DEFAULT_JINA_RERANKER_MAX_DOCS,
    DEFAULT_JINA_RERANKER_MODEL,
)
from ragbot.shared.errors import RetrievalError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunks(n: int = 3, key: str = "content") -> list[dict[str, Any]]:
    return [
        {
            "id": f"chunk-{i}",
            key: f"document text {i}",
            "score": 0.9 - i * 0.1,
            "source": f"doc_{i}.pdf",
            "document_id": f"docid-{i}",
        }
        for i in range(n)
    ]


def _jina_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": DEFAULT_JINA_RERANKER_MODEL, "results": results}


def _make_response(
    results: list[dict[str, Any]],
    status_code: int = 200,
) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = _jina_response(results)
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=mock_resp,
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestJinaRerankerConstructor:
    def test_constructor_requires_api_key(self, monkeypatch) -> None:
        """Empty api_key + no pool + no env fallback must raise ValueError
        immediately — adapter refuses to construct without a credential."""
        # Clear legacy env-fallback names so the constructor's "no key
        # anywhere" guard fires deterministically.
        monkeypatch.delenv("RERANKER_JINA_API_KEY", raising=False)
        monkeypatch.delenv("JINA_API_KEY", raising=False)
        with pytest.raises(ValueError, match="non-empty api_key"):
            JinaReranker(api_key="")

    def test_constructor_accepts_valid_key(self) -> None:
        rr = JinaReranker(api_key="jina_test_key_abc123")
        assert rr is not None

    def test_mode_includes_model_name(self) -> None:
        rr = JinaReranker(api_key="key", model="jina-reranker-v3")
        assert rr.mode == "jina:jina-reranker-v3"

    def test_mode_reflects_custom_model(self) -> None:
        rr = JinaReranker(api_key="key", model="jina-reranker-v2-base-multilingual")
        assert rr.mode == "jina:jina-reranker-v2-base-multilingual"

    def test_get_provider_name_is_jina(self) -> None:
        assert JinaReranker.get_provider_name() == "jina"


# ---------------------------------------------------------------------------
# rerank() — happy path
# ---------------------------------------------------------------------------


class TestJinaRerankerRerank:
    @pytest.mark.asyncio
    async def test_rerank_empty_chunks_returns_empty(self) -> None:
        rr = JinaReranker(api_key="key")
        result = await rr.rerank("any query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_rerank_calls_jina_api_with_correct_payload(self) -> None:
        """Verify the exact JSON payload sent to Jina API."""
        chunks = _make_chunks(3)
        api_results = [
            {"index": 0, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.80},
        ]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="jina_key_test", model=DEFAULT_JINA_RERANKER_MODEL)

        captured_payload: dict[str, Any] = {}

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs) -> MagicMock:  # type: ignore[override]
            captured_payload.update(json)
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("query text", chunks, top_n=2)

        assert captured_payload["model"] == DEFAULT_JINA_RERANKER_MODEL
        assert captured_payload["query"] == "query text"
        assert captured_payload["documents"] == [
            "document text 0",
            "document text 1",
            "document text 2",
        ]
        assert captured_payload["top_n"] == 2
        assert captured_payload["return_documents"] is False

    @pytest.mark.asyncio
    async def test_rerank_returns_top_n_with_rerank_score(self) -> None:
        chunks = _make_chunks(3)
        api_results = [
            {"index": 1, "relevance_score": 0.90},
            {"index": 0, "relevance_score": 0.75},
        ]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("query", chunks, top_n=2)

        assert len(out) == 2
        assert out[0]["id"] == "chunk-1"
        assert out[0]["rerank_score"] == pytest.approx(0.90)
        assert out[1]["id"] == "chunk-0"
        assert out[1]["rerank_score"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_rerank_preserves_chunk_metadata(self) -> None:
        """All original chunk fields must survive the rerank enrichment."""
        chunks = _make_chunks(2)
        api_results = [{"index": 0, "relevance_score": 0.88}]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("query", chunks, top_n=1)

        assert len(out) == 1
        chunk = out[0]
        # Original fields preserved
        assert chunk["id"] == "chunk-0"
        assert chunk["source"] == "doc_0.pdf"
        assert chunk["document_id"] == "docid-0"
        assert chunk["content"] == "document text 0"
        # Enrichment fields added
        assert "rerank_score" in chunk
        assert "retrieval_score" in chunk
        assert "reranker_used" in chunk
        assert chunk["reranker_used"].startswith("jina:")

    @pytest.mark.asyncio
    async def test_rerank_handles_content_or_text_key(self) -> None:
        """Chunks may carry 'text' instead of 'content' — both must work."""
        chunks_text_key = _make_chunks(2, key="text")
        api_results = [{"index": 0, "relevance_score": 0.70}]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        captured_docs: list[str] = []

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):  # type: ignore[override]
            captured_docs.extend(json.get("documents", []))
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            out = await rr.rerank("query", chunks_text_key, top_n=1)

        # text key extracted correctly
        assert captured_docs == ["document text 0", "document text 1"]

    @pytest.mark.asyncio
    async def test_rerank_caps_at_max_docs_64(self) -> None:
        """Input beyond API hard limit must be truncated to DEFAULT_JINA_RERANKER_MAX_DOCS."""
        # Build 70 chunks — exceeds the 64-doc limit
        chunks = _make_chunks(70)
        api_results = [{"index": i, "relevance_score": 0.5} for i in range(10)]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        captured_doc_count: list[int] = []

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):  # type: ignore[override]
            captured_doc_count.append(len(json.get("documents", [])))
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("query", chunks, top_n=5)

        assert captured_doc_count[0] == DEFAULT_JINA_RERANKER_MAX_DOCS  # must be 64

    @pytest.mark.asyncio
    async def test_rerank_score_rounded_6_decimals(self) -> None:
        """rerank_score must be rounded to exactly 6 decimal places."""
        chunks = _make_chunks(1)
        # Long float from API
        api_results = [{"index": 0, "relevance_score": 0.123456789}]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("q", chunks, top_n=1)

        assert out[0]["rerank_score"] == pytest.approx(0.123457, abs=1e-6)

    @pytest.mark.asyncio
    async def test_rerank_stores_retrieval_score(self) -> None:
        """Original 'score' (retrieval RRF) must be preserved in retrieval_score."""
        chunks = [{"id": "c1", "content": "text", "score": 0.033}]
        api_results = [{"index": 0, "relevance_score": 0.85}]
        mock_resp = _make_response(api_results)
        rr = JinaReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("q", chunks, top_n=1)

        assert out[0]["retrieval_score"] == pytest.approx(0.033)
        assert out[0]["score"] == pytest.approx(0.85)  # overwritten with rerank score


# ---------------------------------------------------------------------------
# rerank() — error paths
# ---------------------------------------------------------------------------


class TestJinaRerankerErrors:
    @pytest.mark.asyncio
    async def test_rerank_http_error_raises_retrieval_error(self) -> None:
        """HTTPStatusError (4xx/5xx) must be wrapped in RetrievalError."""
        chunks = _make_chunks(2)
        rr = JinaReranker(api_key="key")

        http_err = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(RetrievalError, match="Jina reranker HTTP error"):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_timeout_raises_retrieval_error(self) -> None:
        """httpx.TimeoutException must be wrapped in RetrievalError."""
        chunks = _make_chunks(2)
        rr = JinaReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ):
            with pytest.raises(RetrievalError, match="timed out"):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_connection_error_raises_retrieval_error(self) -> None:
        """httpx.ConnectError (subclass of HTTPError) → RetrievalError."""
        chunks = _make_chunks(1)
        rr = JinaReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.ConnectError("connection refused")),
        ):
            with pytest.raises(RetrievalError):
                await rr.rerank("q", chunks)


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


class TestJinaRerankerHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_200(self) -> None:
        rr = JinaReranker(api_key="key")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"results": [{"index": 0, "relevance_score": 1.0}]}

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            assert await rr.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self) -> None:
        rr = JinaReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())),
        ):
            assert await rr.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self) -> None:
        rr = JinaReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
        ):
            assert await rr.health_check() is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        rr = JinaReranker(api_key="key")
        result = await rr.close()
        assert result is None


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestJinaRerankerRegistry:
    def test_jina_registered_in_registry(self) -> None:
        from ragbot.infrastructure.reranker.registry import list_providers
        providers = list_providers()
        assert "jina" in providers

    def test_registry_builds_jina_reranker(self) -> None:
        from ragbot.infrastructure.reranker.registry import build_reranker
        rr = build_reranker("jina", api_key="dummy_test_key")
        assert isinstance(rr, JinaReranker)
        assert rr.mode == f"jina:{DEFAULT_JINA_RERANKER_MODEL}"

    def test_list_providers_sorted_includes_all_baselines(self) -> None:
        from ragbot.infrastructure.reranker.registry import list_providers
        providers = list_providers()
        assert providers == sorted(providers)
        assert "jina" in providers
        assert "litellm" in providers
        assert "null" in providers
