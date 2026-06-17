"""Janitor — sweep orphaned ``model_invocations`` rows stuck in ``running``.

Bug 1 P0 — second line of defense. After Sprint S/* the
``InvocationLogger`` was refactored to a single-session INSERT with the
final status (Option A): no row should ever land in ``status='running'``
on disk. This script exists to:

1. Clean up legacy rows from before the Option A migration.
2. Catch the edge case where a process is killed AFTER the
   ``invoke_model`` yield but BEFORE the final ``session.commit()`` —
   technically the row would NOT have been inserted in that window
   either (because we removed the upfront INSERT(running)), so this is
   purely defensive.
3. Catch any external writer that bypassed the wrapper and wrote a
   ``running`` row directly (audit guard).

Strategy: rows older than ``DEFAULT_INVOCATION_STUCK_TIMEOUT_S`` with
``status='running'`` are rewritten to ``status='failed'`` +
``finish_reason='janitor_timeout'`` and their ``finished_at`` set to
``now()``. Idempotent: re-running on the same data is a no-op.

CLI / cron entry::

    # Hourly is plenty given Option A — the wrapper itself prevents
    # most accumulation. Keep idempotent + bounded so it never page.
    0 * * * *  /path/to/venv/bin/python /path/to/repo/scripts/cleanup_stuck_invocations.py

Flags::

    --dry-run            Print what WOULD be updated; no writes.
    --timeout-seconds N  Override the staleness threshold
                         (default DEFAULT_INVOCATION_STUCK_TIMEOUT_S).

Exit code:
    0 always (errors are logged but never fail the cron job).

Domain neutral: no tenant literals; uses .env DATABASE_URL.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Allow `python scripts/cleanup_stuck_invocations.py` from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_INVOCATION_STUCK_TIMEOUT_S,
)

LOG = logging.getLogger("ragbot.cleanup_stuck_invocations")


async def cleanup_stuck_invocations(
    *, dry_run: bool, timeout_seconds: int
) -> dict[str, int]:
    """Mark every ``model_invocations`` row with ``status='running'`` that
    is older than ``timeout_seconds`` as failed/janitor_timeout.

    Returns: ``{"matched": int, "updated": int}``.
    """
    dsn = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC")
    if not dsn:
        raise RuntimeError("DATABASE_URL env var required")

    # Force the async dialect even if the env var was provisioned with
    # the sync URL form.
    if "+asyncpg" not in dsn and dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(dsn, future=True)
    try:
        async with engine.connect() as conn:
            count_row = await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM model_invocations
                    WHERE status = 'running'
                      AND started_at < now() - make_interval(secs => :secs)
                    """
                ),
                {"secs": timeout_seconds},
            )
            matched = int(count_row.scalar() or 0)

            if dry_run or matched == 0:
                LOG.info(
                    "janitor_dry_run_or_noop matched=%d dry_run=%s", matched, dry_run,
                )
                return {"matched": matched, "updated": 0}

            result = await conn.execute(
                text(
                    """
                    UPDATE model_invocations
                       SET status = 'failed',
                           finish_reason = 'janitor_timeout',
                           finished_at = now()
                     WHERE status = 'running'
                       AND started_at < now() - make_interval(secs => :secs)
                    """
                ),
                {"secs": timeout_seconds},
            )
            await conn.commit()
            updated = int(result.rowcount or 0)
            LOG.info(
                "janitor_updated matched=%d updated=%d threshold_s=%d",
                matched, updated, timeout_seconds,
            )
            return {"matched": matched, "updated": updated}
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep orphaned model_invocations rows stuck in 'running'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be updated without writing.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_INVOCATION_STUCK_TIMEOUT_S,
        help=(
            "Staleness threshold in seconds "
            f"(default {DEFAULT_INVOCATION_STUCK_TIMEOUT_S})."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    try:
        result = asyncio.run(
            cleanup_stuck_invocations(
                dry_run=args.dry_run,
                timeout_seconds=args.timeout_seconds,
            ),
        )
        LOG.info("janitor_done %s", result)
    except Exception as exc:  # noqa: BLE001 — janitor never fails cron
        LOG.exception("janitor_failed err=%s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
