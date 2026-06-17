"""C.5 — Per-tenant monthly token meter (prompt + completion).

Redis hash keyed by ``tokens:tenant:{record_tenant_id}:{YYYY-MM}`` with
two fields ``prompt`` and ``completion``. Calendar-month buckets reset
automatically — a new month starts a new key, the previous month's hash
expires after one retention window so historic queries still work.

Cap semantics on ``tenants.monthly_token_cap``:

* ``NULL`` → no cap, every increment allowed, no warn ever.
* ``0`` → block immediately (admin can lock an abusive tenant).
* ``> 0`` → soft-warn at ``DEFAULT_TENANT_TOKEN_CAP_WARN_PERCENT`` of
  the cap, hard-cut at ``DEFAULT_TENANT_TOKEN_CAP_BLOCK_PERCENT``.

The meter is pure I/O — caller wires it into the LLM router boundary
(check before call, increment after). Prometheus warn counter goes
through ``tenant_token_warn_total``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.shared.constants import (
    DEFAULT_TENANT_TOKEN_CAP_BLOCK_PERCENT,
    DEFAULT_TENANT_TOKEN_CAP_WARN_PERCENT,
)

logger = structlog.get_logger(__name__)

_TOKEN_PREFIX = "tokens:tenant:"
# Retention: previous month's bucket survives long enough for cross-month
# reporting without growing unbounded. Two-month window in seconds.
_BUCKET_RETENTION_DAYS = 63
_SECONDS_PER_DAY = 86_400
_BUCKET_TTL_S = _BUCKET_RETENTION_DAYS * _SECONDS_PER_DAY


def _month_key(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


def _redis_key(record_tenant_id: UUID, month: str) -> str:
    return f"{_TOKEN_PREFIX}{record_tenant_id!s}:{month}"


@dataclass(slots=True, frozen=True)
class TokenCapDecision:
    """Outcome of a check_token_cap call."""

    allowed: bool
    reason: str | None  # "no_cap" | "blocked_zero" | "exceeded" | "ok"
    used: int  # current monthly total (prompt + completion)
    cap: int | None
    warn: bool  # True if usage crossed the warn threshold


class TenantTokenMeter:
    """Increment + read monthly per-tenant token usage."""

    def __init__(
        self,
        redis_client: Any,
        *,
        warn_percent: int = DEFAULT_TENANT_TOKEN_CAP_WARN_PERCENT,
        block_percent: int = DEFAULT_TENANT_TOKEN_CAP_BLOCK_PERCENT,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._redis = redis_client
        self._warn_percent = int(warn_percent)
        self._block_percent = int(block_percent)
        # Optional DB checkpoint. Redis evict mid-month would reset the
        # counter; ``tenant_token_usage`` lets ``get_monthly_usage``
        # restore. ``None`` keeps single-node / test usage Redis-only.
        self._sf = session_factory

    async def increment_tokens(
        self,
        record_tenant_id: UUID,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """HINCRBY both fields atomically; returns post-increment dict.

        Negative inputs are clamped to 0 to avoid corrupt counters when
        the upstream usage object is partial.

        Bug 4 (P1) — atomic HINCRBY×2 + EXPIRE via MULTI/EXEC pipeline
        when the client supports it; sequential fallback otherwise so
        unit-test fakes keep working.
        """
        prompt = max(int(prompt_tokens or 0), 0)
        completion = max(int(completion_tokens or 0), 0)
        month = _month_key(now)
        key = _redis_key(record_tenant_id, month)
        new_prompt = 0
        new_completion = 0
        try:
            pipe_factory = getattr(self._redis, "pipeline", None)
            used_pipeline = False
            if pipe_factory is not None:
                try:
                    pipe_ctx = pipe_factory(transaction=True)
                    async with pipe_ctx as pipe:
                        pipe.hincrby(key, "prompt", prompt)
                        pipe.hincrby(key, "completion", completion)
                        pipe.expire(key, _BUCKET_TTL_S)
                        results = await pipe.execute()
                    new_prompt = int(results[0] or 0) if results else 0
                    new_completion = int(results[1] or 0) if len(results) > 1 else 0
                    used_pipeline = True
                except (TypeError, AttributeError):
                    used_pipeline = False
            if not used_pipeline:
                new_prompt = await self._redis.hincrby(key, "prompt", prompt)
                new_completion = await self._redis.hincrby(key, "completion", completion)
                await self._redis.expire(key, _BUCKET_TTL_S)
        except Exception as exc:  # noqa: BLE001 — Redis driver raises a wide hierarchy (RedisError, ConnectionError, OSError, plus wrapped runtime errors from async pools); fail-open meter must absorb all and keep request flowing.
            logger.warning(
                "tenant_token_meter_redis_error",
                record_tenant_id=str(record_tenant_id),
                err=str(exc),
                error_type=type(exc).__name__,
            )
            return {"prompt": 0, "completion": 0, "total": 0}

        total = int(new_prompt or 0) + int(new_completion or 0)

        # Best-effort DB checkpoint. Cold-Redis replicas restore the
        # counter via ``_db_restore`` on next read; Redis remains source
        # of truth for the active bucket so failure is non-fatal.
        if self._sf is not None and total > 0:
            try:
                await self._db_checkpoint(
                    record_tenant_id, month,
                    int(new_prompt or 0), int(new_completion or 0),
                )
            except Exception:  # noqa: BLE001 — checkpoint best-effort
                logger.debug(
                    "tenant_token_meter_db_checkpoint_failed",
                    record_tenant_id=str(record_tenant_id),
                )

        return {
            "prompt": int(new_prompt or 0),
            "completion": int(new_completion or 0),
            "total": total,
        }

    async def _db_checkpoint(
        self,
        record_tenant_id: UUID,
        month: str,
        prompt_total: int,
        completion_total: int,
    ) -> None:
        """UPSERT cumulative counts into ``tenant_token_usage``.

        Used by Bug 4 (P1) Redis-evict recovery path. Schema column
        ``tenant_id`` is the UUID FK to ``tenants.id``.
        """
        if self._sf is None:
            return
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_token_usage
                        (tenant_id, period_yyyymm, prompt_tokens,
                         completion_tokens, updated_at)
                    VALUES (:tid, :pm, :pt, :ct, now())
                    ON CONFLICT (tenant_id, period_yyyymm) DO UPDATE
                    SET prompt_tokens = EXCLUDED.prompt_tokens,
                        completion_tokens = EXCLUDED.completion_tokens,
                        updated_at = now()
                    """,
                ),
                {
                    "tid": str(record_tenant_id),
                    "pm": month,
                    "pt": int(prompt_total),
                    "ct": int(completion_total),
                },
            )
            await session.commit()

    async def _db_restore(
        self,
        record_tenant_id: UUID,
        month: str,
    ) -> dict[str, int] | None:
        """Read the most recent DB checkpoint for the bucket.

        Returns ``None`` when no row exists or the DB is unavailable.
        """
        if self._sf is None:
            return None
        try:
            async with self._sf() as session:
                row = (await session.execute(
                    text(
                        """
                        SELECT prompt_tokens, completion_tokens
                        FROM tenant_token_usage
                        WHERE tenant_id = :tid AND period_yyyymm = :pm
                        """,
                    ),
                    {"tid": str(record_tenant_id), "pm": month},
                )).fetchone()
        except Exception:  # noqa: BLE001 — restore best-effort
            return None
        if row is None:
            return None
        return {"prompt": int(row[0] or 0), "completion": int(row[1] or 0)}

    async def get_monthly_usage(
        self,
        record_tenant_id: UUID,
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Read current month totals — never raises.

        Bug 4 (P1) — when Redis returns an empty hash (evicted under
        memory pressure / cold replica) and a DB checkpoint exists,
        restore from DB so the cap gate doesn't reset to zero.
        """
        month = _month_key(now)
        key = _redis_key(record_tenant_id, month)
        raw: Any = None
        try:
            raw = await self._redis.hgetall(key)
        except Exception as exc:  # noqa: BLE001 — Redis driver hierarchy is wide; fail-open meter absorbs all to avoid blocking the read path.
            logger.warning(
                "tenant_token_meter_read_error",
                record_tenant_id=str(record_tenant_id),
                err=str(exc),
                error_type=type(exc).__name__,
            )
            raw = None
        prompt = _coerce_int(raw, "prompt") if raw else 0
        completion = _coerce_int(raw, "completion") if raw else 0
        if (prompt + completion) == 0 and self._sf is not None:
            checkpoint = await self._db_restore(record_tenant_id, month)
            if checkpoint is not None:
                prompt = checkpoint["prompt"]
                completion = checkpoint["completion"]
                # Re-warm Redis so subsequent reads stay hot.
                try:
                    await self._redis.hset(
                        key,
                        mapping={
                            "prompt": str(prompt),
                            "completion": str(completion),
                        },
                    )
                    await self._redis.expire(key, _BUCKET_TTL_S)
                except Exception:  # noqa: BLE001 — best-effort warm-up
                    pass
        return {
            "prompt": prompt,
            "completion": completion,
            "total": prompt + completion,
        }

    async def check_token_cap(
        self,
        record_tenant_id: UUID,
        cap: int | None,
        *,
        now: datetime | None = None,
    ) -> TokenCapDecision:
        """Check whether the next call is allowed under the cap.

        Returns ``(allowed, reason)``-style decision but bundled with
        diagnostic fields so the caller can emit a warn metric without
        a second Redis read.
        """
        if cap is None:
            return TokenCapDecision(
                allowed=True, reason="no_cap", used=0, cap=None, warn=False,
            )
        if cap == 0:
            usage = await self.get_monthly_usage(record_tenant_id, now=now)
            return TokenCapDecision(
                allowed=False, reason="blocked_zero",
                used=usage["total"], cap=0, warn=True,
            )
        usage = await self.get_monthly_usage(record_tenant_id, now=now)
        used = usage["total"]
        block_at = (cap * self._block_percent) // 100
        warn_at = (cap * self._warn_percent) // 100
        if used >= block_at:
            return TokenCapDecision(
                allowed=False, reason="exceeded",
                used=used, cap=cap, warn=True,
            )
        return TokenCapDecision(
            allowed=True, reason="ok",
            used=used, cap=cap, warn=used >= warn_at,
        )


def _coerce_int(raw: dict, field: str) -> int:
    """Pull a counter value from a Redis hash, regardless of bytes/str keys."""
    val = raw.get(field) if isinstance(raw, dict) else None
    if val is None and isinstance(raw, dict):
        val = raw.get(field.encode("ascii"))
    if val is None:
        return 0
    if isinstance(val, bytes):
        try:
            return int(val.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "TenantTokenMeter",
    "TokenCapDecision",
]
