#!/bin/bash
# ============================================
# RAGbot — Build, Test & Deploy Script
# Services: ragbot-api (FastAPI port 8000)
# Dependencies: PostgreSQL + Redis (local)
# ============================================
# Usage:
#   ./deploy.sh              # Test + deploy
#   ./deploy.sh --test-only  # Only run tests
#   ./deploy.sh --restart    # Just restart (no test)
# ============================================

set -e

ROOT="/var/www/html/ragbot"
VENV="$ROOT/.venv/bin"
PID_FILE="/tmp/ragbot-api.pid"
LOG_FILE="/var/log/ragbot-api.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  RAGbot — Build, Test & Deploy${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

cd "$ROOT"
set -a && source ./.env 2>/dev/null ; set +a

# ── 0. Check dependencies ──
echo -e "${YELLOW}[0/5] Checking dependencies...${NC}"

# Redis
if redis-cli ping > /dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Redis: running${NC}"
else
    echo -e "${YELLOW}  → Starting Redis...${NC}"
    systemctl start redis 2>/dev/null || redis-server --daemonize yes
    sleep 1
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Redis: started${NC}"
    else
        echo -e "${RED}  ✗ Redis: failed to start${NC}"
        exit 1
    fi
fi

# Postgres
if $VENV/python -c "
import os, psycopg2
url = os.environ.get('DATABASE_URL_SYNC', '').replace('postgresql+psycopg2://', 'postgresql://')
conn = psycopg2.connect(url, connect_timeout=3)
conn.close()
print('ok')
" 2>/dev/null | grep -q ok; then
    echo -e "${GREEN}  ✓ Postgres: connected${NC}"
else
    echo -e "${RED}  ✗ Postgres: cannot connect${NC}"
    exit 1
fi

# ── 1. Run migrations ──
echo -e "${YELLOW}[1/5] Running migrations...${NC}"
$VENV/alembic upgrade head 2>&1 | tail -3
echo -e "${GREEN}  ✓ Migrations up to date${NC}"

# ── 2. Run tests ──
if [ "$1" = "--restart" ]; then
    echo -e "${YELLOW}[2/5] Skipping tests (--restart)${NC}"
else
    echo -e "${YELLOW}[2/5] Running tests...${NC}"
    RESULT=$($VENV/pytest tests/unit -q 2>&1 | tail -1)
    if echo "$RESULT" | grep -q "passed"; then
        echo -e "${GREEN}  ✓ $RESULT${NC}"
    else
        echo -e "${RED}  ✗ Tests failed:${NC}"
        $VENV/pytest tests/unit -q 2>&1 | tail -10
        exit 1
    fi

    if [ "$1" = "--test-only" ]; then
        echo ""
        echo -e "${GREEN}Tests passed. Exiting (--test-only).${NC}"
        exit 0
    fi
fi

# ── 3. Stop old process ──
echo -e "${YELLOW}[3/5] Stopping old process...${NC}"
if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    kill "$(cat $PID_FILE)" 2>/dev/null
    sleep 2
    echo -e "${GREEN}  ✓ Old process stopped${NC}"
else
    # Also try to kill by port
    fuser -k 3004/tcp 2>/dev/null || true
    echo -e "${GREEN}  ✓ No old process (clean start)${NC}"
fi

# ── 4. Start app ──
echo -e "${YELLOW}[4/5] Starting ragbot-api...${NC}"
# P25-A6: 2 uvicorn workers (each its own event loop) — doubles HTTP
# ingress capacity. --limit-concurrency 200 caps per-worker in-flight
# requests to prevent event loop starvation under spike. 30s
# graceful-shutdown lets in-flight chats drain before SIGKILL.
nohup $VENV/uvicorn ragbot.interfaces.http.app:app \
    --host 0.0.0.0 \
    --port 3004 \
    --workers 2 \
    --limit-concurrency 200 \
    --timeout-keep-alive 30 \
    --timeout-graceful-shutdown 30 \
    --log-level info \
    --access-log \
    >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo -e "${GREEN}  ✓ PID: $(cat $PID_FILE)${NC}"
echo -e "${GREEN}  ✓ Log: $LOG_FILE${NC}"

# ── 5. Wait for ready ──
echo -e "${YELLOW}[5/5] Waiting for API...${NC}"
sleep 3  # wait for lifespan init (Redis Streams + DB bootstrap)
READY=""
for i in $(seq 1 15); do
    READY=$(curl -s http://localhost:3004/ready 2>/dev/null || true)
    if echo "$READY" | grep -q '"status"' 2>/dev/null; then
        STATUS=$(echo "$READY" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
        DEPS=$(echo "$READY" | python3 -c "import sys,json; d=json.load(sys.stdin)['dependencies']; print(' '.join(f'{k}={v}' for k,v in d.items()))" 2>/dev/null)
        if [ "$STATUS" = "ok" ]; then
            echo -e "${GREEN}  ✓ API ready ($DEPS)${NC}"
        else
            echo -e "${YELLOW}  ⚠ API degraded ($DEPS)${NC}"
        fi
        break
    fi
    sleep 1
done

if ! echo "$READY" | grep -q '"status"'; then
    echo -e "${RED}  ✗ API failed to start. Check: tail -50 $LOG_FILE${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Deploy complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo -e "  API:     http://localhost:3004"
echo -e "  Docs:    http://localhost:3004/docs"
echo -e "  Health:  http://localhost:3004/ready"
echo -e "  Log:     tail -f $LOG_FILE"
echo -e "  Stop:    kill \$(cat $PID_FILE)"
echo ""
