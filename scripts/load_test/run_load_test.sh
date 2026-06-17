#!/usr/bin/env bash
# Run ragbot load tests (smoke + sustained + burst + stream) and dump CSVs.
#
# Domain-neutral: bot identity from env. Defaults usable for the local dev
# fixture only; override via env when running against a different bot.
#
# Usage:
#   ./scripts/load_test/run_load_test.sh smoke      # 1 user 60s
#   ./scripts/load_test/run_load_test.sh sustained  # 10 users 5m
#   ./scripts/load_test/run_load_test.sh burst      # 50 users 2m30s
#   ./scripts/load_test/run_load_test.sh stream     # streaming 10 users 3m
#   ./scripts/load_test/run_load_test.sh all
set -euo pipefail

cd "$(dirname "$0")/../.."

HOST="${RAGBOT_LOAD_HOST:-http://localhost:3004}"
TENANT_ID="${RAGBOT_LOAD_TENANT_ID:-32}"
BOT_ID="${RAGBOT_LOAD_BOT_ID:-thula-test-bot-v1}"
CHANNEL="${RAGBOT_LOAD_CHANNEL:-web}"
LOCUST="${LOCUST_BIN:-./.venv/bin/locust}"
PY="${PYTHON_BIN:-./.venv/bin/python3}"

if [[ ! -x "$LOCUST" ]]; then
    echo "ERROR: locust not found at $LOCUST" >&2
    exit 1
fi

# Mint dev token (RAGBOT_DEV_TOKEN_ENABLED=true required server-side)
if [[ -z "${RAGBOT_TOKEN:-}" ]]; then
    RAGBOT_TOKEN=$(curl -s "$HOST/api/ragbot/test/tokens/self" \
        | "$PY" -c "import sys,json; print(json.loads(sys.stdin.read())['token'])")
    export RAGBOT_TOKEN
fi
if [[ -z "$RAGBOT_TOKEN" ]]; then
    echo "ERROR: failed to mint RAGBOT_TOKEN from $HOST/api/ragbot/test/tokens/self" >&2
    exit 1
fi

export RAGBOT_LOAD_TENANT_ID="$TENANT_ID"
export RAGBOT_LOAD_BOT_ID="$BOT_ID"
export RAGBOT_LOAD_CHANNEL="$CHANNEL"

mkdir -p reports/load_test
TS="$(date +%Y%m%d_%H%M%S)"

run_one() {
    local name="$1" users="$2" rate="$3" duration="$4" classes="${5:-ChatSyncUser}"
    local prefix="reports/load_test/${name}_${TS}"
    echo
    echo "=== $name | users=$users rate=$rate duration=$duration classes=$classes ==="
    "$LOCUST" -f scripts/load_test/locustfile.py --headless \
        -u "$users" -r "$rate" -t "$duration" \
        --host "$HOST" \
        --csv "$prefix" \
        --html "${prefix}.html" \
        --logfile "${prefix}.log" \
        --loglevel INFO \
        --only-summary \
        $classes \
        2>&1 | tee "${prefix}.stdout.log"
    echo "--- saved csv prefix: $prefix ---"
}

mode="${1:-smoke}"

case "$mode" in
    smoke)     run_one smoke     1  1 60s ChatSyncUser ;;
    sustained) run_one sustained 10 2 5m ChatSyncUser ;;
    burst)     run_one burst     50 5 2m30s ChatSyncUser ;;
    stream)    run_one stream    10 2 3m ChatStreamUser ;;
    all)
        run_one smoke     1  1 60s ChatSyncUser
        run_one sustained 10 2 5m  ChatSyncUser
        run_one burst     50 5 2m30s ChatSyncUser
        run_one stream    10 2 3m  ChatStreamUser
        ;;
    *)
        echo "Unknown mode: $mode" >&2
        echo "Use: smoke | sustained | burst | stream | all" >&2
        exit 2
        ;;
esac

echo
echo "Done. Run: $PY scripts/load_test/parse_results.py reports/load_test"
