"""Multi-turn lead-capture conversation test for the spa bot.

Single-turn Q&A does not reflect real usage — a spa customer chats across
turns: greet → ask price → consult skin concern → agree to book → give phone.
This drives the FULL flow on ONE connect_id (so conversation state + action
slot-capture persist) and prints each turn's answer + answer_type + debug so
the booking/lead-capture behaviour can be judged against the corpus flow.

Evidence-only (rule #0): prints raw bot answers + debug per turn; no verdict
is asserted without reading the actual responses.

Usage: python scripts/test_spa_multiturn_leadcapture.py
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

BASE = "http://localhost:3004/api/ragbot/test"
BOT = "test-spa-id"
CHANNEL = "web"
# One stable connect_id for the whole conversation → server keeps history +
# action/slot state across turns.
CONNECT = f"spa-leadflow-{int(time.time())}"

# Realistic lead-capture + consultation conversation, in order.
TURNS = [
    "chào shop ạ",
    "da mình hay bị mụn, lỗ chân lông to với hơi nhạy cảm, shop tư vấn giúp mình với",
    "trị mụn ở bên mình quy trình thế nào vậy, có mấy bước",
    "cho mình hỏi dịch vụ trị mụn chuyên sâu giá bao nhiêu, có ưu đãi gì không",
    "thế còn dịch vụ chăm sóc da công nghệ cao thì sao, khác gì trị mụn",
    "ok mình muốn đặt lịch trị mụn",
    "số điện thoại của mình là 0912 345 678",
    "mình tên Lan nhé, đặt giúp mình 9h sáng thứ 7 tuần này",
    "spa mình ở địa chỉ nào, có xa trung tâm không",
    "ok cảm ơn shop, hẹn gặp cuối tuần nhé",
]


async def _token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE}/tokens/self", timeout=10)
    r.raise_for_status()
    return r.json()["token"]


async def main() -> None:
    print(f"=== SPA MULTI-TURN lead-capture · connect_id={CONNECT} ===\n")
    async with httpx.AsyncClient() as client:
        for i, msg in enumerate(TURNS, 1):
            token = await _token(client)
            t0 = time.time()
            try:
                r = await client.post(
                    f"{BASE}/chat",
                    json={
                        "bot_id": BOT,
                        "channel_type": CHANNEL,
                        "question": msg,
                        "connect_id": CONNECT,
                        "bypass_cache": True,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=120,
                )
                r.raise_for_status()
                d = r.json()
            except Exception as exc:  # noqa: BLE001 — test harness, record+continue
                print(f"--- TURN {i} ERROR: {type(exc).__name__}: {str(exc)[:120]}\n")
                continue
            p = d.get("data") if isinstance(d, dict) and "data" in d else d
            dbg = (p or {}).get("debug") or {}
            print(f"--- TURN {i}  ({round(time.time()-t0,1)}s) ---")
            print(f"USER : {msg}")
            print(f"BOT  : {(p or {}).get('answer','')[:400]}")
            print(f"  intent={dbg.get('intent')!r}  answer_type={(p or {}).get('answer_type')!r}"
                  f"  chunks={(p or {}).get('chunks_used')}  cache={dbg.get('cache_status')}")
            # Surface any action/slot state the pipeline exposes (booking lead).
            for k in ("action_state", "slots", "service_locked", "conversation_state"):
                if k in dbg or k in (p or {}):
                    print(f"  {k}: {dbg.get(k, (p or {}).get(k))}")
            print()
    print(f"=== DONE — đọc từng turn để verify luồng chốt + capture SĐT 0912345678 ===")


if __name__ == "__main__":
    asyncio.run(main())
