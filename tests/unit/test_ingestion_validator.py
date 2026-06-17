"""Unit tests: ingestion quality validator — chunk quality checks."""
from __future__ import annotations

import pytest

from ragbot.shared.ingestion_validator import validate_ingestion, _jaccard_similarity


# ── Helpers ─────────────────────────────────────────────────────────────


def _chunk(content: str, embedding=None) -> dict:
    d = {"content": content}
    if embedding is not None:
        d["embedding"] = embedding
    return d


# ── validate_ingestion ──────────────────────────────────────────────────


class TestValidateIngestion:
    @pytest.mark.asyncio
    async def test_all_valid_chunks_ok(self):
        chunks = [
            _chunk("This is a valid chunk with enough characters to pass."),
            _chunk("Another valid chunk with sufficient content here."),
        ]
        result = await validate_ingestion(chunks, "test.pdf")
        assert result["ok"] is True
        assert result["issues"] == []
        assert result["score"] > 0.9

    @pytest.mark.asyncio
    async def test_empty_chunk_reports_issue(self):
        chunks = [
            _chunk("Valid chunk content with enough text."),
            _chunk(""),
        ]
        result = await validate_ingestion(chunks, "test.pdf")
        assert result["ok"] is False
        assert any("empty chunk" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_chunk_too_small(self):
        chunks = [_chunk("tiny")]
        result = await validate_ingestion(chunks, "test.pdf", min_chunk_chars=20)
        assert result["ok"] is False
        assert any("too short" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_chunk_too_large(self):
        chunks = [_chunk("x" * 6000)]
        result = await validate_ingestion(chunks, "test.pdf", max_chunk_chars=5000)
        assert result["ok"] is False
        assert any("too long" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_near_duplicate_chunks(self):
        text = "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10"
        chunks = [_chunk(text), _chunk(text)]
        result = await validate_ingestion(chunks, "test.pdf")
        assert result["ok"] is False
        assert any("near-duplicate" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_low_coverage(self):
        chunks = [_chunk("Short chunk only.")]
        result = await validate_ingestion(
            chunks,
            "test.pdf",
            original_content_length=10_000,
        )
        assert result["ok"] is False
        assert any("coverage" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_good_coverage(self):
        content = "A" * 500
        chunks = [_chunk(content)]
        result = await validate_ingestion(
            chunks,
            "test.pdf",
            original_content_length=600,
        )
        coverage_issues = [i for i in result["issues"] if "coverage" in i]
        assert len(coverage_issues) == 0

    @pytest.mark.asyncio
    async def test_no_chunks_returns_not_ok(self):
        result = await validate_ingestion([], "test.pdf")
        assert result["ok"] is False
        assert result["score"] == 0.0

    @pytest.mark.asyncio
    async def test_all_zero_embedding_reported(self):
        chunks = [_chunk("Valid content here with enough text.", embedding=[0, 0, 0])]
        result = await validate_ingestion(chunks, "test.pdf")
        assert any("all-zero embedding" in issue for issue in result["issues"])


# ── _jaccard_similarity ─────────────────────────────────────────────────


class TestJaccardSimilarity:
    def test_identical_strings(self):
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        assert _jaccard_similarity("hello world", "foo bar baz") == 0.0

    def test_partial_overlap(self):
        sim = _jaccard_similarity("a b c d", "c d e f")
        assert 0.3 < sim < 0.6

    def test_both_empty(self):
        assert _jaccard_similarity("", "") == 1.0

    def test_one_empty(self):
        assert _jaccard_similarity("hello", "") == 0.0
