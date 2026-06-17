"""Smoke test — retrieve pipeline returns chunks for a pre-seeded corpus.

Regression guard for the  metadata-aware retrieval bug where the
``metadata_filter`` extracted by the read-side intent extractor blocked
every result for un-labelled corpora and the relax fallback failed to
recover them. Without this guard the symptom is silent: the API answers
the OOS template even though the index has perfectly relevant chunks.

The test is intentionally INTEGRATION-level (real Postgres + real OpenAI
embedding) because the bug only surfaces when:
    - documents.metadata_json shape does NOT match intent_extractor output, AND
    - retrieve runs end-to-end, NOT a unit-mocked vector_store.

The test SKIPs gracefully when the demo seed corpus is absent so CI on
fresh DBs stays green; ops trigger it after seeding by exporting the
``RAGBOT_SMOKE_BOT_*`` env triple. No brand/customer literals — bot
slug, tenant id, and probe query are all env-driven (test SKIPs when
env not set).
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
from ragbot.infrastructure.vector.pgvector_store import PgVectorStore
from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS,
    DEFAULT_RETRIEVE_SMOKE_MIN_COSINE,
)


# Smoke test reads bot identity from env triple. SKIPS when env not set
# — keeps test domain-neutral (no brand/customer slug in tracked code).
_SMOKE_BOT_SLUG_ENV = "RAGBOT_SMOKE_BOT_SLUG"
_SMOKE_BOT_TENANT_ENV = "RAGBOT_SMOKE_BOT_TENANT"
_SMOKE_BOT_QUERY_ENV = "RAGBOT_SMOKE_BOT_QUERY"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL=") and "DATABASE_URL_SYNC" not in line:
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not set and .env not found")


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _resolve_smoke_bot(sf: Any) -> tuple[str, str, str] | None:
    """Return (record_bot_id_uuid_str, tenant_id, query) or None if absent.

    Reads the bot slug + tenant from env so this test stays domain-neutral.
    Skips when (a) env not set, (b) bot row missing, or (c) bot has < 1
    document chunk (corpus not seeded).
    """
    # Domain-neutral: SKIP when env triple not provided — no brand
    # fallback. CI on a fresh DB without seed config simply skips the
    # smoke test instead of leaking a tenant slug into tracked code.
    slug = os.environ.get(_SMOKE_BOT_SLUG_ENV)
    tenant_str = os.environ.get(_SMOKE_BOT_TENANT_ENV)
    query = os.environ.get(_SMOKE_BOT_QUERY_ENV)
    if not slug or not tenant_str or not query:
        return None
    try:
        tenant_id = int(tenant_str)
    except ValueError:
        return None
    async with sf() as session:
        row = (
            await session.execute(
                text(
                    "SELECT b.id::text AS bot_uuid, "
                    "  (SELECT count(*) FROM document_chunks dc "
                    "    JOIN documents d ON dc.record_document_id = d.id "
                    "    WHERE d.record_bot_id = b.id AND d.deleted_at IS NULL) AS chunks "
                    "FROM bots b WHERE b.tenant_id = :tid AND b.bot_id = :slug "
                    "  AND b.channel_type = 'web' "
                    "LIMIT 1"
                ),
                {"tid": tenant_id, "slug": slug},
            )
        ).mappings().first()
    if not row:
        return None
    if (row.get("chunks") or 0) < DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS:
        return None
    return row["bot_uuid"], str(tenant_id), query


def _embedding_spec() -> EmbeddingSpec:
    """Build a minimal EmbeddingSpec matching the system default model."""
    from uuid import uuid4
    return EmbeddingSpec(
        binding_id=uuid4(),
        model_name=DEFAULT_EMBEDDING_MODEL,
        provider="OpenAI",
        dimension=1536,
        max_batch=64,
        model_version="smoke",
    )


@pytest.mark.asyncio
async def test_smoke_hybrid_search_returns_chunks(session_factory: Any) -> None:
    """``hybrid_search`` (no metadata_filter) returns >=N chunks for the seeded corpus.

    Catches: SQL filter regressions on ``record_bot_id``, embedding
    dimension drift, broken vector index, channel_type filter
    re-introduction.
    """
    bot_info = await _resolve_smoke_bot(session_factory)
    if bot_info is None:
        pytest.skip("smoke bot not seeded — set RAGBOT_SMOKE_BOT_* envs and ingest corpus")
    record_bot_id, _tenant, query = bot_info

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — smoke test needs real embedding API")

    embedder = LiteLLMEmbedder(model=DEFAULT_EMBEDDING_MODEL)
    qvec = await embedder.embed_one(query, spec=_embedding_spec(), record_tenant_id=None)
    assert qvec, "embedder returned empty vector — check OPENAI_API_KEY / model name"
    assert len(qvec) == 1536, f"unexpected dimension: {len(qvec)}"

    store = PgVectorStore(session_factory=session_factory, dimension=1536)

    # Path 1: dense-only `search` — direct cosine, no RRF.
    dense = await store.search(
        query_embedding=qvec, record_bot_id=record_bot_id, top_k=10,
    )
    assert len(dense) >= DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS, (
        f"dense search returned {len(dense)} chunks, expected "
        f">= {DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS} — pipeline broken or filter blocked"
    )
    top_cosine = max(float(c.get("score", 0)) for c in dense)
    assert top_cosine >= DEFAULT_RETRIEVE_SMOKE_MIN_COSINE, (
        f"top cosine {top_cosine:.4f} < {DEFAULT_RETRIEVE_SMOKE_MIN_COSINE} — "
        "embedding mismatch or index corruption"
    )

    # Path 2: hybrid_search — RRF fused, scores are RRF-normalised
    # (~0.01-0.05). Assert chunk count only, not absolute score floor.
    hybrid = await store.hybrid_search(
        query_text=query,
        query_embedding=qvec,
        record_bot_id=record_bot_id,
        top_k=10,
    )
    assert len(hybrid) >= DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS, (
        f"hybrid_search returned {len(hybrid)} chunks, expected "
        f">= {DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS}"
    )


@pytest.mark.asyncio
async def test_smoke_metadata_filter_blocks_then_relax_recovers(
    session_factory: Any,
) -> None:
    """Metadata filter that misses corpus shape => 0 chunks; same query
    without filter => >=N chunks. This is the contract the relax fallback
    in ``query_graph.retrieve`` depends on: the filtered call MUST NOT
    raise + MUST return [] (so callers can detect + relax). If this
    invariant breaks we ship the bug the report identified.
    """
    bot_info = await _resolve_smoke_bot(session_factory)
    if bot_info is None:
        pytest.skip("smoke bot not seeded")
    record_bot_id, _tenant, query = bot_info

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — smoke test needs real embedding API")

    embedder = LiteLLMEmbedder(model=DEFAULT_EMBEDDING_MODEL)
    qvec = await embedder.embed_one(query, spec=_embedding_spec(), record_tenant_id=None)
    assert qvec
    store = PgVectorStore(session_factory=session_factory, dimension=1536)

    # An intent-shaped filter the corpus does NOT label. Mirrors the
    # extract_intent output for a price-style query against a corpus
    # whose documents.metadata_json only carries source_type / title.
    blocking_filter = {
        "document_type": "policy",
        "entity": "this-entity-does-not-exist-in-corpus",
    }
    blocked = await store.hybrid_search(
        query_text=query,
        query_embedding=qvec,
        record_bot_id=record_bot_id,
        top_k=10,
        metadata_filter=blocking_filter,
    )
    assert blocked == [], (
        f"metadata filter that should block all returned {len(blocked)} chunks — "
        "JSONB containment clause may be misconfigured"
    )

    # Now without the filter — relax fallback proxy.
    recovered = await store.hybrid_search(
        query_text=query,
        query_embedding=qvec,
        record_bot_id=record_bot_id,
        top_k=10,
        metadata_filter=None,
    )
    assert len(recovered) >= DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS, (
        f"relax-equivalent search returned {len(recovered)}, expected >= "
        f"{DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS} — relax fallback would NOT recover"
    )
