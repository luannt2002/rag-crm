"""GuardrailEvent repository (v0.3.0 Task 3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.models_guardrail import GuardrailEventModel
from ragbot.infrastructure.db.models_monitoring import RequestLogModel
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG


class GuardrailRepository:
    """Persist guardrail hits. One row per rule match."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Khởi tạo repository với session factory."""
        self._sf = session_factory

    async def insert(self, event: dict[str, Any]) -> UUID:
        """Insert one guardrail event.

        Expected keys in `event`:
          message_id (int, required), guardrail_type, rule_id, severity,
          action_taken, details (dict), and internal UUIDs:
          record_tenant_id, record_request_id, record_step_id.

        P19-3: accept both legacy unprefixed keys (tenant_id / request_id /
        step_id) and new record_*-prefixed keys to keep existing callers
        working during the rename. The ORM column names are always the
        record_*-prefixed variants, so unprefixed callers were silently
        writing NULL before this fix — that's why the guardrail_events
        table looked empty despite warnings firing.
        """
        event_id = event.get("event_id") or uuid4()

        def _pick(primary: str, legacy: str) -> Any:
            v = event.get(primary)
            return v if v is not None else event.get(legacy)

        record_request_id = _pick("record_request_id", "request_id")
        async with self._sf() as session:
            # guardrail_events.workspace_id inherits from the parent
            # request_log via the soft ``record_request_id`` ref. When the
            # request_id isn't supplied (early-pipeline guardrails before
            # request_log is fully wired) fall back to the system slug so
            # the row still satisfies the CHECK constraint.
            ws_slug = WORKSPACE_SYSTEM_SLUG
            if record_request_id is not None:
                parent_ws = await session.scalar(
                    select(RequestLogModel.workspace_id).where(
                        RequestLogModel.request_id == record_request_id,
                    ),
                )
                if parent_ws is not None:
                    ws_slug = parent_ws
            row = GuardrailEventModel(
                event_id=event_id,
                message_id=int(event["message_id"]),
                record_request_id=record_request_id,
                record_tenant_id=_pick("record_tenant_id", "tenant_id"),
                workspace_id=ws_slug,
                record_step_id=_pick("record_step_id", "step_id"),
                guardrail_type=event["guardrail_type"],
                rule_id=event["rule_id"],
                severity=event["severity"],
                action_taken=event["action_taken"],
                details=event.get("details") or {},
            )
            session.add(row)
            await session.commit()
            return event_id

    async def list_by_message(self, message_id: int) -> list[dict[str, Any]]:
        """Lấy danh sách guardrail events theo message_id, sắp xếp theo thời gian.
        @param message_id: ID message (INT từ upstream)
        @return: danh sách dict event
        """
        async with self._sf() as session:
            stmt = (
                select(GuardrailEventModel)
                .where(GuardrailEventModel.message_id == int(message_id))
                .order_by(GuardrailEventModel.detected_at.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "event_id": str(r.event_id),
                    "message_id": r.message_id,
                    "record_request_id": str(r.record_request_id) if r.record_request_id else None,
                    "record_tenant_id": str(r.record_tenant_id) if r.record_tenant_id else None,
                    "record_step_id": str(r.record_step_id) if r.record_step_id else None,
                    "guardrail_type": r.guardrail_type,
                    "rule_id": r.rule_id,
                    "severity": r.severity,
                    "action_taken": r.action_taken,
                    "details": r.details or {},
                    "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                }
                for r in rows
            ]


__all__ = ["GuardrailRepository"]
