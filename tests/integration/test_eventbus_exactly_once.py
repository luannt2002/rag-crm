"""Exactly-once delivery via transactional inbox — ADR-W1-D8b.

Verifies process-then-mark semantics on :class:`RedisStreamsEventBus`:

1. Handler raise-once-then-succeed → at-least-once redelivery (same
   ``msg_id`` header, the outbox-replay path) → handler re-runs
   (``call_count == 2``) and the side-effect is applied exactly once.
2. Duplicate dispatch after success → handler runs once; the duplicate
   is XACK-skipped from the inbox-row hit (DB is the source of truth,
   the Redis ``SET NX`` key is only a fast-path hint).
3. Crash-window: inbox mark committed but XACK lost → redelivery does
   NOT double-apply (inbox-row-exists → XACK-skip).
4. Poison message (delivered > max) → persisted to the ``:dlq`` stream
   (replayable) and XACKed off the main PEL — not log-and-drop.
5. Handler contract: a handler declaring ``inbox_tx`` receives the hook
   and writes the mark inside its OWN transaction (atomic with its
   side-effects).

All Redis interaction runs on fakeredis; the inbox table lives on an
in-memory aiosqlite database (``ON CONFLICT DO NOTHING`` is portable
SQL — SQLite >= 3.24 and PostgreSQL share the syntax).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus
from ragbot.shared.json_io import dumps as json_dumps

_POLL_INTERVAL_S = 0.02
_WAIT_TIMEOUT_S = 5.0


async def _wait_for(cond, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    """Poll ``cond()`` until truthy or timeout. Returns the final verdict."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(_POLL_INTERVAL_S)
    return bool(cond())


@pytest.fixture
async def inbox_db(tmp_path):
    """File-backed aiosqlite DB with the ``event_inbox`` table + a
    ``side_effects`` table the test handlers write to.

    File-backed (NOT ``:memory:``) on purpose: in-memory aiosqlite runs
    on a single StaticPool connection, so concurrent sessions interleave
    transactions on one connection — file mode gives each session a real
    connection with real transaction isolation, like production PG.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inbox.db")
    async with engine.begin() as conn:
        await conn.execute(sa_text(
            "CREATE TABLE event_inbox ("
            " subscriber_id TEXT NOT NULL,"
            " msg_id TEXT NOT NULL,"
            " processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " PRIMARY KEY (subscriber_id, msg_id))",
        ))
        await conn.execute(sa_text(
            "CREATE TABLE side_effects (val TEXT NOT NULL)",
        ))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _count(factory, sql: str) -> int:
    async with factory() as session:
        return (await session.execute(sa_text(sql))).scalar_one()


def _payload(doc: str) -> bytes:
    return json_dumps({"doc": doc}).encode("utf-8")


class _XackFlakyRedis:
    """Proxy over fakeredis that drops the next XACK (crash-window sim)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.fail_next_xack = False
        self.xack_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def xack(self, *args: Any, **kwargs: Any) -> Any:
        self.xack_attempts += 1
        if self.fail_next_xack:
            self.fail_next_xack = False
            raise ConnectionError("simulated xack loss after commit")
        return await self._inner.xack(*args, **kwargs)


class TestExactlyOnceInbox:
    async def test_handler_raise_once_then_succeed_redelivery_reruns(
        self, inbox_db,
    ) -> None:
        """ADR §4.1 — transient handler failure must NOT consume the
        message. Redelivery with the same msg_id re-runs the handler;
        the side-effect lands exactly once."""
        redis = fakeredis.aioredis.FakeRedis()
        bus = RedisStreamsEventBus(client=redis, session_factory=inbox_db)
        subject = "inbox.test.retry"
        msg_id = str(uuid4())
        calls: list[int] = []

        async def handler(event: Any) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient failure")
            async with inbox_db() as session:
                async with session.begin():
                    await session.execute(
                        sa_text("INSERT INTO side_effects (val) VALUES (:v)"),
                        {"v": event.payload["doc"]},
                    )

        await bus.publish_raw(subject, _payload("a"), msg_id=msg_id)
        sub = await bus.subscribe(subject, handler, durable_name="d1")
        try:
            assert await _wait_for(lambda: len(calls) >= 1)
            # At-least-once redelivery: outbox replay re-emits the SAME
            # msg_id (publisher crash replay / recovery worker re-emit).
            # Pre-fix this is dedup-skipped + XACKed = dropped forever.
            await bus.publish_raw(subject, _payload("a"), msg_id=msg_id)
            assert await _wait_for(lambda: len(calls) >= 2), (
                "handler never re-ran on redelivery — message dropped "
                "(mark-before-dispatch anti-pattern still in place)"
            )
        finally:
            await sub.unsubscribe()

        assert len(calls) == 2
        applied = await _count(inbox_db, "SELECT COUNT(*) FROM side_effects")
        assert applied == 1
        marked = await _count(inbox_db, "SELECT COUNT(*) FROM event_inbox")
        assert marked == 1

    async def test_duplicate_after_success_skipped_from_inbox_hit(
        self, inbox_db,
    ) -> None:
        """ADR §4.2 — duplicate dispatch after success: handler runs
        once; the duplicate entry is XACKed from the inbox-row hit."""
        redis = fakeredis.aioredis.FakeRedis()
        bus = RedisStreamsEventBus(client=redis, session_factory=inbox_db)
        subject = "inbox.test.dup"
        msg_id = str(uuid4())
        calls: list[int] = []

        async def handler(event: Any) -> None:
            calls.append(1)

        await bus.publish_raw(subject, _payload("b"), msg_id=msg_id)
        sub = await bus.subscribe(subject, handler, durable_name="d1")
        try:
            assert await _wait_for(lambda: len(calls) == 1)
            marked = await _count(
                inbox_db, "SELECT COUNT(*) FROM event_inbox",
            )
            assert marked == 1, "success must write the inbox mark"

            await bus.publish_raw(subject, _payload("b"), msg_id=msg_id)

            stream_key = f"ragbot:{subject}"
            group = "default:d1"

            async def _pel_empty() -> bool:
                info = await redis.xpending(stream_key, group)
                return int(info["pending"]) == 0

            drained = False
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _WAIT_TIMEOUT_S
            while loop.time() < deadline:
                if await _pel_empty():
                    drained = True
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
            assert drained, "duplicate was not XACKed from the inbox hit"
        finally:
            await sub.unsubscribe()

        assert len(calls) == 1, "duplicate must not re-run the handler"

    async def test_crash_window_commit_then_xack_lost_no_double_apply(
        self, inbox_db,
    ) -> None:
        """ADR §4.3 — commit lands, XACK lost: redelivery must hit the
        inbox row and skip, never double-apply."""
        inner = fakeredis.aioredis.FakeRedis()
        redis = _XackFlakyRedis(inner)
        bus = RedisStreamsEventBus(client=redis, session_factory=inbox_db)  # type: ignore[arg-type]
        subject = "inbox.test.crashwin"
        msg_id = str(uuid4())
        calls: list[int] = []

        async def handler(event: Any) -> None:
            calls.append(1)
            async with inbox_db() as session:
                async with session.begin():
                    await session.execute(
                        sa_text("INSERT INTO side_effects (val) VALUES (:v)"),
                        {"v": event.payload["doc"]},
                    )

        redis.fail_next_xack = True
        await bus.publish_raw(subject, _payload("c"), msg_id=msg_id)
        sub = await bus.subscribe(subject, handler, durable_name="d1")
        try:
            assert await _wait_for(lambda: len(calls) == 1)
            assert await _wait_for(lambda: redis.xack_attempts >= 1)
            marked = await _count(
                inbox_db, "SELECT COUNT(*) FROM event_inbox",
            )
            assert marked == 1, "mark must be committed before XACK"

            # Redelivery of the same msg_id (replay) → inbox hit → skip.
            await bus.publish_raw(subject, _payload("c"), msg_id=msg_id)
            assert await _wait_for(lambda: redis.xack_attempts >= 2)
        finally:
            await sub.unsubscribe()

        assert len(calls) == 1, "crash-window redelivery double-applied"
        applied = await _count(inbox_db, "SELECT COUNT(*) FROM side_effects")
        assert applied == 1

    async def test_poison_message_lands_in_dlq_stream_replayable(self) -> None:
        """ADR §4.4 — six failed deliveries → entry persisted on the
        ``:dlq`` stream (XLEN == 1), XACKed off the main PEL, and the
        DLQ copy carries the original fields so an admin can replay."""
        redis = fakeredis.aioredis.FakeRedis()
        bus = RedisStreamsEventBus(client=redis)
        subject = "inbox.test.poison"
        stream = f"ragbot:{subject}"
        group = "g1"
        msg_id = str(uuid4())

        entry_id = await redis.xadd(
            stream, {"payload": _payload("p"), "msg_id": msg_id},
        )
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        # First delivery via XREADGROUP, then bump times_delivered > 5
        # through repeated XCLAIM (each claim counts as a delivery).
        await redis.xreadgroup(group, "c0", {stream: ">"}, count=1)
        for _ in range(6):
            await redis.xclaim(
                stream, group, "c0", min_idle_time=0, message_ids=[entry_id],
            )

        # XPENDING's idle filter is strictly-greater-than: let >=1 ms
        # elapse since the last delivery so idle=0 matches the entry.
        await asyncio.sleep(_POLL_INTERVAL_S)
        await bus.recover_pending_messages(
            stream=stream, group=group, consumer="c1", min_idle_ms=0,
        )

        dlq_stream = f"{stream}:dlq"
        assert await redis.xlen(dlq_stream) == 1, (
            "poison message must be persisted to the DLQ stream, "
            "not log-and-dropped"
        )
        pending = await redis.xpending(stream, group)
        assert int(pending["pending"]) == 0, "poison entry must be XACKed"

        # Replayable: the DLQ copy preserves the original fields.
        rows = await redis.xrange(dlq_stream, min="-", max="+")
        _eid, fields = rows[0]
        assert fields[b"payload"] == _payload("p")
        assert fields[b"msg_id"] == msg_id.encode("utf-8")

    async def test_handler_inbox_tx_hook_marks_in_own_transaction(
        self, inbox_db,
    ) -> None:
        """Handler contract — a handler declaring ``inbox_tx`` gets the
        hook and writes the mark atomically with its side-effects; a
        duplicate afterwards is skipped without re-running it."""
        redis = fakeredis.aioredis.FakeRedis()
        bus = RedisStreamsEventBus(client=redis, session_factory=inbox_db)
        subject = "inbox.test.hook"
        msg_id = str(uuid4())
        calls: list[int] = []

        async def handler(event: Any, *, inbox_tx: Any) -> None:
            calls.append(1)
            async with inbox_db() as session:
                async with session.begin():
                    await session.execute(
                        sa_text("INSERT INTO side_effects (val) VALUES (:v)"),
                        {"v": event.payload["doc"]},
                    )
                    await inbox_tx(session)

        await bus.publish_raw(subject, _payload("h"), msg_id=msg_id)
        sub = await bus.subscribe(subject, handler, durable_name="d1")
        try:
            assert await _wait_for(lambda: len(calls) == 1)
            marked = await _count(
                inbox_db, "SELECT COUNT(*) FROM event_inbox",
            )
            assert marked == 1, "hook must have written the inbox mark"

            await bus.publish_raw(subject, _payload("h"), msg_id=msg_id)
            stream_key = f"ragbot:{subject}"
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _WAIT_TIMEOUT_S
            drained = False
            while loop.time() < deadline:
                info = await redis.xpending(stream_key, "default:d1")
                if int(info["pending"]) == 0:
                    drained = True
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
            assert drained
        finally:
            await sub.unsubscribe()

        assert len(calls) == 1
        applied = await _count(inbox_db, "SELECT COUNT(*) FROM side_effects")
        assert applied == 1

    async def test_inbox_tx_conflict_rolls_back_handler_side_effects(
        self, inbox_db,
    ) -> None:
        """Concurrent-duplicate guard — when the mark row already exists
        (another delivery won the race), the ``inbox_tx`` hook raises
        inside the handler's transaction: side-effects roll back (zero
        double-apply) and the entry is XACKed as already-processed."""
        redis = fakeredis.aioredis.FakeRedis()
        bus = RedisStreamsEventBus(client=redis, session_factory=inbox_db)
        subject = "inbox.test.conflict"
        msg_id = str(uuid4())
        calls: list[int] = []

        # Simulate the winning concurrent delivery: mark already
        # committed by "someone else" before this dispatch runs.
        async with inbox_db() as session:
            async with session.begin():
                await session.execute(
                    sa_text(
                        "INSERT INTO event_inbox (subscriber_id, msg_id) "
                        "VALUES (:s, :m)",
                    ),
                    {"s": f"{subject}:default:d1", "m": msg_id},
                )

        async def handler(event: Any, *, inbox_tx: Any) -> None:
            calls.append(1)
            async with inbox_db() as session:
                async with session.begin():
                    await session.execute(
                        sa_text("INSERT INTO side_effects (val) VALUES (:v)"),
                        {"v": event.payload["doc"]},
                    )
                    await inbox_tx(session)

        await bus.publish_raw(subject, _payload("x"), msg_id=msg_id)
        sub = await bus.subscribe(subject, handler, durable_name="d1")
        try:
            assert await _wait_for(lambda: len(calls) == 1)
            stream_key = f"ragbot:{subject}"
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _WAIT_TIMEOUT_S
            drained = False
            while loop.time() < deadline:
                info = await redis.xpending(stream_key, "default:d1")
                if int(info["pending"]) == 0:
                    drained = True
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
            assert drained, "already-processed duplicate must be XACKed"
        finally:
            await sub.unsubscribe()

        applied = await _count(inbox_db, "SELECT COUNT(*) FROM side_effects")
        assert applied == 0, (
            "duplicate's side-effects must roll back with the mark conflict"
        )


class TestEventInboxMigration:
    """Static sanity on alembic 0198 — table shape + revision chain."""

    @staticmethod
    def _load_module():
        repo_root = Path(__file__).resolve().parents[2]
        matches = sorted(repo_root.glob("alembic/versions/*_0198_*.py"))
        assert matches, "alembic 0198 event_inbox migration missing"
        spec = importlib.util.spec_from_file_location("m0198", matches[0])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, matches[0].read_text(encoding="utf-8")

    def test_revision_chain(self) -> None:
        mod, _src = self._load_module()
        assert mod.revision == "0198"
        assert mod.down_revision == "0197"

    def test_table_shape(self) -> None:
        _mod, src = self._load_module()
        assert "event_inbox" in src
        assert "subscriber_id" in src
        assert "msg_id" in src
        assert "processed_at" in src
        # Composite PK (subscriber_id, msg_id) — the dedup guarantee.
        assert '"subscriber_id", "msg_id"' in src or "'subscriber_id', 'msg_id'" in src
