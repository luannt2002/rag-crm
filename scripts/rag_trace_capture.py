"""RAG trace capture for Claude-grading (no external LLM judge).

Runs every scenario question against the live /chat with debug=full and dumps a
RICH per-question record — intent, rewritten query, top_k, score_max,
chunks_graded, EVERY retrieved chunk (content+score), and the full answer — to a
JSON the Claude agent then reads and grades SEMANTICALLY (correct / wrong /
refuse / hallu by MEANING, not substring). Replaces the substring/RAGAS-API
grader whose false-fails were shown in RAG_DEEP_TEST_chinh-sach-xe_20260703.md.

Repeated-run mode (spec 001-rag-truth-audit, contracts/harness-cli.md):
    --repeat N  runs every question N times (unique connect_id per iteration),
    asserts per-run cache_status == "bypassed" (exit 2 on contamination),
    stamps the corpus version at batch start AND end (exit 3 on drift), and
    classifies every significant number in each answer against the served
    chunks + the stats index (grounded / derived_valid / unsupported).

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/rag_trace_capture.py \
        --scenario tests/scenarios/chinh-sach-xe-qa20_scenario.json \
        --out reports/rag_trace_chinh-sach-xe.json [--repeat 15]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}

EXIT_CACHE_CONTAMINATED = 2
EXIT_CORPUS_DRIFT = 3


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_FALLBACK_SLEEP_S = 2.0


def _rate_limited(resp: dict) -> float | None:
    """Return the retry delay when *resp* is a rate-limit error, else None."""
    err = resp.get("error")
    if isinstance(err, dict) and err.get("code") == "RATE_LIMITED":
        try:
            return float(err.get("retry_after_s") or RATE_LIMIT_FALLBACK_SLEEP_S)
        except (TypeError, ValueError):
            return RATE_LIMIT_FALLBACK_SLEEP_S
    return None


async def _with_rate_limit_retry(ask_once, *, max_retries: int = RATE_LIMIT_MAX_RETRIES,
                                 sleep=asyncio.sleep) -> dict:
    """Re-drive *ask_once* while the platform answers RATE_LIMITED (P-09 lesson:
    9/15 baseline runs lost to 429s the harness never retried — a measurement
    error, not a bot error). Exponential-ish: retry_after_s × attempt."""
    resp = await ask_once()
    for attempt in range(1, max_retries + 1):
        delay = _rate_limited(resp)
        if delay is None:
            return resp
        await sleep(delay * attempt)
        resp = await ask_once()
    return resp


async def _ask(c, tok, bot, ch, ws, q, cid) -> dict:
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": cid, "bypass_cache": True, "debug": "full"}

    async def _once() -> dict:
        try:
            r = await c.post(f"{BASE}/api/ragbot/test/chat",
                             headers={"Authorization": f"Bearer {tok}", **_BYPASS},
                             json=body, timeout=180)
            return r.json()
        except Exception as exc:  # noqa: BLE001 — capture harness: record the error, never crash the run
            return {"error": str(exc)}

    return await _with_rate_limit_retry(_once)


def _record(q: dict, resp: dict) -> dict:
    dbg = resp.get("debug") or {}
    chunks = resp.get("retrieved_chunks_content") or []
    # Keep full chunk text (capped) + score so the grader sees exactly what the
    # LLM saw and can judge whether the answer WAS supported by retrieval.
    trimmed = [
        {
            "score": ch.get("score") or ch.get("rerank_score") or ch.get("rrf_score"),
            "content": (ch.get("content") or ch.get("text") or "")[:500],
        }
        for ch in chunks[:12]
    ]
    return {
        "id": q["id"],
        "flow": q.get("flow", ""),
        "question": q["q"],
        "expect": q.get("expect"),
        "intent": dbg.get("intent"),
        "rewritten": dbg.get("rewritten_query") or dbg.get("condensed_query"),
        "retrieve_mode": dbg.get("retrieve_mode"),
        # UNAMBIGUOUS field names — the old "top_k" was misread as "chunks to
        # the LLM" when it is actually the RETRIEVAL candidate width.
        "retrieve_candidates_topk": dbg.get("top_k", len(chunks)),  # wide net pulled from DB
        "chunks_to_llm": len(chunks),                                # what ACTUALLY reached the answer LLM
        "chunks_graded_pass": dbg.get("chunks_graded"),              # survived CRAG grading
        "score_max": dbg.get("score_max"),
        "cache_status": dbg.get("cache_status"),
        "numeric_fidelity": dbg.get("numeric_fidelity"),
        "answer": resp.get("answer") or "",
        "answer_type": resp.get("answer_type"),
        "chunks": trimmed,
        "error": resp.get("error"),
    }


# ---------------------------------------------------------------------------
# Repeated-run support (contracts/harness-cli.md)
# ---------------------------------------------------------------------------

def _asyncpg_dsn() -> str:
    dsn = os.environ["DATABASE_URL"]
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def _corpus_version(bot_slug: str, channel_type: str) -> dict:
    """DB-derivable, restart-proof corpus identity (research.md D4)."""
    import asyncpg  # local: keep the single-pass path dependency-free

    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id FROM bots WHERE bot_id=$1 AND channel_type=$2 AND is_deleted=false",
            bot_slug, channel_type)
        if row is None:
            raise SystemExit(f"bot not found: {bot_slug}/{channel_type}")
        bid = row["id"]
        v = await conn.fetchrow(
            """SELECT count(c.id) AS n_chunks,
                      max(d.updated_at)::text AS max_updated_at,
                      md5(coalesce(string_agg(c.content_hash, '' ORDER BY c.id), '')) AS content_md5
               FROM document_chunks c
               JOIN documents d ON d.id = c.record_document_id
               WHERE c.record_bot_id=$1 AND d.deleted_at IS NULL""", bid)
        stats = await conn.fetch(
            """SELECT entity_name, price_primary, price_secondary, attributes_json
               FROM document_service_index WHERE record_bot_id=$1""", bid)
        values: set[int] = set()
        for r in stats:
            for p in (r["price_primary"], r["price_secondary"]):
                if p is not None:
                    values.add(int(float(p)))
            aj = r["attributes_json"]
            if isinstance(aj, str):
                try:
                    aj = json.loads(aj)
                except ValueError:
                    aj = {}
            if isinstance(aj, dict):
                for vv in aj.values():
                    s = str(vv).strip().replace(".", "").replace(",", "")
                    if s.isdigit():
                        values.add(int(s))
        return {"record_bot_id": str(bid),
                "chunks": v["n_chunks"], "max_updated_at": v["max_updated_at"],
                "content_md5": v["content_md5"], "_stats_values": values}
    finally:
        await conn.close()


def classify_numbers(answer: str, chunk_texts: list[str], stats_values: set[int]) -> list[dict]:
    """Pure verdict classifier (contract §3-4): grounded → derived_valid → unsupported.

    grounded: literal substring of served context OR parsed-value equality with a
    stats value. derived_valid: |a−b| or a+b of two grounded values (research D2).
    """
    from ragbot.shared.number_format import parse_money_vn  # SSoT parsing (research D1)
    import re

    # Significant number tokens: 1.242.000 / 1242000 / 1,242,000 — ≥5 digits total
    # (sizes like 205/55R16 stay below the bar per-token).
    tok_re = re.compile(r"\d[\d.,]*\d|\d")
    joined = "\n".join(chunk_texts)
    out: list[dict] = []
    seen: set[str] = set()
    grounded_vals: list[int] = []
    pending: list[tuple[str, int | None]] = []
    for m in tok_re.finditer(answer):
        tok = m.group(0)
        if sum(c.isdigit() for c in tok) < 5 or tok in seen:
            continue
        seen.add(tok)
        val = parse_money_vn(tok)
        if val is None:
            digits = tok.replace(".", "").replace(",", "")
            val = int(digits) if digits.isdigit() else None
        if tok in joined or (val is not None and val in stats_values):
            out.append({"token": tok, "value": val, "class": "grounded"})
            if val is not None:
                grounded_vals.append(val)
        else:
            pending.append((tok, val))
    for tok, val in pending:
        derived = val is not None and any(
            val == abs(a - b) or val == a + b
            for i, a in enumerate(grounded_vals) for b in grounded_vals[i:])
        out.append({"token": tok, "value": val,
                    "class": "derived_valid" if derived else "unsupported"})
    return out


async def main(scenario: str, out: str, concurrency: int, repeat: int) -> None:
    sc = json.load(open(scenario))
    bot, ch, ws = sc["bot_id"], sc["channel_type"], sc.get("workspace_id", "")
    sem = asyncio.Semaphore(concurrency)

    cv_start = await _corpus_version(bot, ch) if repeat > 1 else None
    stats_values = cv_start.pop("_stats_values") if cv_start else set()

    async with httpx.AsyncClient() as c:
        tok = await _token(c)

        async def _one(q, it: int):
            async with sem:
                cid = f"trace-{q['id']}" if repeat == 1 else f"trace-{q['id']}-r{it:02d}"
                resp = await _ask(c, tok, bot, ch, ws, q["q"], cid)
                rec = _record(q, resp)
                rec["iteration"] = it
                return rec

        records = await asyncio.gather(
            *[_one(q, it) for q in sc["questions"] for it in range(1, repeat + 1)])

    if repeat > 1:
        # Contract §1 — every run must have bypassed the cache, else the batch
        # is not N independent samples. Abort WITHOUT writing partial output.
        bad = [r for r in records if r.get("cache_status") != "bypassed" and not r.get("error")]
        if bad:
            print(f"CACHE CONTAMINATION: {len(bad)} run(s) with cache_status != bypassed "
                  f"(e.g. {bad[0]['id']} r{bad[0]['iteration']} = {bad[0]['cache_status']!r})",
                  file=sys.stderr)
            raise SystemExit(EXIT_CACHE_CONTAMINATED)
        # Contract §2 — corpus must not drift mid-batch.
        cv_end = await _corpus_version(bot, ch)
        cv_end.pop("_stats_values", None)
        if cv_end != cv_start:
            print(f"CORPUS DRIFT: start={cv_start} end={cv_end}", file=sys.stderr)
            raise SystemExit(EXIT_CORPUS_DRIFT)
        for r in records:
            r["verdicts"] = classify_numbers(
                r["answer"], [c["content"] for c in r["chunks"]], stats_values)

    payload = {"bot_id": bot, "scenario": scenario, "repeat": repeat,
               "corpus_version": cv_start, "n": len(records),
               "records": sorted(records, key=lambda r: (r["id"], r.get("iteration", 0)))}
    with open(out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"captured {len(records)} → {out}")

    if repeat > 1:
        # Per-probe aggregate: fabrication = any unsupported number in the answer.
        print(f"  {'id':<6}{'runs':>5}{'unsupported-runs':>17}{'rate':>7}  distinct unsupported tokens")
        by_id: dict[str, list[dict]] = {}
        for r in records:
            by_id.setdefault(r["id"], []).append(r)
        for qid, rs in sorted(by_id.items()):
            fab = [r for r in rs if any(v["class"] == "unsupported" for v in r.get("verdicts", []))]
            toks = sorted({v["token"] for r in fab for v in r["verdicts"] if v["class"] == "unsupported"})
            print(f"  {qid:<6}{len(rs):>5}{len(fab):>17}{len(fab)/len(rs):>7.0%}  {toks[:8]}")
    else:
        print(f"  {'id':<6}{'intent':<12}{'candidates':>11}{'→to_LLM':>9}{'graded':>7}  answer")
        for r in payload["records"]:
            print(f"  {r['id']:<6}{str(r['intent']):<12}"
                  f"{str(r['retrieve_candidates_topk']):>11}{str(r['chunks_to_llm']):>9}"
                  f"{str(r['chunks_graded_pass']):>7}  {r['answer'][:50]!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.scenario, a.out, a.concurrency, a.repeat))
