"""Tests for auto-citation fallback in generate node.

Covers the bug fix: citations: [] even when graded_chunks have data.
Root cause: LLM does not emit [chunk:<id>] markers, so _CITATION_RE finds nothing.
Fix: auto-map top-K graded_chunks -> citations when marker extraction yields [].
"""

from __future__ import annotations

import pytest

from ragbot.shared.constants import DEFAULT_CITATIONS_TOP_K


# ---------------------------------------------------------------------------
# Unit-test the auto-citation helper logic directly (extracted from generate node)
# ---------------------------------------------------------------------------

def _build_citations_from_graded(
    graded: list[dict],
    answer: str,
    top_k: int = DEFAULT_CITATIONS_TOP_K,
) -> list[dict]:
    """Replicate generate-node auto-citation logic for isolated testing."""
    if not answer or not graded:
        return []
    citations = []
    for c in graded[:top_k]:
        cid = str(c.get("chunk_id") or c.get("id") or "")
        if not cid:
            continue
        citations.append({
            "chunk_id": cid,
            "score": round(float(c.get("score") or c.get("relevance_score") or 0.0), 6),
            "source_url": c.get("source_url") or "",
            "document_name": (
                c.get("document_name")
                or (c.get("metadata") or {}).get("document_title")
                or ""
            ),
        })
    return citations


_SAMPLE_GRADED = [
    {
        "chunk_id": "aaaa-1111",
        "score": 0.85,
        "document_name": "Price List 2024",
        "source_url": "https://example.com/price.pdf",
        "text": "Gói A giá 500k",
    },
    {
        "chunk_id": "bbbb-2222",
        "score": 0.72,
        "document_name": "FAQ",
        "source_url": "",
        "text": "Giao hàng miễn phí",
    },
    {
        "chunk_id": "cccc-3333",
        "score": 0.61,
        "document_name": "Policy",
        "source_url": None,
        "text": "Hoàn tiền trong 7 ngày",
    },
    {
        "chunk_id": "dddd-4444",
        "score": 0.50,
        "document_name": "Catalog",
        "source_url": "",
        "text": "Sản phẩm mới 2024",
    },
]


class TestAutoCitationFromGraded:
    """Strategy A: auto-map graded_chunks → citations when answer non-empty."""

    def test_citations_populated_when_grounded(self):
        """Grounded answer + graded chunks → citations populated with chunk_id + score."""
        answer = "Gói A giá 500k, giao hàng miễn phí."
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer)
        assert len(result) >= 1, "Expected at least 1 citation for grounded answer"
        first = result[0]
        assert first["chunk_id"] == "aaaa-1111"
        assert first["score"] == pytest.approx(0.85, abs=1e-5)
        assert first["document_name"] == "Price List 2024"

    def test_citations_empty_when_answer_empty(self):
        """Empty answer (refuse case) → citations must be []."""
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer="")
        assert result == [], "Empty answer must yield empty citations"

    def test_citations_empty_when_no_graded_chunks(self):
        """No graded chunks → citations must be []."""
        result = _build_citations_from_graded([], answer="Some answer text")
        assert result == [], "No graded chunks must yield empty citations"

    def test_citations_top_k_respected(self):
        """Citations count must be <= top_k regardless of graded list size."""
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer="Answer text", top_k=2)
        assert len(result) <= 2, f"Expected at most 2 citations, got {len(result)}"

    def test_citations_top_k_default_is_3(self):
        """Default top_k = DEFAULT_CITATIONS_TOP_K (3) — constant value assertion."""
        assert DEFAULT_CITATIONS_TOP_K == 3, (
            f"DEFAULT_CITATIONS_TOP_K must be 3, got {DEFAULT_CITATIONS_TOP_K}"
        )
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer="Answer", top_k=DEFAULT_CITATIONS_TOP_K)
        assert len(result) == 3, f"Expected exactly 3 citations (default top_k), got {len(result)}"

    def test_citations_fields_complete(self):
        """Each citation must have chunk_id, score, source_url, document_name keys."""
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer="Answer")
        for cit in result:
            assert "chunk_id" in cit, "citation missing chunk_id"
            assert "score" in cit, "citation missing score"
            assert "source_url" in cit, "citation missing source_url"
            assert "document_name" in cit, "citation missing document_name"

    def test_citations_chunk_id_not_empty(self):
        """All returned citations must have a non-empty chunk_id."""
        result = _build_citations_from_graded(_SAMPLE_GRADED, answer="Answer")
        for cit in result:
            assert cit["chunk_id"], f"citation has empty chunk_id: {cit}"

    def test_citations_skips_chunks_without_id(self):
        """Chunks with no chunk_id or id must be skipped."""
        graded_with_gaps = [
            {"chunk_id": "", "score": 0.9, "document_name": "Doc A"},  # empty id
            {"score": 0.8, "document_name": "Doc B"},  # missing id key
            {"chunk_id": "valid-id-xyz", "score": 0.7, "document_name": "Doc C"},
        ]
        result = _build_citations_from_graded(graded_with_gaps, answer="Answer", top_k=5)
        assert len(result) == 1, f"Expected only 1 valid citation, got {len(result)}"
        assert result[0]["chunk_id"] == "valid-id-xyz"

    def test_citations_source_url_none_becomes_empty_string(self):
        """source_url=None → empty string in citation (not None)."""
        graded = [{"chunk_id": "x-123", "score": 0.5, "source_url": None, "document_name": "Doc"}]
        result = _build_citations_from_graded(graded, answer="Answer")
        assert result[0]["source_url"] == "", "None source_url must be coerced to empty string"

    def test_citations_document_name_from_metadata_fallback(self):
        """document_name resolved from metadata.document_title if top-level missing."""
        graded = [{
            "chunk_id": "x-456",
            "score": 0.6,
            "metadata": {"document_title": "Fallback Title"},
        }]
        result = _build_citations_from_graded(graded, answer="Answer")
        assert result[0]["document_name"] == "Fallback Title"

    def test_citations_score_rounded_to_6_decimals(self):
        """Scores are rounded to 6 decimal places."""
        graded = [{"chunk_id": "x-789", "score": 0.12345678901234, "document_name": "Doc"}]
        result = _build_citations_from_graded(graded, answer="Answer")
        assert result[0]["score"] == pytest.approx(0.123457, abs=1e-6)

    def test_citations_id_field_fallback(self):
        """chunk_id resolved from 'id' key if 'chunk_id' not present."""
        graded = [{"id": "fallback-uuid", "score": 0.75, "document_name": "Doc"}]
        result = _build_citations_from_graded(graded, answer="Answer")
        assert result[0]["chunk_id"] == "fallback-uuid"
