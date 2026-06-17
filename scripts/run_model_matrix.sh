#!/usr/bin/env bash
# Overnight 3-model matrix: nano/mini/full × 120 câu, throttled, per-model JSON.
# Restores production (gpt-4.1 full) on exit/error. Resume-safe (worker skips done bots).
set -u
cd /var/www/html/ragbot
set -a && source .env 2>/dev/null; set +a
LOG=/tmp/model_matrix.log
PROD_MODEL="gpt-4.1"   # committed production (alembic 0202)

restore() {
  echo "[$(date +%T)] RESTORE → $PROD_MODEL" | tee -a "$LOG"
  PYTHONPATH=. python scripts/set_answer_model.py "$PROD_MODEL" >>"$LOG" 2>&1 || true
  sudo systemctl restart ragbot-api >>"$LOG" 2>&1 || true
}
trap restore EXIT

clear_state() {
  python - <<'PY' >>"$LOG" 2>&1 || true
import asyncio,os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def m():
    e=create_async_engine(os.environ["DATABASE_URL"])
    async with e.begin() as c:
        for t in ["semantic_cache","request_steps","request_logs","messages","conversations"]:
            await c.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
    await e.dispose()
asyncio.run(m())
PY
  redis-cli FLUSHALL >/dev/null 2>&1 || true
}

wait_health() {
  for _ in $(seq 1 60); do curl -s -m 2 -o /dev/null http://localhost:3004/health 2>/dev/null && return 0; sleep 2; done
}

echo "[$(date +%T)] START model matrix" | tee -a "$LOG"
for MODEL in gpt-4.1-nano gpt-4.1-mini gpt-4.1; do
  # skip if this model file already complete (12 bots)
  N=$(python3 -c "import json;print(sum(len(d['questions']) for d in json.load(open('reports/MODEL_MATRIX_$MODEL.json'))['documents']))" 2>/dev/null || echo 0)
  if [ "$N" -ge 120 ]; then echo "[$(date +%T)] skip $MODEL (done $N/12)" | tee -a "$LOG"; continue; fi
  echo "[$(date +%T)] === $MODEL ===" | tee -a "$LOG"
  PYTHONPATH=. python scripts/set_answer_model.py "$MODEL" >>"$LOG" 2>&1
  sudo systemctl restart ragbot-api >>"$LOG" 2>&1
  wait_health
  clear_state
  sleep 5
  # worker is resume-safe; loop a few times in case it gets killed mid-run
  for attempt in $(seq 1 40); do
    N=$(python3 -c "import json;print(sum(len(d['questions']) for d in json.load(open('reports/MODEL_MATRIX_$MODEL.json'))['documents']))" 2>/dev/null || echo 0)
    [ "$N" -ge 120 ] && break
    echo "[$(date +%T)] $MODEL worker attempt $attempt ($N/12)" | tee -a "$LOG"
    PYTHONPATH=. python -u scripts/capture_score_model.py --model "$MODEL" --sleep 1.0 >>"$LOG" 2>&1
  done
  echo "[$(date +%T)] $MODEL done" | tee -a "$LOG"
done
echo "[$(date +%T)] MATRIX_DONE" | tee -a "$LOG"
