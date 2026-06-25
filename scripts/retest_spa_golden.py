"""Re-test the spa golden set (z-luannt-test-spa.txt) — categorized %, step diagnosis.

Parses the Hỏi/Đáp golden pairs grouped by "Nhóm N", extracts the most specific
verifiable facts from each gold answer (price / phone / duration / step-count /
domain term), asks /test/chat, and for every miss records retrieval signals so the
failing STEP is visible. Parallel (ThreadPool N=6) per the load-test-parallel rule.

    set -a && source .env && set +a && python scripts/retest_spa_golden.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE = "http://localhost:3004"
BOT, CH, WS = "test-spa-id", "web", "spa"
GOLDEN = "/var/www/html/ragbot/z-luannt-test-spa.txt"
BT = os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")


def parse_golden() -> list[dict]:
    cases, cat = [], "?"
    lines = open(GOLDEN, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        m = re.match(r"Nhóm\s*\d+\s*:\s*(.+?)\s*\(", ln)
        if m:
            cat = m.group(1).strip()
        if ln.startswith("Hỏi:"):
            q = ln[len("Hỏi:"):].strip()
            # answer may be on the same or following lines until a "." separator
            ans = ""
            j = i + 1
            while j < len(lines):
                a = lines[j].strip()
                if a.startswith("Đáp:"):
                    ans = a[len("Đáp:"):].strip()
                    break
                j += 1
            if q and ans:
                cases.append({"cat": cat, "q": q, "exp": ans})
            i = j
        i += 1
    return cases


def _key_facts(exp: str) -> list[str]:
    facts: list[str] = []
    facts += re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\s*(?:VNĐ|VND|đ)?", exp)        # 700.000 / 11.999.000
    facts += re.findall(r"\b0\d[\d.\s]{7,}\d\b", exp)                            # hotline 0926.559.268
    facts += re.findall(r"\b\d+\s*(?:phút|buổi|bước|tháng|năm|tiếng|chỉ số|điểm)\b", exp)  # 60 phút / 10 buổi / 20 bước
    facts += re.findall(r"\b(?:MFU|SMAS|IPL|MFU|PAYOT|Diode Laser|Ultherapy|Hydra Ballet|Detox Ballet|Weilaiya|Tretinoin|Ribo|Inno A)\b", exp, re.I)
    facts += re.findall(r"https?://\S+", exp)                                    # fanpage link
    facts += re.findall(r"\b\d{1,3}\s+[A-ZÀ-Ỹ][^,.\d]{4,30}", exp)               # address "102 Vũ Trọng Phụng"
    seen, out = set(), []
    for f in facts:
        f = re.sub(r"\s+", " ", f).strip(" .,")
        k = f.lower()
        if f and len(f) >= 2 and k not in seen:
            seen.add(k)
            out.append(f)
    return out[:5]


def _tok() -> str:
    r = urllib.request.Request(
        f"{BASE}/api/ragbot/test/tokens/self?bot_id={BOT}&channel_type={CH}",
        headers={"X-Ragbot-Loadtest-Bypass": BT},
    )
    return json.load(urllib.request.urlopen(r, timeout=20)).get("token", "")


def ask(jwt: str, q: str) -> dict:
    body = json.dumps({
        "bot_id": BOT, "channel_type": CH, "workspace_id": WS, "question": q,
        "connect_id": "golden", "bypass_cache": True,
    }).encode()
    r = urllib.request.Request(
        f"{BASE}/api/ragbot/test/chat", data=body,
        headers={"Authorization": f"Bearer {jwt}", "X-Ragbot-Loadtest-Bypass": BT,
                 "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(r, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
    return {"error": "retries exhausted"}


def _norm(s: str) -> str:
    return re.sub(r"[.\s]", "", s.lower())


def _fact_hit(fact: str, ans: str, ans_digits: str, ans_norm: str) -> bool:
    """A fact matches if its DIGIT-core (≥3 digits) appears in the answer's digit
    stream (currency-suffix agnostic: gold 'VNĐ' vs bot 'đồng' both pass), OR its
    text-core (dot/space-stripped) appears verbatim (links / domain terms)."""
    fd = re.sub(r"\D", "", fact)
    if len(fd) >= 3 and fd in ans_digits:
        return True
    return _norm(fact) in ans_norm


def run_one(args):
    jwt, idx, c = args
    facts = _key_facts(c["exp"])
    d = ask(jwt, c["q"])
    ans = d.get("answer") or ""
    ans_norm = _norm(ans)
    ans_digits = re.sub(r"\D", "", ans)
    hit = any(_fact_hit(f, ans, ans_digits, ans_norm) for f in facts) if facts else bool(ans.strip())
    src = (d.get("sources") or [{}])
    src0 = src[0] if src else {}
    return {
        "cat": c["cat"], "q": c["q"], "exp": c["exp"][:80], "exp_facts": facts,
        "pass": hit, "chunks_used": d.get("chunks_used"), "top_score": d.get("top_score"),
        "src_preview": (src0.get("preview") or "")[:38], "answer": ans[:140],
        "err": d.get("error"),
    }


def main() -> None:
    cases = parse_golden()
    print(f"parsed {len(cases)} golden Q&A")
    jwt = _tok()
    results = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, rec in enumerate(ex.map(run_one, [(jwt, i, c) for i, c in enumerate(cases)])):
            results[i] = rec
            print(f"[{i+1:02d}/{len(cases)}] {'✅' if rec['pass'] else '❌'} "
                  f"{rec['cat'][:18]:18s} chk={rec['chunks_used']} sc={rec['top_score']} "
                  f"| {rec['q'][:40]}")

    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["cat"]].append(r)
    print("\n" + "=" * 64 + "\nKẾT QUẢ THEO NHÓM")
    tp = tot = 0
    for cat, recs in by_cat.items():
        p = sum(r["pass"] for r in recs)
        tp += p
        tot += len(recs)
        print(f"  {cat[:34]:34s} {p}/{len(recs)} = {round(p/len(recs)*100)}%")
    print(f"\n  TỔNG: {tp}/{tot} = {round(tp/tot*100)}%")

    print("\n" + "=" * 64 + "\nFAILURES — chẩn đoán step")
    for r in results:
        if r["pass"]:
            continue
        if r["err"]:
            step = f"ERROR ({r['err'][:40]})"
        elif r["chunks_used"] in (0, None):
            step = "NO-RETRIEVE (0 chunk → no data / route fail)"
        elif any(w in r["answer"].lower() for w in ("không có", "chưa có", "không tìm", "xin lỗi")):
            step = "RETRIEVED-but-REFUSE (chunk present, LLM refuse → wrong chunk)"
        else:
            step = "ANSWERED (fact-core not matched — verify manually)"
        print(f"  ❌ {r['cat'][:16]:16s} | {r['q'][:46]}")
        print(f"     exp='{r['exp']}' facts={r['exp_facts']}")
        print(f"     got='{r['answer'][:90]}' chk={r['chunks_used']} sc={r['top_score']} → {step}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = f"reports/SPA_GOLDEN_RETEST_{ts}.json"
    json.dump({"total": f"{tp}/{tot}", "pct": round(tp / tot * 100),
               "by_cat": {k: v for k, v in by_cat.items()}, "results": results},
              open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
