"""MessageRepository — direct CRUD for `messages` table.

Separate from ConversationRepository because Privacy 2.B / GDPR endpoints act
on individual message rows (soft-delete of `content`) without loading whole
conversation aggregates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.models import ConversationModel, MessageModel
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId


class MessageRepository:
    """Repository cho bảng messages — CRUD trực tiếp (tách biệt ConversationRepo cho GDPR)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Khởi tạo repository với session factory."""
        self._sf = session_factory

    @staticmethod
    def _ensure(record_tenant_id: TenantId | None) -> TenantId:
        if record_tenant_id is None:
            raise TenantIsolationViolation("tenant_id required")
        return record_tenant_id

    async def create(
        self,
        *,
        message_id: UUID,
        conversation_id: UUID,
        record_tenant_id: TenantId,
        record_bot_id: UUID,
        role: str,
        content: str,
        channel: str = "api",
    ) -> UUID:
        """Tạo message mới trong DB.
        @param role: vai trò (user, assistant)
        @param content: nội dung tin nhắn
        @return: UUID message
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            # Inherit slug from the parent conversation row — keeps the
            # FK chain the single source of truth and avoids a 4-arg
            # signature in callers that already resolved conversation_id.
            parent_ws = await session.scalar(
                select(ConversationModel.workspace_id).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.record_tenant_id == tid,
                ),
            )
            if parent_ws is None:
                raise TenantIsolationViolation(
                    f"conversation {conversation_id} not found in tenant {tid}",
                )
            row = MessageModel(
                id=message_id,
                record_conversation_id=conversation_id,
                record_tenant_id=tid,
                workspace_id=parent_ws,
                record_bot_id=record_bot_id,
                role=role,
                content=content,
                channel=channel,
            )
            session.add(row)
            await session.commit()
            return message_id

    async def get_content(
        self,
        message_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> str | None:
        """Lấy nội dung message (None nếu đã xóa hoặc không tồn tại).
        @param message_id: UUID message
        @return: nội dung hoặc None
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            row = await session.get(MessageModel, message_id)
            if row is None or row.record_tenant_id != tid:
                return None
            if row.deleted_at is not None:
                return None
            return row.content

    async def soft_delete_content(
        self,
        message_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> bool:
        """Null-out `content` and set `deleted_at`. Preserve row + FK integrity.

        Returns True if a row was modified, False if not found / wrong tenant.
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                update(MessageModel)
                .where(
                    MessageModel.id == message_id,
                    MessageModel.record_tenant_id == tid,
                    MessageModel.deleted_at.is_(None),
                )
                .values(content="", deleted_at=datetime.now(tz=timezone.utc))
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def get_conversation_id(
        self,
        message_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> UUID | None:
        """Return the message's ``record_conversation_id`` (tenant-scoped).

        Returns ``None`` when not found or cross-tenant; never raises.
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            row = await session.get(MessageModel, message_id)
            if row is None or row.record_tenant_id != tid:
                return None
            return row.record_conversation_id

    async def soft_delete_conversation(
        self,
        conversation_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> int:
        """Null-out content of every message in a conversation. Returns count."""
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                update(MessageModel)
                .where(
                    MessageModel.record_conversation_id == conversation_id,
                    MessageModel.record_tenant_id == tid,
                    MessageModel.deleted_at.is_(None),
                )
                .values(content="", deleted_at=datetime.now(tz=timezone.utc))
            )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)


__all__ = ["MessageRepository"]
