#!/usr/bin/env python3
"""Demo bot conversation test — 100 rooms x ~15 questions with repeat probes.

Specific constraints:
  - 100 unique rooms.
  - ~15 questions per room.
  - At a random index between 11 and 14, repeat the very first question asked.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# If running from project root, use scripts.test_rooms_v2
try:
    from scripts.test_rooms_v2 import ROOMS
except ImportError:
    from test_rooms_v2 import ROOMS

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_ID = os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1")
CHANNEL = "web"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

async def get_self_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}{SELF_TOKEN_PATH}")
    r.raise_for_status()
    return r.json()["token"]

async def ask(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    channel: str,
    connect_id: str,
    question: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}{CHAT_PATH}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "bot_id": bot_id,
                "channel_type": channel,
                "connect_id": connect_id,
                "question": question,
            },
            timeout=60.0,
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}", "_wall_ms": wall_ms, "body": r.text[:400]}
        body = r.json()
    except Exception as exc:
        return {"_error": str(exc)[:300], "_wall_ms": (time.perf_counter() - t0) * 1000}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return {
        "answer": (data.get("answer") or "")[:2000],
        "answer_type": data.get("answer_type"),
        "answer_reason": data.get("answer_reason"),
        "chunks_used": data.get("chunks_used"),
        "top_score": data.get("top_score"),
        "duration_ms": data.get("duration_ms"),
        "tokens": data.get("tokens"),
        "cost_usd": data.get("cost_usd"),
        "sources": [s.get("document_name") for s in (data.get("sources") or [])] if isinstance(data.get("sources"), list) else [],
        "debug": data.get("debug"),
        "_wall_ms": wall_ms,
    }


def generate_100_rooms() -> list[dict]:
    # We create exactly 100 rooms. We cycle through the base ROOMS to ensure coverage,
    # then randomize somewhat.
    generated = []
    base_count = len(ROOMS)
    for i in range(100):
        base_room = ROOMS[i % base_count]
        # To make it slightly variable, we can shuffle the questions 1 to 13, but keep 0 at the start.
        # The prompt says "hỏi đến câu thứ 12 15 random thì cố ý hỏi lại câu đầu đã hỏi"
        
        # We need a base sequence of 14 questions.
        q_pool = list(base_room["questions"])
        
        # Pick 14 questions
        seq = q_pool[:14]
        
        # 0th question
        q0 = seq[0]
        
        # Insert repeat of Q0 at random index 11, 12, 13, or 14 (which becomes the 12th, 13th, 14th or 15th question)
        repeat_idx = random.randint(11, 14)
        seq.insert(repeat_idx, q0)
        
        generated.append({
            "id": f"r100_{i:03d}_{base_room['id']}",
            "topic": base_room["topic"],
            "questions": seq,
            "repeat_idx": repeat_idx
        })
    return generated


async def run_room(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    channel: str,
    room: dict,
) -> dict:
    connect_id = f"test-100-{room['id']}-{int(time.time())}"
    questions = room["questions"]
    
    turns = []
    for i, q in enumerate(questions):
        resp = await ask(client, token, bot_id=bot_id, channel=channel, connect_id=connect_id, question=q)
        resp["_idx"] = i
        resp["_question"] = q
        resp["_is_repeat_probe"] = (i == room["repeat_idx"])
        turns.append(resp)
        print(f"  [{room['id']}] Q{i+1}: {q[:30]}... -> {resp.get('answer_type', 'err')} ({resp.get('duration_ms', 0)}ms)")
        await asyncio.sleep(0.1)  # tiny jitter
        
    # the second requirement: no history room chat answer comparison.
    # we ask the first question on a completely new session
    # the cold start vs hot start comparison for Q0
    fresh_conn = f"test-100-cold-{room['id']}-{int(time.time())}"
    probe_q = questions[0]
    cold_start_resp = await ask(
        client, token, bot_id=bot_id, channel=channel, connect_id=fresh_conn, question=probe_q
    )
    cold_start_resp["_question"] = probe_q
    cold_start_resp["_cold_start"] = True

    return {
        "room_id": room["id"],
        "topic": room["topic"],
        "n_turns": len(turns),
        "turns": turns,
        "cold_start_probe": cold_start_resp,
        "repeat_idx": room["repeat_idx"]
    }

def summarize(results: dict) -> dict:
    rooms = results["rooms"]
    all_turns = [t for r in rooms for t in r["turns"]]
    answered = [t for t in all_turns if t.get("answer_type") == "answered"]
    blocked = [t for t in all_turns if t.get("answer_type") == "blocked"]
    errored = [t for t in all_turns if t.get("_error")]
    oos = [t for t in all_turns if t.get("answer_type") == "out_of_scope"]
    no_ctx = [t for t in all_turns if t.get("answer_type") == "no_context"]

    # Probe metrics: analyze caching and context hits on the repeated probe
    probes_hot = [t for r in rooms for t in r["turns"] if t.get("_is_repeat_probe")]
    probes_cold = [r["cold_start_probe"] for r in rooms]
    
    def _avg(key, src):
        vals = [t.get(key) or 0 for t in src if t.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    total_cost = sum(t.get("cost_usd") or 0 for t in all_turns)
    total_prompt_tokens = sum((t.get("tokens") or {}).get("prompt", 0) for t in all_turns)
    total_completion_tokens = sum((t.get("tokens") or {}).get("completion", 0) for t in all_turns)
    
    hot_answers = sum(1 for p in probes_hot if p.get("answer_type") == "answered")
    cold_answers = sum(1 for p in probes_cold if p.get("answer_type") == "answered")

    return {
        "total_rooms": len(rooms),
        "total_turns": len(all_turns),
        "answered": len(answered),
        "blocked": len(blocked),
        "out_of_scope": len(oos),
        "no_context": len(no_ctx),
        "errors": len(errored),
        "answer_rate": round(len(answered) / max(len(all_turns), 1), 3),
        "avg_duration_ms": _avg("duration_ms", all_turns),
        "avg_chunks_used": _avg("chunks_used", all_turns),
        "avg_top_score": _avg("top_score", all_turns),
        "probe_hot_latency_ms": _avg("duration_ms", probes_hot),
        "probe_cold_latency_ms": _avg("duration_ms", probes_cold),
        "probe_hot_answered": hot_answers,
        "probe_cold_answered": cold_answers,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost_usd": round(total_cost, 6),
    }

async def main_async(args):
    rooms_data = generate_100_rooms()
    if args.rooms < 100:
        rooms_data = rooms_data[:args.rooms]
        
    async with httpx.AsyncClient() as client:
        token = await get_self_token(client)
        print(f"Token acquired. Starting 100 rooms evaluation.")
        results = {"rooms": [], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        
        batch_size = 35
        for i in range(0, len(rooms_data), batch_size):
            batch = rooms_data[i:i + batch_size]
            print(f"\n--- Batch {i//batch_size + 1} (Rooms {i+1} to {i+len(batch)}) ---")
            
            tasks = []
            for r in batch:
                tasks.append(run_room(client, token, bot_id=BOT_ID, channel=CHANNEL, room=r))
                
            batch_results = await asyncio.gather(*tasks)
            results["rooms"].extend(batch_results)
            
            # small sleep between batches
            await asyncio.sleep(1)

        results["summary"] = summarize(results)
        results["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        out_path = Path(args.output) if args.output else Path(
            f"reports/test_run_100_rooms_{time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        s = results["summary"]
        print("\n" + "=" * 70)
        print(f"SUMMARY ({s['total_rooms']} rooms test)")
        print("=" * 70)
        print(f"  Total turns:      {s['total_turns']}")
        print(f"  Answered:         {s['answered']}  ({s['answer_rate']:.1%})")
        print(f"  Blocked:          {s['blocked']}")
        print(f"  Out of scope:     {s['out_of_scope']}")
        print(f"  No context:       {s['no_context']}")
        print(f"  Errors:           {s['errors']}")
        print(f"  Avg duration:     {s['avg_duration_ms']:.0f} ms")
        print(f"  Avg chunks used:  {s['avg_chunks_used']:.2f}")
        print(f"  Avg top_score:    {s['avg_top_score']:.4f}")
        print(f"  Probe Hot:        {s['probe_hot_answered']} answered, avg {s['probe_hot_latency_ms']:.0f} ms")
        print(f"  Probe Cold:       {s['probe_cold_answered']} answered, avg {s['probe_cold_latency_ms']:.0f} ms")
        print(f"  Total cost USD:   ${s['total_cost_usd']:.4f}")
        print(f"\nDetailed report written to: {out_path}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rooms", type=int, default=100)
    p.add_argument("--output", default="")
    args = p.parse_args()
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()
