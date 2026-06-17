#!/usr/bin/env python3
"""Multi-hop retrieval eval — Paper 14 CARE pattern.

Read a load-test JSON aggregate, isolate multi-hop / aggregation / synthesis
turns, and re-judge each with Opus deep-dive.

Usage:
    python scripts/eval_multi_hop.py \\
        --input reports/LOADTEST_90Q_FULLMINI_1778018956.json \\
        --output reports/MULTI_HOP_EVAL.md \\
        [--max-cost-usd 5] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_COST_USD = 5.0
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"in": 15.0, "out": 75.0},
    "claude-opus-4-6":   {"in": 15.0, "out": 75.0},
}

SYNTHESIS_INTENTS = frozenset({"multi_hop", "aggregation", "comparison"})

JUDGE_PROMPT = """You evaluate a RAG bot's answer for a MULTI-HOP question.
Reply with ONE verdict token + ≤30-word reason on the next line.

Verdict tokens:
- PASS      : answer is correct, complete, grounded in chunks
- PARTIAL   : answer is partly correct but misses a hop or detail
- FAIL      : answer is wrong or misses critical information
- HALLU     : answer contains fabricated facts not in chunks
- NO_ANSWER : bot refused or returned empty

Output format:
TOKEN
≤30-word reason
"""

VERDICTS = {"PASS", "PARTIAL", "FAIL", "HALLU", "NO_ANSWER"}


def estimate_cost(usage: dict[str, Any], model: str) -> float:
    p = PRICING.get(model, PRICING[DEFAULT_MODEL])
    inp = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cw  = usage.get("cache_creation_input_tokens", 0)
    return (inp * p["in"] + out * p["out"] + cw * p["in"]) / 1_000_000


def load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def filter_synthesis_turns(results: list[dict]) -> list[dict]:
    return [r for r in results if r.get("intent", "") in SYNTHESIS_INTENTS]


def build_judge_payload(turn: dict[str, Any]) -> str:
    chunks = turn.get("chunks_used", 0)
    top_score = turn.get("top_score", 0)
    answer = turn.get("answer", "(no answer)")
    question = turn.get("question", "")
    intent = turn.get("intent", "unknown")
    answer_type = turn.get("answer_type", "unknown")

    return (
        f"Question ({intent}): {question}\n"
        f"Answer ({answer_type}): {answer}\n"
        f"Chunks retrieved: {chunks} | Top score: {top_score:.4f}"
    )


def parse_verdict(text: str) -> tuple[str, str]:
    lines = text.strip().split("\n", 1)
    token = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else ""
    if token not in VERDICTS:
        for v in VERDICTS:
            if v in token:
                token = v
                break
        else:
            token = "PARTIAL"
    return token, reason


async def judge_one(
    litellm_module: Any,
    turn: dict[str, Any],
    model: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    payload = build_judge_payload(turn)
    async with sem:
        try:
            resp = await litellm_module.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": JUDGE_PROMPT},
                    {"role": "user", "content": payload},
                ],
                max_tokens=80,
                temperature=0.0,
            )
        except Exception as exc:
            return {
                "qid": turn["qid"],
                "verdict": "ERROR",
                "reason": str(exc)[:80],
                "cost": 0.0,
            }
    text = resp.choices[0].message.content or ""
    verdict, reason = parse_verdict(text)
    usage = getattr(resp, "usage", {}) or {}
    cost = estimate_cost(
        {
            "input_tokens": getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        },
        model,
    )
    return {
        "qid": turn["qid"],
        "room": turn.get("room", ""),
        "question": turn.get("question", "")[:120],
        "intent": turn.get("intent", ""),
        "verdict": verdict,
        "reason": reason,
        "cost": cost,
    }


def render_report(
    label: str,
    judged: list[dict[str, Any]],
    total_cost: float,
    elapsed_s: float,
) -> str:
    counts = Counter(j["verdict"] for j in judged)
    total = len(judged)
    pass_rate = (counts["PASS"] / total * 100) if total else 0

    lines = [
        f"# Multi-Hop Eval — {label}",
        f"",
        f"> Model: {DEFAULT_MODEL} | Turns: {total} | Cost: ${total_cost:.3f} | Time: {elapsed_s:.0f}s",
        f"",
        f"## Summary",
        f"",
        f"| Verdict | Count | % |",
        f"|---|---|---|",
    ]
    for v in ["PASS", "PARTIAL", "FAIL", "HALLU", "NO_ANSWER", "ERROR"]:
        c = counts.get(v, 0)
        pct = c / total * 100 if total else 0
        lines.append(f"| {v} | {c} | {pct:.1f}% |")

    lines += [
        f"",
        f"**PASS rate**: {pass_rate:.1f}%",
        f"",
        f"## Details",
        f"",
    ]
    for j in judged:
        lines.append(
            f"- **{j['verdict']}** | Q{j['qid']} r{j.get('room','?')} "
            f"({j.get('intent','')}): _{j.get('question','')[:80]}_ → {j.get('reason','')}"
        )

    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-hop eval — Paper 14 CARE")
    parser.add_argument("--input", required=True, help="Path to loadtest JSON")
    parser.add_argument("--output", default="reports/MULTI_HOP_EVAL.md")
    parser.add_argument("--label", default="V14", help="Label for report header")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input not found: {input_path}")
        sys.exit(1)

    data = load_json(input_path)
    results = data.get("results", data.get("turns", []))
    synthesis = filter_synthesis_turns(results)

    if not synthesis:
        print("No synthesis turns found. Checking all turns...")
        print(f"Total turns: {len(results)}")
        intents = Counter(r.get("intent", "?") for r in results)
        for intent, count in intents.most_common():
            print(f"  {intent}: {count}")
        sys.exit(1)

    print(f"Found {len(synthesis)} synthesis turns out of {len(results)} total")

    if args.dry_run:
        for t in synthesis[:5]:
            print(f"  Q{t['qid']}: {t['question'][:80]} (intent={t['intent']})")
        print("Dry run — no API calls.")
        return

    try:
        import litellm
    except ImportError:
        print("litellm not installed. Run: pip install litellm")
        sys.exit(1)

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.monotonic()
    total_cost = 0.0
    judged: list[dict[str, Any]] = []

    tasks = [judge_one(litellm, t, args.model, sem) for t in synthesis]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        judged.append(result)
        total_cost += result["cost"]
        if total_cost > args.max_cost_usd:
            print(f"Cost limit ${args.max_cost_usd} exceeded. Stopping.")
            break

    elapsed = time.monotonic() - t0
    judged.sort(key=lambda j: j["qid"])

    report = render_report(args.label, judged, total_cost, elapsed)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Report written: {out_path}")
    print(f"Total cost: ${total_cost:.3f} | Time: {elapsed:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
