"""Deepdive verify harness — per-question ground truth with chunk evidence.

Why this exists (2026-06-04 finding): the verdict-level load test was masking
two failure classes that only a chunk-level deepdive exposes:
  1. **Cache contamination** — answers served from semantic cache (cache_status
     = "hit") do NOT reflect the current retrieval code. This harness forces
     ``bypass_cache=true`` so every answer is freshly retrieved.
  2. **Parametric leak** — the bot answers correctly from the LLM's own world
     knowledge while citing an IRRELEVANT chunk. A pass/answer-correct verdict
     hides this; comparing the cited chunk vs the corpus ground-truth chunk
     exposes it.

For every question it captures: question / bot answer / expected literal /
cited chunks (id+quote+score) / chunks_used / top_score / cache_status, and for
every non-pass it cross-references the corpus (DB) for the chunk that actually
contains the expected literal — the "correct chunk" — and flags the failure
class (retrieval_miss / parametric_leak / wrong_chunk / oos_leak / test_literal).

Output:
  - per-question deepdive block (stdout + /tmp/deepdive_<ts>.log)
  - /tmp/deepdive_<ts>.jsonl  — one JSON line per question
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time

import httpx

from tests.integration.test_all_bots_load_120q import (
    BASE,
    QUESTIONS,
)

# Sync DSN for the corpus ground-truth lookup (psql), stripped of the driver tag.
_PG_DSN = subprocess.run(
    ["bash", "-lc",
     "grep '^DATABASE_URL_SYNC=' /var/www/html/ragbot/.env | cut -d= -f2- | sed 's/+psycopg2//'"],
    capture_output=True, text=True,
).stdout.strip()

_BOT_PK: dict[str, str] = {}  # bot_id -> record_bot_id (cached)


def _bot_pk(bot_id: str) -> str:
    if bot_id in _BOT_PK:
        return _BOT_PK[bot_id]
    out = subprocess.run(
        ["psql", _PG_DSN, "-tA", "-c",
         f"SELECT id FROM bots WHERE bot_id='{bot_id}' LIMIT 1;"],
        capture_output=True, text=True, env={"PGCONNECT_TIMEOUT": "10", "PATH": "/usr/bin:/bin"},
    ).stdout.strip()
    _BOT_PK[bot_id] = out
    return out


def _corpus_correct_chunk(bot_id: str, literal: str) -> str:
    """Return the corpus chunk that contains the expected literal (else "")."""
    pk = _bot_pk(bot_id)
    if not pk or not literal:
        return ""
    safe = literal.replace("'", "''")
    out = subprocess.run(
        ["psql", _PG_DSN, "-tA", "-c",
         f"SELECT left(content,180) FROM document_chunks "
         f"WHERE record_bot_id='{pk}' AND content ILIKE '%{safe}%' LIMIT 1;"],
        capture_output=True, text=True, env={"PGCONNECT_TIMEOUT": "10", "PATH": "/usr/bin:/bin"},
    ).stdout.strip()
    return out


async def _fresh_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE}/tokens/self", timeout=10)
    r.raise_for_status()
    return r.json()["token"]


def _classify(q, answer: str, citations: list, chunks_used: int,
              missing: list, correct_chunk_exists: bool) -> str:
    """Failure-class flag from chunk evidence (not just the literal verdict)."""
    cited_has_literal = any(
        (not missing) or all(k.lower() in (c.get("quote", "") or "").lower() for k in (q.must_contain or []))
        for c in citations
    )
    if q.is_oos:
        return "oos_refuse_ok" if (not answer or "không" in answer.lower() or chunks_used == 0) else "oos_leak"
    if not missing:
        # answer carries the literal — but is it grounded?
        if chunks_used == 0:
            return "parametric_leak"  # correct answer, zero context
        return "grounded_ok" if cited_has_literal else "answer_ok_chunk_weak"
    # literal missing from answer
    if correct_chunk_exists and chunks_used == 0:
        return "retrieval_miss"
    if correct_chunk_exists and not cited_has_literal:
        return "wrong_chunk_retrieved"
    if not correct_chunk_exists:
        return "corpus_gap_or_test_literal"
    return "partial"


async def ask_deep(client: httpx.AsyncClient, q, sem: asyncio.Semaphore) -> dict:
    async with sem:
        token = await _fresh_token(client)
        body = {
            "bot_id": q.bot_id,
            "channel_type": q.channel_type,
            "question": q.text,
            "connect_id": f"deepdive-{q.qid}",
            "bypass_cache": True,
        }
        t = time.time()
        try:
            r = await client.post(
                f"{BASE}/chat", json=body,
                headers={"Authorization": f"Bearer {token}"}, timeout=120,
            )
            r.raise_for_status()
            d = r.json()
        except Exception as exc:  # noqa: BLE001 — harness top-level, record + continue
            return {"qid": q.qid, "bot_id": q.bot_id, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}
        lat = round(time.time() - t, 2)

    p = d.get("data") if isinstance(d, dict) and "data" in d else d
    answer = (p or {}).get("answer", "") or ""
    citations = (p or {}).get("citations") or []
    chunks_used = (p or {}).get("chunks_used", 0) or 0
    top_score = float((p or {}).get("top_score", 0.0) or 0.0)
    cache_status = ((p or {}).get("debug") or {}).get("cache_status", "?")
    ans_low = answer.lower()
    missing = [k for k in q.must_contain if k.lower() not in ans_low]

    correct_chunk = "" if not missing and not q.is_oos else _corpus_correct_chunk(q.bot_id, q.must_contain[0] if q.must_contain else "")
    flag = _classify(q, answer, citations, chunks_used, missing,
                     bool(correct_chunk) or (not missing))

    return {
        "qid": q.qid, "bot_id": q.bot_id, "is_oos": q.is_oos,
        "question": q.text, "must_contain": list(q.must_contain),
        "answer": answer[:240], "missing": missing,
        "chunks_used": chunks_used, "top_score": round(top_score, 4),
        "cache_status": cache_status, "latency_s": lat,
        "citations": [
            {"score": round(float(c.get("score", 0) or 0), 4),
             "chunk_id": c.get("chunk_id", ""),
             "doc": (c.get("document_name") or "")[:40],
             "quote": (c.get("quote") or "")[:140]}
            for c in citations[:3]
        ],
        "correct_chunk_db": correct_chunk[:180],
        "flag": flag,
    }


def _print_block(r: dict) -> None:
    if r.get("error"):
        print(f"  ✖ [{r['qid']}] {r['bot_id']} ERROR: {r['error']}")
        return
    bad = r["flag"] not in ("grounded_ok", "oos_refuse_ok")
    mark = "🔴" if bad else "✅"
    print(f"\n{mark} [{r['qid']}] {r['bot_id']}  flag={r['flag']}  cache={r['cache_status']}  chunks={r['chunks_used']}  top={r['top_score']}")
    print(f"   Q        : {r['question']}")
    print(f"   ĐÁP ĐÚNG : {r['must_contain']}  (missing={r['missing']})")
    print(f"   BOT TRẢ  : {r['answer'][:160]}")
    if r["citations"]:
        for c in r["citations"]:
            print(f"   CHUNK DÙNG: score={c['score']} doc={c['doc']}")
            print(f"        quote: {c['quote']}")
    else:
        print(f"   CHUNK DÙNG: (none)")
    if r["correct_chunk_db"]:
        print(f"   CHUNK ĐÚNG(DB): {r['correct_chunk_db']}")


async def main() -> None:
    ts = int(time.time())
    jsonl = f"/tmp/deepdive_{ts}.jsonl"
    # Optional bot filter: BOT_FILTER="bot-a,bot-b" → only those bots.
    import os as _os
    _filter = [s for s in (_os.getenv("BOT_FILTER", "").split(",")) if s]
    _questions = [q for q in QUESTIONS if not _filter or q.bot_id in _filter]
    print(f"=== DEEPDIVE VERIFY: {len(_questions)} câu, bypass_cache=ON, chunk evidence + DB ground-truth ===")
    print(f"=== filter={_filter or 'ALL'} · jsonl={jsonl} ===")
    sem = asyncio.Semaphore(1)
    flags: dict[str, int] = {}
    async with httpx.AsyncClient() as client:
        await _fresh_token(client)
        for q in _questions:
            r = await ask_deep(client, q, sem)
            _print_block(r)
            flags[r.get("flag", "error")] = flags.get(r.get("flag", "error"), 0) + 1
            with open(jsonl, "a") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n=== FLAG SUMMARY ===")
    for k, v in sorted(flags.items(), key=lambda x: -x[1]):
        print(f"  {k:28s}: {v}")
    print(f"💾 {jsonl}")


if __name__ == "__main__":
    asyncio.run(main())
