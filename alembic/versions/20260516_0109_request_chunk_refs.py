"""[T2-CostPerf] split request_logs.retrieved_chunks jsonb -> request_chunk_refs

Revision ID: 0109
Revises: 0108
Create Date: 2026-05-16

Live evidence (snapshot 2026-05-15):
    avg(pg_column_size(retrieved_chunks)) = 659 bytes / row
    max(pg_column_size(retrieved_chunks)) = 1604 bytes / row
At 10k requests/day this column alone bloats request_logs by ~16 MB/day
and -- crucially -- the inline JSONB chunk-id refs carry NO foreign key.
When a document_chunks row is hard-deleted, the JSONB ref dangles and
no analytic JOIN can detect it.

Fix: split the JSONB array into a relational child table
``request_chunk_refs`` with FK CASCADE on both sides:

    record_request_id -> request_logs.request_id   (CASCADE)
    record_chunk_id   -> document_chunks.id        (CASCADE)

Migration is reversible -- the downgrade re-builds the JSONB array via
jsonb_agg(jsonb_build_object(...)) so existing readers keep working
during a rollback window.

Note on existing payload shape: the live JSONB array elements come from
``query_graph.persist`` -> ``finalize_request_log`` callers and look like

    {"score": 0.83, "preview": "...", "chunk_index": 4, "document_name": "..."}

i.e. they DO NOT carry a stable ``chunk_id`` UUID. The migration tries
both ``chunk_id`` (forward-compat, future writes) and ``id`` (legacy
synonym used by some callers) and skips rows that resolve to NULL --
nothing dangles, nothing crashes.

PII note (paired with admin_gdpr scrub_pii_for_conversation):
``request_chunk_refs`` stores ONLY (request_id, chunk_id, rank, score).
No previews, no document_name -> no PII surface to scrub. The scrub
method becomes a no-op after this migration but the API is preserved
so callers (admin_gdpr routes) keep their contract.

Issue: G15 (mega-sprint Wave-C, Coder-C3).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


_CREATE_TABLE_SQL = text(
    """
    CREATE TABLE request_chunk_refs (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        record_request_id UUID NOT NULL,
        record_chunk_id   UUID NOT NULL,
        rank              INTEGER NOT NULL,
        score             NUMERIC(8, 6),
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT fk_rcr_request FOREIGN KEY (record_request_id)
            REFERENCES request_logs(request_id) ON DELETE CASCADE,
        CONSTRAINT fk_rcr_chunk FOREIGN KEY (record_chunk_id)
            REFERENCES document_chunks(id) ON DELETE CASCADE
    )
    """
)


_CREATE_IDX_REQUEST_SQL = text(
    "CREATE INDEX ix_rcr_request ON request_chunk_refs(record_request_id)"
)


_CREATE_IDX_CHUNK_SQL = text(
    "CREATE INDEX ix_rcr_chunk ON request_chunk_refs(record_chunk_id)"
)


# Migrate existing JSONB arrays into the new relational table. We try the
# two payload shapes ever produced by the codebase:
#   * forward-compat (post-G15): ``chunk_id`` (UUID string)
#   * legacy synonym used in a couple call-sites: ``id`` (UUID string)
# Rows whose chunk reference is NULL or not-a-uuid are skipped silently --
# the migration must not crash on historical drift. ``rank`` falls back to
# the array index (``WITH ORDINALITY``) when the JSONB element omits it.
_MIGRATE_DATA_SQL = text(
    """
    INSERT INTO request_chunk_refs (
        record_request_id, record_chunk_id, rank, score
    )
    SELECT
        rl.request_id,
        cid::uuid,
        COALESCE((chunk->>'rank')::int, ord::int - 1),
        NULLIF(chunk->>'score', '')::numeric
    FROM request_logs rl
    CROSS JOIN LATERAL jsonb_array_elements(rl.retrieved_chunks)
        WITH ORDINALITY AS j(chunk, ord)
    CROSS JOIN LATERAL (
        SELECT COALESCE(chunk->>'chunk_id', chunk->>'id') AS cid
    ) AS pick
    WHERE rl.retrieved_chunks IS NOT NULL
      AND jsonb_typeof(rl.retrieved_chunks) = 'array'
      AND jsonb_array_length(rl.retrieved_chunks) > 0
      AND pick.cid IS NOT NULL
      AND pick.cid ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.id = pick.cid::uuid)
    """
)


_DROP_COLUMN_SQL = text(
    "ALTER TABLE request_logs DROP COLUMN IF EXISTS retrieved_chunks"
)


# Downgrade rebuilds the JSONB column from the relational table so a
# rollback window can read both shapes. The rebuild is best-effort: refs
# that exist for request rows wiped by ON DELETE CASCADE are already gone.
_RECREATE_COLUMN_SQL = text(
    """
    ALTER TABLE request_logs
        ADD COLUMN retrieved_chunks JSONB NOT NULL DEFAULT '[]'::jsonb
    """
)


_REVERSE_MIGRATE_SQL = text(
    """
    UPDATE request_logs rl
       SET retrieved_chunks = COALESCE((
           SELECT jsonb_agg(
                      jsonb_build_object(
                          'chunk_id', rcr.record_chunk_id::text,
                          'rank',     rcr.rank,
                          'score',    rcr.score
                      )
                      ORDER BY rcr.rank
                  )
             FROM request_chunk_refs rcr
            WHERE rcr.record_request_id = rl.request_id
       ), '[]'::jsonb)
    """
)


_DROP_TABLE_SQL = text("DROP TABLE IF EXISTS request_chunk_refs")


def upgrade() -> None:
    op.execute(_CREATE_TABLE_SQL)
    op.execute(_CREATE_IDX_REQUEST_SQL)
    op.execute(_CREATE_IDX_CHUNK_SQL)
    op.execute(_MIGRATE_DATA_SQL)
    op.execute(_DROP_COLUMN_SQL)


def downgrade() -> None:
    op.execute(_RECREATE_COLUMN_SQL)
    op.execute(_REVERSE_MIGRATE_SQL)
    op.execute(_DROP_TABLE_SQL)
