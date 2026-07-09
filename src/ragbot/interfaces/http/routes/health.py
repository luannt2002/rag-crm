"""Single health endpoint — liveness + readiness merged.

Returns HTTP 200 always (so orchestrators don't restart the container on
transient DB/Redis hiccups). The `status` field in the body signals health:
- "ok"       → all deps respond
- "degraded" → at least one dep is down but app is still usable
- "down"     → deps check raised (extremely rare; prefer degraded)

Monitoring (Prometheus, LB health-check) should route on the `status` field
or on the per-dep `dependencies.*` values, not on HTTP status.

`pool_stats` exposes connection-pool saturation per driver — useful for
catching "all connections busy" before it manifests as latency spikes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ragbot.interfaces.http.schemas.common_schema import HealthResponse
from ragbot.shared.perf import timer

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


def _db_pool_stats(engine: AsyncEngine) -> dict[str, int]:
    """SQLAlchemy QueuePool exposes in-use / idle / overflow counts.

    Equivalent to Go's `db.Stats()`:
      pool_size      → SetMaxIdleConns
      pool_size + max_overflow → SetMaxOpenConns
    """
    try:
        pool = engine.pool
        return {
            "db_in_use": pool.checkedout(),
            "db_idle": pool.checkedin(),
            "db_overflow": pool.overflow(),
            "db_size": pool.size(),
        }
    except Exception:  # noqa: BLE001 — pool attr may differ on non-QueuePool
        return {}


def _redis_pool_stats(client: Any) -> dict[str, int]:
    """Redis pool saturation via redis-py private attrs.

    Intentional DIP violation: redis-py doesn't expose pool-stats as a public
    API, and operator-facing pool visibility is worth the brittleness. On
    redis-py upgrade the attribute layout may change — when that happens we
    log at DEBUG so the regression is observable instead of silently
    returning {}.
    """
    try:
        pool = client.connection_pool
        created_list = getattr(pool, "_created_connections", None)
        created = len(created_list) if created_list is not None else 0

        avail = getattr(pool, "_available_connections", None)
        if avail is None:
            available = 0
        elif hasattr(avail, "qsize"):
            available = avail.qsize()
        else:
            available = len(avail)

        in_use = max(created - available, 0)
        return {
            "redis_in_use": in_use,
            "redis_available": available,
            "redis_max": int(getattr(pool, "max_connections", 0) or 0),
        }
    except Exception as exc:  # noqa: BLE001
        # redis-py internals shifted — keep /health responsive, make it visible.
        logger.debug("redis_pool_stats_failed", error=str(exc))
        return {}


async def _check_postgres(container: Any) -> str:
    """Ping Postgres with ``SELECT 1``. Returns ``"ok"`` or ``"down"``."""
    try:
        sf = container.session_factory()
        async with sf() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 — health probe must survive any DB failure type
        return "down"


async def _check_redis(redis_client: Any) -> str:
    """Ping Redis with ``PING``. Returns ``"ok"`` or ``"down"``."""
    try:
        pong = await redis_client.ping()
        return "ok" if pong else "down"
    except Exception:  # noqa: BLE001 — health probe must survive any Redis failure type
        return "down"


def _check_workers(request: Request, settings: Any) -> str | None:
    """Liveness of the embedded worker tasks.

    Returns ``None`` when this API instance does not run embedded workers
    (``embed_workers_enabled`` is False → workers run as separate processes);
    the caller then omits the dep so an API-only node is never flagged
    degraded for workers it does not own. When embedded workers ARE enabled:
    ``"ok"`` while every supervised task is still running, ``"down"`` when any
    task has completed — a supervised worker exits only on crash (see
    ``embedded_workers._supervise``: it logs + returns, never auto-restarts),
    so a finished task is a dead consumer, not a normal state.
    """
    if not settings.app.embed_workers_enabled:
        return None
    tasks = getattr(request.app.state, "embedded_worker_tasks", None) or []
    if not tasks:
        # Enabled but nothing spawned = a startup misconfiguration.
        logger.warning("health_embedded_workers_absent")
        return "down"
    dead = [t.get_name() for t in tasks if t.done()]
    if dead:
        logger.warning("health_embedded_workers_down", dead=dead)
        return "down"
    return "ok"


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness + readiness in one call.

    Pings Postgres (SELECT 1) and Redis (PING) in parallel
    (CLAUDE.md Async Rule 1 — independent probes), reports pool stats.
    Always returns HTTP 200 — status field in body tells the truth.
    """
    container = request.app.state.container
    settings = request.app.state.settings

    deps: dict[str, str] = {}
    pool_stats: dict[str, int] = {}

    async with timer("health_probe"):
        # Resolve Redis client up-front so the gather can include its probe.
        redis_client = None
        try:
            redis_client = container.redis_client()
        except Exception:  # noqa: BLE001 — DI may fail in degraded modes
            redis_client = None

        if redis_client is not None:
            pg_status, redis_status = await asyncio.gather(
                _check_postgres(container),
                _check_redis(redis_client),
            )
            deps["postgres"] = pg_status
            deps["redis"] = redis_status
        else:
            deps["postgres"] = await _check_postgres(container)
            deps["redis"] = "down"

        # Pool stats are sync best-effort; collect after the probe gather.
        try:
            engine: AsyncEngine = container.db_engine()
            pool_stats.update(_db_pool_stats(engine))
        except Exception:  # noqa: BLE001 — pool stats best-effort; missing values must not 500 health
            pass

        if redis_client is not None:
            pool_stats.update(_redis_pool_stats(redis_client))

    # Embedded-worker liveness — only added when this instance runs them,
    # so an API-only node is never marked degraded for external workers.
    worker_status = _check_workers(request, settings)
    if worker_status is not None:
        deps["workers"] = worker_status

    overall: str = "ok" if all(v == "ok" for v in deps.values()) else "degraded"
    return HealthResponse(
        status=overall,  # type: ignore[arg-type]
        version=settings.app.version,
        dependencies=deps,
        pool_stats=pool_stats,
        timestamp=datetime.now(tz=timezone.utc),
    )


__all__ = ["router"]
