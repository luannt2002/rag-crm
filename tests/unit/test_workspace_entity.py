"""Workspace entity: model + migration + repository (ADR-W2-D2 §a/§d).

These pin the additive entity without changing the 4-key identity:
``bots.workspace_id`` stays the canonical slug; ``workspaces`` is a
reference row keyed ``(record_tenant_id, slug)``, RLS-scoped on tenant.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATION = (
    _REPO_ROOT
    / "alembic"
    / "_archive_pre_squash_20260618"
    / "20260610_0199_workspaces_entity.py"
)


# ── Model ──────────────────────────────────────────────────────────────

def test_model_table_and_unique_constraint() -> None:
    from ragbot.infrastructure.db.models import WorkspaceModel

    assert WorkspaceModel.__tablename__ == "workspaces"
    cols = set(WorkspaceModel.__table__.columns.keys())
    assert {"id", "record_tenant_id", "slug", "name", "created_at", "deleted_at"} <= cols
    uniques = {
        tuple(c.name for c in con.columns)
        for con in WorkspaceModel.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("record_tenant_id", "slug") in uniques


def test_model_does_not_touch_4key_tuple() -> None:
    """The 4-key bot identity must be unchanged — no record_workspace_id
    UUID leaked onto bots (ADR-W2-D2 §4 rejects it)."""
    from ragbot.infrastructure.db.models import BotModel

    assert "record_workspace_id" not in BotModel.__table__.columns.keys()
    assert "workspace_id" in BotModel.__table__.columns.keys()  # slug stays


# ── Migration ──────────────────────────────────────────────────────────

def _load_migration():
    spec = importlib.util.spec_from_file_location("m0199", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_chains_after_0198() -> None:
    mod = _load_migration()
    assert mod.revision == "0199"
    assert mod.down_revision == "0198"


def test_migration_has_rls_and_backfill() -> None:
    src = _MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "FORCE ROW LEVEL SECURITY" in src
    assert "current_setting('app.tenant_id', true)" in src
    # Backfill from the existing slugs, idempotent.
    assert "INSERT INTO workspaces" in src
    assert "SELECT DISTINCT record_tenant_id, workspace_id" in src
    assert "ON CONFLICT (record_tenant_id, slug) DO NOTHING" in src


# ── Repository ─────────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self, scalar_result=None):
        self._scalar_result = scalar_result
        self.added: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = self._scalar_result
        res.scalars.return_value.all.return_value = (
            [self._scalar_result] if self._scalar_result else []
        )
        return res

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        ...


def _repo(session: _FakeSession):
    from ragbot.infrastructure.repositories.workspace_repository import (
        WorkspaceRepository,
    )

    return WorkspaceRepository(session_factory=lambda: session)


@pytest.mark.asyncio
async def test_lookup_returns_existing() -> None:
    ws = SimpleNamespace(slug="alpha")
    repo = _repo(_FakeSession(scalar_result=ws))
    out = await repo.lookup(record_tenant_id=uuid4(), slug="alpha")
    assert out is ws


@pytest.mark.asyncio
async def test_ensure_creates_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragbot.infrastructure.repositories import workspace_repository as wr

    tenant = uuid4()
    created = SimpleNamespace(slug="newws", name="newws")
    calls = {"n": 0}

    async def _lookup(self, *, record_tenant_id, slug):
        # absent on first lookup, present after create
        calls["n"] += 1
        return None if calls["n"] == 1 else created

    monkeypatch.setattr(wr.WorkspaceRepository, "lookup", _lookup)
    session = _FakeSession()
    repo = wr.WorkspaceRepository(session_factory=lambda: session)

    out = await repo.ensure(record_tenant_id=tenant, slug="newws")
    assert out is created
    assert session.added and session.added[0].slug == "newws"
    assert session.committed


@pytest.mark.asyncio
async def test_ensure_short_circuits_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragbot.infrastructure.repositories import workspace_repository as wr

    existing = SimpleNamespace(slug="alpha")

    async def _lookup(self, *, record_tenant_id, slug):
        return existing

    monkeypatch.setattr(wr.WorkspaceRepository, "lookup", _lookup)
    session = _FakeSession()
    repo = wr.WorkspaceRepository(session_factory=lambda: session)

    out = await repo.ensure(record_tenant_id=uuid4(), slug="alpha")
    assert out is existing
    assert not session.added  # no create when it already exists


def test_container_provides_workspace_repo() -> None:
    from ragbot.bootstrap import Container
    from ragbot.infrastructure.repositories.workspace_repository import (
        WorkspaceRepository,
    )

    assert Container.workspace_repo.cls is WorkspaceRepository
