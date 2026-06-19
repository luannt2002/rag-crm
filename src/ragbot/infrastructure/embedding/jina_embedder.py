"""Jina AI embedder — hosted multilingual embedding (jina-embeddings-v3, 1024-dim).

Direct HTTP adapter (bypasses LiteLLM). Implements ``EmbeddingPort`` so the
orchestrator swaps providers via DI / ``system_config.embedding_provider`` with
no business-logic change — mirrors :class:`ZeroEntropyEmbedder`.

Endpoint: ``POST https://api.jina.ai/v1/embeddings``
Auth: ``Authorization: Bearer <key>``
Body: ``{model, input: [str, ...], task, dimensions, late_chunking}``
Response (OpenAI-shaped): ``{data: [{embedding: [float, ...]}, ...], usage}``

Provider-specific quirks honoured per CLAUDE.md domain-neutral rule:
* ``task`` mapped from ``EmbeddingSpec.task`` (query→``retrieval.query``,
  else ``retrieval.passage``) — Jina uses task-specific heads, wrong task
  tanks recall.
* ``late_chunking``: when on, the passage path groups consecutive chunks into
  windows under a token budget and embeds each window in one long-context pass,
  so cross-chunk context lands in the vector with ZERO generative-LLM calls.
  The query path never late-chunks (a query is a single short string).
* All URLs / model names / dims / timeouts read from ``shared/constants.py`` or
  env so swapping providers stays one config row.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.application.ports.token_ledger_port import TokenLedgerPort
from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.infrastructure.llm.tpm_rate_limiter import TpmRateLimiter
from ragbot.infrastructure.token_ledger.aux_usage import emit_aux_usage
from ragbot.shared.api_key_pool import ApiKeyEntry, ApiKeyPool, ApiKeyPoolFactory
from ragbot.shared.constants import (
    DEFAULT_API_KEY_MAX_CONCURRENT,
    DEFAULT_EMBEDDER_CB_FAIL_MAX,
    DEFAULT_EMBEDDER_CB_RESET_S,
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_TASK_PASSAGE,
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_EMBEDDING_TIMEOUT_S,
    DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
    DEFAULT_JINA_EMBEDDING_API_URL,
    DEFAULT_JINA_EMBEDDING_DIM,
    DEFAULT_JINA_EMBEDDING_LATE_CHUNKING,
    DEFAULT_JINA_EMBEDDING_MODEL,
    DEFAULT_JINA_EMBEDDING_TPM_LIMIT,
    DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION,
    DEFAULT_JINA_LATE_CHUNK_WINDOW_TOKENS,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
    PROVIDER_KEY_CONCURRENCY_ENV,
)
from ragbot.shared.errors import CircuitBreakerOpen, ExternalServiceError
from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)

_RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _per_key_concurrency(provider_code: str, n_keys: int) -> list[int]:
    """Resolve the per-key max-concurrent list for ``provider_code``.

    Reads ``PROVIDER_KEY_CONCURRENCY_JSON`` (e.g. ``{"jina":[2,50]}``) — a list
    index-aligned with the provider's key ring, so a free key (2) and a paid key
    (50) coexist. Missing/short/invalid → every key falls back to
    ``DEFAULT_API_KEY_MAX_CONCURRENT``. Provider enforces concurrency PER KEY, so
    the caller's TOTAL in-flight budget = sum of this list. No hardcode: the
    default is the constant, overrides are config.
    """
    cfg: dict[str, Any] = {}
    raw = os.environ.get(PROVIDER_KEY_CONCURRENCY_ENV, "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                cfg = parsed
        except (ValueError, TypeError):
            logger.warning("provider_key_concurrency_parse_failed", env=PROVIDER_KEY_CONCURRENCY_ENV)
    raw_list = cfg.get(provider_code) or []
    out: list[int] = []
    for i in range(max(1, n_keys)):
        try:
            out.append(int(raw_list[i]))
        except (IndexError, ValueError, TypeError):
            out.append(DEFAULT_API_KEY_MAX_CONCURRENT)
    return out

# Cheap token estimate (no tiktoken dependency on the hot path). Vietnamese
# subword-tokenizes to ~2.5 chars/token (far denser than English's ~4), so we
# pick the CONSERVATIVE 2 — under-estimating chars-per-token makes the late-
# chunking window SMALLER, keeping the concatenated input safely under Jina's
# 8194-token cap (HTTP 400 INPUT_TOKEN_LIMIT_EXCEEDED otherwise on big tables).
_CHARS_PER_TOKEN: int = 2


class JinaEmbedder(EmbeddingPort):
    """Direct-HTTP embedder for Jina jina-embeddings-v3 (1024-dim multilingual)."""

    _PROVIDER_CODE: str = "jina"
    _PURPOSE: str = "embed"

    def __init__(
        self,
        model: str = DEFAULT_JINA_EMBEDDING_MODEL,
        *,
        api_url: str = DEFAULT_JINA_EMBEDDING_API_URL,
        key_pool_factory: ApiKeyPoolFactory | None = None,
        timeout_s: int = DEFAULT_EMBEDDING_TIMEOUT_S,
        dimensions: int = DEFAULT_JINA_EMBEDDING_DIM,
        late_chunking: bool = DEFAULT_JINA_EMBEDDING_LATE_CHUNKING,
        window_tokens: int = DEFAULT_JINA_LATE_CHUNK_WINDOW_TOKENS,
        ledger: TokenLedgerPort | None = None,
    ) -> None:
        self._model = model
        self._api_url = api_url
        self._timeout_s = timeout_s
        self._dimensions = dimensions
        self._ledger = ledger
        self._late_chunking = late_chunking
        self._window_chars = max(1, int(window_tokens) * _CHARS_PER_TOKEN)
        self._pool: ApiKeyPool | None = (
            key_pool_factory.get(self._PROVIDER_CODE, self._PURPOSE)
            if key_pool_factory is not None
            else None
        )
        # Concurrency + TPM scale with the KEY RING: provider enforces both per
        # key, so N keys round-robin give N× headroom. total_concurrent = sum of
        # per-key caps (config-driven, default constant). The TPM limiter is
        # instance-level (the embedder is a Singleton) sized = per-key TPM × keys.
        n_keys = self._pool.key_count if self._pool is not None else 1
        per_key = _per_key_concurrency(self._PROVIDER_CODE, n_keys)
        total_concurrent = sum(per_key)
        self._tpm_limiter = TpmRateLimiter(
            int(DEFAULT_JINA_EMBEDDING_TPM_LIMIT * n_keys * DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION)
        )
        logger.info(
            "jina_embedder_concurrency",
            n_keys=n_keys, per_key=per_key, total_concurrent=total_concurrent,
        )
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(total_concurrent)
        self._cb: CircuitBreaker = CircuitBreaker(
            name="embedder:jina",
            policy=CircuitBreakerPolicy(
                fail_max=DEFAULT_EMBEDDER_CB_FAIL_MAX,
                reset_timeout_s=DEFAULT_EMBEDDER_CB_RESET_S,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def _resolve_key(self) -> tuple[str | None, ApiKeyEntry | None]:
        if self._pool is not None:
            entry = await self._pool.get_active()
            return entry.key, entry
        legacy = (
            os.environ.get("EMBEDDING_JINA_API_KEY")
            or os.environ.get("JINA_API_KEY")
        )
        return legacy, None

    def _is_query(self, spec: EmbeddingSpec | None) -> bool:
        task = getattr(spec, "task", None) if spec is not None else None
        return isinstance(task, str) and task.lower() in {
            "query", "retrieval.query", "search_query",
        }

    def _strip_prefix(self, spec: EmbeddingSpec | None) -> str:
        raw = spec.model_name if spec and spec.model_name else self._model
        # ``jina_ai/jina-embeddings-v3`` → ``jina-embeddings-v3`` (Jina's API
        # accepts the bare model id only; the LiteLLM prefix is routing-internal).
        return raw.split("/", 1)[1] if "/" in raw else raw

    async def health_check(self) -> bool:
        try:
            key, _ = await self._resolve_key()
            if not key:
                return False
            client = await self._get_client()
            resp = await asyncio.wait_for(
                client.post(
                    self._api_url,
                    json={
                        "model": self._strip_prefix(None),
                        "input": ["health"],
                        "task": DEFAULT_EMBEDDING_TASK_PASSAGE,
                        "dimensions": self._dimensions,
                    },
                    headers={"Authorization": f"Bearer {key}"},
                ),
                timeout=DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S,
            )
            if resp.status_code != 200:
                return False
            try:
                payload = resp.json()
                vec = (payload.get("data") or [{}])[0].get("embedding") or []
                if len(vec) != self._dimensions:
                    logger.warning(
                        "embedder_health_check_dim_mismatch",
                        expected=self._dimensions, actual=len(vec), model=self._model,
                    )
                    return False
            except (ValueError, KeyError, TypeError):
                return False
            return True
        except (TimeoutError, httpx.HTTPError, OSError):
            logger.warning("embedder_health_check_failed", model=self._model, exc_info=True)
            return False

    def _window_passages(self, texts: list[str]) -> list[list[str]]:
        """Group consecutive chunks into windows under the token budget.

        Late chunking needs the chunks of ONE window concatenated in one call so
        the long-context pass sees their shared context. We pack greedily by char
        budget; a single oversized chunk becomes its own window (still valid —
        late chunking degrades to plain chunk embedding for that one).
        """
        windows: list[list[str]] = []
        cur: list[str] = []
        cur_chars = 0
        for t in texts:
            tlen = len(t)
            if cur and cur_chars + tlen > self._window_chars:
                windows.append(cur)
                cur, cur_chars = [], 0
            cur.append(t)
            cur_chars += tlen
        if cur:
            windows.append(cur)
        return windows

    async def _post_embed(
        self, inputs: list[str], *, model: str, task: str, key: str, late: bool,
    ) -> list[list[float]]:
        client = await self._get_client()
        # Pace under the per-key TPM ceiling BEFORE firing — a burst of windows
        # queues here instead of 429-storming. Estimate = total input chars over
        # the (conservative) chars/token ratio.
        est_tokens = sum(len(t) for t in inputs) // _CHARS_PER_TOKEN + 1
        await self._tpm_limiter.acquire(model, est_tokens)

        async def _call() -> list[list[float]]:
            body: dict[str, Any] = {
                "model": model,
                "input": inputs,
                "task": task,
                "dimensions": self._dimensions,
                # Safety net for a single pathological chunk (e.g. one giant table
                # row) that alone exceeds Jina's 8194-token cap — clip it rather
                # than 400 the whole batch. Normal chunks/windows are well under
                # the cap (see _window_passages), so truncation never fires for
                # them; this only saves the rare oversized outlier.
                "truncate": True,
            }
            if late:
                body["late_chunking"] = True
            _emb_t0 = datetime.now(UTC)
            async with asyncio.timeout(self._timeout_s):
                resp = await client.post(
                    self._api_url, json=body,
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code != 200:
                    if resp.status_code in _RETRYABLE_HTTP_STATUS:
                        raise httpx.HTTPStatusError(
                            f"retryable HTTP {resp.status_code}: {resp.text[:200]}",
                            request=resp.request, response=resp,
                        )
                    raise ExternalServiceError(
                        f"Jina embed failed: HTTP {resp.status_code}: {resp.text[:300]}",
                    )
                data: dict[str, Any] = resp.json()
                rows = data.get("data") or []
                # Log-center: snapshot Jina embedding token usage (action="embedding").
                _u = data.get("usage") or {}
                emit_aux_usage(
                    self._ledger,
                    action="embedding",
                    provider=self._PROVIDER_CODE,
                    model=self._model,
                    input_tokens=int(_u.get("prompt_tokens", 0) or 0),
                    total_tokens=int(_u.get("total_tokens", 0) or 0),
                    started_at=_emb_t0,
                    finished_at=datetime.now(UTC),
                )
                # Jina returns rows with an ``index`` field; sort to guarantee
                # alignment with the input order before stripping to vectors.
                rows = sorted(rows, key=lambda r: r.get("index", 0))
                return [row["embedding"] for row in rows]

        try:
            async with self._sem:
                with self._cb:
                    return await retry_with_backoff(
                        _call,
                        policy=RetryPolicy(
                            max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS,
                            initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS,
                            max_backoff_ms=DEFAULT_RETRY_MAX_MS,
                        ),
                        retryable_exceptions=(
                            OSError, ConnectionError, TimeoutError,
                            httpx.HTTPStatusError, httpx.TimeoutException, httpx.HTTPError,
                        ),
                    )
        except CircuitBreakerOpen as exc:
            logger.warning("embedder_circuit_open", model=model)
            raise ExternalServiceError(f"embedding CB open: {exc}") from exc
        except ExternalServiceError:
            raise
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise ExternalServiceError(
                f"Jina embedding API failed after retries: {exc}",
            ) from exc

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> list[list[float]]:
        if not texts:
            return []
        model = self._strip_prefix(spec)
        key, _entry = await self._resolve_key()
        if not key:
            raise ExternalServiceError(
                "Jina embedding API key not configured "
                "(set EMBEDDING_JINA_API_KEY or JINA_API_KEY)",
            )

        is_query = self._is_query(spec)
        task = DEFAULT_EMBEDDING_TASK_QUERY if is_query else DEFAULT_EMBEDDING_TASK_PASSAGE
        # Late chunking only makes sense for passages (indexing). A query is one
        # short string with no neighbours to draw context from.
        late = self._late_chunking and not is_query

        all_results: list[list[float]] = []
        if late:
            # Each window = consecutive chunks embedded together (context-aware).
            for window in self._window_passages(texts):
                all_results.extend(
                    await self._post_embed(window, model=model, task=task, key=key, late=True),
                )
        else:
            for start in range(0, len(texts), DEFAULT_EMBEDDING_MAX_BATCH):
                batch = texts[start : start + DEFAULT_EMBEDDING_MAX_BATCH]
                all_results.extend(
                    await self._post_embed(batch, model=model, task=task, key=key, late=False),
                )
        return all_results

    async def embed_one(
        self,
        text: str,
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,
    ) -> list[float]:
        result = await self.embed_batch([text], spec=spec, record_tenant_id=record_tenant_id)
        return result[0]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["JinaEmbedder"]
