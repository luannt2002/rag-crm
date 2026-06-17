"""FK regression pins — foundations of the bot-purge saga (ADR-W1-D4 §2d).

``BotLifecycleService.purge_bot`` issues a single ``DELETE FROM bots``
and relies on live ``ON DELETE CASCADE`` foreign keys to wipe the child
tables (documents, document_chunks, semantic_cache, ...). Those FKs were
added by historical migrations and verified live; a future migration
that drops or weakens one would silently re-open the orphan-rows bug the
saga closes. These pins read the migration SOURCE (pure python — runs in
every CI without a DB); the integration variant queries ``pg_constraint``
when ``DATABASE_URL`` is present.

Note: ``fk_chunks_bot`` lives in migration ``0108`` (not ``0107c`` as the
gap report sketched) — pinned from its own source file.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIG_0107C = _ALEMBIC_DIR / "20260516_0107c_missing_fks_orphan_reset.py"
_MIG_0108 = _ALEMBIC_DIR / "20260516_0108_chunks_record_bot_id.py"


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_semantic_cache_fk_cascade_pinned() -> None:
    """``fk_semantic_cache_bot`` must stay CASCADE — purge S2 depends on it."""
    mod = _load_module(_MIG_0107C)
    assert (
        "fk_semantic_cache_bot", "semantic_cache", "record_bot_id",
        "bots", "id", "CASCADE",
    ) in mod._FK_CONSTRAINTS


def test_documents_fk_cascade_pinned() -> None:
    mod = _load_module(_MIG_0107C)
    assert (
        "fk_documents_bot", "documents", "record_bot_id",
        "bots", "id", "CASCADE",
    ) in mod._FK_CONSTRAINTS


def test_chunks_fk_cascade_pinned_in_source() -> None:
    """``fk_chunks_bot`` was added by raw DDL in 0108 — pin the source text."""
    src = _MIG_0108.read_text(encoding="utf-8")
    assert "ADD CONSTRAINT fk_chunks_bot" in src
    assert (
        "FOREIGN KEY (record_bot_id) REFERENCES bots(id) ON DELETE CASCADE"
        in src
    )


@pytest.mark.asyncio
async def test_fk_cascade_live_on_database() -> None:
    """Integration variant — assert the three FKs exist with CASCADE on
    the live DB. Skips when ``DATABASE_URL`` is absent (unit-only CI)."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL env var required for live FK pin")
    from sqlalchemy import text  # noqa: PLC0415 — only needed when DB present
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT conname, confdeltype
                    FROM pg_constraint
                    WHERE conname IN (
                        'fk_semantic_cache_bot',
                        'fk_chunks_bot',
                        'fk_documents_bot'
                    )
                    """,
                ),
            )
            # asyncpg returns the ``"char"`` column as bytes — normalise.
            rows = {
                r[0]: (r[1].decode() if isinstance(r[1], bytes) else r[1])
                for r in result.fetchall()
            }
    finally:
        await engine.dispose()

    # confdeltype 'c' == CASCADE in pg_constraint.
    assert rows.get("fk_semantic_cache_bot") == "c"
    assert rows.get("fk_chunks_bot") == "c"
    assert rows.get("fk_documents_bot") == "c"
