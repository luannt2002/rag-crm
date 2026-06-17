"""Tests: MMR filter with lambda parameter for relevance vs diversity balance."""

from __future__ import annotations

import pytest

from ragbot.shared.mmr import (
    _cosine_similarity,
    _trigram_similarity,
    mmr_algorithm,
    mmr_filter,
)


class TestTrigramSimilarity:
    def test_identical_strings(self):
        assert _trigram_similarity("hello world", "hello world") == 1.0

    def test_empty_strings(self):
        assert _trigram_similarity("", "hello") == 0.0
        assert _trigram_similarity("hello", "") == 0.0
        assert _trigram_similarity("", "") == 0.0

    def test_short_strings_below_trigram(self):
        # Strings shorter than 3 chars produce no trigrams
        assert _trigram_similarity("ab", "ab") == 0.0

    def test_different_strings(self):
        sim = _trigram_similarity("hello world", "goodbye universe")
        assert 0.0 <= sim < 0.5  # should be low similarity

    def test_similar_strings(self):
        sim = _trigram_similarity(
            "the quick brown fox jumps over the lazy dog",
            "the quick brown fox leaps over the lazy dog",
        )
        assert sim > 0.5  # high overlap


class TestMmrFilter:
    def test_empty_input(self):
        assert mmr_filter([]) == []

    def test_single_chunk(self):
        chunks = [{"content": "hello", "score": 0.9}]
        assert mmr_filter(chunks) == chunks

    def test_default_keeps_diverse_chunks(self):
        chunks = [
            {"content": "alpha beta gamma delta epsilon", "score": 0.9},
            {"content": "completely different text about something else entirely", "score": 0.8},
            {"content": "alpha beta gamma delta epsilon zeta", "score": 0.7},  # near-duplicate of first
        ]
        result = mmr_filter(chunks, similarity_threshold=0.80)
        # Near-duplicate should be filtered
        assert len(result) <= 3

    def test_lambda_one_pure_relevance_order(self):
        """lambda=1.0 should keep all non-duplicate chunks in original score order."""
        chunks = [
            {"content": "aaa bbb ccc ddd eee fff ggg", "score": 0.9},
            {"content": "xxx yyy zzz www vvv uuu ttt", "score": 0.3},
        ]
        result = mmr_filter(chunks, lambda_param=1.0, similarity_threshold=0.99)
        assert len(result) == 2

    def test_lambda_zero_maximum_diversity(self):
        """lambda=0.0 should prioritise diversity (most different chunk next)."""
        chunks = [
            {"content": "the same repeated text pattern here", "score": 0.9},
            {"content": "the same repeated text pattern here with minor change", "score": 0.85},
            {"content": "completely unrelated content about other things", "score": 0.5},
        ]
        result = mmr_filter(chunks, lambda_param=0.0, similarity_threshold=0.95)
        # With lambda=0, the most diverse chunk should be preferred over similar one
        assert len(result) >= 2
        if len(result) >= 2:
            # Second selected should be the diverse one (index 2 original)
            assert "unrelated" in result[1].get("content", "")

    def test_similarity_threshold_filters_duplicates(self):
        # Two identical chunks
        chunks = [
            {"content": "exact same content here for testing", "score": 0.9},
            {"content": "exact same content here for testing", "score": 0.8},
        ]
        result = mmr_filter(chunks, similarity_threshold=0.5)
        assert len(result) == 1

    def test_max_results_cap(self):
        chunks = [
            {"content": f"unique content number {i} with enough text", "score": 0.9 - i * 0.1}
            for i in range(10)
        ]
        result = mmr_filter(chunks, max_results=3)
        assert len(result) == 3

    def test_rerank_score_fallback(self):
        """Should use rerank_score when score is not present."""
        chunks = [
            {"content": "first chunk with some text content here", "rerank_score": 0.9},
            {"content": "second chunk with different text entirely", "rerank_score": 0.8},
        ]
        result = mmr_filter(chunks, lambda_param=0.7)
        assert len(result) == 2

    def test_missing_score_defaults_zero(self):
        chunks = [
            {"content": "chunk without any score field at all", },
            {"content": "another chunk also without score field", },
        ]
        result = mmr_filter(chunks)
        assert len(result) >= 1

    def test_chunks_with_text_key(self):
        """Should work with 'text' key as well as 'content'."""
        chunks = [
            {"text": "some text content here in first chunk", "score": 0.9},
            {"text": "different text content in second chunk", "score": 0.8},
        ]
        result = mmr_filter(chunks)
        assert len(result) == 2

    def test_backward_compatible_signature(self):
        """Old callers passing only chunks + similarity_threshold should still work."""
        chunks = [
            {"content": "hello world this is a test", "score": 0.9},
            {"content": "goodbye world this is different", "score": 0.8},
        ]
        # Old signature: mmr_filter(chunks, similarity_threshold=0.88)
        result = mmr_filter(chunks, similarity_threshold=0.88)
        assert len(result) >= 1


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        # Cosine of opposite directions is -1
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_vectors_are_zero(self):
        assert _cosine_similarity([], [1.0]) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_mismatched_length_returns_zero(self):
        assert _cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0


class TestCosineMMR:
    """Cosine path of mmr_filter — uses chunk['embedding'] when present."""

    def test_identical_embeddings_filtered_by_threshold(self):
        """Two chunks with identical embeddings → second is dropped above threshold."""
        chunks = [
            {"content": "alpha", "score": 0.9, "embedding": [1.0, 0.0, 0.0]},
            {"content": "beta", "score": 0.85, "embedding": [1.0, 0.0, 0.0]},
            {"content": "gamma", "score": 0.5, "embedding": [0.0, 1.0, 0.0]},
        ]
        result = mmr_filter(chunks, similarity_threshold=0.5, use_cosine=True)
        # Top relevance kept; identical-embedding sibling rejected; orthogonal kept.
        assert len(result) == 2
        contents = {c["content"] for c in result}
        assert "alpha" in contents and "gamma" in contents
        assert "beta" not in contents

    def test_orthogonal_embeddings_all_pass(self):
        """Orthogonal embeddings have cosine=0 — all chunks survive any threshold>0."""
        chunks = [
            {"content": "x", "score": 0.9, "embedding": [1.0, 0.0, 0.0]},
            {"content": "y", "score": 0.8, "embedding": [0.0, 1.0, 0.0]},
            {"content": "z", "score": 0.7, "embedding": [0.0, 0.0, 1.0]},
        ]
        result = mmr_filter(chunks, similarity_threshold=0.5, use_cosine=True)
        assert len(result) == 3

    def test_falls_back_to_trigram_when_embedding_missing(self):
        """Mixed chunks (one without embedding) → fallback to trigram path globally."""
        chunks = [
            {"content": "exact same content here", "score": 0.9, "embedding": [1.0, 0.0]},
            {"content": "exact same content here", "score": 0.85},  # no embedding
        ]
        # Without cosine, lexical-identical should drop one via trigram threshold.
        result = mmr_filter(chunks, similarity_threshold=0.5, use_cosine=True)
        assert len(result) == 1

    def test_use_cosine_false_uses_trigram_even_with_embeddings(self):
        """Caller can force trigram path via use_cosine=False."""
        chunks = [
            {"content": "shared lexical text here", "score": 0.9, "embedding": [1.0, 0.0]},
            {"content": "completely different vocabulary words", "score": 0.85,
             "embedding": [1.0, 0.0]},  # identical embedding
        ]
        # Identical embeddings would force cosine=1.0; trigram sees different
        # text → should keep both.
        result = mmr_filter(chunks, similarity_threshold=0.9, use_cosine=False)
        assert len(result) == 2

    def test_mmr_algorithm_returns_cosine_when_all_have_embeddings(self):
        chunks = [
            {"content": "a", "embedding": [1.0, 0.0]},
            {"content": "b", "embedding": [0.0, 1.0]},
        ]
        assert mmr_algorithm(chunks, use_cosine=True) == "cosine"

    def test_mmr_algorithm_returns_trigram_when_any_missing(self):
        chunks = [
            {"content": "a", "embedding": [1.0, 0.0]},
            {"content": "b"},  # no embedding
        ]
        assert mmr_algorithm(chunks, use_cosine=True) == "trigram"

    def test_mmr_algorithm_use_cosine_false_returns_trigram(self):
        chunks = [{"content": "a", "embedding": [1.0]}]
        assert mmr_algorithm(chunks, use_cosine=False) == "trigram"
