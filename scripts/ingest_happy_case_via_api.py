"""Ingest the happy-case corpus (9 styled files + 3 summary docs) into the DB via the
canonical test API — REAL L1→L7 (parse → chunk → narrate → EMBED → store pgvector).

Per bot: wipe old docs → POST each happy-case file's content → poll until ready. Uses
the dev self-token (loopback). Run AFTER the API is up (python -m ragbot.main).

    set -a && source .env && set +a
    python scripts/ingest_happy_case_via_api.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:3004"
TEST = f"{BASE}/api/ragbot/test"
CLONE = Path(__file__).resolve().parent.parent / "reports" / "happy_case_clone"

# bot (bot_id, channel_type) → its happy-case files (summary first)
BOTS = {
    "spa": ("test-spa-id", "web",
            ["spa-00-summary.md", "spa-1.csv", "spa-2.csv", "spa-3.csv", "spa-4.md"]),
    "xe": ("chinh-sach-xe", "web",
           ["xe-00-summary.md", "xe-1.csv", "xe-2.csv", "xe-3.csv", "xe-4.md"]),
    "legal": ("thong-tu-09-2020-tt-nhnn", "web",
              ["legal-00-summary.md", "thongtu-09-2020.csv"]),
}


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{TEST}/tokens/self")
    r.raise_for_status()
    return r.json()["token"]


async def main() -> None:
    async with httpx.AsyncClient(timeout=120.0) as c:
        token = await _token(c)
        h = {"Authorization": f"Bearer {token}"}
        for label, (bot_id, ch, files) in BOTS.items():
            print(f"\n{'='*70}\nBOT {label}  ({bot_id}:{ch})\n{'='*70}")
            base = f"{TEST}/bots/{bot_id}/{ch}/documents"

            # 1. wipe existing docs
            r = await c.get(base, headers=h)
            old = r.json().get("documents", []) if r.status_code == 200 else []
            for d in old:
                await c.delete(f"{TEST}/documents/{d['id']}", headers=h)
            print(f"  wiped {len(old)} old docs")

            # 2. ingest each happy-case file (content inline)
            posted = 0
            for fn in files:
                p = CLONE / fn
                if not p.exists():
                    print(f"  ⚠️ missing {fn}")
                    continue
                body = {"title": fn, "content": p.read_text(encoding="utf-8")}
                r = await c.post(base, json=body, headers=h)
                if r.status_code in (200, 202):
                    posted += 1
                else:
                    print(f"  ⚠️ {fn} → HTTP {r.status_code}: {r.text[:160]}")
            print(f"  posted {posted}/{len(files)} files → embedding…")

            # 3. poll until all ready (state) — embed runs in the embedded worker
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                await asyncio.sleep(4)
                r = await c.get(base, headers=h)
                docs = r.json().get("documents", []) if r.status_code == 200 else []
                states = [d.get("state") or d.get("status") for d in docs]
                ready = sum(1 for s in states if s in ("ready", "completed", "done", "indexed"))
                chunks = sum(int(d.get("chunks_total") or d.get("chunk_count") or 0) for d in docs)
                if docs and ready >= len(docs):
                    print(f"  ✅ {len(docs)} docs READY · {chunks} chunks")
                    break
                print(f"  … {ready}/{len(docs)} ready, {chunks} chunks (state={set(states)})")
            else:
                print(f"  ⚠️ timeout — states={states}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except httpx.HTTPError as e:
        print(f"HTTP error: {e}")
        sys.exit(1)
