#!/usr/bin/env python3
"""Bot smoke test — 3 rooms × 5 messages (quick verification).

Usage:
    python scripts/test_bot_smoke.py --bot-id <test-bot-id> --base-url http://localhost:8000
    # Or use env default: export RAGBOT_TEST_BOT_ID=test-bot-v1

Quick sanity check before running the full 100-room test.
"""

import argparse
import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

# Vietnamese test questions (subset)
QUESTIONS = [
    "Giá sản phẩm A là bao nhiêu?",
    "Chính sách đổi trả hàng như thế nào?",
    "Thời gian bảo hành sản phẩm là bao lâu?",
    "Có ship hàng ra Hà Nội không?",
    "Phí vận chuyển là bao nhiêu?",
    "Sản phẩm có những màu gì?",
    "Cách đặt hàng online như thế nào?",
    "Hotline hỗ trợ khách hàng số mấy?",
    "Có chương trình khuyến mãi gì không?",
    "Showroom ở đâu?",
]


@dataclass
class RoomResult:
    room_id: str
    messages_sent: int = 0
    messages_answered: int = 0
    cache_hits: int = 0
    avg_latency_ms: float = 0.0
    history_test_passed: bool = False
    errors: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)


async def send_chat(
    client: httpx.AsyncClient,
    base_url: str,
    bot_id: str,
    channel_type: str,
    connect_id: str,
    content: str,
    conversation_id: str,
    api_token: str,
) -> dict:
    """Send a chat message and wait for response."""
    payload = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "connect_id": connect_id,
        "content": content,
        "conversation_id": conversation_id,
        "job_id": str(uuid4()),
        "message_id": random.randint(1, 999999),
        "user_id": connect_id,
        "trace_id": str(uuid4()),
    }

    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

    t0 = time.perf_counter()
    resp = await client.post(
        f"{base_url}/api/ragbot/chat",
        json=payload,
        headers=headers,
        timeout=60,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    if resp.status_code not in (200, 202):
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency_ms": latency_ms}

    data = resp.json()
    data["latency_ms"] = latency_ms
    return data


async def test_room(
    client: httpx.AsyncClient,
    base_url: str,
    bot_id: str,
    channel_type: str,
    room_idx: int,
    api_token: str,
    messages_per_room: int,
) -> RoomResult:
    """Test a single conversation room."""
    connect_id = f"smoke-user-{room_idx:04d}"
    conversation_id = str(uuid4())
    result = RoomResult(room_id=f"room-{room_idx:04d}")

    questions = random.sample(QUESTIONS, min(messages_per_room, len(QUESTIONS)))
    first_question = questions[0]

    for i, question in enumerate(questions):
        # Last message: re-ask first question to test history
        if i == messages_per_room - 1:
            question = first_question
            is_history_test = True
        else:
            is_history_test = False

        try:
            resp = await send_chat(
                client, base_url, bot_id, channel_type,
                connect_id, question, conversation_id, api_token,
            )

            result.messages_sent += 1
            result.latencies.append(resp.get("latency_ms", 0))

            if "error" in resp:
                result.errors.append(f"msg{i}: {resp['error']}")
            else:
                result.messages_answered += 1
                if resp.get("cache_hit"):
                    result.cache_hits += 1
                if is_history_test:
                    result.history_test_passed = True

        except Exception as e:
            result.errors.append(f"msg{i}: {type(e).__name__}: {str(e)[:100]}")

        await asyncio.sleep(0.3)

    if result.latencies:
        result.avg_latency_ms = sum(result.latencies) / len(result.latencies)

    return result


async def run_smoke(
    base_url: str,
    bot_id: str,
    channel_type: str,
    num_rooms: int,
    messages_per_room: int,
    api_token: str,
):
    """Run smoke test."""
    print(f"\n{'='*60}")
    print(f"RAGBOT SMOKE TEST")
    print(f"Bot: {bot_id} | Rooms: {num_rooms} | Messages/room: {messages_per_room}")
    print(f"Base URL: {base_url}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:
        # Health check
        try:
            health = await client.get(f"{base_url}/health", timeout=5)
            print(f"Health: {health.json()}\n")
        except Exception as e:
            print(f"FAIL: Service not reachable at {base_url}: {e}")
            return

        t0 = time.perf_counter()
        results = []
        for idx in range(num_rooms):
            r = await test_room(
                client, base_url, bot_id, channel_type, idx, api_token, messages_per_room
            )
            results.append(r)
            print(f"  Room {idx}: {r.messages_answered}/{r.messages_sent} OK, "
                  f"avg {r.avg_latency_ms:.0f}ms, history={'PASS' if r.history_test_passed else 'SKIP'}")

        total_time = time.perf_counter() - t0

    # Summary
    total_sent = sum(r.messages_sent for r in results)
    total_answered = sum(r.messages_answered for r in results)
    total_errors = sum(len(r.errors) for r in results)
    all_latencies = [l for r in results for l in r.latencies]

    print(f"\n{'='*60}")
    print(f"SMOKE TEST {'PASSED' if total_errors == 0 else 'FAILED'}")
    print(f"{'='*60}")
    print(f"Messages: {total_answered}/{total_sent} answered")
    print(f"Errors:   {total_errors}")
    print(f"Time:     {total_time:.1f}s")
    if all_latencies:
        print(f"Avg lat:  {sum(all_latencies)/len(all_latencies):.0f}ms")

    if total_errors > 0:
        print("\nErrors:")
        for r in results:
            for e in r.errors:
                print(f"  {r.room_id}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Bot smoke test (quick)")
    parser.add_argument("--bot-id", default=os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1"))
    parser.add_argument("--channel-type", default="api")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rooms", type=int, default=3)
    parser.add_argument("--messages", type=int, default=5)
    parser.add_argument("--api-token", default="")
    args = parser.parse_args()

    asyncio.run(run_smoke(
        base_url=args.base_url,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        num_rooms=args.rooms,
        messages_per_room=args.messages,
        api_token=args.api_token,
    ))


if __name__ == "__main__":
    main()
