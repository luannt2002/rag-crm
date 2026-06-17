"""/sync/* audit emit.

Pre-fix bug: ``POST /sync/bot``, ``POST /sync/documents``, and
``DELETE /sync/documents`` all mutate platform state but never emitted
``audit_log`` rows. NestJS upstream calls these on every bot upsert +
bulk doc ingest; auditors require a per-call trail keyed by
``record_bot_id`` + actor token.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.interfaces.http.routes import sync as sync_route


def _request(
    *,
    container: MagicMock,
    settings: Any | None = None,
    tenant_uuid: UUID | None = None,
    role: str = "super_admin",
) -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container, settings=settings or MagicMock())
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            tenant_id=tenant_uuid or uuid4(),
            tenant_id_int=7,
            user_id="nestjs-svc",
            trace_id="sync-trace",
        ),
    )


def _container_for_sync_bot(*, audit_repo: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Container double for ``sync_bot`` — provides session_factory + registry + audit."""

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=_make_session_execute_results())
    fake_session.commit = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _ctx() -> Any:
        yield fake_session

    sf_callable = MagicMock(return_value=_ctx())

    registry = MagicMock()
    registry.invalidate = AsyncMock(return_value=None)

    container = MagicMock()
    container.session_factory = MagicMock(return_value=sf_callable)
    container.bot_registry_service = MagicMock(return_value=registry)
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    container.redis_client = MagicMock(return_value=MagicMock())
    return container, fake_session


def _make_session_execute_results() -> list[Any]:
    """Build the side_effect queue for ``sync_bot`` SQL calls.

    Sequence: SELECT existing bot (None → INSERT path),
    SELECT default model rows, INSERT bot.
    """
    no_bot = MagicMock()
    no_bot.fetchone = MagicMock(return_value=None)
    no_models = MagicMock()
    no_models.fetchall = MagicMock(return_value=[])
    insert_ok = MagicMock()
    return [no_bot, no_models, insert_ok]


class TestSyncBotAudit:
    @pytest.mark.asyncio
    async def test_sync_bot_writes_audit_row(self, monkeypatch: Any) -> None:
        # _sys_config and ensure_bot_bindings touch DB + Redis — stub
        # them out so the test focuses on the audit emission.
        fake_cfg_svc = MagicMock()
        fake_cfg_svc.get_float = AsyncMock(return_value=0.3)
        fake_cfg_svc.get_int = AsyncMock(return_value=450)
        monkeypatch.setattr(sync_route, "_sys_config", lambda req: fake_cfg_svc)

        async def _noop_bindings(*a: Any, **kw: Any) -> None:
            return None

        monkeypatch.setattr(sync_route, "ensure_bot_bindings", _noop_bindings)
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        audit_repo = MagicMock()
        audit_repo.write_audit = AsyncMock(return_value=None)
        container, _session = _container_for_sync_bot(audit_repo=audit_repo)

        req = _request(container=container)
        body = sync_route.SyncBotRequest(
            bot_id="acme-support", channel_type="web",
            bot_name="Acme Support", tenant_id=7,
            system_prompt="be helpful",
        )
        resp = await sync_route.sync_bot(body, req)
        assert resp["ok"] is True
        assert resp["action"] == "created"

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "bot_sync_upsert"
        assert entry.resource_type == "bot"
        assert entry.after is not None
        assert entry.after["bot_id"] == "acme-support"
        assert entry.after["channel_type"] == "web"
        assert entry.after["tenant_id"] == 7
        assert entry.actor_user_id == "nestjs-svc"
        assert entry.trace_id == "sync-trace"


class TestSyncDocumentsAudit:
    @pytest.mark.asyncio
    async def test_sync_documents_writes_audit_row(self, monkeypatch: Any) -> None:
        bot_uuid = uuid4()
        bot_repo = MagicMock()
        bot_repo.find_by_4key = AsyncMock(
            return_value=SimpleNamespace(id=bot_uuid),
        )

        # Stub DocumentService — replace the module-level class so the
        # route's ``DocumentService(...)`` constructor returns our fake.
        fake_doc_svc = MagicMock()
        # Default path uses replace_documents_for_bot (UPSERT).
        # delete_all_for_bot only fires when wipe_existing=True.
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=4, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        audit_repo = MagicMock()
        audit_repo.write_audit = AsyncMock(return_value=None)

        container = MagicMock()
        container.bot_repo = MagicMock(return_value=bot_repo)
        container.session_factory = MagicMock(return_value=MagicMock())
        container.embedder = MagicMock(return_value=MagicMock())
        container.ai_config_repo = MagicMock(return_value=audit_repo)

        req = _request(container=container)
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            documents=[
                sync_route.SyncDocumentItem(title="t", content="c"),
                sync_route.SyncDocumentItem(title="u", content="d"),
            ],
        )
        resp = await sync_route.sync_documents(body, req)
        assert resp["ok"] is True
        assert resp["total_documents"] == 2

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "document_bulk_ingest"
        assert entry.resource_type == "document"
        assert entry.record_bot_id == bot_uuid
        assert entry.after is not None
        assert entry.after["total_documents"] == 2
        assert entry.after["total_chunks"] == 8


class TestDeleteDocumentsAudit:
    @pytest.mark.asyncio
    async def test_delete_documents_writes_audit_row(self, monkeypatch: Any) -> None:
        bot_uuid = uuid4()
        bot_repo = MagicMock()
        bot_repo.find_by_4key = AsyncMock(
            return_value=SimpleNamespace(id=bot_uuid),
        )
        fake_doc_svc = MagicMock()
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(15, 3))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        audit_repo = MagicMock()
        audit_repo.write_audit = AsyncMock(return_value=None)

        container = MagicMock()
        container.bot_repo = MagicMock(return_value=bot_repo)
        container.session_factory = MagicMock(return_value=MagicMock())
        container.embedder = MagicMock(return_value=MagicMock())
        container.ai_config_repo = MagicMock(return_value=audit_repo)

        req = _request(container=container)
        body = sync_route.DeleteDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
        )
        resp = await sync_route.delete_documents(body, req)
        assert resp == {"ok": True, "deleted_chunks": 15, "deleted_documents": 3}

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "document_bulk_delete"
        assert entry.resource_type == "document"
        assert entry.record_bot_id == bot_uuid
        assert entry.before == {"deleted_chunks": 15, "deleted_documents": 3}
        assert entry.after is None
