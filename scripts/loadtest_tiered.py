"""Tiered production load test — isolate MODEL-limit vs PLATFORM-limit.

For each bot's tiered question file (scripts/qa_prod/<bot>.json) this:
  - runs every question 3x (bypass_cache) to measure DETERMINISM (flip = pipeline
    non-determinism; stable-wrong = systematic model/corpus),
  - checks must_contain facts AND, when expected_compute is set, whether the
    computed number is present (ARITHMETIC check — catches mini sum errors),
  - for L0 checks the bot refused and did NOT fabricate,
  - runs L5 multi-turn scenarios threading conversation_id,
  - then ISOLATES: a fact answered at L1 (simple) but missed at L3/L4 (same fact,
    must combine) → the fact was retrievable → failure is GENERATION = model /
    scaffold, NOT retrieval. A fact missed already at L1 → RETRIEVAL = platform.

Parallel per feedback_ragas_parallel (semaphore). Read-only against the API.

Usage: PYTHONPATH=. python scripts/loadtest_tiered.py [bot_id ...]
"""
from __future__ import annotations
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

BASE = "http://localhost:3004/api/ragbot/test"
QDIR = Path(__file__).parent / "qa_prod"
RUNS = 3
SEM = asyncio.Semaphore(6)


def _norm(s: str) -> str:
    # normalise number formatting so "3.597.000" / "3597000" / "3,597,000" match
    return re.sub(r"[.,\s]", "", s.lower())


def _has(answer: str, needle: str) -> bool:
    a, n = _norm(answer), _norm(needle)
    if n.isdigit():
        return n in a
    return needle.lower() in answer.lower()


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


_REFUSE_MARKERS = ("chưa có thông tin", "không có thông tin", "liên hệ", "không tìm thấy",
                   "ngoài phạm vi", "không thuộc", "xin lỗi")


def _judge(q: dict, answer: str) -> tuple[bool, str]:
    """Return (passed, note). Local deterministic check — no LLM judge noise."""
    if q.get("expect_refuse"):
        refused = any(m in answer.lower() for m in _REFUSE_MARKERS)
        return (refused, "refused" if refused else "DID-NOT-REFUSE (possible fabricate)")
    mc = q.get("must_contain", [])
    missing = [f for f in mc if not _has(answer, f)]
    if missing:
        # distinguish arithmetic miss (the computed total) from fact miss
        comp = q.get("expected_compute", "")
        comp_nums = set(re.findall(r"\d[\d.,]{2,}", comp))
        arith_missing = [m for m in missing if _norm(m) in {_norm(x) for x in comp_nums}]
        if arith_missing and len(arith_missing) < len(missing):
            return (False, f"ARITHMETIC miss {arith_missing} (components OK)")
        if arith_missing:
            return (False, f"ARITHMETIC miss {arith_missing}")
        return (False, f"fact miss {missing}")
    return (True, "ok")


async def _run_question(c: httpx.AsyncClient, bot: str, q: dict) -> dict:
    async with SEM:
        if q["level"] == "L5":
            return await _run_scenario(c, bot, q)
        verdicts, notes, answers = [], [], []
        for _ in range(RUNS):
            d = await _ask(c, bot, q["question"])
            ans = d.get("answer", "") if "_error" not in d else ""
            ok, note = _judge(q, ans)
            verdicts.append(ok); notes.append(note); answers.append(ans)
        npass = sum(verdicts)
        stable = npass in (0, RUNS)
        return {"id": q["id"], "level": q["level"], "category": q.get("category"),
                "fact_id": q.get("fact_id"), "fact_ref": q.get("fact_ref"),
                "pass_rate": f"{npass}/{RUNS}", "deterministic": stable,
                "note": notes[-1], "answer": answers[-1][:160],
                "passed": npass >= 2}  # majority


async def _run_scenario(c: httpx.AsyncClient, bot: str, q: dict) -> dict:
    conv = f"tiered-{bot}-{q['id']}"
    turn_res = []
    for i, t in enumerate(q["turns"]):
        d = await _ask(c, bot, t["q"], conv=conv)
        ans = d.get("answer", "") if "_error" not in d else ""
        mc = t.get("must_contain", [])
        mca = t.get("must_contain_any", [])
        ok = all(_has(ans, f) for f in mc) and (not mca or any(_has(ans, f) for f in mca))
        turn_res.append({"turn": i + 1, "ok": ok, "ans": ans[:120]})
    return {"id": q["id"], "level": "L5", "category": "multi_turn",
            "passed": all(t["ok"] for t in turn_res), "turns": turn_res,
            "deterministic": True, "pass_rate": f"{sum(t['ok'] for t in turn_res)}/{len(turn_res)}"}


def _isolate(results: list[dict]) -> list[str]:
    """Same-fact L1-vs-higher comparison → model/platform verdict."""
    l1_ok = {r["fact_id"]: r["passed"] for r in results if r.get("level") == "L1" and r.get("fact_id")}
    out = []
    for r in results:
        if r.get("level") in ("L3", "L4") and not r["passed"] and r.get("fact_ref"):
            refs_seen_simple = [fr for fr in r["fact_ref"] if fr in l1_ok]
            all_simple_ok = refs_seen_simple and all(l1_ok.get(fr, False) for fr in refs_seen_simple)
            if "ARITHMETIC" in r["note"]:
                verdict = "MODEL (arithmetic) — facts retrieved, mini computed wrong"
            elif all_simple_ok:
                verdict = "MODEL/SCAFFOLD (generation) — facts retrievable at L1, failed when combining"
            else:
                verdict = "RETRIEVAL (platform) — a component fact also weak at L1"
            out.append(f"  {r['id']} [{r['category']}] {r['pass_rate']} → {verdict}")
    return out


async def main() -> None:
    bots = sys.argv[1:] or [p.stem for p in QDIR.glob("*.json")]
    async with httpx.AsyncClient() as c:
        for bot in bots:
            spec = json.loads((QDIR / f"{bot}.json").read_text(encoding="utf-8"))
            qs = spec["questions"]
            print(f"\n{'='*70}\n### {bot}  ({spec['archetype']}) — {len(qs)} câu × {RUNS} run\n{'='*70}")
            results = await asyncio.gather(*[_run_question(c, bot, q) for q in qs])
            # by level
            for r in sorted(results, key=lambda x: x["level"]):
                flag = "✅" if r["passed"] else "❌"
                det = "" if r["deterministic"] else " ⚠NON-DETERMINISTIC"
                print(f"{flag} {r['id']:18s} [{r['level']}] {r['pass_rate']}{det}  {r.get('note','')}")
                if r["level"] == "L5":
                    for t in r["turns"]:
                        print(f"      turn{t['turn']} {'ok' if t['ok'] else 'MISS'}: {t['ans'][:80]}")
            print("\n  --- ISOLATION (do MODEL hay PLATFORM) ---")
            iso = _isolate(results)
            print("\n".join(iso) if iso else "  (no L3/L4 failures to isolate)")
            npass = sum(1 for r in results if r["passed"])
            nflip = sum(1 for r in results if not r["deterministic"])
            print(f"\n  SUMMARY {bot}: {npass}/{len(results)} pass · {nflip} non-deterministic")


if __name__ == "__main__":
    asyncio.run(main())
