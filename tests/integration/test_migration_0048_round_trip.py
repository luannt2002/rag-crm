"""Migration 0048 round-trip safety.

Verifies that:
  * upgrade installs ``uq_documents_bot_content_hash`` (partial UNIQUE).
  * downgrade removes it cleanly.
  * upgrade after downgrade re-creates it without state corruption.

Tests use ``alembic`` programmatic API so they hit the real DB pointed
at by ``DATABASE_URL_SYNC`` (alembic uses the sync DSN).
"""
from __future__ import annotations

import os

import pytest

try:
    from alembic import command  # type: ignore[import-not-found]
    from alembic.config import Config  # type: ignore[import-not-found]
    import psycopg2  # type: ignore[import-not-found]
    _ALEMBIC_OK = True
except Exception:  # noqa: BLE001
    _ALEMBIC_OK = False


pytestmark = pytest.mark.skipif(
    not _ALEMBIC_OK, reason="alembic + psycopg2 required for round-trip test",
)


_INDEX_NAME = "uq_documents_bot_content_hash"


def _alembic_cfg() -> "Config":
    cfg_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
    )
    cfg = Config(cfg_path)
    return cfg


def _index_exists(conn) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM pg_indexes "
        "WHERE schemaname='public' AND tablename='documents' AND indexname=%s",
        (_INDEX_NAME,),
    )
    n = cur.fetchone()[0]
    cur.close()
    return n > 0


def _sync_url() -> str:
    url = os.environ.get("DATABASE_URL_SYNC")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL_SYNC="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL_SYNC not set")


def test_migration_0048_round_trip():
    """upgrade head → downgrade 0047 → upgrade head; index goes away then comes back."""
    sync_url = _sync_url()
    # Strip SQLAlchemy driver prefix (psycopg2 expects bare postgresql://).
    if sync_url.startswith("postgresql+psycopg2://"):
        psycopg2_url = sync_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    else:
        psycopg2_url = sync_url

    cfg = _alembic_cfg()

    # Pre-condition: head should already be at 0048 (or we upgrade).
    command.upgrade(cfg, "head")
    conn = psycopg2.connect(psycopg2_url)
    try:
        assert _index_exists(conn) is True, "expected uq index to exist post-upgrade"
    finally:
        conn.close()

    # Downgrade — index gone.
    command.downgrade(cfg, "0047")
    conn = psycopg2.connect(psycopg2_url)
    try:
        assert _index_exists(conn) is False, "expected uq index removed post-downgrade"
    finally:
        conn.close()

    # Upgrade again — index back.
    command.upgrade(cfg, "head")
    conn = psycopg2.connect(psycopg2_url)
    try:
        assert _index_exists(conn) is True, "expected uq index restored after re-upgrade"
    finally:
        conn.close()
