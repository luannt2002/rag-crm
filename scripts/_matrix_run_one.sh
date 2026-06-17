#!/usr/bin/env bash
# Durable single-model capture+score loop. Runs under systemd transient service so
# it survives Claude-session idle-reaping. Worker is resume-safe → relaunch on death
# until the file reaches 120/120. Usage: _matrix_run_one.sh gpt-4.1
set -u
MODEL="${1:?model required}"
cd /var/www/html/ragbot
set -a && source .env 2>/dev/null; set +a
export PYTHONPATH=.
PY=/var/www/html/ragbot/.venv/bin/python
FILE="reports/MODEL_MATRIX_${MODEL}.json"
LOG="/tmp/matrix_${MODEL}.log"

done_count() { "$PY" -c "import json;print(sum(len(d['questions']) for d in json.load(open('$FILE'))['documents']))" 2>/dev/null || echo 0; }

echo "[$(date +%T)] START durable loop for $MODEL" | tee -a "$LOG"
for attempt in $(seq 1 200); do
  N=$(done_count)
  if [ "$N" -ge 120 ]; then echo "[$(date +%T)] $MODEL COMPLETE $N/120" | tee -a "$LOG"; break; fi
  echo "[$(date +%T)] attempt $attempt — $N/120" | tee -a "$LOG"
  "$PY" -u scripts/capture_score_model.py --model "$MODEL" --sleep 0.4 >>"$LOG" 2>&1
  sleep 3
done
echo "[$(date +%T)] $MODEL loop EXIT $(done_count)/120" | tee -a "$LOG"
