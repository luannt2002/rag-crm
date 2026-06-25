"""Re-test the xe golden 40Q (NotebookLM set) — categorized %, per-failure step diagnosis.

Scores each answer against an auto-extracted key fact from the golden answer, and
for every failure records the retrieval signals (chunks_used, top_score, source,
retrieve_mode) so the failing STEP is visible: no-data vs not-retrieved vs
wrong-chunk vs LLM-wrong. Sequential (Jina free-tier friendly).

    set -a && source .env && set +a && python scripts/retest_xe_golden_categorized.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections import defaultdict

BASE = "http://localhost:3004"
BOT, CH, WS = "chinh-sach-xe", "web", "xe"


def _key_facts(exp: str) -> list[str]:
    """Extract the most specific verifiable tokens from a golden answer."""
    facts: list[str] = []
    facts += re.findall(r"\d-[A-ZR]+\d*[A-Z]?\s?\d{2,3}/\d{2,3}\s?[A-Z]{2,3}", exp)  # SKU 2-R13 155/80 LPD
    facts += re.findall(r"\d{3}/\d{2}[A-Z]?R?\d{2}[A-Z]?", exp)  # tyre size 195/65R16
    facts += re.findall(r"\d[\d.,]{2,}\s?(?:VND|đ|đồng)", exp)  # price
    facts += re.findall(r"\d{2,3}/\d{2,3}[A-Z]?", exp)  # ratio/size
    facts += re.findall(r"https?://\S+", exp)  # link
    facts += re.findall(r"\b\d{4}\s?\d{3}\s?\d{3}\b", exp)  # hotline
    facts += re.findall(r"\b(?:0[5-9]\s?năm|\d+\s?ngày|\d+\s?giờ|\d+\s?tháng|≥\s?1\.6mm|1\.6mm)", exp)
    facts += re.findall(r"\b\d{2,3}\b", exp)  # bare numbers (stock/date) — last resort
    # de-dup, keep order
    seen, out = set(), []
    for f in facts:
        f = f.strip()
        if f and f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out[:4]


def _tok() -> str:
    bt = os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")
    r = urllib.request.Request(
        f"{BASE}/api/ragbot/test/tokens/self?bot_id={BOT}&channel_type={CH}",
        headers={"X-Ragbot-Loadtest-Bypass": bt},
    )
    return json.load(urllib.request.urlopen(r, timeout=20)).get("token", "")


def ask(jwt: str, q: str) -> dict:
    bt = os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")
    body = json.dumps({
        "bot_id": BOT, "channel_type": CH, "workspace_id": WS, "question": q,
        "connect_id": "golden", "bypass_cache": True,
    }).encode()
    r = urllib.request.Request(
        f"{BASE}/api/ragbot/test/chat", data=body,
        headers={"Authorization": f"Bearer {jwt}", "X-Ragbot-Loadtest-Bypass": bt,
                 "Content-Type": "application/json"},
    )
    try:
        return json.load(urllib.request.urlopen(r, timeout=90))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def main() -> None:
    cases = json.load(open("/tmp/xe_golden.json"))
    jwt = _tok()
    by_cat: dict[str, list] = defaultdict(list)
    fails = []
    for i, c in enumerate(cases, 1):
        facts = _key_facts(c["exp"])
        d = ask(jwt, c["q"])
        ans = (d.get("answer") or "")
        low = ans.lower()
        hit = any(f.lower() in low for f in facts) if facts else bool(ans)
        src = (d.get("sources") or [{}])[0]
        rec = {
            "cat": c["cat"], "q": c["q"], "exp_facts": facts, "pass": hit,
            "chunks_used": d.get("chunks_used"), "top_score": d.get("top_score"),
            "src_preview": (src.get("preview") or "")[:40], "answer": ans[:120],
        }
        by_cat[c["cat"]].append(rec)
        if not hit:
            fails.append(rec)
        print(f"[{i:02d}/{len(cases)}] {'✅' if hit else '❌'} {c['cat'][:14]:14s} "
              f"chk={rec['chunks_used']} sc={rec['top_score']} | {c['q'][:42]}")
        time.sleep(0.3)

    print("\n" + "=" * 60 + "\nKẾT QUẢ THEO NHÓM")
    tot_p = tot = 0
    for cat, recs in by_cat.items():
        p = sum(r["pass"] for r in recs)
        tot_p += p
        tot += len(recs)
        print(f"  {cat:24s} {p}/{len(recs)} = {round(p/len(recs)*100)}%")
    print(f"\n  TỔNG: {tot_p}/{tot} = {round(tot_p/tot*100)}%")

    print("\n" + "=" * 60 + "\nFAILURES — chẩn đoán step")
    for r in fails:
        # step diagnosis heuristic
        if r["chunks_used"] in (0, None):
            step = "NO-RETRIEVE (0 chunk → no data / route fail)"
        elif "stats_in" in (r["src_preview"] or "") or (r["top_score"] == 1.0):
            step = "STATS-ROUTE (synthetic chunk — maybe wrong/missing field)"
        elif "không" in r["answer"].lower() or "chưa" in r["answer"].lower():
            step = "RETRIEVED-but-REFUSE (chunk present, LLM can't answer → wrong chunk/section)"
        else:
            step = "ANSWERED-WRONG (LLM answered but fact mismatch)"
        print(f"  ❌ {r['cat'][:12]:12s} | {r['q'][:44]:44s}")
        print(f"     exp={r['exp_facts']} chk={r['chunks_used']} sc={r['top_score']} src='{r['src_preview']}'")
        print(f"     → {step}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    json.dump({"by_cat": by_cat, "fails": fails}, open(f"reports/XE_GOLDEN_RETEST_{ts}.json", "w"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
