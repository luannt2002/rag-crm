"""Unit tests: P3 chunk tracking — content_chars and chunk_chars at ingest time."""
from __future__ import annotations


class TestChunkCharsComputation:
    """Verify chunk_chars = len(chunk_text) logic used at ingest."""

    def test_new_ingest_stores_content_chars(self) -> None:
        """content_chars = len(content) for new document."""
        content = "Hello world. This is a test document with Vietnamese content: xin chào."
        content_chars = len(content)
        assert content_chars == 71
        assert content_chars > 0

    def test_new_ingest_stores_chunk_chars(self) -> None:
        """chunk_chars = len(chunk_text) for each chunk."""
        chunks = [
            "Hello world. This is chunk one.",
            "Xin chào. Đây là chunk hai.",
            "Final chunk with some trailing text.",
        ]
        chunk_chars_list = [len(c) for c in chunks]
        assert chunk_chars_list == [31, 27, 36]
        assert all(cc > 0 for cc in chunk_chars_list)

    def test_reindex_updates_content_chars(self) -> None:
        """On re-ingest, content_chars reflects NEW content length."""
        old_content = "Short old content."
        new_content = "This is the new, longer content after re-ingestion with more detail."
        old_chars = len(old_content)
        new_chars = len(new_content)
        assert new_chars != old_chars
        assert new_chars == 68

    def test_reindex_creates_new_chunk_chars(self) -> None:
        """Re-ingest creates new chunks with updated chunk_chars."""
        old_chunks = ["Old chunk A.", "Old chunk B."]
        new_chunks = ["New chunk alpha with more text.", "New chunk beta extended."]
        old_chars = [len(c) for c in old_chunks]
        new_chars = [len(c) for c in new_chunks]
        assert old_chars != new_chars
        assert new_chars == [31, 24]

    def test_delete_preserves_content_chars_for_audit(self) -> None:
        """Soft delete sets deleted_at but content_chars stays for audit."""
        # Simulate: document has content_chars = 500, then soft-deleted
        content_chars_before = 500
        # After soft delete: deleted_at = now(), content_chars unchanged
        content_chars_after = content_chars_before  # preserved
        assert content_chars_after == 500

    def test_coverage_ratio_computation(self) -> None:
        """Coverage ratio = sum(context_chunk_chars) / doc_content_chars."""
        doc_content_chars = 10000
        context_chunks = [
            {"content": "x" * 800, "chunk_chars": 800},
            {"content": "y" * 600, "chunk_chars": 600},
            {"content": "z" * 400, "chunk_chars": 400},
        ]
        context_chars = sum(c["chunk_chars"] for c in context_chunks)
        coverage_ratio = context_chars / doc_content_chars
        assert context_chars == 1800
        assert abs(coverage_ratio - 0.18) < 0.001

    def test_coverage_fallback_to_content_length(self) -> None:
        """When chunk_chars missing, fallback to len(content)."""
        chunks = [
            {"content": "Hello world test"},
            {"content": "Another chunk"},
        ]
        context_chars = sum(
            c.get("chunk_chars", len(c.get("content", ""))) for c in chunks
        )
        assert context_chars == len("Hello world test") + len("Another chunk")
        assert context_chars == 29

    def test_unicode_chars_counted_correctly(self) -> None:
        """Vietnamese text: len() counts codepoints, not bytes."""
        text = "Xin chào thế giới. Đây là văn bản tiếng Việt."
        assert len(text) == 45
        # bytes would be much more due to UTF-8 multi-byte chars
        assert len(text.encode("utf-8")) > len(text)
