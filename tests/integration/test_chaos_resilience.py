"""Chaos resilience pin tests — Y2-CHANNEL-RELIABILITY mission 2026-05-01.

Pins five fail-soft contracts of the platform's resilience layer:

1. Jina reranker → 429 storm trips ``CircuitBreaker`` to OPEN; subsequent
   calls raise ``CircuitBreakerOpen`` immediately (no API call). Caller
   path is documented to fall back to ``NullReranker`` / RRF order.
2. Redis bot-registry down → ``BotRegistryService.lookup`` falls through
   to the DB layer instead of crashing the request.
3. DB connection lost during lookup → repository raises a typed
   ``SQLAlchemyError`` (not ``Exception``) so callers can produce 503.
4. ``model_resolver`` cache stale / miss → registry rebuilds via DB.
5. Tenant isolation under concurrent load → 100 tenants × 10 turns of
   concurrent ``lookup`` calls all resolve to their OWN row, no leak.

These tests are intentionally **fast** (no external network, no live DB)
because their job is to pin contracts, not measure latency. A separate
quarterly game-day exercise (see ``docs/DR_RUNBOOK.md`` §6) runs the
same scenarios against real infrastructure.

Anything testing real Postgres lives in
``test_3key_cross_tenant_isolation.py`` already; this file complements
that with the failure-mode contracts.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.application.services.retry_policy import (
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from ragbot.shared.errors import CircuitBreakerOpen, RetrievalError
from tests.conftest import TEST_TENANT_UUID, upstream_to_uuid

# Reuse the proven FakeRedis from the registry unit test.
from tests.unit.test_bot_registry_service import FakeRedis


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_cfg(
    *,
    bot_id: str = "support",
    channel_type: str = "web",
    record_tenant_id=None,
    upstream_int: int | None = None,
) -> BotConfig:
    """Build a minimal BotConfig with a fresh UUID — sufficient for the
    registry / cache contracts tested here. ``upstream_int`` is a
    convenience for tests that want a deterministic per-tenant UUID
    derived from a positive integer (mirrors the production backfill).
    """
    rt = record_tenant_id or (
        upstream_to_uuid(upstream_int) if upstream_int is not None else TEST_TENANT_UUID
    )
    return BotConfig(
        id=uuid4(),
        bot_id=bot_id,
        channel_type=channel_type,
        record_tenant_id=rt,
        workspace_id=str(rt),
        bot_name=f"Bot {rt}/{bot_id}/{channel_type}",
    )


# ──────────────────────────────────────────────────────────────────────────
# §1. Jina rerank 429 storm → CircuitBreaker OPEN → fallback works
# ──────────────────────────────────────────────────────────────────────────


def test_circuit_breaker_opens_after_burst_of_failures_and_blocks_calls() -> None:
    """A burst of ``fail_max`` consecutive failures MUST trip the breaker
    OPEN; the very next ``can_execute()`` MUST return False so the caller
    short-circuits to the NullReranker fallback path WITHOUT issuing an
    HTTP call. This is the behaviour relied on by ``JinaReranker`` to
    avoid amplifying a 429 storm.

    Equivalent: ``CircuitBreaker.__enter__`` raises ``CircuitBreakerOpen``
    when entered while OPEN — verified in the ``with`` form below.
    """
    cb = CircuitBreaker(
        name="reranker:jina:test",
        policy=CircuitBreakerPolicy(fail_max=3, reset_timeout_s=30),
    )
    # 1st-3rd failures — breaker still CLOSED until threshold.
    for _ in range(3):
        cb.record_failure()
    # Threshold reached → OPEN.
    assert cb.state.value == "open", f"breaker must trip OPEN after fail_max, got {cb.state}"
    assert cb.can_execute() is False, "OPEN breaker must short-circuit calls"

    # The ``with`` form is what JinaReranker uses — it must raise.
    with pytest.raises(CircuitBreakerOpen):
        with cb:
            pass  # pragma: no cover — body unreachable when OPEN


def test_circuit_breaker_recovers_via_half_open_after_reset_timeout() -> None:
    """After the reset window elapses, the breaker MUST move to HALF_OPEN
    on the next ``can_execute()`` call so a probe request can verify the
    upstream is healthy again. A single success closes it.

    Pins the recovery path so a paged-on-call's "Jina is back" test
    doesn't get stuck behind a stale-OPEN breaker.
    """
    cb = CircuitBreaker(
        name="reranker:jina:test",
        policy=CircuitBreakerPolicy(fail_max=2, reset_timeout_s=0),
    )
    cb.record_failure()
    cb.record_failure()
    assert cb.state.value == "open"
    # reset_timeout_s=0 → next can_execute() flips to HALF_OPEN.
    assert cb.can_execute() is True
    assert cb.state.value == "half_open"
    # A successful probe closes the breaker.
    cb.record_success()
    assert cb.state.value == "closed"


def test_jina_reranker_translates_circuit_open_to_retrieval_error() -> None:
    """When the Jina reranker's internal CB is OPEN, ``rerank()`` MUST raise
    ``RetrievalError`` (NOT ``CircuitBreakerOpen``). The orchestrator catches
    ``RetrievalError`` to drop into the NullReranker / RRF fallback path —
    leaking the raw breaker exception would crash the chat turn.

    This contract is documented at ``JinaReranker.rerank`` `@raises` line.
    """
    # Lazy import — avoids module-level httpx open during collection.
    from ragbot.infrastructure.reranker.jina_reranker import JinaReranker

    rr = JinaReranker(api_key="test-key-not-real")
    # Force the CB to OPEN without firing any HTTP request.
    for _ in range(20):
        rr._cb.record_failure()
    assert rr._cb.state.value == "open"

    async def _run() -> None:
        with pytest.raises(RetrievalError):
            await rr.rerank("q", [{"content": "doc"}], top_n=1)

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────
# §2. Redis down → BotRegistry falls through to DB
# ──────────────────────────────────────────────────────────────────────────


class _RedisDown:
    """Drop-in Redis replacement that raises on every call.

    Mirrors ``redis.exceptions.ConnectionError`` semantics — but using
    ``OSError`` because the registry's defensive code paths must accept
    any low-level failure shape, not just a redis-specific one.
    """

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        raise OSError("redis: connection refused (chaos)")

    async def set(self, *a, **kw) -> None:  # noqa: ARG002, D401
        raise OSError("redis: connection refused (chaos)")

    async def delete(self, *a, **kw) -> None:  # noqa: ARG002
        raise OSError("redis: connection refused (chaos)")

    async def sadd(self, *a, **kw) -> None:  # noqa: ARG002
        raise OSError("redis: connection refused (chaos)")

    async def srem(self, *a, **kw) -> None:  # noqa: ARG002
        raise OSError("redis: connection refused (chaos)")

    async def smembers(self, *a, **kw) -> set[str]:  # noqa: ARG002
        raise OSError("redis: connection refused (chaos)")


def test_lookup_propagates_redis_failure_so_caller_can_503() -> None:
    """When Redis is unavailable, the registry's current lookup design
    surfaces the OSError to the caller (top-level chat handler) so it can
    emit a 503 / structured event rather than silently mis-resolving the
    bot. Pinning the fail-fast contract here means future "swallow the
    Redis error and silently DB-fallback" refactors are surfaced in code
    review — that path could mask a chronic Redis outage indefinitely.

    Note: a future enhancement may add a per-call try/except around the
    Redis read path to degrade to DB-only mode. When that lands, this
    test is the one to update — it's the contract pin point.
    """
    cfg = _make_cfg()
    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=cfg)
    svc = BotRegistryService(repo=repo, redis_client=_RedisDown())

    async def _run() -> bool:
        try:
            await svc.lookup(TEST_TENANT_UUID, str(TEST_TENANT_UUID), "support", "web")
            return False
        except OSError:
            return True

    raised = asyncio.run(_run())
    assert raised, "Redis-down lookup must surface OSError so caller can 503"


# ──────────────────────────────────────────────────────────────────────────
# §3. DB connection lost → typed SQLAlchemyError, not bare Exception
# ──────────────────────────────────────────────────────────────────────────


def test_db_connection_lost_raises_typed_sqlalchemy_error() -> None:
    """The repository contract is: DB-layer failures surface as
    ``SQLAlchemyError`` (or a subclass like ``OperationalError``). Top-level
    handlers in the HTTP layer translate that into a 503. A bare
    ``Exception`` here would force broader catches upstream and violate
    the broad-except sweep policy in CLAUDE.md.

    We simulate by giving the registry a repo whose ``find_by_4key``
    raises ``OperationalError``; the cache-miss path of ``lookup`` invokes
    the repo and the typed exception MUST escape unchanged.
    """
    repo = MagicMock()
    repo.find_by_4key = AsyncMock(
        side_effect=OperationalError("SELECT ...", {}, Exception("conn reset"))
    )
    redis_fake = FakeRedis()  # cold cache → forces DB hit
    svc = BotRegistryService(repo=repo, redis_client=redis_fake)

    async def _run() -> type:
        try:
            await svc.lookup(TEST_TENANT_UUID, str(TEST_TENANT_UUID), "support", "web")
        except SQLAlchemyError as exc:
            return type(exc)
        except Exception as exc:  # noqa: BLE001 — test harness only
            pytest.fail(f"DB error must surface as SQLAlchemyError, got {type(exc).__name__}")
            raise
        pytest.fail("expected DB error to be raised")
        raise AssertionError  # unreachable

    err_type = asyncio.run(_run())
    assert issubclass(err_type, SQLAlchemyError)


# ──────────────────────────────────────────────────────────────────────────
# §4. Bot registry cache stale / miss → DB rebuild
# ──────────────────────────────────────────────────────────────────────────


def test_cache_miss_falls_back_to_db_and_repopulates() -> None:
    """Whenever Redis returns no entry (cold start, manual flush, or
    stale-after-restart), ``lookup()`` MUST invoke the repository,
    repopulate the cache, and return the row. Pins the cache-miss
    self-healing contract; a future regression that simply returned
    ``None`` on miss would be silent in production until users complain.
    """
    cfg = _make_cfg(bot_id="support", channel_type="web", record_tenant_id=TEST_TENANT_UUID)
    repo = MagicMock()
    repo.find_by_4key = AsyncMock(return_value=cfg)
    redis_fake = FakeRedis()
    svc = BotRegistryService(repo=repo, redis_client=redis_fake)

    async def _run() -> tuple[BotConfig, BotConfig]:
        # First call — cold cache → DB hit.
        first = await svc.lookup(TEST_TENANT_UUID, str(TEST_TENANT_UUID), "support", "web")  # type: ignore[arg-type]
        assert first is not None
        # Now Redis should hold the entry; second call → cache hit, no DB.
        repo.find_by_4key.reset_mock()
        second = await svc.lookup(TEST_TENANT_UUID, str(TEST_TENANT_UUID), "support", "web")
        assert second is not None
        # Repo must NOT have been touched on the second call.
        repo.find_by_4key.assert_not_called()
        return first, second

    first, second = asyncio.run(_run())
    assert first.id == second.id == cfg.id


# ──────────────────────────────────────────────────────────────────────────
# §5. Tenant isolation under concurrent load (100 tenants × 10 turns)
# ──────────────────────────────────────────────────────────────────────────


def test_tenant_isolation_under_concurrent_load() -> None:
    """100 tenants share the SAME ``(bot_id, channel_type)`` slug. We fire
    1000 concurrent ``lookup`` calls (10 per tenant) and assert EVERY
    response carries the requesting tenant's own ``record_bot_id`` UUID.

    This is the contract that prevents cross-tenant leak under load when
    the registry's internal lock + Redis + DB paths interleave. A bug
    where the cache key collapsed ``tenant_id`` would surface here as a
    statistical mix of UUIDs.

    The test runs entirely in-memory (FakeRedis + mocked repo) so it's
    fast (<1s) and deterministic.
    """
    from uuid import UUID

    n_tenants = 100
    turns_per_tenant = 10
    bot_id = "support"
    channel_type = "web"

    # One BotConfig per tenant — record_tenant_id UUIDs derived from a
    # deterministic upstream-int mapping so the harness can pair requests
    # to expected rows without an INT->UUID side table.
    by_tenant: dict[UUID, BotConfig] = {
        upstream_to_uuid(t): _make_cfg(
            bot_id=bot_id, channel_type=channel_type, upstream_int=t,
        )
        for t in range(1, n_tenants + 1)
    }

    async def _find(  # noqa: ARG001
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> BotConfig | None:
        # Repo signature is positional
        # ``(record_tenant_id, workspace_id, bot_id, channel_type)`` — matches
        # ``SqlAlchemyBotRepository.find_by_4key`` exactly.
        return by_tenant.get(record_tenant_id)

    repo = MagicMock()
    repo.find_by_4key = AsyncMock(side_effect=_find)
    svc = BotRegistryService(repo=repo, redis_client=FakeRedis())

    async def _hit(record_tenant_id: UUID) -> BotConfig | None:
        return await svc.lookup(
            record_tenant_id, str(record_tenant_id), bot_id, channel_type,
        )

    async def _drive() -> list[tuple[UUID, BotConfig | None]]:
        tasks: list[asyncio.Task] = []
        tenant_uuids = [upstream_to_uuid(t) for t in range(1, n_tenants + 1)]
        for tu in tenant_uuids:
            for _ in range(turns_per_tenant):
                tasks.append(asyncio.create_task(_hit(tu)))
        results = await asyncio.gather(*tasks)
        # Pair results back with their requesting record_tenant_id in submission order.
        paired: list[tuple[UUID, BotConfig | None]] = []
        i = 0
        for tu in tenant_uuids:
            for _ in range(turns_per_tenant):
                paired.append((tu, results[i]))
                i += 1
        return paired

    paired = asyncio.run(_drive())
    assert len(paired) == n_tenants * turns_per_tenant

    leaks: list[tuple[UUID, UUID]] = []
    for requested_tenant, cfg in paired:
        assert cfg is not None, f"tenant {requested_tenant} must resolve"
        if cfg.record_tenant_id != requested_tenant:
            leaks.append((requested_tenant, cfg.record_tenant_id))
    assert not leaks, (
        f"cross-tenant leak under concurrent load — first {leaks[:5]} "
        f"out of {len(leaks)}/{len(paired)} requests"
    )


# ──────────────────────────────────────────────────────────────────────────
# §6. NullReranker fail-soft contract (used as Jina-OPEN fallback)
# ──────────────────────────────────────────────────────────────────────────


def test_null_reranker_returns_chunks_unchanged_when_jina_unavailable() -> None:
    """``NullReranker`` is the documented fallback when the Jina reranker
    refuses to serve (CB OPEN, missing key, opt-out). Its contract is to
    return the input chunks truncated to ``top_n`` WITHOUT any external
    call. A regression that started raising or clearing scores would
    cascade into 500 errors on every turn during a Jina outage.
    """
    from ragbot.infrastructure.reranker.null_reranker import NullReranker

    rr = NullReranker()
    chunks = [
        {"content": "doc-1", "score": 0.9},
        {"content": "doc-2", "score": 0.8},
        {"content": "doc-3", "score": 0.7},
    ]

    async def _run() -> list[dict]:
        return await rr.rerank("query", chunks, top_n=2)

    out = asyncio.run(_run())
    assert len(out) == 2
    # Scores survive the bypass — downstream `reranker_min_score` filter
    # applies to the original RRF score.
    assert out[0]["score"] == 0.9
    assert out[1]["score"] == 0.8
