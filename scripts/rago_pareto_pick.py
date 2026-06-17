#!/usr/bin/env python3
"""rago_pareto_pick.py — Stream D Phase 3 (Paper 26 RAGO Pareto pick).

Reads sweep CSV (output of ``rago_pareto_sweep.py``), computes the
Pareto frontier on three axes (maximise pass_rate, minimise p95_ms,
minimise cost_per_turn), then picks one config per SLA preset and
emits a Markdown verdict file.

Configs with ``hallu_count > 0`` are dropped before frontier compute —
HALLU=0 sacred (CLAUDE.md).

Usage::

    python scripts/rago_pareto_pick.py \\
        --input reports/RAGO_PARETO_SWEEP_<date>.csv \\
        --sla-preset latency-priority \\
        --output reports/RAGO_PARETO_VERDICT_<date>.md
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# --- Module constants ----------------------------------------------------- #

SLA_PRESETS = ("latency-priority", "cost-priority", "quality-priority")
QUALITY_FLOOR_MARGIN_PP = 2.0  # PASS rate must stay within 2pp of best
P95_HEADROOM_FACTOR = 1.10  # cost-priority allows p95 up to 1.10× best
COST_HEADROOM_FACTOR = 1.10  # latency-priority allows cost up to 1.10× best
QUALITY_PRIORITY_LATENCY_FACTOR = 1.50  # quality preset allows p95 ≤ 1.5× best
QUALITY_PRIORITY_COST_FACTOR = 1.50

KNOB_KEYS_DEFAULT = (
    "chunk_size",
    "chunk_overlap",
    # Wave M3.3-D — renamed legacy ``top_k_retrieve`` / ``top_k_rerank``
    # to the canonical production keys ``rag_top_k`` / ``rag_rerank_top_n``
    # so Pareto sweep analysis reads the SAME knobs that production
    # ``chat_worker._build_pipeline_config`` reads (pre-fix the script
    # picked up legacy keys with value=10/5 while production was using
    # the canonical keys with value=20/10 → decisions misaligned).
    "rag_top_k",
    "rag_rerank_top_n",
    "multi_query_n_variants",
    "rrf_k",
    "reranker_enabled",
    "reranker_min_score_active",
    "multi_query_enabled",
    "grade_use_structured_output",
    "grade_use_batch",
    "pipeline_parallel_rewrite_mq_enabled",
)


@dataclass
class SweepRow:
    config_id: int
    knob_values: dict[str, Any]
    n_turns: int
    pass_rate: float
    p95_ms: float
    cost_per_turn: float
    hallu_count: int
    error_count: int


# --- CSV loader ----------------------------------------------------------- #


def load_sweep_csv(path: Path) -> tuple[list[SweepRow], list[str]]:
    rows: list[SweepRow] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        knob_keys = [
            k for k in (reader.fieldnames or [])
            if k not in {
                "config_id", "n_turns", "pass_rate", "p95_ms",
                "cost_per_turn", "hallu_count", "error_count",
            }
        ]
        for r in reader:
            knob_values = {k: _coerce_csv(r.get(k, "")) for k in knob_keys}
            rows.append(
                SweepRow(
                    config_id=int(r["config_id"]),
                    knob_values=knob_values,
                    n_turns=int(r["n_turns"]),
                    pass_rate=float(r["pass_rate"]),
                    p95_ms=float(r["p95_ms"]),
                    cost_per_turn=float(r["cost_per_turn"]),
                    hallu_count=int(r["hallu_count"]),
                    error_count=int(r["error_count"]),
                )
            )
    return rows, knob_keys


def _coerce_csv(raw: str) -> Any:
    if raw == "":
        return None
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


# --- Pareto frontier ------------------------------------------------------ #


def is_dominated(a: SweepRow, b: SweepRow) -> bool:
    """True if b dominates a (b is at least as good on every axis, strictly
    better on at least one)."""
    not_worse = (
        b.pass_rate >= a.pass_rate
        and b.p95_ms <= a.p95_ms
        and b.cost_per_turn <= a.cost_per_turn
    )
    strictly_better = (
        b.pass_rate > a.pass_rate
        or b.p95_ms < a.p95_ms
        or b.cost_per_turn < a.cost_per_turn
    )
    return not_worse and strictly_better


def compute_frontier(rows: list[SweepRow]) -> list[SweepRow]:
    """Return Pareto-optimal subset (none dominated by any other)."""
    frontier: list[SweepRow] = []
    for a in rows:
        if not any(is_dominated(a, b) for b in rows if b is not a):
            frontier.append(a)
    return frontier


# --- SLA presets ---------------------------------------------------------- #


def pick_latency_priority(frontier: list[SweepRow]) -> SweepRow | None:
    """Lowest p95 with quality not far below best, cost in headroom."""
    if not frontier:
        return None
    best_pass = max(r.pass_rate for r in frontier)
    best_cost = min(r.cost_per_turn for r in frontier)
    quality_floor = best_pass - (QUALITY_FLOOR_MARGIN_PP / 100.0)
    cost_ceiling = best_cost * COST_HEADROOM_FACTOR
    eligible = [
        r for r in frontier
        if r.pass_rate >= quality_floor and r.cost_per_turn <= cost_ceiling
    ]
    return min(eligible, key=lambda r: r.p95_ms) if eligible else min(frontier, key=lambda r: r.p95_ms)


def pick_cost_priority(frontier: list[SweepRow]) -> SweepRow | None:
    if not frontier:
        return None
    best_pass = max(r.pass_rate for r in frontier)
    best_p95 = min(r.p95_ms for r in frontier)
    quality_floor = best_pass - (QUALITY_FLOOR_MARGIN_PP / 100.0)
    p95_ceiling = best_p95 * P95_HEADROOM_FACTOR
    eligible = [
        r for r in frontier
        if r.pass_rate >= quality_floor and r.p95_ms <= p95_ceiling
    ]
    return min(eligible, key=lambda r: r.cost_per_turn) if eligible else min(frontier, key=lambda r: r.cost_per_turn)


def pick_quality_priority(frontier: list[SweepRow]) -> SweepRow | None:
    if not frontier:
        return None
    best_p95 = min(r.p95_ms for r in frontier)
    best_cost = min(r.cost_per_turn for r in frontier)
    p95_ceiling = best_p95 * QUALITY_PRIORITY_LATENCY_FACTOR
    cost_ceiling = best_cost * QUALITY_PRIORITY_COST_FACTOR
    eligible = [
        r for r in frontier
        if r.p95_ms <= p95_ceiling and r.cost_per_turn <= cost_ceiling
    ]
    return max(eligible, key=lambda r: r.pass_rate) if eligible else max(frontier, key=lambda r: r.pass_rate)


_PICKERS = {
    "latency-priority": pick_latency_priority,
    "cost-priority": pick_cost_priority,
    "quality-priority": pick_quality_priority,
}


# --- Verdict markdown ----------------------------------------------------- #


def render_verdict(
    *,
    sweep_path: Path,
    rows_total: int,
    rows_after_hallu_drop: int,
    frontier: list[SweepRow],
    sla_preset: str,
    pick: SweepRow | None,
    knob_keys: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"# RAGO Pareto Verdict — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"- **Sweep input**: `{sweep_path}`")
    lines.append(f"- **Configs total**: {rows_total}")
    lines.append(f"- **After HALLU drop**: {rows_after_hallu_drop}")
    lines.append(f"- **Frontier size**: {len(frontier)}")
    lines.append(f"- **SLA preset**: `{sla_preset}`")
    lines.append("")
    if pick is None:
        lines.append("## ⚠️ NO PICK — frontier empty")
        lines.append("")
        lines.append(
            "Either every config breached HALLU sacred, or no Pareto-optimal "
            "config met the SLA preset constraints. Re-run sweep with adjusted ranges."
        )
        return "\n".join(lines)

    lines.append("## Recommended config")
    lines.append("")
    lines.append(f"- **config_id**: {pick.config_id}")
    lines.append(f"- **n_turns**: {pick.n_turns}")
    lines.append(f"- **pass_rate**: {pick.pass_rate:.2%}")
    lines.append(f"- **p95_ms**: {pick.p95_ms:.0f}")
    lines.append(f"- **cost_per_turn**: ${pick.cost_per_turn:.6f}")
    lines.append(f"- **hallu_count**: {pick.hallu_count}")
    lines.append(f"- **error_count**: {pick.error_count}")
    lines.append("")
    lines.append("### Knob values")
    lines.append("")
    lines.append("| Knob | Value |")
    lines.append("|---|---|")
    for k in knob_keys:
        lines.append(f"| `{k}` | `{pick.knob_values.get(k)}` |")
    lines.append("")

    lines.append("### Apply commands")
    lines.append("")
    lines.append("```bash")
    lines.append("# Run inside Ragbot venv (Python ≥3.12)")
    lines.append("python -c '")
    lines.append("import asyncio, json")
    lines.append("from ragbot.application.services.system_config_service import SystemConfigService")
    lines.append("from ragbot.bootstrap import build_container")
    lines.append("async def go():")
    lines.append("    svc: SystemConfigService = build_container().system_config_service()")
    for k in knob_keys:
        v = pick.knob_values.get(k)
        if v is None:
            continue
        lines.append(f"    await svc.set({k!r}, {_value_repr(v)}, description=\"rago Pareto pick\")")
    lines.append("asyncio.run(go())'")
    lines.append("```")
    lines.append("")

    if frontier:
        lines.append("### Top-3 frontier (sorted by p95_ms)")
        lines.append("")
        sorted_front = sorted(frontier, key=lambda r: r.p95_ms)[:3]
        lines.append("| config_id | pass_rate | p95_ms | cost/turn | hallu |")
        lines.append("|---|---|---|---|---|")
        for r in sorted_front:
            lines.append(
                f"| {r.config_id} | {r.pass_rate:.2%} | {r.p95_ms:.0f} | "
                f"${r.cost_per_turn:.6f} | {r.hallu_count} |"
            )
        lines.append("")

    lines.append("### V14 verify next steps")
    lines.append("")
    lines.append("1. Apply the recommended config (commands above).")
    lines.append("2. Run V14 90Q load test:")
    lines.append("   ```bash")
    lines.append(
        "   bash scripts/loadtest_kick.sh agent_d_loadtest.py "
        "--bot-id <bot> --tenant-id <int> --channel-type web "
        "--questions-file tests/fixtures/agent_d_questions.md"
    )
    lines.append("   ```")
    lines.append("3. Reclassify with Opus:")
    lines.append("   ```bash")
    lines.append(
        "   python scripts/reclassify_loadtest.py --input <V14 raw json> "
        "--output reports/LOADTEST_90Q_V14_RAGO_PARETO_RECLASSIFY.md --label V14"
    )
    lines.append("   ```")
    lines.append("4. Compare against V13 baseline; if HALLU>0 → revert config.")
    return "\n".join(lines)


def _value_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    return repr(value)


# --- CLI ------------------------------------------------------------------ #


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAGO Pareto pick (Paper 26).")
    p.add_argument("--input", required=True, help="sweep CSV path")
    p.add_argument(
        "--sla-preset",
        choices=SLA_PRESETS,
        default="latency-priority",
    )
    p.add_argument("--output", required=True, help="verdict MD path")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    sweep_path = Path(args.input).resolve()
    if not sweep_path.exists():
        print(f"ERROR: input CSV not found: {sweep_path}", file=sys.stderr)
        return 2
    rows, knob_keys = load_sweep_csv(sweep_path)
    if not rows:
        print(f"ERROR: empty CSV: {sweep_path}", file=sys.stderr)
        return 2
    rows_total = len(rows)
    valid = [r for r in rows if r.hallu_count == 0]
    rows_after_hallu_drop = len(valid)
    frontier = compute_frontier(valid)
    pick = _PICKERS[args.sla_preset](frontier)
    md = render_verdict(
        sweep_path=sweep_path,
        rows_total=rows_total,
        rows_after_hallu_drop=rows_after_hallu_drop,
        frontier=frontier,
        sla_preset=args.sla_preset,
        pick=pick,
        knob_keys=knob_keys,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[done] verdict written to {out_path}", flush=True)
    if pick is None:
        return 1
    print(
        f"[pick] config_id={pick.config_id} pass={pick.pass_rate:.2%} "
        f"p95={pick.p95_ms:.0f}ms cost=${pick.cost_per_turn:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
