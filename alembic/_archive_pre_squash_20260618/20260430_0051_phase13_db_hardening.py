"""Phase 13 DB hardening — HNSW production params + 5 missing FK indexes.

Source: ``reports/DB_SCHEMA_PERFECT_AUDIT_20260430.md`` (Gap V1 + Gap I1).

1. Rebuild ``ix_chunks_embedding_hnsw`` and ``ix_sem_cache_embedding_hnsw``
   with ``m=32, ef_construction=200`` (vs prior ``m=16, ef_construction=64``).
   Industry guidance for 1536-dim cosine vectors lifts recall@10 from
   ~0.88 to ~0.95+. Build cost is paid once; query latency is unchanged.

2. Add 5 single-column FK indexes so child-side ``ON DELETE CASCADE`` does
   not seq-scan when a parent row is removed:
       - ``bot_model_bindings.record_model_id``
       - ``bot_model_bindings.record_fallback_model_id``
       - ``messages.record_bot_id`` (composite
         ``(record_tenant_id, record_bot_id)`` cannot serve a
         ``WHERE record_bot_id = ?`` lookup as the leading column is wrong)
       - ``tenant_model_policy.record_bot_id``
       - ``tenant_model_policy.record_model_id``

CONCURRENTLY caveat
-------------------
Alembic wraps each migration in a transaction; ``CREATE INDEX CONCURRENTLY``
is rejected inside a transaction. We use plain ``CREATE INDEX IF NOT EXISTS``,
matching the precedent set by migration ``0044``. The dev/staging tables are
small (247 chunks, 217 cache rows). For production replay where an exclusive
lock on the parent table matters, operators should:

    1. ``alembic stamp 0051`` (skip the in-tx DDL).
    2. Manually run the equivalent ``DROP INDEX`` + ``CREATE INDEX
       CONCURRENTLY`` statements off-band.

Revision: 0051
Down revision: 0050
"""

from __future__ import annotations

from alembic import op


revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


_HNSW_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("ix_chunks_embedding_hnsw", "document_chunks", "embedding"),
    ("ix_sem_cache_embedding_hnsw", "semantic_cache", "query_embedding"),
)

_FK_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_bmb_record_model_id", "bot_model_bindings", "record_model_id"),
    (
        "ix_bmb_record_fallback_model_id",
        "bot_model_bindings",
        "record_fallback_model_id",
    ),
    ("ix_messages_record_bot_id", "messages", "record_bot_id"),
    ("ix_tmp_record_bot_id", "tenant_model_policy", "record_bot_id"),
    ("ix_tmp_record_model_id", "tenant_model_policy", "record_model_id"),
)

_HNSW_PROD_M = 32
_HNSW_PROD_EF_CONSTRUCTION = 200
_HNSW_LEGACY_M = 16
_HNSW_LEGACY_EF_CONSTRUCTION = 64


def _rebuild_hnsw(m: int, ef_construction: int) -> None:
    """Drop + recreate every HNSW index with the given build parameters."""
    for index_name, table, column in _HNSW_TARGETS:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table} USING hnsw ({column} vector_cosine_ops) "
            f"WITH (m = {m}, ef_construction = {ef_construction})"
        )


def upgrade() -> None:
    """Apply production HNSW params and create the 5 missing FK indexes."""
    _rebuild_hnsw(_HNSW_PROD_M, _HNSW_PROD_EF_CONSTRUCTION)
    for index_name, table, column in _FK_INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
        )


def downgrade() -> None:
    """Drop the 5 FK indexes and restore prior HNSW build parameters."""
    for index_name, _table, _column in _FK_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    _rebuild_hnsw(_HNSW_LEGACY_M, _HNSW_LEGACY_EF_CONSTRUCTION)
