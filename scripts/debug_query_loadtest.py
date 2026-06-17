"""Query-side flow load-test: fire scenario questions at the LIVE pipeline,
log the full answer flow per bot to a file (no scoring — Claude debugs).

Per question captures from the real /chat response: answer, chunks_used,
tokens, cost, latency. Then reads request_steps for the per-step pipeline
timing (retrieve→rrf→rerank→filter→generate→grounding). Writes
reports/debug_traces/QUERY_FLOW_<bot>.md + .json.

Auth = loadtest bypass → self JWT (same as loadtest_90q). Needs the app
running + RAGBOT_LOADTEST_BYPASS_TOKEN in env.

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_query_loadtest.py
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import time

import asyncpg
import httpx

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS_HEADER = "X-Loadtest-Bypass"
_OUT = "reports/debug_traces"


def _bypass() -> dict[str, str]:
    return {_BYPASS_HEADER: os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}


async def _token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}/api/ragbot/test/tokens/self", headers=_bypass())
    r.raise_for_status()
    return r.json()["token"]


async def _ask(client, token, bot, ch, q, connect) -> dict:
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": "",
            "question": q, "connect_id": connect, "bypass_cache": True}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               **_bypass()}
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{BASE_URL}/api/ragbot/test/chat",
                              headers=headers, json=body, timeout=180)
        r.raise_for_status()
        d = r.json()
        d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        return d
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "latency_ms": round((time.perf_counter()-t0)*1000)}


async def _steps_for(conn, bot, rid) -> list[dict]:
    if not rid:
        return []
    rows = await conn.fetch(
        "SELECT step_name, duration_ms, input_tokens, output_tokens, status "
        "FROM request_steps WHERE record_request_id = $1 ORDER BY step_order", rid,
    )
    return [{"step": r["step_name"], "ms": r["duration_ms"],
             "tok": f"{r['input_tokens']}/{r['output_tokens']}" if r["input_tokens"] else "",
             "st": r["status"]} for r in rows]


def _rid_from(resp: dict) -> str | None:
    dbg = resp.get("debug") or {}
    if isinstance(dbg, dict):
        for k in ("request_id", "record_request_id", "trace_id", "request_log_id"):
            if dbg.get(k):
                return str(dbg[k])
    for k in ("request_id", "trace_id"):
        if resp.get(k):
            return str(resp[k])
    return None


async def _run_bot(client, conn, token, sc) -> dict:
    bot, ch = sc["bot_id"], sc["channel_type"]
    print(f"== {bot} ({len(sc['questions'])} Q) ==")
    out_q = []
    for i, q in enumerate(sc["questions"]):
        resp = await _ask(client, token, bot, ch, q["q"], f"debug-{bot}-{q['id']}")
        ans = (resp.get("answer") or "")[:300]
        rid = _rid_from(resp)
        steps = await _steps_for(conn, bot, rid) if rid else []
        # Capture source chunk previews so Claude can judge grounding/correctness.
        _srcs = resp.get("sources") or []
        _src_prev = []
        for s in _srcs[:6]:
            if isinstance(s, dict):
                _src_prev.append({
                    "doc": s.get("document_name"),
                    "score": s.get("score"),
                    "preview": (s.get("preview") or s.get("text") or "")[:160],
                })
        rec = {
            "id": q["id"], "flow": q["flow"], "q": q["q"],
            "expect": q.get("expect"),
            "answer": (resp.get("answer") or "")[:600],
            "answer_type": resp.get("answer_type"),
            "top_score": resp.get("top_score"),
            "chunks_used": resp.get("chunks_used"),
            "tokens": resp.get("tokens"),
            "cost_usd": resp.get("cost_usd"),
            "latency_ms": resp.get("latency_ms"),
            "error": resp.get("error"),
            "n_sources": len(_srcs),
            "sources": _src_prev,
            "steps": steps,
        }
        out_q.append(rec)
        slowest = max(steps, key=lambda s: s["ms"] or 0)["step"] if steps else "?"
        print(f"  {q['id']:<4} lat={rec['latency_ms']}ms chunks={rec['chunks_used']} "
              f"tok={rec['tokens']} slow_step={slowest} "
              f"ans={'ERR' if rec['error'] else ans[:40]!r}")
    return {"bot": bot, "questions": out_q}


async def main() -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    os.makedirs(_OUT, exist_ok=True)
    async with httpx.AsyncClient() as client:
        token = await _token(client)
        for path in sorted(glob.glob("tests/scenarios/*_scenario.json")):
            sc = json.load(open(path))
            res = await _run_bot(client, conn, token, sc)
            with open(f"{_OUT}/QUERY_FLOW_{res['bot']}.json", "w") as fh:
                json.dump(res, fh, ensure_ascii=False, indent=1)
            # human md
            lines = [f"# Query flow — {res['bot']}\n"]
            for r in res["questions"]:
                lines.append(f"\n## {r['id']} [{r['flow']}] — {r['q']}")
                lines.append(f"- lat={r['latency_ms']}ms · chunks_used={r['chunks_used']} "
                             f"· tokens={r['tokens']} · cost=${r['cost_usd']} · sources={r['n_sources']}")
                if r["error"]:
                    lines.append(f"- ERROR: {r['error']}")
                lines.append(f"- answer: {r['answer']}")
                if r["steps"]:
                    top = sorted(r["steps"], key=lambda s: s["ms"] or 0, reverse=True)[:4]
                    lines.append("- slowest steps: " + ", ".join(
                        f"{s['step']}={s['ms']}ms" for s in top))
            with open(f"{_OUT}/QUERY_FLOW_{res['bot']}.md", "w") as fh:
                fh.write("\n".join(lines) + "\n")
            print(f"  → wrote {_OUT}/QUERY_FLOW_{res['bot']}.md")
    await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
