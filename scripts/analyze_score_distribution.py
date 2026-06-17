#!/usr/bin/env python3
"""Stream V Phase 1 — score-distribution analyser for per-bot threshold tuning.

V13 over-refuse cluster B (8 turns at top_score 0.07-0.08 blocked) is a
threshold-misalignment symptom, not a model-quality problem. Before
shipping any runtime change to ``reranker_min_score_active`` or
``grounding_check_threshold``, owner needs to see the *actual* score
distribution per bot so the threshold pick is data-driven, not magic.

This script reads ``request_steps`` rows for a given bot + window,
computes a histogram of ``top_score`` (and optional reranker score),
and recommends a threshold that admits ≥ 80% of historically-answered
turns while still rejecting low-confidence chunks.

Sacred: read-only. No DB writes. No LLM calls. No runtime behaviour
change — owner uses output to manually update
``system_config.reranker_min_score_active`` (or per-bot override
when that schema lands later in Stream V).

Usage:
    python scripts/analyze_score_distribution.py --bot-id 4d741129-... --days 7
    python scripts/analyze_score_distribution.py --bot-id <uuid> --metric reranker_score
    python scripts/analyze_score_distribution.py --bot-id <uuid> --buckets 20 --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Histogram buckets default — 20 covers 0..1 in 0.05 increments which
# matches retrieval score granularity (top_score, cosine, RRF).
DEFAULT_BUCKETS = 20
DEFAULT_DAYS = 7
DEFAULT_PERCENTILE_TARGET = 80  # admit ≥ 80% of "answered" turns


def _connect(dsn: str | None):
    """Late-import psycopg so the script imports cleanly without DATABASE_URL."""
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


def _bucket_for(score: float, n_buckets: int) -> int:
    if score <= 0:
        return 0
    if score >= 1:
        return n_buckets - 1
    return int(score * n_buckets)


def _bucket_label(idx: int, n_buckets: int) -> str:
    lo = idx / n_buckets
    hi = (idx + 1) / n_buckets
    return f"[{lo:.3f}, {hi:.3f})"


def fetch_scores(conn, bot_id: str, days: int, metric: str) -> list[tuple[float, str]]:
    """Pull (score, status) pairs from request_steps + request_logs.

    Honest about the data available: ``request_steps`` writes the
    ``filter_min_score`` step row with metadata but the *post-rerank*
    top_score is what we want for threshold tuning. We pull from
    ``ragbot.request_steps`` joined to ``ragbot.request_logs`` for
    answer/refuse status. Schema is read-only.
    """
    sql = """
        SELECT
            (rs.metadata_json->>'top_score_post_rerank')::float AS top_score,
            COALESCE(rl.answer_type, 'unknown') AS status
        FROM ragbot.request_steps rs
        JOIN ragbot.request_logs rl USING (request_id)
        WHERE rs.step_name = 'filter_min_score'
          AND rl.record_bot_id = %s
          AND rs.created_at > now() - interval '%s days'
          AND (rs.metadata_json->>'top_score_post_rerank') IS NOT NULL
    """
    if metric == "reranker_score":
        sql = sql.replace("top_score_post_rerank", "min_score_threshold")
    rows = []
    with conn.cursor() as cur:
        cur.execute(sql, (bot_id, days))
        for top_score, status in cur.fetchall():
            if top_score is None:
                continue
            rows.append((float(top_score), status or "unknown"))
    return rows


def histogram(scores: list[tuple[float, str]], n_buckets: int) -> dict[int, Counter]:
    """Return {bucket_idx: Counter(status -> count)}."""
    out: dict[int, Counter] = {}
    for score, status in scores:
        b = _bucket_for(score, n_buckets)
        out.setdefault(b, Counter())[status] += 1
    return out


def recommend_threshold(scores: list[tuple[float, str]], target_pct: int) -> tuple[float, dict]:
    """Find lowest threshold T s.t. ≥ target_pct of *answered* turns have top_score ≥ T."""
    answered = sorted(s for s, st in scores if st in ("answered", "ANSWERED"))
    if not answered:
        return 0.0, {"reason": "no answered turns in window — cannot recommend"}
    # Cut at the (100 - target_pct) percentile from the bottom.
    cutoff_idx = max(0, int(len(answered) * (100 - target_pct) / 100) - 1)
    threshold = answered[cutoff_idx]
    refused_below = sum(1 for s, st in scores if st in ("no_context", "blocked", "refuse", "REFUSE") and s < threshold)
    answered_below = sum(1 for s in answered if s < threshold)
    answered_above = sum(1 for s in answered if s >= threshold)
    rationale = {
        "answered_total": len(answered),
        "answered_above_threshold": answered_above,
        "answered_below_threshold": answered_below,
        "refuse_below_threshold": refused_below,
        "target_admit_pct": target_pct,
    }
    return threshold, rationale


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--bot-id", required=True, help="record_bot_id UUID")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--metric", choices=("top_score", "reranker_score"), default="top_score")
    p.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS)
    p.add_argument("--target-pct", type=int, default=DEFAULT_PERCENTILE_TARGET,
                   help="admit at least this %% of answered turns (default 80)")
    p.add_argument("--csv", help="optional path to write histogram as CSV")
    args = p.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    with _connect(dsn) as conn:
        scores = fetch_scores(conn, args.bot_id, args.days, args.metric)

    if not scores:
        print(f"No scores found for bot_id={args.bot_id} in last {args.days} days.")
        print("(Either no traffic, or request_steps not instrumented for filter_min_score yet — see Stream M.)")
        return 1

    print(f"=== Score distribution — bot={args.bot_id[:8]}... metric={args.metric} ({args.days}d, n={len(scores)}) ===")
    hist = histogram(scores, args.buckets)
    print()
    print(f"{'bucket':<18} {'total':>6} {'ans':>5} {'refuse':>7} {'block':>6}")
    print("-" * 54)
    for b in range(args.buckets):
        if b not in hist:
            continue
        c = hist[b]
        ans = c.get("answered", 0) + c.get("ANSWERED", 0)
        refuse = c.get("no_context", 0) + c.get("REFUSE", 0) + c.get("refuse", 0)
        block = c.get("blocked", 0)
        total = sum(c.values())
        print(f"{_bucket_label(b, args.buckets):<18} {total:>6} {ans:>5} {refuse:>7} {block:>6}")

    threshold, rationale = recommend_threshold(scores, args.target_pct)
    print()
    print(f"=== Threshold recommendation (admit ≥ {args.target_pct}% of answered turns) ===")
    print(f"  Recommended {args.metric} ≥ {threshold:.4f}")
    for k, v in rationale.items():
        print(f"  {k}: {v}")
    print()
    print("To apply (no schema change yet — uses existing system_config knob):")
    print(f"  UPDATE ragbot.system_config SET value = '{threshold:.3f}' WHERE key = 'reranker_min_score_active';")
    print("  -- per-bot override schema lands in Stream V Phase 2.")

    if args.csv:
        out = Path(args.csv)
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bucket_lo", "bucket_hi", "total", "answered", "refuse", "blocked"])
            for b in range(args.buckets):
                if b not in hist:
                    continue
                c = hist[b]
                w.writerow([
                    f"{b/args.buckets:.3f}", f"{(b+1)/args.buckets:.3f}",
                    sum(c.values()),
                    c.get("answered", 0) + c.get("ANSWERED", 0),
                    c.get("no_context", 0) + c.get("REFUSE", 0) + c.get("refuse", 0),
                    c.get("blocked", 0),
                ])
        print(f"\nHistogram CSV written: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
