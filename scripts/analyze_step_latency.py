#!/usr/bin/env python3
"""Per-step latency p50/p95/p99 analytics from ``request_steps``.

Sprint 0 / MoM 00c-analytics — read-only observability tool. Identifies
the slowest pipeline steps so Sprint planning targets the bottleneck
node, not a guess. Mirrors the OBSERVABILITY-MATRIX contract: each
new feature owns its own ``step_name`` row in ``request_steps`` and
this script aggregates them.

Pattern: per-step latency dashboard for adaptive RAG pipelines.
Reference: internal observability tooling per
``plans/260514-master-of-master/OBSERVABILITY-MATRIX.md`` §1-§2.
Inspired by NVIDIA RAG Blueprint per-stage timing panels.

Sacred: read-only. No DB writes. No LLM calls. Never touches request
answers — telemetry only. Domain-neutral: no brand / industry literals.
All thresholds + window defaults sourced from
``ragbot.shared.constants`` (zero hardcode rule).

Usage:
    # Last 24h, all bots, ASCII table to stdout
    python scripts/analyze_step_latency.py

    # Last 7 days for one bot, JSON report
    python scripts/analyze_step_latency.py \\
        --hours 168 --bot-id <record_bot_id> --out report.json

    # Constrain to one tenant + workspace (4-key identity)
    python scripts/analyze_step_latency.py \\
        --record-tenant-id <uuid> --workspace-id <slug>

Exit codes:
    0  success
    2  missing DATABASE_URL / psycopg
    3  invalid CLI arg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_STEP_LATENCY_WINDOW_HOURS,
    MAX_STEP_LATENCY_WINDOW_HOURS,
)

# Percentile targets the script reports. Standard SRE percentiles
# (p50 median, p95 tail, p99 worst-case). Not configurable because
# every dashboard / SLO downstream uses these three buckets.
REPORTED_PERCENTILES: tuple[float, ...] = (0.50, 0.95, 0.99)


def _connect(dsn: str | None):
    """Late-import psycopg so help/parse works without DATABASE_URL set."""
    if not dsn:
        sys.stderr.write(
            "DATABASE_URL env var required (read-only access to request_steps).\n"
        )
        sys.exit(2)
    try:
        import psycopg
    except ImportError:
        sys.stderr.write("psycopg not installed — pip install psycopg[binary]\n")
        sys.exit(2)
    return psycopg.connect(dsn)


def _percentile(sorted_vals: list[int], pct: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. Empty → 0."""
    if not sorted_vals:
        return 0
    if pct <= 0:
        return sorted_vals[0]
    if pct >= 1:
        return sorted_vals[-1]
    # Nearest-rank: ceil(pct * n) - 1, bounded.
    idx = int(pct * len(sorted_vals))
    if idx >= len(sorted_vals):
        idx = len(sorted_vals) - 1
    return sorted_vals[idx]


def _parse_uuid(value: str, name: str) -> str:
    try:
        UUID(value)
    except (ValueError, TypeError):
        sys.stderr.write(f"--{name} must be a UUID, got: {value!r}\n")
        sys.exit(3)
    return value


def fetch_step_rows(
    conn,
    *,
    hours: int,
    bot_id: str | None,
    record_tenant_id: str | None,
    workspace_id: str | None,
) -> list[tuple[str, int, str, str | None]]:
    """Pull (step_name, duration_ms, status, feature_flag) rows.

    Filters by lookback window + optional 4-key identity slice. The
    ``feature_flag`` lives in ``metadata_json->>'feature_flag'`` per
    the OBSERVABILITY-MATRIX contract — None when the step pre-dates
    the feature-flag instrumentation.

    Joins ``request_logs`` only when ``--bot-id`` is provided, because
    ``request_steps.record_bot_id`` is not on the table (the bot is
    resolved through the parent request).
    """
    params: dict[str, Any] = {"hours": hours}
    where_extra = ""
    join_extra = ""

    if record_tenant_id is not None:
        where_extra += " AND rs.record_tenant_id = %(record_tenant_id)s"
        params["record_tenant_id"] = record_tenant_id
    if workspace_id is not None:
        where_extra += " AND rs.workspace_id = %(workspace_id)s"
        params["workspace_id"] = workspace_id
    if bot_id is not None:
        join_extra = (
            " JOIN request_logs rl "
            "   ON rl.request_id = rs.record_request_id"
        )
        where_extra += " AND rl.record_bot_id = %(bot_id)s"
        params["bot_id"] = bot_id

    sql = (
        "SELECT rs.step_name, rs.duration_ms, rs.status, "
        "       rs.metadata_json->>'feature_flag' AS feature_flag "
        "FROM request_steps rs"
        f"{join_extra} "
        "WHERE rs.started_at > NOW() - (%(hours)s * INTERVAL '1 hour')"
        f"{where_extra}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def aggregate(
    rows: list[tuple[str, int, str, str | None]],
) -> dict[str, dict[str, Any]]:
    """Group rows by ``step_name`` and compute count/percentiles/error rate."""
    per_step_durations: dict[str, list[int]] = defaultdict(list)
    per_step_errors: dict[str, int] = defaultdict(int)
    per_step_flag: dict[str, str | None] = {}

    for step_name, duration_ms, status, feature_flag in rows:
        if duration_ms is None:
            duration_ms = 0
        per_step_durations[step_name].append(int(duration_ms))
        if status not in ("success", "ok"):
            per_step_errors[step_name] += 1
        # First non-null flag wins; flags are stable per step_name.
        if feature_flag and step_name not in per_step_flag:
            per_step_flag[step_name] = feature_flag

    out: dict[str, dict[str, Any]] = {}
    for step_name, durations in per_step_durations.items():
        durations.sort()
        n = len(durations)
        percentile_map = {
            f"p{int(pct * 100)}_ms": _percentile(durations, pct)
            for pct in REPORTED_PERCENTILES
        }
        out[step_name] = {
            "count": n,
            "error_count": per_step_errors[step_name],
            "error_rate_pct": (
                (per_step_errors[step_name] / n) * 100 if n else 0
            ),
            "feature_flag": per_step_flag.get(step_name),
            **percentile_map,
        }
    return out


def render_ascii(per_step: dict[str, dict[str, Any]]) -> str:
    """Format the aggregate as a fixed-width ASCII table sorted by p95 desc."""
    if not per_step:
        return "(no request_steps rows in window)\n"

    rows_sorted = sorted(
        per_step.items(), key=lambda kv: kv[1]["p95_ms"], reverse=True,
    )
    header = (
        f"{'step_name':<40} {'count':>8} {'p50_ms':>8} "
        f"{'p95_ms':>8} {'p99_ms':>8} {'err%':>6}  feature_flag"
    )
    lines = [header, "-" * len(header)]
    for step_name, stats in rows_sorted:
        flag = stats["feature_flag"] or "-"
        lines.append(
            f"{step_name:<40} {stats['count']:>8} "
            f"{stats['p50_ms']:>8} {stats['p95_ms']:>8} "
            f"{stats['p99_ms']:>8} {stats['error_rate_pct']:>5.1f}  {flag}"
        )
    return "\n".join(lines) + "\n"


def build_report(
    per_step: dict[str, dict[str, Any]],
    *,
    hours: int,
    bot_id: str | None,
    record_tenant_id: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "window_hours": hours,
        "filter": {
            "bot_id": bot_id,
            "record_tenant_id": record_tenant_id,
            "workspace_id": workspace_id,
        },
        "reported_percentiles": [int(p * 100) for p in REPORTED_PERCENTILES],
        "step_count": len(per_step),
        "total_rows": sum(s["count"] for s in per_step.values()),
        "per_step": per_step,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-step latency p50/p95/p99 from request_steps",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_STEP_LATENCY_WINDOW_HOURS,
        help=(
            "Lookback window in hours "
            f"(default {DEFAULT_STEP_LATENCY_WINDOW_HOURS}, "
            f"max {MAX_STEP_LATENCY_WINDOW_HOURS})"
        ),
    )
    parser.add_argument(
        "--bot-id",
        default=None,
        help="Filter to one bot (record_bot_id UUID on request_logs)",
    )
    parser.add_argument(
        "--record-tenant-id",
        default=None,
        help="Filter to one tenant (record_tenant_id UUID)",
    )
    parser.add_argument(
        "--workspace-id",
        default=None,
        help="Filter to one workspace slug",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write JSON report to this path (default: stdout ASCII only)",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Override DATABASE_URL (read-only access is sufficient)",
    )
    args = parser.parse_args(argv)

    if args.hours <= 0:
        sys.stderr.write("--hours must be positive\n")
        return 3
    if args.hours > MAX_STEP_LATENCY_WINDOW_HOURS:
        sys.stderr.write(
            f"--hours capped at {MAX_STEP_LATENCY_WINDOW_HOURS} "
            f"(request_steps does not have a covering index for wider scans)\n"
        )
        return 3

    if args.bot_id is not None:
        args.bot_id = _parse_uuid(args.bot_id, "bot-id")
    if args.record_tenant_id is not None:
        args.record_tenant_id = _parse_uuid(
            args.record_tenant_id, "record-tenant-id",
        )

    dsn = args.dsn or os.getenv("DATABASE_URL")
    conn = _connect(dsn)
    try:
        rows = fetch_step_rows(
            conn,
            hours=args.hours,
            bot_id=args.bot_id,
            record_tenant_id=args.record_tenant_id,
            workspace_id=args.workspace_id,
        )
    finally:
        conn.close()

    per_step = aggregate(rows)
    sys.stdout.write(render_ascii(per_step))

    if args.out:
        report = build_report(
            per_step,
            hours=args.hours,
            bot_id=args.bot_id,
            record_tenant_id=args.record_tenant_id,
            workspace_id=args.workspace_id,
        )
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8",
        )
        sys.stderr.write(f"wrote JSON report → {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
