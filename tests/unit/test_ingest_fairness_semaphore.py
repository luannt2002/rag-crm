"""Ingest fairness semaphore — keyed by (bot+channel) and workspace (NOT tenant).

2026-06-13 owner spec: a single ``(bot_id, channel_type)`` may hold up to
``DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL`` (5) slots; a single ``workspace_id``
up to ``DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE`` (10). Workspace sem is acquired
OUTSIDE the bot+channel sem, both INSIDE the global handler budget — so one
noisy bot can't starve sibling bots of the same workspace, and a noisy
workspace can't starve others. Tenant is NOT a fairness key anymore.
"""

from __future__ import annotations

import asyncio

from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus
from ragbot.shared.constants import (
    DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL,
    DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE,
    DEFAULT_BUS_TENANT_SEM_MAX,
)


def _bus() -> RedisStreamsEventBus:
    return RedisStreamsEventBus(client=object())  # type: ignore[arg-type]


def test_fairness_keys_extract_bot_channel_and_workspace() -> None:
    bus = _bus()
    data = {b"payload": b'{"record_bot_id":"bot1","channel_type":"web","workspace_id":"ws9"}'}
    bc, ws = bus._fairness_keys(data)
    assert bc == "bot1:web"
    assert ws == "ws9"


def test_fairness_keys_handles_str_payload_and_bot_id_alias() -> None:
    bus = _bus()
    bc, ws = bus._fairness_keys({"payload": '{"bot_id":"b2","channel_type":"zalo","workspace_id":"w2"}'})
    assert bc == "b2:zalo"
    assert ws == "w2"


def test_fairness_keys_missing_fields_fall_back() -> None:
    bus = _bus()
    bc, ws = bus._fairness_keys({b"payload": b'{"x":1}'})
    assert bc == bus._FAIRNESS_NO_TENANT_KEY
    assert ws == bus._FAIRNESS_NO_TENANT_KEY
    assert bus._fairness_keys({}) == (bus._FAIRNESS_NO_TENANT_KEY, bus._FAIRNESS_NO_TENANT_KEY)


def test_fairness_keys_malformed_payload_falls_back() -> None:
    bus = _bus()
    bc, ws = bus._fairness_keys({b"payload": b"not-json{"})
    assert bc == bus._FAIRNESS_NO_TENANT_KEY
    assert ws == bus._FAIRNESS_NO_TENANT_KEY


def test_bot_channel_semaphore_limit_5_stable_isolated() -> None:
    bus = _bus()
    a1 = bus._fairness_semaphore(bus._bot_channel_sems, "bot1:web", DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL)
    a2 = bus._fairness_semaphore(bus._bot_channel_sems, "bot1:web", DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL)
    b = bus._fairness_semaphore(bus._bot_channel_sems, "bot1:zalo", DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL)
    assert a1 is a2 and a1 is not b
    assert a1._value == DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL == 5


def test_workspace_semaphore_limit_10() -> None:
    bus = _bus()
    w = bus._fairness_semaphore(bus._workspace_sems, "ws1", DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE)
    assert w._value == DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE == 10


def test_registry_bounded_with_overflow() -> None:
    bus = _bus()
    for i in range(DEFAULT_BUS_TENANT_SEM_MAX):
        bus._fairness_semaphore(bus._bot_channel_sems, f"b{i}:web", 5)
    o1 = bus._fairness_semaphore(bus._bot_channel_sems, "ov1:web", 5)
    o2 = bus._fairness_semaphore(bus._bot_channel_sems, "ov2:web", 5)
    assert o1 is o2
    assert len(bus._bot_channel_sems) <= DEFAULT_BUS_TENANT_SEM_MAX + 1


def test_noisy_bot_does_not_block_sibling_bot_same_workspace() -> None:
    """Noisy bot floods >5 tasks; a sibling bot (same workspace) still runs —
    the flooder's overflow tasks block on its own 5-slot bot+channel sem
    without holding a workspace slot."""
    bus = _bus()
    global_sem = asyncio.Semaphore(DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE)
    sibling_ran = asyncio.Event()

    async def _task(bc_key: str, hold: asyncio.Event | None) -> None:
        ws_sem = bus._fairness_semaphore(bus._workspace_sems, "ws", DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE)
        bc_sem = bus._fairness_semaphore(bus._bot_channel_sems, bc_key, DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL)
        async with ws_sem, bc_sem, global_sem:
            if bc_key == "sibling:web":
                sibling_ran.set()
            if hold is not None:
                await hold.wait()

    async def _run() -> bool:
        release = asyncio.Event()
        noisy = [asyncio.create_task(_task("noisy:web", release))
                 for _ in range(DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL + 3)]
        await asyncio.sleep(0)
        sib = asyncio.create_task(_task("sibling:web", None))
        try:
            await asyncio.wait_for(sibling_ran.wait(), timeout=1.0)
            ran = True
        except asyncio.TimeoutError:
            ran = False
        release.set()
        await asyncio.gather(*noisy, sib, return_exceptions=True)
        return ran

    assert asyncio.run(_run()), "sibling bot starved — bot+channel sem must be OUTSIDE global"
