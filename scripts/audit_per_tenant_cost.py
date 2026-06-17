#!/usr/bin/env python3
"""Per-tenant monthly token cost-cap audit (read-only).

Aggregates ``request_logs.total_tokens`` per tenant over a trailing
window and prints a markdown table of tenants flagged at the warn or
exceed threshold. Designed for ops cron + on-demand SRE drilldown — the
script never writes to the database.

Exit codes:
  * 0 — no tenant exceeded its monthly cap (warnings allowed).
  * 1 — at least one tenant exceeded its monthly cap.

Usage::

    python scripts/audit_per_tenant_cost.py
    python scripts/audit_per_tenant_cost.py --since-days 30 \
        --warn-ratio 0.8 --exceed-ratio 1.0
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.application.services.cost_cap_alerter import (
    COST_CAP_EXCEEDED_EVENT,
    CostCapEvent,
    evaluate_tenants,
)
from ragbot.shared.constants import (
    DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS,
    DEFAULT_COST_CAP_EXCEED_RATIO,
    DEFAULT_COST_CAP_WARN_RATIO,
)

_TABLE_HEADER = (
    "| tenant | tenant_id | used_tokens | quota_tokens | ratio | severity |\n"
    "|---|---|---:|---:|---:|---|"
)


def _format_row(evt: CostCapEvent) -> str:
    return (
        f"| {evt.tenant_name} | {evt.record_tenant_id} "
        f"| {evt.used_tokens} | {evt.quota_tokens} "
        f"| {evt.ratio:.4f} | {evt.severity} |"
    )


async def _run(
    *,
    since_days: int,
    warn_ratio: float,
    exceed_ratio: float,
) -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2

    engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    logger = structlog.get_logger("ragbot.audit_per_tenant_cost")
    try:
        async with factory() as session:
            events = await evaluate_tenants(
                session=session,
                logger=logger,
                since_days=since_days,
                warn_ratio=warn_ratio,
                exceed_ratio=exceed_ratio,
            )
    finally:
        await engine.dispose()

    if not events:
        print(
            f"No tenants flagged (window={since_days}d, "
            f"warn={warn_ratio}, exceed={exceed_ratio}).",
        )
        return 0

    # Stable ordering: highest ratio first → operators triage worst case.
    events_sorted = sorted(events, key=lambda e: e.ratio, reverse=True)
    print(_TABLE_HEADER)
    for evt in events_sorted:
        print(_format_row(evt))

    has_exceed = any(e.severity == COST_CAP_EXCEEDED_EVENT for e in events_sorted)
    return 1 if has_exceed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-days",
        type=int,
        default=DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS,
        help=(
            "Trailing window in days for the usage aggregate "
            f"(default: {DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS})."
        ),
    )
    parser.add_argument(
        "--warn-ratio",
        type=float,
        default=DEFAULT_COST_CAP_WARN_RATIO,
        help=(
            "Ratio (used/quota) at/above which to flag cost_cap_warning "
            f"(default: {DEFAULT_COST_CAP_WARN_RATIO})."
        ),
    )
    parser.add_argument(
        "--exceed-ratio",
        type=float,
        default=DEFAULT_COST_CAP_EXCEED_RATIO,
        help=(
            "Ratio at/above which to flag cost_cap_exceeded "
            f"(default: {DEFAULT_COST_CAP_EXCEED_RATIO})."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(
        _run(
            since_days=args.since_days,
            warn_ratio=args.warn_ratio,
            exceed_ratio=args.exceed_ratio,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
