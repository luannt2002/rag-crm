"""LLM-backed implementation of ChunkContextProviderPort.

Wraps an injected ``LLMPort`` to generate per-chunk context labels for
Contextual Retrieval (Anthropic pattern).  The ``LLMSpec`` is resolved via
``ModelResolverService`` with ``intent="contextualization"`` at construction
time — callers need no knowledge of which model is active.

Output flows ONLY to the ``document_chunks.chunk_context`` DB column, never
into any LLM answer prompt (Quality Gate #10 / HALLU=0 sacred).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from uuid import UUID

import httpx
import structlog

from ragbot.application.ports.llm_port import LLMMessage, LLMPort
from ragbot.application.services.chunk_context_enricher import (
    CHUNK_CONTEXT_PROMPT_TEMPLATE,
)
from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.shared.constants import DEFAULT_CHUNK_CONTEXT_ENRICHMENT_CONCURRENCY
from ragbot.shared.types import BotId, TenantId

logger = structlog.get_logger(__name__)


class LLMChunkContextProvider:
    """Generate per-chunk context strings using a resolved LLM binding.

    Constructor params
    ------------------
    llm:
        Injected ``LLMPort`` implementation (DynamicLiteLLMRouter in production).
    model_resolver:
        ``ModelResolverService`` used to look up the LLMSpec for
        intent="contextualization" at call time.
    record_tenant_id:
        Internal UUID for the owning tenant (multi-tenancy scope).
    record_bot_id:
        Internal UUID for the owning bot (used by resolver for per-bot binding).
    """

    def __init__(
        self,
        llm: LLMPort,
        model_resolver: ModelResolverService,
        record_tenant_id: UUID,
        record_bot_id: UUID,
    ) -> None:
        self._llm = llm
        self._model_resolver = model_resolver
        self._record_tenant_id: TenantId = record_tenant_id  # type: ignore[assignment]
        self._record_bot_id: BotId = record_bot_id  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Port contract
    # ------------------------------------------------------------------

    @staticmethod
    def get_provider_name() -> str:
        return "llm_enrichment"

    async def generate(
        self,
        *,
        doc_full_text: str,
        chunks: Sequence[str],
        max_context_tokens: int,
    ) -> list[str]:
        """Generate context labels for each chunk.

        Runs chunks with bounded concurrency (semaphore =
        ``DEFAULT_CHUNK_CONTEXT_ENRICHMENT_CONCURRENCY``).  Per-chunk failures
        return an empty string so the caller stores NULL in the optional
        column instead of failing the whole ingest batch.

        The generated strings are stored in ``document_chunks.chunk_context``
        ONLY — never injected into any answer LLM prompt.
        """
        if not chunks:
            return []

        spec = await self._model_resolver.resolve_llm(
            self._record_bot_id,
            record_tenant_id=self._record_tenant_id,
            intent="contextualization",
        )

        sem = asyncio.Semaphore(DEFAULT_CHUNK_CONTEXT_ENRICHMENT_CONCURRENCY)
        t_start = time.monotonic()

        async def _one(chunk: str) -> str:
            async with sem:
                prompt = CHUNK_CONTEXT_PROMPT_TEMPLATE.format(
                    doc=doc_full_text,
                    chunk=chunk,
                    max_tokens=max_context_tokens,
                )
                try:
                    resp = await self._llm.complete(
                        [LLMMessage(role="user", content=prompt)],
                        spec=spec,
                        record_tenant_id=self._record_tenant_id,
                        trace_id=None,  # type: ignore[arg-type] — enrichment is offline
                    )
                    return resp.content
                except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
                    logger.warning(
                        "chunk_context_provider_chunk_failed",
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    return ""
                except Exception as exc:  # noqa: BLE001 — per-chunk failure must not block ingest
                    logger.warning(
                        "chunk_context_provider_chunk_failed",
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    return ""

        # Warm-then-fan-out: run the FIRST chunk alone so the shared
        # "Document: {doc}" prompt prefix lands in the provider's prompt
        # cache, THEN fan out the remaining chunks — each reuses the cached
        # document prefix (cache-read ≈ 10% tokens) instead of re-sending the
        # full doc cold. Converts the per-batch cost from N×doc_size into
        # 1×doc_size + N×chunk_size — the canonical Anthropic Contextual-
        # Retrieval prompt-cache pattern. A cold concurrent gather sends N
        # full-doc copies simultaneously; on a large doc that alone trips the
        # org TPM cap (RateLimitError) and fails ingest. The warm call cannot
        # raise (``_one`` degrades to "" on any error), so a cache-miss simply
        # falls back to the prior cold behaviour for the remainder.
        if len(chunks) > 1:
            warmed = await _one(chunks[0])
            rest = await asyncio.gather(*[_one(c) for c in chunks[1:]])
            output: list[str] = [warmed, *rest]
        else:
            output = list(await asyncio.gather(*[_one(c) for c in chunks]))

        latency_ms = int((time.monotonic() - t_start) * 1000)
        n_non_empty = sum(1 for r in output if r)
        logger.info(
            "chunk_context_provider_complete",
            n_chunks=len(output),
            n_non_empty=n_non_empty,
            latency_ms=latency_ms,
        )
        return output
