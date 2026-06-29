"""STAGE C+D — Replay câu SAI N lần (consistency) + trace step (debug).

Input: file judged (EVAL_AGENT_JUDGE json) HOẶC list câu hỏi. Với mỗi câu FAIL:
  - hỏi lại N lần → đếm sai/đúng/error → phân biệt SAI-BỀN vs FLAKY(innocom).
  - mỗi lần ghi step-trace: chunks_used, top_score, answer_type, error → chỉ ra
    bug ở step nào (no-retrieve / wrong-chunk / refuse / answered-wrong / provider).

    set -a && source .env && set +a
    python scripts/eval_replay_debug.py <bot_id> <channel> <workspace> <N> "<câu hỏi 1>" "<câu 2>" ...
"""
from __future__ import annotations
import json, os, sys, time, urllib.error, urllib.request

BASE="http://localhost:3004"; BT=os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN","")
BOT,CH,WS,N=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4])
QS=sys.argv[5:]

def tok():
    r=urllib.request.Request(f"{BASE}/api/ragbot/test/tokens/self?bot_id={BOT}&channel_type={CH}",headers={"X-Ragbot-Loadtest-Bypass":BT})
    return json.load(urllib.request.urlopen(r,timeout=20)).get("token","")

def ask(jwt,q):
    body=json.dumps({"bot_id":BOT,"channel_type":CH,"workspace_id":WS,"question":q,"connect_id":"replay","bypass_cache":True}).encode()
    r=urllib.request.Request(f"{BASE}/api/ragbot/test/chat",data=body,headers={"Authorization":f"Bearer {jwt}","X-Ragbot-Loadtest-Bypass":BT,"Content-Type":"application/json"})
    try: return json.load(urllib.request.urlopen(r,timeout=120))
    except urllib.error.HTTPError as e: return {"error":f"HTTP {e.code}"}
    except Exception as e: return {"error":str(e)}

def step_of(d):
    if d.get("error"): return f"PROVIDER-ERR({d['error']})"
    if d.get("chunks_used") in (0,None): return "NO-RETRIEVE"
    a=(d.get("answer") or "").lower()
    if not a.strip(): return "EMPTY"
    if any(w in a for w in ("chưa","không tìm","không có thông tin","vui lòng liên hệ")): return "REFUSE/miss"
    return "ANSWERED"

def main():
    jwt=tok(); report=[]
    for q in QS:
        runs=[]
        print(f"\n=== Q: {q[:70]} (×{N}) ===")
        for i in range(N):
            d=ask(jwt,q)
            st=step_of(d)
            runs.append({"step":st,"chunks":d.get("chunks_used"),"sc":d.get("top_score"),"ans":(d.get("answer") or "")[:80]})
            print(f"  lần {i+1}: {st} chunks={d.get('chunks_used')} sc={d.get('top_score')} | {(d.get('answer') or '')[:60]}")
            time.sleep(1)
        steps=[r["step"] for r in runs]
        from collections import Counter
        dist=dict(Counter(steps))
        # phân loại: bền-sai nếu KHÔNG có ANSWERED nào / luôn cùng 1 non-answered step
        provider=sum(1 for s in steps if s.startswith("PROVIDER"))
        verdict=("FLAKY(provider)" if provider>0 and provider<N else
                 "ALL-PROVIDER-ERR" if provider==N else
                 "SAI-BỀN" if "ANSWERED" not in steps and len(set(s for s in steps if not s.startswith("PROVIDER")))<=1 else
                 "INCONSISTENT")
        print(f"  → {verdict} | dist={dist}")
        report.append({"q":q,"verdict":verdict,"dist":dist,"runs":runs})
    ts=time.strftime("%Y%m%d_%H%M%S")
    out=f"reports/EVAL_REPLAY_{BOT}_{ts}.json"
    json.dump({"bot":BOT,"N":N,"report":report},open(out,"w"),ensure_ascii=False,indent=1)
    print(f"\n→ {out}")
    print("BỀN-SAI (cần debug step):", [r["q"][:45] for r in report if r["verdict"]=="SAI-BỀN"])

if __name__=="__main__": main()
