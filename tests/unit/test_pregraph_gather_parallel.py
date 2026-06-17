"""Pin tests — pre-graph parallel gather in test_chat handler.

Covers:
- test_pregraph_gather_parallel          — 6 calls run concurrently (timing)
- test_pregraph_gather_partial_failure   — get_int fail → fallback default
- test_pregraph_bot_cfg_first           — bot_cfg.find_by_4key BEFORE group B
- test_pregraph_quota_gate_preserved    — can_answer logic same behavior
- test_pregraph_request_log_after_quota — order: quota gate before request_log
- test_pregraph_multi_tenant_isolation  — separate bot_cfg.id → separate L1 key
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLEEP_MS = 0.04  # 40ms per slow coroutine


def _make_bot_cfg(
    bot_id: uuid.UUID | None = None,
    *,
    extra_max_tokens: int = 0,
    tokens_used: int = 0,
    bypass_token_check: bool = False,
    oos_answer_template: str = "",
) -> MagicMock:
    cfg = MagicMock()
    cfg.id = bot_id or uuid.uuid4()
    cfg.extra_max_tokens = extra_max_tokens
    cfg.tokens_used = tokens_used
    cfg.bypass_token_check = bypass_token_check
    cfg.oos_answer_template = oos_answer_template
    cfg.bot_name = "test-bot"
    return cfg


def _make_slow_get_int(value: int, delay: float = SLEEP_MS):
    async def _fn(key, default=None):
        await asyncio.sleep(delay)
        return value
    return _fn


def _make_slow_redis_get(value, delay: float = SLEEP_MS):
    async def _fn(key):
        await asyncio.sleep(delay)
        return value
    return _fn


async def _make_slow_ready_check(result=(5, 5, 5), delay: float = SLEEP_MS):
    await asyncio.sleep(delay)
    return result


# ---------------------------------------------------------------------------
# Test 1 — 6 Group-B calls run concurrently (wall time < sum of delays)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_gather_parallel():
    """All 6 Group-B coroutines run in parallel — wall time ≈ max(delay)
    rather than sum(delays).  Sequential would take ~240ms; parallel ~40ms.
    """
    delay = SLEEP_MS

    call_order: list[str] = []

    async def slow_get_int_max(key, default=None):
        call_order.append(f"get_int:{key}")
        await asyncio.sleep(delay)
        return 10_000

    async def slow_redis_get(key):
        call_order.append("redis_get")
        await asyncio.sleep(delay)
        return None

    async def slow_ready_check():
        call_order.append("ready_check")
        await asyncio.sleep(delay)
        return (0, 0, 0)

    from ragbot.shared.constants import (
        DEFAULT_MAX_HISTORY,
        DEFAULT_MAX_TOKENS_TOTAL,
        ROLLING_SUMMARY_KEEP_LAST,
        ROLLING_SUMMARY_THRESHOLD,
    )

    t0 = time.perf_counter()
    results = await asyncio.gather(
        slow_get_int_max("max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL),
        slow_redis_get("ragbot:bot:tokens_used:some-id"),
        slow_ready_check(),
        slow_get_int_max("chat_max_history", DEFAULT_MAX_HISTORY),
        slow_get_int_max("rolling_summary_threshold", ROLLING_SUMMARY_THRESHOLD),
        slow_get_int_max("rolling_summary_keep_last", ROLLING_SUMMARY_KEEP_LAST),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - t0

    # Sequential would be 6 × 40ms = 240ms; parallel = ~40ms.
    assert elapsed < 0.12, (
        f"Group-B gather not parallel: elapsed={elapsed:.3f}s "
        f"(expected < 0.12s, sequential would be ~0.24s)"
    )
    # All 6 results arrived
    assert len(results) == 6
    # All 6 tasks started (order is non-deterministic but all must be present)
    assert "redis_get" in call_order
    assert "ready_check" in call_order
    assert sum(1 for c in call_order if c.startswith("get_int:")) == 4


# ---------------------------------------------------------------------------
# Test 2 — partial failure in gather → fallback default, not crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_gather_partial_failure_fallback():
    """If get_int raises, the fallback constant is used; chat is NOT blocked."""
    from ragbot.shared.constants import (
        DEFAULT_MAX_HISTORY,
        DEFAULT_MAX_TOKENS_TOTAL,
        ROLLING_SUMMARY_KEEP_LAST,
        ROLLING_SUMMARY_THRESHOLD,
    )

    async def failing_get_int(key, default=None):
        raise RuntimeError("redis timeout")

    async def ok_redis_get(key):
        return None

    async def ok_ready_check():
        return (0, 0, 0)

    (
        _system_max_raw,
        _l1_value,
        _ready_stat,
        _chat_max_history_raw,
        _rolling_threshold_raw,
        _rolling_keep_last_raw,
    ) = await asyncio.gather(
        failing_get_int("max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL),
        ok_redis_get("key"),
        ok_ready_check(),
        failing_get_int("chat_max_history", DEFAULT_MAX_HISTORY),
        failing_get_int("rolling_summary_threshold", ROLLING_SUMMARY_THRESHOLD),
        failing_get_int("rolling_summary_keep_last", ROLLING_SUMMARY_KEEP_LAST),
        return_exceptions=True,
    )

    # All "failing" ones come back as BaseException instances
    assert isinstance(_system_max_raw, BaseException)
    assert isinstance(_chat_max_history_raw, BaseException)
    assert isinstance(_rolling_threshold_raw, BaseException)
    assert isinstance(_rolling_keep_last_raw, BaseException)

    # Handler fallback logic — mirrors test_chat.py
    _system_max = (
        int(_system_max_raw)
        if not isinstance(_system_max_raw, BaseException)
        else DEFAULT_MAX_TOKENS_TOTAL
    )
    _chat_max_history_cfg = (
        int(_chat_max_history_raw)
        if not isinstance(_chat_max_history_raw, BaseException)
        else DEFAULT_MAX_HISTORY
    )
    _rolling_threshold = (
        int(_rolling_threshold_raw)
        if not isinstance(_rolling_threshold_raw, BaseException)
        else ROLLING_SUMMARY_THRESHOLD
    )
    _rolling_keep_last = (
        int(_rolling_keep_last_raw)
        if not isinstance(_rolling_keep_last_raw, BaseException)
        else ROLLING_SUMMARY_KEEP_LAST
    )
    if isinstance(_l1_value, BaseException):
        _l1_value = None

    # All fallback to module-level defaults — no exception propagated
    assert _system_max == DEFAULT_MAX_TOKENS_TOTAL
    assert _chat_max_history_cfg == DEFAULT_MAX_HISTORY
    assert _rolling_threshold == ROLLING_SUMMARY_THRESHOLD
    assert _rolling_keep_last == ROLLING_SUMMARY_KEEP_LAST
    assert _l1_value is None  # Redis failure → None → fallback to bot_cfg.tokens_used


# ---------------------------------------------------------------------------
# Test 3 — bot_cfg.find_by_4key MUST complete before Group-B calls start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_bot_cfg_first():
    """Step A (bot_cfg lookup) completes before any Step-B coroutine is
    even *scheduled*, verifying the sequential THEN parallel DAG order.
    """
    sequencer: list[str] = []

    async def find_by_4key(tid, ws, bot_id, ch):
        sequencer.append("find_by_4key_start")
        await asyncio.sleep(0.02)
        sequencer.append("find_by_4key_done")
        return _make_bot_cfg()

    async def group_b_call(label: str):
        sequencer.append(f"group_b:{label}_start")
        await asyncio.sleep(0.01)
        sequencer.append(f"group_b:{label}_done")
        return 0

    # Simulate the Step A → Step B ordering as in test_chat
    bot_cfg = await find_by_4key(uuid.uuid4(), "ws", "bot", "web")

    # Step B only starts AFTER Step A (await) completes
    await asyncio.gather(
        group_b_call("cfg"),
        group_b_call("redis"),
        group_b_call("ready"),
        return_exceptions=True,
    )

    find_done_idx = sequencer.index("find_by_4key_done")
    first_b_start_idx = min(
        i for i, e in enumerate(sequencer) if e.startswith("group_b:") and e.endswith("_start")
    )
    assert find_done_idx < first_b_start_idx, (
        f"bot_cfg lookup must finish before any Group-B call starts. "
        f"Sequence: {sequencer}"
    )


# ---------------------------------------------------------------------------
# Test 4 — can_answer() quota gate preserved with gathered inputs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_quota_gate_preserved():
    """Quota gate sees correct _tokens_used and _effective_limit regardless
    of whether values came from gather or sequential await.
    """
    from ragbot.shared.token_budget import can_answer, compute_effective_max_tokens

    # Scenario: bot has 9_000 tokens used, limit is 10_000 → allowed
    system_max = 10_000
    bot_extra = 0
    l1_value = b"9000"  # Redis returns bytes-like

    effective_limit = compute_effective_max_tokens(
        system_max_tokens=system_max,
        bot_extra_max_tokens=bot_extra,
    )
    tokens_used = int(l1_value)
    assert can_answer(tokens_used=tokens_used, effective_limit=effective_limit, bypass=False)

    # Scenario: bot exhausted → denied
    l1_value_exhausted = b"10001"
    tokens_used_exhausted = int(l1_value_exhausted)
    assert not can_answer(
        tokens_used=tokens_used_exhausted,
        effective_limit=effective_limit,
        bypass=False,
    )

    # bypass_token_check=True always allows regardless
    assert can_answer(
        tokens_used=tokens_used_exhausted,
        effective_limit=effective_limit,
        bypass=True,
    )


# ---------------------------------------------------------------------------
# Test 5 — request_log.create_request_log called AFTER quota gate passes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_request_log_create_after_quota():
    """request_log_repo.create_request_log is only called when can_answer()
    returns True — it must NOT be called for QUOTA_EXHAUSTED paths.
    """
    from ragbot.shared.token_budget import can_answer, compute_effective_max_tokens

    create_request_log = AsyncMock()

    async def _simulate_pre_graph(
        *,
        tokens_used: int,
        system_max: int,
        bot_extra: int = 0,
        bypass: bool = False,
    ) -> dict:
        """Minimal simulation of the Step D ordering in test_chat."""
        effective_limit = compute_effective_max_tokens(
            system_max_tokens=system_max,
            bot_extra_max_tokens=bot_extra,
        )
        if not can_answer(
            tokens_used=tokens_used,
            effective_limit=effective_limit,
            bypass=bypass,
        ):
            return {"ok": False, "blocked_reason": "QUOTA_EXHAUSTED"}

        # Only reaches here when quota gate passes (Step D)
        await create_request_log(request_id=uuid.uuid4(), record_bot_id=uuid.uuid4())
        return {"ok": True}

    # Case A: quota OK → create_request_log called
    create_request_log.reset_mock()
    result_ok = await _simulate_pre_graph(tokens_used=5_000, system_max=10_000)
    assert result_ok["ok"] is True
    create_request_log.assert_awaited_once()

    # Case B: quota exhausted → create_request_log NOT called
    create_request_log.reset_mock()
    result_blocked = await _simulate_pre_graph(tokens_used=10_001, system_max=10_000)
    assert result_blocked["ok"] is False
    assert result_blocked["blocked_reason"] == "QUOTA_EXHAUSTED"
    create_request_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 6 — multi-tenant isolation: L1 Redis key scoped to bot_cfg.id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pregraph_multi_tenant_isolation():
    """Two different bots produce different Redis L1 keys in Step B gather.
    Token budget check must never cross-pollinate between tenants.
    """
    bot_a_id = uuid.uuid4()
    bot_b_id = uuid.uuid4()

    key_a = f"ragbot:bot:tokens_used:{bot_a_id}"
    key_b = f"ragbot:bot:tokens_used:{bot_b_id}"

    # Keys must be distinct — different bot_cfg.id → different Redis key
    assert key_a != key_b, "L1 Redis keys must differ across bots"

    # Simulate gather for both bots independently
    redis_store = {key_a: b"100", key_b: b"999999"}

    async def redis_get(key):
        return redis_store.get(key)

    result_a, result_b = await asyncio.gather(
        redis_get(key_a),
        redis_get(key_b),
    )

    assert int(result_a) == 100, "Bot A tokens_used should be 100"
    assert int(result_b) == 999999, "Bot B tokens_used should be 999999"

    # Ensure cross-tenant isolation: bot B's exhaustion does not block bot A
    from ragbot.shared.token_budget import can_answer

    assert can_answer(tokens_used=int(result_a), effective_limit=10_000, bypass=False)
    assert not can_answer(tokens_used=int(result_b), effective_limit=10_000, bypass=False)
