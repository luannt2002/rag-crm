"""Cron */5 min — re-sync Redis L1 ← DB SSoT. Drift safety net.

If Redis cold-restart or eviction caused token_used counter loss,
this cron rehydrates from DB within 5 min window.
"""
from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text

from ragbot.bootstrap import Container

logger = structlog.get_logger(__name__)


async def reconcile() -> dict:
    container = Container()
    sf = container.session_factory()
    redis = container.redis_client()

    synced = 0
    errors = 0

    async with sf() as session:
        result = await session.execute(text(
            "SELECT id, tokens_used FROM bots WHERE is_deleted = false"
        ))
        bots = result.fetchall()

    for bot in bots:
        try:
            key = f"ragbot:bot:tokens_used:{bot.id}"
            # SET overrides — DB is SSoT, Redis is cache.
            await redis.set(key, int(bot.tokens_used or 0), ex=60)
            synced += 1
        except Exception as exc:  # noqa: BLE001 — cron top-level
            errors += 1
            logger.warning(
                "reconcile_bot_failed",
                record_bot_id=str(bot.id), error=str(exc)[:200],
            )

    logger.info("reconcile_complete", synced=synced, errors=errors)
    return {"synced": synced, "errors": errors}


if __name__ == "__main__":
    asyncio.run(reconcile())
