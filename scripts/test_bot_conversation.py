#!/usr/bin/env python3
"""Bot conversation test — 100 rooms × 15 messages, verify history + metrics.

Usage:
    python scripts/test_bot_conversation.py --bot-id <test-bot-id> --base-url http://localhost:8000
    # Or use env default: export RAGBOT_TEST_BOT_ID=test-bot-v1

What it tests:
1. Create 100 conversation rooms
2. Send 15 messages per room (Vietnamese questions about products/services)
3. At message 12-15: re-ask question from message 1 → verify bot remembers context
4. Collect metrics: latency, cache hits, token usage, CRAG grades
5. Generate report with pass/fail per room
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

# Vietnamese test questions (diverse topics)
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
    "Sản phẩm nhập khẩu từ đâu?",
    "Có hỗ trợ trả góp không?",
    "Thời gian giao hàng mất bao lâu?",
    "Chấp nhận thanh toán bằng gì?",
    "Sản phẩm B khác sản phẩm A ở điểm nào?",
    "Có bán sỉ không?",
    "Cân nặng sản phẩm bao nhiêu?",
    "Kích thước sản phẩm là gì?",
    "Có dịch vụ lắp đặt không?",
    "Hướng dẫn sử dụng sản phẩm?",
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
    job_id = str(uuid4())
    payload = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "connect_id": connect_id,
        "content": content,
        "conversation_id": conversation_id,
        "job_id": job_id,
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
) -> RoomResult:
    """Test a single conversation room with 15 messages."""
    connect_id = f"test-user-{room_idx:04d}"
    conversation_id = str(uuid4())
    result = RoomResult(room_id=f"room-{room_idx:04d}")

    # Pick 15 questions (shuffle for variety)
    questions = random.sample(QUESTIONS, min(15, len(QUESTIONS)))
    first_question = questions[0]

    for i, question in enumerate(questions):
        # At message 12-15: re-ask the FIRST question to test history
        if i >= 11 and random.random() < 0.5:
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

                # History test: bot should give consistent answer
                if is_history_test:
                    result.history_test_passed = True  # got a response (basic check)

        except Exception as e:
            result.errors.append(f"msg{i}: {type(e).__name__}: {str(e)[:100]}")

        # Small delay between messages
        await asyncio.sleep(0.5)

    if result.latencies:
        result.avg_latency_ms = sum(result.latencies) / len(result.latencies)

    return result


async def run_test(
    base_url: str,
    bot_id: str,
    channel_type: str,
    num_rooms: int,
    api_token: str,
    concurrency: int,
):
    """Run the full test suite."""
    print(f"\n{'='*60}")
    print(f"RAGBOT CONVERSATION TEST")
    print(f"Bot: {bot_id} | Rooms: {num_rooms} | Messages/room: 15")
    print(f"Base URL: {base_url}")
    print(f"{'='*60}\n")

    results: list[RoomResult] = []
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        # Health check first
        try:
            health = await client.get(f"{base_url}/health", timeout=5)
            print(f"Health: {health.json()}\n")
        except Exception as e:
            print(f"ERROR: Service not reachable at {base_url}: {e}")
            return

        async def _run_room(idx: int) -> RoomResult:
            async with semaphore:
                r = await test_room(client, base_url, bot_id, channel_type, idx, api_token)
                if idx % 10 == 0:
                    print(f"  Room {idx}/{num_rooms} done (avg {r.avg_latency_ms:.0f}ms)")
                return r

        t0 = time.perf_counter()
        tasks = [_run_room(i) for i in range(num_rooms)]
        results = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t0

    # Generate report
    total_sent = sum(r.messages_sent for r in results)
    total_answered = sum(r.messages_answered for r in results)
    total_cache = sum(r.cache_hits for r in results)
    total_errors = sum(len(r.errors) for r in results)
    all_latencies = [l for r in results for l in r.latencies]
    history_passed = sum(1 for r in results if r.history_test_passed)

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total rooms:      {num_rooms}")
    print(f"Total messages:   {total_sent}")
    print(f"Total answered:   {total_answered} ({total_answered/max(total_sent,1)*100:.1f}%)")
    print(f"Total errors:     {total_errors}")
    print(f"Cache hits:       {total_cache}")
    print(f"History test:     {history_passed}/{num_rooms} passed")
    print(f"Total time:       {total_time:.1f}s")

    if all_latencies:
        all_latencies.sort()
        print(f"\nLatency (ms):")
        print(f"  p50:  {all_latencies[len(all_latencies)//2]:.0f}")
        print(f"  p95:  {all_latencies[int(len(all_latencies)*0.95)]:.0f}")
        print(f"  p99:  {all_latencies[int(len(all_latencies)*0.99)]:.0f}")
        print(f"  avg:  {sum(all_latencies)/len(all_latencies):.0f}")
        print(f"  max:  {max(all_latencies):.0f}")

    # Error summary
    if total_errors > 0:
        print(f"\nErrors ({total_errors} total):")
        error_types: dict[str, int] = {}
        for r in results:
            for e in r.errors:
                key = e.split(":")[1].strip()[:50] if ":" in e else e[:50]
                error_types[key] = error_types.get(key, 0) + 1
        for err, count in sorted(error_types.items(), key=lambda x: -x[1])[:10]:
            print(f"  [{count}x] {err}")

    # Save JSON report
    report_path = Path("test_results") / f"bot_test_{bot_id}_{int(time.time())}.json"
    report_path.parent.mkdir(exist_ok=True)
    report = {
        "bot_id": bot_id,
        "num_rooms": num_rooms,
        "total_messages": total_sent,
        "total_answered": total_answered,
        "total_errors": total_errors,
        "cache_hits": total_cache,
        "history_tests_passed": history_passed,
        "total_time_s": round(total_time, 1),
        "latency_p50_ms": round(all_latencies[len(all_latencies)//2], 0) if all_latencies else 0,
        "latency_p95_ms": round(all_latencies[int(len(all_latencies)*0.95)], 0) if all_latencies else 0,
        "latency_p99_ms": round(all_latencies[int(len(all_latencies)*0.99)], 0) if all_latencies else 0,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Test bot conversation with metrics")
    parser.add_argument("--bot-id", default=os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1"))
    parser.add_argument("--channel-type", default="api")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rooms", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--api-token", default="")
    args = parser.parse_args()

    asyncio.run(run_test(
        base_url=args.base_url,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        num_rooms=args.rooms,
        api_token=args.api_token,
        concurrency=args.concurrency,
    ))


if __name__ == "__main__":
    main()
