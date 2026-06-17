"""Hook stage=post_commit: increment Redis L1 counter for fast gate.

Runs AFTER DB commit succeeds. Failure here = brief drift, recovered
by reconciliation cron (5min).
"""
from __future__ import annotations
from typing import Any
import structlog

from ragbot.application.events.chat_completed import ChatCompletedEvent

logger = structlog.get_logger(__name__)


class TokenUsageRedisHook:
    """INCR Redis tokens_used counter post-commit."""

    REDIS_KEY_PREFIX = "ragbot:bot:tokens_used:"
    TTL_S = 60  # force re-sync with DB after 60s — drift safety

    def __init__(self, redis_client: Any):
        self._redis = redis_client

    @property
    def hook_name(self) -> str:
        return "token_usage_redis"

    @property
    def stage(self) -> str:
        return "post_commit"

    async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
        key = f"{self.REDIS_KEY_PREFIX}{event.record_bot_id}"
        new_value = await self._redis.incrby(key, event.tokens_used_delta)
        await self._redis.expire(key, self.TTL_S)
        logger.debug(
            "token_usage_redis_incremented",
            record_bot_id=str(event.record_bot_id),
            delta=event.tokens_used_delta,
            redis_value=new_value,
            request_id=str(event.request_id),
        )
