#!/usr/bin/env bash
# devstack.sh — dev infrastructure control for ragbot (redis + postgres + server).
# Management only — never weakens security; the redis protected-mode fix is
# printed for the operator to run explicitly (it is a network-ACL decision).
#
# Usage: scripts/devstack.sh {status|health|server-start|server-stop|server-restart|redis-fix-help|logs}
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
set -a; source .env 2>/dev/null; set +a
PORT=3004
REDIS_HOSTPORT="${REDIS_URL##*:}"; REDIS_HOSTPORT="${REDIS_HOSTPORT%%/*}"  # 6380
LOG=/tmp/ragbot_server.log

_redis_ping() {
  .venv/bin/python - <<'PY' 2>/dev/null
import redis,os
try:
    print("OK" if redis.from_url(os.environ["REDIS_URL"],socket_connect_timeout=2).ping() else "NO")
except Exception as e: print(f"FAIL:{type(e).__name__}")
PY
}
_server_pid() { ss -ltnp 2>/dev/null | grep ":$PORT" | grep -oE "pid=[0-9]+" | head -1 | cut -d= -f2; }
_health() { curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/api/ragbot/health" 2>/dev/null; }

status() {
  echo "== containers =="
  docker ps --filter "name=rag-crm" --format "  {{.Names}}: {{.Status}} {{.Ports}}" 2>/dev/null
  echo "== redis (app sees localhost:$REDIS_HOSTPORT) =="
  echo "  ping: $(_redis_ping)"
  echo "== ragbot server =="
  local pid; pid=$(_server_pid)
  echo "  pid: ${pid:-DOWN} · health: $(_health)"
}
health() { status; }

server_stop() {
  local pid; pid=$(_server_pid)
  [ -n "$pid" ] && { kill -9 "$pid" 2>/dev/null; echo "killed $pid"; } || echo "no server on :$PORT"
}
server_start() {
  [ "$(_redis_ping)" = "OK" ] || { echo "REFUSING: redis not reachable (run 'redis-fix-help' first)"; exit 1; }
  nohup .venv/bin/python -m ragbot.main > "$LOG" 2>&1 &
  echo "started pid $!  (log: $LOG)"
  for i in $(seq 1 25); do sleep 3
    [ "$(_health)" = "401" ] && { echo "UP after ~$((i*3))s"; return 0; }
  done
  echo "TIMEOUT — tail $LOG"; tail -5 "$LOG"
}
server_restart() { server_stop; sleep 3; server_start; }

redis_fix_help() {
  cat <<EOF
redis is in protected-mode (denies the host->container port-forward connection).
This is a NETWORK ACL — choose ONE (operator runs it explicitly):

  [A] SECURE (recommended) — add auth, keep protected-mode:
      1) docker-compose: add  "--requirepass", "<STRONG_PW>"  to the redis command
      2) .env: REDIS_URL=redis://:<STRONG_PW>@localhost:$REDIS_HOSTPORT/0
      3) docker compose up -d --force-recreate redis
      (auth lets connections through regardless of protected-mode; nothing weakened)

  [B] DEV-QUICK — bind redis to loopback + disable protected-mode:
      1) docker-compose.override.yml ports: "127.0.0.1:$REDIS_HOSTPORT:6379"
      2) docker-compose command: add  "--protected-mode", "no"
      3) docker compose up -d --force-recreate redis
      (only acceptable because (1) makes redis non-routable externally)

  [C] RUNTIME (ephemeral, dev only) — lost on container restart:
      docker exec rag-crm-redis-1 redis-cli CONFIG SET protected-mode no
EOF
}

case "${1:-status}" in
  status) status ;;
  health) health ;;
  server-start) server_start ;;
  server-stop) server_stop ;;
  server-restart) server_restart ;;
  redis-fix-help) redis_fix_help ;;
  logs) tail -n "${2:-40}" "$LOG" 2>/dev/null ;;
  *) echo "usage: $0 {status|health|server-start|server-stop|server-restart|redis-fix-help|logs}"; exit 2 ;;
esac
