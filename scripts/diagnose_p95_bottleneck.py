#!/usr/bin/env python3
"""Diagnose p95 latency bottleneck for Ragbot query pipeline.

Reads ``request_steps`` + ``request_logs`` + ``model_invocations`` from the
production Postgres pointed to by ``DATABASE_URL`` (or ``--dsn``), and prints
per-step latency distributions plus cross-bot breakdown.

Goal: answer "21s p95 đang ở node nào?" with evidence, not guesses.

Usage:
    set -a && source .env && set +a
    python scripts/diagnose_p95_bottleneck.py --hours 24
    python scripts/diagnose_p95_bottleneck.py --hours 168 --top 20 --json
    python scripts/diagnose_p95_bottleneck.py --bot legalbot --hours 24

No write access — read-only diagnostic. Safe to run against prod replica.

Exit codes: 0 = success, 2 = DB unreachable, 3 = no rows in window.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

# ---------- defaults ---------------------------------------------------------
DEFAULT_WINDOW_HOURS: int = 24
DEFAULT_TOP_STEPS: int = 30
DEFAULT_TOP_BOTS: int = 10
DEFAULT_RERANK_SCORE_WINDOW_DAYS: int = 7
EXIT_DB_UNREACHABLE: int = 2
EXIT_NO_ROWS: int = 3


# ---------- helpers ----------------------------------------------------------
def resolve_dsn(cli_dsn: str | None) -> str:
    """Convert async URL → sync psycopg2 URL. Strip ``+asyncpg``/``+psycopg``."""
    raw = cli_dsn or os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not raw:
        sys.stderr.write(
            "[diagnose_p95] DATABASE_URL not set. "
            "Run: set -a && source .env && set +a\n",
        )
        sys.exit(EXIT_DB_UNREACHABLE)
    # psycopg2 expects bare postgresql://; strip dialect+driver hints
    for suffix in ("+asyncpg", "+psycopg2", "+psycopg"):
        raw = raw.replace(suffix, "")
    return raw


def fmt_ms(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.0f}ms"


def percentile_query(window_hours: int) -> str:
    """Per-step distribution: avg / p50 / p95 / p99 / max + sample count."""
    return f"""
    SELECT step_name,
           COUNT(*)                                                        AS n,
           AVG(duration_ms)::float                                          AS avg_ms,
           PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms)::float AS p50_ms,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)::float AS p95_ms,
           PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms)::float AS p99_ms,
           MAX(duration_ms)::float                                          AS max_ms,
           SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END)             AS errors
    FROM request_steps
    WHERE started_at > NOW() - INTERVAL '{window_hours} hours'
    GROUP BY step_name
    ORDER BY p95_ms DESC NULLS LAST
    """


def end_to_end_query(window_hours: int, bot_filter: str | None) -> str:
    """End-to-end p95 from request_logs (matching reports).

    Note: ``request_logs.record_bot_id`` is the UUID PK of ``bots.id``
    (per 4-key naming rule — record_ prefix = internal UUID FK).
    ``bots.bot_id`` is the VARCHAR slug we filter on.
    """
    where = f"WHERE started_at > NOW() - INTERVAL '{window_hours} hours'"
    if bot_filter:
        # bot_filter is a bot_id slug; resolve via JOIN bots
        where += f" AND record_bot_id IN (SELECT id FROM bots WHERE bot_id = %(bot)s)"
    return f"""
    SELECT COUNT(*)                                                        AS n,
           AVG(duration_ms)::float                                          AS avg_ms,
           PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms)::float AS p50_ms,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)::float AS p95_ms,
           PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms)::float AS p99_ms,
           MAX(duration_ms)::float                                          AS max_ms,
           SUM(CASE WHEN status = 'refused' THEN 1 ELSE 0 END)              AS refused,
           SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END)              AS failed,
           SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)              AS success
    FROM request_logs
    {where}
    """


def per_bot_query(window_hours: int, top: int) -> str:
    """End-to-end p95 split per bot — answers Q5."""
    return f"""
    SELECT b.bot_id,
           rl.channel_type,
           COUNT(*)                                                            AS n,
           PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY rl.duration_ms)::float AS p50_ms,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY rl.duration_ms)::float AS p95_ms,
           PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY rl.duration_ms)::float AS p99_ms
    FROM request_logs rl
    LEFT JOIN bots b ON b.id = rl.record_bot_id
    WHERE rl.started_at > NOW() - INTERVAL '{window_hours} hours'
    GROUP BY b.bot_id, rl.channel_type
    HAVING COUNT(*) >= 5
    ORDER BY p95_ms DESC NULLS LAST
    LIMIT {top}
    """


def grade_retry_distribution_query(window_hours: int) -> str:
    """How many grade retries per request — answers Q2."""
    return f"""
    SELECT COALESCE((metadata_json ->> 'grade_retries')::int, 0) AS retries,
           COUNT(*)                                              AS n,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)::float AS p95_ms
    FROM request_steps
    WHERE step_name = 'grade'
      AND started_at > NOW() - INTERVAL '{window_hours} hours'
    GROUP BY 1
    ORDER BY 1
    """


def llm_calls_per_turn_query(window_hours: int) -> str:
    """Avg LLM call count per request_id — answers Q13."""
    return f"""
    SELECT AVG(call_count)::float                                          AS avg_calls,
           PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY call_count)::float AS p50_calls,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY call_count)::float AS p95_calls,
           MAX(call_count)                                                  AS max_calls,
           COUNT(*)                                                         AS n_requests
    FROM (
        SELECT record_request_id, COUNT(*) AS call_count
        FROM model_invocations
        WHERE started_at > NOW() - INTERVAL '{window_hours} hours'
          AND record_request_id IS NOT NULL
        GROUP BY record_request_id
    ) sub
    """


def reflect_opt_in_query() -> str:
    """How many bots have plan_limits.reflection_enabled=true — answers Q8."""
    return """
    SELECT bot_id, workspace_id,
           plan_limits ->> 'reflection_enabled' AS reflection,
           plan_limits ->> 'grounding_check_async_enabled' AS grounding_async
    FROM bots
    WHERE plan_limits ->> 'reflection_enabled' = 'true'
       OR plan_limits ->> 'grounding_check_async_enabled' = 'true'
    """


def dead_path_flags_query() -> str:
    """Verify Q10 + Q11 dead paths — counts bots/system_config with True."""
    return """
    SELECT key,
           value,
           COUNT(*) AS n
    FROM system_config
    WHERE key IN (
        'metadata_extraction_enabled',
        'adapchunk_layer3_doc_profile_enabled',
        'cleanbase_tier0_enabled'
    )
    GROUP BY key, value
    ORDER BY key, value
    """


def semantic_cache_index_query() -> str:
    """List indexes on ``semantic_cache`` — answers Bug #3 (cache_check
    1.21s). If an HNSW index on the embedding column is missing,
    similarity scans degrade to a sequential scan over the full table.
    """
    return """
    SELECT indexname,
           indexdef
    FROM pg_indexes
    WHERE tablename = 'semantic_cache'
    ORDER BY indexname
    """


def semantic_cache_config_query() -> str:
    """All ``system_config`` keys touching semantic cache (threshold,
    TTL, lock-TTL, wait-retry). Lets the operator confirm the active
    thresholds before chasing index issues.
    """
    return """
    SELECT key, value
    FROM system_config
    WHERE key ILIKE '%semantic_cache%'
       OR key ILIKE 'cache_similarity_threshold%'
       OR key ILIKE 'cache_ttl%'
    ORDER BY key
    """


def semantic_cache_size_query() -> str:
    """Row-count + age distribution. A bloated semantic_cache (millions
    of rows) plus a missing HNSW index amplifies the p95 — useful to
    flag when an LRU eviction policy should be wired."""
    return """
    SELECT COUNT(*)                                                AS n_rows,
           SUM(CASE WHEN expires_at < NOW() THEN 1 ELSE 0 END)     AS n_expired,
           SUM(CASE WHEN expires_at >= NOW() THEN 1 ELSE 0 END)    AS n_active,
           MIN(created_at)                                         AS oldest,
           MAX(created_at)                                         AS newest
    FROM semantic_cache
    """


def cache_hit_rate_query(window_hours: int) -> str:
    """Semantic cache hit rate per bot over the last N hours (WA-7).

    Joins ``request_steps`` (where ``step_name='cache_check'`` rows are
    instrumented with ``metadata_json->>'hit'``) onto ``request_logs`` to
    attribute the hit to its bot_id slug. Returns one row per bot:
    ``{bot_id, total_queries, cache_hits, hit_rate, threshold_active}``.

    ``threshold_active`` is sampled from the most recent cache_check row
    metadata for that bot — the value the cache actually applied at hit
    time, not the static system_config seed. Useful for spotting drift
    after a per-bot ``plan_limits.semantic_cache_threshold`` override.
    """
    return f"""
    WITH cache_steps AS (
        SELECT rs.record_request_id,
               rs.metadata_json,
               rs.started_at,
               COALESCE((rs.metadata_json ->> 'hit')::bool, false) AS hit,
               (rs.metadata_json ->> 'threshold')::float           AS threshold_seen
        FROM request_steps rs
        WHERE rs.step_name = 'cache_check'
          AND rs.started_at > NOW() - INTERVAL '{window_hours} hours'
    ),
    joined AS (
        SELECT b.bot_id,
               cs.hit,
               cs.threshold_seen,
               ROW_NUMBER() OVER (
                   PARTITION BY b.bot_id ORDER BY cs.started_at DESC
               ) AS recency_rank
        FROM cache_steps cs
        JOIN request_logs rl ON rl.request_id = cs.record_request_id
        LEFT JOIN bots b ON b.id = rl.record_bot_id
    )
    SELECT bot_id,
           COUNT(*)                                                    AS total_queries,
           SUM(CASE WHEN hit THEN 1 ELSE 0 END)                        AS cache_hits,
           ROUND(
               (SUM(CASE WHEN hit THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0)) * 100, 2
           )                                                           AS hit_rate_pct,
           MAX(CASE WHEN recency_rank = 1 THEN threshold_seen END)     AS threshold_active
    FROM joined
    GROUP BY bot_id
    ORDER BY total_queries DESC NULLS LAST
    """


def rerank_score_histogram_query(window_days: int) -> str:
    """Histogram of ``request_steps`` rerank step ``top_score`` over a
    rolling window, grouped by bot. Feeds CT-4 A/B threshold picker:
    a bot whose rerank top_score sits in 0.20-0.29 most of the time
    will see ~80% refuse if the platform default (0.30) is applied —
    the operator may want a per-bot override of 0.20 or invest in
    chunk-quality work instead.

    Reads ``metadata_json ->> 'top_score'`` from the rerank step
    (instrumented in ``orchestration/query_graph.py``). NULL rows
    (rerank bypassed) are excluded; bucket boundaries match the
    A/B script for cross-comparison.
    """
    return f"""
    WITH rerank_scores AS (
        SELECT b.bot_id,
               COALESCE((rs.metadata_json ->> 'top_score')::float, NULL) AS top_score
        FROM request_steps rs
        LEFT JOIN request_logs rl ON rl.request_id = rs.record_request_id
        LEFT JOIN bots b ON b.id = rl.record_bot_id
        WHERE rs.step_name = 'rerank'
          AND rs.started_at > NOW() - INTERVAL '{window_days} days'
          AND rs.metadata_json ? 'top_score'
    )
    SELECT bot_id,
           CASE
               WHEN top_score IS NULL THEN 'null'
               WHEN top_score < 0.10 THEN '0.00-0.09'
               WHEN top_score < 0.20 THEN '0.10-0.19'
               WHEN top_score < 0.30 THEN '0.20-0.29'
               WHEN top_score < 0.40 THEN '0.30-0.39'
               WHEN top_score < 0.50 THEN '0.40-0.49'
               ELSE '0.50+'
           END                                                   AS bucket,
           COUNT(*)                                              AS n,
           AVG(top_score)::float                                 AS avg_score
    FROM rerank_scores
    GROUP BY bot_id, bucket
    ORDER BY bot_id NULLS LAST, bucket
    """


def print_rerank_score_histogram(rows: list[dict[str, Any]], window_days: int) -> None:
    """Render rerank top_score histogram per bot as a text bar chart.

    For each bot, prints a horizontal histogram (% of total samples per
    bucket) plus the recommended threshold band. Bucket boundaries align
    with ``scripts/reranker_threshold_ab_test.py::score_bucket``.
    """
    print_section(f"RERANK top_score HISTOGRAM (last {window_days}d, per bot)")
    if not rows:
        print("  [no rerank steps with top_score in window]")
        print("  → step_name='rerank' may not write metadata_json.top_score,")
        print("    or no chat traffic ran the reranker mode.")
        return
    by_bot: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        bid = r.get("bot_id") or "<unknown>"
        by_bot.setdefault(bid, []).append(r)
    bucket_order = [
        "0.00-0.09", "0.10-0.19", "0.20-0.29",
        "0.30-0.39", "0.40-0.49", "0.50+", "null",
    ]
    for bot, brows in sorted(by_bot.items()):
        total = sum(b["n"] for b in brows)
        if total == 0:
            continue
        by_bucket = {b["bucket"]: b for b in brows}
        print()
        print(f"  bot_id={bot}  n={total}")
        for bk in bucket_order:
            row = by_bucket.get(bk)
            n = (row or {}).get("n", 0) or 0
            pct = (n / total * 100) if total else 0.0
            bar = "#" * max(0, int(pct / 2))
            print(f"    {bk:<10} n={n:>5} ({pct:>5.1f}%) {bar}")
        # Recommended threshold pick: lowest bucket with cumulative
        # coverage ≥ 80% gives a "catch most queries" floor.
        cumulative = 0
        recommend = "—"
        for bk in bucket_order:
            row = by_bucket.get(bk)
            if not row or bk == "null":
                continue
            cumulative += row.get("n", 0) or 0
            if cumulative / total >= 0.8:
                recommend = bk.split("-")[0]
                break
        print(f"    → 80% cumulative coverage at top_score >= {recommend}")


# ---------- output -----------------------------------------------------------
@dataclass
class DiagReport:
    generated_at: str
    window_hours: int
    bot_filter: str | None
    end_to_end: dict[str, Any]
    per_step: list[dict[str, Any]]
    per_bot: list[dict[str, Any]]
    grade_retries: list[dict[str, Any]]
    llm_calls: dict[str, Any]
    reflect_opt_in: list[dict[str, Any]]
    dead_path_flags: list[dict[str, Any]]
    # Bug #3 (cache_check 1.21s) diagnostics — index + config + size.
    semantic_cache_indexes: list[dict[str, Any]]
    semantic_cache_config: list[dict[str, Any]]
    semantic_cache_size: dict[str, Any]
    # CT-4 — rerank top_score histogram per bot (optional, off by default).
    rerank_score_histogram: list[dict[str, Any]] | None = None
    rerank_score_window_days: int | None = None


def print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def print_end_to_end(row: dict[str, Any]) -> None:
    print_section("END-TO-END (request_logs)")
    if not row or row.get("n", 0) == 0:
        print("  [no rows in window]")
        return
    print(f"  n={row['n']}  success={row.get('success')}  "
          f"refused={row.get('refused')}  failed={row.get('failed')}")
    print(f"  avg={fmt_ms(row['avg_ms'])}  p50={fmt_ms(row['p50_ms'])}  "
          f"p95={fmt_ms(row['p95_ms'])}  p99={fmt_ms(row['p99_ms'])}  "
          f"max={fmt_ms(row['max_ms'])}")


def print_per_step(rows: list[dict[str, Any]]) -> None:
    print_section("PER-STEP LATENCY (request_steps, sorted p95 desc)")
    if not rows:
        print("  [no rows] — request_steps table empty in this window")
        print("  → instrumentation may be disabled OR no chat traffic")
        return
    header = f"  {'step_name':<32} {'n':>7} {'avg':>9} {'p50':>9} {'p95':>9} {'p99':>9} {'max':>9} {'err':>5}"
    print(header)
    print("  " + "-" * 76)
    for r in rows:
        print(
            f"  {r['step_name']:<32} {r['n']:>7} "
            f"{fmt_ms(r['avg_ms']):>9} {fmt_ms(r['p50_ms']):>9} "
            f"{fmt_ms(r['p95_ms']):>9} {fmt_ms(r['p99_ms']):>9} "
            f"{fmt_ms(r['max_ms']):>9} {r['errors']:>5}",
        )


def print_per_bot(rows: list[dict[str, Any]]) -> None:
    print_section("PER-BOT p95 (cross-bot distribution — answers Q5)")
    if not rows:
        print("  [no rows]")
        return
    header = f"  {'bot_id':<28} {'channel':<10} {'n':>6} {'p50':>9} {'p95':>9} {'p99':>9}"
    print(header)
    print("  " + "-" * 76)
    for r in rows:
        bot_id = (r["bot_id"] or "<unknown>")[:28]
        ch = (r["channel_type"] or "—")[:10]
        print(
            f"  {bot_id:<28} {ch:<10} {r['n']:>6} "
            f"{fmt_ms(r['p50_ms']):>9} {fmt_ms(r['p95_ms']):>9} {fmt_ms(r['p99_ms']):>9}",
        )


def print_grade_retries(rows: list[dict[str, Any]]) -> None:
    print_section("GRADE RETRY DISTRIBUTION (answers Q2)")
    if not rows:
        print("  [no grade step rows in window]")
        return
    for r in rows:
        print(f"  retries={r['retries']}  count={r['n']}  p95={fmt_ms(r['p95_ms'])}")


def print_llm_calls(row: dict[str, Any]) -> None:
    print_section("LLM CALLS PER TURN (answers Q13)")
    if not row or row.get("n_requests", 0) == 0:
        print("  [no model_invocations rows]")
        return
    print(f"  n_requests={row['n_requests']}")
    print(f"  avg={row['avg_calls']:.2f}  p50={row['p50_calls']:.1f}  "
          f"p95={row['p95_calls']:.1f}  max={row['max_calls']}")


def print_reflect_opt_in(rows: list[dict[str, Any]]) -> None:
    print_section("BOTS OPT-IN FOR Reflect / Async-Grounding (answers Q8 + Q9)")
    if not rows:
        print("  0 bot opt-in — default OFF confirmed")
        print("  → 'Reflect async save -2000ms' claim = PHANTOM (no fires)")
        return
    for r in rows:
        print(f"  bot_id={r['bot_id']:<24} ws={r['workspace_id']:<16} "
              f"reflect={r['reflection']}  grounding_async={r['grounding_async']}")


def print_dead_path(rows: list[dict[str, Any]]) -> None:
    print_section("DEAD PATH FLAGS in system_config (answers Q10 + Q11)")
    if not rows:
        print("  [system_config keys not present — defaults apply]")
        return
    for r in rows:
        marker = " ← ENABLED" if r["value"] in ("true", "True", "1") else ""
        print(f"  {r['key']:<48} = {r['value']:<6} n={r['n']}{marker}")


def print_semantic_cache_diagnostic(
    indexes: list[dict[str, Any]],
    config: list[dict[str, Any]],
    size: dict[str, Any],
) -> None:
    """Bug #3 — cache_check 1.21s diagnostic.

    Top-3 causes of slow semantic_cache lookups:
      (1) HNSW index missing on the embedding column → seqscan.
      (2) Threshold too low (<0.7) → cosine scan over many candidates.
      (3) Bloated table (no LRU eviction) — millions of expired rows.
    """
    print_section("SEMANTIC_CACHE DIAGNOSTIC (answers Bug #3 — cache_check 1.21s)")

    # Indexes
    if not indexes:
        print("  [no indexes returned — table missing OR permission denied]")
    else:
        print("  Indexes:")
        has_hnsw_embedding = False
        for idx in indexes:
            name = idx.get("indexname", "—")
            ddl = idx.get("indexdef", "")
            print(f"    {name:<40} {ddl[:80]}")
            if "hnsw" in ddl.lower() and "embedding" in ddl.lower():
                has_hnsw_embedding = True
        if not has_hnsw_embedding:
            print("    ← MISSING: no HNSW index on embedding column."
                  " Add migration: CREATE INDEX ... USING hnsw"
                  " (query_embedding vector_cosine_ops)")

    # Config
    print()
    if not config:
        print("  Config: [no semantic_cache keys in system_config — defaults apply]")
    else:
        print("  Config:")
        for r in config:
            print(f"    {r['key']:<40} = {r['value']}")

    # Size
    print()
    if not size or size.get("n_rows") is None:
        print("  Size: [size query unavailable]")
    else:
        n_rows = size.get("n_rows") or 0
        n_active = size.get("n_active") or 0
        n_expired = size.get("n_expired") or 0
        print(f"  Size: n_rows={n_rows}  active={n_active}  expired={n_expired}")
        print(f"        oldest={size.get('oldest')}  newest={size.get('newest')}")


def write_json(path: str, report: DiagReport) -> None:
    with open(path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)
    print(f"\n  → JSON written: {path}")


# ---------- cache-stats CLI mode (WA-7) --------------------------------------
def run_cache_stats(args: argparse.Namespace) -> int:
    """``--cache-stats`` CLI handler: per-bot semantic cache hit rate.

    Emits structured JSON to stdout (one object per bot) so downstream
    tooling (notebooks, dashboards) can ingest without parsing the
    p95 ASCII tables. Schema:

        {
            "generated_at": "<iso8601>",
            "window_hours": <int>,
            "stats": [
                {"bot_id": "<slug>", "total_queries": <int>,
                 "cache_hits": <int>, "hit_rate": <float 0..1>,
                 "threshold_active": <float>}
            ]
        }

    Exit codes: 0 = success (even when no rows), 2 = DB unreachable.
    """
    dsn = resolve_dsn(args.dsn)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except psycopg2.OperationalError as exc:
        sys.stderr.write(f"[diagnose_p95] DB connect failed: {exc}\n")
        return EXIT_DB_UNREACHABLE
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(cache_hit_rate_query(args.hours))
        rows = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        sys.stderr.write(f"[diagnose_p95] cache_hit_rate query failed: {exc}\n")
        rows = []
        conn.rollback()
    cur.close()
    conn.close()

    stats: list[dict[str, Any]] = []
    for r in rows:
        total = int(r.get("total_queries") or 0)
        hits = int(r.get("cache_hits") or 0)
        # hit_rate is unitless 0..1; ``hit_rate_pct`` is 0..100 for humans.
        # JSON consumer sees the 0..1 form to keep the integration math sane.
        hit_rate = (hits / total) if total > 0 else 0.0
        stats.append({
            "bot_id": r.get("bot_id") or "<unknown>",
            "total_queries": total,
            "cache_hits": hits,
            "hit_rate": round(hit_rate, 4),
            "threshold_active": (
                float(r["threshold_active"]) if r.get("threshold_active") is not None else None
            ),
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": args.hours,
        "stats": stats,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


# ---------- main -------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    if args.cache_stats:
        return run_cache_stats(args)
    dsn = resolve_dsn(args.dsn)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except psycopg2.OperationalError as exc:
        sys.stderr.write(f"[diagnose_p95] DB connect failed: {exc}\n")
        return EXIT_DB_UNREACHABLE
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # --- End-to-end ---
    e2e_params: dict[str, Any] = {}
    if args.bot:
        e2e_params["bot"] = args.bot
    cur.execute(end_to_end_query(args.hours, args.bot), e2e_params)
    e2e = dict(cur.fetchone() or {})

    # --- Per-step ---
    cur.execute(percentile_query(args.hours))
    per_step = [dict(r) for r in cur.fetchall()][: args.top]

    # --- Per-bot ---
    cur.execute(per_bot_query(args.hours, args.top_bots))
    per_bot = [dict(r) for r in cur.fetchall()]

    # --- Grade retries ---
    cur.execute(grade_retry_distribution_query(args.hours))
    grade_retries = [dict(r) for r in cur.fetchall()]

    # --- LLM calls / turn ---
    cur.execute(llm_calls_per_turn_query(args.hours))
    llm_calls = dict(cur.fetchone() or {})

    # --- Reflect opt-in ---
    try:
        cur.execute(reflect_opt_in_query())
        reflect_opt_in = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        # bots.plan_limits might be missing on dev schemas
        reflect_opt_in = []
        conn.rollback()

    # --- Dead path flags ---
    try:
        cur.execute(dead_path_flags_query())
        dead_path = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        dead_path = []
        conn.rollback()

    # --- Bug #3 cache_check 1.21s diagnostics ---
    try:
        cur.execute(semantic_cache_index_query())
        sc_indexes = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        sc_indexes = []
        conn.rollback()
    try:
        cur.execute(semantic_cache_config_query())
        sc_config = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        sc_config = []
        conn.rollback()
    try:
        cur.execute(semantic_cache_size_query())
        sc_size = dict(cur.fetchone() or {})
    except psycopg2.Error:
        sc_size = {}
        conn.rollback()

    # --- CT-4 rerank top_score histogram (opt-in) ---
    rerank_hist: list[dict[str, Any]] | None = None
    if getattr(args, "rerank_score_histogram", False):
        try:
            cur.execute(rerank_score_histogram_query(args.rerank_score_days))
            rerank_hist = [dict(r) for r in cur.fetchall()]
        except psycopg2.Error:
            rerank_hist = []
            conn.rollback()

    cur.close()
    conn.close()

    report = DiagReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_hours=args.hours,
        bot_filter=args.bot,
        end_to_end=e2e,
        per_step=per_step,
        per_bot=per_bot,
        grade_retries=grade_retries,
        llm_calls=llm_calls,
        reflect_opt_in=reflect_opt_in,
        dead_path_flags=dead_path,
        semantic_cache_indexes=sc_indexes,
        semantic_cache_config=sc_config,
        semantic_cache_size=sc_size,
        rerank_score_histogram=rerank_hist,
        rerank_score_window_days=(
            args.rerank_score_days if getattr(args, "rerank_score_histogram", False)
            else None
        ),
    )

    if args.json_out:
        write_json(args.json_out, report)
        return 0

    print(f"Ragbot p95 Diagnostic — generated_at={report.generated_at}")
    print(f"Window: last {args.hours}h  bot_filter={args.bot or '*'}")
    print_end_to_end(e2e)
    print_per_step(per_step)
    print_per_bot(per_bot)
    print_grade_retries(grade_retries)
    print_llm_calls(llm_calls)
    print_reflect_opt_in(reflect_opt_in)
    print_dead_path(dead_path)
    print_semantic_cache_diagnostic(sc_indexes, sc_config, sc_size)
    if rerank_hist is not None:
        print_rerank_score_histogram(rerank_hist, args.rerank_score_days)

    if not per_step and (not e2e or e2e.get("n", 0) == 0):
        return EXIT_NO_ROWS
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS,
                   help=f"Window in hours (default {DEFAULT_WINDOW_HOURS})")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_STEPS,
                   help=f"Top N step_name rows (default {DEFAULT_TOP_STEPS})")
    p.add_argument("--top-bots", type=int, default=DEFAULT_TOP_BOTS,
                   help=f"Top N bot rows (default {DEFAULT_TOP_BOTS})")
    p.add_argument("--bot", type=str, default=None,
                   help="Filter end-to-end by bot_id slug")
    p.add_argument("--dsn", type=str, default=None,
                   help="Override DATABASE_URL")
    p.add_argument("--json-out", type=str, default=None,
                   help="Write report as JSON to this path")
    p.add_argument("--cache-stats", action="store_true", default=False,
                   help=(
                       "Emit per-bot semantic cache hit-rate JSON "
                       "({bot_id, total_queries, cache_hits, hit_rate, "
                       "threshold_active}) for the configured --hours "
                       "window and exit. Skips the full p95 report."
                   ))
    # Empirical rerank score histogram (informs A/B threshold pick).
    p.add_argument("--rerank-score-histogram", action="store_true",
                   help=("Print per-bot histogram of rerank top_score over"
                         f" {DEFAULT_RERANK_SCORE_WINDOW_DAYS} days"
                         " (informs per-bot threshold tuning)"))
    p.add_argument("--rerank-score-days", type=int,
                   default=DEFAULT_RERANK_SCORE_WINDOW_DAYS,
                   help=("Window (days) for --rerank-score-histogram"
                         f" (default {DEFAULT_RERANK_SCORE_WINDOW_DAYS})"))
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
