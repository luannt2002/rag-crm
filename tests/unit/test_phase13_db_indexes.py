"""Phase 13 DB hardening — verify HNSW prod params and FK indexes exist.

Skip-marked: live-DB integration. Run explicitly with
``pytest tests/unit/test_phase13_db_indexes.py --run-db``.
"""

from __future__ import annotations

import os
import re

import pytest


_DSN_ENV_KEYS = ("DATABASE_URL_SYNC", "DATABASE_URL", "ALEMBIC_SQLALCHEMY_URL")

_EXPECTED_HNSW = (
    "ix_chunks_embedding_hnsw",
    # Renamed from ``ix_sem_cache_embedding_hnsw`` to ``ix_semantic_cache_qe_hnsw``
    # in production (descriptive: query_embedding column on semantic_cache).
    # Same HNSW params, same column, just clearer naming.
    "ix_semantic_cache_qe_hnsw",
)
_EXPECTED_FK_INDEXES = (
    "ix_bmb_record_model_id",
    "ix_bmb_record_fallback_model_id",
    "ix_messages_record_bot_id",
    "ix_tmp_record_bot_id",
    "ix_tmp_record_model_id",
)
_HNSW_M = 32
_HNSW_EF_CONSTRUCTION = 200


def _resolve_sync_dsn() -> str | None:
    """Return a sync psycopg2 DSN or None if the env exposes no DB URL."""
    for key in _DSN_ENV_KEYS:
        raw = os.getenv(key)
        if raw:
            return raw.replace("+asyncpg", "+psycopg2")
    return None


pytestmark = pytest.mark.skipif(
    _resolve_sync_dsn() is None,
    reason="DB integration only — set DATABASE_URL_SYNC to run",
)


@pytest.fixture(scope="module")
def _engine():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    pytest.importorskip("psycopg2")
    dsn = _resolve_sync_dsn()
    assert dsn is not None
    engine = sqlalchemy.create_engine(dsn, pool_pre_ping=True)
    yield engine
    engine.dispose()


def test_hnsw_uses_production_params(_engine) -> None:
    """Both HNSW indexes are rebuilt with m=32, ef_construction=200."""
    from sqlalchemy import text

    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE indexname = ANY(:names)"
            ).bindparams(names=list(_EXPECTED_HNSW))
        ).all()
    found = {name: defn for name, defn in rows}
    for name in _EXPECTED_HNSW:
        assert name in found, f"HNSW index {name} missing"
        defn = found[name]
        m_match = re.search(r"m\s*=\s*'?(\d+)'?", defn)
        ef_match = re.search(r"ef_construction\s*=\s*'?(\d+)'?", defn)
        assert m_match is not None, f"{name} indexdef has no m param: {defn}"
        assert ef_match is not None, (
            f"{name} indexdef has no ef_construction param: {defn}"
        )
        assert int(m_match.group(1)) == _HNSW_M, (
            f"{name} m={m_match.group(1)} (expected {_HNSW_M})"
        )
        assert int(ef_match.group(1)) == _HNSW_EF_CONSTRUCTION, (
            f"{name} ef_construction={ef_match.group(1)} "
            f"(expected {_HNSW_EF_CONSTRUCTION})"
        )


def test_missing_fk_indexes_now_exist(_engine) -> None:
    """All 5 FK indexes from audit Gap I1 are present."""
    from sqlalchemy import text

    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = ANY(:names)"
            ).bindparams(names=list(_EXPECTED_FK_INDEXES))
        ).all()
    actual = {row[0] for row in rows}
    for name in _EXPECTED_FK_INDEXES:
        assert name in actual, f"FK index {name} missing after migration"
