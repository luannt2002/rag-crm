#!/bin/bash
# Smoke test cho 1 bot trên 1 query với 3 rephrase variant.
#
# Why: lesson 2026-05-14 — query format (case, terse vs verbose) ảnh hưởng
# top_score lớn (0.93 → 0.41). Test 1 variant duy nhất tạo cảm giác bot
# work, nhưng prod user paraphrase đa dạng → fail rate cao. Smoke gate
# 3 variant tối thiểu trước khi gọi bot "ready".
#
# Usage:
#   bash scripts/smoke_chat_multi_variant.sh <bot_id> <channel_type> <workspace_id> "<query_concept>"
# Example:
#   bash scripts/smoke_chat_multi_variant.sh thong-tu-09-2020-tt-nhnn web c2f66cb2-9911-5d34-a46e-a4a6da068e23 "Điều 14"

set -u
BOT_ID="${1:-thong-tu-09-2020-tt-nhnn}"
CHANNEL="${2:-web}"
WORKSPACE="${3:-c2f66cb2-9911-5d34-a46e-a4a6da068e23}"
CONCEPT="${4:-Điều 14}"

# Pass token bypass nếu có (paid tier dev token); fall back to anonymous bypass
# only when admin explicitly exported it. KHÔNG hard-code production token.
BYPASS_HEADER=""
if [ -n "${RAGBOT_LOADTEST_BYPASS_TOKEN:-}" ]; then
    BYPASS_HEADER="-H X-Ragbot-Loadtest-Bypass: $RAGBOT_LOADTEST_BYPASS_TOKEN"
fi

TOKEN=$(curl -s http://localhost:3004/api/ragbot/test/tokens/self | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")
if [ -z "$TOKEN" ]; then
    echo "❌ Không lấy được dev token. App down hoặc endpoint thay đổi."
    exit 1
fi

declare -a VARIANTS=(
    "${CONCEPT} nói gì?"
    "${CONCEPT,,} thông tư nói về điều gì"
    "Cho tôi biết nội dung của ${CONCEPT}"
)

declare -i pass=0
declare -i total=0
declare -a results=()

for q in "${VARIANTS[@]}"; do
    total=$((total + 1))
    body=$(python3 -c "import json,sys; print(json.dumps({'bot_id':'${BOT_ID}','channel_type':'${CHANNEL}','workspace_id':'${WORKSPACE}','question':sys.argv[1]}, ensure_ascii=False))" "$q")
    resp=$(curl -s -X POST http://localhost:3004/api/ragbot/test/chat \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        $BYPASS_HEADER \
        -d "$body")

    parsed=$(python3 << PYEOF
import json
try:
    d = json.loads('''$resp''')
except Exception as e:
    print(f"PARSE_ERR|{e}|")
    raise SystemExit
chunks = d.get('chunks_used') or 0
ans = (d.get('answer') or '').strip()
tok_out = (d.get('tokens') or {}).get('completion', 0)
refuse = d.get('refusal_reason')
dur = d.get('duration_ms') or 0
# pass gate: chunks>=1 + tok_out>=50 (avoid stub refuse) + no refusal
# refusal_reason None hoặc empty là OK (refuse có thể đúng cho trap, nhưng smoke đây hỏi facts)
status = 'PASS'
if chunks == 0:
    status = 'FAIL_NO_CHUNKS'
elif tok_out < 50:
    status = 'FAIL_STUB_ANSWER'
elif refuse:
    status = f'FAIL_REFUSE:{refuse}'
print(f"{status}|chunks={chunks}|tok_out={tok_out}|dur={dur}ms|ans={ans[:80].replace(chr(10),' ')}")
PYEOF
)
    status=$(echo "$parsed" | cut -d'|' -f1)
    detail=$(echo "$parsed" | cut -d'|' -f2-)
    if [ "$status" = "PASS" ]; then
        pass=$((pass + 1))
        echo "  ✅ [$total/3] \"$q\" → $detail"
    else
        echo "  ❌ [$total/3] $status — \"$q\" → $detail"
    fi
    results+=("$status: $q")
done

echo ""
echo "===================="
echo "Smoke: $pass / $total PASS ($((pass * 100 / total))%)"
echo "===================="
# Gate: ≥2/3 PASS để approve (1 variant FAIL có thể do edge query). 0 PASS = bot broken.
if [ "$pass" -ge 2 ]; then
    echo "VERDICT: READY (≥2/3 variants pass)"
    exit 0
elif [ "$pass" -ge 1 ]; then
    echo "VERDICT: FRAGILE (chỉ 1/3 variant pass — retrieval không robust)"
    exit 1
else
    echo "VERDICT: BROKEN (0/3 variant pass — pipeline có bug)"
    exit 2
fi
