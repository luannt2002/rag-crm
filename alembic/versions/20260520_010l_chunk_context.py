"""[T1-Smartness] document_chunks.chunk_context — Anthropic CR storage column.

Revision ID: 010l
Revises: 010k
Create Date: 2026-05-20

Anthropic Contextual Retrieval (Sep 2024,
https://www.anthropic.com/news/contextual-retrieval — reports -49%
retrieval failure when chunks are augmented with LLM-generated context
before embedding) requires the per-chunk situated-context string to live
alongside the chunk so the hybrid retrieval path (BM25 over
``content || ' ' || chunk_context`` plus dense embedding over the same
augmented text) can score the bigger surface area without re-deriving the
context on every query.

The existing CR path (``contextual_chunk_enrichment.enrich_chunk_with_context``)
wraps the context inline in ``content`` (``<chunk_context>...</chunk_context>``
+ original chunk). That works for embedding-only retrieval but couples the
context to the searchable text — there is no way to BM25-boost on the
context alone, no way to inspect / re-render citations without the wrap
tags, and no way to A/B the context-only signal against the chunk-only
signal.

This migration lifts the context into a dedicated ``VARCHAR(1024) NULL``
column. ``NULL`` is the legacy / opt-out value: bots that have not flipped
``plan_limits.cr_enhanced_enabled = true`` keep the column unpopulated and
retrieval behaves byte-identically to today. Bots that opt in run the
``ChunkContextEnricher`` at ingest and persist the context string here.

GIN-trigram index is created on the column so BM25-style fuzzy match over
context is fast even on hundreds-of-thousands of chunks. The index is
``WHERE chunk_context IS NOT NULL`` so the planner skips it entirely for
opted-out bots.

Why VARCHAR(1024) (not TEXT):
* Anthropic CR paper recommends 50-100 token context labels; 1024 chars
  gives ~3-4x headroom which is enough for very-long entity-disambiguation
  labels without bloating page storage.
* Hard cap matches ``shared/constants.DEFAULT_CHUNK_CONTEXT_MAX_TOKENS``
  budget enforced at the application boundary — DB-side guard so a buggy
  enricher cannot blow up storage.
* Service emits a structlog warn if it has to truncate; ingest never
  fails on context overflow (HALLU=0 is unaffected — context is a
  retrieval-side signal, not an answer-side input).

Down: drop the index then drop the column. Forward-compat safe: the
``ChunkContextEnricher`` writes via repo helper that no-ops when the
column is missing (downgrade path test in unit suite).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010l"
down_revision = "010k"
branch_labels = None
depends_on = None

# Column length cap — keep in sync with
# ``ragbot/shared/constants.DEFAULT_CHUNK_CONTEXT_MAX_TOKENS``
# (token budget is enforced at application boundary; DB column gives a
# defence-in-depth cap so a buggy enricher cannot bloat storage).
# Inline literal so the migration is self-contained — alembic upgrade
# never imports app code.
_CHUNK_CONTEXT_VARCHAR_LEN = 1024


def upgrade() -> None:
    # Provision pg_trgm extension idempotently — required for the
    # GIN-trigram index below. Some clusters ship without it by default
    # (verified empirically post-Wave-A: ix_chunks_chunk_context_trgm
    # fails with "operator class gin_trgm_ops does not exist" otherwise).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "document_chunks",
        sa.Column(
            "chunk_context",
            sa.String(length=_CHUNK_CONTEXT_VARCHAR_LEN),
            nullable=True,
        ),
    )
    # GIN-trigram index for hybrid retrieval BM25-style fuzzy match over
    # the context string. ``WHERE chunk_context IS NOT NULL`` keeps the
    # index lean — opted-out rows do not consume index pages.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_chunk_context_trgm "
        "ON document_chunks USING GIN (chunk_context gin_trgm_ops) "
        "WHERE chunk_context IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_chunk_context_trgm")
    op.drop_column("document_chunks", "chunk_context")
