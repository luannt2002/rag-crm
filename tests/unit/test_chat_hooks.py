"""Unit tests for chat completion hooks (db + redis + quota notify)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.events.chat_completed import ChatCompletedEvent
from ragbot.infrastructure.chat_hooks.quota_threshold_notify_hook import (
    QuotaThresholdNotifyHook,
)
from ragbot.infrastructure.chat_hooks.token_usage_db_hook import TokenUsageDbHook
from ragbot.infrastructure.chat_hooks.token_usage_redis_hook import (
    TokenUsageRedisHook,
)


def _make_event(*, delta: int = 100) -> ChatCompletedEvent:
    return ChatCompletedEvent(
        record_tenant_id=uuid4(),
        workspace_id="ws-test",
        bot_id="bot-test",
        channel_type="web",
        record_bot_id=uuid4(),
        request_id=uuid4(),
        prompt_tokens=40,
        completion_tokens=60,
        tokens_used_delta=delta,
        refusal_reason=None,
        intent="qa",
        timestamp_iso="2026-05-14T00:00:00Z",
    )


# ---------- TokenUsageDbHook -----------------------------------------------

@pytest.mark.asyncio
async def test_token_usage_db_hook_executes_atomic_update():
    """Hook MUST SET LOCAL isolation + run UPDATE bots ... += delta with right params."""
    event = _make_event(delta=250)
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar.return_value = 1250  # new tokens_used after increment
    session.execute = AsyncMock(return_value=result_mock)

    hook = TokenUsageDbHook()
    assert hook.hook_name == "token_usage_db"
    assert hook.stage == "db"

    await hook.run(event, session=session)

    # 2 calls: SET LOCAL isolation, then UPDATE
    assert session.execute.await_count == 2
    isolation_call = session.execute.await_args_list[0]
    update_call = session.execute.await_args_list[1]

    isolation_sql = str(isolation_call.args[0])
    assert "SERIALIZABLE" in isolation_sql
    assert "SET LOCAL" in isolation_sql

    update_sql = str(update_call.args[0])
    assert "UPDATE bots" in update_sql
    assert "tokens_used = tokens_used + :delta" in update_sql
    assert "RETURNING tokens_used" in update_sql

    params = update_call.args[1]
    assert params["delta"] == 250
    assert params["bid"] == str(event.record_bot_id)


# ---------- TokenUsageRedisHook --------------------------------------------

@pytest.mark.asyncio
async def test_token_usage_redis_hook_incrby_and_expire():
    """Hook MUST INCRBY by delta + set TTL on the per-bot key."""
    event = _make_event(delta=42)
    redis = MagicMock()
    redis.incrby = AsyncMock(return_value=142)
    redis.expire = AsyncMock(return_value=True)

    hook = TokenUsageRedisHook(redis_client=redis)
    assert hook.hook_name == "token_usage_redis"
    assert hook.stage == "post_commit"

    await hook.run(event, session=None)

    expected_key = f"ragbot:bot:tokens_used:{event.record_bot_id}"
    redis.incrby.assert_awaited_once_with(expected_key, 42)
    redis.expire.assert_awaited_once_with(expected_key, TokenUsageRedisHook.TTL_S)


# ---------- QuotaThresholdNotifyHook ---------------------------------------

class _CfgStub:
    def __init__(self, max_tokens_total: int):
        self._v = max_tokens_total

    async def get_int(self, key: str, default: int) -> int:
        if key == "max_tokens_total":
            return self._v
        return default


def _bot_row(*, extra_max_tokens: int = 0, bot_name: str = "TestBot",
             bypass_token_check: bool = False) -> Any:
    row = MagicMock()
    row.extra_max_tokens = extra_max_tokens
    row.bot_name = bot_name
    row.bypass_token_check = bypass_token_check
    return row


def _session_returning(row: Any) -> Any:
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)
    return session


@pytest.mark.asyncio
async def test_quota_notify_below_threshold_no_fire():
    """tokens_used_after < limit → no notify."""
    event = _make_event(delta=50)
    redis = MagicMock()
    # Redis says we are at 500 after the 50-delta increment (was 450).
    redis.get = AsyncMock(return_value=b"500")
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(_bot_row(extra_max_tokens=0))

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_notify_just_crossed_fires_once():
    """before < limit <= after → fire."""
    event = _make_event(delta=100)
    redis = MagicMock()
    # After=1050, Before=950, limit=1000 → just crossed.
    redis.get = AsyncMock(return_value=b"1050")
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(_bot_row(extra_max_tokens=0, bot_name="MyBot"))

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_awaited_once()
    kwargs = notifier.send_quota_exhausted.await_args.kwargs
    assert kwargs["record_tenant_id"] == event.record_tenant_id
    assert kwargs["record_bot_id"] == event.record_bot_id
    assert kwargs["bot_name"] == "MyBot"
    assert kwargs["tokens_used"] == 1050
    assert kwargs["effective_limit"] == 1000


@pytest.mark.asyncio
async def test_quota_notify_already_above_no_refire():
    """before >= limit → not "just crossed" → no notify."""
    event = _make_event(delta=100)
    redis = MagicMock()
    # Before=1100, After=1200, both above limit=1000 → no re-fire.
    redis.get = AsyncMock(return_value=b"1200")
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(_bot_row(extra_max_tokens=0))

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_notify_bypass_skips_entirely():
    """bypass_token_check=True → never notify even if crossed."""
    event = _make_event(delta=500)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"100000")
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(_bot_row(extra_max_tokens=0, bypass_token_check=True))

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_notify_bot_disappeared_returns_silently():
    """If bot row missing (deleted mid-flight) → no notify, no raise."""
    event = _make_event(delta=100)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"1500")
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(row=None)  # SELECT returned no row

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_notify_extra_max_tokens_raises_threshold():
    """bot.extra_max_tokens=500 → effective_limit=1500 → 1100 is still below."""
    event = _make_event(delta=100)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"1100")  # before=1000, after=1100; limit=1500
    notifier = MagicMock()
    notifier.send_quota_exhausted = AsyncMock()
    cfg = _CfgStub(max_tokens_total=1000)
    session = _session_returning(_bot_row(extra_max_tokens=500))

    hook = QuotaThresholdNotifyHook(redis_client=redis, notifier=notifier, config_service=cfg)
    await hook.run(event, session=session)

    notifier.send_quota_exhausted.assert_not_awaited()


# ---------- Port conformance ------------------------------------------------

def test_all_hooks_declare_required_port_attrs():
    """Each hook MUST expose hook_name (str) and stage ('db' or 'post_commit')."""
    db_hook = TokenUsageDbHook()
    redis_hook = TokenUsageRedisHook(redis_client=MagicMock())
    notify_hook = QuotaThresholdNotifyHook(
        redis_client=MagicMock(),
        notifier=MagicMock(),
        config_service=MagicMock(),
    )

    assert db_hook.hook_name == "token_usage_db" and db_hook.stage == "db"
    assert redis_hook.hook_name == "token_usage_redis" and redis_hook.stage == "post_commit"
    assert notify_hook.hook_name == "quota_threshold_notify" and notify_hook.stage == "post_commit"
