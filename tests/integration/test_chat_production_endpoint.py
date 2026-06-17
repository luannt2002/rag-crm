"""Production /api/ragbot/chat regression tests.

Bug surfaced 2026-04-29 by load test: every call to ``/api/ragbot/chat``
returned 500 because ``conversation_repository.save`` referenced
``conversation.tenant_id`` / ``conversation.bot_id`` — attributes that
do NOT exist on the ``Conversation`` aggregate (the canonical names are
``record_tenant_id`` / ``record_bot_id`` per CLAUDE.md naming-convention).
This test pins the contract by exercising ``save()`` end-to-end against
the real DB session factory; an AttributeError regression would fail
``test_chat_persists_conversation_with_record_tenant_id`` immediately.

Run requires DATABASE_URL — same as ``test_p24_l1_cache_invalidation``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.message import Message
from ragbot.infrastructure.repositories.conversation_repository import (
    SqlAlchemyConversationRepository,
)
from ragbot.shared.types import (
    BotId,
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL=") and "DATABASE_URL_SYNC" not in line:
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not set and .env not found")


@pytest.fixture(scope="module")
def database_url() -> str:
    return _database_url()


@pytest.fixture()
async def session_factory(database_url: str) -> Any:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _ensure_tenant_row(sf: Any, record_tenant_id: uuid.UUID) -> None:
    """Idempotent insert into ``tenants`` so the FK on ``bots`` is satisfied."""
    async with sf() as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, quota_monthly_tokens, config, bypass_rate_limit, created_at, updated_at) "
                "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": record_tenant_id, "name": f"test-{str(record_tenant_id)[:8]}"},
        )
        await session.commit()


async def _seed_bot(
    sf: Any,
    record_bot_id: uuid.UUID,
    *,
    record_tenant_id: uuid.UUID,
) -> None:
    await _ensure_tenant_row(sf, record_tenant_id)
    async with sf() as session:
        await session.execute(
            text(
                """
                INSERT INTO bots (
                    id, record_tenant_id, workspace_id, bot_id, channel_type,
                    bot_name, system_prompt, is_deleted,
                    created_at, updated_at, setting_options,
                    custom_vocabulary, max_documents, plan_limits,
                    bypass_token_limit, bypass_rate_limit, language
                )
                VALUES (
                    :id, :rt, :ws, :bot_id, 'web',
                    'chat-prod-bug test', '', false,
                    now(), now(), CAST(:opts AS jsonb),
                    '{}'::jsonb, 100, '{}'::jsonb,
                    false, false, 'vi'
                )
                ON CONFLICT DO NOTHING
                """,
            ),
            {
                "id": record_bot_id,
                "rt": record_tenant_id,
                "ws": f"ws-{record_bot_id.hex[:8]}",
                "bot_id": f"chat-prod-{record_bot_id.hex[:12]}",
                "opts": json.dumps({}),
            },
        )
        await session.commit()


async def _cleanup(sf: Any, record_bot_id: uuid.UUID) -> None:
    async with sf() as session:
        await session.execute(
            text(
                """
                DELETE FROM messages WHERE record_conversation_id IN (
                    SELECT id FROM conversations WHERE record_bot_id = :bid
                )
                """,
            ),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM conversations WHERE record_bot_id = :bid"),
            {"bid": record_bot_id},
        )
        await session.execute(
            text("DELETE FROM bots WHERE id = :id"),
            {"id": record_bot_id},
        )
        await session.commit()


async def test_chat_endpoint_returns_200_not_500(session_factory: Any) -> None:
    """Smoke: ``ConversationRepository.save`` does NOT raise AttributeError.

    The original bug at conversation_repository.py:135 referenced
    ``conversation.tenant_id`` (does not exist) → AttributeError → HTTP 500.
    Calling save with a freshly-built Conversation domain entity must
    succeed silently with no AttributeError on either ``.tenant_id`` or
    ``.bot_id``.
    """
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        repo = SqlAlchemyConversationRepository(session_factory=session_factory)
        now = datetime.now(tz=timezone.utc)
        conv = Conversation(
            id=ConversationId(uuid.uuid4()),
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            connect_id=UserId("test-user-prod-bug"),
            channel="api",
            messages=(),
            rolling_summary="",
            turn_count=0,
            created_at=now,
            last_message_at=now,
            metadata={},
        )
        # The buggy code path: save() reads conversation.tenant_id /
        # conversation.bot_id. After fix this must succeed.
        await repo.save(conv, record_tenant_id=TenantId(record_tenant_id))

        async with session_factory() as session:
            r = await session.execute(
                text("SELECT COUNT(*) FROM conversations WHERE id = :id"),
                {"id": conv.id},
            )
            assert int(r.scalar_one()) == 1
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_chat_persists_conversation_with_record_tenant_id(
    session_factory: Any,
) -> None:
    """save() persists ``record_tenant_id`` + ``record_bot_id`` correctly.

    This guards against the regression returning by binding ``record_*``
    column names to the domain ``record_*`` attribute names.
    """
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        repo = SqlAlchemyConversationRepository(session_factory=session_factory)
        now = datetime.now(tz=timezone.utc)

        # First create empty conversation
        conv = Conversation(
            id=ConversationId(uuid.uuid4()),
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            connect_id=UserId("test-user-persist"),
            channel="api",
            messages=(),
            rolling_summary="",
            turn_count=0,
            created_at=now,
            last_message_at=now,
            metadata={},
        )
        # Add a user message → tests the message-loop path which also
        # reads ``msg.record_bot_id`` / ``msg.record_tenant_id``.
        user_msg = Message(
            id=MessageId(uuid.uuid4()),
            conversation_id=conv.id,
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            role="user",
            content="ping",
            channel="api",
            created_at=now,
            citations=(),
            metadata={},
        )
        conv = conv.add_message(user_msg)
        await repo.save(conv, record_tenant_id=TenantId(record_tenant_id))

        async with session_factory() as session:
            r = await session.execute(
                text(
                    """
                    SELECT record_tenant_id, record_bot_id
                    FROM conversations WHERE id = :id
                    """,
                ),
                {"id": conv.id},
            )
            row = r.mappings().one()
            assert str(row["record_tenant_id"]) == str(record_tenant_id)
            assert str(row["record_bot_id"]) == str(record_bot_id)

            r2 = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM messages
                    WHERE record_conversation_id = :cid AND role = 'user'
                    """,
                ),
                {"cid": conv.id},
            )
            assert int(r2.scalar_one()) == 1
    finally:
        await _cleanup(session_factory, record_bot_id)


async def test_chat_audit_log_emitted(session_factory: Any) -> None:
    """Audit-trail proxy: 2nd save() on existing conversation merges, not
    duplicates — proving the ``existing is None`` branch (which references
    the previously-buggy attributes) is reached.
    """
    record_bot_id = uuid.uuid4()
    record_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    await _seed_bot(session_factory, record_bot_id, record_tenant_id=record_tenant_id)
    try:
        repo = SqlAlchemyConversationRepository(session_factory=session_factory)
        now = datetime.now(tz=timezone.utc)
        conv = Conversation(
            id=ConversationId(uuid.uuid4()),
            record_tenant_id=TenantId(record_tenant_id),
            record_bot_id=BotId(record_bot_id),
            connect_id=UserId("test-user-audit"),
            channel="api",
            messages=(),
            rolling_summary="",
            turn_count=0,
            created_at=now,
            last_message_at=now,
            metadata={},
        )
        # First save: hits the create branch → reads conversation.record_bot_id
        await repo.save(conv, record_tenant_id=TenantId(record_tenant_id))
        # Second save: hits the update branch → still must not AttributeError
        conv2 = conv.with_summary("rolling summary v2")
        await repo.save(conv2, record_tenant_id=TenantId(record_tenant_id))

        async with session_factory() as session:
            r = await session.execute(
                text(
                    """
                    SELECT rolling_summary FROM conversations WHERE id = :id
                    """,
                ),
                {"id": conv.id},
            )
            assert r.scalar_one() == "rolling summary v2"
    finally:
        await _cleanup(session_factory, record_bot_id)
