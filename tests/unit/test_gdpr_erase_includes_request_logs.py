"""HIGH-2 (F8 red-team report) — GDPR erasure must scrub PII previews
from ``request_logs.retrieved_chunks``, scoped to the caller's tenant.

Pre-fix: ``admin_gdpr.gdpr_erase_message`` only nullified
``messages.content``. ``request_logs.retrieved_chunks`` JSONB kept
chunk previews (up to ``DEFAULT_LOG_PREVIEW_CHARS``) forever — and
combined with CRIT-1 was a cross-tenant PII path.

Fix: erase fans out to ``RequestLogRepository.scrub_pii_for_conversation``
filtered by ``record_tenant_id`` so no cross-tenant blast.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.interfaces.http.routes import admin_gdpr


def _request(*, role: str, tenant_uuid: UUID, container: MagicMock) -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            # admin_gdpr.py reads ``request.state.record_tenant_id`` (UUID).
            record_tenant_id=tenant_uuid,
            tenant_id=tenant_uuid,  # legacy alias kept for older helpers
            user_id="alice",
            trace_id="trace-xyz",
        ),
    )


def _container(
    *,
    msg_repo: MagicMock,
    request_log_repo: MagicMock,
) -> tuple[MagicMock, MagicMock]:
    audit_repo = MagicMock()
    audit_repo.write_audit = AsyncMock(return_value=None)
    container = MagicMock()
    container.message_repo = MagicMock(return_value=msg_repo)
    container.request_log_repo = MagicMock(return_value=request_log_repo)
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    return container, audit_repo


# ---------------------------------------------------------------------------
# 1. Message-erase fans out PII scrub to request_logs (tenant-scoped).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_nullifies_request_logs_retrieved_chunks() -> None:
    """The route must call `scrub_pii_for_conversation` after the message
    soft-delete, with the caller's tenant explicitly forwarded."""
    msg_id = uuid4()
    conv_id = uuid4()
    tenant_uuid = uuid4()

    msg_repo = MagicMock()
    msg_repo.get_conversation_id = AsyncMock(return_value=conv_id)
    msg_repo.soft_delete_content = AsyncMock(return_value=True)

    request_log_repo = MagicMock()
    request_log_repo.scrub_pii_for_conversation = AsyncMock(return_value=3)

    container, audit_repo = _container(
        msg_repo=msg_repo, request_log_repo=request_log_repo,
    )
    req = _request(role="super_admin", tenant_uuid=tenant_uuid, container=container)

    resp = await admin_gdpr.gdpr_erase_message(msg_id, req)

    # Scrub was fanned out with tenant context.
    request_log_repo.scrub_pii_for_conversation.assert_awaited_once_with(
        conv_id, record_tenant_id=tenant_uuid,
    )
    assert resp["request_logs_scrubbed"] == 3

    # Audit emitted: original erasure + new scrub event.
    actions = [
        call.args[0].action for call in audit_repo.write_audit.await_args_list
    ]
    assert "gdpr_erasure_message" in actions
    assert "gdpr_erasure_request_logs_scrubbed" in actions


# ---------------------------------------------------------------------------
# 2. Tenant isolation: tenant A's erase request must NOT scrub tenant B's
#    rows. Verified by checking the repo call binds tenant A's UUID and
#    nothing else.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_only_affects_owning_tenant() -> None:
    msg_id = uuid4()
    conv_id = uuid4()
    tenant_a = uuid4()
    tenant_b = uuid4()

    msg_repo = MagicMock()
    msg_repo.get_conversation_id = AsyncMock(return_value=conv_id)
    msg_repo.soft_delete_content = AsyncMock(return_value=True)

    request_log_repo = MagicMock()
    request_log_repo.scrub_pii_for_conversation = AsyncMock(return_value=2)

    container, _audit_repo = _container(
        msg_repo=msg_repo, request_log_repo=request_log_repo,
    )
    # Caller is tenant A — request.state.tenant_id = tenant_a.
    req = _request(role="super_admin", tenant_uuid=tenant_a, container=container)

    await admin_gdpr.gdpr_erase_message(msg_id, req)

    # The scrub MUST be bound to tenant A only.
    call = request_log_repo.scrub_pii_for_conversation.await_args
    assert call.kwargs["record_tenant_id"] == tenant_a
    assert call.kwargs["record_tenant_id"] != tenant_b

    # The message lookup MUST also be tenant-scoped (defence in depth).
    msg_repo.get_conversation_id.assert_awaited_once_with(
        msg_id, record_tenant_id=tenant_a,
    )
    msg_repo.soft_delete_content.assert_awaited_once_with(
        msg_id, record_tenant_id=tenant_a,
    )


# ---------------------------------------------------------------------------
# 3. When the message is missing (cross-tenant probe / already deleted):
#    `get_conversation_id` returns None → we must NOT call scrub (no
#    blast radius), but still emit the gdpr_erasure_message audit row.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_skips_scrub_when_message_not_found() -> None:
    msg_id = uuid4()
    tenant_uuid = uuid4()

    msg_repo = MagicMock()
    msg_repo.get_conversation_id = AsyncMock(return_value=None)
    msg_repo.soft_delete_content = AsyncMock(return_value=False)

    request_log_repo = MagicMock()
    request_log_repo.scrub_pii_for_conversation = AsyncMock(return_value=0)

    container, audit_repo = _container(
        msg_repo=msg_repo, request_log_repo=request_log_repo,
    )
    req = _request(role="super_admin", tenant_uuid=tenant_uuid, container=container)

    resp = await admin_gdpr.gdpr_erase_message(msg_id, req)

    # No scrub call when conversation can't be resolved.
    request_log_repo.scrub_pii_for_conversation.assert_not_awaited()
    assert resp["request_logs_scrubbed"] == 0
    # No "scrubbed" audit row when nothing was scrubbed.
    actions = [
        call.args[0].action for call in audit_repo.write_audit.await_args_list
    ]
    assert "gdpr_erasure_request_logs_scrubbed" not in actions
