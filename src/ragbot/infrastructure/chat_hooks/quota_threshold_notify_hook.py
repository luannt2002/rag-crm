"""Hook stage=post_commit: fire operator webhook on quota exhausted.

Reads Redis L1 (just incremented by TokenUsageRedisHook). Throttle via
Redis SETNX 1h per bot — no spam.
"""
from __future__ import annotations
from typing import Any
import structlog
from sqlalchemy import text

from ragbot.application.events.chat_completed import ChatCompletedEvent
from ragbot.shared.token_budget import compute_effective_max_tokens, is_just_depleted
from ragbot.shared.constants import DEFAULT_MAX_TOKENS_TOTAL

logger = structlog.get_logger(__name__)


class QuotaThresholdNotifyHook:
    """Detect threshold crossing + fire one-shot notify."""

    REDIS_KEY_PREFIX = "ragbot:bot:tokens_used:"

    def __init__(self, redis_client: Any, notifier: Any, config_service: Any):
        self._redis = redis_client
        self._notifier = notifier
        self._cfg = config_service

    @property
    def hook_name(self) -> str:
        return "quota_threshold_notify"

    @property
    def stage(self) -> str:
        return "post_commit"

    async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
        # Read current tokens_used from Redis L1 (TokenUsageRedisHook just updated).
        key = f"{self.REDIS_KEY_PREFIX}{event.record_bot_id}"
        raw = await self._redis.get(key)
        tokens_used_after = int(raw or 0)
        tokens_used_before = tokens_used_after - event.tokens_used_delta

        # Resolve effective_limit (system_config + bot.extra_max_tokens)
        system_max = await self._cfg.get_int(
            "max_tokens_total", DEFAULT_MAX_TOKENS_TOTAL,
        )
        # Get bot.extra_max_tokens from just-committed row (same session).
        row = await session.execute(
            text("SELECT extra_max_tokens, bot_name, bypass_token_check FROM bots WHERE id = :bid"),
            {"bid": str(event.record_bot_id)},
        )
        bot_row = row.first()
        if bot_row is None:
            return  # Bot disappeared mid-flight, skip notify

        if bot_row.bypass_token_check:
            return  # VIP bot, skip quota notify entirely

        effective_limit = compute_effective_max_tokens(
            system_max_tokens=int(system_max),
            bot_extra_max_tokens=int(bot_row.extra_max_tokens),
        )

        if not is_just_depleted(
            tokens_used_before=tokens_used_before,
            tokens_used_after=tokens_used_after,
            effective_limit=effective_limit,
        ):
            return  # Not crossing threshold, no notify

        # Fire notify (throttled inside notifier)
        await self._notifier.send_quota_exhausted(
            record_tenant_id=event.record_tenant_id,
            record_bot_id=event.record_bot_id,
            bot_name=bot_row.bot_name or "",
            tokens_used=tokens_used_after,
            effective_limit=effective_limit,
        )
        logger.info(
            "quota_threshold_notify_fired",
            record_bot_id=str(event.record_bot_id),
            tokens_used=tokens_used_after,
            effective_limit=effective_limit,
        )
