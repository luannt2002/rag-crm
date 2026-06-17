"""Smartness 300Q analyzer — Phase D Stream D5.

Consumes the JSON aggregate produced by ``loadtest_smartness_300q.py`` and
emits a markdown report with:

- Per-bot PASS-rate, HALLU-breach count, REFUSE-gap count, p50/p95 latency.
- Per-pattern breakdown (single_entity / multi_entity / typo /
  abbreviation / semantic / cross_reference / trap_hallu).
- Final verdict line: PASS / FAIL against acceptance gate
  (HALLU=0 sacred, PASS≥85% per bot, p95≤15s).

Optional ``--llm-reclassify`` flag emits stub for follow-up Opus judge
(reused from ``reclassify_loadtest.py`` — kept OUT of this script to
avoid spawning LLM calls in analyzer; analyzer is pure offline).

Admin executes after the runner; team coder does NO live execution.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Acceptance gate constants — Phase D §D5 (HANDOFF.md lines 263-269).
# Defined inline (test tooling, not production), explicit per CLAUDE.md
# zero-hardcode rule for response/behaviour-affecting magic numbers.
ACCEPTANCE_PASS_RATE_PCT: float = 85.0
ACCEPTANCE_P95_LATENCY_MS: float = 15_000.0
ACCEPTANCE_HALLU_BREACH_MAX: int = 0  # sacred
ACCEPTANCE_REFUSE_GAP_PCT_MAX: float = 5.0
ACCEPTANCE_HALLU_MISINTERPRET_MAX: int = 2

PASS_VERDICTS = {"PASS_ANSWERED", "PASS_REFUSED"}


def reclassify(rec: dict[str, Any]) -> str:
    """Re-derive verdict from the raw record (idempotent re-check).

    Returns one of the 5 buckets the runner produces. Useful when the
    refuse heuristic evolves between runner-time and analyzer-time:
    re-running the analyzer with an updated heuristic gives the new
    verdict without re-querying the live bot.
    """
    answer = rec.get("answer") or ""
    if not answer or rec.get("error"):
        return "ERR"
    is_trap = bool(rec.get("hallu_trap"))
    expected = rec.get("expected_verdict", "ANSWERED")
    # Import here so test code can monkeypatch is_refuse before this runs.
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent),
    )
    from scripts._loadtest_common import is_refuse  # noqa: PLC0415

    refused = is_refuse(answer)
    if is_trap:
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    if expected == "REFUSED":
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    return "REFUSE_GAP" if refused else "PASS_ANSWERED"


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(round((pct / 100.0) * (len(s) - 1)))
    return s[idx]


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce a structured analysis from the raw records list.

    Returns dict with keys: ``per_bot``, ``per_pattern``, ``totals``,
    ``acceptance`` (verdict against the D5 gate).
    """
    per_bot: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "pass": 0,
            "hallu_breach": 0,
            "refuse_gap": 0,
            "err": 0,
            "latencies": [],
        }
    )
    per_pattern: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "pass": 0, "hallu_breach": 0, "refuse_gap": 0}
    )

    for rec in records:
        v = reclassify(rec)
        bot = rec.get("bot_id", "?")
        pat = rec.get("pattern", "?")
        per_bot[bot]["total"] += 1
        per_pattern[pat]["total"] += 1
        if v in PASS_VERDICTS:
            per_bot[bot]["pass"] += 1
            per_pattern[pat]["pass"] += 1
        if v == "HALLU_BREACH":
            per_bot[bot]["hallu_breach"] += 1
            per_pattern[pat]["hallu_breach"] += 1
        if v == "REFUSE_GAP":
            per_bot[bot]["refuse_gap"] += 1
            per_pattern[pat]["refuse_gap"] += 1
        if v == "ERR":
            per_bot[bot]["err"] += 1
        lat = rec.get("latency_ms")
        if isinstance(lat, (int, float)):
            per_bot[bot]["latencies"].append(float(lat))

    # Finalize per-bot
    for b, blob in per_bot.items():
        lats = blob.pop("latencies")
        blob["pass_rate_pct"] = (
            round(blob["pass"] / blob["total"] * 100, 1)
            if blob["total"] else 0.0
        )
        blob["refuse_gap_pct"] = (
            round(blob["refuse_gap"] / blob["total"] * 100, 1)
            if blob["total"] else 0.0
        )
        blob["p50_latency_ms"] = percentile(lats, 50)
        blob["p95_latency_ms"] = percentile(lats, 95)

    # Finalize per-pattern
    for p, blob in per_pattern.items():
        blob["pass_rate_pct"] = (
            round(blob["pass"] / blob["total"] * 100, 1)
            if blob["total"] else 0.0
        )

    total = len(records)
    total_pass = sum(b["pass"] for b in per_bot.values())
    total_hallu_breach = sum(b["hallu_breach"] for b in per_bot.values())
    total_refuse_gap = sum(b["refuse_gap"] for b in per_bot.values())
    total_err = sum(b["err"] for b in per_bot.values())

    # Acceptance gate — per-bot AND global.
    bot_pass_ok = all(
        b["pass_rate_pct"] >= ACCEPTANCE_PASS_RATE_PCT
        for b in per_bot.values()
    )
    bot_p95_ok = all(
        b["p95_latency_ms"] <= ACCEPTANCE_P95_LATENCY_MS
        for b in per_bot.values()
    )
    hallu_ok = total_hallu_breach <= ACCEPTANCE_HALLU_BREACH_MAX
    refuse_gap_ok = (
        (total_refuse_gap / total * 100) <= ACCEPTANCE_REFUSE_GAP_PCT_MAX
        if total else True
    )

    overall_pass = bot_pass_ok and bot_p95_ok and hallu_ok and refuse_gap_ok

    return {
        "per_bot": dict(per_bot),
        "per_pattern": dict(per_pattern),
        "totals": {
            "total": total,
            "pass": total_pass,
            "hallu_breach": total_hallu_breach,
            "refuse_gap": total_refuse_gap,
            "err": total_err,
            "pass_rate_pct": (
                round(total_pass / total * 100, 1) if total else 0.0
            ),
        },
        "acceptance": {
            "hallu_zero_sacred": hallu_ok,
            "per_bot_pass_rate_ok": bot_pass_ok,
            "per_bot_p95_latency_ok": bot_p95_ok,
            "refuse_gap_ok": refuse_gap_ok,
            "overall": overall_pass,
        },
    }


def render_markdown(analysis: dict[str, Any], *, label: str) -> str:
    """Render the analysis dict as a markdown report."""
    lines: list[str] = []
    lines.append(f"# Smartness 300Q Analysis — {label}")
    lines.append("")
    totals = analysis["totals"]
    acc = analysis["acceptance"]
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- total: {totals['total']}")
    lines.append(
        f"- pass: {totals['pass']} ({totals['pass_rate_pct']}%)"
    )
    lines.append(f"- hallu_breach: {totals['hallu_breach']} (sacred=0)")
    lines.append(f"- refuse_gap: {totals['refuse_gap']}")
    lines.append(f"- err: {totals['err']}")
    lines.append("")
    lines.append("## Acceptance gate")
    lines.append("")
    lines.append(
        f"- HALLU = 0 sacred: "
        f"{'PASS' if acc['hallu_zero_sacred'] else 'FAIL'}"
    )
    lines.append(
        f"- Per-bot PASS >= {ACCEPTANCE_PASS_RATE_PCT}%: "
        f"{'PASS' if acc['per_bot_pass_rate_ok'] else 'FAIL'}"
    )
    lines.append(
        f"- Per-bot p95 <= {ACCEPTANCE_P95_LATENCY_MS} ms: "
        f"{'PASS' if acc['per_bot_p95_latency_ok'] else 'FAIL'}"
    )
    lines.append(
        f"- Refuse-gap <= {ACCEPTANCE_REFUSE_GAP_PCT_MAX}%: "
        f"{'PASS' if acc['refuse_gap_ok'] else 'FAIL'}"
    )
    lines.append("")
    lines.append(
        f"**Overall verdict: {'PASS' if acc['overall'] else 'FAIL'}**"
    )
    lines.append("")
    lines.append("## Per-bot breakdown")
    lines.append("")
    lines.append(
        "| bot | total | pass | pass% | hallu_breach | refuse_gap | "
        "p50 ms | p95 ms |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for b, blob in sorted(analysis["per_bot"].items()):
        lines.append(
            f"| {b} | {blob['total']} | {blob['pass']} | "
            f"{blob['pass_rate_pct']}% | {blob['hallu_breach']} | "
            f"{blob['refuse_gap']} | {blob['p50_latency_ms']:.0f} | "
            f"{blob['p95_latency_ms']:.0f} |"
        )
    lines.append("")
    lines.append("## Per-pattern breakdown")
    lines.append("")
    lines.append(
        "| pattern | total | pass | pass% | hallu_breach | refuse_gap |"
    )
    lines.append("|---|---|---|---|---|---|")
    for p, blob in sorted(analysis["per_pattern"].items()):
        lines.append(
            f"| {p} | {blob['total']} | {blob['pass']} | "
            f"{blob['pass_rate_pct']}% | {blob['hallu_breach']} | "
            f"{blob['refuse_gap']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Runner JSON output path.")
    ap.add_argument("--output", default=None, help="Markdown output path.")
    ap.add_argument(
        "--label", default="300Q",
        help="Label used in the report heading.",
    )
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text())
    records = raw.get("results", raw if isinstance(raw, list) else [])
    analysis = analyze(records)
    md = render_markdown(analysis, label=args.label)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"Wrote analysis to {out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
