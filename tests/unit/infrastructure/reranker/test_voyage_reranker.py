"""Unit tests for VoyageReranker — mocked httpx, no real API calls.

Coverage matches Plan F3 minimum 7 tests:
1. test_rerank_basic — payload + headers shape, top-N output enriched
2. test_rerank_empty_documents → []
3. test_rerank_top_n_cap — top_n > len(docs) → API capped to len
4. test_circuit_breaker_open — 5 consecutive transport failures trip CB
5. test_health_check_success — 200 OK with data → True
6. test_health_check_failure_returns_false — 500 → False (no raise)
7. test_api_error_propagates_through_circuit — httpx.HTTPError → RetrievalError
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ragbot.infrastructure.reranker.voyage_reranker import VoyageReranker
from ragbot.shared.constants import (
    DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX,
    DEFAULT_VOYAGE_RERANK_MODEL,
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


def _voyage_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Voyage rerank response envelope (Cohere-style ``data`` field)."""
    return {"model": DEFAULT_VOYAGE_RERANK_MODEL, "data": results}


def _make_response(
    results: list[dict[str, Any]],
    status_code: int = 200,
) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = _voyage_response(results)
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
# 1. test_rerank_basic — payload shape + Bearer header + enrichment
# ---------------------------------------------------------------------------


class TestVoyageRerankerBasic:
    @pytest.mark.asyncio
    async def test_rerank_basic(self) -> None:
        chunks = _make_chunks(3)
        api_results = [
            {"index": 0, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.80},
        ]
        mock_resp = _make_response(api_results)
        rr = VoyageReranker(
            api_key="voyage_key_test", model=DEFAULT_VOYAGE_RERANK_MODEL,
        )

        captured_payload: dict[str, Any] = {}
        captured_headers: dict[str, str] = {}

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs) -> MagicMock:  # type: ignore[override]
            captured_payload.update(json)
            captured_headers.update(headers)
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            out = await rr.rerank("query text", chunks, top_n=2)

        # Payload shape — Voyage uses top_k (not top_n) and "documents".
        assert captured_payload["model"] == DEFAULT_VOYAGE_RERANK_MODEL
        assert captured_payload["query"] == "query text"
        assert captured_payload["documents"] == [
            "document text 0",
            "document text 1",
            "document text 2",
        ]
        assert captured_payload["top_k"] == 2
        # dimensions=0 (default) must NOT be forwarded — clean default body.
        assert "dimensions" not in captured_payload
        # Bearer auth header carries the key value.
        assert captured_headers["Authorization"] == "Bearer voyage_key_test"

        # Output enrichment.
        assert len(out) == 2
        assert out[0]["id"] == "chunk-0"
        assert out[0]["rerank_score"] == pytest.approx(0.95)
        assert out[0]["retrieval_score"] == pytest.approx(0.9)
        assert out[0]["score"] == pytest.approx(0.95)
        assert out[0]["reranker_used"] == f"voyage:{DEFAULT_VOYAGE_RERANK_MODEL}"


# ---------------------------------------------------------------------------
# 2. test_rerank_empty_documents
# ---------------------------------------------------------------------------


class TestVoyageRerankerEmpty:
    @pytest.mark.asyncio
    async def test_rerank_empty_documents(self) -> None:
        rr = VoyageReranker(api_key="key")
        # No httpx mock needed — the adapter must short-circuit before any
        # network call when there are no chunks to rank.
        result = await rr.rerank("any query", [])
        assert result == []


# ---------------------------------------------------------------------------
# 3. test_rerank_top_n_cap — top_n > len(docs)
# ---------------------------------------------------------------------------


class TestVoyageRerankerTopNCap:
    @pytest.mark.asyncio
    async def test_rerank_top_n_cap(self) -> None:
        """When caller asks for more results than there are docs, the
        payload ``top_k`` must be clamped to ``len(documents)`` so the
        upstream never sees an over-shoot."""
        chunks = _make_chunks(2)
        api_results = [
            {"index": 0, "relevance_score": 0.91},
            {"index": 1, "relevance_score": 0.71},
        ]
        mock_resp = _make_response(api_results)
        rr = VoyageReranker(api_key="key")

        captured: dict[str, Any] = {}

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs) -> MagicMock:  # type: ignore[override]
            captured.update(json)
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            out = await rr.rerank("q", chunks, top_n=10)

        assert captured["top_k"] == 2  # capped to len(documents)
        assert len(out) == 2


# ---------------------------------------------------------------------------
# 4. test_circuit_breaker_open — repeated transport errors trip CB
# ---------------------------------------------------------------------------


class TestVoyageRerankerCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_open(self) -> None:
        """After ``DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX`` consecutive failures,
        the CB opens and subsequent calls fast-fail with ``RetrievalError``
        (CircuitBreakerOpen wrapped) WITHOUT a network call."""
        chunks = _make_chunks(1)
        rr = VoyageReranker(api_key="key")

        # Drive 5 failures so the CB tips OPEN. Each call uses a fresh
        # AsyncMock to avoid retry-policy collapsing internal attempts.
        http_status_err = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        fail_mock = MagicMock(spec=httpx.Response)
        fail_mock.raise_for_status.side_effect = http_status_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=fail_mock)):
            for _ in range(DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX):
                with pytest.raises(RetrievalError):
                    await rr.rerank("q", chunks, top_n=1)

        # Now CB must be OPEN; the next call short-circuits before any
        # http.post is even attempted.
        no_call_mock = AsyncMock(side_effect=AssertionError("must not be called"))
        with patch.object(httpx.AsyncClient, "post", new=no_call_mock):
            with pytest.raises(RetrievalError, match="CB open"):
                await rr.rerank("q", chunks, top_n=1)
        no_call_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 5. test_health_check_success
# ---------------------------------------------------------------------------


class TestVoyageRerankerHealthCheckSuccess:
    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        rr = VoyageReranker(api_key="key")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": [{"index": 0, "relevance_score": 1.0}],
        }

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            assert await rr.health_check() is True


# ---------------------------------------------------------------------------
# 6. test_health_check_failure_returns_false
# ---------------------------------------------------------------------------


class TestVoyageRerankerHealthCheckFailure:
    @pytest.mark.asyncio
    async def test_health_check_failure_returns_false(self) -> None:
        """A 500 from the API must NOT raise out of health_check — the
        probe is a liveness signal; callers branch on the bool."""
        rr = VoyageReranker(api_key="key")
        http_err = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            assert await rr.health_check() is False


# ---------------------------------------------------------------------------
# 7. test_api_error_propagates_through_circuit
# ---------------------------------------------------------------------------


class TestVoyageRerankerApiError:
    @pytest.mark.asyncio
    async def test_api_error_propagates_through_circuit(self) -> None:
        """A raw ``httpx.HTTPError`` (e.g. transport-level RequestError)
        from the underlying client must surface as ``RetrievalError`` so
        the caller falls back to NullReranker rather than crashing the
        request handler."""
        chunks = _make_chunks(2)
        rr = VoyageReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(
                side_effect=httpx.HTTPError("transport degraded"),
            ),
        ):
            with pytest.raises(RetrievalError, match="HTTP error"):
                await rr.rerank("q", chunks)


# ---------------------------------------------------------------------------
# Registry integration (sanity — proves the "voyage" key is wired)
# ---------------------------------------------------------------------------


class TestVoyageRerankerRegistry:
    def test_voyage_registered_in_registry(self) -> None:
        from ragbot.infrastructure.reranker.registry import list_providers
        providers = list_providers()
        assert "voyage" in providers

    def test_registry_builds_voyage_reranker(self) -> None:
        from ragbot.infrastructure.reranker.registry import build_reranker
        rr = build_reranker("voyage", api_key="dummy_test_key")
        assert isinstance(rr, VoyageReranker)
        assert rr.mode == f"voyage:{DEFAULT_VOYAGE_RERANK_MODEL}"
