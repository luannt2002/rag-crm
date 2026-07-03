"""Redis Streams event bus.

At-least-once delivery qua XREADGROUP/XACK consumer groups.
Compatible interface với EventBusPort.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Final
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.domain.events.base import DomainEvent
from ragbot.shared.errors import BusError, InboxDuplicateError
from ragbot.shared.json_io import dumps as json_dumps, loads as json_loads
from ragbot.shared.constants import (
    DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL,
    DEFAULT_BUS_CONCURRENCY_PER_TENANT,  # noqa: F401 — kept for back-compat / other callers
    DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE,
    DEFAULT_BUS_DLQ_MAX_DELIVERIES,
    DEFAULT_BUS_HANDLER_CONCURRENCY,
    DEFAULT_BUS_TENANT_SEM_MAX,
    DEFAULT_OUTBOX_DEDUP_TTL_S,
    DEFAULT_STREAM_MAXLEN,
    REDIS_XREAD_BLOCK_MS,
    REDIS_XREAD_COUNT,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Redis key prefix for Msg-Id consumer-side dedup. The publisher stamps
# every outbox row with its UUID as the ``Msg-Id`` header. The key is a
# FAST-PATH HINT only (ADR-W1-D8b): a hint-hit still consults the
# ``event_inbox`` table — only an inbox-row-exists may XACK-and-skip.
# The hint never carries XACK authority because it is written BEFORE the
# handler runs; trusting it would drop messages whose handler failed
# (mark-before-dispatch anti-pattern).
_OUTBOX_DEDUP_PREFIX = "ragbot:outbox:dedup:"

# Transactional-inbox SQL (ADR-W1-D8b). Portable across PostgreSQL and
# SQLite >= 3.24 — both accept targetless ``ON CONFLICT DO NOTHING``;
# the composite PK (subscriber_id, msg_id) is the conflict arbiter.
_INBOX_MARK_SQL = (
    "INSERT INTO event_inbox (subscriber_id, msg_id) "
    "VALUES (:subscriber_id, :msg_id) ON CONFLICT DO NOTHING"
)
_INBOX_SEEN_SQL = (
    "SELECT 1 FROM event_inbox "
    "WHERE subscriber_id = :subscriber_id AND msg_id = :msg_id"
)

# Name of the optional keyword-only handler parameter that receives the
# inbox-mark hook. Handlers declaring it own the mark: they MUST await
# the hook on their session inside the same transaction as their
# side-effects (atomic process-then-mark). Handlers without it keep the
# old 1-arg signature — the bus wraps the mark in its own transaction
# after the handler returns.
_INBOX_TX_PARAM = "inbox_tx"

logger = structlog.get_logger(__name__)


class _StreamSubscription:
    """Handle cho 1 subscription — cancel loop khi unsubscribe."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task

    async def unsubscribe(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class RedisStreamsEventBus:
    """EventBus qua Redis Streams."""

    def __init__(
        self,
        client: Redis,
        stream_prefix: str = "ragbot",
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._redis = client
        self._prefix = stream_prefix
        # Transactional inbox (ADR-W1-D8b). ``None`` disables the DB
        # mark: duplicates then re-run the handler (at-least-once, the
        # safe direction) instead of being skipped — never dropped.
        self._session_factory = session_factory
        # Ingest-fairness semaphores (2026-06-13 owner spec): keyed by
        # (bot_id, channel_type) [cap 5] and by workspace [cap 10] instead of
        # tenant. Lazy, in-process, each bounded by DEFAULT_BUS_TENANT_SEM_MAX
        # with a shared overflow entry. Transient runtime state — reset on
        # restart is correct; nothing persisted.
        self._tenant_sems: dict[str, asyncio.Semaphore] = {}  # legacy/back-compat
        self._bot_channel_sems: dict[str, asyncio.Semaphore] = {}
        self._workspace_sems: dict[str, asyncio.Semaphore] = {}

    def _stream_key(self, subject: str) -> str:
        return f"{self._prefix}:{subject}"

    # --- Per-tenant ingest fairness (ADR-W2-D8) --------------------------

    _FAIRNESS_OVERFLOW_KEY: Final[str] = "_overflow"
    _FAIRNESS_NO_TENANT_KEY: Final[str] = "_no_tenant"

    @staticmethod
    def _fairness_keys(data: dict[Any, Any]) -> tuple[str, str]:
        """Extract the ingest-fairness keys: (bot+channel, workspace).

        NOT keyed by tenant (2026-06-13 owner spec) — fairness is enforced at
        the finer grain so one bot can't starve sibling bots of the same
        workspace. ``bot_channel`` = ``f"{record_bot_id}:{channel_type}"``,
        ``workspace`` = ``workspace_id``. Any parse failure / missing field →
        the shared no-key fallback (messages without these — e.g.
        ``registry_changed`` — don't need ingest fairness).
        """
        raw = data.get(b"payload") or data.get("payload")
        if raw is None:
            return (RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY,
                    RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY)
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            p = json_loads(raw)
            bot = p.get("record_bot_id") or p.get("bot_id")
            ch = p.get("channel_type") or ""
            ws = p.get("workspace_id")
        except (ValueError, TypeError, AttributeError):
            return (RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY,
                    RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY)
        bc = f"{bot}:{ch}" if bot else RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY
        wsk = str(ws) if ws else RedisStreamsEventBus._FAIRNESS_NO_TENANT_KEY
        return (bc, wsk)

    def _fairness_semaphore(
        self, registry: dict[str, asyncio.Semaphore], key: str, limit: int,
    ) -> asyncio.Semaphore:
        """Get-or-create a bounded-registry fairness semaphore for ``key``.

        Once the registry holds DEFAULT_BUS_TENANT_SEM_MAX distinct keys,
        further keys share one overflow semaphore so the dict can never grow
        without limit.
        """
        sem = registry.get(key)
        if sem is not None:
            return sem
        if len(registry) >= DEFAULT_BUS_TENANT_SEM_MAX:
            key = self._FAIRNESS_OVERFLOW_KEY
            sem = registry.get(key)
            if sem is not None:
                return sem
        sem = asyncio.Semaphore(max(1, limit))
        registry[key] = sem
        return sem

    # --- Transactional inbox (ADR-W1-D8b) --------------------------------

    async def _inbox_seen(self, subscriber_id: str, msg_id: str) -> bool:
        """True iff the (subscriber_id, msg_id) mark is committed.

        Fails OPEN toward re-processing: a DB error returns ``False``
        so the handler re-runs (at-least-once) — the failure direction
        must never be "skip + XACK" (that would re-introduce the drop).
        """
        if self._session_factory is None:
            return False
        try:
            async with self._session_factory() as session:
                row = await session.execute(
                    sa_text(_INBOX_SEEN_SQL),
                    {"subscriber_id": subscriber_id, "msg_id": msg_id},
                )
                return row.first() is not None
        except (SQLAlchemyError, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "event_inbox_check_failed",
                subscriber_id=subscriber_id, msg_id=msg_id,
                error=str(exc), error_type=type(exc).__name__,
            )
            return False

    async def _mark_processed(self, subscriber_id: str, msg_id: str) -> None:
        """Write the inbox mark in a bus-owned transaction.

        Used for handlers with the legacy 1-arg signature (no
        ``inbox_tx`` hook). Such handlers must be idempotent for
        side-effects outside this transaction — documented contract.
        Errors propagate: a failed mark means NO XACK, so the message
        redelivers and the (idempotent) handler runs again.

        A conflict (rowcount 0) means a concurrent duplicate delivery
        already marked the message. The handler's side-effects ran in
        their own committed transaction and cannot be undone here, so
        this only logs — exactness for non-DB side-effects rides on the
        handler-idempotency contract; handlers needing atomic
        exactly-once declare the ``inbox_tx`` hook instead.
        """
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    sa_text(_INBOX_MARK_SQL),
                    {"subscriber_id": subscriber_id, "msg_id": msg_id},
                )
        if result.rowcount == 0:
            logger.warning(
                "event_inbox_mark_duplicate",
                subscriber_id=subscriber_id, msg_id=msg_id,
            )

    def _make_inbox_tx(
        self, subscriber_id: str, msg_id: str | None,
    ) -> Callable[[AsyncSession], Awaitable[None]]:
        """Build the hook a hook-aware handler awaits on ITS session.

        The INSERT joins the handler's open transaction, so mark and
        side-effects commit or roll back together (atomic
        process-then-mark). A conflict (rowcount 0 — concurrent
        duplicate delivery already committed the mark) raises
        :class:`InboxDuplicateError`, rolling the handler's transaction
        back so the duplicate cannot double-apply (PK-conflict-aborts
        idempotent-consumer pattern). No-op when the message carries no
        Msg-Id.
        """
        async def _inbox_tx(session: AsyncSession) -> None:
            if msg_id is None:
                return
            result = await session.execute(
                sa_text(_INBOX_MARK_SQL),
                {"subscriber_id": subscriber_id, "msg_id": msg_id},
            )
            if result.rowcount == 0:
                raise InboxDuplicateError(
                    f"already processed: subscriber={subscriber_id} "
                    f"msg_id={msg_id}",
                )
        return _inbox_tx

    async def ensure_streams(self) -> None:
        """No-op — Redis Streams auto-create on XADD."""

    async def publish(
        self,
        event: DomainEvent,
        *,
        headers: dict[str, str] | None = None,
        msg_id: str | None = None,
    ) -> str:
        """Publish DomainEvent tới Redis Stream.

        Returns the Redis Stream entry id assigned by XADD. Raises
        :class:`BusError` when XADD fails or returns falsy — callers
        (outbox publisher) MUST treat this as "row stays pending" so a
        Redis blip cannot mark an outbox row processed while no entry
        is on the stream.
        """
        key = self._stream_key(event.event_type)
        payload = json_dumps(event.to_dict(), default=str)
        data: dict[str, str] = {"payload": payload}
        if headers:
            data["headers"] = json_dumps(headers)
        if msg_id:
            data["msg_id"] = msg_id
        return await self._xadd_or_raise(key, data)

    async def publish_raw(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
        msg_id: str | None = None,
    ) -> str:
        """Publish raw bytes payload (dùng bởi outbox_publisher).

        Returns the Redis Stream entry id assigned by XADD. See
        :meth:`publish` for the durability contract — the entry id is
        threaded back to the outbox row so operators can join an outbox
        record to the actual stream entry for forensic replay.
        """
        key = self._stream_key(subject)
        data: dict[str, str | bytes] = {"payload": payload}
        if headers:
            data["headers"] = json_dumps(headers)
        if msg_id:
            data["msg_id"] = msg_id
        return await self._xadd_or_raise(key, data)

    async def _xadd_or_raise(
        self,
        key: str,
        data: dict[str, str | bytes],
    ) -> str:
        """XADD wrapper enforcing the publish-durability contract.

        Three failure modes are reified as :class:`BusError` so the
        outbox publisher can roll back its lock-tx and leave the row
        pending:

        * Transport / protocol error from redis-py (``RedisError``,
          ``OSError``, ``asyncio.TimeoutError``).
        * Falsy return value from ``XADD`` (defence in depth — current
          redis-py versions raise on failure, but a future version
          returning ``None`` for a refused write would otherwise be
          silently swallowed).
        """
        try:
            entry_id = await self._redis.xadd(  # type: ignore[arg-type]
                key, data, maxlen=DEFAULT_STREAM_MAXLEN, approximate=True,
            )
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            raise BusError(
                f"xadd transport failure on stream={key}: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        if not entry_id:
            raise BusError(f"xadd returned empty entry_id on stream={key}")
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode("utf-8")
        logger.debug("redis_streams_xadd_ok", stream=key, entry_id=entry_id)
        return entry_id

    async def subscribe(
        self,
        subject: str,
        handler: Callable[..., Awaitable[None]],
        *,
        durable_name: str = "default",
        queue_group: str | None = None,
        concurrency: int = DEFAULT_BUS_HANDLER_CONCURRENCY,
    ) -> _StreamSubscription:
        """Start consumer loop via XREADGROUP.

        Processes up to ``concurrency`` messages in parallel per batch via
        :class:`asyncio.Semaphore`. Document ingest is I/O-bound (HTTP fetch
        + embed API + DB INSERT — most of the 5-30s/doc is network wait),
        so a single subscribe loop drives 5x throughput when the semaphore
        opens 5 slots. Each dispatched task is fire-isolated: a handler
        exception logs but never propagates to siblings or the outer loop.

        Set ``concurrency=1`` to force strict sequential processing (e.g.
        for ordering guarantees in a partitioned stream).
        """
        key = self._stream_key(subject)
        group = f"{queue_group or 'default'}:{durable_name}"
        consumer_name = f"{group}:{uuid4().hex[:8]}"
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        try:
            await self._redis.xgroup_create(key, group, id="0", mkstream=True)
        except (ResponseError, RedisError):
            pass  # group already exists (BUSYGROUP) or transient connectivity

        # 1 message × N independent subscribers — each (subject, group)
        # pair owns its own inbox mark.
        subscriber_id = f"{subject}:{group}"
        # Hook-aware handlers declare the keyword-only ``inbox_tx``
        # param and write the mark inside their own transaction.
        handler_takes_inbox_tx = (
            _INBOX_TX_PARAM in inspect.signature(handler).parameters
        )

        async def _dispatch_one(msg_id_bytes: bytes, data: dict[Any, Any]) -> None:
            """Decode, process, mark inbox, then XACK (ADR-W1-D8b).

            Process-then-mark ordering: the Redis ``SET NX`` dedup key
            is only a fast-path hint; the ``event_inbox`` row — written
            AFTER handler success, atomically with its DB side-effects —
            is the source of truth. XACK fires only after that commit,
            so a crash anywhere leaves the message redeliverable and a
            redelivery of committed work is skipped from the inbox hit.

            Fairness (ADR-W2-D8): the per-tenant semaphore is acquired
            OUTSIDE the global one, so a noisy tenant whose tasks are blocked
            on its own cap never holds a global slot — other tenants always
            reach the ≥(global − per_tenant) remaining slots. Inner ``sem``
            keeps the worker's total concurrency bounded. Errors logged +
            isolated so the consumer loop stays alive.
            """
            _bc_key, _ws_key = self._fairness_keys(data)
            # Workspace cap (10, outer) then bot+channel cap (5, inner) then the
            # global handler budget. A noisy bot blocks on its own 5-slot cap
            # without holding a workspace/global slot; a noisy workspace blocks
            # on its 10-slot cap without starving other workspaces.
            ws_sem = self._fairness_semaphore(
                self._workspace_sems, _ws_key, DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE)
            bc_sem = self._fairness_semaphore(
                self._bot_channel_sems, _bc_key, DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL)
            async with ws_sem, bc_sem, sem:
                try:
                    payload_raw = data.get(b"payload") or data.get("payload", b"{}")
                    if isinstance(payload_raw, bytes):
                        payload_raw = payload_raw.decode("utf-8")
                    payload = json_loads(payload_raw)

                    # Msg-Id header carries the stable outbox UUID.
                    stream_msg_id = data.get(b"msg_id") or data.get("msg_id")
                    if isinstance(stream_msg_id, bytes):
                        stream_msg_id = stream_msg_id.decode("utf-8")
                    if stream_msg_id:
                        dedup_key = _OUTBOX_DEDUP_PREFIX + str(stream_msg_id)
                        was_new = await self._redis.set(
                            dedup_key, "1",
                            ex=DEFAULT_OUTBOX_DEDUP_TTL_S, nx=True,
                        )
                        # Hint-hit alone may NOT skip — the hint is set
                        # before the handler, so it also fires for a
                        # message whose previous attempt FAILED. Only a
                        # committed inbox row proves completed work.
                        if not was_new and await self._inbox_seen(
                            subscriber_id, str(stream_msg_id),
                        ):
                            logger.info(
                                "redis_streams_dedup_skip",
                                stream=key, msg_id=stream_msg_id,
                            )
                            await self._redis.xack(key, group, msg_id_bytes)
                            return

                    class _Event:
                        def __init__(self, p: dict[str, Any]) -> None:
                            self.payload = p
                            self.subject = subject

                    if handler_takes_inbox_tx:
                        # Handler owns the mark: it awaits the hook on
                        # its session inside its own side-effect tx.
                        await handler(
                            _Event(payload),
                            inbox_tx=self._make_inbox_tx(
                                subscriber_id,
                                str(stream_msg_id) if stream_msg_id else None,
                            ),
                        )
                    else:
                        await handler(_Event(payload))
                        if stream_msg_id:
                            await self._mark_processed(
                                subscriber_id, str(stream_msg_id),
                            )
                    # XACK ONLY after the mark committed. Crash between
                    # commit and XACK → redelivery → inbox-hit → skip.
                    await self._redis.xack(key, group, msg_id_bytes)
                except InboxDuplicateError:
                    # Concurrent duplicate lost the mark race — its
                    # side-effect tx rolled back; the winning delivery
                    # committed the row, so this entry is safe to XACK.
                    logger.info(
                        "redis_streams_inbox_duplicate_skip",
                        stream=key, msg_id=msg_id_bytes,
                    )
                    await self._redis.xack(key, group, msg_id_bytes)
                except Exception:  # noqa: BLE001 — handler is user code; isolate any failure type so the consumer loop stays alive
                    logger.exception(
                        "redis_streams_handler_error", msg_id=msg_id_bytes,
                    )

        async def _ensure_group_exists() -> None:
            """Re-create the consumer group when Redis returned NOGROUP.

            This is the recovery path for the operational scenario where
            Redis is FLUSHDB'd, restarted without persistence, or the
            stream/group is administratively deleted (rare but observed
            in dev environments). Without this, the loop spins on
            NOGROUP error forever — verified Bug #11 (260525).
            """
            try:
                await self._redis.xgroup_create(
                    key, group, id="0", mkstream=True,
                )
                logger.warning(
                    "redis_streams_group_recreated",
                    stream=key, group=group, consumer=consumer_name,
                )
            except (ResponseError, RedisError) as exc:
                # BUSYGROUP = race with another consumer that already
                # recreated it. Treat as success.
                if "BUSYGROUP" in str(exc):
                    return
                # Anything else — log and let the outer loop retry.
                logger.error(
                    "redis_streams_group_recreate_failed",
                    stream=key, group=group, error=str(exc),
                )

        async def _loop() -> None:
            _recovery_counter = 0
            while True:
                try:
                    # Periodically recover stale messages from crashed consumers
                    _recovery_counter += 1
                    if _recovery_counter % 12 == 0:  # every ~60s (12 * 5s block)
                        await self.recover_pending_messages(
                            stream=key, group=group, consumer=consumer_name,
                            dispatch=_dispatch_one,
                        )
                    messages = await self._redis.xreadgroup(
                        group, consumer_name,
                        {key: ">"},
                        count=REDIS_XREAD_COUNT,
                        block=REDIS_XREAD_BLOCK_MS,
                    )
                    # Fan-out dispatch — gather() runs up to ``concurrency``
                    # handlers in parallel; the rest queue on the semaphore
                    # and wake as slots free. ``return_exceptions=True`` so
                    # one handler's transient programmer bug cannot kill
                    # the batch — each task already has its own try/except.
                    tasks: list[asyncio.Task[None]] = []
                    for _stream, msgs in messages or []:
                        for msg_id_bytes, data in msgs:
                            tasks.append(
                                asyncio.create_task(_dispatch_one(msg_id_bytes, data)),
                            )
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    return
                except ResponseError as exc:
                    # 260525 Bug #11 — NOGROUP auto-recover. Redis raises
                    # ``NOGROUP`` when the stream key or consumer group is
                    # missing (FLUSHDB, manual delete, no-persistence
                    # restart). Pre-fix the loop spammed the same error
                    # forever; now we self-heal by recreating the group
                    # and resuming the loop. Universal — applies to every
                    # tenant sharing the same Redis instance.
                    if "NOGROUP" in str(exc):
                        logger.warning(
                            "redis_streams_nogroup_recovering",
                            stream=key, group=group,
                        )
                        await _ensure_group_exists()
                        await asyncio.sleep(1)
                        continue
                    logger.exception("redis_streams_read_error")
                    await asyncio.sleep(1)
                except Exception:  # noqa: BLE001 — background consumer loop wrapper; log + sleep + continue regardless of failure type
                    logger.exception("redis_streams_read_error")
                    await asyncio.sleep(1)

        task = asyncio.create_task(_loop())
        logger.info(
            "redis_streams_subscribed",
            stream=key, group=group, concurrency=int(concurrency),
        )
        return _StreamSubscription(task)

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout_s: float = 5.0,
    ) -> bytes:
        """Request-reply not supported via Redis Streams. Raise."""
        raise NotImplementedError("request-reply not supported via Redis Streams")

    async def close(self) -> None:
        """No-op — Redis client lifecycle managed by bootstrap."""

    async def recover_pending_messages(
        self,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int = 30_000,
        count: int = 10,
        dispatch: Callable[[bytes, dict[Any, Any]], Awaitable[None]] | None = None,
    ) -> int:
        """Claim messages from crashed consumers via XPENDING/XCLAIM.

        When ``dispatch`` is supplied the reclaimed messages are re-driven
        through it (the consumer loop passes its own ``_dispatch_one``).
        Without re-drive an XCLAIMed message only changes owner: it idles
        in this consumer's PEL, gets re-claimed each pass, and after
        ``DEFAULT_BUS_DLQ_MAX_DELIVERIES`` dead-letters WITHOUT ever running
        the handler — a transient-failed job (embed 429, owner crash) rots
        to DLQ unprocessed. ``_dispatch_one`` owns XACK + inbox-dedup, so a
        job that DID commit on its prior attempt is skipped via its inbox
        row; only genuinely-unprocessed work re-runs.
        """
        try:
            pending = await self._redis.xpending_range(
                stream, group,
                min="-", max="+",
                count=count,
                idle=min_idle_ms,
            )
        except (RedisError, OSError, asyncio.TimeoutError):
            return 0

        if not pending:
            return 0

        # Separate poison messages (delivery count over threshold) from
        # claimable ones. Poison entries are persisted to the
        # ``{stream}:dlq`` parking-lot stream (admin-replayable) BEFORE
        # being XACKed — never log-and-drop.
        claimable_ids: list[bytes] = []
        for entry in pending:
            if entry.get("times_delivered", 0) > DEFAULT_BUS_DLQ_MAX_DELIVERIES:
                await self._dead_letter(stream, group, entry)
            else:
                claimable_ids.append(entry["message_id"])

        if not claimable_ids:
            return 0

        try:
            claimed = await self._redis.xclaim(
                stream, group, consumer,
                min_idle_time=min_idle_ms,
                message_ids=claimable_ids,
            )
            if claimed:
                logger.info("redis_streams_claimed_pending", count=len(claimed), stream=stream)
                if dispatch is not None:
                    # Re-drive each reclaimed message through the handler.
                    # Same (msg_id, fields) shape as an xreadgroup entry, so
                    # _dispatch_one handles decode + inbox-dedup + XACK. Isolate
                    # per message (gather return_exceptions) — one poison payload
                    # must not abort recovery of its siblings.
                    _redrive = [
                        asyncio.create_task(dispatch(_mid, dict(_fields)))
                        for _mid, _fields in claimed
                    ]
                    await asyncio.gather(*_redrive, return_exceptions=True)
            return len(claimed) if claimed else 0
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("redis_streams_claim_failed", error=str(exc))
            return 0

    async def _dead_letter(
        self, stream: str, group: str, entry: dict[str, Any],
    ) -> None:
        """Persist a poison PEL entry to ``{stream}:dlq`` then XACK it.

        Ordering is XADD-then-XACK: a Redis blip during the DLQ write
        leaves the entry in the PEL (retried next recovery pass) — the
        message can never be ACKed without a persisted copy. The DLQ
        entry preserves the original fields (``payload`` / ``headers`` /
        ``msg_id``) so an admin replay = XADD the fields back onto the
        main stream; provenance travels in ``dlq_*`` meta fields, which
        ``_dispatch_one`` ignores.
        """
        entry_id = entry["message_id"]
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode("utf-8")
        dlq_stream = f"{stream}:dlq"
        times_delivered = entry.get("times_delivered", 0)
        try:
            rows = await self._redis.xrange(stream, min=entry_id, max=entry_id)
            if rows:
                _eid, fields = rows[0]
                data: dict[Any, Any] = dict(fields)
                data["dlq_source_stream"] = stream
                data["dlq_source_entry"] = entry_id
                data["dlq_times_delivered"] = str(times_delivered)
                await self._redis.xadd(
                    dlq_stream, data,
                    maxlen=DEFAULT_STREAM_MAXLEN, approximate=True,
                )
                logger.error(
                    "redis_streams_dead_letter",
                    msg_id=entry_id, stream=stream, dlq_stream=dlq_stream,
                    times_delivered=times_delivered,
                )
            else:
                # Entry already trimmed off the stream — nothing left to
                # persist; XACK clears the dangling PEL reference.
                logger.error(
                    "redis_streams_dead_letter_lost",
                    msg_id=entry_id, stream=stream,
                    times_delivered=times_delivered,
                )
            await self._redis.xack(stream, group, entry_id)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            # Leave the entry in the PEL — next recovery pass retries
            # the DLQ move. Failure direction is "keep", never "drop".
            logger.warning(
                "redis_streams_dead_letter_failed",
                msg_id=entry_id, stream=stream, error=str(exc),
            )

    async def health_check(self) -> bool:
        try:
            return await self._redis.ping()
        except (RedisError, OSError, asyncio.TimeoutError):
            return False
