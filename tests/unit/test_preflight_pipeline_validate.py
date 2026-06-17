"""Unit tests for scripts/preflight_pipeline_validate.py.

Coverage:
  test_alembic_jsonb_pattern_finds_bad      — 6.1 detects sa.JSONB without import
  test_alembic_jsonb_pattern_passes_good    — 6.1 passes file with proper import
  test_alembic_jsonb_pattern_passes_both    — 6.1 passes file that has both (import present)
  test_backcompat_reranker_enabled_detected     — 6.2 flags value='false'
  test_backcompat_reranker_provider_detected    — 6.3 flags value='null'
  test_provider_code_null_detected          — 6.4 flags active row with code IS NULL
  test_provider_env_key_missing_detected    — 6.5 flags provider with no env key
  test_provider_env_key_present_passes      — 6.5 passes when env key present
  test_registry_alias_mismatch_detected     — 6.6 flags code not in registry
  test_registry_alias_present_passes        — 6.6 passes when code in registry
"""
from __future__ import annotations

import os
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Script under test — import via importlib so we don't need PYTHONPATH tricks.
import importlib.util
import sys

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "preflight_pipeline_validate.py"
_spec = importlib.util.spec_from_file_location("preflight_pipeline_validate", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

check_alembic_jsonb_pattern = _mod.check_alembic_jsonb_pattern
check_system_config_reranker = _mod.check_system_config_reranker
check_ai_providers_code = _mod.check_ai_providers_code
check_provider_env_keys = _mod.check_provider_env_keys
check_reranker_registry_alias = _mod.check_reranker_registry_alias
LEGACY_RERANKER_VALUES = _mod.LEGACY_RERANKER_VALUES
JSONB_GOOD_IMPORT = _mod.JSONB_GOOD_IMPORT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_with_rows(rows: list[Any] | None = None, *, side_effect: Exception | None = None) -> MagicMock:
    """Return a mock asyncpg engine whose connect() yields rows."""
    fetchone_result = rows[0] if (rows and len(rows) == 1) else None
    fetchall_result = rows or []

    result_mock = MagicMock()
    result_mock.fetchone.return_value = fetchone_result
    result_mock.fetchall.return_value = fetchall_result

    execute_mock = AsyncMock(return_value=result_mock)
    if side_effect:
        execute_mock.side_effect = side_effect

    conn_mock = AsyncMock()
    conn_mock.execute = execute_mock

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx
    return engine


# ---------------------------------------------------------------------------
# 6.1 — Alembic JSONB pattern checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alembic_jsonb_pattern_finds_bad(tmp_path: Path) -> None:
    """A migration file with sa.JSONB and NO proper import must produce one fail."""
    bad_file = tmp_path / "0001_create_table.py"
    bad_file.write_text(
        "import sqlalchemy as sa\n"
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('data', sa.JSONB()))\n",
        encoding="utf-8",
    )
    with patch.object(_mod, "ALEMBIC_VERSIONS_DIR", tmp_path):
        fails = await check_alembic_jsonb_pattern()

    assert len(fails) == 1, f"Expected 1 fail, got {fails}"
    assert fails[0]["file"] == bad_file.name
    assert "sa.JSONB" in fails[0]["issue"]


@pytest.mark.asyncio
async def test_alembic_jsonb_pattern_passes_good(tmp_path: Path) -> None:
    """A migration file with proper JSONB import and NO sa.JSONB must pass."""
    good_file = tmp_path / "0002_create_table.py"
    good_file.write_text(
        "from sqlalchemy.dialects.postgresql import JSONB\n"
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('data', JSONB()))\n",
        encoding="utf-8",
    )
    with patch.object(_mod, "ALEMBIC_VERSIONS_DIR", tmp_path):
        fails = await check_alembic_jsonb_pattern()

    assert fails == [], f"Expected no fails, got {fails}"


@pytest.mark.asyncio
async def test_alembic_jsonb_pattern_passes_both(tmp_path: Path) -> None:
    """A file with both the good import AND sa.JSONB literal must still pass.

    This case can arise when a migration comment references sa.JSONB to
    explain what NOT to do; the presence of the good import makes it legal.
    """
    mixed_file = tmp_path / "0003_migration.py"
    mixed_file.write_text(
        "# Previously used sa.JSONB — now corrected.\n"
        "from sqlalchemy.dialects.postgresql import JSONB\n"
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('data', JSONB()))\n",
        encoding="utf-8",
    )
    with patch.object(_mod, "ALEMBIC_VERSIONS_DIR", tmp_path):
        fails = await check_alembic_jsonb_pattern()

    assert fails == [], f"Expected no fails, got {fails}"


# ---------------------------------------------------------------------------
# 6.2 + 6.3 — system_config reranker checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backcompat_reranker_enabled_detected() -> None:
    """reranker_enabled='false' in system_config must produce a fail for 6.2."""
    # Two consecutive calls: first for reranker_enabled, second for reranker_provider.
    enabled_row = MagicMock()
    enabled_row.__getitem__ = lambda self, i: "false"

    provider_row = MagicMock()
    provider_row.__getitem__ = lambda self, i: '"jina_ai"'

    result_enabled = MagicMock()
    result_enabled.fetchone.return_value = enabled_row

    result_provider = MagicMock()
    result_provider.fetchone.return_value = provider_row

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(side_effect=[result_enabled, result_provider])

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_system_config_reranker(engine)

    assert any(f["key"] == "reranker_enabled" for f in fails), f"Expected reranker_enabled fail; got {fails}"
    assert any("legacy" in f["issue"] or "disabled" in f["issue"] for f in fails if f.get("key") == "reranker_enabled")


@pytest.mark.asyncio
async def test_backcompat_reranker_provider_detected() -> None:
    """reranker_provider='null' in system_config must produce a fail for 6.3."""
    enabled_row = MagicMock()
    enabled_row.__getitem__ = lambda self, i: '"true"'

    provider_row = MagicMock()
    provider_row.__getitem__ = lambda self, i: "null"

    result_enabled = MagicMock()
    result_enabled.fetchone.return_value = enabled_row

    result_provider = MagicMock()
    result_provider.fetchone.return_value = provider_row

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(side_effect=[result_enabled, result_provider])

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_system_config_reranker(engine)

    assert any(f.get("key") == "reranker_provider" for f in fails), (
        f"Expected reranker_provider fail; got {fails}"
    )


# ---------------------------------------------------------------------------
# 6.4 — ai_providers.code IS NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_code_null_detected() -> None:
    """Active provider row with code IS NULL must produce a fail for 6.4."""
    row = MagicMock()
    row.__getitem__ = lambda self, i: ("some-uuid-1234", "Jina AI")[i]

    result = MagicMock()
    result.fetchall.return_value = [row]

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_ai_providers_code(engine)

    assert len(fails) == 1, f"Expected 1 fail, got {fails}"
    assert fails[0]["issue"] == "code IS NULL"
    assert "some-uuid-1234" in fails[0]["provider_id"]


@pytest.mark.asyncio
async def test_provider_code_null_passes_when_empty() -> None:
    """No active providers with NULL code → no fails for 6.4."""
    result = MagicMock()
    result.fetchall.return_value = []

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_ai_providers_code(engine)

    assert fails == []


# ---------------------------------------------------------------------------
# 6.5 — provider env key missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_env_key_missing_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active jina_ai provider with no JINA_API_KEY set must produce a fail."""
    for key in ("JINA_API_KEY", "RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    row = MagicMock()
    row.__getitem__ = lambda self, i: "jina_ai"

    result = MagicMock()
    result.fetchall.return_value = [row]

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_provider_env_keys(engine)

    assert len(fails) == 1, f"Expected 1 fail, got {fails}"
    assert fails[0]["provider_code"] == "jina_ai"
    assert "none of" in fails[0]["issue"]


@pytest.mark.asyncio
async def test_provider_env_key_present_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active jina_ai provider with JINA_API_KEY set must produce no fails."""
    monkeypatch.setenv("JINA_API_KEY", "test-key-value")

    row = MagicMock()
    row.__getitem__ = lambda self, i: "jina_ai"

    result = MagicMock()
    result.fetchall.return_value = [row]

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    fails = await check_provider_env_keys(engine)

    assert fails == [], f"Expected no fails, got {fails}"


# ---------------------------------------------------------------------------
# 6.6 — registry alias mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_alias_mismatch_detected() -> None:
    """Provider code 'unknown_provider' not in registry must produce a fail."""
    row = MagicMock()
    row.__getitem__ = lambda self, i: "unknown_provider"

    result = MagicMock()
    result.fetchall.return_value = [row]

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    with patch.object(
        sys.modules.get("ragbot.infrastructure.reranker.registry", types.ModuleType("x")),
        "list_providers",
        return_value=["jina", "jina_ai", "litellm", "null", "viranker_local"],
        create=True,
    ):
        # Patch via the module's import path since the function does a local import.
        with patch(
            "ragbot.infrastructure.reranker.registry.list_providers",
            return_value=["jina", "jina_ai", "litellm", "null", "viranker_local"],
            create=True,
        ):
            fails = await check_reranker_registry_alias(engine)

    assert len(fails) == 1, f"Expected 1 fail, got {fails}"
    assert fails[0]["provider_code"] == "unknown_provider"
    assert "not in reranker registry" in fails[0]["issue"]


@pytest.mark.asyncio
async def test_registry_alias_present_passes() -> None:
    """Provider code 'jina_ai' is in registry — must produce no fails."""
    row = MagicMock()
    row.__getitem__ = lambda self, i: "jina_ai"

    result = MagicMock()
    result.fetchall.return_value = [row]

    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(return_value=result)

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connect_ctx

    with patch(
        "ragbot.infrastructure.reranker.registry.list_providers",
        return_value=["jina", "jina_ai", "litellm", "null", "viranker_local"],
        create=True,
    ):
        fails = await check_reranker_registry_alias(engine)

    assert fails == [], f"Expected no fails, got {fails}"
