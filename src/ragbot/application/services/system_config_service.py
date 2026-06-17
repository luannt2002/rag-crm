"""Dịch vụ cấu hình hệ thống — lưu DB, cache Redis.

Giá trị cấu hình lưu trong bảng `system_config` (key/value). Cache Redis với TTL 5 phút
để đọc nhanh. Khi cập nhật, cache bị xóa + kích hoạt side effect (ví dụ: cắt bớt
lịch sử chat khi max_history giảm).
"""

from __future__ import annotations

import json
import random
from typing import Any
from uuid import uuid4

import orjson
import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.shared.constants import (
    DEFAULT_SERVICE_CACHE_TTL_S,
    DEFAULT_TTL_JITTER_RATIO,
    SUBJECT_SYSTEM_CONFIG_CHANGED,
    WORKSPACE_SYSTEM_SLUG,
)

logger = structlog.get_logger(__name__)

CACHE_PREFIX = "ragbot:sysconfig:"
CACHE_TTL = DEFAULT_SERVICE_CACHE_TTL_S


def _jittered_ttl(ttl: int = CACHE_TTL, *, ratio: float = DEFAULT_TTL_JITTER_RATIO) -> int:
    """Return TTL with ±``ratio`` uniform jitter applied.

    Spreads expiry of cache entries written during the same burst so the
    upstream DB doesn't see a synchronised refresh stampede. Minimum
    returned TTL is 1s (clamps weird ratio configurations).
    """
    if ttl <= 0 or ratio <= 0:
        return max(ttl, 1)
    spread = int(ttl * ratio)
    if spread <= 0:
        return ttl
    return max(ttl + random.randint(-spread, spread), 1)


class SystemConfigService:
    """Đọc/ghi cấu hình hệ thống với lớp cache Redis."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client,
        outbox_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Khởi tạo service với session factory và Redis client.
        @param session_factory: factory tạo async session kết nối DB
        @param redis_client: client kết nối Redis để cache
        @param outbox_session_factory: optional factory used to enqueue
            ``system_config.changed.v1`` outbox rows so peer replicas
            invalidate their local Redis cache. ``None`` keeps the
            single-node behaviour (local-cache-only invalidation) for
            unit tests / one-shot scripts that don't run a publisher.
        """
        self._sf = session_factory
        self._redis = redis_client
        self._outbox_sf = outbox_session_factory

    async def get(self, key: str, default=None):
        """Đọc cấu hình: ưu tiên Redis cache → fallback DB → giá trị mặc định.
        @param key: khóa cấu hình cần đọc
        @param default: giá trị mặc định nếu không tìm thấy
        @return: giá trị cấu hình hoặc default
        """
        cache_key = CACHE_PREFIX + key
        raw = await self._redis.get(cache_key)
        if raw is not None:
            return json.loads(raw)

        async with self._sf() as session:
            row = (await session.execute(
                text("SELECT value FROM system_config WHERE key = :k"),
                {"k": key},
            )).fetchone()

        if row is None:
            return default

        val = row[0]  # JSONB → Python object
        await self._redis.set(cache_key, json.dumps(val), ex=_jittered_ttl())
        return val

    async def get_int(self, key: str, default: int = 0) -> int:
        """Đọc cấu hình và ép kiểu sang số nguyên.
        @param key: khóa cấu hình cần đọc
        @param default: giá trị mặc định nếu không tìm thấy
        @return: giá trị cấu hình dạng int
        """
        val = await self.get(key, default)
        return int(val) if val is not None else default

    async def get_bool(self, key: str, default: bool = False) -> bool:
        """Đọc cấu hình và ép kiểu sang boolean.
        @param key: khóa cấu hình cần đọc
        @param default: giá trị mặc định nếu không tìm thấy
        @return: giá trị cấu hình dạng bool
        """
        val = await self.get(key, default)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)

    async def get_float(self, key: str, default: float = 0.0) -> float:
        """Đọc cấu hình và ép kiểu sang số thực.
        @param key: khóa cấu hình cần đọc
        @param default: giá trị mặc định nếu không tìm thấy
        @return: giá trị cấu hình dạng float
        """
        val = await self.get(key, default)
        return float(val) if val is not None else default

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Batch-fetch multiple config keys in a single round-trip.

        Reads all ``keys`` via one Redis ``MGET`` for hits and a single
        ``SELECT key, value FROM system_config WHERE key = ANY(:keys)``
        for misses. Returns a ``{key: value}`` dict where missing keys
        are absent (caller applies its own default per key).

        Per-call cost: 1 Redis round-trip + at most 1 DB round-trip,
        regardless of how many keys are requested — replaces N
        sequential ``get()`` calls (each = 1 Redis hit OR 1 Redis miss
        + 1 DB hit + 1 Redis set).
        """
        if not keys:
            return {}
        # De-dup while preserving order so caller can rely on the same
        # key landing once in the result map.
        unique_keys: list[str] = []
        seen: set[str] = set()
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)

        cache_keys = [CACHE_PREFIX + k for k in unique_keys]
        try:
            raw_values = await self._redis.mget(cache_keys)
        except RedisError:
            # Treat a Redis blip as an all-miss — fall through to DB.
            raw_values = [None] * len(unique_keys)

        out: dict[str, Any] = {}
        missing: list[str] = []
        for key, raw in zip(unique_keys, raw_values):
            if raw is not None:
                try:
                    out[key] = json.loads(raw)
                except (TypeError, ValueError):
                    # Corrupt cache entry — re-fetch from DB.
                    missing.append(key)
            else:
                missing.append(key)

        if missing:
            async with self._sf() as session:
                rows = (await session.execute(
                    text("SELECT key, value FROM system_config WHERE key = ANY(:keys)"),
                    {"keys": missing},
                )).fetchall()
            # Refresh Redis for the rows we fetched so the next call
            # short-circuits without hitting the DB again.
            for row in rows:
                k = row[0]
                v = row[1]
                out[k] = v
                try:
                    await self._redis.set(CACHE_PREFIX + k, json.dumps(v), ex=_jittered_ttl())
                except RedisError:
                    # Best-effort cache fill; main flow already has the value.
                    pass
        return out

    async def get_all(self) -> list[dict]:
        """Lấy toàn bộ danh sách cấu hình từ DB.
        @return: danh sách dict chứa key, value, value_type, description, updated_at
        """
        async with self._sf() as session:
            rows = (await session.execute(
                text("SELECT key, value, value_type, description, updated_at FROM system_config ORDER BY key"),
            )).fetchall()

        return [
            {
                "key": r[0], "value": r[1], "value_type": r[2],
                "description": r[3],
                "updated_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]

    async def set(self, key: str, value, *, description: str | None = None) -> dict:
        """Cập nhật cấu hình vào DB, xóa cache, kích hoạt side effect.
        @param key: khóa cấu hình cần cập nhật
        @param value: giá trị mới
        @param description: mô tả cấu hình (tùy chọn)
        @return: dict chứa key, old, new, trimmed_rooms
        """
        old_value = await self.get(key)

        async with self._sf() as session:
            if description is not None:
                await session.execute(
                    text("""
                        INSERT INTO system_config (key, value, description, updated_at)
                        VALUES (:k, CAST(:v AS jsonb), :desc, now())
                        ON CONFLICT (key) DO UPDATE
                        SET value = CAST(:v AS jsonb), description = :desc, updated_at = now()
                    """),
                    {"k": key, "v": json.dumps(value), "desc": description},
                )
            else:
                await session.execute(
                    text("""
                        INSERT INTO system_config (key, value, updated_at)
                        VALUES (:k, CAST(:v AS jsonb), now())
                        ON CONFLICT (key) DO UPDATE
                        SET value = CAST(:v AS jsonb), updated_at = now()
                    """),
                    {"k": key, "v": json.dumps(value)},
                )
            await session.commit()

        # Invalidate LOCAL cache (this replica only).
        await self._redis.delete(CACHE_PREFIX + key)

        # Bug 2 (P0) — emit cross-replica invalidation event so peer
        # replicas drop their stale Redis copies as soon as the outbox
        # publisher dispatches the row. Without this each replica keeps
        # the previous value for up to ``CACHE_TTL`` (5 min) → divergent
        # config behaviour across the fleet for that window. ``None``
        # outbox factory keeps single-node test usage working.
        if self._outbox_sf is not None:
            await self._emit_changed_event(key)

        # Side effect: trim chat histories if max_history decreased
        trimmed = 0
        if key == "chat_max_history":
            new_max = int(value)
            if old_value is not None and int(old_value) > new_max:
                trimmed = await self._trim_chat_histories(new_max)

        logger.info("system_config_updated", key=key, old=old_value, new=value, trimmed=trimmed)
        return {"key": key, "old": old_value, "new": value, "trimmed_rooms": trimmed}

    async def _emit_changed_event(self, key: str) -> None:
        """Insert a ``system_config.changed.v1`` outbox row.

        Bug 2 (P0) — local Redis ``DELETE`` only invalidates the current
        replica. The publisher fans the event out via Redis Streams; the
        ``ai_config_listener`` worker on every replica drops the same
        cache key and the next ``get(key)`` falls through to DB.

        Best-effort: if the insert fails the local cache is still
        invalidated, so the caller's UPDATE is not rolled back. We log
        and continue rather than blowing up the admin write path.
        """
        try:
            async with self._outbox_sf() as session:  # type: ignore[misc]
                await session.execute(
                    text(
                        """
                        INSERT INTO outbox (
                            id, subject, payload, headers, trace_id,
                            workspace_id, retry_count, status,
                            metadata_json, created_at
                        )
                        VALUES (
                            :id, :subject, :payload,
                            CAST(:headers AS jsonb), :trace_id,
                            :workspace_id, 0, 'pending',
                            CAST(:meta AS jsonb), now()
                        )
                        """,
                    ),
                    {
                        "id": uuid4(),
                        "subject": SUBJECT_SYSTEM_CONFIG_CHANGED,
                        "payload": orjson.dumps({"key": key}),
                        "headers": json.dumps(
                            {"event-type": SUBJECT_SYSTEM_CONFIG_CHANGED},
                        ),
                        "trace_id": "",
                        "workspace_id": WORKSPACE_SYSTEM_SLUG,
                        "meta": json.dumps(
                            {"event_type": SUBJECT_SYSTEM_CONFIG_CHANGED},
                        ),
                    },
                )
                await session.commit()
        except (SQLAlchemyError, OSError):
            # Diagnostic-only path: local cache already invalidated, so an
            # outbox INSERT failure won't roll back the caller's UPDATE.
            logger.exception("system_config_outbox_emit_failed", key=key)

    async def _trim_chat_histories(self, new_max: int) -> int:
        """Cắt bớt lịch sử chat của tất cả phòng về số lượng new_max tin nhắn.
        @param new_max: số tin nhắn tối đa giữ lại mỗi phòng
        @return: số phòng đã bị cắt bớt
        """
        async with self._sf() as session:
            # Find rooms that exceed the limit
            rooms = (await session.execute(text("""
                SELECT bot_id, channel_type, connect_id, count(*) as cnt
                FROM chat_histories
                GROUP BY bot_id, channel_type, connect_id
                HAVING count(*) > :max
            """), {"max": new_max})).fetchall()

            trimmed = 0
            for room in rooms:
                # Delete oldest messages, keep newest new_max
                await session.execute(text("""
                    DELETE FROM chat_histories
                    WHERE id IN (
                        SELECT id FROM chat_histories
                        WHERE bot_id = :bid AND channel_type = :ch AND connect_id = :cid
                        ORDER BY id ASC
                        LIMIT :del_count
                    )
                """), {
                    "bid": room[0], "ch": room[1], "cid": room[2],
                    "del_count": room[3] - new_max,
                })
                trimmed += 1

            await session.commit()

        logger.info("chat_histories_trimmed", rooms=trimmed, new_max=new_max)
        return trimmed


async def invalidate_local_cache(redis_client: Any, key: str) -> None:
    """Cross-replica handler — drop the local cache entry for ``key``.

    Bug 2 (P0) hook — invoked by ``ai_config_listener`` on every replica
    when a ``system_config.changed.v1`` outbox event arrives. Pure
    Redis ``DEL`` so the next ``get`` rebuilds from DB and converges
    every node onto the new value.
    """
    try:
        await redis_client.delete(CACHE_PREFIX + key)
    except (RedisError, OSError):
        # Best-effort: next TTL expiry covers a transient Redis blip.
        logger.warning("system_config_local_invalidate_failed", key=key)


__all__ = ["SystemConfigService", "invalidate_local_cache"]
