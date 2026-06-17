#!/usr/bin/env python3
"""Feature-ablation report from master load-test results.

Sprint 0 / MoM 00c-analytics — turns the 4-config ablation matrix from
``plans/260514-master-of-master/OBSERVABILITY-MATRIX.md`` §3 into a
markdown table that calls KEEP / TUNE / DROP per feature, using the
thresholds defined as constants (zero hardcode).

Input shape (CSV or JSON) — one row per ``loadtest_run``:

    config_name       e.g. "A_baseline" / "B_t1_chunking_quality" / ...
    pass_rate         float in [0,1]
    hallu_rate        float in [0,1] (HALLU=0 sacred — non-zero → DROP)
    p95_ms            integer milliseconds
    cost_per_turn     float USD
    feature_flags     map<flag_name, bool>  (snapshot of all flags ON/OFF)

The script computes lift WITH-vs-WITHOUT each feature by:
  1. picking the config where the feature is ON (master / all-on)
  2. picking the closest config where the feature is OFF (single-drop or
     baseline) — chosen as the config that differs from the master config
     ONLY by this one flag, when available; otherwise baseline.
  3. delta_pass = pass_with - pass_without (percentage points)
     delta_p95_pct  = (p95_with - p95_without) / p95_without * 100
     delta_cost_pct = (cost_with - cost_without) / cost_without * 100
  4. Verdict per the Decision Gate (OBSERVABILITY-MATRIX §3):
       DROP_HALLU_BREACH      if delta_hallu > 0 (sacred)
       KEEP_QUALITY_LIFT      if delta_pass >= DEFAULT_ABLATION_KEEP_PASS_LIFT_PP
       KEEP_LATENCY_WIN       if -delta_p95_pct >= DEFAULT_ABLATION_KEEP_LATENCY_DROP_PCT
       KEEP_COST_WIN          if -delta_cost_pct >= DEFAULT_ABLATION_KEEP_COST_DROP_PCT
       TUNE_MARGINAL          if 0 < delta_pass < DEFAULT_ABLATION_KEEP_PASS_LIFT_PP
       DROP_NO_VALUE          otherwise

Pattern: per-feature ablation reporting for adaptive RAG pipelines.
Reference: internal observability tooling per
``plans/260514-master-of-master/OBSERVABILITY-MATRIX.md`` §3-§6.

Sacred: read-only on input file. No DB writes. No LLM calls. Never
touches request answers — pure offline analysis. Domain-neutral.

Usage:
    python scripts/feature_ablation_report.py --input results.json
    python scripts/feature_ablation_report.py --input results.csv --out report.md

Exit codes:
    0  success
    2  invalid input shape
    3  invocation error
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_ABLATION_KEEP_COST_DROP_PCT,
    DEFAULT_ABLATION_KEEP_LATENCY_DROP_PCT,
    DEFAULT_ABLATION_KEEP_PASS_LIFT_PP,
)

# Config-name conventions documented in OBSERVABILITY-MATRIX §3. Treated
# as data, not behaviour — script does not enforce these names, it only
# picks the master config heuristically when caller omits ``--master``.
MASTER_CONFIG_HINTS: tuple[str, ...] = ("D_master_all_on", "all_on", "master")
BASELINE_CONFIG_HINTS: tuple[str, ...] = ("A_baseline", "baseline")


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------
def load_runs(path: Path) -> list[dict[str, Any]]:
    """Load the load-test rows from JSON or CSV.

    JSON shape: either a list[dict] or {"runs": list[dict]}.
    CSV shape: header row + columns
        config_name,pass_rate,hallu_rate,p95_ms,cost_per_turn,feature_flags
    where ``feature_flags`` is a JSON-encoded object.
    """
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        if isinstance(data, dict) and "runs" in data:
            rows = data["runs"]
        else:
            rows = data
        if not isinstance(rows, list):
            sys.stderr.write("JSON input must be a list or {'runs': [...]}\n")
            sys.exit(2)
    elif path.suffix.lower() == ".csv":
        rows = []
        reader = csv.DictReader(raw.splitlines())
        for r in reader:
            flags_field = r.get("feature_flags") or "{}"
            try:
                flags = json.loads(flags_field)
            except json.JSONDecodeError:
                sys.stderr.write(
                    f"row {r.get('config_name')!r}: feature_flags must be JSON\n"
                )
                sys.exit(2)
            rows.append(
                {
                    "config_name": r["config_name"],
                    "pass_rate": float(r["pass_rate"]),
                    "hallu_rate": float(r.get("hallu_rate") or 0),
                    "p95_ms": int(float(r["p95_ms"])),
                    "cost_per_turn": float(r["cost_per_turn"]),
                    "feature_flags": flags,
                },
            )
    else:
        sys.stderr.write(f"unknown input extension: {path.suffix}\n")
        sys.exit(2)

    for row in rows:
        for key in ("config_name", "pass_rate", "p95_ms", "cost_per_turn"):
            if key not in row:
                sys.stderr.write(f"input row missing key: {key} (row: {row})\n")
                sys.exit(2)
        if "feature_flags" not in row or not isinstance(
            row["feature_flags"], dict,
        ):
            sys.stderr.write(
                f"input row missing 'feature_flags' map: {row['config_name']}\n",
            )
            sys.exit(2)
        row.setdefault("hallu_rate", 0.0)
    return rows


# ---------------------------------------------------------------------------
# Config selection
# ---------------------------------------------------------------------------
def _find_by_hints(
    runs: list[dict[str, Any]], hints: Iterable[str],
) -> dict[str, Any] | None:
    by_name = {r["config_name"]: r for r in runs}
    for hint in hints:
        if hint in by_name:
            return by_name[hint]
    return None


def pick_master(
    runs: list[dict[str, Any]], explicit: str | None,
) -> dict[str, Any]:
    if explicit:
        for r in runs:
            if r["config_name"] == explicit:
                return r
        sys.stderr.write(f"--master config {explicit!r} not in input\n")
        sys.exit(3)
    found = _find_by_hints(runs, MASTER_CONFIG_HINTS)
    if found is None:
        # Fall back to the config with the most ON flags — it is the
        # closest analogue to "everything enabled".
        found = max(
            runs,
            key=lambda r: sum(1 for v in r["feature_flags"].values() if v),
        )
    return found


def pick_baseline(
    runs: list[dict[str, Any]], explicit: str | None,
) -> dict[str, Any]:
    if explicit:
        for r in runs:
            if r["config_name"] == explicit:
                return r
        sys.stderr.write(f"--baseline config {explicit!r} not in input\n")
        sys.exit(3)
    found = _find_by_hints(runs, BASELINE_CONFIG_HINTS)
    if found is None:
        # Fall back to the config with the fewest ON flags.
        found = min(
            runs,
            key=lambda r: sum(1 for v in r["feature_flags"].values() if v),
        )
    return found


def pick_drop_run(
    runs: list[dict[str, Any]],
    master: dict[str, Any],
    flag: str,
) -> dict[str, Any] | None:
    """Find the run that differs from master ONLY by ``flag`` being OFF.

    Returns None when no such run exists in the input (caller falls back
    to baseline).
    """
    master_flags = master["feature_flags"]
    for r in runs:
        if r is master:
            continue
        rf = r["feature_flags"]
        if rf.get(flag, False):
            continue  # flag still ON — not a drop run for this flag
        # Compute the set of flags that differ. Must be exactly {flag}.
        differing = {
            k
            for k in (master_flags.keys() | rf.keys())
            if master_flags.get(k, False) != rf.get(k, False)
        }
        if differing == {flag}:
            return r
    return None


# ---------------------------------------------------------------------------
# Verdict gate
# ---------------------------------------------------------------------------
def decide(
    *,
    delta_pass_pp: float,
    delta_hallu: float,
    delta_p95_pct: float,
    delta_cost_pct: float,
    pass_lift_threshold_pp: float,
    latency_drop_threshold_pct: float,
    cost_drop_threshold_pct: float,
) -> str:
    """Return one of the canonical verdict strings."""
    if delta_hallu > 0:
        return "DROP_HALLU_BREACH"
    if delta_pass_pp >= pass_lift_threshold_pp:
        return "KEEP_QUALITY_LIFT"
    latency_drop_pct = -delta_p95_pct
    if latency_drop_pct >= latency_drop_threshold_pct:
        return "KEEP_LATENCY_WIN"
    cost_drop_pct = -delta_cost_pct
    if cost_drop_pct >= cost_drop_threshold_pct:
        return "KEEP_COST_WIN"
    if 0 < delta_pass_pp < pass_lift_threshold_pp:
        return "TUNE_MARGINAL"
    return "DROP_NO_VALUE"


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------
def compute_per_feature(
    runs: list[dict[str, Any]],
    master: dict[str, Any],
    baseline: dict[str, Any],
    *,
    pass_lift_threshold_pp: float,
    latency_drop_threshold_pct: float,
    cost_drop_threshold_pct: float,
) -> list[dict[str, Any]]:
    """For every flag ON in master, compute lift vs the best comparison run."""
    rows: list[dict[str, Any]] = []
    for flag, on_in_master in sorted(master["feature_flags"].items()):
        if not on_in_master:
            # Cannot ablate a feature that is OFF in the master config —
            # there is no "with" data point to compare.
            continue
        comparison = pick_drop_run(runs, master, flag) or baseline
        comparison_source = (
            "single_drop" if comparison is not baseline else "baseline"
        )
        if comparison is master:
            # Defensive: a master vs master compare is meaningless.
            continue

        delta_pass_pp = (master["pass_rate"] - comparison["pass_rate"]) * 100
        delta_hallu = master["hallu_rate"] - comparison["hallu_rate"]
        # Guard zero-denominator (a 0ms baseline cannot exist in practice
        # but the script must not crash on bad input).
        if comparison["p95_ms"] > 0:
            delta_p95_pct = (
                (master["p95_ms"] - comparison["p95_ms"])
                / comparison["p95_ms"]
                * 100
            )
        else:
            delta_p95_pct = 0.0
        if comparison["cost_per_turn"] > 0:
            delta_cost_pct = (
                (master["cost_per_turn"] - comparison["cost_per_turn"])
                / comparison["cost_per_turn"]
                * 100
            )
        else:
            delta_cost_pct = 0.0

        verdict = decide(
            delta_pass_pp=delta_pass_pp,
            delta_hallu=delta_hallu,
            delta_p95_pct=delta_p95_pct,
            delta_cost_pct=delta_cost_pct,
            pass_lift_threshold_pp=pass_lift_threshold_pp,
            latency_drop_threshold_pct=latency_drop_threshold_pct,
            cost_drop_threshold_pct=cost_drop_threshold_pct,
        )
        rows.append(
            {
                "feature_flag": flag,
                "comparison_source": comparison_source,
                "comparison_config": comparison["config_name"],
                "delta_pass_pp": delta_pass_pp,
                "delta_hallu": delta_hallu,
                "delta_p95_pct": delta_p95_pct,
                "delta_cost_pct": delta_cost_pct,
                "verdict": verdict,
            },
        )
    return rows


def render_markdown(
    *,
    master: dict[str, Any],
    baseline: dict[str, Any],
    per_feature: list[dict[str, Any]],
    pass_lift_threshold_pp: float,
    latency_drop_threshold_pct: float,
    cost_drop_threshold_pct: float,
) -> str:
    lines: list[str] = []
    lines.append("# Feature Ablation Report")
    lines.append("")
    lines.append(
        f"- Master config: `{master['config_name']}` — "
        f"PASS={master['pass_rate']:.3f}, HALLU={master['hallu_rate']:.3f}, "
        f"p95={master['p95_ms']}ms, cost/turn=${master['cost_per_turn']:.4f}"
    )
    lines.append(
        f"- Baseline config: `{baseline['config_name']}` — "
        f"PASS={baseline['pass_rate']:.3f}, HALLU={baseline['hallu_rate']:.3f}, "
        f"p95={baseline['p95_ms']}ms, cost/turn=${baseline['cost_per_turn']:.4f}"
    )
    lines.append(
        "- Thresholds: "
        f"PASS-lift >= {pass_lift_threshold_pp}pp / "
        f"latency drop >= {latency_drop_threshold_pct}% / "
        f"cost drop >= {cost_drop_threshold_pct}%"
    )
    lines.append("")
    lines.append(
        "| Feature flag | Compared vs | ΔPASS (pp) | Δp95 (%) "
        "| Δcost (%) | Verdict |"
    )
    lines.append("|---|---|---:|---:|---:|---|")
    for r in per_feature:
        lines.append(
            f"| `{r['feature_flag']}` "
            f"| `{r['comparison_config']}` ({r['comparison_source']}) "
            f"| {r['delta_pass_pp']:+.2f} "
            f"| {r['delta_p95_pct']:+.1f} "
            f"| {r['delta_cost_pct']:+.1f} "
            f"| **{r['verdict']}** |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Feature ablation report from load-test results",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to load-test results (.json or .csv)",
    )
    parser.add_argument(
        "--out",
        default=None,
        type=Path,
        help="Write the markdown report to this path (default: stdout)",
    )
    parser.add_argument(
        "--master",
        default=None,
        help=(
            "config_name for the master 'all-on' run "
            "(default: auto-detect)"
        ),
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=(
            "config_name for the baseline 'all-off' run "
            "(default: auto-detect)"
        ),
    )
    parser.add_argument(
        "--pass-lift-pp",
        type=float,
        default=DEFAULT_ABLATION_KEEP_PASS_LIFT_PP,
        help=(
            "KEEP threshold for ΔPASS in percentage points "
            f"(default {DEFAULT_ABLATION_KEEP_PASS_LIFT_PP})"
        ),
    )
    parser.add_argument(
        "--latency-drop-pct",
        type=float,
        default=DEFAULT_ABLATION_KEEP_LATENCY_DROP_PCT,
        help=(
            "KEEP threshold for p95 drop in percent "
            f"(default {DEFAULT_ABLATION_KEEP_LATENCY_DROP_PCT})"
        ),
    )
    parser.add_argument(
        "--cost-drop-pct",
        type=float,
        default=DEFAULT_ABLATION_KEEP_COST_DROP_PCT,
        help=(
            "KEEP threshold for cost drop in percent "
            f"(default {DEFAULT_ABLATION_KEEP_COST_DROP_PCT})"
        ),
    )
    parser.add_argument(
        "--json-out",
        default=None,
        type=Path,
        help="Optional JSON sidecar with the per-feature numeric rows",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        sys.stderr.write(f"input not found: {args.input}\n")
        return 3

    runs = load_runs(args.input)
    if len(runs) < 2:
        sys.stderr.write(
            "need at least two load-test runs (master + baseline) to ablate\n",
        )
        return 2

    master = pick_master(runs, args.master)
    baseline = pick_baseline(runs, args.baseline)
    if master is baseline:
        sys.stderr.write(
            "master and baseline resolved to the same config — "
            "pass --master / --baseline explicitly\n",
        )
        return 3

    per_feature = compute_per_feature(
        runs,
        master,
        baseline,
        pass_lift_threshold_pp=args.pass_lift_pp,
        latency_drop_threshold_pct=args.latency_drop_pct,
        cost_drop_threshold_pct=args.cost_drop_pct,
    )
    md = render_markdown(
        master=master,
        baseline=baseline,
        per_feature=per_feature,
        pass_lift_threshold_pp=args.pass_lift_pp,
        latency_drop_threshold_pct=args.latency_drop_pct,
        cost_drop_threshold_pct=args.cost_drop_pct,
    )

    if args.out:
        args.out.write_text(md, encoding="utf-8")
        sys.stderr.write(f"wrote markdown report → {args.out}\n")
    else:
        sys.stdout.write(md)

    if args.json_out:
        payload = {
            "schema_version": 1,
            "master_config": master["config_name"],
            "baseline_config": baseline["config_name"],
            "thresholds": {
                "pass_lift_pp": args.pass_lift_pp,
                "latency_drop_pct": args.latency_drop_pct,
                "cost_drop_pct": args.cost_drop_pct,
            },
            "per_feature": per_feature,
        }
        args.json_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8",
        )
        sys.stderr.write(f"wrote JSON sidecar → {args.json_out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
