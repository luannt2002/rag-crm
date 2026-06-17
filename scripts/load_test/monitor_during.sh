#!/usr/bin/env bash
# Sample DB pool, Redis, CPU/RAM every 5s into a CSV while load test runs.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT="${1:-reports/load_test/monitor_$(date +%Y%m%d_%H%M%S).csv}"
INTERVAL="${MONITOR_INTERVAL_S:-5}"
DURATION="${MONITOR_DURATION_S:-300}"

set -a; source .env; set +a
DSN="${DATABASE_URL_SYNC/postgresql+psycopg2/postgresql}"
PY=./.venv/bin/python3

end=$(( $(date +%s) + DURATION ))
echo "ts,db_active,db_idle,db_total,redis_used_memory_mb,cpu_user_pct,mem_used_pct" > "$OUT"

while [[ $(date +%s) -lt $end ]]; do
    ts=$(date -u +%FT%TZ)
    db=$("$PY" -c "
import psycopg2
c = psycopg2.connect('''$DSN''')
cur = c.cursor()
cur.execute(\"SELECT count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE state='idle'), count(*) FROM pg_stat_activity WHERE datname='ragbot_v2_dev'\")
r = cur.fetchone()
print(f'{r[0]},{r[1]},{r[2]}')
" 2>/dev/null || echo "0,0,0")

    redis_mem=$(redis-cli -h 127.0.0.1 INFO memory 2>/dev/null \
        | awk -F: '/^used_memory:/{printf "%.2f", $2/1048576}')
    [[ -z "$redis_mem" ]] && redis_mem=0

    cpu_user=$(top -bn1 | awk '/Cpu\(s\)/{gsub("%","",$2); print $2; exit}')
    [[ -z "$cpu_user" ]] && cpu_user=0

    mem_used=$(free | awk '/^Mem:/{printf "%.1f", ($3/$2)*100}')
    [[ -z "$mem_used" ]] && mem_used=0

    echo "$ts,$db,$redis_mem,$cpu_user,$mem_used" >> "$OUT"
    sleep "$INTERVAL"
done
echo "monitor done -> $OUT"
