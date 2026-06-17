"""Measure booking-confirmation reliability for a conversational-action bot.

Drives the SAME multi-turn lead-capture conversation N times on distinct
connect_ids and classifies the FINAL turn (all slots provided) as:
  - CONFIRM : bot emits a confirmation (echoes name+phone+datetime+service)
  - REASK   : bot re-asks for a slot the user already provided (the bug)
  - OTHER   : neither (ambiguous)

Evidence-only (rule #0): prints each final answer verbatim + the classification
basis. No assertion is made without reading the raw text. Used to measure
before/after a sysprompt/model fix.

Usage: python scripts/measure_booking_confirm.py [N]
"""
from __future__ import annotations

import asyncio
import re
import sys
import time

import httpx

BASE = "http://localhost:3004/api/ragbot/test"
BOT = "test-spa-id"
CHANNEL = "web"

TURNS = [
    "chào shop ạ",
    "cho mình hỏi dịch vụ chăm sóc da chuyên sâu giá bao nhiêu vậy",
    "da mình hay bị mụn với hơi nhạy cảm, có dịch vụ nào hợp không",
    "ok mình muốn đặt lịch trị mụn",
    "số điện thoại của mình là 0912 345 678",
    "mình tên Lan nhé, đặt giúp mình buổi cuối tuần",
]
PHONE = "0912 345 678"


def _classify(final: str) -> str:
    low = final.lower()
    has_phone = "0912" in final.replace(".", "").replace(" ", "")
    has_name = "lan" in low
    # Re-ask if it requests phone/name/SĐT while the user already gave them.
    asks_phone = bool(re.search(r"(số điện thoại|sđt|liên hệ).{0,20}(\?|ạ|cho em|cung cấp|vui lòng)", low)) \
        or "cho em xin số" in low or "xin số điện thoại" in low
    asks_name = "cho em xin tên" in low or "cung cấp tên" in low or "cho em biết tên" in low
    confirms = ("xác nhận" in low) or (has_phone and has_name and ("cuối tuần" in low or "thời gian" in low))
    if confirms and not (asks_phone or asks_name):
        return "CONFIRM"
    if asks_phone or asks_name:
        return "REASK"
    return "OTHER"


async def _run_once(client: httpx.AsyncClient, idx: int) -> tuple[str, str]:
    connect = f"spa-measure-{int(time.time())}-{idx}"
    final = ""
    atype = ""
    for msg in TURNS:
        tok = (await client.get(f"{BASE}/tokens/self", timeout=10)).json()["token"]
        r = await client.post(
            f"{BASE}/chat",
            json={"bot_id": BOT, "channel_type": CHANNEL, "question": msg,
                  "connect_id": connect, "bypass_cache": True},
            headers={"Authorization": f"Bearer {tok}"},
            timeout=120,
        )
        d = r.json()
        p = d.get("data") if isinstance(d, dict) and "data" in d else d
        final = (p or {}).get("answer", "") or ""
        atype = (p or {}).get("answer_type", "") or ""
    if atype == "blocked":
        return "BLOCKED", final
    return _classify(final), final


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"=== MEASURE booking-confirm · bot={BOT} · N={n} ===\n")
    counts: dict[str, int] = {"CONFIRM": 0, "REASK": 0, "BLOCKED": 0, "OTHER": 0}
    async with httpx.AsyncClient() as client:
        for i in range(1, n + 1):
            verdict, final = await _run_once(client, i)
            counts[verdict] += 1
            print(f"--- RUN {i}: {verdict}")
            print(f"    FINAL: {final[:300]}\n")
    total = sum(counts.values())
    print("=== SUMMARY ===")
    for k in ("CONFIRM", "REASK", "BLOCKED", "OTHER"):
        print(f"  {k}: {counts[k]}/{total}  ({round(100*counts[k]/total)}%)")


if __name__ == "__main__":
    asyncio.run(main())
