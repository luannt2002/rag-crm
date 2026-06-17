#!/usr/bin/env python3
"""Per-batch deepdive analyser for BATCH-10 mode load-test outputs.

Companion to ``scripts/loadtest_batch_analyze.py`` (which only mirrors the
live-mode markdown). This deepdive adds per-batch table + worst-N + failure
mode auto-classification + latency progression + cumulative cost + optional
cross-round trend mode.

App-mindset (CLAUDE.md): pure analyser, never invokes the LLM, never
injects text, never overrides model output. Read-only on inputs.
Domain-neutral. Zero-hardcode (every threshold below is a module-level
``Final[...]`` constant declared at top).

Usage::

    .venv/bin/python scripts/loadtest_perbatch_deepdive.py \\
        --input /tmp/mega_round8_OLD_<ts>.json \\
        --batch-size 10 \\
        --output /tmp/r8_old_deepdive.md

    .venv/bin/python scripts/loadtest_perbatch_deepdive.py \\
        --batch-glob "/tmp/mega_round_RA_OLD_*.batch_*.json" \\
        --output /tmp/RA_OLD_deepdive.md

    .venv/bin/python scripts/loadtest_perbatch_deepdive.py \\
        --trend "/tmp/mega_round{7,8,9}_*_*.json" \\
        --output /tmp/trend_R7_R8_R9.md
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Final

# ---- Tuning constants (zero-hardcode) -----------------------------------
RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD: Final[float] = 0.05
LATENCY_OUTLIER_DURATION_MS: Final[int] = 15_000
PER_BATCH_WORST_TOP_N: Final[int] = 3
PREVIEW_CHARS: Final[int] = 110
DEFAULT_BATCH_GLOB_BATCH_SIZE: Final[int] = 10
_BUCKET_PRIORITY: Final[dict[str, int]] = {
    "ERROR": 5,
    "EMPTY_FAIL": 4,
    "REFUSE_NO_DOCS": 3,
    "REFUSE_WITH_DOCS": 2,
    "PASS": 0,
}
FAILURE_MODES: Final[tuple[str, ...]] = (
    "CORPUS_GAP",
    "RETRIEVAL_WEAK",
    "LATENCY_OUTLIER",
    "STREAM_ERROR",
    "EMPTY_GENERATE",
)
BUCKETS: Final[tuple[str, ...]] = (
    "PASS",
    "REFUSE_NO_DOCS",
    "REFUSE_WITH_DOCS",
    "EMPTY_FAIL",
    "ERROR",
)


# ---- Pure helpers -------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Inclusive percentile of an already-sorted ascending list."""
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return float(sorted_values[0])
    if pct >= 100:
        return float(sorted_values[-1])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f] * (c - k) + sorted_values[c] * (k - f))


def classify_failure_modes(turn: dict[str, Any]) -> list[str]:
    """Return the failure-mode labels that apply to this turn (multi-label)."""
    labels: list[str] = []
    cls = turn.get("classification") or ""
    chunks_used = int(turn.get("chunks_used") or 0)
    top_score = float(turn.get("top_score") or 0.0)
    duration_ms = int(turn.get("duration_ms") or 0)
    if cls == "ERROR":
        labels.append("STREAM_ERROR")
    if cls == "EMPTY_FAIL":
        labels.append("EMPTY_GENERATE")
    if cls != "PASS":
        if chunks_used == 0:
            labels.append("CORPUS_GAP")
        elif top_score < RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD:
            labels.append("RETRIEVAL_WEAK")
    if duration_ms > LATENCY_OUTLIER_DURATION_MS:
        labels.append("LATENCY_OUTLIER")
    return labels


def _worst_score(turn: dict[str, Any]) -> tuple[int, int, float]:
    cls = turn.get("classification") or ""
    bucket_pri = _BUCKET_PRIORITY.get(cls, 1)
    chunks_used = int(turn.get("chunks_used") or 0)
    top_score = float(turn.get("top_score") or 0.0)
    return (-bucket_pri, -1 if chunks_used == 0 else 0, top_score)


def slice_turns(
    turns: list[dict[str, Any]], *, batch_size: int
) -> list[tuple[int, int, list[dict[str, Any]]]]:
    """Split flat turn list into ``(lo, hi, sublist)`` 1-indexed tuples."""
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    out: list[tuple[int, int, list[dict[str, Any]]]] = []
    n = len(turns)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        out.append((start + 1, end, turns[start:end]))
    return out


def summarize_batch(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute one batch's deep-dive summary dict (JSON-friendly)."""
    n = len(turns)
    counts = Counter(t.get("classification") or "UNKNOWN" for t in turns)
    pass_turns = [t for t in turns if t.get("classification") == "PASS"]
    top_score_avg_pass = (
        round(
            sum(float(t.get("top_score") or 0.0) for t in pass_turns) / len(pass_turns), 4
        )
        if pass_turns
        else 0.0
    )
    durations = sorted(
        float(t.get("duration_ms") or 0)
        for t in turns
        if (t.get("duration_ms") or 0) > 0
    )
    cost_total = round(sum(float(t.get("cost_usd") or 0.0) for t in turns), 6)
    fm: Counter[str] = Counter()
    for t in turns:
        for mode in classify_failure_modes(t):
            fm[mode] += 1
    non_pass = [t for t in turns if t.get("classification") != "PASS"]
    pool = non_pass if non_pass else turns
    worst = sorted(pool, key=_worst_score)[:PER_BATCH_WORST_TOP_N]
    return {
        "total_turns": n,
        "counts": dict(counts),
        "top_score_avg_pass": top_score_avg_pass,
        "latency_ms_p50": int(_percentile(durations, 50)),
        "latency_ms_p95": int(_percentile(durations, 95)),
        "latency_ms_p99": int(_percentile(durations, 99)),
        "latency_ms_max": int(durations[-1]) if durations else 0,
        "cost_usd_total": cost_total,
        "failure_modes": dict(fm),
        "worst_turns": worst,
    }


# ---- Markdown rendering -------------------------------------------------


def _truncate(text: str | None, *, n: int = PREVIEW_CHARS) -> str:
    s = (text or "").replace("\n", " ").replace("|", "/").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _render_table(headers: list[str], rows: list[list[str]], *, right_align_from: int = 1) -> str:
    sep = []
    for i in range(len(headers)):
        sep.append("---:" if i >= right_align_from else "---")
    parts = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    for r in rows:
        parts.append("| " + " | ".join(r) + " |")
    return "\n".join(parts)


def render_per_batch_table(summaries: list[dict[str, Any]]) -> str:
    headers = [
        "Batch", "Turns", "PASS", "REFUSE_NO_DOCS", "REFUSE_WITH_DOCS",
        "EMPTY_FAIL", "ERROR", "top_score(PASS)", "p95 ms", "$/batch",
    ]
    rows: list[list[str]] = []
    for s in summaries:
        c = s["summary"]["counts"]
        rows.append([
            str(s["idx"]),
            str(s["summary"]["total_turns"]),
            str(c.get("PASS", 0)),
            str(c.get("REFUSE_NO_DOCS", 0)),
            str(c.get("REFUSE_WITH_DOCS", 0)),
            str(c.get("EMPTY_FAIL", 0)),
            str(c.get("ERROR", 0)),
            f"{s['summary']['top_score_avg_pass']:.4f}",
            str(s["summary"]["latency_ms_p95"]),
            f"${s['summary']['cost_usd_total']:.5f}",
        ])
    return "## Per-batch overview\n\n" + _render_table(headers, rows) + "\n"


def render_failure_mode_table(summaries: list[dict[str, Any]]) -> str:
    headers = ["Batch", *FAILURE_MODES]
    rows: list[list[str]] = []
    portfolio: Counter[str] = Counter()
    for s in summaries:
        fm = s["summary"]["failure_modes"]
        row = [str(s["idx"])]
        for mode in FAILURE_MODES:
            v = int(fm.get(mode, 0))
            portfolio[mode] += v
            row.append(str(v))
        rows.append(row)
    rows.append(["**Total**", *(f"**{portfolio.get(m, 0)}**" for m in FAILURE_MODES)])
    return "## Per-batch failure-mode breakdown\n\n" + _render_table(headers, rows) + "\n"


def render_worst_per_batch(summaries: list[dict[str, Any]]) -> str:
    parts = [f"## Per-batch worst questions (top {PER_BATCH_WORST_TOP_N})", ""]
    for s in summaries:
        lo, hi = s["turn_range"]
        c = s["summary"]["counts"]
        parts.append(
            f"### Batch {s['idx']} (turns {lo}-{hi}) — PASS={c.get('PASS', 0)}/{s['summary']['total_turns']}"
        )
        worst = s["summary"]["worst_turns"] or []
        if not worst:
            parts.extend(["- (no non-PASS turns)", ""])
            continue
        for i, t in enumerate(worst, start=1):
            modes = classify_failure_modes(t) or ["-"]
            parts.append(
                "{i}. r{room:02d} Q{idx:02d} [{cls}] [{modes}] \"{q}\" — chunks={ck} top={ts:.3f} dur={dur}ms".format(
                    i=i,
                    room=int(t.get("room") or 0),
                    idx=int(t.get("idx") or 0) + 1,
                    cls=t.get("classification") or "?",
                    modes=",".join(modes),
                    q=_truncate(t.get("question")),
                    ck=int(t.get("chunks_used") or 0),
                    ts=float(t.get("top_score") or 0.0),
                    dur=int(t.get("duration_ms") or 0),
                )
            )
        parts.append("")
    return "\n".join(parts)


def render_latency_progression(summaries: list[dict[str, Any]]) -> str:
    headers = ["Batch", "p50 ms", "p95 ms", "p99 ms", "max ms"]
    rows = [
        [
            str(s["idx"]),
            str(s["summary"]["latency_ms_p50"]),
            str(s["summary"]["latency_ms_p95"]),
            str(s["summary"]["latency_ms_p99"]),
            str(s["summary"]["latency_ms_max"]),
        ]
        for s in summaries
    ]
    return "## Latency progression (cache-warming detector)\n\n" + _render_table(headers, rows) + "\n"


def render_cumulative_cost(summaries: list[dict[str, Any]]) -> str:
    headers = ["Batch", "$/batch", "$ cumulative"]
    rows: list[list[str]] = []
    cum = 0.0
    for s in summaries:
        c = float(s["summary"]["cost_usd_total"])
        cum += c
        rows.append([str(s["idx"]), f"${c:.5f}", f"${cum:.5f}"])
    return "## Cumulative cost across batches\n\n" + _render_table(headers, rows) + "\n"


def render_intent_breakdown(turns: list[dict[str, Any]]) -> str:
    have_intent = [t for t in turns if t.get("intent")]
    if not have_intent:
        return ""
    by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in have_intent:
        by_intent[str(t.get("intent"))].append(t)
    headers = ["Intent", "Turns", "PASS", "PASS%"]
    rows: list[list[str]] = []
    for intent, items in sorted(by_intent.items()):
        n = len(items)
        p = sum(1 for x in items if x.get("classification") == "PASS")
        pct = round(100.0 * p / n, 1) if n else 0.0
        rows.append([intent, str(n), str(p), f"{pct}%"])
    return "## Per-intent breakdown\n\n" + _render_table(headers, rows) + "\n"


def render_report(
    *,
    source_label: str,
    config_block: dict[str, Any] | None,
    turns: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> str:
    parts: list[str] = [f"# Per-batch deep-dive — {source_label}", ""]
    if config_block:
        parts.extend([
            "## Source config",
            "",
            f"- bot_id: `{config_block.get('bot_id', '')}`",
            f"- tenant_id: `{config_block.get('tenant_id', '')}`",
            f"- channel_type: `{config_block.get('channel_type', '')}`",
            f"- rooms: `{config_block.get('rooms', '')}`",
            f"- total turns: `{len(turns)}`",
            "",
        ])
    parts.extend([
        "## Thresholds in use",
        "",
        f"- RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD = `{RETRIEVAL_WEAK_TOP_SCORE_THRESHOLD}`",
        f"- LATENCY_OUTLIER_DURATION_MS = `{LATENCY_OUTLIER_DURATION_MS}`",
        f"- PER_BATCH_WORST_TOP_N = `{PER_BATCH_WORST_TOP_N}`",
        "",
        render_per_batch_table(summaries),
        render_failure_mode_table(summaries),
        render_worst_per_batch(summaries),
        render_latency_progression(summaries),
        render_cumulative_cost(summaries),
    ])
    intent_md = render_intent_breakdown(turns)
    if intent_md:
        parts.append(intent_md)
    return "\n".join(parts) + "\n"


# ---- IO -----------------------------------------------------------------

_BATCH_FILE_RE = re.compile(r"\.batch_(\d+)\.json$")


def load_aggregate(input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    return raw.get("config") or {}, list(raw.get("turns") or [])


def load_batch_glob(
    pattern: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int | None]:
    """Load ``*.batch_NN.json`` files matching pattern, in idx order."""
    files = sorted(glob.glob(pattern))
    if not files:
        raise ValueError(f"no files matched glob: {pattern}")

    def _key(p: str) -> tuple[int, str]:
        m = _BATCH_FILE_RE.search(p)
        return (int(m.group(1)) if m else 1_000_000, p)

    files.sort(key=_key)
    config: dict[str, Any] = {}
    all_turns: list[dict[str, Any]] = []
    declared_size: int | None = None
    for f in files:
        try:
            payload = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to read {f}: {exc}") from exc
        if not config:
            config = payload.get("config") or {}
        batch_meta = payload.get("batch") or {}
        if declared_size is None and batch_meta.get("turn_range"):
            lo, hi = batch_meta["turn_range"]
            declared_size = int(hi) - int(lo) + 1
        all_turns.extend(payload.get("turns") or [])
    return config, all_turns, declared_size


def render_trend(round_files: list[Path]) -> str:
    """Round-over-round table — one row per round, columns = bucket counts."""
    headers = [
        "Round file", "Turns", "PASS", "REFUSE_NO_DOCS", "REFUSE_WITH_DOCS",
        "EMPTY_FAIL", "ERROR", "p95 ms", "$ total",
    ]
    rows: list[list[str]] = []
    for f in round_files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append([f.name, f"(error: {exc})", "", "", "", "", "", "", ""])
            continue
        turns = raw.get("turns") or []
        c = Counter(t.get("classification") or "UNKNOWN" for t in turns)
        durations = sorted(
            float(t.get("duration_ms") or 0)
            for t in turns
            if (t.get("duration_ms") or 0) > 0
        )
        p95 = int(_percentile(durations, 95))
        cost = round(sum(float(t.get("cost_usd") or 0.0) for t in turns), 5)
        rows.append([
            f.name, str(len(turns)),
            str(c.get("PASS", 0)), str(c.get("REFUSE_NO_DOCS", 0)),
            str(c.get("REFUSE_WITH_DOCS", 0)), str(c.get("EMPTY_FAIL", 0)),
            str(c.get("ERROR", 0)), str(p95), f"${cost:.5f}",
        ])
    return "# Cross-round trend\n\n" + _render_table(headers, rows) + "\n"


# ---- Top-level orchestration --------------------------------------------


def build_report(
    *,
    source_label: str,
    config_block: dict[str, Any],
    turns: list[dict[str, Any]],
    batch_size: int,
) -> str:
    if not turns:
        raise ValueError("no turns to analyse")
    batches = slice_turns(turns, batch_size=batch_size)
    summaries = [
        {"idx": idx, "turn_range": [lo, hi], "summary": summarize_batch(sub)}
        for idx, (lo, hi, sub) in enumerate(batches, start=1)
    ]
    return render_report(
        source_label=source_label,
        config_block=config_block,
        turns=turns,
        summaries=summaries,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Per-batch deep-dive analyser for BATCH-10 mode load-test outputs.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to aggregate <output>.json (single file).")
    src.add_argument(
        "--batch-glob",
        help="Glob over *.batch_NN.json checkpoint files (concatenated in idx order).",
    )
    src.add_argument(
        "--trend",
        help="Glob over multiple round aggregate JSONs — emits trend report.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="Batch size for slicing aggregate input (required with --input).",
    )
    p.add_argument("--output", default="", help="Markdown output path; empty=stdout.")
    return p.parse_args()


def _write_or_print(md: str, output: str) -> None:
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote {out_path}")
    else:
        sys.stdout.write(md)


def main() -> int:
    args = _parse_args()
    if args.trend:
        files = [Path(p) for p in sorted(glob.glob(args.trend))]
        if not files:
            print(f"ERROR: no files matched --trend glob: {args.trend}", file=sys.stderr)
            return 2
        _write_or_print(render_trend(files), args.output)
        return 0
    if args.input:
        in_path = Path(args.input)
        if not in_path.exists():
            print(f"ERROR: input not found: {in_path}", file=sys.stderr)
            return 2
        if int(args.batch_size) <= 0:
            print("ERROR: --batch-size must be > 0 when using --input", file=sys.stderr)
            return 2
        config, turns = load_aggregate(in_path)
        md = build_report(
            source_label=in_path.name,
            config_block=config,
            turns=turns,
            batch_size=int(args.batch_size),
        )
        _write_or_print(md, args.output)
        return 0
    config, turns, declared_size = load_batch_glob(args.batch_glob)
    batch_size = (
        int(args.batch_size) if args.batch_size > 0
        else (declared_size or DEFAULT_BATCH_GLOB_BATCH_SIZE)
    )
    md = build_report(
        source_label=f"batch-glob {args.batch_glob}",
        config_block=config,
        turns=turns,
        batch_size=batch_size,
    )
    _write_or_print(md, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
