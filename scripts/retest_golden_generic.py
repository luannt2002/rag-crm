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


# Specific verifiable facts (a strong single-match pass): prices / SKUs / sizes /
# acronyms / durations / links. These are exact tokens the answer must carry.
def specific_facts(exp: str) -> list[str]:
    f: list[str] = []
    f += re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b", exp)                  # prices
    f += re.findall(r"\b0\d[\d.\s]{7,}\d\b", exp)                       # hotline
    f += re.findall(r"\d-[A-Z]+\d*\s+\d+/\d+\s+[A-Z]+", exp)            # SKU
    f += re.findall(r"\d{3}/\d{2}R?\d{2}[A-Z]?", exp)                   # tyre size
    f += re.findall(r"\bhttps?://\S+", exp)                             # link
    f += re.findall(r"\b[A-ZÀ-Ỹ]{3,}\b", exp)                           # acronyms NAPAS/VAMC
    f += re.findall(r"\b\d+\s*(?:phút|buổi|bước|tháng|năm|ngày|lần|%|yếu tố)\b", exp)
    seen, out = set(), []
    for x in f:
        x = re.sub(r"\s+", " ", x).strip(" .,")
        if x and x.lower() not in seen:
            seen.add(x.lower()); out.append(x)
    return out


# Vietnamese stopwords / function words — excluded from content-overlap so a gold
# answer's DISTINCTIVE words (not "là/để/của") drive the semantic match.
_STOP = set("la cua va cac nhung duoc trong cho voi khi tu den theo mot nay do co "
            "khong de hoac nhu ve bi boi tren duoi sau truoc gi nao ra vao thi ma "
            "tai con cung neu hay tuc bao gom cac don vi theo dieu khoan thong tu".split())


def _fold(s: str) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower())
                if unicodedata.category(c) != "Mn")
    return s


def content_words(exp: str) -> list[str]:
    """Distinctive content words of a gold answer (accent-folded, ≥4 chars, non-stop,
    plus any numbers). These carry the MEANING; a correct paraphrase reuses most."""
    out: list[str] = []
    for w in re.findall(r"[A-Za-zÀ-ỹ]+|\d[\d.,]*", exp):
        fw = _fold(w)
        if fw.isdigit() and len(fw) >= 2:
            out.append(fw)
        elif len(fw) >= 4 and fw not in _STOP:
            out.append(fw)
    return out


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
    d = ask(jwt, c["q"])
    ans = d.get("answer") or ""
    facts = specific_facts(c["exp"])
    an_fold = _fold(ans)
    an_alnum = re.sub(r"[^a-z0-9]", "", an_fold)
    an_digits = re.sub(r"\D", "", ans)
    an_words = set(content_words(ans))

    def fact_hit(f: str) -> bool:
        fa = re.sub(r"[^a-z0-9]", "", _fold(f))
        if len(fa) >= 4 and fa in an_alnum:
            return True
        fd = re.sub(r"\D", "", f)
        return len(fd) >= 3 and fd in an_digits

    # STRONG: any specific fact present. SEMANTIC: ≥50% of the gold's distinctive
    # content words appear in the answer (handles prose / paraphrase / definitions).
    gw = content_words(c["exp"])
    overlap = (sum(1 for w in gw if w in an_words) / len(gw)) if gw else 0.0
    ok = bool(ans.strip()) and (
        (facts and any(fact_hit(f) for f in facts)) or overlap >= 0.5
    )
    return {"cat": c["cat"], "q": c["q"], "exp": c["exp"][:70], "facts": facts,
            "overlap": round(overlap, 2), "pass": ok,
            "chk": d.get("chunks_used"), "sc": d.get("top_score"),
            "ans": ans[:160], "err": d.get("error")}


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
