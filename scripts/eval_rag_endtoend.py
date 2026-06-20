#!/usr/bin/env python3
"""End-to-end RAG eval with LAYER-SPLIT diagnosis (the decisive scorecard).

Runs every scenario question against the LIVE pipeline and, for each one,
joins back the actually-retrieved chunks via ``request_chunk_refs`` so we can
separate the two failure modes the project keeps conflating:

  * RETRIEVAL_MISS — the answer-bearing chunk never reached the LLM
    (``expect`` substring absent from EVERY retrieved chunk).
  * LLM_MISS       — retrieval DID surface the answer chunk, but the answer
    omitted / refused it (``expect`` in a retrieved chunk, not in the answer).

This is the root-cause discipline CLAUDE.md mandates: a coverage failure is
useless to "fix" until you know whether the retrieval layer or the LLM layer
broke. The paper's Table-5 analogues fall out of the same run:

  * COVERAGE       (≈ Answer Correctness)     = answer contains ``expect``
  * CHUNK_RECALL   (≈ Retrieval Completeness) = a retrieved chunk contains ``expect``
  * HALLU rate     (sacred = 0)               = a ``*_trap`` flow got answered

Deterministic scoring — NO LLM judge (project rule). Live run: needs the
server up + DB reachable. Read-only on the DB side (SELECT request_chunk_refs).

Usage::

    set -a && source .env && set +a
    .venv/bin/python scripts/eval_rag_endtoend.py \\
        --output-json reports/rag_endtoend_20260620.json \\
        --output-md   reports/rag_endtoend_20260620.md \\
        --raw-jsonl   reports/rag_endtoend_raw_20260620.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}

# --- refusal heuristic (broadened from eval_gate.py after 2026-06-20 false
# positives: polite OOS redirects like "em chỉ hỗ trợ về lốp", "chưa hỗ trợ
# được việc đó" were mis-scored as HALLU breaches). Domain-neutral redirect /
# scope-limit phrases — a bot deflecting an out-of-scope ask is a REFUSAL,
# never a hallucination. ---
_REFUSAL_MARKERS = (
    "vui lòng liên hệ", "liên hệ hotline", "liên hệ trực tiếp",
    "tham khảo văn bản", "cơ quan có thẩm quyền",
    "chưa có thông tin", "không nằm trong", "chưa có dữ liệu",
    "chỉ hỗ trợ", "chỉ tư vấn", "chỉ chuyên", "chưa hỗ trợ",
    "không hỗ trợ", "ngoài phạm vi", "không thuộc phạm vi",
    "chưa hỗ trợ được", "nên chưa", "chỉ cung cấp", "chỉ phụ trách",
)
_DENIAL_RE = re.compile(
    r"(không|chưa)\s+"
    r"(có|thấy|tìm thấy|quy định|đề cập|bao gồm|thuộc|tồn tại|cung cấp|bán|nằm trong|"
    r"đề\s*cập|được\s+(quy định|đề cập|trích dẫn))"
)


def _is_refusal(ans: str) -> bool:
    a = (ans or "").lower()
    return bool(_DENIAL_RE.search(a)) or any(m in a for m in _REFUSAL_MARKERS)


def _norm_num(s: str) -> str:
    return re.sub(r"(?<=\d)[.,\s](?=\d)", "", (s or "").lower())


def _contains(expect: str, hay: str) -> bool:
    """Number-format-agnostic substring match (mirrors eval_gate)."""
    if not expect or not hay:
        return False
    return expect.lower() in hay.lower() or _norm_num(expect) in _norm_num(hay)


@dataclass
class QResult:
    bot_id: str
    qid: str
    flow: str
    question: str
    expect: str | None
    answer: str
    answer_type: str | None
    request_id: str | None
    chunks_used: int
    top_score: float | None
    latency_ms: int
    cost_usd: float
    is_trap: bool
    refused: bool
    answer_hit: bool = False
    chunk_hit: bool | None = None  # None = refs unavailable
    retrieved_chars: int = 0
    verdict: str = ""


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(c: httpx.AsyncClient, tok: str, bot: str, ch: str, q: str,
               connect: str, ws: str) -> dict:
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": connect, "bypass_cache": True}
    t0 = time.perf_counter()
    try:
        r = await c.post(
            f"{BASE}/api/ragbot/test/chat",
            headers={"Authorization": f"Bearer {tok}", **_BYPASS},
            json=body, timeout=240,
        )
        d = r.json()
        d["_lat"] = round((time.perf_counter() - t0) * 1000)
        return d
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"error": str(exc), "_lat": round((time.perf_counter() - t0) * 1000)}


async def _retrieved_chunks(dsn: str, request_ids: list[str]) -> dict[str, str]:
    """request_id -> concatenated content of its retrieved chunks (for chunk_hit).

    Best-effort: refs are written by the pipeline; a request with no refs row
    (logging disabled / async lag) maps to ``''`` and yields chunk_hit=None.
    """
    if not request_ids:
        return {}
    eng = create_async_engine(dsn)
    try:
        conn = await (await eng.connect()).execution_options(isolation_level="AUTOCOMMIT")
        rows = list(await conn.execute(
            sql_text(
                """
                SELECT rcr.record_request_id::text AS rid,
                       string_agg(dc.content, ' ¦ ' ORDER BY rcr.rank) AS blob
                FROM request_chunk_refs rcr
                JOIN document_chunks dc ON dc.id = rcr.record_chunk_id
                WHERE rcr.record_request_id::text = ANY(:ids)
                GROUP BY rcr.record_request_id
                """
            ),
            {"ids": request_ids},
        ))
        await conn.close()
        return {r.rid: (r.blob or "") for r in rows}
    finally:
        await eng.dispose()


def _classify(q: QResult) -> None:
    if q.is_trap:
        q.verdict = "PASS_REFUSE" if q.refused else "HALLU_BREACH"
        return
    if not q.expect:
        q.verdict = "ANSWERED" if (q.answer and not q.refused) else "REFUSE"
        return
    if q.answer_hit:
        q.verdict = "PASS"
    elif q.chunk_hit is True:
        q.verdict = "LLM_MISS"       # retrieval brought it, answer dropped it
    elif q.chunk_hit is False:
        q.verdict = "RETRIEVAL_MISS"  # answer chunk never retrieved
    else:
        q.verdict = "REFUSE_GAP" if q.refused else "WRONG"  # refs unknown


@dataclass
class BotScore:
    bot_id: str
    results: list[QResult] = field(default_factory=list)

    def summary(self) -> dict:
        answerable = [r for r in self.results if r.expect and not r.is_trap]
        traps = [r for r in self.results if r.is_trap]
        covered = [r for r in answerable if r.answer_hit]
        chunk_ok = [r for r in answerable if r.chunk_hit is True]
        hallu = [r for r in traps if r.verdict == "HALLU_BREACH"]
        retr_miss = [r for r in answerable if r.verdict == "RETRIEVAL_MISS"]
        llm_miss = [r for r in answerable if r.verdict == "LLM_MISS"]
        # Third bucket: a coverage-fail whose retrieved chunks are UNKNOWN
        # (``request_chunk_refs`` not written) — e.g. the stats-index route
        # returns a synthetic chunk with no real chunk FK, so chunk_hit is None.
        # Previously these fell out of BOTH retr_miss and llm_miss → the summary
        # showed retr_miss=0 while COVERAGE<1 (an invisible miss that misled
        # layer attribution). Surface it so the three buckets + covered sum to
        # the answerable set.
        unknown_miss = [
            r for r in answerable if r.verdict in ("WRONG", "REFUSE_GAP")
        ]
        lats = sorted(r.latency_ms for r in self.results if r.latency_ms)
        p95 = lats[min(int(len(lats) * 0.95), len(lats) - 1)] if lats else 0
        n_ans = len(answerable)
        return {
            "bot_id": self.bot_id,
            "n_questions": len(self.results),
            "n_answerable": n_ans,
            "coverage": round(len(covered) / n_ans, 3) if n_ans else 1.0,
            "chunk_recall": round(len(chunk_ok) / n_ans, 3) if n_ans else 1.0,
            "hallu_rate": round(len(hallu) / len(traps), 3) if traps else 0.0,
            "retrieval_miss": len(retr_miss),
            "llm_miss": len(llm_miss),
            "unknown_miss": len(unknown_miss),
            "p95_ms": p95,
            "cost_usd": round(sum(r.cost_usd for r in self.results), 5),
        }


def render_md(scores: list[BotScore]) -> str:
    lines = [
        "# End-to-end RAG scorecard + layer split (live)",
        "",
        "Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL "
        "= a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). "
        "RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.",
        "",
        "| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    agg = {"cov": [], "rec": []}
    for s in scores:
        m = s.summary()
        agg["cov"].append(m["coverage"])
        agg["rec"].append(m["chunk_recall"])
        lines.append(
            f"| {m['bot_id']} | {m['n_questions']} | {m['n_answerable']} | "
            f"{m['coverage']:.2f} | {m['chunk_recall']:.2f} | {m['hallu_rate']:.2f} "
            f"| {m['retrieval_miss']} | {m['llm_miss']} | {m['unknown_miss']} | "
            f"{m['p95_ms']} | {m['cost_usd']:.4f} |"
        )
    if agg["cov"]:
        lines.append(
            f"| **MEAN** |  |  | **{sum(agg['cov'])/len(agg['cov']):.2f}** | "
            f"**{sum(agg['rec'])/len(agg['rec']):.2f}** |  |  |  |  |  |"
        )
    lines += ["", "## Failures (layer-attributed)", ""]
    any_fail = False
    for s in scores:
        for r in s.results:
            if r.verdict in ("RETRIEVAL_MISS", "LLM_MISS", "WRONG",
                             "REFUSE_GAP", "HALLU_BREACH"):
                any_fail = True
                lines.append(
                    f"- **{r.bot_id}/{r.qid}** ({r.flow}) → `{r.verdict}` · "
                    f"expect=`{r.expect}` · chunks_used={r.chunks_used} · "
                    f"top_score={r.top_score} · retrieved_chars={r.retrieved_chars}"
                )
    if not any_fail:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_rag_endtoend")
    p.add_argument("--scenarios", default="tests/scenarios/*_scenario.json")
    p.add_argument("--output-json", type=Path, default=None)
    p.add_argument("--output-md", type=Path, default=None)
    p.add_argument("--raw-jsonl", type=Path, default=None)
    args = p.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2

    scores: list[BotScore] = []
    raw_rows: list[dict] = []
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        for path in sorted(glob.glob(args.scenarios)):
            sc = json.load(open(path))
            bot, ch = sc["bot_id"], sc["channel_type"]
            ws = sc.get("workspace_id", "")
            bs = BotScore(bot_id=bot)
            for q in sc["questions"]:
                d = await _ask(c, tok, bot, ch, q["q"], f"e2e-{q['id']}", ws)
                ans = d.get("answer") or ""
                flow = q.get("flow", "")
                qr = QResult(
                    bot_id=bot, qid=q["id"], flow=flow, question=q["q"],
                    expect=q.get("expect"), answer=ans,
                    answer_type=d.get("answer_type"),
                    request_id=d.get("request_id"),
                    chunks_used=int(d.get("chunks_used") or 0),
                    top_score=d.get("top_score"),
                    latency_ms=int(d.get("_lat") or 0),
                    cost_usd=float(d.get("cost_usd") or 0.0),
                    is_trap=flow.endswith("_trap"),
                    refused=_is_refusal(ans) or (d.get("answer_type") == "blocked"),
                )
                if qr.expect:
                    qr.answer_hit = _contains(qr.expect, ans)
                bs.results.append(qr)
            scores.append(bs)

    # Layer attribution — join back retrieved chunks for answerable misses.
    rid_map = {r.request_id: r for s in scores for r in s.results if r.request_id}
    chunk_blobs = await _retrieved_chunks(dsn, list(rid_map))
    for rid, r in rid_map.items():
        blob = chunk_blobs.get(rid)
        if blob is not None and r.expect and not r.is_trap:
            r.retrieved_chars = len(blob)
            r.chunk_hit = _contains(r.expect, blob) if blob else False
    for s in scores:
        for r in s.results:
            _classify(r)
            raw_rows.append(r.__dict__)

    md = render_md(scores)
    js = json.dumps(
        {"schema_version": 1, "bots": [s.summary() for s in scores]},
        indent=2, ensure_ascii=False,
    )
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(js, encoding="utf-8")
    if args.raw_jsonl:
        args.raw_jsonl.parent.mkdir(parents=True, exist_ok=True)
        args.raw_jsonl.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in raw_rows),
            encoding="utf-8",
        )
    sys.stdout.write(md + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
