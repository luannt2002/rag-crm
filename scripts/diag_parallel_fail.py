#!/usr/bin/env python3
"""Reproduce 5 parallel upload fails — log EXACT exception."""
import asyncio, json, time, urllib.request, urllib.error, os, traceback
from pathlib import Path

ENV = Path("/var/www/html/ragbot/.env")
for line in ENV.read_text().splitlines():
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

BASE = "http://localhost:3004/api/ragbot"
with urllib.request.urlopen(f"{BASE}/test/tokens/self", timeout=10) as r:
    TOKEN = json.loads(r.read())["token"]


def call_sync(bot_id):
    """Minimal call — just hit endpoint, log full exception."""
    t0 = time.time()
    body = {
        "tenant_id": 32, "bot_id": bot_id, "channel_type": "web",
        "documents": [{"title": "diag-test", "content": "Diagnostic " * 50,
                       "url": f"local://{bot_id}/diag", "source_type": "manual"}],
        "wipe_existing": False,
    }
    req = urllib.request.Request(
        f"{BASE}/sync/documents",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read())
        return {"ok": res.get("ok"), "elapsed": time.time() - t0, "bot": bot_id}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        return {"ok": False, "elapsed": time.time() - t0, "bot": bot_id,
                "http_code": e.code, "body": body, "exc_type": "HTTPError"}
    except Exception as e:
        return {"ok": False, "elapsed": time.time() - t0, "bot": bot_id,
                "exc_type": type(e).__name__, "exc": str(e)[:400],
                "trace": traceback.format_exc()[:600]}


async def main():
    BOTS = ["toan-hoc-12", "vat-ly-11", "hoa-hoc-10", "sinh-hoc-12", "lich-su-vn",
            "dia-ly-vn", "y-te-co-ban", "luat-giao-thong", "kinh-te-vi-mo", "tin-hoc-co-ban"]
    sem = asyncio.Semaphore(5)
    loop = asyncio.get_event_loop()

    async def _one(bot):
        async with sem:
            return await loop.run_in_executor(None, call_sync, bot)

    print(f"Launching {len(BOTS)} parallel calls (sem=5)", flush=True)
    t0 = time.time()
    results = await asyncio.gather(*[_one(b) for b in BOTS], return_exceptions=True)
    print(f"\nTotal: {time.time()-t0:.1f}s", flush=True)
    print("\n=== RESULTS ===")
    for r in results:
        if isinstance(r, dict):
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"ASYNC EXCEPTION: {type(r).__name__}: {r}")

asyncio.run(main())
