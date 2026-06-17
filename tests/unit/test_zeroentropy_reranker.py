"""Unit tests for ZeroEntropyReranker — mocked httpx, no real API calls.

Coverage:
1.  test_constructor_requires_api_key — ValueError when api_key is empty
2.  test_mode_includes_model_name — mode property format "zeroentropy:<model>"
3.  test_rerank_empty_chunks_returns_empty — short-circuit guard
4.  test_rerank_calls_api_with_correct_payload — payload structure verified
5.  test_rerank_returns_top_n_sorted_by_score — output sorted by relevance
6.  test_rerank_preserves_chunk_metadata — all original fields propagated
7.  test_rerank_handles_content_or_text_key — flexible input key
8.  test_rerank_caps_at_max_docs — truncate input before sending
9.  test_rerank_4xx_raises_retrieval_error — HTTP 401/403 → RetrievalError
10. test_rerank_5xx_raises_retrieval_error — HTTP 503 → RetrievalError
11. test_rerank_timeout_raises_retrieval_error — TimeoutException → RetrievalError
12. test_rerank_score_rounded_6_decimals — precision guarantee
13. test_rerank_top_n_trim — top_n=2 selects only 2 enriched results
14. test_health_check_returns_true_on_200 — health_check happy path
15. test_health_check_returns_false_on_error — health_check fail-soft
16. test_registry_registers_zeroentropy — registry integration
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ragbot.infrastructure.reranker.zeroentropy_reranker import ZeroEntropyReranker
from ragbot.shared.constants import (
    DEFAULT_ZEROENTROPY_RERANKER_LATENCY_MODE,
    DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS,
    DEFAULT_ZEROENTROPY_RERANKER_MODEL,
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


def _ze_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": DEFAULT_ZEROENTROPY_RERANKER_MODEL, "results": results}


def _make_response(
    results: list[dict[str, Any]],
    status_code: int = 200,
) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = _ze_response(results)
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


class TestZeroEntropyRerankerConstructor:
    def test_constructor_requires_api_key(self, monkeypatch) -> None:
        """Empty api_key + no pool + no env fallback must raise ValueError
        immediately — adapter refuses to construct without a credential."""
        monkeypatch.delenv("RERANKER_ZEROENTROPY_API_KEY", raising=False)
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)
        with pytest.raises(ValueError, match="non-empty api_key"):
            ZeroEntropyReranker(api_key="")

    def test_constructor_accepts_valid_key(self) -> None:
        rr = ZeroEntropyReranker(api_key="ze_test_key_abc123")
        assert rr is not None

    def test_constructor_reads_env_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("RERANKER_ZEROENTROPY_API_KEY", "env_key_xyz")
        rr = ZeroEntropyReranker(api_key="")
        assert rr.mode.startswith("zeroentropy:")

    def test_mode_includes_model_name(self) -> None:
        rr = ZeroEntropyReranker(api_key="key", model="zerank-2")
        assert rr.mode == "zeroentropy:zerank-2"

    def test_mode_reflects_custom_model(self) -> None:
        rr = ZeroEntropyReranker(api_key="key", model="zerank-1-small")
        assert rr.mode == "zeroentropy:zerank-1-small"

    def test_get_provider_name_is_zeroentropy(self) -> None:
        assert ZeroEntropyReranker.get_provider_name() == "zeroentropy"


# ---------------------------------------------------------------------------
# rerank() — happy path
# ---------------------------------------------------------------------------


class TestZeroEntropyRerankerRerank:
    @pytest.mark.asyncio
    async def test_rerank_empty_chunks_returns_empty(self) -> None:
        rr = ZeroEntropyReranker(api_key="key")
        result = await rr.rerank("any query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_rerank_calls_api_with_correct_payload(self) -> None:
        """Verify the exact JSON payload sent to ZeroEntropy API."""
        chunks = _make_chunks(3)
        api_results = [
            {"index": 0, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.80},
        ]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(
            api_key="ze_key_test", model=DEFAULT_ZEROENTROPY_RERANKER_MODEL,
        )

        captured_payload: dict[str, Any] = {}
        captured_headers: dict[str, str] = {}

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs) -> MagicMock:  # type: ignore[override]
            captured_payload.update(json)
            captured_headers.update(headers)
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("query text", chunks, top_n=2)

        assert captured_payload["model"] == DEFAULT_ZEROENTROPY_RERANKER_MODEL
        assert captured_payload["query"] == "query text"
        assert captured_payload["documents"] == [
            "document text 0",
            "document text 1",
            "document text 2",
        ]
        assert captured_payload["top_n"] == 2
        # Default latency mode is "slow" → 5 MB/min free-tier quota (10× the
        # 500 KB/min "fast" ceiling) for ~+400ms latency. Sent in the payload.
        assert DEFAULT_ZEROENTROPY_RERANKER_LATENCY_MODE == "slow"
        assert captured_payload["latency"] == "slow"
        # Bearer auth header carries the key value.
        assert captured_headers["Authorization"] == "Bearer ze_key_test"

    @pytest.mark.asyncio
    async def test_rerank_includes_latency_when_explicitly_set(self) -> None:
        """An explicit latency mode (ops pin) IS sent in the payload."""
        chunks = _make_chunks(2)
        mock_resp = _make_response([{"index": 0, "relevance_score": 0.9}])
        rr = ZeroEntropyReranker(api_key="ze_key_test", latency="slow")

        captured: dict[str, Any] = {}

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):  # type: ignore[override]
            captured.update(json)
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("q", chunks)

        assert captured.get("latency") == "slow"

    @pytest.mark.asyncio
    async def test_rerank_returns_top_n_sorted_by_score(self) -> None:
        chunks = _make_chunks(3)
        # API returns sorted desc; adapter must preserve that order.
        api_results = [
            {"index": 1, "relevance_score": 0.90},
            {"index": 0, "relevance_score": 0.75},
        ]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("query", chunks, top_n=2)

        assert len(out) == 2
        # First result must carry the highest rerank_score.
        assert out[0]["rerank_score"] >= out[1]["rerank_score"]
        assert out[0]["id"] == "chunk-1"
        assert out[0]["rerank_score"] == pytest.approx(0.90)
        assert out[1]["id"] == "chunk-0"
        assert out[1]["rerank_score"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_rerank_top_n_trim(self) -> None:
        """Adapter returns exactly the rows the API gave us (top_n trim
        happens upstream + via the ``top_n`` payload field)."""
        chunks = _make_chunks(5)
        # Even though we have 5 input chunks, the API returns 2 (top_n=2).
        api_results = [
            {"index": 3, "relevance_score": 0.88},
            {"index": 0, "relevance_score": 0.71},
        ]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("query", chunks, top_n=2)

        assert len(out) == 2
        assert {o["id"] for o in out} == {"chunk-3", "chunk-0"}

    @pytest.mark.asyncio
    async def test_rerank_preserves_chunk_metadata(self) -> None:
        """All original chunk fields must survive the rerank enrichment."""
        chunks = _make_chunks(2)
        api_results = [{"index": 0, "relevance_score": 0.88}]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("query", chunks, top_n=1)

        assert len(out) == 1
        chunk = out[0]
        # Original fields preserved.
        assert chunk["id"] == "chunk-0"
        assert chunk["source"] == "doc_0.pdf"
        assert chunk["document_id"] == "docid-0"
        assert chunk["content"] == "document text 0"
        # Enrichment fields added.
        assert "rerank_score" in chunk
        assert "retrieval_score" in chunk
        assert "reranker_used" in chunk
        assert chunk["reranker_used"].startswith("zeroentropy:")

    @pytest.mark.asyncio
    async def test_rerank_handles_content_or_text_key(self) -> None:
        """Chunks may carry 'text' instead of 'content' — both must work."""
        chunks_text_key = _make_chunks(2, key="text")
        api_results = [{"index": 0, "relevance_score": 0.70}]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        captured_docs: list[str] = []

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):  # type: ignore[override]
            captured_docs.extend(json.get("documents", []))
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("query", chunks_text_key, top_n=1)

        assert captured_docs == ["document text 0", "document text 1"]

    @pytest.mark.asyncio
    async def test_rerank_caps_at_max_docs(self) -> None:
        """Input beyond the per-request cap must be truncated."""
        chunks = _make_chunks(DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS + 6)
        api_results = [
            {"index": i, "relevance_score": 0.5} for i in range(10)
        ]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        captured_doc_count: list[int] = []

        async def _fake_post(url: str, *, json: dict, headers: dict, **kwargs):  # type: ignore[override]
            captured_doc_count.append(len(json.get("documents", [])))
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=_fake_post)):
            await rr.rerank("query", chunks, top_n=5)

        assert captured_doc_count[0] == DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS

    @pytest.mark.asyncio
    async def test_rerank_score_rounded_6_decimals(self) -> None:
        """rerank_score must be rounded to exactly 6 decimal places."""
        chunks = _make_chunks(1)
        api_results = [{"index": 0, "relevance_score": 0.123456789}]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("q", chunks, top_n=1)

        assert out[0]["rerank_score"] == pytest.approx(0.123457, abs=1e-6)

    @pytest.mark.asyncio
    async def test_rerank_stores_retrieval_score(self) -> None:
        """Original 'score' (retrieval RRF) must be preserved in retrieval_score."""
        chunks = [{"id": "c1", "content": "text", "score": 0.033}]
        api_results = [{"index": 0, "relevance_score": 0.85}]
        mock_resp = _make_response(api_results)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            out = await rr.rerank("q", chunks, top_n=1)

        assert out[0]["retrieval_score"] == pytest.approx(0.033)
        # Adapter overwrites the canonical "score" with the rerank value
        # so downstream filters see the cross-encoder score, not RRF.
        assert out[0]["score"] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# rerank() — error paths
# ---------------------------------------------------------------------------


class TestZeroEntropyRerankerErrors:
    @pytest.mark.asyncio
    async def test_rerank_4xx_raises_retrieval_error(self) -> None:
        """HTTP 401 (auth) → RetrievalError; caller falls back to Null."""
        chunks = _make_chunks(2)
        rr = ZeroEntropyReranker(api_key="key")

        http_err = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401  # _do_post reads resp.status_code (transient-5xx guard) before raise_for_status
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(RetrievalError, match="ZeroEntropy reranker HTTP error"):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_403_raises_retrieval_error(self) -> None:
        """HTTP 403 (forbidden / out of balance) → RetrievalError."""
        chunks = _make_chunks(1)
        rr = ZeroEntropyReranker(api_key="key")

        http_err = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 403  # 403 not in transient set → falls through to raise_for_status
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(RetrievalError):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_5xx_raises_retrieval_error(self) -> None:
        """HTTP 503 (transport/upstream outage) → RetrievalError after retry.

        Retry policy retries 5xx transparently, then surfaces as
        RetrievalError so the caller falls back to NullReranker (no raise
        leak)."""
        chunks = _make_chunks(2)
        rr = ZeroEntropyReranker(api_key="key")

        http_err = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 503  # transient → _do_post raises ConnectError, retry exhausts → RetrievalError
        mock_resp.text = ""
        mock_resp.raise_for_status.side_effect = http_err

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(RetrievalError):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_timeout_raises_retrieval_error(self) -> None:
        """httpx.TimeoutException must be wrapped in RetrievalError."""
        chunks = _make_chunks(2)
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ):
            with pytest.raises(RetrievalError, match="timed out"):
                await rr.rerank("q", chunks)

    @pytest.mark.asyncio
    async def test_rerank_connection_error_raises_retrieval_error(self) -> None:
        """httpx.ConnectError → RetrievalError (transport degraded)."""
        chunks = _make_chunks(1)
        rr = ZeroEntropyReranker(api_key="key")

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


class TestZeroEntropyRerankerHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_200(self) -> None:
        rr = ZeroEntropyReranker(api_key="key")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [{"index": 0, "relevance_score": 1.0}],
        }

        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_resp)):
            assert await rr.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self) -> None:
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.HTTPStatusError(
                "503", request=MagicMock(), response=MagicMock(),
            )),
        ):
            assert await rr.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self) -> None:
        rr = ZeroEntropyReranker(api_key="key")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
        ):
            assert await rr.health_check() is False

    @pytest.mark.asyncio
    async def test_close_releases_client(self) -> None:
        rr = ZeroEntropyReranker(api_key="key")
        # Force-create the lazy client so close() has something to release.
        await rr._get_client()  # type: ignore[attr-defined]
        assert rr._client is not None  # type: ignore[attr-defined]
        await rr.close()
        assert rr._client is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Key-pool cooldown differentiation — 429 (transient BPM) vs 403 (forbidden)
# ---------------------------------------------------------------------------


class TestZeroEntropyRerankerCooldown:
    """``_handle_status_error`` must cool a 429'd key only briefly (the
    per-minute quota refills in ~60s) while a 403 keeps the long default —
    otherwise a rate-limited key drops out of the round-robin for 5 minutes
    and cascades the remaining keys into the same wall."""

    def _pool_spy(self):
        from ragbot.shared.api_key_pool import ApiKeyEntry

        pool = MagicMock()
        pool.provider_code = "zeroentropy"
        pool.purpose = "rerank"
        pool.mark_cooldown = AsyncMock()
        pool.get_active = AsyncMock(
            return_value=ApiKeyEntry(key="k2", label="secondary")
        )
        return pool

    @pytest.mark.asyncio
    async def test_429_uses_short_ratelimit_cooldown(self) -> None:
        from ragbot.shared.api_key_pool import ApiKeyEntry
        from ragbot.shared.constants import DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S

        pool = self._pool_spy()
        rr = ZeroEntropyReranker(api_key="seed", key_pool=pool)
        entry = ApiKeyEntry(key="k1", label="primary")
        exc = httpx.HTTPStatusError(
            "429", request=MagicMock(),
            response=MagicMock(status_code=429),
        )

        await rr._handle_status_error(exc, entry)  # type: ignore[attr-defined]

        pool.mark_cooldown.assert_awaited_once()
        kwargs = pool.mark_cooldown.await_args.kwargs
        assert kwargs["cooldown_s"] == DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S
        assert kwargs["reason"] == "HTTP_429"

    @pytest.mark.asyncio
    async def test_403_keeps_long_default_cooldown(self) -> None:
        from ragbot.shared.api_key_pool import ApiKeyEntry

        pool = self._pool_spy()
        rr = ZeroEntropyReranker(api_key="seed", key_pool=pool)
        entry = ApiKeyEntry(key="k1", label="primary")
        exc = httpx.HTTPStatusError(
            "403", request=MagicMock(),
            response=MagicMock(status_code=403),
        )

        await rr._handle_status_error(exc, entry)  # type: ignore[attr-defined]

        pool.mark_cooldown.assert_awaited_once()
        kwargs = pool.mark_cooldown.await_args.kwargs
        # None → pool falls back to its long default TTL.
        assert kwargs["cooldown_s"] is None
        assert kwargs["reason"] == "HTTP_403"

    @pytest.mark.asyncio
    async def test_429_then_200_self_heals_via_key_rotation(self) -> None:
        """A real 429 must NOT degrade to RRF: ``_do_post`` cools the hot key
        and the retry round-robins onto a fresh key that returns 200. The
        rerank succeeds end-to-end with the throttled key cooled briefly."""
        from ragbot.shared.api_key_pool import ApiKeyPool
        from ragbot.shared.constants import DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S

        # Real pool over 3 keys so per-attempt rotation is genuine.
        class _Redis:
            def __init__(self) -> None:
                self.store: dict[str, str] = {}
                self.ttls: list[int] = []

            async def get(self, k: str):
                return self.store.get(k)

            async def set(self, k: str, v: str, *, ex: int | None = None):
                self.store[k] = v
                self.ttls.append(int(ex or 0))
                return True

        redis = _Redis()
        pool = ApiKeyPool(
            primary="k1", secondary="k2", extras=["k3"],
            redis_client=redis, provider_code="zeroentropy", purpose="rerank",
        )
        rr = ZeroEntropyReranker(api_key="seed", key_pool=pool)
        chunks = _make_chunks(2)

        # First POST → 429; second POST → 200 with valid results.
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.text = '{"detail":"BPM ratelimit"}'
        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.raise_for_status = MagicMock(return_value=None)
        resp_200.json = MagicMock(
            return_value=_ze_response([{"index": 0, "relevance_score": 0.9}])
        )
        post = AsyncMock(side_effect=[resp_429, resp_200])

        with patch.object(httpx.AsyncClient, "post", new=post):
            out = await rr.rerank("q", chunks)

        # Succeeded — no RetrievalError, real reranked output returned.
        assert out and out[0]["reranker_used"] == "zeroentropy:zerank-2"
        # Exactly one key cooled, with the SHORT rate-limit TTL.
        assert redis.ttls == [DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S]
        assert post.await_count == 2  # retried on a different key

    @pytest.mark.asyncio
    async def test_503_rate_limit_body_cools_key_like_429(self) -> None:
        """A 503 whose body is the ZE 'Rate limit … could not be met' message
        is a per-key rate signal, NOT an outage — it must cool the key + rotate
        (same as 429), then self-heal on a fresh key. A 503 WITHOUT that body
        stays a plain transient (no cooldown)."""
        from ragbot.shared.api_key_pool import ApiKeyPool
        from ragbot.shared.constants import DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S

        class _Redis:
            def __init__(self) -> None:
                self.store: dict[str, str] = {}
                self.ttls: list[int] = []

            async def get(self, k: str):
                return self.store.get(k)

            async def set(self, k: str, v: str, *, ex: int | None = None):
                self.store[k] = v
                self.ttls.append(int(ex or 0))
                return True

        redis = _Redis()
        pool = ApiKeyPool(
            primary="k1", secondary="k2", extras=["k3"],
            redis_client=redis, provider_code="zeroentropy", purpose="rerank",
        )
        rr = ZeroEntropyReranker(api_key="seed", key_pool=pool)
        chunks = _make_chunks(2)

        resp_503 = MagicMock(spec=httpx.Response)
        resp_503.status_code = 503
        resp_503.text = (
            '{"detail":"Rate limit for `\\"fast\\"` could not be met, '
            'please request a latency of `\\"slow\\"` or `None`."}'
        )
        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.raise_for_status = MagicMock(return_value=None)
        resp_200.json = MagicMock(
            return_value=_ze_response([{"index": 0, "relevance_score": 0.9}])
        )
        post = AsyncMock(side_effect=[resp_503, resp_200])

        with patch.object(httpx.AsyncClient, "post", new=post):
            out = await rr.rerank("q", chunks)

        assert out  # self-healed, not degraded
        assert redis.ttls == [DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S]  # cooled short
        assert post.await_count == 2


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestZeroEntropyRerankerRegistry:
    def test_zeroentropy_registered_in_registry(self) -> None:
        from ragbot.infrastructure.reranker.registry import list_providers
        providers = list_providers()
        assert "zeroentropy" in providers

    def test_registry_builds_zeroentropy_reranker(self) -> None:
        from ragbot.infrastructure.reranker.registry import build_reranker
        rr = build_reranker("zeroentropy", api_key="dummy_test_key")
        assert isinstance(rr, ZeroEntropyReranker)
        assert rr.mode == f"zeroentropy:{DEFAULT_ZEROENTROPY_RERANKER_MODEL}"

    def test_list_providers_includes_zeroentropy_and_baselines(self) -> None:
        from ragbot.infrastructure.reranker.registry import list_providers
        providers = list_providers()
        # Stable sort guarantees test determinism.
        assert providers == sorted(providers)
        assert "zeroentropy" in providers
        assert "jina" in providers
        assert "null" in providers
