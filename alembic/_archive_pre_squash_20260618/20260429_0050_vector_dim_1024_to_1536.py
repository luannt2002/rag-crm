"""Vector dimension 1024 → 1536 (text-embedding-3-small alignment).

P0 from Z1 audit (`AUDIT_DEEPDIVE_MIGRATIONS_DB_20260429_142848.md`):
Migrations 0013 + 0014 declared `embedding vector(1024)` and
`query_embedding vector(1024)`, but the configured embedding model
`text-embedding-3-small` outputs 1536 dimensions. Latent P0:

- A fresh `alembic upgrade head` against an empty DB recreates the
  columns at vector(1024). The first ingest with the 1536-dim model
  then fails: `ERROR: expected 1024 dimensions, not 1536`.
- This was masked in the current dev/prod DB because someone ALTERed
  the columns to vector(1536) out-of-band (psql), but the alembic
  chain still says 1024. Any disaster-recovery or fresh spin-up
  rebuilds at 1024 → broken.

This migration is **idempotent + non-destructive**: it inspects each
column's current type via `format_type(atttypid, atttypmod)` (the
pg-supplied formatter, not arithmetic on atttypmod) and SKIPs the
ALTER when already vector(1536). So:
  - Existing prod DB (already at 1536 manually): NO-OP, fast.
  - Fresh DB at 1024: ALTERed to 1536, HNSW index recreated.

INCIDENT NOTE (2026-04-29):
A previous draft of this migration computed dim arithmetically as
`atttypmod - 4`, assuming pgvector packs `N+4` into atttypmod (it
doesn't — the formatter `format_type()` adds the +4 framing itself,
but `atttypmod` for vector is just `N`). That made the predicate
`current == 1536` false on already-1536 columns, triggering an
ALTER ... USING NULL that wiped 95 production embeddings. They were
restored by re-embedding from `dc.content` (text survives column
type changes). This rewrite uses `format_type()` text comparison
to eliminate the entire arithmetic-error class.

DDL strategy (only when actually at 1024):
  1. SET LOCAL statement_timeout=0 (long ops can take minutes)
  2. DROP HNSW index (cannot ALTER underlying column with HNSW)
  3. ALTER COLUMN ... TYPE vector(1536) USING NULL
     (1024-dim values cannot cast to 1536; operator MUST re-ingest
     after, OR pre-populate via re-embed script — Phase 2.9
     raise-on-mismatch ensures any fresh ingest writes correct dim)
  4. CREATE INDEX hnsw at the new dimension (m=16, ef_construction=64)

Migrations:
  0013 — created document_chunks.embedding vector(1024)
  0014 — created semantic_cache.query_embedding vector(1024)
  0050 — this — align both to vector(1536)

Revision ID: 0050
Revises: 0049
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


TARGET_TYPE = "vector(1536)"
LEGACY_TYPE = "vector(1024)"


def _current_type(conn, table: str, column: str) -> str | None:
    """Return formatted type string (e.g. 'vector(1536)') or None.

    Uses `format_type(atttypid, atttypmod)` — Postgres's own formatter,
    which knows how to render pgvector's atttypmod into 'vector(N)'
    correctly. We compare TEXT, never arithmetic.
    """
    return conn.execute(
        sa.text(
            """
            SELECT format_type(a.atttypid, a.atttypmod) AS typ
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = :table
              AND a.attname = :column
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ),
        {"table": table, "column": column},
    ).scalar()


def _alter_to_target(conn, table: str, column: str, hnsw_index: str) -> None:
    """Idempotent ALTER COLUMN vector → TARGET_TYPE with HNSW recreate.

    Triple safety:
      - Type-text comparison (no arithmetic).
      - Skip cleanly when already at target.
      - Refuse to wipe data unless dim is genuinely the legacy value.
    """
    cur = _current_type(conn, table, column)
    if cur is None:
        # Column doesn't exist — table maybe dropped in a future migration.
        return
    if cur == TARGET_TYPE:
        # Common case: already migrated. NO-OP.
        return
    if cur != LEGACY_TYPE:
        # Unexpected dim — refuse to ALTER USING NULL because we cannot be
        # sure the operator wants a wipe. Halt loudly.
        raise RuntimeError(
            f"vector dim migration: {table}.{column} has unexpected type "
            f"{cur!r} (expected {LEGACY_TYPE!r} or {TARGET_TYPE!r}). "
            "Refusing to ALTER USING NULL — manual review required.",
        )

    # Genuinely at vector(1024) → safe to migrate.
    conn.execute(sa.text(f'DROP INDEX IF EXISTS {hnsw_index}'))
    conn.execute(
        sa.text(
            f'ALTER TABLE {table} ALTER COLUMN {column} '
            f'TYPE {TARGET_TYPE} USING NULL'
        )
    )
    conn.execute(
        sa.text(
            f'CREATE INDEX {hnsw_index} ON {table} '
            f'USING hnsw ({column} vector_cosine_ops) '
            f'WITH (m = 16, ef_construction = 64)'
        )
    )


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("SET LOCAL statement_timeout = 0"))

    _alter_to_target(
        conn,
        table="document_chunks",
        column="embedding",
        hnsw_index="ix_chunks_embedding_hnsw",
    )
    _alter_to_target(
        conn,
        table="semantic_cache",
        column="query_embedding",
        hnsw_index="ix_sem_cache_embedding_hnsw",
    )


def downgrade() -> None:
    """Reverse: 1536 → 1024.

    Symmetric to upgrade — swap TARGET/LEGACY semantics. Refuses to wipe
    if the column isn't actually at 1536.
    """
    conn = op.get_bind()
    conn.execute(sa.text("SET LOCAL statement_timeout = 0"))

    def _down(table: str, column: str, hnsw_index: str) -> None:
        cur = _current_type(conn, table, column)
        if cur is None or cur == LEGACY_TYPE:
            return
        if cur != TARGET_TYPE:
            raise RuntimeError(
                f"downgrade: {table}.{column} has unexpected type {cur!r}. "
                "Manual review required.",
            )
        conn.execute(sa.text(f'DROP INDEX IF EXISTS {hnsw_index}'))
        conn.execute(
            sa.text(
                f'ALTER TABLE {table} ALTER COLUMN {column} '
                f'TYPE {LEGACY_TYPE} USING NULL'
            )
        )
        conn.execute(
            sa.text(
                f'CREATE INDEX {hnsw_index} ON {table} '
                f'USING hnsw ({column} vector_cosine_ops) '
                f'WITH (m = 16, ef_construction = 64)'
            )
        )

    _down("document_chunks", "embedding", "ix_chunks_embedding_hnsw")
    _down("semantic_cache", "query_embedding", "ix_sem_cache_embedding_hnsw")
