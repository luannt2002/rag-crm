"""STAGE A — Collect Q&A (KHÔNG chấm điểm). Run golden questions → dump {q, expected,
bot_answer, meta} ra JSON. Chấm điểm tách sang Stage B (agent judge).

    set -a && source .env && set +a
    python scripts/eval_collect.py <golden_file> <bot_id> <channel> <workspace> [workers]
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE="http://localhost:3004"; BT=os.environ.get("RAGBOT_LOADTEST_BYPASS_TOKEN","")
GOLDEN,BOT,CH,WS=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
WORKERS=int(sys.argv[5]) if len(sys.argv)>5 else 1   # default SEQUENTIAL (tránh innocom 503)

def parse_golden():
    cases,cat=[],"?"; lines=open(GOLDEN,encoding="utf-8").read().splitlines(); i=0
    while i<len(lines):
        ln=lines[i].strip()
        m=re.match(r"Nhóm\s*\d+\s*:\s*(.+?)\s*(?:\(|$)",ln)
        if m: cat=m.group(1).strip()[:30]
        if ln.startswith("Hỏi:"):
            q=ln[4:].strip(); ans="";j=i+1
            while j<len(lines):
                a=lines[j].strip()
                if a.startswith("Đáp:"): ans=a[4:].strip(); break
                j+=1
            if q and ans: cases.append({"cat":cat,"q":q,"expected":ans})
            i=j
        i+=1
    return cases

def tok():
    r=urllib.request.Request(f"{BASE}/api/ragbot/test/tokens/self?bot_id={BOT}&channel_type={CH}",headers={"X-Ragbot-Loadtest-Bypass":BT})
    return json.load(urllib.request.urlopen(r,timeout=20)).get("token","")

def ask(jwt,q):
    body=json.dumps({"bot_id":BOT,"channel_type":CH,"workspace_id":WS,"question":q,"connect_id":"eval","bypass_cache":True}).encode()
    r=urllib.request.Request(f"{BASE}/api/ragbot/test/chat",data=body,headers={"Authorization":f"Bearer {jwt}","X-Ragbot-Loadtest-Bypass":BT,"Content-Type":"application/json"})
    for att in range(3):
        try: return json.load(urllib.request.urlopen(r,timeout=120))
        except urllib.error.HTTPError as e:
            if e.code in (429,503) and att<2: time.sleep(3*(att+1)); continue
            return {"error":f"HTTP {e.code}"}
        except Exception as e: return {"error":str(e)}
    return {"error":"retries"}

def run_one(args):
    jwt,c=args; d=ask(jwt,c["q"])
    return {**c,"bot_answer":d.get("answer") or "","chunks_used":d.get("chunks_used"),"top_score":d.get("top_score"),"error":d.get("error")}

def main():
    cases=parse_golden(); jwt=tok(); res=[None]*len(cases)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i,r in enumerate(ex.map(run_one,[(jwt,c) for c in cases])): res[i]=r
    ts=time.strftime("%Y%m%d_%H%M%S")
    out=f"reports/EVAL_COLLECT_{BOT}_{ts}.json"
    json.dump({"bot":BOT,"collected":res},open(out,"w"),ensure_ascii=False,indent=1)
    err=sum(1 for r in res if r.get("error"))
    print(f"COLLECTED {len(res)} Q&A → {out} (errors/innocom: {err})")

if __name__=="__main__": main()
