#!/usr/bin/env python3
"""LLM-as-judge audit for a test_rooms_v3.py harness JSON output.

For every turn:
  - Calls OpenAI (gpt-4.1-mini by default) with (question, answer, sources).
  - Judge returns 4 labels: answered, grounded, correct, hallucinated + reason.

Consistency analysis:
  - For each room, compares the answer to `questions[0]` given at turn 0
    vs the repeat at turn 12 (same connect_id, has history).
  - Compares turn 0 vs the cold-start probe for the same question
    (fresh connect_id, no history).
  - Emits consistency score per pair.

Outputs:
  reports/audit_<input_basename>.json   — per-turn judgements + metadata
  reports/audit_<input_basename>.md     — human-readable summary + top-N
                                          hallucinations, inconsistencies

Usage:
    python scripts/audit_harness_run.py reports/test_run_sprint3_final.json
    python scripts/audit_harness_run.py --max-turns 50 reports/run.json  # smoke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:
    sys.exit("openai package required: .venv/bin/pip install openai")


def _load_judge_model_from_system_config() -> str:
    """Đọc llm_default_model từ system_config DB — single source of truth.

    Không có fallback hardcode: nếu DB hoặc key missing, raise clear error
    để caller seed qua scripts/init_system_config.py.
    """
    import psycopg2  # only when script actually runs
    dsn = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL_SYNC / DATABASE_URL required to resolve judge model "
            "from system_config. Source .env first."
        )
    if dsn.startswith("postgresql+psycopg2://"):
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM system_config WHERE key = %s", ("llm_default_model",))
        row = cur.fetchone()
    if not row or not row[0]:
        raise RuntimeError(
            "system_config.llm_default_model is missing. Run scripts/init_system_config.py "
            "to seed defaults, or INSERT via admin UI."
        )
    val = row[0]
    return val.strip('"') if isinstance(val, str) else str(val)


JUDGE_MODEL = _load_judge_model_from_system_config()

JUDGE_SYSTEM = """You are a strict auditor for a Vietnamese customer-service spa bot.
Domain: a generic beauty/spa brand (hair wash, facial, laser hair removal, massage, combo pricing).

Given a (question, bot_answer, retrieved_sources), return STRICT JSON:
{
  "answered": bool,        // true if bot gave a substantive reply (not refuse/greeting/stall)
  "grounded": bool,        // true if the answer is supported by retrieved_sources
  "correct": bool,         // true if the answer is factually plausible for a spa context
                           //   and consistent with the sources (if grounded=false, correct=false
                           //   UNLESS the question is small-talk/clarify)
  "hallucinated": bool,    // true if the answer mentions specific prices/durations/services
                           //   NOT present in retrieved_sources (strict check)
  "reason": "<=40 words Vietnamese explanation"
}

IMPORTANT:
- If the answer is a clarifying question ("anh/chị muốn hỏi về dịch vụ nào?"), set
  answered=true, grounded=true, correct=true, hallucinated=false.
- If the bot declines out-of-scope (weather, bitcoin, etc.), set answered=true,
  grounded=true (design-correct), correct=true, hallucinated=false.
- Any specific number (giá, phút, %) must appear in sources or be marked
  hallucinated=true.
- Answer must match the language of the question (Vietnamese).

Output ONLY valid JSON. No markdown fences."""


CONSISTENCY_SYSTEM = """You compare two answers from the same bot to the SAME question.

Given (question, answer_A, answer_B), return STRICT JSON:
{
  "semantically_equivalent": bool,   // same meaning, allowing paraphrase
  "price_consistent": bool,           // same prices / durations / numbers (null if neither mentions numbers)
  "hallucinated_difference": bool,    // differing facts that cannot both be true
  "reason": "<=40 words Vietnamese"
}

Output ONLY valid JSON."""


async def _judge_one(client: AsyncOpenAI, turn: dict) -> dict:
    question = turn.get("_question", "")
    answer = turn.get("answer") or turn.get("_error") or ""
    sources = turn.get("sources", [])

    # HARN-3: prefer real chunk content when the harness captured it
    # (requires --debug=full on test_rooms_v3). With chunk text the judge
    # can verify specific numbers instead of conservatively flagging them.
    # Fall back to legacy source-names payload when content is absent so
    # old run files (pre-HARN-3) still judge-able.
    chunks_content = turn.get("retrieved_chunks_content") or []
    if chunks_content:
        chunks_text = "\n\n".join(
            f"[chunk {i + 1}] {c.get('source') or ''}:\n{(c.get('content') or '')[:2000]}"
            for i, c in enumerate(chunks_content[:5])
        )
        user_msg = (
            f"Câu hỏi khách: {question}\n\n"
            f"Câu trả lời bot: {answer}\n\n"
            f"Nội dung chunks đã retrieve:\n{chunks_text}"
        )
    else:
        sources_str = "\n".join(f"- {s}" for s in sources) if sources else "(không có nguồn nào)"
        user_msg = (
            f"Câu hỏi khách: {question}\n\n"
            f"Câu trả lời bot: {answer}\n\n"
            f"Nguồn đã retrieve:\n{sources_str}"
        )

    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            return json.loads(raw)
        except Exception as e:
            if attempt == 2:
                return {
                    "answered": None, "grounded": None, "correct": None,
                    "hallucinated": None, "reason": f"judge_error: {e!r}",
                }
            await asyncio.sleep(1 + attempt)
    return {}


async def _consistency_one(client: AsyncOpenAI, question: str, answer_a: str, answer_b: str) -> dict:
    user_msg = (
        f"Câu hỏi: {question}\n\n"
        f"Answer A: {answer_a}\n\n"
        f"Answer B: {answer_b}"
    )
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": CONSISTENCY_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:
            if attempt == 2:
                return {"semantically_equivalent": None, "reason": f"consistency_error: {e!r}"}
            await asyncio.sleep(1 + attempt)
    return {}


def _collect_turns(run: dict, max_turns: int | None) -> list[tuple[str, dict, bool]]:
    """Yield (room_id, turn, is_cold) across the run, capped at max_turns."""
    out: list[tuple[str, dict, bool]] = []
    for room in run.get("rooms", []):
        rid = room["room_id"]
        for t in room.get("turns", []):
            out.append((rid, t, False))
        for t in room.get("cold_start_probes") or room.get("cold_probes") or []:
            out.append((rid, t, True))
    if max_turns is not None:
        out = out[:max_turns]
    return out


async def _bounded_gather(coros, limit: int = 6):
    sem = asyncio.Semaphore(limit)

    async def _wrap(c):
        async with sem:
            return await c

    return await asyncio.gather(*[_wrap(c) for c in coros])


async def audit_async(args):
    in_path = Path(args.input)
    run = json.loads(in_path.read_text(encoding="utf-8"))
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    turns = _collect_turns(run, args.max_turns)
    print(f"Judging {len(turns)} turns with model {JUDGE_MODEL}...")
    t0 = time.time()

    judgements = await _bounded_gather(
        [_judge_one(client, turn) for (_, turn, _) in turns],
        limit=args.concurrency,
    )
    print(f"  judged in {time.time() - t0:.1f}s")

    per_turn: list[dict] = []
    for (rid, turn, is_cold), verdict in zip(turns, judgements):
        per_turn.append({
            "room_id": rid,
            "idx": turn.get("_idx"),
            "cold_start": is_cold,
            "question": turn.get("_question"),
            "answer_type": turn.get("answer_type") or ("error" if turn.get("_error") else "unknown"),
            "answer_preview": (turn.get("answer") or turn.get("_error") or "")[:200],
            "top_score": turn.get("top_score"),
            "chunks_used": turn.get("chunks_used"),
            "sources": turn.get("sources"),
            "judge": verdict,
        })

    pairs: list[dict] = []
    for room in run.get("rooms", []):
        rid = room["room_id"]
        turns_by_idx = {t.get("_idx"): t for t in room.get("turns", [])}
        cold_probes = room.get("cold_start_probes") or room.get("cold_probes") or []
        cold_by_idx = {t.get("_idx"): t for t in cold_probes}

        for (orig_idx, repeat_idx) in [(0, 12), (2, 14)]:
            t_orig = turns_by_idx.get(orig_idx)
            t_repeat = turns_by_idx.get(repeat_idx)
            t_cold = cold_by_idx.get(orig_idx)

            if t_orig and t_repeat and t_orig.get("answer") and t_repeat.get("answer"):
                r = await _consistency_one(
                    client, t_orig.get("_question", ""),
                    t_orig["answer"], t_repeat["answer"],
                )
                pairs.append({
                    "room_id": rid, "kind": "in_history",
                    "orig_idx": orig_idx, "repeat_idx": repeat_idx,
                    "question": t_orig.get("_question"),
                    "answer_a": t_orig["answer"][:200],
                    "answer_b": t_repeat["answer"][:200],
                    "judge": r,
                })
            if t_orig and t_cold and t_orig.get("answer") and t_cold.get("answer"):
                r = await _consistency_one(
                    client, t_orig.get("_question", ""),
                    t_orig["answer"], t_cold["answer"],
                )
                pairs.append({
                    "room_id": rid, "kind": "cold_start",
                    "orig_idx": orig_idx, "cold_idx": orig_idx,
                    "question": t_orig.get("_question"),
                    "answer_a": t_orig["answer"][:200],
                    "answer_b": t_cold["answer"][:200],
                    "judge": r,
                })

    out_dir = Path("reports")
    stem = in_path.stem
    out_json = out_dir / f"audit_{stem}.json"
    out_md = out_dir / f"audit_{stem}.md"

    out_doc = {
        "input": str(in_path),
        "judge_model": JUDGE_MODEL,
        "n_turns_judged": len(per_turn),
        "n_pairs": len(pairs),
        "per_turn": per_turn,
        "consistency_pairs": pairs,
    }
    out_json.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2))

    _write_markdown_report(out_md, per_turn, pairs, input_path=in_path)
    print(f"\nWrote: {out_json}")
    print(f"Wrote: {out_md}")


def _write_markdown_report(
    out_md: Path, per_turn: list[dict], pairs: list[dict], *, input_path: Path
) -> None:
    def _bool_pct(items: list[dict], key: str) -> str:
        vals = [it["judge"].get(key) for it in items if isinstance(it.get("judge"), dict)]
        vals = [v for v in vals if v is not None]
        if not vals:
            return "n/a"
        return f"{sum(1 for v in vals if v) / len(vals):.1%} ({sum(1 for v in vals if v)}/{len(vals)})"

    warm = [t for t in per_turn if not t["cold_start"]]
    cold = [t for t in per_turn if t["cold_start"]]

    def _bucket(items, label):
        if not items:
            return f"_{label}: 0 turns_\n"
        return (
            f"### {label} ({len(items)} turns)\n\n"
            f"- answered:     {_bool_pct(items, 'answered')}\n"
            f"- grounded:     {_bool_pct(items, 'grounded')}\n"
            f"- correct:      {_bool_pct(items, 'correct')}\n"
            f"- hallucinated: {_bool_pct(items, 'hallucinated')}\n"
        )

    halluc = sorted(
        [t for t in per_turn if (t.get("judge") or {}).get("hallucinated")],
        key=lambda t: (t["room_id"], t["idx"] or 0),
    )[:10]

    inconsistent_pairs = [
        p for p in pairs
        if (p.get("judge") or {}).get("hallucinated_difference")
        or (p.get("judge") or {}).get("semantically_equivalent") is False
    ][:10]

    lines: list[str] = []
    lines.append(f"# LLM-as-judge audit — {input_path.name}\n")
    lines.append(f"> Judge model: `{JUDGE_MODEL}` | Turns: {len(per_turn)} | Consistency pairs: {len(pairs)}\n")

    lines.append("\n## Overall verdict\n")
    lines.append(_bucket(per_turn, "All turns"))
    lines.append("\n## In-history vs cold-start\n")
    lines.append(_bucket(warm, "Warm (has history)"))
    lines.append("\n")
    lines.append(_bucket(cold, "Cold-start (fresh connect_id)"))

    if halluc:
        lines.append("\n## Top hallucinations (up to 10)\n")
        for h in halluc:
            j = h.get("judge") or {}
            lines.append(
                f"- **{h['room_id']} #{h['idx']}** — `{h['question']}`\n"
                f"  - Answer: {h['answer_preview']}\n"
                f"  - Sources: {h.get('sources')}\n"
                f"  - Judge: {j.get('reason')}\n"
            )

    if pairs:
        total_pairs = len(pairs)
        same = sum(1 for p in pairs if (p.get("judge") or {}).get("semantically_equivalent"))
        price_ok = sum(1 for p in pairs if (p.get("judge") or {}).get("price_consistent"))
        halluc_diff = sum(1 for p in pairs if (p.get("judge") or {}).get("hallucinated_difference"))
        lines.append("\n## Consistency (same question, repeat)\n")
        lines.append(f"- semantically_equivalent:  {same}/{total_pairs} ({same / total_pairs:.1%})\n")
        lines.append(f"- price_consistent:         {price_ok}/{total_pairs} ({price_ok / total_pairs:.1%})\n")
        lines.append(f"- hallucinated_difference:  {halluc_diff}/{total_pairs} ({halluc_diff / total_pairs:.1%})\n")

        kinds = {}
        for p in pairs:
            kinds.setdefault(p["kind"], []).append(p)
        for kind, items in sorted(kinds.items()):
            same_k = sum(1 for p in items if (p.get("judge") or {}).get("semantically_equivalent"))
            lines.append(f"  - **{kind}**: {same_k}/{len(items)} ({same_k / len(items):.1%}) semantically equivalent\n")

    if inconsistent_pairs:
        lines.append("\n## Top inconsistencies (up to 10)\n")
        for p in inconsistent_pairs:
            j = p.get("judge") or {}
            lines.append(
                f"- **{p['room_id']} / {p['kind']}** — `{p['question']}`\n"
                f"  - A: {p['answer_a']}\n"
                f"  - B: {p['answer_b']}\n"
                f"  - Judge: {j.get('reason')}\n"
            )

    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", help="harness run JSON")
    p.add_argument("--max-turns", type=int, default=None, help="Cap turns (for smoke test)")
    p.add_argument("--concurrency", type=int, default=6, help="LLM call concurrency")
    args = p.parse_args()
    asyncio.run(audit_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
