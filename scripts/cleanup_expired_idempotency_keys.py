#!/usr/bin/env python3
"""Nightly sweep — delete expired ``ingest_idempotency_keys`` rows.

Without this sweep the table grows unbounded — every BE-to-BE upload
adds one row, with a 24h default TTL. The unique constraint also
prevents a partner from re-using a key after the original ingest
expires unless the row is gone. The service has a fall-back code
path that deletes the row inline when an expired collision is
detected, but ops still want a global guarantee that the table size
matches the active retry window.

Invoked from cron / systemd timer:

    0 3 * * * /var/www/html/ragbot/.venv/bin/python \\
        /var/www/html/ragbot/scripts/cleanup_expired_idempotency_keys.py

The DELETE is bulk (no row-by-row fetch) so the cost stays bounded
even when the table has accumulated millions of expired rows. Logs a
single summary line so ops can attribute the sweep + spot anomalies.
"""

from __future__ import annotations

import asyncio
import os
import sys

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine

# Run from project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ragbot.infrastructure.db.models import IngestIdempotencyKeyModel  # noqa: E402

logger = structlog.get_logger(__name__)


async def main() -> int:
    """Delete rows whose ``expires_at`` is in the past.

    Returns 0 on success. Exit non-zero on DB connectivity failure so
    cron / systemd can alert.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2
    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            count_stmt = select(func.count()).select_from(
                IngestIdempotencyKeyModel,
            ).where(
                IngestIdempotencyKeyModel.expires_at < func.now(),
            )
            before = (await conn.execute(count_stmt)).scalar() or 0
            await conn.execute(
                delete(IngestIdempotencyKeyModel).where(
                    IngestIdempotencyKeyModel.expires_at < func.now(),
                )
            )
            logger.info(
                "ingest_idempotency_cleanup_done",
                deleted_rows=int(before),
            )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
