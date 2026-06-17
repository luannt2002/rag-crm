"""Tests for ``CorpusVersionService`` — the per-bot cache-bust derivation.

Critical behaviours under test:

* Hash is 12 chars + deterministic for the same ``(bot_id, marker)``.
* Two different markers produce two different hashes (cache-bust works).
* Empty corpus → stable sentinel, NOT a NULL or random value.
* Redis hit short-circuits the DB query (cache works).
* Redis miss falls through to DB then writes the result back.
* Tenant isolation — the Redis key includes tenant_id.
* Failure modes degrade to the legacy ``"latest"`` tag, never raise.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.services.corpus_version_service import (
    CorpusVersionService,
    _hash_payload,
    _redis_key,
)
from ragbot.shared.constants import (
    CACHE_KEY_CORPUS_VERSION_PREFIX,
    DEFAULT_BOT_CACHE_VERSION_HASH_LEN,
    DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL,
    LEGACY_CORPUS_VERSION_TAG,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal redis.asyncio.Redis stand-in — get/set/delete only."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.delete_calls = 0
        self.last_ttl: int | None = None
        self.fail_get = False
        self.fail_set = False

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        if self.fail_get:
            raise RedisError("boom")
        return self.store.get(key)

    async def set(self, key: str, value: bytes, *, ex: int | None = None) -> None:
        self.set_calls += 1
        self.last_ttl = ex
        if self.fail_set:
            raise RedisError("boom")
        self.store[key] = value

    async def delete(self, key: str) -> int:
        self.delete_calls += 1
        return 1 if self.store.pop(key, None) is not None else 0


class _FakeRow:
    """Mimics SQLAlchemy ``Row.first()`` indexing semantics."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def __getitem__(self, idx: int) -> Any:
        if idx != 0:
            raise IndexError(idx)
        return self._value


class _FakeResult:
    def __init__(self, row: _FakeRow | None) -> None:
        self._row = row

    def first(self) -> _FakeRow | None:
        return self._row


class _FakeSession:
    def __init__(self, marker: Any | None) -> None:
        self.marker = marker
        self.execute_calls = 0
        self.last_params: dict[str, Any] | None = None

    async def execute(self, _stmt: Any, params: dict[str, Any]) -> _FakeResult:
        self.execute_calls += 1
        self.last_params = params
        if self.marker is None:
            return _FakeResult(_FakeRow(None))
        return _FakeResult(_FakeRow(self.marker))


def _make_session_factory(marker: Any | None, *, raise_on_execute: bool = False):
    """Returns a callable session_factory() that yields an async-context FakeSession."""
    sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def _factory():
        s = _FakeSession(marker)
        if raise_on_execute:
            async def _raise(*_args: Any, **_kwargs: Any) -> _FakeResult:
                raise SQLAlchemyError("db down")
            s.execute = _raise  # type: ignore[assignment]
        sessions.append(s)
        try:
            yield s
        finally:
            pass

    _factory.sessions = sessions  # type: ignore[attr-defined]
    return _factory


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_hash_payload_returns_12_char_hex() -> None:
    bot = uuid.uuid4()
    out = _hash_payload(bot, "2026-05-01 12:00:00+00:00")
    assert len(out) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN
    assert all(c in "0123456789abcdef" for c in out)


def test_hash_payload_is_deterministic() -> None:
    bot = uuid.uuid4()
    a = _hash_payload(bot, "marker-1")
    b = _hash_payload(bot, "marker-1")
    assert a == b


def test_hash_payload_changes_with_marker() -> None:
    bot = uuid.uuid4()
    a = _hash_payload(bot, "marker-1")
    b = _hash_payload(bot, "marker-2")
    assert a != b, "marker change MUST flip the hash for cache-bust"


def test_hash_payload_changes_with_bot() -> None:
    a = _hash_payload(uuid.uuid4(), "same-marker")
    b = _hash_payload(uuid.uuid4(), "same-marker")
    assert a != b, "different bots must derive different versions"


def test_redis_key_is_tenant_scoped() -> None:
    tid = uuid.uuid4()
    bid = uuid.uuid4()
    key = _redis_key(tid, bid)
    assert key.startswith(CACHE_KEY_CORPUS_VERSION_PREFIX)
    assert str(tid) in key
    assert str(bid) in key


# ---------------------------------------------------------------------------
# get_for_bot — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_for_bot_returns_12_char_hash_from_db() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    out = await svc.get_for_bot(tid, bid)

    assert len(out) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN
    assert sf.sessions[0].execute_calls == 1
    assert sf.sessions[0].last_params == {"bot_id": str(bid)}
    assert redis.set_calls == 1, "DB result must be written to cache"


@pytest.mark.asyncio
async def test_get_for_bot_redis_hit_skips_db() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    # Pre-populate cache.
    redis.store[_redis_key(tid, bid)] = b"cafef00dbabe"

    out = await svc.get_for_bot(tid, bid)

    assert out == "cafef00dbabe"
    assert sf.sessions == [], "DB must NOT be hit on redis cache hit"


@pytest.mark.asyncio
async def test_get_for_bot_same_corpus_same_version() -> None:
    redis = _FakeRedis()
    marker = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    sf = _make_session_factory(marker)
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    v1 = await svc.get_for_bot(tid, bid)
    # Bust cache so we re-derive from DB; DB still returns same marker.
    await svc.invalidate(tid, bid)
    v2 = await svc.get_for_bot(tid, bid)

    assert v1 == v2, "stable corpus → stable version (so cache CAN hit)"


@pytest.mark.asyncio
async def test_get_for_bot_doc_updated_changes_version() -> None:
    redis = _FakeRedis()
    sf1 = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc1 = CorpusVersionService(session_factory=sf1, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    v_before = await svc1.get_for_bot(tid, bid)
    await svc1.invalidate(tid, bid)

    # Simulate doc update: new marker.
    sf2 = _make_session_factory(datetime(2026, 5, 2, tzinfo=timezone.utc))
    svc2 = CorpusVersionService(session_factory=sf2, redis_client=redis)
    v_after = await svc2.get_for_bot(tid, bid)

    assert v_before != v_after, "doc updated → version MUST flip (cache-bust)"


@pytest.mark.asyncio
async def test_get_for_bot_empty_corpus_returns_sentinel() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(None)  # MAX(updated_at) → NULL
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)

    out = await svc.get_for_bot(uuid.uuid4(), uuid.uuid4())

    assert out == DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL


# ---------------------------------------------------------------------------
# get_for_bot — failure / fallback paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_for_bot_no_record_bot_id_returns_legacy() -> None:
    sf = _make_session_factory(datetime.now(tz=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=_FakeRedis())

    out = await svc.get_for_bot(uuid.uuid4(), None)

    assert out == LEGACY_CORPUS_VERSION_TAG
    assert sf.sessions == [], "must not query DB without a bot id"


@pytest.mark.asyncio
async def test_get_for_bot_db_error_falls_back_to_legacy() -> None:
    sf = _make_session_factory(None, raise_on_execute=True)
    svc = CorpusVersionService(session_factory=sf, redis_client=_FakeRedis())

    out = await svc.get_for_bot(uuid.uuid4(), uuid.uuid4())

    assert out == LEGACY_CORPUS_VERSION_TAG, "DB outage must NOT break chat"


@pytest.mark.asyncio
async def test_get_for_bot_redis_get_failure_falls_through_to_db() -> None:
    redis = _FakeRedis()
    redis.fail_get = True
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)

    out = await svc.get_for_bot(uuid.uuid4(), uuid.uuid4())

    # Failure was logged + ignored; DB result still produced a real hash.
    assert len(out) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN
    assert sf.sessions[0].execute_calls == 1


@pytest.mark.asyncio
async def test_get_for_bot_redis_set_failure_does_not_raise() -> None:
    redis = _FakeRedis()
    redis.fail_set = True
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)

    out = await svc.get_for_bot(uuid.uuid4(), uuid.uuid4())

    assert len(out) == DEFAULT_BOT_CACHE_VERSION_HASH_LEN
    # No exception bubbled.


@pytest.mark.asyncio
async def test_get_for_bot_writes_with_configured_ttl() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis, cache_ttl_s=42)

    await svc.get_for_bot(uuid.uuid4(), uuid.uuid4())

    assert redis.last_ttl == 42


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_deletes_redis_entry() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    # Prime the cache.
    await svc.get_for_bot(tid, bid)
    assert redis.store, "cache primed"

    await svc.invalidate(tid, bid)

    assert _redis_key(tid, bid) not in redis.store


@pytest.mark.asyncio
async def test_invalidate_redis_failure_silent() -> None:
    class _DeleteRaisingRedis(_FakeRedis):
        async def delete(self, key: str) -> int:  # type: ignore[override]
            raise RedisError("boom")

    sf = _make_session_factory(None)
    svc = CorpusVersionService(session_factory=sf, redis_client=_DeleteRaisingRedis())

    # Should NOT raise.
    await svc.invalidate(uuid.uuid4(), uuid.uuid4())


# ---------------------------------------------------------------------------
# Concurrency smoke — multiple coroutines on a hot bot share cache.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_lookups_share_cache_after_first() -> None:
    redis = _FakeRedis()
    sf = _make_session_factory(datetime(2026, 5, 1, tzinfo=timezone.utc))
    svc = CorpusVersionService(session_factory=sf, redis_client=redis)
    tid = uuid.uuid4()
    bid = uuid.uuid4()

    # First call populates cache; subsequent 9 must read from Redis.
    await svc.get_for_bot(tid, bid)
    results = await asyncio.gather(*(svc.get_for_bot(tid, bid) for _ in range(9)))

    assert len(set(results)) == 1, "all callers see the same version"
    assert len(sf.sessions) == 1, "DB only hit once across 10 calls"


# ---------------------------------------------------------------------------
# Regression guard — the orchestrator must not pin corpus_version="latest"
# any more. This test pins the fix so a future regression (someone copy-
# pasting the old call shape) is caught at unit-test time, not in prod.
# ---------------------------------------------------------------------------


def test_query_graph_no_hardcoded_latest_corpus_version() -> None:
    """``query_graph.py`` must derive corpus_version, not hard-code 'latest'."""
    from pathlib import Path

    from ragbot.orchestration import query_graph as _qg

    src = Path(_qg.__file__).read_text(encoding="utf-8")
    # Allow the constant import + comment mention; forbid the literal at call sites.
    assert 'corpus_version="latest"' not in src, (
        "corpus_version='latest' literal removed in critical-fix #1 — see "
        "CorpusVersionService. Re-derive via _resolve_corpus_version(state)."
    )
