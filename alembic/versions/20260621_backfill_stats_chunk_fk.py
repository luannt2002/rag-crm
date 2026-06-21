"""Backfill document_service_index.record_chunk_id from chunk_index.

STEP-5 attribution (Phase B-1): stats entities were ingested with
``record_chunk_id`` NULL (the column existed but ``bulk_insert`` never wrote
it). The stats route therefore answers from a synthetic chunk whose sentinel id
is FK-rejected, so its retrieval is invisible to CHUNK_RECALL.

Each entity carries ``attributes_json->>'chunk_index'`` and
``(record_document_id, chunk_index)`` maps 1-to-1 to ``document_chunks`` — so
the real source chunk FK can be re-derived WITHOUT a re-ingest (no re-embed,
no re-chunk). The callback ref-writer reads ``record_chunk_id`` from the matched
entities and writes ``request_chunk_refs`` for them, WITHOUT feeding the raw
chunks to the LLM (HALLU-safe: generate context unchanged). Going forward
``bulk_insert`` resolves the FK at INSERT time, so this backfill is one-shot for
the pre-existing corpus.

Derived-index rebuild (not content state) — reproducible from chunks, so a
tracked migration is the correct vehicle (vs an out-of-band UPDATE).

Revision ID: backfill_stats_chunk_fk_20260621
Revises: align_model_stack_jina_20260619
Create Date: 2026-06-21
"""

from __future__ import annotations

from alembic import op

revision = "backfill_stats_chunk_fk_20260621"
down_revision = "align_model_stack_jina_20260619"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Re-link each entity to its source chunk via (record_document_id,
    # chunk_index). Only touches rows that are NULL + carry a chunk_index.
    op.execute(
        """
        UPDATE document_service_index si
        SET record_chunk_id = dc.id
        FROM document_chunks dc
        WHERE si.record_chunk_id IS NULL
          AND si.attributes_json ? 'chunk_index'
          AND dc.record_document_id = si.record_document_id
          AND dc.chunk_index = (si.attributes_json->>'chunk_index')::int
        """
    )


def downgrade() -> None:
    # Faithful reverse: NULL only the rows whose record_chunk_id IS the
    # chunk_index-derived value this migration set (leave any other-source FK).
    op.execute(
        """
        UPDATE document_service_index si
        SET record_chunk_id = NULL
        WHERE si.record_chunk_id IS NOT NULL
          AND si.attributes_json ? 'chunk_index'
          AND EXISTS (
            SELECT 1 FROM document_chunks dc
            WHERE dc.id = si.record_chunk_id
              AND dc.record_document_id = si.record_document_id
              AND dc.chunk_index = (si.attributes_json->>'chunk_index')::int
          )
        """
    )
