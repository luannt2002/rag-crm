"""3.6 — dense query path must NFC-normalize before embedding.

The sparse/lexical side (``pgvector_store.hybrid_search``) and the ingest side
already NFC-normalize; the DENSE embedding path (``_embed_query`` /
``_prewarm_embedding_cache`` in query_graph, and ``_embed_batch_queries`` in the
retrieve node) did not. A query typed on a macOS/iOS Vietnamese IME arrives
decomposed (NFD) — embedded as-is it yields a DIFFERENT vector from the
NFC-indexed corpus, so dense recall silently misses.

These drive the real ``retrieve`` node (same harness as the J1 prewarm tests)
with an NFD query and assert every string handed to the embedder is composed
(NFC — no standalone combining marks). Both the single-embed and the
batch-prewarm seams are covered so the paths never desync (a half-fix would
split the cache key space).
"""
from __future__ import annotations

import unicodedata
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit._node_test_helpers import (
    FakeGuardrail,
    FakeInvocationLogger,
    RecordingAuditLogger,
    make_resolver_and_llm,
    make_state,
    node_callable,
)

# Decompose VALID precomposed VN strings so each fixture round-trips cleanly
# (NFD → NFC == original). A malformed hand-typed decomposition would leave a
# stray mark that no normalizer can compose, giving a false RED.
_NFC_QUERY = "giá lốp Michelin"
_NFD_QUERY = unicodedata.normalize("NFD", _NFC_QUERY)


def _has_combining(s: str) -> bool:
    return any(unicodedata.combining(c) for c in s)


def _assert_truly_nfd(s: str) -> None:
    """Guard the fixture: input must actually be decomposed, else it proves nothing."""
    assert s != unicodedata.normalize("NFC", s)
    assert _has_combining(s)


async def _drive_retrieve_capturing_embeds(query: str, mq_variants: list[str] | None):
    """Build the graph with a capturing embedder, run ``retrieve`` on *query*,
    return every text string the embedder received."""
    import ragbot.orchestration.query_graph as qg

    captured: list[str] = []

    async def _get(_redis, text, *, model, dim, provider=None):
        return None  # force a cold embed every time

    async def _set(*_a, **_kw):
        return None

    orig_get, orig_set = qg.get_cached_embedding, qg.set_cached_embedding
    qg.get_cached_embedding = _get  # type: ignore[assignment]
    qg.set_cached_embedding = _set  # type: ignore[assignment]
    import ragbot.orchestration.nodes.retrieve as _rn
    if hasattr(_rn, "set_cached_embedding"):
        _rn.set_cached_embedding = _set
    if hasattr(_rn, "get_cached_embedding"):
        _rn.get_cached_embedding = _get
    try:
        embedder = MagicMock()

        async def _embed_one(text, *_a, **_kw):
            captured.append(text)
            return [0.1] * 8

        async def _embed_batch(texts, *_a, **_kw):
            captured.extend(texts)
            return [[0.1] * 8 for _ in texts]

        embedder.embed_one = _embed_one
        embedder.embed_batch = _embed_batch
        embedder.embed = _embed_batch

        vstore = MagicMock()
        del vstore.hybrid_search
        vstore.search = AsyncMock(return_value=[])

        from ragbot.orchestration.query_graph import build_graph

        resolver, llm, _cfg = make_resolver_and_llm()
        compiled = build_graph(
            invocation_logger=FakeInvocationLogger(),
            guardrail=FakeGuardrail(),
            model_resolver=resolver,
            llm=llm,
            vector_store=vstore,
            embedder=embedder,
            semantic_cache=None,
            audit_logger=RecordingAuditLogger(),
        )
        fn = node_callable(compiled, "retrieve")
        kwargs = {"_mq_queries": mq_variants} if mq_variants is not None else {}
        state = make_state(query=query, **kwargs)
        await fn(state)
        return captured
    finally:
        qg.get_cached_embedding = orig_get  # type: ignore[assignment]
        qg.set_cached_embedding = orig_set  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_embed_query_normalizes_nfd_single_query() -> None:
    """A single NFD query reaches the embedder composed (NFC)."""
    _assert_truly_nfd(_NFD_QUERY)
    captured = await _drive_retrieve_capturing_embeds(_NFD_QUERY, mq_variants=None)

    assert captured, "embedder was never called — test would be vacuous"
    for text in captured:
        assert not _has_combining(text), f"stray combining mark survived: {text!r}"


@pytest.mark.asyncio
async def test_prewarm_normalizes_nfd_variants() -> None:
    """The multi-query batch/prewarm embeds NFC variants too (mirror of the
    single path — otherwise a prewarmed key never collides with the per-branch
    embed and the cache silently misses)."""
    variants = [
        unicodedata.normalize("NFD", s)
        for s in ("giá lốp", "lốp tốt nhất", "chính sách bảo hành")
    ]
    for v in variants:
        _assert_truly_nfd(v)
    captured = await _drive_retrieve_capturing_embeds(_NFD_QUERY, mq_variants=variants)

    assert captured, "embedder was never called — test would be vacuous"
    for text in captured:
        assert not _has_combining(text), f"stray combining mark survived: {text!r}"
