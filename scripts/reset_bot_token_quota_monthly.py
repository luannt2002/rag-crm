"""Cron 00:01 day-1 each month — snapshot bots.tokens_used → log JSON → reset.

Schedule via systemd timer: deploy/ragbot-monthly-reset.timer

Logic:
  1. Compute PREV_MONTH_KEY = YYYY_MM của month just finished (timezone-aware)
  2. For each bot (is_deleted=false):
     a. UPSERT bot_token_usage_log: append usage_by_month[PREV_KEY] = tokens_used
     b. Reset bots.tokens_used = 0
     c. Bust Redis ragbot:bot:tokens_used:{id} key
  3. Per-bot try/except → 1 fail = log + continue, không kill batch.
  4. All commit at end (per bot, atomic).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text

from ragbot.bootstrap import Container
from ragbot.shared.constants import DEFAULT_TOKEN_QUOTA_RESET_TIMEZONE

logger = structlog.get_logger(__name__)


def compute_prev_month_key(*, tz_name: str = DEFAULT_TOKEN_QUOTA_RESET_TIMEZONE) -> str:
    """Cron fires 00:01 day-1 → return YYYY_MM của yesterday (prev month)."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    return f"{yesterday.year:04d}_{yesterday.month:02d}"


async def reset_all_bots(container: Container, prev_month_key: str) -> dict:
    session_factory = container.session_factory()

    errors: list[dict] = []
    processed = 0

    async with session_factory() as session:
        result = await session.execute(text(
            "SELECT id, record_tenant_id, workspace_id, bot_id, channel_type, tokens_used "
            "FROM bots WHERE is_deleted = false"
        ))
        bots = result.fetchall()

        for bot in bots:
            try:
                # UPSERT log row — append usage_by_month
                await session.execute(text("""
                    INSERT INTO bot_token_usage_log (
                        record_tenant_id, workspace_id, bot_id, channel_type,
                        record_bot_id, usage_by_month
                    ) VALUES (
                        :tenant_id, :workspace_id, :bot_id, :channel_type,
                        :record_bot_id,
                        jsonb_build_object(:month_key, :used::bigint)
                    )
                    ON CONFLICT (record_tenant_id, workspace_id, bot_id, channel_type)
                    DO UPDATE SET
                        usage_by_month = bot_token_usage_log.usage_by_month
                                       || jsonb_build_object(:month_key, :used::bigint),
                        updated_at = now()
                """), {
                    "tenant_id": str(bot.record_tenant_id),
                    "workspace_id": bot.workspace_id,
                    "bot_id": bot.bot_id,
                    "channel_type": bot.channel_type,
                    "record_bot_id": str(bot.id),
                    "month_key": prev_month_key,
                    "used": int(bot.tokens_used or 0),
                })
                # Reset tokens_used
                await session.execute(text(
                    "UPDATE bots SET tokens_used = 0, updated_at = now() WHERE id = :bid"
                ), {"bid": str(bot.id)})
                processed += 1
            except Exception as exc:  # noqa: BLE001 — cron top-level
                errors.append({"record_bot_id": str(bot.id), "error": str(exc)[:200]})
                logger.warning(
                    "monthly_reset_bot_failed",
                    record_bot_id=str(bot.id), error=str(exc)[:200],
                )

        await session.commit()

    # Bust Redis L1 token cache (next chat reads fresh tokens_used=0)
    try:
        redis = container.redis_client()
        async for k in redis.scan_iter(match="ragbot:bot:tokens_used:*"):
            await redis.delete(k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("monthly_reset_redis_bust_failed", error=str(exc)[:200])

    logger.info(
        "monthly_reset_complete",
        prev_month_key=prev_month_key,
        processed=processed,
        errors=len(errors),
    )
    return {"processed": processed, "errors": errors, "prev_month_key": prev_month_key}


async def main() -> int:
    container = Container()
    config_service = container.system_config_service()
    tz_name_raw = await config_service.get(
        "token_quota_reset_timezone", DEFAULT_TOKEN_QUOTA_RESET_TIMEZONE,
    )
    tz_name = (
        tz_name_raw if isinstance(tz_name_raw, str) else DEFAULT_TOKEN_QUOTA_RESET_TIMEZONE
    ).strip('"')

    prev_key = compute_prev_month_key(tz_name=tz_name)
    result = await reset_all_bots(container, prev_month_key=prev_key)
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
