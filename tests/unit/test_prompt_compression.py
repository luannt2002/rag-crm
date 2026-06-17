"""Unit tests for prompt compression module."""

from __future__ import annotations

import pytest

from ragbot.shared.prompt_compression import compress_chunk_text, compress_chunks


class TestCompressChunkText:
    """Tests for single-chunk text compression."""

    def test_empty_text(self):
        assert compress_chunk_text("") == ""

    def test_whitespace_normalization(self):
        text = "Hello   world\n\n\n\nParagraph   two"
        result = compress_chunk_text(text, max_chars=1000)
        assert "   " not in result
        assert "\n\n\n" not in result

    def test_boilerplate_removal(self):
        text = "Important content here.\nXem thêm tại: https://example.com\nNguồn: Wikipedia"
        result = compress_chunk_text(text, max_chars=1000, remove_boilerplate=True)
        assert "Xem thêm" not in result
        assert "Nguồn:" not in result
        assert "Important content" in result

    def test_boilerplate_removal_disabled(self):
        text = "Content.\nNguồn: Wikipedia"
        result = compress_chunk_text(text, max_chars=1000, remove_boilerplate=False)
        assert "Nguồn:" in result

    def test_markdown_removal(self):
        text = "## Header\n**bold text** and [link](http://example.com)"
        result = compress_chunk_text(text, max_chars=1000, remove_markdown=True)
        assert "##" not in result
        assert "**" not in result
        assert "bold text" in result
        assert "link" in result
        assert "http://example.com" not in result

    def test_truncation_respects_max_chars(self):
        text = "Sentence one. Sentence two is longer. Sentence three is the longest of all."
        result = compress_chunk_text(text, max_chars=30)
        assert len(result) <= 40  # some tolerance for sentence boundary

    def test_preserves_numbers_and_prices(self):
        text = "Giá sản phẩm là 500.000 VND. Đây là thông tin quan trọng. Và các từ không có nghĩa gì nhiều."
        result = compress_chunk_text(text, max_chars=200)
        assert "500.000" in result

    def test_short_text_unchanged(self):
        text = "Short text."
        result = compress_chunk_text(text, max_chars=500)
        assert result == "Short text."


class TestCompressChunks:
    """Tests for batch chunk compression."""

    def test_empty_list(self):
        assert compress_chunks([]) == []

    def test_preserves_original_content(self):
        chunks = [
            {"chunk_id": "1", "content": "A " * 300, "text": "A " * 300},
        ]
        result = compress_chunks(chunks, max_chars_per_chunk=100)
        assert len(result) == 1
        assert "original_content" in result[0]
        assert len(result[0]["original_content"]) > len(result[0]["content"])

    def test_no_modification_keeps_original(self):
        chunks = [
            {"chunk_id": "1", "content": "Short.", "text": "Short."},
        ]
        result = compress_chunks(chunks, max_chars_per_chunk=500)
        assert len(result) == 1
        assert "original_content" not in result[0]

    def test_chunk_without_content(self):
        chunks = [{"chunk_id": "1", "score": 0.9}]
        result = compress_chunks(chunks)
        assert result == chunks

    def test_multiple_chunks(self):
        chunks = [
            {"chunk_id": "1", "content": "Content one. " * 50, "text": "Content one. " * 50},
            {"chunk_id": "2", "content": "Short.", "text": "Short."},
            {"chunk_id": "3", "content": "Content three. " * 50, "text": "Content three. " * 50},
        ]
        result = compress_chunks(chunks, max_chars_per_chunk=100)
        assert len(result) == 3
        # First and third should be compressed
        assert "original_content" in result[0]
        assert "original_content" not in result[1]
        assert "original_content" in result[2]

    def test_feature_flag_disabled(self):
        """When called with default params, should still work."""
        chunks = [{"chunk_id": "1", "content": "Hello world", "text": "Hello world"}]
        result = compress_chunks(chunks)
        assert len(result) == 1


class TestSentenceScoring:
    """Tests for information density scoring."""

    def test_stopword_only_sentence_low_score(self):
        from ragbot.shared.prompt_compression import _sentence_info_score
        score = _sentence_info_score("là và của có được cho với trong")
        assert score < 0.2

    def test_content_rich_sentence_high_score(self):
        from ragbot.shared.prompt_compression import _sentence_info_score
        score = _sentence_info_score("Giá sản phẩm ABC là 500.000 VND tháng 12/2024")
        assert score > 0.5

    def test_empty_sentence(self):
        from ragbot.shared.prompt_compression import _sentence_info_score
        assert _sentence_info_score("") == 0.0
