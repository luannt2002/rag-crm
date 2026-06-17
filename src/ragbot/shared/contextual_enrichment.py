"""Contextual Enrichment — prepend context prefix to chunks before embedding.

Kỹ thuật từ Anthropic Contextual Retrieval (2024):
- Giảm 67% retrieval failure khi kết hợp BM25 + reranking
- Cost: ~$1/M tokens (one-time at ingest)
- Mỗi chunk nhận 50-100 token prefix mô tả vị trí trong document

Prompt caching optimization (P1-3):
- Document content in system message (stable → cached by LLM provider)
- Chunk content in user message (varies per call)
- Saves ~90% prompt tokens for multi-chunk documents
"""
from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

import structlog

from ragbot.shared.constants import (
    DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS,
    DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS,
    DEFAULT_ENRICHMENT_MAX_CONCURRENCY,
    DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS,
)

logger = structlog.get_logger(__name__)

# Type alias for the LLM callback
LLMFunction = Callable[[str, str], Awaitable[str]]


def _build_cache_system_prompt(
    document_title: str,
    full_document: str,
    doc_preview_chars: int,
    total_chunks: int,
) -> str:
    """Build a stable system prompt with document content (cache-friendly)."""
    return (
        f"<document>\n"
        f"Tài liệu: {document_title}\n\n"
        f"{full_document[:doc_preview_chars]}\n"
        f"</document>\n\n"
        f"Tài liệu có {total_chunks} đoạn. "
        f"Bạn sẽ nhận từng đoạn. Với mỗi đoạn, viết 1-2 câu NGẮN mô tả vị trí và nội dung chính "
        f"của đoạn đó trong tài liệu. CHỈ trả về prefix, KHÔNG lặp lại nội dung."
    )


def _build_cache_user_prompt(
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    chunk_preview_chars: int,
) -> str:
    """Build per-chunk user prompt (varies per call → not cached)."""
    return (
        f"<chunk>\n"
        f"Đoạn {chunk_index + 1}/{total_chunks}:\n"
        f"{chunk[:chunk_preview_chars]}\n"
        f"</chunk>\n"
        f"Viết prefix ngắn (1-2 câu) cho đoạn này:"
    )


def _fallback_prefix(document_title: str, chunk_index: int, total: int) -> str:
    """Template-based enrichment prefix (no LLM cost)."""
    if chunk_index == 0:
        position = "đầu"
    elif chunk_index == total - 1:
        position = "cuối"
    else:
        position = f"giữa (phần {chunk_index + 1}/{total})"
    return f"Tài liệu: {document_title}. Đoạn {position}."


async def enrich_chunks(
    chunks: list[str],
    document_title: str,
    full_document: str,
    llm_fn: LLMFunction | None = None,
    doc_preview_chars: int = DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS,
    chunk_preview_chars: int = DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS,
    max_prefix_chars: int = DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS,
    use_cache_pattern: bool = True,
    max_concurrency: int = DEFAULT_ENRICHMENT_MAX_CONCURRENCY,
) -> list[str]:
    """Thêm context prefix vào mỗi chunk trước khi embedding.

    @param chunks: list of chunk texts
    @param document_title: tên tài liệu
    @param full_document: nội dung đầy đủ (để LLM hiểu context)
    @param llm_fn: async function(system, user) -> str, None = dùng template đơn giản
    @param doc_preview_chars: chars gửi cho LLM từ full doc
    @param chunk_preview_chars: chars gửi cho LLM từ chunk
    @param max_prefix_chars: max chars cho prefix output
    @param use_cache_pattern: use cache-friendly message structure (document in system, chunk in user)
    @param max_concurrency: max concurrent LLM calls (semaphore limit)
    @return: list of enriched chunks (prefix + original)
    """
    if not chunks:
        return []

    total = len(chunks)

    # Pre-build stable system prompt for cache pattern (same for all chunks)
    cache_system_prompt: str | None = None
    if llm_fn is not None and use_cache_pattern:
        cache_system_prompt = _build_cache_system_prompt(
            document_title=document_title,
            full_document=full_document,
            doc_preview_chars=doc_preview_chars,
            total_chunks=total,
        )

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _enrich_one(idx: int, chunk: str) -> tuple[int, str]:
        """Enrich a single chunk; returns (index, enriched_text)."""
        if llm_fn is not None:
            async with semaphore:
                try:
                    if use_cache_pattern and cache_system_prompt is not None:
                        system_msg = cache_system_prompt
                        user_msg = _build_cache_user_prompt(
                            chunk=chunk,
                            chunk_index=idx,
                            total_chunks=total,
                            chunk_preview_chars=chunk_preview_chars,
                        )
                    else:
                        system_msg = (
                            "Bạn là trợ lý tạo context cho RAG system. "
                            "Viết 1-2 câu NGẮN mô tả vị trí và nội dung chính của đoạn text trong tài liệu. "
                            "CHỈ trả về prefix, KHÔNG lặp lại nội dung."
                        )
                        user_msg = (
                            f"Tài liệu: {document_title}\n\n"
                            f"Toàn bộ tài liệu (tóm tắt):\n{full_document[:doc_preview_chars]}\n\n"
                            f"Đoạn {idx + 1}/{total}:\n{chunk[:chunk_preview_chars]}\n\n"
                            f"Viết prefix ngắn (1-2 câu) cho đoạn này:"
                        )

                    prefix = await llm_fn(system_msg, user_msg)
                    prefix = prefix.strip()
                    if prefix and len(prefix) < max_prefix_chars:
                        return idx, f"{prefix}\n\n{chunk}"
                except Exception as exc:  # noqa: BLE001 — LLM provider exception classes vary across litellm/httpx/openai; falling back to template-based enrichment is the documented contract.
                    logger.debug(
                        "contextual_enrichment_llm_failed",
                        chunk_index=idx,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

        # Fallback: template-based enrichment (no LLM cost)
        fallback = _fallback_prefix(document_title, idx, total)
        return idx, f"{fallback}\n\n{chunk}"

    tasks = [_enrich_one(i, chunk) for i, chunk in enumerate(chunks)]
    # asyncio.gather preserves submission order — output[i] corresponds to
    # input task[i] regardless of completion order — so the historical
    # ``sorted(results, key=lambda x: x[0])`` was a no-op pure overhead.
    results_sorted = await asyncio.gather(*tasks)

    logger.info(
        "contextual_enrichment_done",
        doc=document_title,
        chunks=total,
        llm_used=llm_fn is not None,
        cache_pattern=use_cache_pattern and llm_fn is not None,
        max_concurrency=max_concurrency,
    )
    return [text for _, text in results_sorted]


__all__ = ["enrich_chunks"]
