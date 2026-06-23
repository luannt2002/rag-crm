"""Conversation repository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.ports.repository_ports import ConversationRepositoryPort
from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.message import Message
from ragbot.infrastructure.db.models import ConversationModel, MessageModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.constants import MAX_HISTORY_LIMIT_REQUEST
from ragbot.shared.types import (
    BotId,
    Channel,
    ConversationId,
    MessageId,
    Role,
    TenantId,
    UserId,
    WorkspaceId,
)


def _to_message(row: MessageModel) -> Message:
    """Convert an ORM MessageModel into a domain Message.

    @param row: ORM record
    @return: domain entity Message
    """
    return Message(
        id=MessageId(row.id),
        conversation_id=ConversationId(row.record_conversation_id),
        record_tenant_id=TenantId(row.record_tenant_id),
        record_bot_id=BotId(row.record_bot_id),
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        channel=row.channel,  # type: ignore[arg-type]
        created_at=row.created_at,
        citations=tuple(row.citations or ()),
        metadata=dict(row.metadata_json or {}),
    )


def _to_conversation(row: ConversationModel, msgs: list[MessageModel]) -> Conversation:
    """Convert an ORM ConversationModel + messages into a domain Conversation.

    @param row: ORM conversation record
    @param msgs: list of ORM message records
    @return: domain entity Conversation
    """
    return Conversation(
        id=ConversationId(row.id),
        record_tenant_id=TenantId(row.record_tenant_id),
        record_bot_id=BotId(row.record_bot_id),
        connect_id=UserId(row.connect_id),
        channel=row.channel,  # type: ignore[arg-type]
        messages=tuple(_to_message(m) for m in msgs),
        rolling_summary=row.rolling_summary,
        turn_count=row.turn_count,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
        metadata=dict(row.metadata_json or {}),
    )


class SqlAlchemyConversationRepository(TenantScopedRepository, ConversationRepositoryPort):
    """Repository for the conversations table — create or fetch a conversation
    together with its messages."""

    async def get_or_create(
        self,
        record_bot_id: BotId,
        connect_id: UserId,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> Conversation:
        """Fetch the existing conversation or create one if absent.

        Hot-path: single LEFT OUTER JOIN ``conversations`` ↔ ``messages``
        capped at :data:`MAX_HISTORY_LIMIT_REQUEST` rows so that the
        ``(conversations.lookup, messages.most-recent-N)`` pair round-trips
        once instead of twice (was an N+1 read on every turn).

        @param record_bot_id: bot UUID
        @param connect_id: external user id
        @param record_tenant_id: tenant UUID
        @param workspace_id: branch slug within the tenant (from bot config)
        @return: Conversation entity
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            conv, msgs = await self._fetch_by_keys_with_messages(
                session=session,
                record_bot_id=record_bot_id,
                connect_id=connect_id,
                record_tenant_id=tid,
            )
            if conv is not None:
                return _to_conversation(conv, msgs)

            new = ConversationModel(
                id=uuid4(),
                record_tenant_id=tid,
                workspace_id=workspace_id,
                record_bot_id=record_bot_id,
                connect_id=connect_id,
                channel="api",
                rolling_summary="",
                turn_count=0,
                last_message_at=datetime.now(tz=timezone.utc),
            )
            session.add(new)
            await session.commit()
            await session.refresh(new)
            return _to_conversation(new, [])

    async def get_by_id(
        self,
        conversation_id: ConversationId,
        *,
        record_tenant_id: TenantId,
    ) -> Conversation | None:
        """Fetch a conversation by ID together with all its messages.

        @param conversation_id: conversation UUID
        @param record_tenant_id: tenant UUID
        @return: Conversation, or None
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.record_tenant_id == tid,
                ),
            )
            if row is None:
                return None
            msgs = await self._fetch_messages(session, conversation_id)
            return _to_conversation(row, msgs)

    async def save(
        self,
        conversation: Conversation,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> None:
        """Insert a new conversation or update the existing one, tenant-fenced.

        @param conversation: Conversation aggregate to persist
        @param record_tenant_id: request tenant UUID (isolation check)
        @param workspace_id: branch slug — required when creating a new
            conversation; when appending messages to an existing conversation
            the slug is inherited from the parent row.

        IDOR-write fence: the UPDATE branch filters on BOTH the primary key AND
        ``record_tenant_id`` (``RETURNING workspace_id``), so a foreign-tenant
        conversation id matches zero rows and falls through to INSERT under the
        request tenant — it can never overwrite another tenant's conversation.
        Every message INSERT is forced to the REQUEST tenant (``tid``), not the
        message entity's own ``record_tenant_id``, so a caller cannot smuggle a
        cross-tenant message row past the parent's tenant scope.
        """
        tid = self._ensure_tenant(record_tenant_id)
        if conversation.record_tenant_id != tid:
            from ragbot.shared.errors import TenantIsolationViolation  # noqa: PLC0415
            raise TenantIsolationViolation("conversation.tenant != request tenant")

        async with self._new_session() as session:
            # Tenant-fenced UPDATE first: only a row owned by THIS tenant is
            # mutated. ``RETURNING workspace_id`` hands back the parent slug so
            # message rows inherit it without a second SELECT.
            stmt = (
                update(ConversationModel)
                .where(
                    ConversationModel.id == conversation.id,
                    ConversationModel.record_tenant_id == tid,
                )
                .values(
                    rolling_summary=conversation.rolling_summary,
                    turn_count=conversation.turn_count,
                    last_message_at=conversation.last_message_at,
                    metadata_json=dict(conversation.metadata),
                )
                .returning(ConversationModel.workspace_id)
                .execution_options(synchronize_session=False)
            )
            parent_slug = (await session.execute(stmt)).scalar_one_or_none()
            if parent_slug is None:
                # No row for this (id, tenant) — create it under the request
                # tenant. A foreign-tenant id lands here and inserts a fresh,
                # correctly-scoped row rather than clobbering the foreign row.
                session.add(
                    ConversationModel(
                        id=conversation.id,
                        record_tenant_id=tid,
                        workspace_id=workspace_id,
                        record_bot_id=conversation.record_bot_id,
                        connect_id=conversation.connect_id,
                        channel=conversation.channel,
                        rolling_summary=conversation.rolling_summary,
                        turn_count=conversation.turn_count,
                        last_message_at=conversation.last_message_at,
                        metadata_json=dict(conversation.metadata),
                    ),
                )
                # Newly inserted parent's slug is the supplied one — use it
                # for the message rows below without an extra round-trip.
                msg_workspace_id = workspace_id
            else:
                # Messages inherit the parent conversation's slug; the
                # caller-supplied value is ignored here so the FK chain stays
                # the single source of truth.
                msg_workspace_id = WorkspaceId(parent_slug)

            # Persist new messages (those not yet in DB — keyed by id).
            existing_ids = {
                row[0]
                for row in (
                    await session.execute(
                        select(MessageModel.id).where(
                            MessageModel.record_conversation_id == conversation.id,
                        ),
                    )
                ).all()
            }
            for msg in conversation.messages:
                if msg.id in existing_ids:
                    continue
                session.add(
                    MessageModel(
                        id=msg.id,
                        record_conversation_id=msg.conversation_id,
                        # Force the REQUEST tenant — never trust the message
                        # entity's own tenant field (defence vs a cross-tenant
                        # message smuggled onto a tenant-scoped conversation).
                        record_tenant_id=tid,
                        workspace_id=msg_workspace_id,
                        record_bot_id=msg.record_bot_id,
                        role=msg.role,
                        content=msg.content,
                        citations=list(msg.citations),
                        channel=msg.channel,
                        metadata_json=dict(msg.metadata),
                        created_at=msg.created_at,
                    ),
                )
            await session.commit()

    @staticmethod
    async def _fetch_by_keys_with_messages(
        *,
        session: AsyncSession,
        record_bot_id: BotId,
        connect_id: UserId,
        record_tenant_id: TenantId,
    ) -> tuple[ConversationModel | None, list[MessageModel]]:
        """Single LEFT OUTER JOIN: conversation header + most-recent N messages.

        SQL ``ORDER BY messages.created_at DESC LIMIT N`` is pushed down
        so RAM never holds more than N+1 rows. When the conversation is
        new (no messages) the outer-join contributes one row with NULL
        on the message side, which we filter back into an empty list.

        Result is returned oldest-first to match the legacy
        :meth:`_fetch_messages` contract that the rest of the pipeline
        depends on for history ordering.

        @param session: active AsyncSession (caller owns lifecycle)
        @param record_bot_id: internal bot UUID
        @param connect_id: external user id
        @param record_tenant_id: internal tenant UUID
        @return: ``(conversation_or_None, oldest_first_messages)``
        """
        stmt = (
            select(ConversationModel, MessageModel)
            .select_from(ConversationModel)
            .outerjoin(
                MessageModel,
                MessageModel.record_conversation_id == ConversationModel.id,
            )
            .where(
                ConversationModel.record_tenant_id == record_tenant_id,
                ConversationModel.record_bot_id == record_bot_id,
                ConversationModel.connect_id == connect_id,
            )
            .order_by(MessageModel.created_at.desc())
            .limit(MAX_HISTORY_LIMIT_REQUEST)
        )
        result = await session.execute(stmt)
        rows = result.all()
        if not rows:
            return None, []
        # All rows share the same conversation header (single-row lookup
        # via tenant+bot+connect_id).
        conv = rows[0][0]
        msgs: list[MessageModel] = [r[1] for r in rows if r[1] is not None]
        msgs.reverse()  # oldest-first for context
        return conv, msgs

    @staticmethod
    async def _fetch_messages(
        session: AsyncSession,
        conv_id: ConversationId,
        *,
        max_messages: int = MAX_HISTORY_LIMIT_REQUEST,
    ) -> list[MessageModel]:
        """Fetch the N most-recent messages of a conversation, oldest-first.

        Used by ``get_by_id`` when the caller already has the conversation
        UUID and does not need the JOIN. Uses a SQL LIMIT to avoid loading the
        whole history into RAM.

        @param conv_id: conversation UUID
        @param max_messages: maximum number of messages to load
        @return: list of MessageModel (oldest-first)
        """
        result = await session.execute(
            select(MessageModel)
            .where(MessageModel.record_conversation_id == conv_id)
            .order_by(MessageModel.created_at.desc())
            .limit(max_messages),
        )
        rows = list(result.scalars().all())
        rows.reverse()  # oldest-first for context
        return rows


__all__ = ["SqlAlchemyConversationRepository"]
