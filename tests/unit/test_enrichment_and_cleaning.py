"""Unit tests: batch contextual enrichment + document cleaning hyphenation fix."""
from __future__ import annotations

import asyncio

import pytest

from ragbot.application.services.document_service import _fix_hyphenation
from ragbot.shared.contextual_enrichment import enrich_chunks


# ── TASK 29: Hyphenation fix tests ────────────────────────────────────


class TestHyphenationFix:
    def test_hyphenation_fix_english(self) -> None:
        """ASCII hyphenation at line break should be joined without space."""
        assert _fix_hyphenation("infor-\nmation") == "information"

    def test_hyphenation_fix_vietnamese(self) -> None:
        """Vietnamese/Unicode hyphenation should be joined with space (word boundary)."""
        assert _fix_hyphenation("thông-\ntin") == "thông tin"

    def test_hyphenation_preserves_normal_hyphens(self) -> None:
        """Hyphens not at line breaks should be untouched."""
        assert _fix_hyphenation("well-known") == "well-known"

    def test_hyphenation_preserves_non_alpha(self) -> None:
        """Hyphens between non-alpha chars should be untouched."""
        assert _fix_hyphenation("123-\n456") == "123-\n456"

    def test_hyphenation_mixed(self) -> None:
        """Mixed document with Vietnamese context uses space-join for all."""
        text = "infor-\nmation and Thông-\nTin"
        result = _fix_hyphenation(text)
        # In a Vietnamese-context document, all hyphenations use space join
        assert "infor mation" in result
        assert "Thông Tin" in result

    def test_hyphenation_pure_english(self) -> None:
        """Pure English text joins without space."""
        text = "This is infor-\nmation about the topic"
        result = _fix_hyphenation(text)
        assert "information" in result


# ── TASK 27: Batch enrichment concurrency tests ──────────────────────


class TestBatchEnrichmentConcurrency:
    @pytest.mark.asyncio
    async def test_batch_enrichment_concurrency(self) -> None:
        """Verify chunks are enriched in parallel with correct order preserved."""
        call_log: list[tuple[float, int]] = []

        async def _mock_llm(system: str, user: str) -> str:
            # Extract chunk index from user message
            idx = int(user.split("Đoạn ")[1].split("/")[0]) - 1
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)  # simulate LLM latency
            call_log.append((start, idx))
            return f"Prefix for chunk {idx}"

        chunks = [f"Chunk content {i}" for i in range(10)]
        result = await enrich_chunks(
            chunks=chunks,
            document_title="Test Doc",
            full_document="Full document content here.",
            llm_fn=_mock_llm,
            max_concurrency=5,
        )

        # All chunks enriched
        assert len(result) == 10
        # Order preserved
        for i, text in enumerate(result):
            assert f"Chunk content {i}" in text
            assert "Prefix for chunk" in text

        # Concurrency check: with 10 chunks and concurrency=5,
        # there should be overlapping start times (not fully sequential)
        starts = sorted(t for t, _ in call_log)
        # At least some calls should start before the first one finishes
        # (first call takes 0.05s, if sequential all would start >0.05s apart)
        gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        # With concurrency=5, at least the first 5 should start nearly simultaneously
        small_gaps = [g for g in gaps[:4] if g < 0.02]
        assert len(small_gaps) >= 3, f"Expected concurrent execution, gaps: {gaps[:4]}"

    @pytest.mark.asyncio
    async def test_batch_enrichment_fallback_on_error(self) -> None:
        """If LLM fails for a chunk, fallback prefix is used (no crash)."""

        async def _failing_llm(system: str, user: str) -> str:
            raise RuntimeError("LLM unavailable")

        chunks = ["Chunk A", "Chunk B", "Chunk C"]
        result = await enrich_chunks(
            chunks=chunks,
            document_title="Fail Doc",
            full_document="Content",
            llm_fn=_failing_llm,
            max_concurrency=3,
        )

        assert len(result) == 3
        # All should use fallback template
        for i, text in enumerate(result):
            assert f"Tài liệu: Fail Doc" in text
            assert chunks[i] in text

    @pytest.mark.asyncio
    async def test_batch_enrichment_no_llm(self) -> None:
        """Without LLM, all chunks get template-based prefix."""
        chunks = ["First chunk", "Middle chunk", "Last chunk"]
        result = await enrich_chunks(
            chunks=chunks,
            document_title="Template Doc",
            full_document="Content",
            llm_fn=None,
            max_concurrency=5,
        )

        assert len(result) == 3
        assert "đầu" in result[0]
        assert "cuối" in result[2]

    @pytest.mark.asyncio
    async def test_batch_enrichment_respects_semaphore(self) -> None:
        """Semaphore limits concurrent calls to max_concurrency."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _tracking_llm(system: str, user: str) -> str:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            return "prefix"

        chunks = [f"Chunk {i}" for i in range(10)]
        await enrich_chunks(
            chunks=chunks,
            document_title="Sem Doc",
            full_document="Content",
            llm_fn=_tracking_llm,
            max_concurrency=3,
        )

        assert max_concurrent <= 3, f"Exceeded semaphore: {max_concurrent} concurrent"

    @pytest.mark.asyncio
    async def test_batch_enrichment_empty_chunks(self) -> None:
        """Empty chunk list returns empty result."""
        result = await enrich_chunks(
            chunks=[],
            document_title="Empty",
            full_document="Content",
            max_concurrency=5,
        )
        assert result == []
