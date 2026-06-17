"""Robust load-test + grading — verifies against DB, judges semantically.

Fixes the brittle string-matching of loadtest_tiered.py v1 (Ctrl+B vs Ctrl + B,
p2 vs p², NX vs X-M, comma vs dot, narrow refuse markers, wrong hand-authored
facts). Per GRADING_SOP.md, for each question (3 runs):

  1. DB ground-truth: confirm each gold fact actually appears in the bot's corpus
     (catches wrong test data — e.g. "10 steps" when corpus says "21").
  2. Run bot (bypass_cache), capture answer + retrieved sources/citations.
  3. LLM-judge SEMANTICALLY (no literal match): facts_covered / answer_correct /
     refused / fabricated.
  4. Attribution: db_has × retrieved × correct → CORPUS-GAP / RETRIEVAL / MODEL /
     SCAFFOLD / PASS.
  5. Isolation: L1 ok + L3/L4 fail (same fact) → GENERATION not retrieval.

Parallel (semaphore) per feedback_ragas_parallel. Read-only against API + DB.

Usage: PYTHONPATH=. python scripts/loadtest_graded.py [bot_id ...]
       → reports/GRADED_<bot>.json + console scorecard
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://localhost:3004/api/ragbot/test"
QDIR = Path(__file__).parent / "qa_prod"
JUDGE = "gpt-4.1-mini"
RUNS = 3
SEM = asyncio.Semaphore(5)
JSEM = asyncio.Semaphore(8)
_ENGINE = None
_CORPUS: dict[str, str] = {}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    return _ENGINE


async def _corpus(bot: str) -> str:
    """Whole-corpus text for the bot (cached) — for DB ground-truth verification."""
    if bot not in _CORPUS:
        async with _engine().connect() as cx:
            rows = await cx.execute(text("""
                SELECT dc.content FROM document_chunks dc
                JOIN documents d ON d.id = dc.record_document_id
                JOIN bots b ON b.id = d.record_bot_id
                WHERE b.bot_id = :bot
            """), {"bot": bot})
            _CORPUS[bot] = "\n".join(r[0] or "" for r in rows.fetchall())
    return _CORPUS[bot]


def _norm(s: str) -> str:
    return re.sub(r"[.,\s]", "", str(s).lower())


def _db_has(corpus: str, fact: str) -> bool:
    n = _norm(fact)
    if re.fullmatch(r"\d+", n):           # a number → match digits anywhere
        return n in _norm(corpus)
    # token-ish: most alpha tokens of the fact present
    toks = [t for t in re.split(r"\s+", fact.lower()) if len(t) > 2]
    if not toks:
        return _norm(fact) in _norm(corpus)
    hit = sum(1 for t in toks if t in corpus.lower())
    return hit >= max(1, len(toks) // 2)


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def _ask(c: httpx.AsyncClient, bot: str, q: str, conv: str | None = None) -> dict:
    for attempt in range(4):
        tok = await _token(c)
        body = {"bot_id": bot, "channel_type": "web", "question": q, "bypass_cache": True}
        if conv:
            body["conversation_id"] = conv
        r = await c.post(f"{BASE}/chat", json=body,
                         headers={"Authorization": f"Bearer {tok}"}, timeout=120)
        if r.status_code == 503:
            await asyncio.sleep(4 * (attempt + 1)); continue
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}"}
        d = r.json()
        return d.get("data") if isinstance(d, dict) and "data" in d else d
    return {"_error": "503"}


_JUDGE_SYS = (
    "Bạn là giám khảo chấm câu trả lời RAG. Chấm theo NGHĨA, BỎ QUA khác biệt "
    "định dạng (dấu cách, ², dấu phẩy/chấm trong số, ký hiệu tương đương như NX = X−M, "
    "Ctrl+B = Ctrl + B). Chỉ trả JSON, không giải thích."
)


def _judge_prompt(q: dict, answer: str) -> str:
    if q.get("expect_refuse"):
        return (
            f"CÂU HỎI (ngoài phạm vi tài liệu): {q['question']}\n"
            f"BOT TRẢ LỜI: {answer}\n\n"
            "Bot có TỪ CHỐI đúng cách (nói không có trong tài liệu / khuyên hỏi nguồn khác) "
            "và KHÔNG BỊA số liệu/dữ kiện cụ thể không?\n"
            'Trả JSON: {"refused": true/false, "fabricated": true/false}'
        )
    facts = q.get("must_contain", [])
    comp = q.get("expected_compute", "")
    extra = f"\nPHÉP TÍNH KỲ VỌNG: {comp}" if comp else ""
    return (
        f"CÂU HỎI: {q['question']}\n"
        f"CÁC DỮ KIỆN BẮT BUỘC (gold, đối chiếu theo nghĩa): {facts}{extra}\n"
        f"BOT TRẢ LỜI: {answer}\n\n"
        "Với MỖI dữ kiện bắt buộc, đánh giá bot có nêu ĐÚNG về nghĩa không. "
        "answer_correct = true nếu trả lời đúng trọng tâm câu hỏi (gồm cả phép tính nếu có).\n"
        'Trả JSON: {"facts_covered": {"<dữ kiện>": true/false, ...}, "answer_correct": true/false}'
    )


async def _judge(q: dict, answer: str) -> dict:
    if not answer:
        return {"answer_correct": False, "facts_covered": {}, "refused": False, "fabricated": False}
    async with JSEM:
        try:
            r = await litellm.acompletion(
                model=JUDGE, temperature=0.0, max_tokens=400,
                api_key=os.environ.get("OPENAI_API_KEY"),
                messages=[{"role": "system", "content": _JUDGE_SYS},
                          {"role": "user", "content": _judge_prompt(q, answer)}],
            )
            raw = (r.choices[0].message.content or "").strip()
            raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — judge failure shouldn't crash run
            return {"_judge_error": type(exc).__name__, "answer_correct": False, "facts_covered": {}}


def _retrieved_text(d: dict) -> str:
    parts = []
    for s in (d.get("sources") or []):
        parts.append(str(s.get("preview", "")))
    for c in (d.get("citations") or []):
        parts.append(str(c.get("quote", "")))
    return "\n".join(parts)


async def _grade_one(c: httpx.AsyncClient, bot: str, q: dict, corpus: str) -> dict:
    if q["level"] == "L5":
        return await _grade_scenario(c, bot, q)
    facts = q.get("must_contain", [])
    # A computed total (RHS of expected_compute, e.g. "...=398000") is DERIVED,
    # not a corpus literal — exclude it from DB ground-truth + retrieval checks
    # (only the LLM judge grades whether the bot computed it right).
    comp = q.get("expected_compute", "")
    derived = {_norm(x) for x in re.findall(r"=\s*([\d.,]+)", comp)}
    source_facts = [f for f in facts if _norm(f) not in derived]
    db_has = {f: _db_has(corpus, f) for f in source_facts}
    db_all = all(db_has.values()) if db_has else True
    runs = []
    for _ in range(RUNS):
        d = await _ask(c, bot, q["question"])
        ans = d.get("answer", "") if "_error" not in d else ""
        verdict = await _judge(q, ans)
        retr_text = _retrieved_text(d)
        retrieved = {f: _db_has(retr_text, f) for f in source_facts}
        if q.get("expect_refuse"):
            passed = bool(verdict.get("refused")) and not verdict.get("fabricated")
        else:
            passed = bool(verdict.get("answer_correct"))
        runs.append({"answer": ans[:1200], "passed": passed, "verdict": verdict,
                     "retrieved": retrieved})
    npass = sum(1 for r in runs if r["passed"])
    # attribution on a failing run (prefer a failed one)
    fr = next((r for r in runs if not r["passed"]), runs[-1])
    layer = _attribute(q, db_all, db_has, fr)
    # HALLU is the refuse-trap fabrication signal: bot invented a concrete
    # fact for an out-of-corpus question. Sacred metric — track per question.
    hallu = bool(q.get("expect_refuse")) and any(
        r["verdict"].get("fabricated") for r in runs
    )
    return {"id": q["id"], "level": q["level"], "category": q.get("category"),
            "fact_id": q.get("fact_id"), "fact_ref": q.get("fact_ref"),
            "question": q["question"], "expect_refuse": bool(q.get("expect_refuse")),
            "gold_facts": source_facts, "expected_compute": comp or None,
            "db_ground_truth": db_all, "pass_rate": f"{npass}/{RUNS}",
            "deterministic": npass in (0, RUNS), "passed": npass >= 2,
            "hallu": hallu, "judge": fr["verdict"],
            "layer": layer, "sample_answer": fr["answer"]}


def _attribute(q: dict, db_all: bool, db_has: dict, run: dict) -> str:
    if run["passed"]:
        return "PASS"
    if q.get("expect_refuse"):
        return "REFUSAL-FAIL (fabricated)" if run["verdict"].get("fabricated") else "OK-refused"
    if not db_all:
        missing = [f for f, v in db_has.items() if not v]
        return f"CORPUS-GAP/TEST-DATA (fact not in DB: {missing})"
    retrieved_all = all(run["retrieved"].values()) if run["retrieved"] else True
    comp = bool(q.get("expected_compute"))
    if not retrieved_all:
        return "RETRIEVAL (platform) — gold fact not in retrieved chunks"
    if comp:
        return "MODEL/SCAFFOLD (compute) — facts retrieved, computation wrong/absent"
    return "GENERATION (model) — facts retrieved, answer wrong"


async def _grade_scenario(c: httpx.AsyncClient, bot: str, q: dict) -> dict:
    conv = f"graded-{bot}-{q['id']}"
    turns = []
    for i, t in enumerate(q["turns"]):
        d = await _ask(c, bot, t["q"], conv=conv)
        ans = d.get("answer", "") if "_error" not in d else ""
        v = await _judge({"question": t["q"], "must_contain": t.get("must_contain", []) + t.get("must_contain_any", [])}, ans)
        turns.append({"turn": i + 1, "ok": bool(v.get("answer_correct")), "ans": ans[:120]})
    return {"id": q["id"], "level": "L5", "category": "multi_turn",
            "question": " · ".join(t["q"] for t in q["turns"]),
            "expect_refuse": False, "gold_facts": [], "expected_compute": None,
            "passed": all(t["ok"] for t in turns), "deterministic": True,
            "pass_rate": f"{sum(t['ok'] for t in turns)}/{len(turns)}", "layer": "scenario",
            "hallu": False, "judge": {}, "turns": turns,
            "sample_answer": " || ".join(f"T{t['turn']}:{t['ans']}" for t in turns),
            "db_ground_truth": True}


def _isolate(results: list[dict]) -> list[str]:
    l1 = {r["fact_id"]: r["passed"] for r in results if r.get("level") == "L1" and r.get("fact_id")}
    out = []
    for r in results:
        if r.get("level") in ("L3", "L4") and not r["passed"] and r.get("fact_ref"):
            simple = [fr for fr in r["fact_ref"] if fr in l1]
            all_ok = simple and all(l1.get(fr) for fr in simple)
            note = "facts OK at L1 → GENERATION/SCAFFOLD" if all_ok else "component weak at L1 → RETRIEVAL"
            out.append(f"  {r['id']} [{r['category']}] {r['pass_rate']} {r['layer']} | isolation: {note}")
    return out


async def main() -> None:
    bots = sys.argv[1:] or [p.stem for p in sorted(QDIR.glob("*.json"))]
    grand = []
    async with httpx.AsyncClient() as c:
        for bot in bots:
            spec = json.loads((QDIR / f"{bot}.json").read_text(encoding="utf-8"))
            corpus = await _corpus(bot)
            qs = spec["questions"]
            print(f"\n{'='*72}\n### {bot} ({spec['archetype']}) — {len(qs)} câu × {RUNS} run [judge+DB]\n{'='*72}")
            results = await asyncio.gather(*[_grade_one(c, bot, q, corpus) for q in qs])
            for r in sorted(results, key=lambda x: x["level"]):
                flag = "✅" if r["passed"] else "❌"
                det = "" if r["deterministic"] else " ⚠FLIP"
                gap = "" if r.get("db_ground_truth", True) else " ⚠TEST-DATA"
                print(f"{flag} {r['id']:16s}[{r['level']}] {r['pass_rate']}{det}{gap}  {r['layer']}")
            iso = _isolate(results)
            if iso:
                print("  --- ISOLATION ---"); print("\n".join(iso))
            npass = sum(1 for r in results if r["passed"])
            nflip = sum(1 for r in results if not r["deterministic"])
            ndata = sum(1 for r in results if not r.get("db_ground_truth", True))
            print(f"  SUMMARY {bot}: {npass}/{len(results)} pass · {nflip} flip · {ndata} test-data-issue")
            grand.append({"bot": bot, "pass": npass, "n": len(results), "flip": nflip,
                          "test_data_issues": ndata, "results": results})
            Path("reports").mkdir(exist_ok=True)
            Path(f"reports/GRADED_{bot}.json").write_text(
                json.dumps({"bot": bot, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*72}\nGRAND TOTAL: {sum(g['pass'] for g in grand)}/{sum(g['n'] for g in grand)} pass · "
          f"{sum(g['flip'] for g in grand)} flips · {sum(g['test_data_issues'] for g in grand)} test-data issues")
    Path("reports/GRADED_SUMMARY.json").write_text(
        json.dumps(grand, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GRADED_ALL_DONE")


if __name__ == "__main__":
    asyncio.run(main())
