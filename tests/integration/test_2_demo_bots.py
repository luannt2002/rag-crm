"""Subset load test — only test-spa-id + thong-tu-09-2020-tt-nhnn (demo bots).

Run after alembic 0158 (spa sysprompt fix rules 22 + 23) to verify
spa-05 hotline + spa-07 giá CSD work correctly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path("/var/www/html/ragbot")
sys.path.insert(0, str(ROOT / "tests" / "integration"))

# Reuse harness from full 120Q test
from test_all_bots_load_120q import (  # noqa: E402
    QUESTIONS, ask, aggregate, _fresh_token,
    CONCURRENCY,
)
import httpx
import json
import time

DEMO_BOTS = {"test-spa-id", "thong-tu-09-2020-tt-nhnn"}


async def main() -> None:
    demo_questions = [q for q in QUESTIONS if q.bot_id in DEMO_BOTS]
    print(f"=== 2 DEMO BOT TEST: {len(demo_questions)} questions ===")
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        _ = await _fresh_token(client)
        t0 = time.time()
        tasks = [ask(client, q, sem) for q in demo_questions]
        results = await asyncio.gather(*tasks)
        wall = time.time() - t0

    # Print each result inline
    print()
    by_bot = {}
    for r in results:
        by_bot.setdefault(r.bot_id, []).append(r)
    for bot, rs in by_bot.items():
        print(f"\n{'='*100}")
        print(f"BOT: {bot}")
        print('='*100)
        for r in rs:
            icon = {"pass":"✅","partial":"🟡","oos_correct":"🚫","hallu":"🔴","error":"❌"}[r.verdict]
            print(f"\n{icon} [{r.qid}] {r.verdict.upper():12s} | {r.question}")
            ans = r.answer[:250].replace("\n", " ")
            print(f"   A: {ans}")
            if r.must_contain_missing:
                print(f"   MISS: {r.must_contain_missing}")
            if r.must_not_contain_violations:
                print(f"   VIOLATE: {r.must_not_contain_violations}")

    agg = aggregate(results)
    out = f"/tmp/demo_2bots_{int(time.time())}.json"
    with open(out, "w") as f:
        json.dump({"wall_s": wall, "results": [r.__dict__ for r in results], "aggregate": agg},
                  f, indent=2, ensure_ascii=False)

    print()
    print(f"\n{'='*100}")
    print(f"AGGREGATE ({len(demo_questions)}Q, {wall:.1f}s)")
    print('='*100)
    v = agg["verdicts"]
    print(f"  ✅ pass        : {v['pass']:2d}/{len(demo_questions)}")
    print(f"  🟡 partial     : {v['partial']:2d}/{len(demo_questions)}")
    print(f"  🚫 oos_correct : {v['oos_correct']:2d}/{len(demo_questions)}")
    print(f"  🔴 HALLU       : {v['hallu']:2d}/{len(demo_questions)}  ⭐")
    print(f"  ❌ error       : {v['error']:2d}/{len(demo_questions)}")
    r = agg["aggregate"]
    print()
    print(f"  faithfulness    : {r['faithfulness']}")
    print(f"  answer_relev    : {r['answer_relevance']}")
    print(f"  ctx_precision   : {r['context_precision_proxy']}")
    print(f"  oos_refuse_rate : {r['oos_refuse_rate']}")
    print(f"  p50/p95/p99 (s) : {r['latency_p50_s']:.2f}/{r['latency_p95_s']:.2f}/{r['latency_p99_s']:.2f}")
    print()
    print(f"💾 saved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
