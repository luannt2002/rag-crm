"""Reproducible bot init from HTTPS source URLs (no manual UI).

Uploads each bot's documents via the real /documents API (bypass→self JWT,
same auth as the UI), then polls until the worker finishes chunk+embed.
Optionally wipes existing docs/chunks first for a clean re-init.

This replaces manual UI re-upload with a scriptable, reproducible flow so
the upload→convert(.md)→chunk→embed pipeline can be re-run on demand.

Config: tests/scenarios/bot_sources.json
Auth: RAGBOT_LOADTEST_BYPASS_TOKEN in env. App must be running.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/init_bots_from_urls.py --dry-run
    .venv/bin/python scripts/init_bots_from_urls.py --wipe --apply
    .venv/bin/python scripts/init_bots_from_urls.py --apply --only test-spa-id
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}
_CFG = "tests/scenarios/bot_sources.json"


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _wipe(conn: asyncpg.Connection, bot_id: str) -> None:
    ids = [r["id"] for r in await conn.fetch(
        "SELECT id FROM bots WHERE bot_id = $1", bot_id)]
    if not ids:
        return
    await conn.execute("DELETE FROM document_chunks WHERE record_bot_id = ANY($1)", ids)
    await conn.execute("DELETE FROM documents WHERE record_bot_id = ANY($1)", ids)
    print(f"  wiped docs+chunks for {bot_id}")


async def _upload(c: httpx.AsyncClient, tok: str, bot: str, ch: str, doc: dict) -> dict:
    body = {"title": doc["title"], "url": doc["url"]}
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **_BYPASS}
    r = await c.post(f"{BASE}/api/ragbot/test/bots/{bot}/{ch}/documents",
                     headers=headers, json=body, timeout=120)
    return {"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text[:200]}


async def _chunk_count(conn: asyncpg.Connection, bot_id: str) -> int:
    return await conn.fetchval(
        """SELECT count(dc.*) FROM document_chunks dc
           JOIN documents d ON d.id = dc.record_document_id
           JOIN bots b ON b.id = d.record_bot_id WHERE b.bot_id = $1""", bot_id) or 0


async def main(wipe: bool, apply: bool, only: str | None) -> int:
    cfg = json.load(open(_CFG))
    bots = [b for b in cfg["bots"] if not only or b["bot_id"] == only]
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        async with httpx.AsyncClient() as c:
            tok = await _token(c)
            for b in bots:
                bot, ch = b["bot_id"], b["channel_type"]
                print(f"== {bot} ({len(b['documents'])} docs) ==")
                if not apply:
                    for d in b["documents"]:
                        print(f"  [dry] would upload {d['title']}: {d['url'][:60]}")
                    continue
                if wipe:
                    await _wipe(conn, bot)
                for d in b["documents"]:
                    res = await _upload(c, tok, bot, ch, d)
                    ok = res["status"] in (200, 202)
                    print(f"  upload {d['title']}: {res['status']} {'OK' if ok else res['body']}")
            if apply:
                # Poll until chunk counts stabilize (worker finishes embed).
                print("== waiting for worker (chunk+embed) ==")
                prev = {}
                for _ in range(40):
                    await asyncio.sleep(8)
                    cur = {b["bot_id"]: await _chunk_count(conn, b["bot_id"]) for b in bots}
                    print("  " + " | ".join(f"{k}={v}" for k, v in cur.items()))
                    if cur == prev and all(v > 0 for v in cur.values()):
                        print("  stabilized.")
                        break
                    prev = cur
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="delete existing docs+chunks first")
    ap.add_argument("--apply", action="store_true", help="actually upload (else dry-run)")
    ap.add_argument("--only", default=None, help="single bot_id")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.wipe, a.apply, a.only)))
