"""Phase 14 — seed ``bots.rerank_intent_whitelist`` with the recommended starter set.

Source: ``reports/TOP_SCORE_BOOST_ANALYSIS_20260430.md`` — Jina rerank
measurably lifts top_score on factoid / comparison / aggregation /
booking / yesno (+0.20..+0.45) but barely moves chitchat / off_topic /
vu_vo / hallucination_trap (+0.00..+0.18). Skipping rerank on the latter
saves ~150ms per turn + Jina cost without changing answer quality.

This script applies the recommended starter whitelist
(``DEFAULT_RERANK_WHITELIST_INTENTS``) to ONE bot or ALL active bots.

Usage:
    # Apply to one bot by record_bot_id (UUID PK):
    python -m scripts.seed_rerank_intent_whitelist --record-bot-id <UUID>

    # Apply to all active bots that currently have a NULL whitelist:
    python -m scripts.seed_rerank_intent_whitelist --all

    # Override the intent set (comma-separated). Domain-neutral — no brand
    # literal in the script; operator picks intents per their data:
    python -m scripts.seed_rerank_intent_whitelist --record-bot-id <UUID> \\
        --intents factoid,comparison,booking

    # Dry-run (print only, no writes):
    python -m scripts.seed_rerank_intent_whitelist --all --dry-run

After seeding, the operator should also call
``bot_registry_service.invalidate(...)`` (or restart the worker) so the
Redis cache picks up the new whitelist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update

from ragbot.application.dto.bot_config import RerankIntentWhitelist
from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.infrastructure.db.models import BotModel
from ragbot.shared.constants import (
    DEFAULT_RERANK_INTENT_WHITELIST_ENABLED,
    DEFAULT_RERANK_WHITELIST_INTENTS,
)


logger = structlog.get_logger(__name__)


def _parse_intents(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated CLI flag into a clean intent tuple."""
    if not raw:
        return DEFAULT_RERANK_WHITELIST_INTENTS
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p)


def _build_payload(intents: tuple[str, ...], enabled: bool) -> dict[str, Any]:
    """Validate via Pydantic before serialising — symmetric with load path."""
    wl = RerankIntentWhitelist(enabled=enabled, intents=intents)
    return wl.model_dump()


async def _seed_one(
    container: Container,
    *,
    record_bot_id: UUID,
    payload: dict[str, Any],
    dry_run: bool,
) -> bool:
    """Update one bot's ``rerank_intent_whitelist`` column. Idempotent."""
    session_factory = container.session_factory()
    async with session_factory() as session:
        row = (await session.execute(
            select(BotModel).where(BotModel.id == record_bot_id),
        )).scalar_one_or_none()
        if row is None:
            logger.warning("seed_skip_bot_not_found", record_bot_id=str(record_bot_id))
            return False
        before = row.rerank_intent_whitelist
        if before == payload:
            logger.info(
                "seed_skip_unchanged",
                record_bot_id=str(record_bot_id),
                bot_id=row.bot_id,
            )
            return False
        if dry_run:
            logger.info(
                "seed_dry_run",
                record_bot_id=str(record_bot_id),
                bot_id=row.bot_id,
                before=before,
                after=payload,
            )
            return True
        await session.execute(
            update(BotModel)
            .where(BotModel.id == record_bot_id)
            .values(rerank_intent_whitelist=payload),
        )
        await session.commit()
        logger.info(
            "seed_applied",
            record_bot_id=str(record_bot_id),
            bot_id=row.bot_id,
            payload=payload,
        )
        return True


async def _seed_all(
    container: Container,
    *,
    payload: dict[str, Any],
    dry_run: bool,
    only_null: bool,
) -> int:
    """Seed every active bot. Returns count of rows changed."""
    session_factory = container.session_factory()
    async with session_factory() as session:
        stmt = select(BotModel).where(BotModel.is_deleted.is_(False))
        if only_null:
            stmt = stmt.where(BotModel.rerank_intent_whitelist.is_(None))
        rows = (await session.execute(stmt)).scalars().all()

    changed = 0
    for row in rows:
        applied = await _seed_one(
            container,
            record_bot_id=row.id,
            payload=payload,
            dry_run=dry_run,
        )
        if applied:
            changed += 1
    return changed


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed bots.rerank_intent_whitelist with the recommended "
                    "Jina-boost-positive intent set.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--record-bot-id",
        type=UUID,
        help="Apply to a single bot by internal UUID PK.",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help="Apply to every active bot (filtered to NULL whitelists by default).",
    )
    parser.add_argument(
        "--intents",
        default=None,
        help=(
            "Comma-separated intent override. Default: "
            f"{','.join(DEFAULT_RERANK_WHITELIST_INTENTS)}"
        ),
    )
    parser.add_argument(
        "--enabled",
        choices=("true", "false"),
        default=str(DEFAULT_RERANK_INTENT_WHITELIST_ENABLED).lower(),
        help="Set the ``enabled`` flag (default: %(default)s).",
    )
    parser.add_argument(
        "--include-non-null",
        action="store_true",
        help="With --all, also overwrite bots that already have a whitelist set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended changes without writing.",
    )
    return parser


async def main_async(args: argparse.Namespace) -> int:
    setup_logging()
    container = Container()
    container.wire(modules=[__name__])

    intents = _parse_intents(args.intents)
    enabled = args.enabled.lower() == "true"
    payload = _build_payload(intents, enabled)
    logger.info(
        "seed_starting",
        intents=list(intents),
        enabled=enabled,
        dry_run=args.dry_run,
    )

    if args.record_bot_id is not None:
        applied = await _seed_one(
            container,
            record_bot_id=args.record_bot_id,
            payload=payload,
            dry_run=args.dry_run,
        )
        return 0 if applied else 1

    changed = await _seed_all(
        container,
        payload=payload,
        dry_run=args.dry_run,
        only_null=not args.include_non_null,
    )
    logger.info("seed_complete", rows_changed=changed)
    return 0


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
