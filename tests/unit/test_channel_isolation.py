"""Channel isolation pin tests — Y2-CHANNEL-RELIABILITY mission 2026-05-01.

Pins three structural guarantees of the multi-channel design without booting
Postgres / Redis / FastAPI. They use the same ``FakeRedis`` + mocked-repo
pattern as ``test_bot_registry_service.py`` so the tests stay fast and
deterministic on CI.

Guarantees pinned (mirrors docs/MULTI_CHANNEL_INTEGRATION.md §6):

1. Same ``bot_id`` on different ``channel_type`` values → ``BotRegistryService``
   resolves to different ``record_bot_id`` UUIDs (no per-channel collision).
2. Redis cache keys are per-channel: a ``web`` lookup CANNOT serve a
   ``messenger`` request even when ``(tenant_id, bot_id)`` match — the cache
   key namespaces channels apart.
3. A ``BotConfig`` for one channel never carries another channel's
   system_prompt — the registry never silently falls back across channels
   when the requested channel is unregistered.

The 3 tests double as regression guards: if anyone collapses the cache key
to 2-tuple ``(tenant_id, bot_id)`` or adds a "default-channel" fallback,
the assertions trip immediately.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import BotRegistryService
from tests.conftest import TEST_TENANT_UUID

# Reuse the proven fake from the sibling registry test rather than rolling
# a new one — keeps behaviour identical to the existing suite.
from tests.unit.test_bot_registry_service import FakeRedis


# ── Test helpers ──────────────────────────────────────────────────────────


def _make_cfg(
    *,
    bot_id: str,
    channel_type: str,
    record_tenant_id=None,
    system_prompt: str = "",
    bot_name: str | None = None,
) -> BotConfig:
    """Construct a minimal BotConfig for in-memory registry tests.

    ``id`` is a fresh UUID per call so two configs that share
    ``(bot_id, channel_type)`` slug but differ in tenant or channel still
    have distinct ``record_bot_id`` values — same invariant DB enforces.
    """
    rt = record_tenant_id or TEST_TENANT_UUID
    return BotConfig(
        id=uuid4(),
        bot_id=bot_id,
        channel_type=channel_type,
        record_tenant_id=rt,
        workspace_id=str(rt),
        bot_name=bot_name or f"Bot {bot_id}/{channel_type}",
        system_prompt=system_prompt,
    )


def _make_service(rows: list[BotConfig]) -> BotRegistryService:
    """Build a registry backed by FakeRedis + a mocked repository.

    ``find_by_4key`` defaults to ``None``; individual tests override it
    when they want to exercise the cache-miss → DB fallback branch.
    """
    repo = MagicMock()
    repo.list_active = AsyncMock(return_value=rows)
    repo.find_by_4key = AsyncMock(return_value=None)
    return BotRegistryService(repo=repo, redis_client=FakeRedis())


# All cfgs in this file resolve onto the same workspace slug
# ``str(TEST_TENANT_UUID)`` (the ``_make_cfg`` default), so tests pass
# this value to ``lookup``.
_DEFAULT_WS = str(TEST_TENANT_UUID)


# ── Test 1 — same bot_id, different channel_type → distinct UUIDs ─────────


def test_same_bot_id_different_channel_type_resolve_to_distinct_uuids() -> None:
    """A bot owner registers ``bot_id="support"`` on both ``web`` and
    ``messenger`` for the same tenant. The registry MUST surface them as
    two separate ``BotConfig`` rows with different ``record_bot_id`` values
    and per-channel ``system_prompt`` text.

    Regression guard: if the cache or lookup ever ignores ``channel_type``
    and uses only ``(tenant_id, bot_id)``, one channel's prompt would
    leak into the other and ``cfg_web.id == cfg_messenger.id`` would
    falsely succeed.
    """
    web_prompt = "WEB_PROMPT_VARIANT"
    messenger_prompt = "MESSENGER_PROMPT_VARIANT"
    cfg_web = _make_cfg(
        bot_id="support",
        channel_type="web",
        record_tenant_id=TEST_TENANT_UUID,
        system_prompt=web_prompt,
    )
    cfg_msg = _make_cfg(
        bot_id="support",
        channel_type="messenger",
        record_tenant_id=TEST_TENANT_UUID,
        system_prompt=messenger_prompt,
    )
    # Sanity — UUIDs distinct at DTO layer (DB unique-constraint analogue).
    assert cfg_web.id != cfg_msg.id

    svc = _make_service([cfg_web, cfg_msg])
    asyncio.run(svc.bootstrap_cache())

    got_web = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "web"))
    got_msg = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "messenger"))

    assert got_web is not None and got_msg is not None
    # Different physical rows.
    assert got_web.id == cfg_web.id
    assert got_msg.id == cfg_msg.id
    assert got_web.id != got_msg.id
    # Per-channel prompts intact — no cross-channel inheritance.
    assert got_web.system_prompt == web_prompt
    assert got_msg.system_prompt == messenger_prompt
    assert messenger_prompt not in got_web.system_prompt
    assert web_prompt not in got_msg.system_prompt


# ── Test 2 — Redis cache key namespaces channels apart ────────────────────


def test_redis_cache_key_includes_channel_type_no_cross_channel_serve() -> None:
    """Lookup populates Redis under a key that includes ``channel_type``.
    A subsequent lookup for the SAME ``(tenant_id, bot_id)`` but different
    ``channel_type`` MUST NOT receive the previously cached config.

    Implementation detail under test: the key shape
    ``ragbot:bot:{record_tenant_id}:{bot_id}:{channel_type}``.

    If a future refactor accidentally drops ``channel_type`` from the key,
    this test fails because the second lookup would see a hit and return
    the wrong row, OR (more likely) the registry would still call DB but
    overwrite the original cache entry — either way the ``record_bot_id``
    asserted below would diverge from the channel under test.
    """
    cfg_web = _make_cfg(
        bot_id="support", channel_type="web", record_tenant_id=TEST_TENANT_UUID,
        system_prompt="WEB",
    )
    cfg_msg = _make_cfg(
        bot_id="support", channel_type="messenger", record_tenant_id=TEST_TENANT_UUID,
        system_prompt="MSG",
    )
    svc = _make_service([cfg_web, cfg_msg])
    asyncio.run(svc.bootstrap_cache())

    # Inspect the underlying FakeRedis store: both keys must coexist with
    # distinct values.
    redis = svc._redis  # type: ignore[attr-defined]
    keys = sorted(redis._store.keys())  # type: ignore[attr-defined]
    web_key = f"ragbot:bot:{TEST_TENANT_UUID}:{_DEFAULT_WS}:support:web"
    msg_key = f"ragbot:bot:{TEST_TENANT_UUID}:{_DEFAULT_WS}:support:messenger"
    assert web_key in keys
    assert msg_key in keys

    # Two payloads must differ — proves channel_type is in the cache value
    # and key, not just the DTO.
    web_payload = redis._store[web_key]  # type: ignore[attr-defined]
    msg_payload = redis._store[msg_key]  # type: ignore[attr-defined]
    assert web_payload != msg_payload

    # Functional assertion — channel-A lookup never serves channel-B row.
    got_web = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "web"))
    got_msg = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "messenger"))
    assert got_web is not None and got_web.channel_type == "web"
    assert got_msg is not None and got_msg.channel_type == "messenger"


# ── Test 3 — unregistered channel returns None (no silent fallback) ───────


def test_unregistered_channel_returns_none_no_fallback_across_channels() -> None:
    """A tenant registered ``(bot_id="support", channel_type="web")`` only.
    A request arrives with ``channel_type="messenger"``. The registry MUST
    return ``None`` so the caller can produce HTTP 404
    ``bot_not_registered`` — it MUST NOT silently fall back to the web row.

    This is the strict 3-key contract: missing channel = missing row.
    Falling back would create a pseudo cross-channel leak (messenger users
    receive the web persona / prompt) and break per-channel UX guarantees.
    """
    cfg_web = _make_cfg(
        bot_id="support", channel_type="web", record_tenant_id=TEST_TENANT_UUID,
        system_prompt="WEB_ONLY",
    )
    svc = _make_service([cfg_web])
    asyncio.run(svc.bootstrap_cache())

    # The bot owner only registered ``web``. ``messenger`` must be missing.
    got_web = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "web"))
    got_msg = asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "messenger"))

    assert got_web is not None
    assert got_web.channel_type == "web"
    assert got_msg is None, (
        "unregistered channel must NOT fall back to another channel's bot — "
        "would violate 3-key identity and leak cross-channel persona"
    )


# ── Test 4 — empty channel_type rejected (defense-in-depth) ──────────────


def test_empty_channel_type_returns_none_does_not_match_any_row() -> None:
    """A buggy adapter passing empty / whitespace ``channel_type`` MUST NOT
    accidentally match any registered bot. The registry rejects the lookup
    rather than degrading to a 2-key match on ``(tenant_id, bot_id)``.
    """
    cfg = _make_cfg(
        bot_id="support", channel_type="web", record_tenant_id=TEST_TENANT_UUID,
        system_prompt="WEB",
    )
    svc = _make_service([cfg])
    asyncio.run(svc.bootstrap_cache())

    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "")) is None
    assert asyncio.run(svc.lookup(TEST_TENANT_UUID, _DEFAULT_WS, "support", "   ")) is None
