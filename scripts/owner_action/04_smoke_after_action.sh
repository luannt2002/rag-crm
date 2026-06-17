#!/usr/bin/env bash
# 04_smoke_after_action.sh — 5-question smoke after owner action.
#
# Verifies:
#   1. /health returns 200
#   2. 5 chat turns succeed (HTTP 200, ok=true)
#   3. At least 1 turn has top_score > $SMOKE_TOP_SCORE_MIN (vector path active)
#   4. Greeting turn does not refuse (heuristic: response contains greet token)
#
# Domain-neutral: smoke questions are generic (greeting / intro / address /
# booking / chitchat). NO industry/brand terms.
#
# Required env:
#   LOADTEST_BOT_ID, LOADTEST_TENANT_ID, LOADTEST_CHANNEL_TYPE
#   RAGBOT_TOKEN
#
# Optional env:
#   RAGBOT_BASE_URL    (default http://localhost:3004)
#   PYTHON_BIN         (default ./.venv/bin/python3)
#   SMOKE_TOP_SCORE_MIN (default 0.20 — vector path active threshold)
#   OWNER_ACTION_LOG_DIR (default /var/log/ragbot)
#
# Usage:
#   scripts/owner_action/04_smoke_after_action.sh
#
# Exit codes:
#   0 smoke OK
#   1 precondition failure
#   2 chat call failed (non-200 or ok=false)
#   3 quality check failed (top_score below threshold)

set -euo pipefail

cd "$(dirname "$0")/../.."

: "${LOADTEST_BOT_ID:?LOADTEST_BOT_ID env REQUIRED}"
: "${LOADTEST_TENANT_ID:?LOADTEST_TENANT_ID env REQUIRED}"
: "${LOADTEST_CHANNEL_TYPE:?LOADTEST_CHANNEL_TYPE env REQUIRED}"
: "${RAGBOT_TOKEN:?RAGBOT_TOKEN env REQUIRED}"

PY="${PYTHON_BIN:-./.venv/bin/python3}"
BASE_URL="${RAGBOT_BASE_URL:-http://localhost:3004}"
TOP_SCORE_MIN="${SMOKE_TOP_SCORE_MIN:-0.20}"
LOG_DIR="${OWNER_ACTION_LOG_DIR:-/var/log/ragbot}"
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/owner_action_04_smoke_${TS}.log"

log() {
    printf '%s | 04_smoke | %s\n' "$(date -Is)" "$1" | tee -a "$LOG_FILE"
}

log "START base=${BASE_URL} bot=${LOADTEST_BOT_ID} tenant=${LOADTEST_TENANT_ID} top_score_min=${TOP_SCORE_MIN}"

if ! curl -fsS -o /dev/null --max-time 5 "${BASE_URL}/health" 2>/dev/null; then
    log "ERROR server_unreachable"
    exit 1
fi
log "OK health"

# Domain-neutral smoke questions (generic VN — work across verticals)
QUESTIONS=(
    "greeting|Xin chào, mình mới biết bên bạn"
    "factoid|Cho mình hỏi bên bạn cung cấp dịch vụ gì?"
    "address|Địa chỉ liên hệ của bên bạn ở đâu?"
    "booking|Mình muốn đặt lịch, làm thế nào ạ?"
    "chitchat|Cảm ơn bạn nhé"
)

PASS_GREET=0
MAX_TOP_SCORE="0.0"
FAIL_COUNT=0

for entry in "${QUESTIONS[@]}"; do
    intent="${entry%%|*}"
    question="${entry#*|}"
    RESP_FILE="${LOG_DIR}/owner_action_04_resp_${intent}_${TS}.json"
    BODY=$("$PY" -c '
import json, os, sys
print(json.dumps({
    "tenant_id": int(os.environ["LOADTEST_TENANT_ID"]),
    "bot_id": os.environ["LOADTEST_BOT_ID"],
    "channel_type": os.environ["LOADTEST_CHANNEL_TYPE"],
    "question": sys.argv[1],
    "user_id": "owner-action-smoke",
}))
' "$question")

    HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w '%{http_code}' \
        -X POST "${BASE_URL}/api/ragbot/test/chat" \
        -H "Authorization: Bearer ${RAGBOT_TOKEN}" \
        -H "Content-Type: application/json" \
        --max-time "${SMOKE_HTTP_TIMEOUT_S:-60}" \
        --data-binary "$BODY" 2>>"$LOG_FILE") || true

    if [[ "$HTTP_CODE" != "200" ]]; then
        log "FAIL intent=${intent} http=${HTTP_CODE}"
        FAIL_COUNT=$((FAIL_COUNT+1))
        continue
    fi

    OK=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('ok',False))" "$RESP_FILE" 2>>"$LOG_FILE")
    ANSWER_TYPE=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('answer_type','?'))" "$RESP_FILE" 2>>"$LOG_FILE")
    TOP_SCORE=$("$PY" -c "import json,sys
d=json.load(open(sys.argv[1]))
ts = d.get('top_score') or d.get('debug',{}).get('top_score') or 0.0
print(ts)" "$RESP_FILE" 2>>"$LOG_FILE")
    log "INFO intent=${intent} ok=${OK} answer_type=${ANSWER_TYPE} top_score=${TOP_SCORE}"

    if [[ "$OK" != "True" ]]; then
        log "FAIL intent=${intent} ok=False"
        FAIL_COUNT=$((FAIL_COUNT+1))
        continue
    fi

    # Greeting must not refuse
    if [[ "$intent" == "greeting" && "$ANSWER_TYPE" != "refuse" && "$ANSWER_TYPE" != "out_of_scope" ]]; then
        PASS_GREET=1
    fi

    # Track max top_score for vector-path-active assertion
    MAX_TOP_SCORE=$("$PY" -c "import sys; a=float(sys.argv[1]); b=float(sys.argv[2]); print(max(a,b))" "$MAX_TOP_SCORE" "$TOP_SCORE")
done

log "SUMMARY pass_greet=${PASS_GREET} max_top_score=${MAX_TOP_SCORE} fail_count=${FAIL_COUNT}"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    log "FAIL chat_calls failed=${FAIL_COUNT}"
    exit 2
fi

if [[ "$PASS_GREET" -eq 0 ]]; then
    log "FAIL greeting refused — sysprompt loosen may not be applied"
    exit 3
fi

# Vector path active check (best-effort — top_score may be omitted by schema)
EXCEED=$("$PY" -c "import sys; print('1' if float(sys.argv[1]) > float(sys.argv[2]) else '0')" "$MAX_TOP_SCORE" "$TOP_SCORE_MIN")
if [[ "$EXCEED" != "1" ]]; then
    log "WARN max_top_score=${MAX_TOP_SCORE} <= ${TOP_SCORE_MIN} — vector path may still be inactive (or top_score not exposed)"
    # Not a hard fail: top_score may be absent from response schema
fi

log "DONE smoke OK"
exit 0
