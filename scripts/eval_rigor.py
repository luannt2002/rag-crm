#!/usr/bin/env python3
"""eval_rigor.py — N-run rigorous eval + A/B significance (Phase B-2).

The measurement spine. Wraps ``eval_rag_endtoend.py``: runs the scenarios N
times (capturing per-query verdicts), aggregates per-(bot,qid) pass-rate +
flip-rate + per-bot mean±std + HALLU breaches, and — in compare mode — runs a
Wilcoxon signed-rank test on paired per-query pass-rates to tell whether an A/B
change is SIGNIFICANT (p<0.05).

This is the gate that turns "we think it helps" into "measured, significant" —
the closed measurement loop whose absence left every dormant feature unproven.
Single run-pass ≠ evidence; flip-rate + significance are required (rule #0).

Usage:
  # collect N runs of a config into an agg.json
  python scripts/eval_rigor.py --n 3 --out reports/rigor/baseline
  # after a change, collect again then compare for significance
  python scripts/eval_rigor.py --n 3 --out reports/rigor/after
  python scripts/eval_rigor.py --compare reports/rigor/baseline/agg.json \
                                         reports/rigor/after/agg.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

_PASS_TRAP = "PASS_REFUSE"
_HALLU = "HALLU_BREACH"


def _run_once(scenarios: str, out_jsonl: Path) -> None:
    subprocess.run(
        [sys.executable, "scripts/eval_rag_endtoend.py",
         "--scenarios", scenarios, "--raw-jsonl", str(out_jsonl)],
        check=True, capture_output=True,
    )


def _load(jsonl: Path) -> dict:
    rows: dict = {}
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[(r["bot_id"], r["qid"])] = r
    return rows


def run_mode(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    for i in range(args.n):
        jf = out / f"run{i + 1}.jsonl"
        print(f"[run {i + 1}/{args.n}] ...", flush=True)
        _run_once(args.scenarios, jf)
        runs.append(_load(jf))

    keys = sorted(set().union(*(r.keys() for r in runs)))
    per_query: dict = {}
    for k in keys:
        samples = [r.get(k) for r in runs if r.get(k)]
        if not samples:
            continue
        s0 = samples[0]
        if s0.get("is_trap"):
            passes = [1 if s.get("verdict") == _PASS_TRAP else 0 for s in samples]
            kind = "trap"
        elif s0.get("expect"):
            passes = [1 if s.get("answer_hit") else 0 for s in samples]
            kind = "answerable"
        else:
            continue  # null-expect non-trap (greeting/booking): not scored
        hallu = sum(1 for s in samples if s.get("verdict") == _HALLU)
        pr = sum(passes) / len(passes)
        per_query[f"{k[0]}|{k[1]}"] = {
            "bot": k[0], "qid": k[1], "kind": kind,
            "pass_rate": pr, "flip": 0 < sum(passes) < len(passes),
            "hallu": hallu,
        }

    per_bot: dict = {}
    for v in per_query.values():
        b = per_bot.setdefault(
            v["bot"], {"cov": [], "flip": 0, "hallu": 0})
        if v["kind"] == "answerable":
            b["cov"].append(v["pass_rate"])
        if v["flip"]:
            b["flip"] += 1
        b["hallu"] += v["hallu"]
    summary: dict = {}
    for bot, b in per_bot.items():
        summary[bot] = {
            "coverage_mean": round(statistics.mean(b["cov"]), 3) if b["cov"] else None,
            "coverage_std": round(statistics.pstdev(b["cov"]), 3) if len(b["cov"]) > 1 else 0.0,
            "flip_queries": b["flip"],
            "hallu_breaches": b["hallu"],
        }
    report = {"n_runs": args.n, "per_bot": summary, "per_query": per_query}
    (out / "agg.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(s["hallu_breaches"] > 0 for s in summary.values()):
        print("\n*** HALLU BREACH across runs — SACRED FAIL ***")
        return 2
    return 0


def compare_mode(args: argparse.Namespace) -> int:
    from scipy.stats import wilcoxon
    a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))["per_query"]
    b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))["per_query"]
    keys = sorted(set(a) & set(b))
    da = [a[k]["pass_rate"] for k in keys]
    db = [b[k]["pass_rate"] for k in keys]
    changed = sum(1 for x, y in zip(da, db) if x != y)
    print(f"queries compared: {len(keys)}  changed: {changed}")
    print(f"mean pass-rate: A={statistics.mean(da):.3f}  B={statistics.mean(db):.3f}  "
          f"Δ={statistics.mean(db) - statistics.mean(da):+.3f}")
    ha = sum(a[k]["hallu"] for k in keys)
    hb = sum(b[k]["hallu"] for k in keys)
    print(f"HALLU breaches: A={ha}  B={hb}  (sacred: both must be 0)")
    if changed == 0:
        print("no per-query change → not significant (identical)")
        return 0
    try:
        _, p = wilcoxon(da, db)
        verdict = "SIGNIFICANT (p<0.05)" if p < 0.05 else "NOT significant"
        print(f"Wilcoxon signed-rank: p={p:.4f}  → {verdict}")
    except ValueError as exc:
        print(f"Wilcoxon: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_rigor")
    p.add_argument("--scenarios", default="tests/scenarios/*_scenario.json")
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--out", default="reports/rigor/run")
    p.add_argument("--compare", nargs=2, metavar=("AGG_A", "AGG_B"))
    a = p.parse_args(argv)
    return compare_mode(a) if a.compare else run_mode(a)


if __name__ == "__main__":
    sys.exit(main())
