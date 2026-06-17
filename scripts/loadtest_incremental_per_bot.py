"""Incremental per-bot driver for the 120Q load test.

Reuses QUESTIONS / ask / Result / compute_ragas_lite from the canonical
load-test module, but runs **one bot at a time** and prints that bot's
verdict + RAGAS-lite breakdown the moment its questions finish — instead
of waiting for all 120 to complete (matches the incremental-batch-reporting
requirement, feedback_subagent_lifecycle).

Output:
  - live per-bot table rows to stdout (and /tmp/loadtest_incr_<ts>.log)
  - /tmp/loadtest_incr_<ts>.jsonl  — one JSON line per bot as it finishes
  - /tmp/all_bots_load_<ts>.json   — full result at end (same shape as canonical)
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import asdict

import httpx

from tests.integration.test_all_bots_load_120q import (
    CONCURRENCY,
    QUESTIONS,
    Result,
    _fresh_token,
    aggregate,
    ask,
    compute_ragas_lite,
)


def _group_by_bot_in_order() -> "OrderedDict[str, list]":
    grouped: "OrderedDict[str, list]" = OrderedDict()
    for q in QUESTIONS:
        grouped.setdefault(q.bot_id, []).append(q)
    return grouped


def _print_bot_block(bot: str, rs: list[Result]) -> None:
    v = {"pass": 0, "partial": 0, "hallu": 0, "oos_correct": 0, "error": 0}
    for r in rs:
        v[r.verdict] = v.get(r.verdict, 0) + 1
    rl = compute_ragas_lite(rs)
    real_success = v["pass"] + v["oos_correct"]
    print()
    print(f"┌─ ✅ BOT DONE: {bot}  ({len(rs)} câu) "
          f"───────────────────────────────")
    print(f"│  pass={v['pass']}  partial={v['partial']}  oos_correct={v['oos_correct']}  "
          f"HALLU={v['hallu']}  err={v['error']}")
    print(f"│  real_success = {real_success}/{len(rs)} = {real_success/len(rs)*100:.0f}%")
    print(f"│  faithfulness={rl['faithfulness']}  relevance={rl['answer_relevance']}  "
          f"ctx_precision={rl['context_precision_proxy']}  oos_refuse={rl['oos_refuse_rate']}")
    print(f"│  latency p50/p95 = {rl['latency_p50_s']:.1f}s / {rl['latency_p95_s']:.1f}s")
    # Surface every HALLU + every refuse-on-corpus (partial) for this bot
    for r in rs:
        if r.verdict == "hallu":
            tag = "🔴 HALLU"
        elif r.verdict == "partial":
            tag = "🟡 partial"
        else:
            continue
        print(f"│   {tag} [{r.qid}] Q: {r.question[:55]}")
        print(f"│        A: {r.answer[:110]}")
        if r.must_contain_missing:
            print(f"│        MISS: {r.must_contain_missing}")
        if r.must_not_contain_violations:
            print(f"│        VIOLATE: {r.must_not_contain_violations}")
    print(f"└────────────────────────────────────────────────────────────")


async def main() -> None:
    grouped = _group_by_bot_in_order()
    ts = int(time.time())
    jsonl_path = f"/tmp/loadtest_incr_{ts}.jsonl"
    print(f"=== INCREMENTAL per-bot load test: {len(QUESTIONS)} câu / "
          f"{len(grouped)} bot, concurrency={CONCURRENCY} ===")
    print(f"=== mỗi bot xong sẽ chốt kết quả ngay. jsonl={jsonl_path} ===")

    sem = asyncio.Semaphore(CONCURRENCY)
    all_results: list[Result] = []
    t0 = time.time()

    async with httpx.AsyncClient() as client:
        try:
            await _fresh_token(client)
        except Exception as exc:  # noqa: BLE001 — top-level entrypoint warm
            print(f"WARM_FAILED: {type(exc).__name__}: {exc}")
            return

        for bot, qs in grouped.items():
            bt = time.time()
            rs: list[Result] = await asyncio.gather(*[ask(client, q, sem) for q in qs])
            all_results.extend(rs)
            _print_bot_block(bot, rs)

            v = {"pass": 0, "partial": 0, "hallu": 0, "oos_correct": 0, "error": 0}
            for r in rs:
                v[r.verdict] = v.get(r.verdict, 0) + 1
            with open(jsonl_path, "a") as f:
                f.write(json.dumps({
                    "bot_id": bot,
                    "verdicts": v,
                    "ragas_lite": compute_ragas_lite(rs),
                    "bot_wall_s": round(time.time() - bt, 1),
                    "results": [asdict(r) for r in rs],
                }, ensure_ascii=False) + "\n")

    wall = time.time() - t0
    agg = aggregate(all_results)
    out_path = f"/tmp/all_bots_load_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump({
            "wall_clock_s": round(wall, 2),
            "concurrency": CONCURRENCY,
            "aggregate": agg,
            "results": [asdict(r) for r in all_results],
        }, f, indent=2, ensure_ascii=False)

    vv = agg["verdicts"]
    n = len(all_results)
    print()
    print(f"=== FINAL AGGREGATE ({n} câu, {wall:.0f}s wall) ===")
    print(f"  pass={vv['pass']}  partial={vv['partial']}  oos_correct={vv['oos_correct']}  "
          f"HALLU={vv['hallu']}  error={vv['error']}")
    print(f"  real_success = {(vv['pass']+vv['oos_correct'])}/{n} = "
          f"{(vv['pass']+vv['oos_correct'])/n*100:.1f}%")
    a = agg["aggregate"]
    print(f"  faithfulness={a['faithfulness']}  relevance={a['answer_relevance']}  "
          f"ctx={a['context_precision_proxy']}  p95={a['latency_p95_s']:.1f}s")
    print(f"💾 {out_path}")
    print(f"💾 {jsonl_path}")


if __name__ == "__main__":
    asyncio.run(main())
