#!/usr/bin/env python3
"""Aggregate per-room load-test JSONs into a single markdown report.

Usage:
  python3 scripts/analyze_75q_results.py \\
    --inputs '/tmp/<bot>_75q_room*.json' \\
    --output reports/LOAD_TEST_AGGREGATE_<ts>.md

Room topics resolved (in order of precedence):
  1) `--room-topics-file` JSON {"<room_num>": "<label>"}
  2) `payload.config.room_topics` dict written by upstream runner
  3) literal "Room <N>" placeholder (no domain assumption)
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

CLASS_ORDER = ["PASS", "REFUSE_NO_DOCS", "REFUSE_WITH_DOCS", "FAIL", "ERROR"]
TOP_REFUSE_LIMIT = 10


def percentile(sorted_vals: list[int], p: float) -> int:
    """Nearest-rank percentile."""
    if not sorted_vals:
        return 0
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def load_inputs(paths: list[str]) -> list[dict[str, Any]]:
    """Resolve glob patterns + load JSON payloads."""
    expanded: list[str] = []
    for p in paths:
        matched = sorted(glob.glob(p))
        if matched:
            expanded.extend(matched)
        elif Path(p).exists():
            expanded.append(p)
    payloads: list[dict[str, Any]] = []
    for p in expanded:
        with open(p, encoding="utf-8") as f:
            payloads.append(json.load(f))
    return payloads


def _resolve_room_topics(
    payloads: list[dict[str, Any]],
    override: dict[str, str] | None,
) -> dict[int, str]:
    """Resolve room→topic mapping. Caller override > payload config > empty."""
    out: dict[int, str] = {}
    if override:
        for k, v in override.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    for pl in payloads:
        cfg_topics = (pl.get("config") or {}).get("room_topics") or {}
        if isinstance(cfg_topics, dict):
            for k, v in cfg_topics.items():
                try:
                    out[int(k)] = str(v)
                except (TypeError, ValueError):
                    continue
    return out


def render_report(
    payloads: list[dict[str, Any]],
    room_topics: dict[int, str] | None = None,
) -> str:
    """Build markdown report from per-room payloads."""
    all_turns: list[dict[str, Any]] = []
    for pl in payloads:
        all_turns.extend(pl.get("turns") or [])
    n = len(all_turns)
    if not n:
        return "# Load Test Result\n\nNo turns found.\n"
    topics = room_topics or {}

    counts: dict[str, int] = defaultdict(int)
    per_room_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_room_dur: dict[int, list[int]] = defaultdict(list)
    cost_total = 0.0
    tokens_in = 0
    tokens_out = 0
    durations_all: list[int] = []
    refuse_with_docs: list[dict[str, Any]] = []

    for t in all_turns:
        cls = t.get("classification") or "ERROR"
        room = int(t.get("room") or 0)
        counts[cls] += 1
        per_room_counts[room][cls] += 1
        cost_total += float(t.get("cost_usd") or 0.0)
        tokens_in += int(t.get("tokens_in") or 0)
        tokens_out += int(t.get("tokens_out") or 0)
        d = int(t.get("duration_ms") or 0)
        if d > 0:
            durations_all.append(d)
            per_room_dur[room].append(d)
        if cls == "REFUSE_WITH_DOCS":
            refuse_with_docs.append(t)

    durations_sorted = sorted(durations_all)
    p50 = percentile(durations_sorted, 50)
    p95 = percentile(durations_sorted, 95)
    p99 = percentile(durations_sorted, 99)
    max_lat = durations_sorted[-1] if durations_sorted else 0
    timeouts = sum(
        1
        for t in all_turns
        if (t.get("error") or "").lower().count("timeout")
        or (t.get("error") or "").lower().count("readtimeout")
    )

    cfg = payloads[0].get("config", {}) if payloads else {}

    lines: list[str] = []
    lines.append("# Load Test Result")
    lines.append("")
    lines.append(f"- **Bot**: `{cfg.get('bot_id', '?')}` tenant=`{cfg.get('tenant_id', '?')}` channel=`{cfg.get('channel_type', '?')}`")
    lines.append(f"- **Source**: `{cfg.get('questions_file', '?')}`")
    lines.append(f"- **Inputs**: {len(payloads)} room JSONs merged")
    lines.append(f"- **bypass_cache**: {cfg.get('bypass_cache')}")
    lines.append("")
    lines.append("## Overall classification")
    lines.append("")
    lines.append(f"TOTAL: **{n}**")
    lines.append("")
    for cls in CLASS_ORDER:
        c = counts.get(cls, 0)
        pct = round(100.0 * c / n, 1)
        lines.append(f"- {cls:18s}: **{c}** ({pct}%)")
    lines.append("")

    lines.append("## Per-room breakdown")
    lines.append("")
    lines.append("| Room | Topic | N | PASS | REFUSE_NO_DOCS | REFUSE_WITH_DOCS | FAIL | ERROR | p50 ms | p95 ms |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for room in sorted(per_room_counts):
        rc = per_room_counts[room]
        rd = sorted(per_room_dur.get(room, []))
        rn = sum(rc.values())
        topic = topics.get(room) or f"Room {room}"
        lines.append(
            f"| {room} | {topic} | {rn} | "
            f"{rc.get('PASS', 0)} | {rc.get('REFUSE_NO_DOCS', 0)} | "
            f"{rc.get('REFUSE_WITH_DOCS', 0)} | {rc.get('FAIL', 0)} | "
            f"{rc.get('ERROR', 0)} | {percentile(rd, 50)} | {percentile(rd, 95)} |"
        )
    lines.append("")

    lines.append("## Cost stats")
    lines.append("")
    lines.append(f"- Total: **${cost_total:.4f}**")
    lines.append(f"- Per-turn avg: **${cost_total / n:.5f}**")
    lines.append(f"- Tokens in / out: {tokens_in:,} / {tokens_out:,}")
    lines.append("")

    lines.append("## Latency stats")
    lines.append("")
    lines.append(f"- p50: **{p50} ms**")
    lines.append(f"- p95: **{p95} ms**")
    lines.append(f"- p99: **{p99} ms**")
    lines.append(f"- max: {max_lat} ms")
    lines.append(f"- timeouts: {timeouts}")
    lines.append(f"- non-zero duration samples: {len(durations_all)} / {n}")
    lines.append("")

    lines.append(f"## Top REFUSE_WITH_DOCS questions (potential bot smartness gap, max {TOP_REFUSE_LIMIT})")
    lines.append("")
    if not refuse_with_docs:
        lines.append("_None — bot did not refuse on questions with docs match._")
    else:
        lines.append("| Room | Q | chunks | top_score | top_score_min | answer (head) |")
        lines.append("|---|---|---|---|---|---|")
        sorted_rw = sorted(
            refuse_with_docs,
            key=lambda t: float(t.get("top_score") or 0.0),
            reverse=True,
        )[:TOP_REFUSE_LIMIT]
        for t in sorted_rw:
            q = (t.get("question") or "")[:80].replace("|", "\\|")
            ans = (t.get("answer") or "")[:80].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {t.get('room')} | {q} | {t.get('chunks_used')} | "
                f"{float(t.get('top_score') or 0):.3f} | "
                f"{float(t.get('top_score_min') or 0):.3f} | {ans} |"
            )
    lines.append("")

    # Repeat-probe (last question of each room) — quick cache-hit eyeball
    lines.append("## Repeat-probe (last question per room)")
    lines.append("")
    lines.append("| Room | Question | classification | duration ms | chunks |")
    lines.append("|---|---|---|---|---|")
    for t in all_turns:
        if t.get("is_repeat_probe"):
            q = (t.get("question") or "")[:60].replace("|", "\\|")
            lines.append(
                f"| {t.get('room')} | {q} | {t.get('classification')} | "
                f"{t.get('duration_ms')} | {t.get('chunks_used')} |"
            )
    lines.append("")

    lines.append("## ERROR turns (if any)")
    lines.append("")
    err_turns = [t for t in all_turns if t.get("classification") == "ERROR"]
    if not err_turns:
        lines.append("_None._")
    else:
        lines.append("| Room | idx | question | error |")
        lines.append("|---|---|---|---|")
        for t in err_turns[:20]:
            q = (t.get("question") or "")[:60].replace("|", "\\|")
            err = (t.get("error") or "")[:120].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {t.get('room')} | {t.get('idx')} | {q} | {err} |")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate load-test room JSONs into markdown report.")
    ap.add_argument("--inputs", nargs="+", required=True, help="JSON paths or globs")
    ap.add_argument("--output", required=True, help="Output markdown path")
    ap.add_argument(
        "--room-topics-file",
        default=None,
        help='Optional JSON {"<room_num>": "<label>"} to label rooms (overrides payload config).',
    )
    args = ap.parse_args(argv)

    payloads = load_inputs(args.inputs)
    if not payloads:
        print(f"ERROR: no input files matched {args.inputs}", file=sys.stderr)
        return 2

    override: dict[str, str] | None = None
    if args.room_topics_file:
        try:
            override = json.loads(Path(args.room_topics_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: cannot read --room-topics-file: {exc}", file=sys.stderr)

    topics = _resolve_room_topics(payloads, override)
    out = render_report(payloads, room_topics=topics)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")
    print(f"Wrote {out_path} ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
