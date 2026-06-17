"""/sync/documents UPSERT-safe contract.

Pre-fix bug: ``POST /sync/documents`` always called
``DocumentService.delete_all_for_bot`` BEFORE ingesting the new payload.
A partial sync (1 doc) wiped the entire knowledge base for that bot.
The smartness deepdive auditor accidentally NUKED 3 price docs while
testing 1 info-doc upload.

Fix: default to ``replace_documents_for_bot`` (UPSERT semantic — soft
delete only docs whose ``source_url`` is in the incoming payload).
``wipe_existing=true`` retains legacy behaviour but is super-admin gated.

These tests exercise the route handler directly via in-process mocks,
following the pattern in ``test_sync_audit_emit.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ragbot.interfaces.http.routes import sync as sync_route


def _request(
    *,
    container: MagicMock,
    settings: Any | None = None,
    tenant_uuid: UUID | None = None,
    role: str = "super_admin",
) -> Any:
    """Build a fake Starlette request with role + container wiring.

    Mirrors the helper in ``test_sync_audit_emit.py`` so future
    auditors can read both files in one pass.
    """
    app = MagicMock()
    app.state = SimpleNamespace(container=container, settings=settings or MagicMock())
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            tenant_id=tenant_uuid or uuid4(),
            tenant_id_int=7,
            user_id="nestjs-svc",
            trace_id="upsert-trace",
        ),
    )


def _build_container(
    *,
    bot_uuid: UUID,
    fake_doc_svc: MagicMock,
) -> tuple[MagicMock, MagicMock]:
    """Build the container double + audit_repo double for sync_documents."""
    bot_repo = MagicMock()
    bot_repo.find_by_4key = AsyncMock(
        return_value=SimpleNamespace(id=bot_uuid),
    )
    audit_repo = MagicMock()
    audit_repo.write_audit = AsyncMock(return_value=None)

    container = MagicMock()
    container.bot_repo = MagicMock(return_value=bot_repo)
    container.session_factory = MagicMock(return_value=MagicMock())
    container.embedder = MagicMock(return_value=MagicMock())
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    return container, audit_repo


class TestSyncDocumentsUpsertSafe:
    @pytest.mark.asyncio
    async def test_sync_replace_same_url_only(self, monkeypatch: Any) -> None:
        """Default path (wipe_existing=False) MUST call
        ``replace_documents_for_bot`` with the incoming URLs only —
        never ``delete_all_for_bot``.
        """
        bot_uuid = uuid4()
        fake_doc_svc = MagicMock()
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 1))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=3, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        container, _audit = _build_container(bot_uuid=bot_uuid, fake_doc_svc=fake_doc_svc)
        req = _request(container=container, role="service")
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            documents=[
                sync_route.SyncDocumentItem(
                    title="updated", content="new content", url="doc://a",
                ),
            ],
        )
        resp = await sync_route.sync_documents(body, req)
        assert resp["ok"] is True
        # Critical assertion: HARD-DELETE-all path NOT taken
        fake_doc_svc.delete_all_for_bot.assert_not_called()
        # UPSERT path took over
        fake_doc_svc.replace_documents_for_bot.assert_awaited_once()
        kwargs = fake_doc_svc.replace_documents_for_bot.await_args.kwargs
        assert kwargs["source_urls"] == ["doc://a"]

    @pytest.mark.asyncio
    async def test_sync_does_not_wipe_other_docs(self, monkeypatch: Any) -> None:
        """Multiple incoming URLs — replace_documents_for_bot receives
        EXACTLY those URLs. Other docs (different URLs) untouched
        because the UPSERT method only soft-deletes overlapping rows.
        """
        bot_uuid = uuid4()
        fake_doc_svc = MagicMock()
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=2, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        container, _audit = _build_container(bot_uuid=bot_uuid, fake_doc_svc=fake_doc_svc)
        req = _request(container=container, role="service")
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            documents=[
                sync_route.SyncDocumentItem(title="x", content="c1", url="doc://x"),
                sync_route.SyncDocumentItem(title="y", content="c2", url="doc://y"),
            ],
        )
        resp = await sync_route.sync_documents(body, req)
        assert resp["ok"] is True
        # Hard-delete path NEVER fires under default
        fake_doc_svc.delete_all_for_bot.assert_not_called()
        # The replace path receives exactly the two URLs from the payload
        kwargs = fake_doc_svc.replace_documents_for_bot.await_args.kwargs
        assert sorted(kwargs["source_urls"]) == ["doc://x", "doc://y"]

    @pytest.mark.asyncio
    async def test_sync_wipe_existing_requires_super_admin(
        self, monkeypatch: Any,
    ) -> None:
        """``wipe_existing=True`` with role < super_admin → 403.

        Service-token (level 50) is the typical NestJS upstream caller
        — they MUST NOT be able to flip the destructive switch even if
        a payload says so.
        """
        bot_uuid = uuid4()
        fake_doc_svc = MagicMock()
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=1, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        container, _audit = _build_container(bot_uuid=bot_uuid, fake_doc_svc=fake_doc_svc)
        # Service role (level 50) — under super_admin (100)
        req = _request(container=container, role="service")
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            wipe_existing=True,
            documents=[
                sync_route.SyncDocumentItem(title="t", content="c", url="doc://a"),
            ],
        )
        with pytest.raises(HTTPException) as exc:
            await sync_route.sync_documents(body, req)
        assert exc.value.status_code == 403
        assert "super_admin" in str(exc.value.detail)
        # No destructive call made because gate fired before service ops
        fake_doc_svc.delete_all_for_bot.assert_not_called()
        fake_doc_svc.replace_documents_for_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_wipe_existing_super_admin_ok(
        self, monkeypatch: Any,
    ) -> None:
        """``wipe_existing=True`` with role super_admin → legacy
        hard-wipe path executes (delete_all_for_bot called).
        """
        bot_uuid = uuid4()
        fake_doc_svc = MagicMock()
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(99, 5))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=2, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        container, audit_repo = _build_container(
            bot_uuid=bot_uuid, fake_doc_svc=fake_doc_svc,
        )
        req = _request(container=container, role="super_admin")
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            wipe_existing=True,
            documents=[
                sync_route.SyncDocumentItem(title="t", content="c", url="doc://a"),
            ],
        )
        resp = await sync_route.sync_documents(body, req)
        assert resp["ok"] is True
        # Legacy hard-wipe path fires
        fake_doc_svc.delete_all_for_bot.assert_awaited_once()
        # Safe UPSERT path skipped
        fake_doc_svc.replace_documents_for_bot.assert_not_called()
        # Audit row records the destructive flag for forensic
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.after["wipe_existing"] is True

    @pytest.mark.asyncio
    async def test_sync_default_safe_audit_records_wipe_false(
        self, monkeypatch: Any,
    ) -> None:
        """Audit row MUST capture ``wipe_existing=False`` for the safe
        path so auditors can prove a sync did NOT wipe.

        This stands in for "chunks cascade soft-delete" — the route
        delegates that responsibility to ``replace_documents_for_bot``,
        which we mock here. The route contract we pin is: default safe
        path → replace mock called, hard-wipe mock NOT called, audit
        flag = False.
        """
        bot_uuid = uuid4()
        fake_doc_svc = MagicMock()
        fake_doc_svc.replace_documents_for_bot = AsyncMock(return_value=(0, 1))
        fake_doc_svc.delete_all_for_bot = AsyncMock(return_value=(0, 0))
        fake_doc_svc.ingest = AsyncMock(return_value=SimpleNamespace(
            chunks=4, embedded=True, title="t",
            document_id=uuid4(),
        ))
        monkeypatch.setattr(
            sync_route, "DocumentService", lambda **kw: fake_doc_svc,
        )
        monkeypatch.setattr(sync_route, "enforce_tenant_match", lambda req, tid: None)

        container, audit_repo = _build_container(
            bot_uuid=bot_uuid, fake_doc_svc=fake_doc_svc,
        )
        req = _request(container=container, role="service")
        body = sync_route.SyncDocumentsRequest(
            tenant_id=7, bot_id="b", channel_type="web",
            documents=[
                sync_route.SyncDocumentItem(
                    title="t", content="c", url="doc://only",
                ),
            ],
        )
        resp = await sync_route.sync_documents(body, req)
        assert resp["ok"] is True

        fake_doc_svc.delete_all_for_bot.assert_not_called()
        fake_doc_svc.replace_documents_for_bot.assert_awaited_once()

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.after["wipe_existing"] is False
        assert entry.after["total_documents"] == 1
        assert entry.after["total_chunks"] == 4
