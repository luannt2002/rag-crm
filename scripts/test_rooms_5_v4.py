#!/usr/bin/env python3
"""Demo bot conversation test — 5 rooms x 15 questions with 2 repeat probes.

Specific constraints:
  - 5 rooms.
  - 15 questions per room.
  - From idx 10 to 14 (roughly 10-15), 2 questions MUST exactly duplicate questions from idx 0 to 9.
"""
from __future__ import annotations
import argparse, asyncio, json, os, random, sys, time
from pathlib import Path
from typing import Any
import httpx

try:
    from scripts.test_rooms_v2 import ROOMS
except ImportError:
    from test_rooms_v2 import ROOMS

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_ID = os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1")
CHANNEL = "web"
TENANT_ID = int(os.getenv("RAGBOT_TEST_TENANT_ID", "32"))
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
    bypass_cache: bool = False,
    debug: str = "",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        payload = {
            "tenant_id": TENANT_ID,
            "bot_id": bot_id,
            "channel_type": channel,
            "connect_id": connect_id,
            "question": question,
        }
        if bypass_cache:
            payload["bypass_cache"] = True
        if debug:
            payload["debug"] = debug
        r = await client.post(
            f"{BASE_URL}{CHAT_PATH}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
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
        "duration_ms": data.get("duration_ms"),
        "cost_usd": data.get("cost_usd", 0),
        "citations": data.get("citations") or [],
        "sources": data.get("sources") or [],
        "debug": data.get("debug")
    }

def generate_5_rooms() -> list[dict]:
    generated = []
    for i in range(5):
        base_room = ROOMS[i % len(ROOMS)]
        q_pool = list(base_room["questions"])
        seq = q_pool[:13] # core 13 questions
        
        # Pick 2 questions from index 0 to 9
        dupe1_idx = random.randint(0, 4)
        dupe2_idx = random.randint(5, 9)
        dupe1 = seq[dupe1_idx]
        dupe2 = seq[dupe2_idx]
        
        # Insert them into the end positions 13 and 14
        seq.append(dupe1)
        seq.append(dupe2)
        
        generated.append({
            "id": f"r5_{i:02d}_{base_room['id']}",
            "topic": base_room["topic"],
            "questions": seq,
            "repeat_indices": [13, 14],
            "original_indices": [dupe1_idx, dupe2_idx]
        })
    return generated

async def run_room(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    channel: str,
    room: dict,
    bypass_cache: bool = False,
    debug: str = "",
) -> dict:
    connect_id = f"test-5-room-{room['id']}-{int(time.time())}"
    questions = room["questions"]
    turns = []

    for i, q in enumerate(questions):
        resp = await ask(
            client,
            token,
            bot_id=bot_id,
            channel=channel,
            connect_id=connect_id,
            question=q,
            bypass_cache=bypass_cache,
            debug=debug,
        )
        resp["_idx"] = i
        resp["_question"] = q
        resp["_is_repeat"] = i in room["repeat_indices"]
        turns.append(resp)
        print(f"  [{room['id']}] Q{i+1}/{len(questions)}: {q[:30]}... -> {resp.get('answer_type')} ({resp.get('duration_ms',0)}ms)")
        await asyncio.sleep(0.1)

    return {
        "room_id": room["id"],
        "n_turns": len(turns),
        "turns": turns,
    }

def summarize(results: dict) -> dict:
    all_turns = [t for r in results["rooms"] for t in r["turns"]]
    repeats = [t for t in all_turns if t.get("_is_repeat")]
    non_repeats = [t for t in all_turns if not t.get("_is_repeat")]
    
    return {
        "total_rooms": len(results["rooms"]),
        "total_turns": len(all_turns),
        "answered": sum(1 for t in all_turns if t.get("answer_type") == "answered"),
        "non_repeat_avg_latency_ms": sum(t.get("duration_ms", 0) for t in non_repeats) / max(1, len(non_repeats)),
        "repeat_answered": sum(1 for t in repeats if t.get("answer_type") == "answered"),
        "repeat_avg_latency_ms": sum(t.get("duration_ms", 0) for t in repeats) / max(1, len(repeats))
    }

async def main_async(args):
    rooms_data = generate_5_rooms()
    async with httpx.AsyncClient() as client:
        token = await get_self_token(client)
        print(
            f"Token acquired. Starting 5 rooms evaluation "
            f"(bypass_cache={args.bypass_cache}, debug={args.debug or 'off'}, "
            f"serial={args.serial})."
        )
        results = {
            "rooms": [],
            "config": {
                "bypass_cache": args.bypass_cache,
                "debug": args.debug,
                "serial": args.serial,
            },
        }

        if args.serial:
            # Serial mode — 1 room at a time. Use when external API
            # rate limits (e.g. Jina free tier 100 RPM) cap concurrent runs.
            batch_results = []
            for r in rooms_data:
                out = await run_room(
                    client, token,
                    bot_id=BOT_ID, channel=CHANNEL, room=r,
                    bypass_cache=args.bypass_cache, debug=args.debug,
                )
                batch_results.append(out)
        else:
            # Concurrent — 5 rooms parallel (default; OK if no rate limit).
            tasks = [
                run_room(
                    client, token,
                    bot_id=BOT_ID, channel=CHANNEL, room=r,
                    bypass_cache=args.bypass_cache, debug=args.debug,
                )
                for r in rooms_data
            ]
            batch_results = await asyncio.gather(*tasks)
        results["rooms"] = batch_results
        
        results["summary"] = summarize(results)
        
        out_path = Path(args.output) if args.output else Path("reports/test_run_5_rooms_latest.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print(f"\nTest finished. Results written to {out_path}.")
        print(json.dumps(results["summary"], indent=2))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="5-room × 15-question test harness for ragbot.")
    p.add_argument(
        "--bypass-cache",
        action="store_true",
        help="Test-mode: skip semantic cache so pipeline always runs.",
    )
    p.add_argument(
        "--debug",
        default="",
        choices=["", "full"],
        help="Pass debug=full to /test/chat for retrieval_chunks_content.",
    )
    p.add_argument(
        "--output",
        default="",
        help="Output JSON path. Default reports/test_run_5_rooms_latest.json.",
    )
    p.add_argument(
        "--serial",
        action="store_true",
        help="Run rooms 1-by-1 instead of concurrent. Use when external API "
             "rate limits (Jina free 100 RPM) cap concurrent throughput.",
    )
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(_parse_args()))
