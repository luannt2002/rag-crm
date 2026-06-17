"""Unit tests — :mod:`ragbot.application.services.bot_lifecycle_service`.

ADR-W1-D4 §2a — purge saga contract (repro of gap H-BOT: soft-delete
left semantic_cache / chunks / Redis keys alive forever because the FK
CASCADE only fires on HARD delete, which never happened).

Covered (mock session / redis — no live infra):

- guard: live bot (``is_deleted=false``) → ``BotNotPurgeableError``.
- DELETE FROM bots is tenant-scoped (``record_tenant_id`` in WHERE).
- audit row + outbox INSERT happen in the SAME transaction as the
  DELETE (single commit).
- registry / corpus_version / uq-cache busts fire with correct args.
- idempotent re-run: row already gone → ``db_rows_bots=0``, Redis steps
  still run, no raise.
- shared embedding L1 cache is deliberately NOT touched.
- ``purge_tenant``: sequential fan-out, soft-deletes tenant after the
  drain, partial failure reported without aborting later bots.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.services.bot_lifecycle_service import (
    SKIP_EMBEDDING_CACHE,
    SKIP_OUTBOX_DEDUP,
    BotLifecycleService,
    BotNotPurgeableError,
    BotPurgeReport,
)
from ragbot.shared.constants import CACHE_KEY_UQ_PREFIX, SUBJECT_BOT_PURGED

# ── Fakes ───────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _BotRow:
    """Mirror of the S1 guard SELECT columns."""

    def __init__(self, *, is_deleted: bool = True) -> None:
        self.id = uuid4()
        self.workspace_id = "ws-a"
        self.bot_id = "support"
        self.channel_type = "web"
        self.is_deleted = is_deleted


class _FakeSession:
    """Records executes; returns queued results in order."""

    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = list(results)
        self.executes: list[tuple[str, dict[str, Any] | None]] = []
        self.commits = 0
        self.closed = False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.executes.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return _FakeResult([], rowcount=0)

    async def commit(self) -> None:
        self.commits += 1

    async def close(self) -> None:
        self.closed = True

    def add(self, _obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class _FakeRedis:
    """SCAN + UNLINK recorder. Two scan pages to exercise the cursor loop."""

    def __init__(self, keys: list[str] | None = None) -> None:
        self._keys = keys or []
        self.scan_calls: list[dict[str, Any]] = []
        self.unlinked: list[str] = []

    async def scan(self, cursor: int = 0, match: str = "", count: int = 0):
        self.scan_calls.append({"cursor": cursor, "match": match, "count": count})
        if cursor == 0 and len(self._keys) > 1:
            return 1, self._keys[:1]
        if cursor == 1:
            return 0, self._keys[1:]
        return 0, list(self._keys)

    async def unlink(self, *keys: str) -> int:
        self.unlinked.extend(keys)
        return len(keys)


class _Harness:
    """Constructed service + recorders. The infra collaborators
    (``session_with_tenant`` / ``insert_audit_row`` / TenantRepository)
    are INJECTED via the constructor — hexagonal boundary keeps them out
    of the application module, so tests inject fakes the same way
    bootstrap injects the real ones."""

    def __init__(
        self,
        sessions: list[_FakeSession],
        *,
        redis: Any | None = None,
        tenant_repo_factory: Any | None = None,
    ) -> None:
        iterator = iter(sessions)

        def factory() -> _FakeSession:
            return next(iterator)

        self.bound_tenants: list[Any] = []
        self.audit_calls: list[dict[str, Any]] = []

        @asynccontextmanager
        async def _fake_swt(inner_factory: Any, *, record_tenant_id: Any = None):
            self.bound_tenants.append(record_tenant_id)
            session = inner_factory()
            try:
                yield session
            finally:
                await session.close()

        async def _fake_audit(_session: Any, **kwargs: Any) -> Any:
            self.audit_calls.append(kwargs)
            return MagicMock()

        self.registry = MagicMock()
        self.registry.invalidate = AsyncMock()
        self.corpus = MagicMock()
        self.corpus.invalidate = AsyncMock()
        self.redis = redis if redis is not None else _FakeRedis()
        self.svc = BotLifecycleService(
            session_factory=factory,
            registry=self.registry,
            corpus_version_service=self.corpus,
            redis_client=self.redis,
            tenant_session=_fake_swt,
            audit_writer=_fake_audit,
            tenant_repository_factory=(
                tenant_repo_factory if tenant_repo_factory is not None
                else MagicMock()
            ),
        )


# ── purge_bot ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_refuses_live_bot() -> None:
    """is_deleted=false → guard raises; nothing deleted, no Redis bust."""
    row = _BotRow(is_deleted=False)
    session = _FakeSession([_FakeResult([row])])
    h = _Harness([session])
    svc, registry, corpus, redis = h.svc, h.registry, h.corpus, h.redis

    with pytest.raises(BotNotPurgeableError):
        await svc.purge_bot(
            row.id, record_tenant_id=uuid4(), actor_user_id="admin",
        )

    # Only the guard SELECT ran — no DELETE, no commit.
    assert len(session.executes) == 1
    assert session.commits == 0
    registry.invalidate.assert_not_awaited()
    corpus.invalidate.assert_not_awaited()
    assert redis.unlinked == []


@pytest.mark.asyncio
async def test_purge_deletes_bot_row_scoped() -> None:
    """DELETE must carry record_tenant_id + is_deleted in the WHERE —
    RLS GUC alone is not the only guard (R2)."""
    row = _BotRow(is_deleted=True)
    tenant = uuid4()
    session = _FakeSession([
        _FakeResult([row]),              # S1 guard SELECT
        _FakeResult([], rowcount=1),     # S2 DELETE
        _FakeResult([], rowcount=1),     # S2 outbox INSERT
    ])
    h = _Harness([session])
    svc, bound = h.svc, h.bound_tenants

    report = await svc.purge_bot(
        row.id, record_tenant_id=tenant, actor_user_id="admin",
    )

    assert isinstance(report, BotPurgeReport)
    assert report.purged is True
    assert report.db_rows_bots == 1
    assert bound == [tenant]
    delete_sql, delete_params = session.executes[1]
    assert "DELETE FROM bots" in delete_sql
    assert "record_tenant_id" in delete_sql
    assert "is_deleted" in delete_sql
    assert delete_params["record_tenant_id"] == tenant
    assert delete_params["record_bot_id"] == row.id


@pytest.mark.asyncio
async def test_purge_emits_audit_and_outbox_same_tx() -> None:
    """Audit row + outbox INSERT execute on the SAME session before the
    single commit — atomic with the DELETE (saga S2)."""
    row = _BotRow(is_deleted=True)
    tenant = uuid4()
    session = _FakeSession([
        _FakeResult([row]),
        _FakeResult([], rowcount=1),
        _FakeResult([], rowcount=1),
    ])
    h = _Harness([session])
    svc, audit_calls = h.svc, h.audit_calls

    await svc.purge_bot(
        row.id, record_tenant_id=tenant, actor_user_id="ops-admin",
        trace_id="trace-9",
    )

    assert session.commits == 1
    # Audit — action 'purge' on resource_type 'bot' with 4-key snapshot.
    assert len(audit_calls) == 1
    audit = audit_calls[0]
    assert audit["action"] == "purge"
    assert audit["resource_type"] == "bot"
    assert audit["resource_id"] == str(row.id)
    assert audit["record_tenant_id"] == tenant
    assert audit["workspace_id"] == row.workspace_id
    assert audit["actor_user_id"] == "ops-admin"
    assert audit["before_json"]["bot_id"] == row.bot_id
    assert audit["before_json"]["channel_type"] == row.channel_type
    # Outbox — same session, subject bot.purged.v1, 4-key payload.
    outbox_sql, outbox_params = session.executes[2]
    assert "INSERT INTO outbox" in outbox_sql
    assert outbox_params["subject"] == SUBJECT_BOT_PURGED
    payload = json.loads(outbox_params["payload"])
    assert payload["event_type"] == SUBJECT_BOT_PURGED
    assert payload["record_tenant_id"] == str(tenant)
    assert payload["workspace_id"] == row.workspace_id
    assert payload["bot_id"] == row.bot_id
    assert payload["channel_type"] == row.channel_type
    assert payload["bot_uuid"] == str(row.id)


@pytest.mark.asyncio
async def test_purge_busts_registry_corpus_uq() -> None:
    """S3-S5 collaborators receive the right args post-commit."""
    row = _BotRow(is_deleted=True)
    tenant = uuid4()
    keys = [
        f"ragbot:uq:v1:{row.id}:aaaa",
        f"ragbot:uq:v2:{row.id}:bbbb",
    ]
    redis = _FakeRedis(keys=keys)
    session = _FakeSession([
        _FakeResult([row]),
        _FakeResult([], rowcount=1),
        _FakeResult([], rowcount=1),
    ])
    h = _Harness([session], redis=redis)
    svc, registry, corpus = h.svc, h.registry, h.corpus

    report = await svc.purge_bot(
        row.id, record_tenant_id=tenant, actor_user_id="admin",
    )

    corpus.invalidate.assert_awaited_once_with(tenant, row.id)
    registry.invalidate.assert_awaited_once_with(
        tenant, row.workspace_id, row.bot_id, row.channel_type,
    )
    # SCAN pattern: wildcard prompt-version, bot-scoped.
    assert redis.scan_calls[0]["match"] == f"{CACHE_KEY_UQ_PREFIX}*:{row.id}:*"
    assert sorted(redis.unlinked) == sorted(keys)
    assert report.redis_uq_keys == 2


@pytest.mark.asyncio
async def test_purge_idempotent_rerun() -> None:
    """Row already gone (crash between S2 commit and S3-S5, or plain
    re-run): db_rows_bots=0, Redis steps still run → saga converges."""
    bot_uuid = uuid4()
    tenant = uuid4()
    session = _FakeSession([_FakeResult([])])  # guard SELECT → no row
    redis = _FakeRedis(keys=[f"ragbot:uq:v1:{bot_uuid}:cccc"])
    h = _Harness([session], redis=redis)
    svc, registry, corpus = h.svc, h.registry, h.corpus

    report = await svc.purge_bot(
        bot_uuid, record_tenant_id=tenant, actor_user_id="admin",
    )

    assert report.purged is False
    assert report.db_rows_bots == 0
    # S3 + S5 still converge; S4 needs the 4-key snapshot which is gone —
    # registry was already invalidated at soft-delete time (delete_bot).
    corpus.invalidate.assert_awaited_once_with(tenant, bot_uuid)
    registry.invalidate.assert_not_awaited()
    assert redis.unlinked == [f"ragbot:uq:v1:{bot_uuid}:cccc"]
    # No DELETE was attempted (no row to act on) and nothing committed.
    assert len(session.executes) == 1
    assert session.commits == 0


@pytest.mark.asyncio
async def test_purge_skips_shared_embedding_cache() -> None:
    """Embedding L1 cache is content-keyed and SHARED cross-bot — purge
    must never UNLINK ``ragbot:emb:*`` keys. The deliberate skip is
    reported so the gate test can assert intent, not absence-by-bug."""
    row = _BotRow(is_deleted=True)
    redis = _FakeRedis(keys=[])
    session = _FakeSession([
        _FakeResult([row]),
        _FakeResult([], rowcount=1),
        _FakeResult([], rowcount=1),
    ])
    h = _Harness([session], redis=redis)
    svc = h.svc

    report = await svc.purge_bot(
        row.id, record_tenant_id=uuid4(), actor_user_id="admin",
    )

    assert SKIP_EMBEDDING_CACHE in report.skipped
    assert SKIP_OUTBOX_DEDUP in report.skipped
    # Every SCAN issued was uq-scoped — never an emb pattern.
    for call in redis.scan_calls:
        assert call["match"].startswith(CACHE_KEY_UQ_PREFIX)
        assert "emb" not in call["match"]


# ── purge_tenant ────────────────────────────────────────────────────────────


def _tenant_fanout_service(
    bot_ids: list[Any],
) -> tuple[BotLifecycleService, list[Any], list[Any]]:
    """Service with purge_bot stubbed; records call order. The tenant
    listing session returns ``bot_ids`` rows; the soft-delete session is
    served by a stubbed TenantRepository."""

    class _IdRow:
        def __init__(self, bid: Any) -> None:
            self._bid = bid

        def __getitem__(self, idx: int) -> Any:
            assert idx == 0
            return self._bid

    list_session = _FakeSession([_FakeResult([_IdRow(b) for b in bot_ids])])
    soft_delete_session = _FakeSession([])

    purge_order: list[Any] = []
    soft_deleted: list[Any] = []

    class _FakeTenantRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def soft_delete_tenant(self, record_tenant_id: Any) -> dict:
            soft_deleted.append(record_tenant_id)
            return {"id": str(record_tenant_id)}

    h = _Harness(
        [list_session, soft_delete_session],
        tenant_repo_factory=_FakeTenantRepo,
    )
    return h.svc, purge_order, soft_deleted


@pytest.mark.asyncio
async def test_purge_tenant_fans_out_sequential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N bots → N purge_bot calls IN ORDER, never overlapping (Async
    Rule 7 — heavy cascade DELETEs must not gather on one pool)."""
    bot_ids = [uuid4(), uuid4(), uuid4()]
    tenant = uuid4()
    svc, purge_order, _sd = _tenant_fanout_service(bot_ids)

    in_flight = 0
    max_in_flight = 0

    async def _fake_purge_bot(bid: Any, **_kw: Any) -> BotPurgeReport:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)  # yield — overlap would be visible here
        purge_order.append(bid)
        in_flight -= 1
        return BotPurgeReport(
            record_bot_id=bid, purged=True, db_rows_bots=1,
            redis_uq_keys=0, skipped=[],
        )

    monkeypatch.setattr(svc, "purge_bot", _fake_purge_bot)

    reports = await svc.purge_tenant(tenant, actor_user_id="admin")

    assert purge_order == bot_ids
    assert max_in_flight == 1
    assert [r.record_bot_id for r in reports] == bot_ids


@pytest.mark.asyncio
async def test_purge_tenant_soft_deletes_tenant_after_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_delete_tenant fires AFTER the last purge_bot."""
    bot_ids = [uuid4(), uuid4()]
    tenant = uuid4()
    svc, purge_order, soft_deleted = _tenant_fanout_service(bot_ids)

    async def _fake_purge_bot(bid: Any, **_kw: Any) -> BotPurgeReport:
        assert soft_deleted == []  # tenant must NOT be deleted mid-drain
        purge_order.append(bid)
        return BotPurgeReport(
            record_bot_id=bid, purged=True, db_rows_bots=1,
            redis_uq_keys=0, skipped=[],
        )

    monkeypatch.setattr(svc, "purge_bot", _fake_purge_bot)

    await svc.purge_tenant(tenant, actor_user_id="admin")

    assert purge_order == bot_ids
    assert soft_deleted == [tenant]


@pytest.mark.asyncio
async def test_purge_tenant_partial_failure_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot 2/3 raises transient → its report says purged=False; bot 3
    still runs (saga per-bot independent — R6)."""
    bot_ids = [uuid4(), uuid4(), uuid4()]
    tenant = uuid4()
    svc, purge_order, soft_deleted = _tenant_fanout_service(bot_ids)

    async def _fake_purge_bot(bid: Any, **_kw: Any) -> BotPurgeReport:
        purge_order.append(bid)
        if bid == bot_ids[1]:
            raise SQLAlchemyError("transient blip on bot 2")
        return BotPurgeReport(
            record_bot_id=bid, purged=True, db_rows_bots=1,
            redis_uq_keys=0, skipped=[],
        )

    monkeypatch.setattr(svc, "purge_bot", _fake_purge_bot)

    reports = await svc.purge_tenant(tenant, actor_user_id="admin")

    assert purge_order == bot_ids  # all three attempted
    assert [r.purged for r in reports] == [True, False, True]
    assert reports[1].record_bot_id == bot_ids[1]
    assert reports[1].db_rows_bots == 0
    # Drain "completed" (failed bot stays soft-deleted, not active) →
    # tenant soft-delete still proceeds; re-run converges the leftover.
    assert soft_deleted == [tenant]
