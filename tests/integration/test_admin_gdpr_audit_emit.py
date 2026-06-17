"""GDPR erasure audit emit.

Pre-fix bug: ``admin_gdpr.py`` erased message + conversation rows but
never wrote an ``audit_log`` row. GDPR right-to-erasure is a regulated
action — auditors require a tamper-evident trail per call (actor,
target id, pre-delete snapshot, trace id).

Tests verify that both DELETE routes call ``audit_repo.write_audit``
with a well-formed ``AuditEntry``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.interfaces.http.routes import admin_gdpr


def _request(*, role: str, tenant_uuid: UUID, container: MagicMock) -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            tenant_id=tenant_uuid,
            user_id="alice",
            trace_id="trace-xyz",
        ),
    )


def _container_with(
    message_repo: MagicMock,
    request_log_repo: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Stub container exposing ``message_repo()`` + ``ai_config_repo()``
    + ``request_log_repo()`` (HIGH-2 fan-out).

    Returns ``(container, audit_repo)`` so tests can assert on captured
    ``write_audit`` calls.
    """
    audit_repo = MagicMock()
    audit_repo.write_audit = AsyncMock(return_value=None)
    if request_log_repo is None:
        request_log_repo = MagicMock()
        request_log_repo.scrub_pii_for_conversation = AsyncMock(return_value=0)
    container = MagicMock()
    container.message_repo = MagicMock(return_value=message_repo)
    container.request_log_repo = MagicMock(return_value=request_log_repo)
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    return container, audit_repo


class TestErasureMessageAudit:
    @pytest.mark.asyncio
    async def test_erase_message_writes_audit_row(self) -> None:
        message_id = uuid4()
        tenant_uuid = uuid4()
        msg_repo = MagicMock()
        msg_repo.get_conversation_id = AsyncMock(return_value=None)
        msg_repo.soft_delete_content = AsyncMock(return_value=True)
        container, audit_repo = _container_with(msg_repo)
        req = _request(role="super_admin", tenant_uuid=tenant_uuid, container=container)

        resp = await admin_gdpr.gdpr_erase_message(message_id, req)
        # HIGH-2 added request_logs_scrubbed counter to the response shape.
        assert resp == {
            "ok": True,
            "erased": True,
            "message_id": str(message_id),
            "request_logs_scrubbed": 0,
        }

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "gdpr_erasure_message"
        assert entry.resource_type == "message"
        assert str(entry.resource_id) == str(message_id)
        assert str(entry.record_tenant_id) == str(tenant_uuid)
        assert entry.actor_user_id == "alice"
        assert entry.trace_id == "trace-xyz"
        assert entry.reason == "gdpr_right_to_erasure"
        assert entry.after is None
        # Pre-delete state captured for forensic diff.
        assert entry.before == {"erased": True}


class TestErasureConversationAudit:
    @pytest.mark.asyncio
    async def test_erase_conversation_writes_audit_row(self) -> None:
        conv_id = uuid4()
        tenant_uuid = uuid4()
        msg_repo = MagicMock()
        msg_repo.soft_delete_conversation = AsyncMock(return_value=42)
        container, audit_repo = _container_with(msg_repo)
        req = _request(role="super_admin", tenant_uuid=tenant_uuid, container=container)

        resp = await admin_gdpr.gdpr_erase_conversation(conv_id, req)
        # HIGH-2 added request_logs_scrubbed counter to the response shape.
        assert resp == {
            "ok": True,
            "erased_count": 42,
            "conversation_id": str(conv_id),
            "request_logs_scrubbed": 0,
        }

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "gdpr_erasure_conversation"
        assert entry.resource_type == "conversation"
        assert str(entry.resource_id) == str(conv_id)
        assert str(entry.record_tenant_id) == str(tenant_uuid)
        assert entry.before == {"erased_count": 42}
        assert entry.after is None
