#!/usr/bin/env python3
"""Agent D — 15-Stage Deepdive Load Test.

Generic 5-category × 10-question harness with per-stage metric extraction
(guard / cache / intent / retrieve / rerank / CRAG / generate). Output:
JSONL raw + CSV per-stage + MD report.

The question set is loaded from a markdown fixture (`--questions-file`,
default `tests/fixtures/agent_d_questions.md`) so the harness stays
domain-neutral — bot owners point it at their own corpus-shaped fixture.

Bot identity (tenant_id, bot_id, channel_type) MUST come from CLI flags
or the matching `LOADTEST_*` env vars; literals never appear in this
file (CLAUDE.md domain-neutral rule).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from scripts._loadtest_common import REFUSE_PATTERN, is_refuse

# === Module constants — no inline magic numbers below this block ===
DEFAULT_BASE_URL = "http://localhost:3004"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

REQUEST_TIMEOUT_S = 65.0
HTTP_OK = 200
DEFAULT_PAUSE_BETWEEN_QUERIES_S = 1.5
DEFAULT_QUESTIONS_FILE = "tests/fixtures/agent_d_questions.md"
DEFAULT_REPORTS_DIR = "/var/www/html/ragbot/reports"

# Markdown fixture parse: category heading "## <NAME>" then numbered items.
CATEGORY_HEADING_RE = re.compile(r"^##\s+([A-Za-z0-9_\-]+)\s*$")
NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")

# Score-classification thresholds for the report's prose. Lift to constants
# so retrieval-tuning sprints can adjust without grepping markdown.
NEAR_RANDOM_TOP1_THRESHOLD = 0.05
RERANKER_DISABLED_RRF_RATIO = 0.4  # >40% RRF_only (non-cache) = silent disable
P95_LATENCY_HIGH_MS = 15_000
GRADED_REFUSE_MED_THRESHOLD = 5
CACHE_REFUSE_HIGH_THRESHOLD = 5
ANSWER_PREVIEW_LEN = 300
JSONL_ANSWER_PREVIEW_LEN = 500
JSONL_CHUNK_PREVIEW_LEN = 200
HIGH_REFUSE_PCT_THRESHOLD = 60.0


# --------------------------------------------------------------------------- #
# Data models                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BotIdentity:
    tenant_id: int
    bot_id: str
    channel_type: str


@dataclass(frozen=True)
class HarnessPaths:
    jsonl: Path
    csv: Path
    md: Path


# --------------------------------------------------------------------------- #
# Question fixture parser                                                     #
# --------------------------------------------------------------------------- #


def parse_questions_file(md_path: Path) -> list[dict[str, Any]]:
    """Parse `## CATEGORY` + numbered list into [{idx, category, q}].

    Empty categories are tolerated; the harness reports them but doesn't
    abort. Index is global (0-based) across categories so the JSONL/CSV
    rows still line up with the original 50-question schema.
    """
    if not md_path.exists():
        raise FileNotFoundError(f"questions file not found: {md_path}")
    items: list[dict[str, Any]] = []
    current_cat: str | None = None
    for raw_line in md_path.read_text(encoding="utf-8").splitlines():
        m_cat = CATEGORY_HEADING_RE.match(raw_line)
        if m_cat:
            current_cat = m_cat.group(1).upper()
            continue
        if current_cat is None:
            continue
        m_item = NUMBERED_ITEM_RE.match(raw_line)
        if m_item:
            text = m_item.group(2).strip()
            if text:
                items.append(
                    {
                        "idx": len(items),
                        "category": current_cat,
                        "q": text,
                    }
                )
    return items


# --------------------------------------------------------------------------- #
# HTTP harness                                                                #
# --------------------------------------------------------------------------- #


async def get_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    r = await client.get(f"{base_url}{SELF_TOKEN_PATH}", timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    return r.json()["token"]


async def call_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    bot: BotIdentity,
    question: str,
    connect_id: str,
) -> dict[str, Any]:
    payload = {
        "question": question,
        "tenant_id": bot.tenant_id,
        "bot_id": bot.bot_id,
        "channel_type": bot.channel_type,
        "connect_id": connect_id,
        "debug": "full",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{base_url}{CHAT_PATH}",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_S,
        )
    except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
        return {
            "error": f"{type(exc).__name__}: {exc!s}"[:400],
            "_elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code != HTTP_OK:
        return {
            "error": f"HTTP {r.status_code}: {r.text[:400]}",
            "_elapsed_ms": elapsed_ms,
        }
    try:
        data = r.json()
    except (ValueError, json.JSONDecodeError) as exc:
        data = {"error": f"json_parse: {exc!s}"[:200]}
    data["_elapsed_ms"] = elapsed_ms
    return data


# --------------------------------------------------------------------------- #
# Per-query metric extraction                                                 #
# --------------------------------------------------------------------------- #


def extract_metrics(item: dict[str, Any], resp: dict[str, Any]) -> dict[str, Any]:
    """Pull stage-level signals out of /chat response into a flat dict."""
    debug = resp.get("debug", {}) if isinstance(resp.get("debug"), dict) else {}
    chunks = resp.get("retrieved_chunks_content", []) or []
    tokens = resp.get("tokens", {}) or {}
    answer = resp.get("answer", "") or ""
    answer_type = resp.get("answer_type", "unknown")

    guard_flags = debug.get("guardrail_flags", []) or []
    guard_ok = 1 if not guard_flags else 0
    cache_hit = 1 if answer_type == "cache_hit" else 0
    intent = debug.get("intent", "") or ""
    rewritten_query = debug.get("rewritten_query", "") or ""

    retrieved_count = debug.get("top_k", 0) or len(chunks)
    top1_score = 0.0
    top5_avg_score = 0.0
    if chunks:
        scores = [c.get("score", 0.0) for c in chunks]
        scores_sorted = sorted(scores, reverse=True)
        top1_score = scores_sorted[0] if scores_sorted else 0.0
        top5_avg_score = statistics.mean(scores_sorted[:5]) if scores_sorted else 0.0

    score_max = debug.get("score_max", 0.0) or 0.0
    chunks_graded = debug.get("chunks_graded", 0) or 0
    llm_model = debug.get("model", "") or ""

    if cache_hit:
        rerank_mode = "cache_bypass"
    elif chunks_graded == 0 and retrieved_count > 0:
        rerank_mode = "RRF_only"
    elif chunks_graded > 0:
        rerank_mode = "CRAG_graded"
    else:
        rerank_mode = "no_chunks"
    rerank_top1 = score_max

    refused = 1 if is_refuse(answer, pattern=REFUSE_PATTERN) else 0

    if cache_hit:
        crag_state = "CACHE_HIT"
    elif answer_type == "refused" or (refused and chunks_graded == 0):
        crag_state = "REFUSE"
    elif chunks_graded > 0 and not refused:
        crag_state = "GROUNDED"
    elif chunks_graded > 0 and refused:
        crag_state = "GRADED_REFUSE"
    elif "error" in resp:
        crag_state = "ERROR"
    else:
        crag_state = "UNKNOWN"

    grounding_score = top1_score if chunks_graded > 0 else 0.0

    return {
        "query_idx": item["idx"],
        "category": item["category"],
        "q": item["q"],
        "elapsed_ms": resp.get("_elapsed_ms", 0),
        "guard_ok": guard_ok,
        "guard_flags": "|".join(str(f) for f in guard_flags),
        "cache_hit": cache_hit,
        "intent": intent,
        "rewritten_query": rewritten_query[:80] if rewritten_query else "",
        "retrieved_count": retrieved_count,
        "top1_score": round(top1_score, 6),
        "top5_avg_score": round(top5_avg_score, 6),
        "rerank_mode": rerank_mode,
        "rerank_top1": round(rerank_top1, 6),
        "chunks_graded": chunks_graded,
        "crag_state": crag_state,
        "llm_model": llm_model,
        "input_tok": int(tokens.get("prompt", 0) or 0),
        "output_tok": int(tokens.get("completion", 0) or 0),
        "cached_tok": int(tokens.get("cached", 0) or 0),
        "grounding_score": round(grounding_score, 6),
        "refused": refused,
        "answer_type": answer_type,
        "answer_len": len(answer),
        "answer_preview": answer[:ANSWER_PREVIEW_LEN].replace("\n", " "),
        "query_decomposed": 1 if debug.get("query_decomposed") else 0,
        "parents_expanded": debug.get("parents_expanded_count", 0) or 0,
        "history_msgs": debug.get("history_messages", 0) or 0,
    }


def percentile(data: list[int | float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


# --------------------------------------------------------------------------- #
# Run loop                                                                    #
# --------------------------------------------------------------------------- #


async def run_load_test(
    *,
    base_url: str,
    bot: BotIdentity,
    questions: list[dict[str, Any]],
    paths: HarnessPaths,
    pause_s: float,
    timestamp: str,
) -> list[dict[str, Any]]:
    paths.jsonl.parent.mkdir(parents=True, exist_ok=True)
    print(f"Agent D Load Test — {timestamp}", flush=True)
    print(f"Output JSONL: {paths.jsonl}", flush=True)
    print(f"Output CSV:   {paths.csv}", flush=True)
    print(f"Output MD:    {paths.md}", flush=True)
    print("=" * 70, flush=True)

    all_metrics: list[dict[str, Any]] = []
    n = len(questions)

    async with httpx.AsyncClient() as client:
        token = await get_self_token(client, base_url)
        print(f"Token: {token[:30]}...", flush=True)

        with paths.jsonl.open("w", encoding="utf-8") as jsonl_f:
            for item in questions:
                connect_id = f"agent-d-{timestamp}-{item['idx']}"
                print(
                    f"[{item['idx'] + 1:02d}/{n:02d}] {item['category']:14} | "
                    f"{item['q'][:55]:<55}",
                    end=" ",
                    flush=True,
                )
                resp = await call_chat(
                    client,
                    base_url=base_url,
                    token=token,
                    bot=bot,
                    question=item["q"],
                    connect_id=connect_id,
                )
                metrics = extract_metrics(item, resp)

                raw_rec = {
                    "i": item["idx"],
                    "category": item["category"],
                    "q": item["q"],
                    "elapsed_ms": resp.get("_elapsed_ms", 0),
                    "answer": (resp.get("answer") or "")[:JSONL_ANSWER_PREVIEW_LEN],
                    "answer_type": resp.get("answer_type", ""),
                    "refused": metrics["refused"],
                    "debug": resp.get("debug", {}),
                    "chunks": [
                        {
                            "chunk_id": c.get("chunk_id"),
                            "score": c.get("score"),
                            "content": (c.get("content") or "")[:JSONL_CHUNK_PREVIEW_LEN],
                        }
                        for c in (resp.get("retrieved_chunks_content") or [])[:5]
                    ],
                    "tokens": resp.get("tokens", {}),
                    "metrics": metrics,
                }
                jsonl_f.write(json.dumps(raw_rec, ensure_ascii=False) + "\n")
                jsonl_f.flush()

                all_metrics.append(metrics)

                cache_sym = "C" if metrics["cache_hit"] else "-"
                ref_sym = "R" if metrics["refused"] else "-"
                print(
                    f"{metrics['elapsed_ms']:6}ms | {cache_sym}{ref_sym} | "
                    f"top1={metrics['top1_score']:.4f} | "
                    f"graded={metrics['chunks_graded']:2d} | "
                    f"{metrics['crag_state']:15} | {metrics['rerank_mode']}",
                    flush=True,
                )
                if pause_s > 0:
                    await asyncio.sleep(pause_s)

    return all_metrics


def write_csv(all_metrics: list[dict[str, Any]], csv_path: Path) -> None:
    if not all_metrics:
        return
    fields = list(all_metrics[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_metrics)
    print(f"CSV written: {csv_path}", flush=True)


def analyze_category(cat_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not cat_metrics:
        return {}
    latencies = [m["elapsed_ms"] for m in cat_metrics]
    top1_scores = [m["top1_score"] for m in cat_metrics]
    cache_hits = sum(m["cache_hit"] for m in cat_metrics)
    refuses = sum(m["refused"] for m in cat_metrics)
    graded_counts = [m["chunks_graded"] for m in cat_metrics]
    rerank_modes: dict[str, int] = {}
    crag_states: dict[str, int] = {}
    for m in cat_metrics:
        rerank_modes[m["rerank_mode"]] = rerank_modes.get(m["rerank_mode"], 0) + 1
        crag_states[m["crag_state"]] = crag_states.get(m["crag_state"], 0) + 1
    cached_toks = [m["cached_tok"] for m in cat_metrics]

    return {
        "n": len(cat_metrics),
        "avg_ms": round(statistics.mean(latencies)) if latencies else 0,
        "p50_ms": round(percentile(latencies, 50)),
        "p95_ms": round(percentile(latencies, 95)),
        "avg_top1": round(statistics.mean(top1_scores), 4) if top1_scores else 0,
        "min_top1": round(min(top1_scores), 4) if top1_scores else 0,
        "max_top1": round(max(top1_scores), 4) if top1_scores else 0,
        "cache_hit_pct": round(cache_hits / len(cat_metrics) * 100, 1),
        "refuse_rate": round(refuses / len(cat_metrics) * 100, 1),
        "avg_graded": round(statistics.mean(graded_counts), 1) if graded_counts else 0,
        "avg_cached_tok": round(statistics.mean(cached_toks)) if cached_toks else 0,
        "rerank_modes": rerank_modes,
        "crag_states": crag_states,
    }


def generate_report(
    all_metrics: list[dict[str, Any]],
    *,
    bot: BotIdentity,
    paths: HarnessPaths,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append("# Agent D — 15-Stage Deepdive Load Test")
    lines.append(f"**Generated**: {now}  ")
    lines.append(
        f"**Bot**: tenant_id={bot.tenant_id}, bot_id={bot.bot_id}, "
        f"channel_type={bot.channel_type}  "
    )
    n_total = len(all_metrics)
    lines.append(f"**Queries**: {n_total} (loaded from fixture)  ")
    lines.append(f"**Raw JSONL**: {paths.jsonl}  ")
    lines.append(f"**CSV**: {paths.csv}  ")
    lines.append("")

    if not all_metrics:
        lines.append("(no queries executed)")
        return "\n".join(lines)

    lines.append("## Overall Summary")
    lines.append("")
    total_cache = sum(m["cache_hit"] for m in all_metrics)
    total_refused = sum(m["refused"] for m in all_metrics)
    total_graded = sum(1 for m in all_metrics if m["chunks_graded"] > 0)
    latencies = [m["elapsed_ms"] for m in all_metrics]
    all_top1 = [m["top1_score"] for m in all_metrics]

    def pct(num: int) -> float:
        return round(num / n_total * 100, 1)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total queries | {n_total} |")
    lines.append(f"| Cache hits | {total_cache} ({pct(total_cache)}%) |")
    lines.append(f"| Refused | {total_refused} ({pct(total_refused)}%) |")
    lines.append(f"| Graded (CRAG) | {total_graded} ({pct(total_graded)}%) |")
    lines.append(f"| Avg latency | {round(statistics.mean(latencies))}ms |")
    lines.append(f"| P50 latency | {round(percentile(latencies, 50))}ms |")
    lines.append(f"| P95 latency | {round(percentile(latencies, 95))}ms |")
    lines.append(f"| Avg top1_score | {round(statistics.mean(all_top1), 4)} |")
    lines.append(f"| Max top1_score | {round(max(all_top1), 4)} |")

    rerank_dist: dict[str, int] = {}
    crag_dist: dict[str, int] = {}
    for m in all_metrics:
        rerank_dist[m["rerank_mode"]] = rerank_dist.get(m["rerank_mode"], 0) + 1
        crag_dist[m["crag_state"]] = crag_dist.get(m["crag_state"], 0) + 1
    lines.append(
        "| Rerank modes | "
        + ", ".join(f"{k}={v}" for k, v in sorted(rerank_dist.items()))
        + " |"
    )
    lines.append(
        "| CRAG states | "
        + ", ".join(f"{k}={v}" for k, v in sorted(crag_dist.items()))
        + " |"
    )
    lines.append("")

    cats = sorted({m["category"] for m in all_metrics})
    cat_stats = {
        cat: analyze_category([m for m in all_metrics if m["category"] == cat])
        for cat in cats
    }
    lines.append("## Per-Category Statistical Analysis")
    lines.append("")
    lines.append(
        "| Category | n | avg_ms | p50_ms | p95_ms | avg_top1 | "
        "cache% | refuse% | avg_graded | avg_cached_tok |"
    )
    lines.append(
        "|----------|---|--------|--------|--------|----------|"
        "--------|---------|------------|----------------|"
    )
    for cat in cats:
        s = cat_stats[cat]
        lines.append(
            f"| {cat} | {s['n']} | {s['avg_ms']} | {s['p50_ms']} | "
            f"{s['p95_ms']} | {s['avg_top1']} | {s['cache_hit_pct']}% | "
            f"{s['refuse_rate']}% | {s['avg_graded']} | {s['avg_cached_tok']} |"
        )
    lines.append("")

    refuse_pct = pct(total_refused)
    cache_pct = pct(total_cache)
    rrf_only_count = sum(1 for m in all_metrics if m["rerank_mode"] == "RRF_only")
    graded_refuse = sum(1 for m in all_metrics if m["crag_state"] == "GRADED_REFUSE")
    cache_hit_then_refuse = sum(
        1 for m in all_metrics if m["cache_hit"] and m["refused"]
    )
    p95_all = round(percentile(latencies, 95))
    avg_top1_all = round(statistics.mean(all_top1), 4)
    non_cache = [m for m in all_metrics if not m["cache_hit"]]
    avg_top1_non_cache = (
        round(statistics.mean(m["top1_score"] for m in non_cache), 4)
        if non_cache
        else 0
    )

    lines.append("## Pin-Point Root Cause Analysis")
    lines.append("")
    lines.append("| Symptom | Stage | Evidence (quantitative) | Severity |")
    lines.append("|---------|-------|------------------------|----------|")
    refuse_severity = "HIGH" if refuse_pct > HIGH_REFUSE_PCT_THRESHOLD else "MED"
    lines.append(
        f"| Refuse rate {refuse_pct}% | Retrieve → Generate | "
        f"avg top1_score(non-cache)={avg_top1_non_cache} "
        f"(near-random threshold {NEAR_RANDOM_TOP1_THRESHOLD}) | "
        f"{refuse_severity} |"
    )
    rrf_pct = pct(rrf_only_count) if non_cache else 0.0
    rrf_severity = (
        "HIGH"
        if non_cache
        and rrf_only_count / max(1, len(non_cache)) > RERANKER_DISABLED_RRF_RATIO
        else "MED"
    )
    lines.append(
        f"| Reranker: {rrf_only_count}/{n_total} RRF_only ({rrf_pct}%) | "
        f"Rerank stage | reranker preflight may have disabled provider | "
        f"{rrf_severity} |"
    )
    cache_severity = (
        "HIGH" if cache_hit_then_refuse > CACHE_REFUSE_HIGH_THRESHOLD else "LOW"
    )
    lines.append(
        f"| Cache hit {cache_pct}% — {cache_hit_then_refuse} refuse via cache | "
        f"Semantic cache | possible stale refuse cached "
        f"(cache_hit AND refused = {cache_hit_then_refuse}) | "
        f"{cache_severity} |"
    )
    lat_severity = "HIGH" if p95_all > P95_LATENCY_HIGH_MS else "MED"
    lines.append(
        f"| P95 latency {p95_all}ms | Generate (LLM) | "
        f"see CSV for per-query input_tok / cached_tok | {lat_severity} |"
    )
    grade_severity = (
        "MED" if graded_refuse > GRADED_REFUSE_MED_THRESHOLD else "LOW"
    )
    lines.append(
        f"| {graded_refuse} queries graded but still refused | "
        f"Grade → Generate | chunks reached LLM but answer triggered "
        f"refuse heuristic | {grade_severity} |"
    )
    lines.append("")
    lines.append(f"_Overall avg top1_score = {avg_top1_all}._")
    lines.append("")

    lines.append("## Per-Query Detail")
    lines.append("")
    lines.append(
        "| # | cat | q | ms | C | R | top1 | graded | crag | rerank | "
        "model | in_tok | out_tok | cached |"
    )
    lines.append(
        "|---|-----|---|----|---|---|------|--------|------|--------|"
        "-------|--------|---------|--------|"
    )
    for m in all_metrics:
        c_sym = "Y" if m["cache_hit"] else "-"
        r_sym = "Y" if m["refused"] else "-"
        lines.append(
            f"| {m['query_idx'] + 1} | {m['category']} | {m['q'][:35]} | "
            f"{m['elapsed_ms']} | {c_sym} | {r_sym} | "
            f"{m['top1_score']:.4f} | {m['chunks_graded']} | "
            f"{m['crag_state']} | {m['rerank_mode']} | "
            f"{m['llm_model'][:20] if m['llm_model'] else '-'} | "
            f"{m['input_tok']} | {m['output_tok']} | {m['cached_tok']} |"
        )
    lines.append("")

    lines.append("---")
    lines.append(f"**Agent D done** — {paths.md} + {paths.jsonl} + {paths.csv}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI entrypoint                                                              #
# --------------------------------------------------------------------------- #


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Agent D — 15-stage deepdive load test. Reads questions from a "
            "markdown fixture (no domain content embedded in the harness)."
        )
    )
    p.add_argument(
        "--bot-id",
        default=os.getenv("LOADTEST_BOT_ID", ""),
    )
    p.add_argument(
        "--tenant-id",
        type=int,
        default=int(os.getenv("LOADTEST_TENANT_ID", "0") or "0"),
    )
    p.add_argument(
        "--channel-type",
        default=os.getenv("LOADTEST_CHANNEL_TYPE", ""),
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("RAGBOT_BASE_URL", DEFAULT_BASE_URL),
    )
    p.add_argument(
        "--questions-file",
        default=os.getenv("LOADTEST_QUESTIONS_FILE", DEFAULT_QUESTIONS_FILE),
    )
    p.add_argument(
        "--reports-dir",
        default=os.getenv("LOADTEST_REPORTS_DIR", DEFAULT_REPORTS_DIR),
    )
    p.add_argument(
        "--pause",
        type=float,
        default=DEFAULT_PAUSE_BETWEEN_QUERIES_S,
        help="seconds to sleep between queries",
    )
    args = p.parse_args()
    missing: list[str] = []
    if not args.bot_id:
        missing.append("--bot-id (env LOADTEST_BOT_ID)")
    if not args.channel_type:
        missing.append("--channel-type (env LOADTEST_CHANNEL_TYPE)")
    if args.tenant_id is None or args.tenant_id < 1:
        missing.append("--tenant-id positive int (env LOADTEST_TENANT_ID)")
    if missing:
        p.error("bot identity required, missing: " + ", ".join(missing))
    return args


def _build_paths(reports_dir: str, timestamp: str) -> HarnessPaths:
    base = Path(reports_dir)
    return HarnessPaths(
        jsonl=base / "agent_d_raw_responses.jsonl",
        csv=base / "agent_d_per_stage_metrics.csv",
        md=base / f"DEEPDIVE_15STAGE_AGENT_D_LOADTEST_{timestamp}.md",
    )


async def _amain(args: argparse.Namespace) -> int:
    bot = BotIdentity(
        tenant_id=args.tenant_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
    )
    questions = parse_questions_file(Path(args.questions_file))
    if not questions:
        print(
            f"ERROR: no questions parsed from {args.questions_file}",
            file=sys.stderr,
        )
        return 2

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = _build_paths(args.reports_dir, timestamp)
    all_metrics = await run_load_test(
        base_url=args.base_url,
        bot=bot,
        questions=questions,
        paths=paths,
        pause_s=args.pause,
        timestamp=timestamp,
    )
    write_csv(all_metrics, paths.csv)
    report = generate_report(all_metrics, bot=bot, paths=paths)
    paths.md.write_text(report, encoding="utf-8")
    print(f"\n{'=' * 70}")
    print("Agent D done.")
    print(f"  MD:    {paths.md}")
    print(f"  JSONL: {paths.jsonl}")
    print(f"  CSV:   {paths.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain(_parse_args())))
