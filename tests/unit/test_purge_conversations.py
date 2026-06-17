"""Light unit tests for scripts/purge_expired_conversations.py.

We test:
  1. --dry-run mode executes SELECT COUNT but never a DELETE.
  2. _compute_cutoff math is `now - days` within seconds tolerance.
  3. _resolve_retention_days falls back to DEFAULT_CONVERSATION_RETENTION_DAYS
     when neither CLI nor system_config provides a value.

We mock SQLAlchemy's engine so no real DB is required.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ragbot.shared.constants import DEFAULT_CONVERSATION_RETENTION_DAYS

_SCRIPT_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), os.pardir, os.pardir,
        "scripts", "purge_expired_conversations.py",
    )
)


def _load_purge_module():
    """Load the CLI script as a module for import-level testing."""
    spec = importlib.util.spec_from_file_location(
        "purge_expired_conversations", _SCRIPT_PATH,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["purge_expired_conversations"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def purge_mod():
    return _load_purge_module()


def test_cutoff_computed_from_days_arg(purge_mod):
    before = datetime.now(tz=timezone.utc)
    cutoff = purge_mod._compute_cutoff(days=30)
    after = datetime.now(tz=timezone.utc)

    expected_low = before - timedelta(days=30, seconds=2)
    expected_high = after - timedelta(days=30) + timedelta(seconds=2)
    assert expected_low <= cutoff <= expected_high
    assert cutoff.tzinfo is not None


def test_default_days_uses_constant(purge_mod):
    # No CLI, empty system_config -> falls back to constant.
    assert (
        purge_mod._resolve_retention_days({}, cli_days=None)
        == DEFAULT_CONVERSATION_RETENTION_DAYS
    )
    # system_config integer wins over default.
    assert purge_mod._resolve_retention_days(
        {"conversation_retention_days": 7}, cli_days=None
    ) == 7
    # CLI trumps everything.
    assert purge_mod._resolve_retention_days(
        {"conversation_retention_days": 7}, cli_days=45
    ) == 45
    # Invalid system_config value → default.
    assert (
        purge_mod._resolve_retention_days(
            {"conversation_retention_days": "not-an-int"}, cli_days=None,
        )
        == DEFAULT_CONVERSATION_RETENTION_DAYS
    )


def test_dry_run_mode_no_delete_sql(purge_mod):
    """Invoke main(['--dry-run']) with engine + system_config fully mocked.

    Verify that:
      - a COUNT was executed (so the dry-run knows the volume), and
      - no DELETE was executed.
    """
    # Simulate DB env present so _database_url() doesn't raise.
    fake_url = "postgresql+psycopg2://u:p@localhost:5432/x"
    with patch.dict(os.environ, {"DATABASE_URL_SYNC": fake_url}, clear=False):
        # Patch engine creation inside the module.
        fake_engine = MagicMock(name="engine")
        # _load_system_config uses engine.connect() as a context manager.
        conn_ctx = MagicMock(name="conn_ctx")
        conn = MagicMock(name="conn")
        conn_ctx.__enter__.return_value = conn
        conn_ctx.__exit__.return_value = False
        fake_engine.connect.return_value = conn_ctx

        # Track every SQL string executed against the connection.
        executed_sql: list[str] = []

        class _Result:
            def __init__(self, rows=None, scalar=0):
                self._rows = rows or []
                self._scalar = scalar

            def fetchall(self):
                return self._rows

            def scalar(self):
                return self._scalar

        def _execute(stmt, *args, **kwargs):
            executed_sql.append(str(stmt))
            text = str(stmt).lower()
            if "count(*)" in text:
                return _Result(scalar=5)
            if "system_config" in text:
                return _Result(rows=[])
            return _Result()

        conn.execute.side_effect = _execute

        with patch.object(purge_mod, "create_engine", return_value=fake_engine, create=True):
            # create_engine is imported locally in main(); patch via sqlalchemy module.
            import sqlalchemy
            with patch.object(sqlalchemy, "create_engine", return_value=fake_engine):
                rc = purge_mod.main(["--dry-run"])

    assert rc == 0
    joined = " || ".join(s.lower() for s in executed_sql)
    assert "delete from conversations" not in joined, (
        f"Dry-run executed a DELETE: {executed_sql}"
    )
    assert "count(*)" in joined, "Dry-run should have counted expired rows"
