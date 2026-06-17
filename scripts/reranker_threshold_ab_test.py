#!/usr/bin/env python3
"""Reranker threshold A/B test framework (CT-4).

Empirical validation tool for ``DEFAULT_RERANKER_MIN_SCORE_ACTIVE``. Wave A
agent WA-5 raised the platform default from 0.15 → 0.30; this CLI sweeps a
list of candidate threshold values against a single bot using a golden RAGAS
dataset (WA-1 fixture format) and records pass rate, refuse rate, top_score
distribution + P95 latency per value, so bot owners can pick a per-bot
override with evidence instead of vibes.

The threshold is overridden at runtime via the env var
``RAGBOT_RERANKER_MIN_SCORE_ACTIVE_OVERRIDE`` (read by ``resolve_bot_limit``
in ``bot_limits.py`` if present, else falls back to plan_limits chain). The
script never mutates the DB or ``shared/constants.py`` permanently — the
override is set per-sweep and cleared at the end.

Usage::

    set -a && source .env && set +a
    python scripts/reranker_threshold_ab_test.py \\
        --bot <bot-slug> --workspace-id default --channel-type web \\
        --threshold-values 0.20,0.30,0.40,0.50 \\
        --dataset <path-to-golden-dataset.json>

Outputs (always under ``reports/``):
    reports/reranker_ab_<bot>_<TS>.json   — full per-turn payload + summary
    reports/reranker_ab_<bot>_<TS>.csv    — one row per (threshold, turn)

Exit codes: 0 = success, 2 = dataset missing/invalid, 3 = API unreachable,
4 = invalid threshold value.

Quality Gate #10: this tool NEVER injects text into the LLM prompt and
NEVER overrides the bot answer. It simply sweeps a knob and records.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------- constants --------------------------------------------------------
DEFAULT_BASE_URL_FALLBACK = "http://localhost:3004"
DEFAULT_PACE_SECONDS: float = 0.1
DEFAULT_TIMEOUT_SECONDS: float = 120.0
DEFAULT_REPORTS_DIR = "reports"
THRESHOLD_OVERRIDE_ENV = "RAGBOT_RERANKER_MIN_SCORE_ACTIVE_OVERRIDE"

EXIT_DATASET_MISSING = 2
EXIT_API_UNREACHABLE = 3
EXIT_INVALID_THRESHOLD = 4

# Refuse heuristic — kept module-private and conservative; the platform's
# bots.oos_answer_template is the source of truth for actual refusal text.
_REFUSE_PHRASES = (
    "không có thông tin",
    "không tìm thấy",
    "i don't have",
    "i do not have",
    "out of scope",
    "ngoài phạm vi",
)


# ---------- data shapes ------------------------------------------------------
@dataclass
class TurnResult:
    threshold: float
    question: str
    answer: str
    refused: bool
    top_score: float | None
    chunks_used: int
    latency_ms: int
    cost_usd: float | None
    trace_id: str | None
    error: str | None


@dataclass
class ThresholdSummary:
    threshold: float
    n: int
    n_answered: int
    n_refused: int
    n_error: int
    pass_rate: float           # answered / (n - error)
    refuse_rate: float         # refused / n
    p50_latency_ms: int
    p95_latency_ms: int
    avg_top_score: float
    score_buckets: dict[str, int] = field(default_factory=dict)


@dataclass
class SweepReport:
    generated_at: str
    bot_id: str
    workspace_id: str
    channel_type: str
    dataset_path: str
    dataset_size: int
    threshold_values: list[float]
    base_url: str
    per_threshold: list[ThresholdSummary]
    raw: list[TurnResult]


# ---------- helpers ----------------------------------------------------------
def _bypass_headers() -> dict[str, str]:
    """Loadtest bypass header, if operator set RAGBOT_LOADTEST_BYPASS_TOKEN."""
    token = os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")
    if not token:
        return {}
    return {"X-Loadtest-Bypass": token}


def parse_threshold_csv(raw: str) -> list[float]:
    """Parse '0.20,0.30,0.40' → [0.20, 0.30, 0.40]. Raises ValueError on bad input."""
    if not raw or not raw.strip():
        raise ValueError("threshold-values must be non-empty CSV")
    out: list[float] = []
    for chunk in raw.split(","):
        s = chunk.strip()
        if not s:
            continue
        try:
            v = float(s)
        except ValueError as exc:
            raise ValueError(f"invalid threshold value: {s!r}") from exc
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"threshold value out of [0,1]: {v}")
        out.append(v)
    if not out:
        raise ValueError("no valid threshold values parsed")
    return out


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Read RAGAS-style golden dataset (list of objects with `question`)."""
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"dataset must be a JSON list, got {type(data).__name__}")
    cleaned: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            continue
        q = row.get("question")
        if not isinstance(q, str) or not q.strip():
            continue
        cleaned.append({"question": q, "_idx": i})
    if not cleaned:
        raise ValueError("dataset has no usable questions (need `question` field)")
    return cleaned


def is_refuse(answer: str | None) -> bool:
    if not answer:
        return False
    low = answer.lower()
    return any(p in low for p in _REFUSE_PHRASES)


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return s[idx]


def score_bucket(top_score: float | None) -> str:
    """Bucket top_score into named bins for histogram display."""
    if top_score is None:
        return "null"
    if top_score < 0.10:
        return "0.00-0.09"
    if top_score < 0.20:
        return "0.10-0.19"
    if top_score < 0.30:
        return "0.20-0.29"
    if top_score < 0.40:
        return "0.30-0.39"
    if top_score < 0.50:
        return "0.40-0.49"
    return "0.50+"


# ---------- HTTP ------------------------------------------------------------
async def get_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    r = await client.get(
        f"{base_url}/api/ragbot/test/tokens/self",
        headers=_bypass_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


async def ask_one(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    bot_id: str,
    workspace_id: str,
    channel_type: str,
    question: str,
    connect_id: str,
) -> dict[str, Any]:
    body = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "workspace_id": workspace_id,
        "question": question,
        "connect_id": connect_id,
        "bypass_cache": True,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **_bypass_headers(),
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{base_url}/api/ragbot/test/chat",
            headers=headers,
            json=body,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        d = r.json()
        d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        return d
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }


# ---------- sweep core ------------------------------------------------------
async def run_threshold(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    threshold: float,
    bot_id: str,
    workspace_id: str,
    channel_type: str,
    dataset: list[dict[str, Any]],
    pace: float,
) -> list[TurnResult]:
    """Run all dataset turns at a single threshold value.

    Threshold is exposed via env var ``RAGBOT_RERANKER_MIN_SCORE_ACTIVE_OVERRIDE``
    so the server-side `resolve_bot_limit` chain can pick it up if wired. Bots
    without that wiring will fall back to plan_limits/system_config/schema
    default — the test is still useful (compares zero-override baseline).

    Token rotation (WE-4 fix): a long sweep crosses the 5-min Redis cache
    TTL of ``/api/ragbot/test/tokens/self`` and any parallel call to that
    endpoint bumps the ``ver`` claim — both of which invalidate ``token``
    mid-run. Each threshold pass refreshes the token at entry; one 401 also
    triggers a single retry with a fresh token before the row is marked
    errored.
    """
    os.environ[THRESHOLD_OVERRIDE_ENV] = f"{threshold:.4f}"
    # Refresh token at the start of every threshold — covers TTL expiry
    # and concurrent ver-bumps from sibling tabs/agents.
    try:
        token = await get_self_token(client, base_url)
    except httpx.HTTPError:
        # Fall through with the inherited token; ask_one will surface the error
        pass
    results: list[TurnResult] = []
    for i, turn in enumerate(dataset, 1):
        connect_id = f"ab-test-{threshold:.2f}-{int(time.time())}-{i}"
        resp = await ask_one(
            client,
            base_url=base_url,
            token=token,
            bot_id=bot_id,
            workspace_id=workspace_id,
            channel_type=channel_type,
            question=turn["question"],
            connect_id=connect_id,
        )
        # Detect 401 token expiry and retry once with a fresh token.
        if resp.get("error") and "401" in resp["error"]:
            try:
                token = await get_self_token(client, base_url)
                resp = await ask_one(
                    client,
                    base_url=base_url,
                    token=token,
                    bot_id=bot_id,
                    workspace_id=workspace_id,
                    channel_type=channel_type,
                    question=turn["question"],
                    connect_id=connect_id + "-retry",
                )
            except httpx.HTTPError:
                pass
        answer = resp.get("answer", "") or ""
        results.append(
            TurnResult(
                threshold=threshold,
                question=turn["question"][:200],
                answer=answer[:600],
                refused=is_refuse(answer),
                top_score=resp.get("top_score"),
                chunks_used=int(resp.get("chunks_used", 0) or 0),
                latency_ms=int(resp.get("latency_ms", 0) or 0),
                cost_usd=resp.get("cost_usd"),
                trace_id=resp.get("trace_id"),
                error=resp.get("error"),
            )
        )
        if pace > 0:
            await asyncio.sleep(pace)
    return results


def summarize(threshold: float, rows: list[TurnResult]) -> ThresholdSummary:
    n = len(rows)
    n_error = sum(1 for r in rows if r.error)
    n_refused = sum(1 for r in rows if r.refused and not r.error)
    n_answered = sum(1 for r in rows if not r.refused and not r.error)
    answerable = n - n_error
    pass_rate = (n_answered / answerable) if answerable > 0 else 0.0
    refuse_rate = (n_refused / n) if n > 0 else 0.0
    lats = [float(r.latency_ms) for r in rows if r.latency_ms > 0]
    scores = [r.top_score for r in rows if isinstance(r.top_score, (int, float))]
    avg_score = (sum(scores) / len(scores)) if scores else 0.0
    buckets: dict[str, int] = {}
    for r in rows:
        b = score_bucket(r.top_score)
        buckets[b] = buckets.get(b, 0) + 1
    return ThresholdSummary(
        threshold=threshold,
        n=n,
        n_answered=n_answered,
        n_refused=n_refused,
        n_error=n_error,
        pass_rate=round(pass_rate, 4),
        refuse_rate=round(refuse_rate, 4),
        p50_latency_ms=int(percentile(lats, 50)),
        p95_latency_ms=int(percentile(lats, 95)),
        avg_top_score=round(avg_score, 4),
        score_buckets=buckets,
    )


# ---------- output ----------------------------------------------------------
def write_json(path: Path, report: SweepReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": report.generated_at,
        "bot_id": report.bot_id,
        "workspace_id": report.workspace_id,
        "channel_type": report.channel_type,
        "dataset_path": report.dataset_path,
        "dataset_size": report.dataset_size,
        "threshold_values": report.threshold_values,
        "base_url": report.base_url,
        "per_threshold": [asdict(s) for s in report.per_threshold],
        "raw": [asdict(r) for r in report.raw],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[TurnResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "threshold", "question", "refused", "top_score",
        "chunks_used", "latency_ms", "cost_usd", "trace_id", "error",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow([
                f"{r.threshold:.4f}",
                r.question.replace("\n", " ")[:200],
                "1" if r.refused else "0",
                "" if r.top_score is None else f"{r.top_score:.4f}",
                r.chunks_used,
                r.latency_ms,
                "" if r.cost_usd is None else f"{r.cost_usd:.6f}",
                r.trace_id or "",
                (r.error or "")[:200],
            ])


def print_summary(report: SweepReport) -> None:
    print()
    print("=" * 78)
    print(f"  RERANKER THRESHOLD A/B — bot={report.bot_id} ws={report.workspace_id}")
    print("=" * 78)
    print(f"  dataset={report.dataset_path}  n={report.dataset_size}  base_url={report.base_url}")
    print()
    header = (
        f"  {'threshold':<10} {'n':>4} {'pass':>6} {'refuse':>7} "
        f"{'p50':>7} {'p95':>7} {'avg_score':>10}"
    )
    print(header)
    print("  " + "-" * 60)
    for s in report.per_threshold:
        print(
            f"  {s.threshold:<10.2f} {s.n:>4} "
            f"{s.pass_rate * 100:>5.1f}% "
            f"{s.refuse_rate * 100:>6.1f}% "
            f"{s.p50_latency_ms:>6}ms {s.p95_latency_ms:>6}ms "
            f"{s.avg_top_score:>10.4f}"
        )


# ---------- main ------------------------------------------------------------
async def _async_main(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.environ.get("RAGBOT_BASE_URL", DEFAULT_BASE_URL_FALLBACK)
    try:
        thresholds = parse_threshold_csv(args.threshold_values)
    except ValueError as exc:
        sys.stderr.write(f"[ab-test] invalid threshold-values: {exc}\n")
        return EXIT_INVALID_THRESHOLD

    dataset_path = Path(args.dataset)
    try:
        dataset = load_dataset(dataset_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[ab-test] dataset error: {exc}\n")
        return EXIT_DATASET_MISSING

    if args.limit and args.limit > 0:
        dataset = dataset[: args.limit]

    print(
        f"[ab-test] bot={args.bot} ws={args.workspace_id} ch={args.channel_type} "
        f"thresholds={thresholds} dataset_n={len(dataset)}"
    )

    all_rows: list[TurnResult] = []
    per_threshold: list[ThresholdSummary] = []
    try:
        async with httpx.AsyncClient() as client:
            token = await get_self_token(client, base_url)
            for thr in thresholds:
                print(f"[ab-test] >>> threshold={thr:.2f}")
                rows = await run_threshold(
                    client,
                    base_url=base_url,
                    token=token,
                    threshold=thr,
                    bot_id=args.bot,
                    workspace_id=args.workspace_id,
                    channel_type=args.channel_type,
                    dataset=dataset,
                    pace=args.pace,
                )
                all_rows.extend(rows)
                summary = summarize(thr, rows)
                per_threshold.append(summary)
                print(
                    f"[ab-test] <<< threshold={thr:.2f} "
                    f"pass={summary.pass_rate * 100:.1f}% "
                    f"refuse={summary.refuse_rate * 100:.1f}% "
                    f"p95={summary.p95_latency_ms}ms"
                )
    except httpx.HTTPError as exc:
        sys.stderr.write(f"[ab-test] API error: {exc}\n")
        return EXIT_API_UNREACHABLE
    finally:
        os.environ.pop(THRESHOLD_OVERRIDE_ENV, None)

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_json = Path(args.output or f"{DEFAULT_REPORTS_DIR}/reranker_ab_{args.bot}_{ts}.json")
    out_csv = out_json.with_suffix(".csv")
    report = SweepReport(
        generated_at=ts,
        bot_id=args.bot,
        workspace_id=args.workspace_id,
        channel_type=args.channel_type,
        dataset_path=str(dataset_path),
        dataset_size=len(dataset),
        threshold_values=thresholds,
        base_url=base_url,
        per_threshold=per_threshold,
        raw=all_rows,
    )
    write_json(out_json, report)
    write_csv(out_csv, all_rows)
    print_summary(report)
    print()
    print(f"  → JSON: {out_json}")
    print(f"  → CSV:  {out_csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bot", required=True, help="bot_id slug (4-key external)")
    p.add_argument("--workspace-id", default="default",
                   help="workspace_id slug (default: 'default')")
    p.add_argument("--channel-type", default="web",
                   help="channel_type — default 'web'")
    p.add_argument("--threshold-values", required=True,
                   help="Comma-separated floats in [0,1], e.g. 0.20,0.30,0.40,0.50")
    p.add_argument("--dataset", required=True,
                   help="Path to RAGAS golden JSON (list of {question, ...})")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap dataset size for smoke runs (0 = no cap)")
    p.add_argument("--pace", type=float, default=DEFAULT_PACE_SECONDS,
                   help=f"Sleep between turns (default {DEFAULT_PACE_SECONDS}s)")
    p.add_argument("--base-url", default=None,
                   help="Override RAGBOT_BASE_URL env")
    p.add_argument("--output", default=None,
                   help="Override JSON output path (CSV derived from .json)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
