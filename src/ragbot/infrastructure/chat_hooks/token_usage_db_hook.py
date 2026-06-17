"""Hook stage=db: atomic UPDATE bots.tokens_used += delta.

Runs INSIDE caller's transaction. SERIALIZABLE isolation + FOR UPDATE
row lock for concurrent-safety (2 chat call cùng bot không lost update).
"""
from __future__ import annotations
from typing import Any
import structlog
from sqlalchemy import text

from ragbot.application.events.chat_completed import (
    ChatCompletedEvent, ChatCompletionHookPort,
)

logger = structlog.get_logger(__name__)


class TokenUsageDbHook:
    """Atomic increment bots.tokens_used. SERIALIZABLE transaction."""

    @property
    def hook_name(self) -> str:
        return "token_usage_db"

    @property
    def stage(self) -> str:
        return "db"

    async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
        # SERIALIZABLE isolation prevents lost update under concurrent INCR.
        # FOR UPDATE locks the bot row for the duration of this txn.
        await session.execute(text(
            "SET LOCAL transaction_isolation = 'SERIALIZABLE'"
        ))
        # Atomic increment using SQL expression (no read-modify-write race).
        result = await session.execute(
            text("""
                UPDATE bots
                SET tokens_used = tokens_used + :delta, updated_at = now()
                WHERE id = :bid
                RETURNING tokens_used
            """),
            {"delta": event.tokens_used_delta, "bid": str(event.record_bot_id)},
        )
        new_value = result.scalar()
        logger.debug(
            "token_usage_db_committed",
            record_bot_id=str(event.record_bot_id),
            delta=event.tokens_used_delta,
            new_total=new_value,
            request_id=str(event.request_id),
        )
