#!/usr/bin/env python3
"""Reclassify a load-test JSON aggregate using Opus.

Stream F (T1-Eval): take the raw aggregate produced by `agent_d_loadtest.py`
and re-judge each turn with a deepdive Opus call to produce the *real*
PASS rate (V12 used this method to lift raw 86.7% → reclassified 98.9%).

Verdicts (single-token reply enforced):
  PASS          — bot answered correctly, grounded
  VALID_REFUSE  — refused for OOS / KB-gap → legitimate
  OVER_REFUSE   — refused but chunks supported answering
  HALLU         — fabricated number / fact (sacred breach)
  PARTIAL       — answered but incomplete or pronoun-vague

PASS_RATE = (PASS + VALID_REFUSE) / total × 100  (V12 methodology).

Usage:
    python scripts/reclassify_loadtest.py \\
        --input  reports/LOADTEST_90Q_FULLMINI_1778018956.json \\
        --output reports/LOADTEST_90Q_V13_RECLASSIFY_20260506.md \\
        --label  V13 \\
        [--max-cost-usd 5] [--dry-run] [--background --status FILE]

Sacred: this script is OFFLINE/eval-only — it does NOT inject anything
into runtime LLM prompts. Output is owner-facing markdown only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_COST_USD = 5.0
PRICING = {  # USD per 1M token; flat across opus 4.x
    "claude-opus-4-7":   {"in": 15.0, "out": 75.0},
    "claude-opus-4-6":   {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0},
}
JUDGE_PROMPT = """You judge a single RAG bot turn against retrieved context. Reply with EXACTLY one verdict token + a brief reason on the next line.

Verdict tokens (pick one):
- PASS          : bot answered correctly, grounded in chunks_returned
- VALID_REFUSE  : bot refused but the question is OOS / KB has no relevant chunks → refusal is legitimate
- OVER_REFUSE   : bot refused but the chunks contained enough info to answer
- HALLU         : bot fabricated a number, fact, or attribute NOT present in chunks
- PARTIAL       : bot answered but missed part / used vague pronoun / cited wrong chunk

Strict rules:
- HALLU is sacred — flag any fabricated number / promotion / commitment.
- VALID_REFUSE only when chunks_returned == 0 OR all chunks irrelevant.
- Output: line 1 = TOKEN, line 2 = ≤25-word reason. Nothing else.
"""

VERDICTS = {"PASS", "VALID_REFUSE", "OVER_REFUSE", "HALLU", "PARTIAL"}


def write_status(path: Path | None, value: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    except OSError as e:
        sys.stderr.write(f"[reclassify] status write failed: {e}\n")


def estimate_cost(usage: dict, model: str) -> float:
    p = PRICING.get(model, PRICING[DEFAULT_MODEL])
    inp = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cw  = usage.get("cache_creation_input_tokens", 0)
    return (inp * p["in"] + out * p["out"] + cw * p["in"]) / 1_000_000


def fmt_chunks(chunks: list[dict] | None) -> str:
    if not chunks:
        return "(empty — no chunks returned)"
    lines = []
    for i, c in enumerate(chunks[:8]):
        score = c.get("top_score") or c.get("score") or "?"
        text = (c.get("content") or c.get("text") or "")[:300]
        lines.append(f"  [#{i} score={score}] {text}")
    if len(chunks) > 8:
        lines.append(f"  ... +{len(chunks) - 8} more")
    return "\n".join(lines)


def build_user_msg(turn: dict) -> str:
    qid = turn.get("qid", "?")
    question = turn.get("question") or turn.get("query") or ""
    response = turn.get("response") or turn.get("answer") or ""
    status = turn.get("status") or turn.get("answer_type") or ""
    chunks = turn.get("chunks") or turn.get("retrieved_chunks") or []
    top_score = turn.get("top_score", "?")
    return (
        f"<turn id=\"{qid}\" status=\"{status}\" top_score=\"{top_score}\">\n"
        f"<question>{question}</question>\n"
        f"<bot_response>{response}</bot_response>\n"
        f"<chunks>\n{fmt_chunks(chunks)}\n</chunks>\n"
        f"</turn>"
    )


def parse_verdict(raw: str) -> tuple[str, str]:
    if not raw:
        return "PARTIAL", "(empty response from judge)"
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return "PARTIAL", "(no content)"
    token = lines[0].upper().strip(":.,").strip()
    if token not in VERDICTS:
        for v in VERDICTS:
            if v in token:
                token = v
                break
        else:
            return "PARTIAL", f"(unparsed: {lines[0][:60]})"
    reason = lines[1] if len(lines) > 1 else ""
    return token, reason[:200]


async def judge_turn(turn: dict, *, model: str, dry_run: bool) -> tuple[str, str, dict]:
    user_msg = build_user_msg(turn)
    if dry_run:
        return "PASS", "(dry-run, no LLM call)", {}
    import litellm as _litellm
    resp = await _litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=80,
        timeout=60,
    )
    raw = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    usage_dict = (
        {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
        }
        if usage else {}
    )
    verdict, reason = parse_verdict(raw)
    return verdict, reason, usage_dict


def render_report(label: str, results: list[dict], totals: dict, agg_input: dict) -> str:
    counts = Counter(r["verdict"] for r in results)
    n = len(results)
    pass_rate = (counts["PASS"] + counts["VALID_REFUSE"]) / n * 100 if n else 0
    hallu_count = counts["HALLU"]

    by_section: dict[str, Counter] = {}
    for r in results:
        sec = r.get("section", "unknown")
        by_section.setdefault(sec, Counter())[r["verdict"]] += 1

    lines = [
        f"# LOADTEST RECLASSIFY — {label}",
        "",
        f"**Source**: `{agg_input.get('source_file', '?')}`",
        f"**Bot**: `{agg_input.get('bot_id', '?')}` · **Combo**: {agg_input.get('combo', '?')}",
        f"**Total turns judged**: {n}  ·  **Cost**: ${totals['cost']:.4f}",
        "",
        "## Headline",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **PASS_RATE (PASS + VALID_REFUSE)** | **{pass_rate:.1f}%** |",
        f"| HALLU sacred breach | {'❌ ' + str(hallu_count) if hallu_count else '✅ 0'} |",
        f"| OVER_REFUSE count | {counts['OVER_REFUSE']} |",
        f"| PARTIAL count | {counts['PARTIAL']} |",
        f"| Raw PASS | {counts['PASS']} |",
        f"| Raw VALID_REFUSE | {counts['VALID_REFUSE']} |",
        "",
        "## Verdict counts",
        "",
        "| Verdict | Count | % |",
        "|---|---|---|",
    ]
    for v in ("PASS", "VALID_REFUSE", "OVER_REFUSE", "HALLU", "PARTIAL"):
        c = counts[v]
        lines.append(f"| {v} | {c} | {c/n*100:.1f}% |")
    lines.append("")
    lines.append("## By section")
    lines.append("")
    lines.append("| Section | n | PASS | VALID_REFUSE | OVER_REFUSE | HALLU | PARTIAL |")
    lines.append("|---|---|---|---|---|---|---|")
    for sec, ct in sorted(by_section.items()):
        lines.append(
            f"| {sec} | {sum(ct.values())} | {ct['PASS']} | {ct['VALID_REFUSE']} | "
            f"{ct['OVER_REFUSE']} | {ct['HALLU']} | {ct['PARTIAL']} |"
        )

    if hallu_count:
        lines.append("")
        lines.append("## ⚠ HALLU breach detail (sacred)")
        lines.append("")
        for r in results:
            if r["verdict"] == "HALLU":
                lines.append(f"- `{r['qid']}` — {r['reason']}")

    if counts["OVER_REFUSE"]:
        lines.append("")
        lines.append("## OVER_REFUSE detail (potential lift)")
        lines.append("")
        for r in results:
            if r["verdict"] == "OVER_REFUSE":
                lines.append(f"- `{r['qid']}` — {r['reason']}")

    lines.append("")
    lines.append("## Per-turn verdicts (full)")
    lines.append("")
    lines.append("| qid | section | verdict | reason |")
    lines.append("|---|---|---|---|")
    for r in results:
        reason = r["reason"].replace("|", "\\|")[:150]
        lines.append(f"| {r['qid']} | {r['section']} | {r['verdict']} | {reason} |")

    return "\n".join(lines) + "\n"


def collect_turns(agg: dict) -> list[dict]:
    """Normalise turns from various agg shapes — agent_d_loadtest writes `results`."""
    if "results" in agg:
        out = []
        for t in agg["results"]:
            section = t.get("section") or t.get("category") or "baseline"
            out.append({**t, "section": section})
        return out
    if "turns" in agg:
        return agg["turns"]
    raise ValueError("Aggregate JSON has neither 'results' nor 'turns' key")


async def amain(args) -> int:
    status_path = Path(args.status) if args.status else None
    write_status(status_path, "running")
    try:
        with open(args.input) as f:
            agg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        write_status(status_path, f"error: {e}")
        sys.stderr.write(f"[reclassify] failed to read {args.input}: {e}\n")
        return 2

    turns = collect_turns(agg)
    if args.limit:
        turns = turns[: args.limit]

    results: list[dict] = []
    totals = {"cost": 0.0, "tok_in": 0, "tok_out": 0}
    started = time.time()
    progress_path = Path(args.progress) if args.progress else None

    for i, turn in enumerate(turns):
        try:
            verdict, reason, usage = await judge_turn(
                turn, model=args.model, dry_run=args.dry_run,
            )
        except Exception as e:  # noqa: BLE001 — top-level eval driver, log + continue
            sys.stderr.write(f"[reclassify] qid={turn.get('qid')} judge failed: {e}\n")
            verdict, reason, usage = "PARTIAL", f"(judge error: {type(e).__name__})", {}
        cost = estimate_cost(usage, args.model)
        totals["cost"] += cost
        totals["tok_in"] += usage.get("input_tokens", 0)
        totals["tok_out"] += usage.get("output_tokens", 0)
        results.append({
            "qid": turn.get("qid", f"#{i}"),
            "section": turn.get("section", "unknown"),
            "verdict": verdict,
            "reason": reason,
        })

        if progress_path:
            try:
                progress_path.write_text(
                    f"{i+1}/{len(turns)} verdict={verdict} cost=${totals['cost']:.4f}\n"
                )
            except OSError:
                pass

        if totals["cost"] > args.max_cost_usd:
            sys.stderr.write(
                f"[reclassify] cost cap ${args.max_cost_usd} hit at turn {i+1}, stopping\n"
            )
            break

        if (i + 1) % 10 == 0:
            elapsed = time.time() - started
            sys.stderr.write(
                f"[reclassify] {i+1}/{len(turns)} done · ${totals['cost']:.4f} · {elapsed:.1f}s\n"
            )

    agg_input = {
        "source_file": str(args.input),
        "bot_id": agg.get("meta", {}).get("bot_id", "?"),
        "combo": agg.get("meta", {}).get("combo", "?"),
    }
    report = render_report(args.label, results, totals, agg_input)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    sys.stderr.write(
        f"[reclassify] DONE → {out_path}  (cost ${totals['cost']:.4f})\n"
    )
    write_status(status_path, f"done cost=${totals['cost']:.4f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--input", required=True, help="JSON aggregate from agent_d_loadtest.py")
    p.add_argument("--output", required=True, help="Output markdown report path")
    p.add_argument("--label", default="loadtest", help="Label shown in report header (e.g. V13)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Judge model (default {DEFAULT_MODEL})")
    p.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    p.add_argument("--limit", type=int, default=0, help="Limit number of turns (0=all)")
    p.add_argument("--dry-run", action="store_true", help="Skip LLM call (smoke test)")
    p.add_argument("--status", help="File to write running/done/error status (Stream Y pattern)")
    p.add_argument("--progress", help="File to write per-turn progress")
    args = p.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
