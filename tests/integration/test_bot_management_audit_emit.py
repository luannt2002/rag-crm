"""bot CRUD audit emit regression tests.

Pre-fix bug: ``BotManagementService._write_audit`` passed ``tenant_id=`` to
``AuditLogModel(...)`` while the column is ``record_tenant_id`` (migration
0010/0011). The broad-except in ``_write_audit`` swallowed every
``TypeError`` so the bug was silent for the project's lifetime. Result:
zero audit rows for create / update / delete bot.

Lessons from P20 (4-layer audit rule): column name + DI + state key +
broad-except. This time the failure straddles layers 1 and 4 — kwarg
mismatch + swallow.

Tests verify:
    1. ``create_bot`` writes one audit row with the new ``record_tenant_id``
       kwarg, action="create", resource_type="bot".
    2. ``update_bot`` writes one audit row, action="update", with both
       before + after snapshots.
    3. ``delete_bot`` writes one audit row, action="delete", with the
       pre-delete snapshot in ``before`` and ``after`` = None.
    4. ``AuditLogModel`` parametrize: legacy ``tenant_id=`` kwarg now
       raises ``TypeError`` loudly (no swallow); new ``record_tenant_id=``
       kwarg succeeds.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_management_service import (
    BotManagementService,
    CreateBotCommand,
    UpdateBotCommand,
)


def _bot_config(**overrides: Any) -> BotConfig:
    """Build a minimum-valid BotConfig DTO for service-layer fakes.

    BotConfig defaults the optional bits (setting_options factory,
    plan_limits dict). We only pin the 5 required identity fields and
    let callers override ``id`` / ``bot_name`` for diff scenarios.
    """
    base: dict[str, Any] = {
        "id": uuid4(),
        "bot_id": "audit-test",
        "channel_type": "web",
        "bot_name": "Audit Test Bot",
        "tenant_id": 7,
    }
    base.update(overrides)
    return BotConfig(**base)


class _AuditCapturingSession:
    """Captures ``session.add(AuditLogModel(...))`` calls for assertion.

    Mirrors the contract ``BotManagementService._write_audit`` relies on:
    async context manager + ``add`` + ``commit``. The captured row is
    exposed on ``self.added`` so tests can check column values directly.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed = True


def _session_factory_capture() -> tuple[Any, _AuditCapturingSession]:
    session = _AuditCapturingSession()

    @asynccontextmanager
    async def _ctx() -> Any:
        yield session

    return _ctx, session


def _service_fixtures(cfg_before: BotConfig | None = None,
                      cfg_after: BotConfig | None = None) -> tuple[
    BotManagementService, _AuditCapturingSession,
]:
    """Build BotManagementService with stubbed repo/registry/uow for audit-only tests.

    Returns the service plus the audit-capturing session so tests can
    assert on the inserted row. ``cfg_before`` / ``cfg_after`` drive the
    ``get_by_id`` + repo mutate return values (None falls back to a
    fresh DTO).
    """
    cfg_a = cfg_after or _bot_config()
    cfg_b = cfg_before or cfg_a

    repo = MagicMock()
    repo.create_bot = AsyncMock(return_value=cfg_a)
    repo.update_bot = AsyncMock(return_value=cfg_a)
    repo.get_by_id = AsyncMock(return_value=cfg_b)
    repo.soft_delete = AsyncMock(return_value=True)

    registry = MagicMock()
    registry.invalidate = AsyncMock(return_value=None)

    # uow_factory yields a UoW that swallows outbox writes — we only
    # care about the audit session here.
    uow = MagicMock()
    uow.add_outbox_raw = AsyncMock(return_value=None)
    uow.commit = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _uow_ctx() -> Any:
        yield uow

    sf, session = _session_factory_capture()

    service = BotManagementService(
        repo=repo,
        registry=registry,
        uow_factory=_uow_ctx,
        session_factory=sf,
    )
    return service, session


# ---------------------------------------------------------------------------
# 1. create_bot writes audit row
# ---------------------------------------------------------------------------


class TestCreateBotAuditEmit:
    @pytest.mark.asyncio
    async def test_create_bot_writes_audit_row(self) -> None:
        cfg = _bot_config()
        service, session = _service_fixtures(cfg_after=cfg)
        cmd = CreateBotCommand(
            bot_id="audit-test", channel_type="web",
            bot_name="Audit Test Bot", tenant_id=7,
        )
        await service.create_bot(
            cmd, admin_tenant=7, actor_user_id="alice", trace_id="t-1",
        )
        assert session.committed is True
        assert len(session.added) == 1
        row = session.added[0]
        # Pre-fix: ``tenant_id`` kwarg → TypeError swallowed → row never added.
        # Post-fix: ``record_tenant_id`` kwarg → row inserted.
        assert hasattr(row, "record_tenant_id")
        assert row.action == "create"
        assert row.resource_type == "bot"
        assert row.resource_id == str(cfg.id)
        assert row.actor_user_id == "alice"
        assert row.trace_id == "t-1"
        # Bot CRUD audits live at platform scope — no UUID tenant binding.
        assert row.record_tenant_id is None
        # ``before`` is None for create; ``after`` carries the new DTO.
        assert row.before_json is None
        assert row.after_json is not None
        assert row.after_json["bot_id"] == "audit-test"


# ---------------------------------------------------------------------------
# 2. update_bot writes audit row
# ---------------------------------------------------------------------------


class TestUpdateBotAuditEmit:
    @pytest.mark.asyncio
    async def test_update_bot_writes_audit_row(self) -> None:
        before = _bot_config(bot_name="Old Name")
        after = _bot_config(id=before.id, bot_name="New Name")
        service, session = _service_fixtures(cfg_before=before, cfg_after=after)
        cmd = UpdateBotCommand(bot_name="New Name")
        await service.update_bot(
            before.id, cmd,
            admin_tenant=7, actor_user_id="bob", trace_id="t-2",
        )
        assert len(session.added) == 1
        row = session.added[0]
        assert row.action == "update"
        assert row.resource_type == "bot"
        assert row.resource_id == str(after.id)
        assert row.actor_user_id == "bob"
        # Both snapshots present so reviewer can diff.
        assert row.before_json is not None
        assert row.before_json["bot_name"] == "Old Name"
        assert row.after_json is not None
        assert row.after_json["bot_name"] == "New Name"


# ---------------------------------------------------------------------------
# 3. delete_bot writes audit row
# ---------------------------------------------------------------------------


class TestDeleteBotAuditEmit:
    @pytest.mark.asyncio
    async def test_delete_bot_writes_audit_row(self) -> None:
        cfg = _bot_config(bot_name="To Delete")
        service, session = _service_fixtures(cfg_before=cfg)
        await service.delete_bot(
            cfg.id, admin_tenant=7, actor_user_id="carol", trace_id="t-3",
        )
        assert len(session.added) == 1
        row = session.added[0]
        assert row.action == "delete"
        assert row.resource_type == "bot"
        assert row.resource_id == str(cfg.id)
        assert row.actor_user_id == "carol"
        # Delete = ``after`` is None, ``before`` snapshots the soon-gone row.
        assert row.before_json is not None
        assert row.before_json["bot_name"] == "To Delete"
        assert row.after_json is None


# ---------------------------------------------------------------------------
# 4. AuditLogModel kwarg consistency — old kwarg must FAIL loud, new succeeds.
# ---------------------------------------------------------------------------


class TestAuditLogModelKwargConsistency:
    """Regression guard for the silent-swallow class of bug.

    Pre-fix the service called ``AuditLogModel(tenant_id=...)``. Post-fix
    it MUST call ``AuditLogModel(record_tenant_id=...)``. Parametrize so
    drift in either direction fails the test.
    """

    def test_backcompat_tenant_id_kwarg_raises(self) -> None:
        from ragbot.infrastructure.db.models import AuditLogModel
        # SQLAlchemy ORM raises ``TypeError`` for unknown column kwargs.
        with pytest.raises(TypeError):
            AuditLogModel(
                tenant_id=None,  # type: ignore[call-arg]
                actor_user_id="x",
                action="create",
                resource_type="bot",
                resource_id="y",
            )

    def test_new_record_tenant_id_kwarg_accepted(self) -> None:
        from ragbot.infrastructure.db.models import AuditLogModel
        row = AuditLogModel(
            record_tenant_id=None,
            actor_user_id="x",
            action="create",
            resource_type="bot",
            resource_id="y",
        )
        # ORM model constructor doesn't run column validation, but the
        # attribute set must succeed — that's what failed pre-fix.
        assert row.actor_user_id == "x"
        assert row.action == "create"
