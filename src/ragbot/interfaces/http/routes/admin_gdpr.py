"""Admin GDPR routes (Phần 2.B — right to erasure).

Erases raw `content` of messages while preserving `request_logs` + hashes so
metrics/accuracy history stay intact. Only tenant_admin/superadmin for own tenant.

P0 — every erasure MUST emit a forensic ``audit_log`` row.
GDPR right-to-erasure is a regulated action; auditors expect every
``erase_message`` / ``erase_conversation`` call to leave a tamper-evident
trail (actor, target id, pre-delete snapshot, trace id). Mirror the
``admin_tenant_policy`` pattern: write through the shared ``ai_config_repo``
since ``audit_log`` is the unified table since migration 0046.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.shared.constants import DEFAULT_TENANT_ADMIN_LEVEL
from ragbot.shared.rbac import require_min_level
from ragbot.shared.types import TenantId

router = APIRouter(tags=["admin/gdpr"])


def _require_admin(request: Request) -> None:
    require_min_level(request, DEFAULT_TENANT_ADMIN_LEVEL)


def _caller_tenant_uuid(request: Request) -> UUID | None:
    """Lift ``request.state.record_tenant_id`` UUID for audit row.

    Returns ``None`` only when the value is missing or unparseable —
    RBAC gate already rejected unauthenticated callers before we reach here.
    """
    raw = getattr(request.state, "record_tenant_id", None)
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


@router.delete("/gdpr/erase/message/{message_id}")
async def gdpr_erase_message(
    message_id: UUID, request: Request,
) -> dict[str, object]:
    _require_admin(request)
    container = request.app.state.container
    repo = container.message_repo()
    caller_tid = _caller_tenant_uuid(request)
    tenant_id = TenantId(request.state.record_tenant_id)

    # Resolve the conversation FIRST so we can fan-out the scrub even if
    # `soft_delete_content` happens to be a no-op (already-deleted row).
    conv_id = await repo.get_conversation_id(
        message_id, record_tenant_id=tenant_id,
    )
    erased = await repo.soft_delete_content(
        message_id, record_tenant_id=tenant_id,
    )

    # pre-fix `request_logs.retrieved_chunks`
    # JSONB persisted PII chunk previews forever — GDPR-erase only nullified
    # `messages.content`. Combined with the gap was a cross-tenant
    # PII path. Now scrub all retrieved_chunks tied to this message's
    # conversation; tenant-scoped so no cross-tenant blast.
    scrubbed_logs = 0
    request_log_repo = container.request_log_repo()
    if conv_id is not None:
        scrubbed_logs = await request_log_repo.scrub_pii_for_conversation(
            conv_id, record_tenant_id=tenant_id,
        )

    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        AuditEntry(
            record_tenant_id=caller_tid,
            record_bot_id=None,
            actor_user_id=getattr(request.state, "user_id", None) or "unknown",
            action="gdpr_erasure_message",
            resource_type="message",
            resource_id=message_id,
            before={"erased": bool(erased)},
            after=None,
            reason="gdpr_right_to_erasure",
            trace_id=getattr(request.state, "trace_id", "n/a"),
        ),
    )
    if scrubbed_logs > 0:
        await audit_repo.write_audit(
            AuditEntry(
                record_tenant_id=caller_tid,
                record_bot_id=None,
                actor_user_id=getattr(request.state, "user_id", None) or "unknown",
                action="gdpr_erasure_request_logs_scrubbed",
                resource_type="request_logs",
                resource_id=conv_id,
                before={"rows_scrubbed": int(scrubbed_logs)},
                after=None,
                reason="gdpr_right_to_erasure",
                trace_id=getattr(request.state, "trace_id", "n/a"),
            ),
        )
    return {
        "ok": True,
        "erased": erased,
        "message_id": str(message_id),
        "request_logs_scrubbed": scrubbed_logs,
    }


@router.delete("/gdpr/erase/conversation/{conversation_id}")
async def gdpr_erase_conversation(
    conversation_id: UUID, request: Request,
) -> dict[str, object]:
    _require_admin(request)
    container = request.app.state.container
    repo = container.message_repo()
    caller_tid = _caller_tenant_uuid(request)
    tenant_id = TenantId(request.state.record_tenant_id)
    count = await repo.soft_delete_conversation(
        conversation_id, record_tenant_id=tenant_id,
    )

    # scrub PII previews from request_logs for this conversation.
    request_log_repo = container.request_log_repo()
    scrubbed_logs = await request_log_repo.scrub_pii_for_conversation(
        conversation_id, record_tenant_id=tenant_id,
    )

    audit_repo = container.ai_config_repo()
    await audit_repo.write_audit(
        AuditEntry(
            record_tenant_id=caller_tid,
            record_bot_id=None,
            actor_user_id=getattr(request.state, "user_id", None) or "unknown",
            action="gdpr_erasure_conversation",
            resource_type="conversation",
            resource_id=conversation_id,
            before={"erased_count": int(count)},
            after=None,
            reason="gdpr_right_to_erasure",
            trace_id=getattr(request.state, "trace_id", "n/a"),
        ),
    )
    if scrubbed_logs > 0:
        await audit_repo.write_audit(
            AuditEntry(
                record_tenant_id=caller_tid,
                record_bot_id=None,
                actor_user_id=getattr(request.state, "user_id", None) or "unknown",
                action="gdpr_erasure_request_logs_scrubbed",
                resource_type="request_logs",
                resource_id=conversation_id,
                before={"rows_scrubbed": int(scrubbed_logs)},
                after=None,
                reason="gdpr_right_to_erasure",
                trace_id=getattr(request.state, "trace_id", "n/a"),
            ),
        )
    return {
        "ok": True,
        "erased_count": count,
        "conversation_id": str(conversation_id),
        "request_logs_scrubbed": scrubbed_logs,
    }


__all__ = ["router"]
