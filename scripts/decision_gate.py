#!/usr/bin/env python3
"""Decision Gate — auto-classify KEEP / TUNE / DROP per feature.

Stream 20 (master-of-master plan) companion to ``loadtest_master_ablation.py``.

Reads the ``aggregate.json`` written by the master loadtest run, re-classifies
each ablated feature against the (admin-overridable) thresholds, and prints
the decision matrix as both stdout JSON and a regenerated markdown table.

Decoupling rationale:
    - The loadtest writes the *evidence* (per-config aggregate).
    - This script enforces the *gate* — so admin can re-run the gate with
      different thresholds (e.g. stricter HALLU policy) WITHOUT re-running
      the (expensive) 360-turn loadtest.
    - Output is deterministic from the JSON ⇒ CI can run this in seconds.

CLAUDE.md compliance:
    - Zero-hardcode: thresholds resolved via env override
      (RAGBOT_GATE_KEEP_PASS_LIFT_PP, RAGBOT_GATE_KEEP_LATENCY_DROP_PCT,
      RAGBOT_GATE_HALLU_SACRED_MAX) → fallback to constants imported from
      ``loadtest_master_ablation`` (single source of truth).
    - Domain-neutral: operates on feature names only; no brand literal.
    - HALLU=0 sacred — gate is non-overridable upward
      (env can only LOWER the cap; can't relax sacred=0 to >0).

Usage:
    python scripts/decision_gate.py \\
        --aggregate reports/MASTER_LOADTEST_260514/aggregate.json
    python scripts/decision_gate.py \\
        --aggregate reports/MASTER_LOADTEST_260514/aggregate.json \\
        --output reports/MASTER_LOADTEST_260514/decisions.md
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))


def _load_master_module() -> ModuleType:
    """Import the master ablation script by path (scripts/ isn't a package).

    Both scripts must agree on the FeatureSpec / decision-gate semantics —
    we re-use the master module's pure functions so the gate logic is the
    single source of truth.
    """
    path = _REPO_ROOT / "scripts" / "loadtest_master_ablation.py"
    spec = importlib.util.spec_from_file_location(
        "_master_ablation", path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load master ablation module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def gate(aggregate_blob: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-run the decision logic against a serialized aggregate."""
    master = _load_master_module()

    # Resolve overrides — env may LOWER (stricter) thresholds; HALLU sacred
    # cap is allowed only to be 0 (cannot relax >0).
    keep_pass_lift = _env_float(
        "RAGBOT_GATE_KEEP_PASS_LIFT_PP", master.KEEP_PASS_LIFT_PP,
    )
    keep_latency_drop = _env_float(
        "RAGBOT_GATE_KEEP_LATENCY_DROP_PCT", master.KEEP_LATENCY_DROP_PCT,
    )
    cost_regression_max = _env_float(
        "RAGBOT_GATE_COST_REGRESSION_PCT_MAX",
        master.COST_REGRESSION_PCT_MAX,
    )
    hallu_max = _env_int(
        "RAGBOT_GATE_HALLU_SACRED_MAX", master.HALLU_SACRED_MAX,
    )
    if hallu_max > master.HALLU_SACRED_MAX:
        # Block relaxing the sacred gate via env.
        hallu_max = master.HALLU_SACRED_MAX

    # Patch the master module thresholds in-process so its
    # classify_feature_decision uses the override values.
    master.KEEP_PASS_LIFT_PP = keep_pass_lift
    master.KEEP_LATENCY_DROP_PCT = keep_latency_drop
    master.COST_REGRESSION_PCT_MAX = cost_regression_max
    master.HALLU_SACRED_MAX = hallu_max

    feature_specs = []
    by_name = {f.name: f for f in master.build_default_feature_matrix()}
    for f in aggregate_blob.get("feature_set", []):
        spec = by_name.get(f["name"])
        if spec is None:
            # Unknown feature in aggregate (forward-compat): synthesize.
            spec = master.FeatureSpec(
                name=f["name"],
                tier=f.get("tier", "?"),
                flag_constant=f.get("flag_constant", ""),
                env_var=f.get("env_var", ""),
                description="(loaded from aggregate)",
            )
        feature_specs.append(spec)

    all_on = aggregate_blob["matrix"].get("all_on", {})
    ablation = aggregate_blob.get("ablation", {})

    decisions = master.build_decision_matrix(feature_specs, all_on, ablation)
    return [asdict(d) for d in decisions]


def render_markdown(decisions: list[dict[str, Any]]) -> str:
    keep = [d for d in decisions if d["verdict"] == "KEEP"]
    tune = [d for d in decisions if d["verdict"] == "TUNE"]
    drop = [d for d in decisions if d["verdict"] == "DROP"]

    lines = [
        "# Decision Gate Output",
        "",
        (
            "| Feature | Tier | Verdict | PASS lift pp | p95 drop % | "
            "Cost Δ % | HALLU with | HALLU without | Reason |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in decisions:
        lines.append(
            f"| {d['feature']} | {d['tier']} | **{d['verdict']}** | "
            f"{d['pass_lift_pp']:+.2f} | {d['latency_drop_pct']:+.1f} | "
            f"{d['cost_delta_pct']:+.1f} | "
            f"{d['hallu_with']} | {d['hallu_without']} | {d['reason']} |"
        )
    lines.extend([
        "",
        "## Summary",
        "",
        f"- KEEP ({len(keep)}): {', '.join(d['feature'] for d in keep) or '—'}",
        f"- TUNE ({len(tune)}): {', '.join(d['feature'] for d in tune) or '—'}",
        f"- DROP ({len(drop)}): {', '.join(d['feature'] for d in drop) or '—'}",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aggregate", required=True, help="aggregate.json path")
    ap.add_argument(
        "--output", default=None,
        help="optional markdown path; if absent, only stdout JSON",
    )
    args = ap.parse_args(argv)

    blob = json.loads(Path(args.aggregate).read_text(encoding="utf-8"))
    decisions = gate(blob)

    summary = {
        "decisions": {d["feature"]: d["verdict"] for d in decisions},
        "keep": [d["feature"] for d in decisions if d["verdict"] == "KEEP"],
        "tune": [d["feature"] for d in decisions if d["verdict"] == "TUNE"],
        "drop": [d["feature"] for d in decisions if d["verdict"] == "DROP"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output:
        Path(args.output).write_text(render_markdown(decisions), encoding="utf-8")

    # Exit code: non-zero on HALLU sacred breach or any DROP for ops scripting.
    if summary["drop"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
