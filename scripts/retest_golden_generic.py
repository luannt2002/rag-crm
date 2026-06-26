"""Generic golden re-test for ANY bot — parse Hỏi/Đáp, score by digit/alnum/term core,
parallel, step diagnosis. Works for spa (prices), xe (SKU/stock), legal (prose/acronyms).

    set -a && source .env && set +a && \
      python scripts/retest_golden_generic.py <golden_file> <bot_id> <channel> <workspace>
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE = "http://localhost:3004"
BT = os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN", "")
GOLDEN, BOT, CH, WS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def parse_golden() -> list[dict]:
    cases, cat = [], "?"
    lines = open(GOLDEN, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        m = re.match(r"Nhóm\s*\d+\s*:\s*(.+?)\s*(?:\(|$)", ln)
        if m:
            cat = m.group(1).strip()[:30]
        if ln.startswith("Hỏi:"):
            q = ln[len("Hỏi:"):].strip()
            ans, j = "", i + 1
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


def key_facts(exp: str) -> list[str]:
    f: list[str] = []
    f += re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b", exp)                  # prices
    f += re.findall(r"\b0\d[\d.\s]{7,}\d\b", exp)                       # hotline
    f += re.findall(r"\d-[A-Z]+\d*\s+\d+/\d+\s+[A-Z]+", exp)            # SKU
    f += re.findall(r"\d{3}/\d{2}R?\d{2}[A-Z]?", exp)                   # tyre size
    f += re.findall(r"\b[A-ZÀ-Ỹ]{2,}(?:[A-ZÀ-Ỹ]+)?\b", exp)            # acronyms NAPAS/VAMC
    f += re.findall(r"\b\d+\s*(?:phút|buổi|bước|tháng|năm|ngày|lần|%)\b", exp)
    f += re.findall(r"https?://\S+", exp)
    f += [w for w in re.findall(r"\b[a-zà-ỹ]{7,}\b", exp.lower())][:3]  # distinctive long words
    seen, out = set(), []
    for x in f:
        x = re.sub(r"\s+", " ", x).strip(" .,")
        k = x.lower()
        if x and len(x) >= 2 and k not in seen:
            seen.add(k)
            out.append(x)
    return out[:6]


def tok() -> str:
    r = urllib.request.Request(
        f"{BASE}/api/ragbot/test/tokens/self?bot_id={BOT}&channel_type={CH}",
        headers={"X-Ragbot-Loadtest-Bypass": BT})
    return json.load(urllib.request.urlopen(r, timeout=20)).get("token", "")


def ask(jwt: str, q: str) -> dict:
    body = json.dumps({"bot_id": BOT, "channel_type": CH, "workspace_id": WS,
                       "question": q, "connect_id": "golden", "bypass_cache": True}).encode()
    r = urllib.request.Request(f"{BASE}/api/ragbot/test/chat", data=body,
        headers={"Authorization": f"Bearer {jwt}", "X-Ragbot-Loadtest-Bypass": BT,
                 "Content-Type": "application/json"})
    for att in range(3):
        try:
            return json.load(urllib.request.urlopen(r, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code == 429 and att < 2:
                time.sleep(3 * (att + 1)); continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
    return {"error": "retries"}


def run_one(args):
    jwt, c = args
    facts = key_facts(c["exp"])
    d = ask(jwt, c["q"])
    ans = d.get("answer") or ""
    an = re.sub(r"[^a-z0-9à-ỹ]", "", ans.lower())
    ad = re.sub(r"\D", "", ans)

    def hit(f: str) -> bool:
        fa = re.sub(r"[^a-z0-9à-ỹ]", "", f.lower())
        if len(fa) >= 4 and fa in an:
            return True
        fd = re.sub(r"\D", "", f)
        return len(fd) >= 3 and fd in ad
    ok = any(hit(f) for f in facts) if facts else bool(ans.strip())
    return {"cat": c["cat"], "q": c["q"], "exp": c["exp"][:70], "facts": facts, "pass": ok,
            "chk": d.get("chunks_used"), "sc": d.get("top_score"), "ans": ans[:110], "err": d.get("error")}


def main():
    cases = parse_golden()
    jwt = tok()
    res = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=2) as ex:
        for i, r in enumerate(ex.map(run_one, [(jwt, c) for c in cases])):
            res[i] = r
    by = defaultdict(list)
    for r in res:
        by[r["cat"]].append(r)
    print(f"\n=== {BOT} : {len(cases)}Q ===")
    tp = tot = 0
    for cat, rs in by.items():
        p = sum(x["pass"] for x in rs); tp += p; tot += len(rs)
        print(f"  {cat[:30]:30s} {p}/{len(rs)}")
    print(f"  TỔNG: {tp}/{tot} = {round(tp/tot*100)}%")
    fails = [r for r in res if not r["pass"]]
    print(f"  --- {len(fails)} fails (step) ---")
    for r in fails:
        step = ("ERROR" if r["err"] else "NO-RETRIEVE" if r["chk"] in (0, None)
                else "REFUSE/wrong-chunk" if any(w in r["ans"].lower() for w in ("chưa", "không có", "không tìm"))
                else "ANSWERED-mismatch")
        print(f"   ❌ {r['cat'][:14]:14s} chk={r['chk']} sc={r['sc']} | {r['q'][:42]} → {step}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    json.dump({"bot": BOT, "pct": round(tp/tot*100), "total": f"{tp}/{tot}", "results": res},
              open(f"reports/GOLDEN_{BOT}_{ts}.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
