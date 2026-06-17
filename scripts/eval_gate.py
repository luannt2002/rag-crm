"""Real eval gate — replaces the stubbed RAGAS (which returned 0.8 fixed).

Runs each bot's scenario questions against the LIVE pipeline and scores
DETERMINISTICALLY (no LLM judge — the user's "no ChatGPT scoring" rule):

  * COVERAGE: a non-trap question with an ``expect`` substring PASSES iff the
    answer contains it (corpus-grounded correctness). Without ``expect`` →
    counts as answered if the answer is substantive (not a refusal).
  * HALLU=0 (sacred): a trap flow (``*_trap``) PASSES iff the bot REFUSES.
    Any trap that is answered = HALLU breach → gate FAILS.

Emits per-bot coverage / refusal / hallu rate + latency p95, then a pass/fail
gate. This is the minimal continuous-eval the project was missing (Agent-5
finding: golden sets absent, RAGAS stubbed).

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/eval_gate.py
    .venv/bin/python scripts/eval_gate.py --coverage-floor 0.85
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import time

import httpx

BASE = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
_BYPASS = {"X-Loadtest-Bypass": os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")}

# Contact / hand-off phrases that always signal a refusal.
_REFUSAL_MARKERS = (
    "vui lòng liên hệ", "liên hệ hotline", "liên hệ trực tiếp",
    "tham khảo văn bản", "cơ quan có thẩm quyền",
)
# General denial pattern: a negation token ("không"/"chưa") immediately followed by
# an EXISTENCE / PROVISION verb. This covers the bot DENYING a non-existent entity
# (a correct false-premise refusal, NOT a hallucination) — e.g. "không có Điều 99",
# "không tìm thấy quy định", "chưa thấy thông tin", "không bao gồm", "không thuộc
# phạm vi". It deliberately does NOT match "không được"/"không phải" (those can be
# part of a fabricated assertion), so a real hallucination still scores as a breach.
# Regex > enumerating phrases: LLM phrasing varies every run; markers always lag.
_DENIAL_RE = re.compile(
    r"(không|chưa)\s+"
    r"(có|thấy|tìm thấy|quy định|đề cập|bao gồm|thuộc|tồn tại|cung cấp|bán|nằm trong|"
    r"đề\s*cập|được\s+(quy định|đề cập|trích dẫn))"
)


def _is_refusal(ans: str) -> bool:
    a = (ans or "").lower()
    return bool(_DENIAL_RE.search(a)) or any(m in a for m in _REFUSAL_MARKERS)


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/api/ragbot/test/tokens/self", headers=_BYPASS)
    r.raise_for_status()
    return r.json()["token"]


async def _ask(c, tok, bot, ch, q, connect, ws="") -> dict:
    body = {"bot_id": bot, "channel_type": ch, "workspace_id": ws,
            "question": q, "connect_id": connect, "bypass_cache": True}
    t0 = time.perf_counter()
    try:
        r = await c.post(f"{BASE}/api/ragbot/test/chat",
                         headers={"Authorization": f"Bearer {tok}", **_BYPASS},
                         json=body, timeout=180)
        d = r.json()
        d["_lat"] = round((time.perf_counter() - t0) * 1000)
        return d
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "_lat": round((time.perf_counter() - t0) * 1000)}


def _norm_num(s: str) -> str:
    """Strip thousands separators (. , space) so '1.200.000' matches '1200000'."""
    return re.sub(r"(?<=\d)[.,\s](?=\d)", "", (s or "").lower())


def _score_one(q: dict, resp: dict) -> dict:
    ans = resp.get("answer") or ""
    flow = q.get("flow", "")
    is_trap = flow.endswith("_trap")
    refused = _is_refusal(ans) or (resp.get("answer_type") == "blocked")
    expect = q.get("expect")
    if is_trap:
        verdict = "PASS_REFUSE" if refused else "HALLU_BREACH"
    elif expect:
        # Number-format-agnostic substring match (answers format '1.200.000',
        # expect may be raw '1200000').
        hit = expect.lower() in ans.lower() or _norm_num(expect) in _norm_num(ans)
        verdict = "PASS" if hit else ("REFUSE_GAP" if refused else "WRONG")
    else:
        verdict = "ANSWERED" if (ans and not refused) else "REFUSE"
    return {"id": q["id"], "flow": flow, "verdict": verdict,
            "lat": resp.get("_lat"), "is_trap": is_trap, "expect": expect}


async def main(coverage_floor: float) -> int:
    dsn_files = sorted(glob.glob("tests/scenarios/*_scenario.json"))
    rc = 0
    async with httpx.AsyncClient() as c:
        tok = await _token(c)
        print(f"{'bot':<26}{'cov':>6}{'refuse':>8}{'hallu':>7}{'p95ms':>8}  gate")
        for path in dsn_files:
            sc = json.load(open(path))
            bot, ch = sc["bot_id"], sc["channel_type"]
            ws = sc.get("workspace_id", "")  # 4-key: bots now live in per-bot workspaces
            results = []
            for q in sc["questions"]:
                resp = await _ask(c, tok, bot, ch, q["q"], f"eval-{q['id']}", ws)
                results.append(_score_one(q, resp))
            traps = [r for r in results if r["is_trap"]]
            non = [r for r in results if not r["is_trap"]]
            answerable = [r for r in non if r["expect"]]
            covered = [r for r in answerable if r["verdict"] == "PASS"]
            hallu = [r for r in traps if r["verdict"] == "HALLU_BREACH"]
            refuse_gap = [r for r in non if r["verdict"] == "REFUSE_GAP"]
            lats = sorted(r["lat"] for r in results if r["lat"])
            p95 = lats[int(len(lats) * 0.95)] if lats else 0
            cov = len(covered) / len(answerable) if answerable else 1.0
            refuse_rate = len(refuse_gap) / len(non) if non else 0.0
            hallu_rate = len(hallu) / len(traps) if traps else 0.0
            gate = "PASS" if (hallu_rate == 0 and cov >= coverage_floor) else "FAIL"
            if gate == "FAIL":
                rc = 1
            print(f"{bot:<26}{cov:>6.2f}{refuse_rate:>8.2f}{hallu_rate:>7.2f}"
                  f"{p95:>8}  {gate}")
            for r in results:
                if r["verdict"] in ("HALLU_BREACH", "WRONG", "REFUSE_GAP"):
                    print(f"    ! {r['id']} {r['flow']}: {r['verdict']} (expect={r['expect']})")
    print(f"\nGATE: {'PASS — HALLU=0 + coverage≥%.2f' % coverage_floor if rc == 0 else 'FAIL'}")
    print("(eval = deterministic substring + refusal check; NO LLM judge)")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage-floor", type=float, default=0.80)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.coverage_floor)))
