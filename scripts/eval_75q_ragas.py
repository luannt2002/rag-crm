#!/usr/bin/env python3
"""RAGAS-style eval CLI — scores test_75q_load.py JSON output.

Reads ANY transcript JSON produced by ``scripts/test_75q_load.py`` (or any
file with the same shape) and computes faithfulness / answer-relevance /
context-precision per turn, then aggregates per-room and overall.

Usage:
  python3 scripts/eval_75q_ragas.py \\
    --input "/tmp/<bot>_75q_room*.json" \\
    --output reports/RAGAS_75Q_$(date +%Y%m%d_%H%M).json
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Ensure src/ on path when invoked outside an installed venv.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbot.evaluation.ragas_metrics import (  # noqa: E402
    LLMRagasEvaluator,
    TurnInput,
    TurnScore,
)
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_RAGAS_EMBED_MODEL,
    DEFAULT_RAGAS_JUDGE_MODEL,
    DEFAULT_RAGAS_MAX_CONCURRENCY,
    DEFAULT_RAGAS_REVERSE_QUESTIONS_N,
)

# --- module constants — keep zero magic numbers ----------------------------
SKIP_CLASSIFICATIONS = ("ERROR",)  # transport-level fail; nothing to score
LOW_SCORE_TOPN = 5
ROOMS_KEY_ALL = "ALL"


def _extract_chunk_texts(turn: dict[str, Any]) -> tuple[str, ...]:
    """Pull chunk texts — prefer full ``chunks[].content``, fall back to preview."""
    out: list[str] = []
    for c in turn.get("chunks") or []:
        if not isinstance(c, dict):
            continue
        text = c.get("content") or c.get("text")
        if isinstance(text, str) and text.strip():
            out.append(text)
    if out:
        return tuple(out)
    for src in turn.get("sources") or []:
        if not isinstance(src, dict):
            continue
        preview = src.get("preview") or src.get("content") or src.get("text")
        if isinstance(preview, str) and preview.strip():
            out.append(preview)
    return tuple(out)


def _extract_citation_ids(turn: dict[str, Any]) -> tuple[str, ...]:
    """Citation chunk_ids — kept for downstream analysis only."""
    out: list[str] = []
    for c in turn.get("citations") or []:
        if isinstance(c, dict):
            cid = c.get("chunk_id")
            if isinstance(cid, str):
                out.append(cid)
    return tuple(out)


def _load_turns(input_glob: str) -> list[dict[str, Any]]:
    paths = sorted(glob.glob(input_glob))
    if not paths:
        raise FileNotFoundError(f"No files matched glob: {input_glob}")
    turns: list[dict[str, Any]] = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"  WARN: skip {p}: {exc}", file=sys.stderr)
            continue
        for t in data.get("turns") or []:
            if isinstance(t, dict):
                t["_source_file"] = p
                turns.append(t)
    return turns


def _filter_rooms(turns: list[dict[str, Any]], rooms: list[int] | None) -> list[dict[str, Any]]:
    if not rooms:
        return turns
    room_set = set(rooms)
    return [t for t in turns if int(t.get("room", -1)) in room_set]


async def _score_one(
    evaluator: LLMRagasEvaluator,
    turn: dict[str, Any],
) -> tuple[dict[str, Any], TurnScore | None]:
    classification = (turn.get("classification") or "").upper()
    if classification in SKIP_CLASSIFICATIONS:
        return turn, None
    chunks = _extract_chunk_texts(turn)
    inp = TurnInput(
        question=turn.get("question") or "",
        answer=turn.get("answer") or "",
        retrieved_chunks=chunks,
        citations=_extract_citation_ids(turn),
    )
    score = await evaluator.score_turn(inp)
    return turn, score


def _aggregate(scored: list[tuple[dict[str, Any], TurnScore]]) -> dict[str, Any]:
    """Per-room means + overall + low-score top-N tables."""
    by_room: dict[Any, list[TurnScore]] = {ROOMS_KEY_ALL: []}
    for turn, sc in scored:
        room = turn.get("room", "unknown")
        by_room.setdefault(room, []).append(sc)
        by_room[ROOMS_KEY_ALL].append(sc)

    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    rooms_summary = {}
    for room, scores in by_room.items():
        rooms_summary[str(room)] = {
            "n": len(scores),
            "faithfulness_mean": _mean([s.faithfulness for s in scores]),
            "answer_relevance_mean": _mean([s.answer_relevance for s in scores]),
            "context_precision_mean": _mean([s.context_precision for s in scores]),
        }

    # Top-N low scores per metric
    def _topn(metric: str) -> list[dict[str, Any]]:
        ranked = sorted(scored, key=lambda x: getattr(x[1], metric))
        out: list[dict[str, Any]] = []
        for turn, sc in ranked[:LOW_SCORE_TOPN]:
            out.append(
                {
                    "room": turn.get("room"),
                    "idx": turn.get("idx"),
                    "question": (turn.get("question") or "")[:120],
                    "answer_head": (turn.get("answer") or "")[:120],
                    "faithfulness": sc.faithfulness,
                    "answer_relevance": sc.answer_relevance,
                    "context_precision": sc.context_precision,
                    "n_claims": sc.n_claims,
                    "n_chunks": sc.n_chunks,
                }
            )
        return out

    return {
        "rooms": rooms_summary,
        "low_faithfulness_topN": _topn("faithfulness"),
        "low_context_precision_topN": _topn("context_precision"),
        "low_answer_relevance_topN": _topn("answer_relevance"),
    }


def _print_table(rooms_summary: dict[str, Any]) -> None:
    print(f"\n{'Room':<12} {'N':>4} {'Faith':>8} {'AnsRel':>8} {'CtxPrec':>9}")
    print("-" * 44)
    for room, agg in rooms_summary.items():
        print(
            f"{room:<12} {agg['n']:>4} {agg['faithfulness_mean']:>8.3f} "
            f"{agg['answer_relevance_mean']:>8.3f} {agg['context_precision_mean']:>9.3f}"
        )


async def main_async(args: argparse.Namespace) -> int:
    turns_raw = _load_turns(args.input)
    rooms = (
        [int(x.strip()) for x in args.rooms.split(",") if x.strip()]
        if args.rooms
        else None
    )
    turns = _filter_rooms(turns_raw, rooms)
    if not turns:
        print(f"ERROR: no turns to score (input={args.input}, rooms={rooms})", file=sys.stderr)
        return 2

    print(
        f"Loaded {len(turns)} turns. Judge={args.judge_model} Embed={args.embed_model} "
        f"Concurrency={args.concurrency}",
        flush=True,
    )

    evaluator = LLMRagasEvaluator(
        judge_model=args.judge_model,
        embed_model=args.embed_model,
        max_concurrency=args.concurrency,
        reverse_questions_n=args.reverse_questions_n,
    )

    t0 = time.perf_counter()
    coros = [_score_one(evaluator, t) for t in turns]
    results: list[tuple[dict[str, Any], TurnScore | None]] = await asyncio.gather(
        *coros, return_exceptions=False
    )
    wall_s = time.perf_counter() - t0

    scored: list[tuple[dict[str, Any], TurnScore]] = [
        (t, s) for t, s in results if s is not None
    ]
    skipped = len(results) - len(scored)

    aggregate = _aggregate(scored)

    out_payload = {
        "config": {
            "input": args.input,
            "rooms": rooms,
            "judge_model": args.judge_model,
            "embed_model": args.embed_model,
            "concurrency": args.concurrency,
            "reverse_questions_n": args.reverse_questions_n,
        },
        "wall_seconds": round(wall_s, 2),
        "n_turns_total": len(turns),
        "n_turns_scored": len(scored),
        "n_turns_skipped": skipped,
        "judge_calls_total": sum(s.judge_calls for _t, s in scored),
        "embed_calls_total": sum(s.embed_calls for _t, s in scored),
        "aggregate": aggregate,
        "per_turn": [
            {
                "room": t.get("room"),
                "idx": t.get("idx"),
                "question": t.get("question"),
                "classification": t.get("classification"),
                "score": asdict(s),
            }
            for t, s in scored
        ],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nWrote {out_path}  (wall {wall_s:.1f}s, scored={len(scored)} skipped={skipped})")
    _print_table(aggregate["rooms"])
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAGAS-style automated eval over 75q transcript JSON.")
    p.add_argument("--input", required=True, help="Glob to JSON files (e.g. /tmp/<bot>_75q_room*.json)")
    p.add_argument("--output", default=f"reports/RAGAS_75Q_{int(time.time())}.json")
    p.add_argument("--judge-model", default=os.getenv("RAGAS_JUDGE_MODEL", DEFAULT_RAGAS_JUDGE_MODEL))
    p.add_argument("--embed-model", default=os.getenv("RAGAS_EMBED_MODEL", DEFAULT_RAGAS_EMBED_MODEL))
    p.add_argument("--concurrency", type=int, default=DEFAULT_RAGAS_MAX_CONCURRENCY)
    p.add_argument("--reverse-questions-n", type=int, default=DEFAULT_RAGAS_REVERSE_QUESTIONS_N)
    p.add_argument("--rooms", default="", help="Optional comma-sep room filter (e.g. 1,2,3)")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(_parse_args())))
