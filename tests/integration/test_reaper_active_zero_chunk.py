"""Integration gate G2 — reaper catches active-0-chunk rows (ADR-W1-D4 §2c).

Real Postgres execution of ``_scan_stuck_documents`` (the unit suite
pins the SQL string; only a live DB can prove the predicate semantics).
Seeded matrix:

- ``active`` + 0 chunks + stale ``updated_at``      → RETURNED (the bug)
- ``active`` + ``chunks_processed=5``               → not returned
- ``active`` + 0 ``chunks_processed`` + fresh row   → not returned
- ``active`` + NULL ``chunks_processed`` but a chunk
  row exists (crash mid-write)                      → not returned
- ``DRAFT`` old                                     → RETURNED (regression)
- ``active`` + 0 chunks + stale, with an OLD outbox
  upload event predating the UPSERT bump            → RETURNED (the
  ``GREATEST(created_at, updated_at)`` anti-dup fix — the original
  upload event must not mask a crashed re-ingest)

Skips without ``DATABASE_URL``. All rows use fresh UUIDs + cleanup.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.interfaces.workers.document_recovery_worker import (
    _scan_stuck_documents,
)
from ragbot.shared.constants import SUBJECT_DOCUMENT_UPLOADED

pytestmark = pytest.mark.integration

_THRESHOLD_S = 900


@pytest.fixture()
async def session_factory() -> AsyncIterator[Any]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL env var required for integration tests")
    engine = create_async_engine(dsn, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _seed_tenant_bot(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, bot_uuid = uuid.uuid4(), uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, quota_monthly_tokens, config, "
            "bypass_rate_limit, created_at, updated_at) "
            "VALUES (:id, :name, 0, '{}'::jsonb, false, now(), now())",
        ),
        {"id": tenant_id, "name": f"reaper-g2-{str(tenant_id)[:8]}"},
    )
    await session.execute(
        text(
            """
            INSERT INTO bots (
                id, bot_id, channel_type, workspace_id, record_tenant_id,
                bot_name, system_prompt, setting_options, custom_vocabulary,
                max_documents, plan_limits, bypass_token_limit,
                bypass_rate_limit, language, is_deleted,
                created_at, updated_at
            ) VALUES (
                :id, :bot_id, 'web', 'reaper-g2-ws', :tid,
                'reaper-g2-bot', '', '{}'::jsonb, '{}'::jsonb,
                0, '{}'::jsonb, false, false, 'vi', false, now(), now()
            )
            """,
        ),
        {
            "id": bot_uuid,
            "bot_id": f"reaper-g2-{str(bot_uuid)[:8]}",
            "tid": tenant_id,
        },
    )
    return tenant_id, bot_uuid


async def _seed_doc(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    bot_uuid: uuid.UUID,
    state: str,
    chunks_processed: int | None,
    age: str,
    with_chunk: bool = False,
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    await session.execute(
        text(
            f"""
            INSERT INTO documents (
                id, record_tenant_id, workspace_id, record_bot_id,
                source_url, document_name, tool_name, mime_type, language,
                state, version, content_hash, acl, metadata_json,
                chunks_processed, created_at, updated_at
            ) VALUES (
                :id, :tid, 'reaper-g2-ws', :bid,
                :src, 'reaper-g2-doc', :tool, 'text/plain', 'vi',
                :state, 1, :ch, '{{}}', '{{}}'::jsonb,
                :cp, now() - interval '{age}', now() - interval '{age}'
            )
            """,  # noqa: S608 — age is a test-controlled literal
        ),
        {
            "id": doc_id, "tid": tenant_id, "bid": bot_uuid,
            "src": f"https://example.test/reaper-g2/{doc_id}",
            "ch": uuid.uuid4().hex, "state": state, "cp": chunks_processed,
            # uq_doc_tool: (tenant, bot, tool_name) unique.
            "tool": f"reaper-g2-{str(doc_id)[:8]}",
        },
    )
    if with_chunk:
        await session.execute(
            text(
                """
                INSERT INTO document_chunks (
                    id, record_document_id, record_bot_id,
                    chunk_index, content, content_hash, created_at
                ) VALUES (:id, :doc, :bid, 0, 'c', :ch, now())
                """,
            ),
            {
                "id": uuid.uuid4(), "doc": doc_id, "bid": bot_uuid,
                "ch": uuid.uuid4().hex,
            },
        )
    return doc_id


@pytest.mark.asyncio
async def test_scan_returns_active_zero_chunk_row(session_factory: Any) -> None:
    sf = session_factory
    async with sf() as session:
        tenant_id, bot_uuid = await _seed_tenant_bot(session)
        stuck = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="active", chunks_processed=None, age="1 hour",
        )
        ctrl_processed = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="active", chunks_processed=5, age="1 hour",
        )
        ctrl_fresh = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="active", chunks_processed=0, age="10 seconds",
        )
        ctrl_has_chunk = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="active", chunks_processed=None, age="1 hour",
            with_chunk=True,
        )
        draft_old = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="DRAFT", chunks_processed=None, age="1 hour",
        )
        # Anti-dup fix case: stuck active row whose ORIGINAL upload event
        # predates the UPSERT bump — must still be returned.
        stuck_with_old_event = await _seed_doc(
            session, tenant_id=tenant_id, bot_uuid=bot_uuid,
            state="active", chunks_processed=None, age="1 hour",
        )
        await session.execute(
            text(
                """
                INSERT INTO outbox (
                    id, subject, payload, headers, trace_id,
                    record_tenant_id, workspace_id, retry_count, status,
                    metadata_json, created_at
                ) VALUES (
                    :id, :subject, :payload, '{}'::jsonb, 'reaper-g2',
                    :tid, 'reaper-g2-ws', 0, 'processed',
                    '{}'::jsonb, now() - interval '2 hours'
                )
                """,
            ),
            {
                "id": uuid.uuid4(),
                "subject": SUBJECT_DOCUMENT_UPLOADED,
                "payload": json.dumps(
                    {"document_id": str(stuck_with_old_event)},
                ).encode("utf-8"),
                "tid": tenant_id,
            },
        )
        # Control: replay event NEWER than the row (in-flight) → excluded.
        await session.execute(
            text(
                """
                INSERT INTO outbox (
                    id, subject, payload, headers, trace_id,
                    record_tenant_id, workspace_id, retry_count, status,
                    metadata_json, created_at
                ) VALUES (
                    :id, :subject, :payload, '{}'::jsonb, 'reaper-g2',
                    :tid, 'reaper-g2-ws', 0, 'pending',
                    '{}'::jsonb, now()
                )
                """,
            ),
            {
                "id": uuid.uuid4(),
                "subject": SUBJECT_DOCUMENT_UPLOADED,
                "payload": json.dumps({"document_id": str(draft_old)}).encode(
                    "utf-8",
                ),
                "tid": tenant_id,
            },
        )
        await session.commit()

    scan_session = sf()
    try:
        rows = await _scan_stuck_documents(
            session=scan_session,
            stuck_threshold_s=_THRESHOLD_S,
            batch_size=100,
        )
    finally:
        await scan_session.close()

    try:
        returned = {r.id for r in rows if r.record_bot_id == bot_uuid}
        assert stuck in returned, "active-0-chunk stale row missed"
        assert stuck_with_old_event in returned, (
            "old upload event masked the crashed row — GREATEST fix broken"
        )
        assert ctrl_processed not in returned
        assert ctrl_fresh not in returned
        assert ctrl_has_chunk not in returned, (
            "row with persisted chunks replayed — NOT EXISTS guard broken"
        )
        # draft_old has a NEWER pending event → anti-dup excludes it.
        assert draft_old not in returned
    finally:
        async with sf() as session:
            await session.execute(
                text("DELETE FROM outbox WHERE record_tenant_id = :t"),
                {"t": tenant_id},
            )
            await session.execute(
                text("DELETE FROM bots WHERE id = :b"), {"b": bot_uuid},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id},
            )
            await session.commit()
