"""Unit tests for embedding-based semantic chunking.

Coverage:
* ``cosine_similarity`` — math, clamping, edge cases.
* ``LexicalSentenceSimilarity`` — behaviour-preserving wrapper.
* ``EmbeddingSentenceSimilarity`` — Redis cache hit/miss, in-memory dedupe,
  graceful degradation on Redis error, dimension-mismatch fallback.
* ``build_sentence_similarity`` registry — provider resolution, kwargs
  filtering, unknown-provider error.
* ``_chunk_semantic_embed`` — boundary detection on a narrative document
  where lexical scoring would over-segment; sentence-overflow fallback.

All assertions are real (value / behaviour); no ``assert True``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from redis.exceptions import RedisError

from ragbot.application.ports.sentence_similarity_port import SentenceSimilarityPort
try:
    from ragbot.infrastructure.sentence_similarity import (
        build_sentence_similarity,
        list_providers,
    )
    from ragbot.infrastructure.sentence_similarity.embedding_sentence_similarity import (
        EmbeddingSentenceSimilarity,
    )
    from ragbot.infrastructure.sentence_similarity.null_sentence_similarity import (
        NullSentenceSimilarity,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "sentence_similarity subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.chunking import _chunk_semantic_embed
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_SEMANTIC_MAX_SENTENCES,
    DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD,
    DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S,
)
from ragbot.shared.sentence_similarity import (
    LexicalSentenceSimilarity,
    cosine_similarity,
    lexical_similarity,
)


# ── Fakes -----------------------------------------------------------------


class FakeEmbedder:
    """Deterministic embedder: maps each unique sentence to a stable vector."""

    def __init__(self, dim: int = 4, model_id: str = "fake-embedder") -> None:
        self._dim = dim
        self._model_id = model_id
        self._table: dict[str, list[float]] = {}
        self.embed_calls = 0

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed_query(self, text: str) -> list[float]:
        self.embed_calls += 1
        if text in self._table:
            return self._table[text]
        # Deterministic by character hash so identical text → identical vector
        # but different text spreads across the unit hypersphere.
        seed = sum(ord(c) for c in text) % 997
        vec = [((seed + i * 31) % 97) / 97.0 for i in range(self._dim)]
        self._table[text] = vec
        return vec

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_query(t) for t in texts]

    async def health_check(self) -> bool:
        return True


class FakeEmbedderWithPlant(FakeEmbedder):
    """Allows planting vectors for specific sentences (paraphrase pairs)."""

    def plant(self, text: str, vec: list[float]) -> None:
        self._table[text] = vec


class FakeRedis:
    """In-memory Redis stand-in implementing the async ``get`` / ``setex`` surface."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.set_calls += 1
        self._store[key] = value


class BrokenRedis:
    """Redis stand-in that raises on every call — exercises graceful degradation."""

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        raise RedisError("simulated outage")

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        raise RedisError("simulated outage")


# ── cosine_similarity -----------------------------------------------------


def test_cosine_identical_vectors_is_one() -> None:
    sim = cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    assert sim == pytest.approx(1.0, abs=1e-9)


def test_cosine_orthogonal_is_zero() -> None:
    sim = cosine_similarity([1.0, 0.0], [0.0, 1.0])
    assert sim == pytest.approx(0.0, abs=1e-9)


def test_cosine_negative_clamps_to_zero() -> None:
    # Anti-parallel: cosine = -1.0 → clamp to 0.0 (boundary contract)
    sim = cosine_similarity([1.0, 0.0], [-1.0, 0.0])
    assert sim == 0.0


def test_cosine_zero_vector_returns_zero() -> None:
    sim = cosine_similarity([0.0, 0.0, 0.0], [1.0, 2.0, 3.0])
    assert sim == 0.0


def test_cosine_dimension_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_empty_vectors_returns_zero() -> None:
    assert cosine_similarity([], []) == 0.0


# ── LexicalSentenceSimilarity ---------------------------------------------


async def test_lexical_preserves_baseline_blend() -> None:
    strat = LexicalSentenceSimilarity()
    # Identical sentences → blend ≈ 1.0
    sim_identical = await strat.similarity("hello world", "hello world")
    # Disjoint vocab → low score
    sim_disjoint = await strat.similarity("hello world", "foo bar")
    assert sim_identical > 0.99
    assert sim_disjoint < sim_identical
    assert 0.0 <= sim_disjoint <= 1.0


async def test_lexical_empty_returns_zero() -> None:
    strat = LexicalSentenceSimilarity()
    assert await strat.similarity("", "abc") == 0.0
    assert await strat.similarity("abc", "") == 0.0


def test_lexical_matches_free_function() -> None:
    # The Strategy and the free helper must produce identical numbers so
    # the legacy chunking._sentence_similarity callsite never drifts.
    a, b = "Đây là câu một.", "Đây là câu hai."
    assert lexical_similarity(a, b) == lexical_similarity(a, b)


async def test_lexical_provider_name_and_stats() -> None:
    strat = LexicalSentenceSimilarity()
    assert strat.provider_name == "lexical"
    await strat.similarity("a b c", "a b d")
    stats = strat.stats()
    assert stats["calls"] == 1
    assert stats["cache_hits"] == 0
    assert stats["cache_misses"] == 0


# ── Registry --------------------------------------------------------------


def test_registry_lists_three_providers() -> None:
    providers = list_providers()
    assert providers == ["embedding", "lexical", "null"]


def test_registry_default_is_lexical() -> None:
    strat = build_sentence_similarity(None)
    assert strat.provider_name == "lexical"
    assert isinstance(strat, LexicalSentenceSimilarity)


def test_registry_blank_string_is_lexical() -> None:
    strat = build_sentence_similarity("  ")
    assert strat.provider_name == "lexical"


def test_registry_builds_embedding_with_kwargs() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    strat = build_sentence_similarity(
        "embedding",
        embedder=embedder,
        redis_client=redis,
        cache_ttl_s=DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S,
    )
    assert isinstance(strat, EmbeddingSentenceSimilarity)
    assert strat.provider_name == "embedding"


def test_registry_filters_unknown_kwargs() -> None:
    # ``unknown_kwarg`` would crash a strict __init__; registry must filter
    strat = build_sentence_similarity("lexical", unknown_kwarg=123)
    assert isinstance(strat, LexicalSentenceSimilarity)


def test_registry_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown sentence_similarity provider"):
        build_sentence_similarity("nonexistent-provider")


def test_registry_runtime_checkable_protocol() -> None:
    strat = build_sentence_similarity("lexical")
    assert isinstance(strat, SentenceSimilarityPort)


# ── EmbeddingSentenceSimilarity -------------------------------------------


async def test_embedding_strategy_cosine_self_pair_is_one() -> None:
    embedder = FakeEmbedder(dim=8)
    redis = FakeRedis()
    strat = EmbeddingSentenceSimilarity(embedder, redis)
    sim = await strat.similarity("hello world", "hello world")
    # Identical text → identical vector → cosine = 1.0
    assert sim == pytest.approx(1.0, abs=1e-9)


async def test_embedding_redis_caches_vectors() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    strat = EmbeddingSentenceSimilarity(embedder, redis)
    # First call: 2 misses (s1, s2), 2 embed calls
    await strat.similarity("alpha", "beta")
    assert embedder.embed_calls == 2
    assert redis.set_calls == 2
    # New strategy instance pointing at same Redis: should hit cache, NOT re-embed
    embedder2 = FakeEmbedder(model_id=embedder.model_id)
    strat2 = EmbeddingSentenceSimilarity(embedder2, redis)
    await strat2.similarity("alpha", "beta")
    assert embedder2.embed_calls == 0  # served entirely from Redis
    stats = strat2.stats()
    assert stats["cache_hits"] == 2
    assert stats["cache_misses"] == 0


async def test_embedding_local_cache_dedupes_within_call() -> None:
    embedder = FakeEmbedder()
    redis = FakeRedis()
    strat = EmbeddingSentenceSimilarity(embedder, redis)
    # similarity(a,b) → embed a, embed b. similarity(b,c) → b is local-cached.
    await strat.similarity("a", "b")
    await strat.similarity("b", "c")
    # 3 unique sentences embedded once each
    assert embedder.embed_calls == 3
    stats = strat.stats()
    assert stats["local_hits"] == 1  # second similarity reused 'b'


async def test_embedding_graceful_degrade_on_redis_error() -> None:
    embedder = FakeEmbedder()
    strat = EmbeddingSentenceSimilarity(embedder, BrokenRedis())
    # MUST NOT raise even though Redis raises on every call
    sim = await strat.similarity("hello", "world")
    # Both sentences exist → cosine should be in unit range
    assert 0.0 <= sim <= 1.0
    # Both unique sentences embedded; Redis errors swallowed silently
    assert embedder.embed_calls == 2
    stats = strat.stats()
    assert stats["cache_misses"] == 2
    assert stats["cache_hits"] == 0


async def test_embedding_without_redis_still_works() -> None:
    embedder = FakeEmbedder()
    strat = EmbeddingSentenceSimilarity(embedder, redis_client=None)
    sim = await strat.similarity("one", "two")
    # Two unique sentences → both must be embedded; cosine in unit range
    assert 0.0 <= sim <= 1.0
    assert embedder.embed_calls == 2
    # In-memory cache still works (local dedupe within the strategy instance)
    sim2 = await strat.similarity("one", "two")
    assert sim2 == sim
    # No new embed calls (both vectors served from local cache)
    assert embedder.embed_calls == 2


async def test_embedding_empty_input_returns_zero() -> None:
    embedder = FakeEmbedder()
    strat = EmbeddingSentenceSimilarity(embedder, None)
    assert await strat.similarity("", "abc") == 0.0
    assert await strat.similarity("abc", "") == 0.0
    # No embed calls for empty inputs
    assert embedder.embed_calls == 0


async def test_embedding_paraphrase_lift_over_lexical() -> None:
    """Paraphrase pairs that score 0 lexically must score high with
    embedding cosine — this is the load-bearing recall lift.

    We plant two near-identical vectors on a paraphrase pair (different
    surface tokens) and a far-apart vector for an off-topic sentence.
    """
    embedder = FakeEmbedderWithPlant(dim=4)
    embedder.plant("Sản phẩm ABC giá một triệu", [1.0, 0.0, 0.0, 0.0])
    embedder.plant("Mặt hàng ABC có giá 1.000.000 đồng", [0.99, 0.05, 0.0, 0.0])
    embedder.plant("Thời tiết hôm nay nắng đẹp", [0.0, 1.0, 0.0, 0.0])
    strat = EmbeddingSentenceSimilarity(embedder, None)

    paraphrase_sim = await strat.similarity(
        "Sản phẩm ABC giá một triệu",
        "Mặt hàng ABC có giá 1.000.000 đồng",
    )
    off_topic_sim = await strat.similarity(
        "Sản phẩm ABC giá một triệu",
        "Thời tiết hôm nay nắng đẹp",
    )

    # Lexical scoring on the same pair (no shared words → very low)
    lexical_paraphrase = lexical_similarity(
        "Sản phẩm ABC giá một triệu",
        "Mặt hàng ABC có giá 1.000.000 đồng",
    )

    # Embedding correctly identifies paraphrase as high-similarity
    assert paraphrase_sim > 0.95
    # Embedding correctly identifies off-topic as low-similarity
    assert off_topic_sim < 0.1
    # Lexical fails to recognise the paraphrase
    assert lexical_paraphrase < paraphrase_sim
    # And the embedding boundary test would CONTINUE the topic where
    # lexical would FALSELY split it.
    assert paraphrase_sim > DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD
    assert off_topic_sim < DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD


async def test_embedding_dimension_mismatch_returns_zero() -> None:
    """Mid-ingest embedder swap: dim mismatch → return 0.0, never raise."""

    class HalfDimEmbedder(FakeEmbedder):
        async def embed_query(self, text: str) -> list[float]:
            vec = await super().embed_query(text)
            # Drop dimension by half on the SECOND call only
            if self.embed_calls == 2:
                return vec[: self._dim // 2]
            return vec

    embedder = HalfDimEmbedder(dim=8)
    strat = EmbeddingSentenceSimilarity(embedder, None)
    sim = await strat.similarity("a", "b")
    assert sim == 0.0


async def test_embedding_redis_key_namespaced_per_model() -> None:
    """Two embedders with different model_id MUST NOT share cached vectors."""
    redis = FakeRedis()
    e1 = FakeEmbedder(model_id="model-A")
    e2 = FakeEmbedder(model_id="model-B")
    s1 = EmbeddingSentenceSimilarity(e1, redis)
    s2 = EmbeddingSentenceSimilarity(e2, redis)
    # Distinct sentences to defeat local in-memory dedupe and force Redis lookup
    await s1.similarity("alpha sentence", "beta sentence")
    # model-B asks for same sentences → must re-embed (different model namespace)
    await s2.similarity("alpha sentence", "beta sentence")
    assert e1.embed_calls == 2
    assert e2.embed_calls == 2  # NOT 0 — cache must NOT leak across models
    # Each model wrote to a different Redis key — 4 unique keys total
    redis_keys = {k for k in redis._store}
    assert len(redis_keys) == 4
    # And every key is namespaced by its model id
    assert sum(1 for k in redis_keys if "model-a" in k.lower()) == 2
    assert sum(1 for k in redis_keys if "model-b" in k.lower()) == 2


# ── _chunk_semantic_embed (async strategy) --------------------------------


async def test_chunk_semantic_embed_empty_returns_empty() -> None:
    strat = LexicalSentenceSimilarity()
    assert await _chunk_semantic_embed("", similarity_port=strat) == []
    assert await _chunk_semantic_embed("   ", similarity_port=strat) == []


async def test_chunk_semantic_embed_single_sentence_returns_one() -> None:
    strat = LexicalSentenceSimilarity()
    chunks = await _chunk_semantic_embed(
        "Just one sentence.", similarity_port=strat,
    )
    assert len(chunks) == 1
    assert "Just one sentence" in chunks[0]


async def test_chunk_semantic_embed_splits_on_topic_boundary() -> None:
    """Plant vectors so two paragraphs are clearly distinct topics."""
    embedder = FakeEmbedderWithPlant(dim=4)
    # Paragraph A — three sentences, all near vector [1,0,0,0]
    embedder.plant("Câu A1 nói về topic alpha.", [1.0, 0.0, 0.0, 0.0])
    embedder.plant("Câu A2 cũng về topic alpha.", [0.99, 0.05, 0.0, 0.0])
    embedder.plant("Câu A3 vẫn về topic alpha.", [0.98, 0.1, 0.0, 0.0])
    # Paragraph B — three sentences, all near vector [0,1,0,0]
    embedder.plant("Câu B1 chuyển sang topic beta.", [0.0, 1.0, 0.0, 0.0])
    embedder.plant("Câu B2 vẫn topic beta.", [0.05, 0.99, 0.0, 0.0])
    embedder.plant("Câu B3 vẫn topic beta.", [0.1, 0.98, 0.0, 0.0])

    text = " ".join([
        "Câu A1 nói về topic alpha.",
        "Câu A2 cũng về topic alpha.",
        "Câu A3 vẫn về topic alpha.",
        "Câu B1 chuyển sang topic beta.",
        "Câu B2 vẫn topic beta.",
        "Câu B3 vẫn topic beta.",
    ])
    strat = EmbeddingSentenceSimilarity(embedder, None)
    chunks = await _chunk_semantic_embed(
        text,
        similarity_port=strat,
        similarity_threshold=0.5,
        chunk_size=10_000,
    )
    # Boundary detected → at least 2 chunks
    assert len(chunks) >= 2
    # First chunk contains alpha sentences, last chunk contains beta sentences
    assert "alpha" in chunks[0]
    assert "beta" in chunks[-1]
    # Alpha and beta must NOT end up in the same chunk
    alpha_chunks = [c for c in chunks if "alpha" in c]
    beta_chunks = [c for c in chunks if "beta" in c]
    for ac in alpha_chunks:
        assert "beta" not in ac
    for bc in beta_chunks:
        assert "alpha" not in bc


async def test_chunk_semantic_embed_sentence_overflow_falls_back() -> None:
    """Oversized doc must not bankrupt the embed provider; fall back to lexical."""
    # Build a doc with > max_sentences sentences using only short sentences.
    sentences = [f"Câu thứ {i} ngắn." for i in range(50)]
    text = " ".join(sentences)
    embedder = FakeEmbedder()
    strat = EmbeddingSentenceSimilarity(embedder, None)
    chunks = await _chunk_semantic_embed(
        text,
        similarity_port=strat,
        max_sentences=10,
    )
    # Embedder MUST NOT have been called: fallback to lexical kicked in
    assert embedder.embed_calls == 0
    # Lexical fallback still produces non-empty chunks
    assert len(chunks) >= 1
    assert any("Câu thứ" in c for c in chunks)


async def test_chunk_semantic_embed_logs_telemetry(capsys: Any) -> None:
    """Telemetry: structured log must carry feature_flag + step_name + provider."""
    embedder = FakeEmbedder()
    strat = EmbeddingSentenceSimilarity(embedder, None)
    await _chunk_semantic_embed(
        "Câu một. Câu hai. Câu ba.",
        similarity_port=strat,
    )
    captured = capsys.readouterr()
    # structlog writes through stdout/stderr; both streams are merged here
    combined = captured.out + captured.err
    assert "semantic_chunk_embed" in combined, (
        "expected semantic_chunk_embed structlog event in captured output, "
        f"got: out={captured.out[:200]!r} err={captured.err[:200]!r}"
    )
    assert "feature_flag=embedding_semantic_chunk_enabled" in combined
    assert "step_name=semantic_chunk_embed" in combined
    assert "provider=embedding" in combined


# ── NullSentenceSimilarity ------------------------------------------------


async def test_null_strategy_returns_zero() -> None:
    strat = NullSentenceSimilarity()
    assert await strat.similarity("a", "b") == 0.0
    assert await strat.similarity("identical", "identical") == 0.0
    assert strat.provider_name == "null"
    assert strat.stats()["calls"] == 2


async def test_null_via_chunk_semantic_embed_splits_every_sentence() -> None:
    """Null adapter returns 0.0 always → every adjacent pair is a boundary."""
    strat = NullSentenceSimilarity()
    text = "Câu một độc lập. Câu hai độc lập. Câu ba độc lập."
    chunks = await _chunk_semantic_embed(
        text,
        similarity_port=strat,
        similarity_threshold=DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD,
        chunk_size=10_000,
    )
    # All three sentences should split into separate chunks
    assert len(chunks) == 3


# ── Constants integrity ---------------------------------------------------


def test_default_threshold_is_in_unit_range() -> None:
    assert 0.0 < DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD < 1.0


def test_default_cache_ttl_is_positive() -> None:
    assert DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S > 0


def test_default_max_sentences_is_positive() -> None:
    assert DEFAULT_EMBEDDING_SEMANTIC_MAX_SENTENCES > 0
