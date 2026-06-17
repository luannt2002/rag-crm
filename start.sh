#!/bin/bash
cd /var/www/html/ragbot
set -a && source .env 2>/dev/null && set +a

# P25-A6: 2 uvicorn workers (each its own event loop) — doubles HTTP
# ingress capacity. --limit-concurrency 200 caps per-worker in-flight
# requests to prevent event loop starvation under spike. 30s
# graceful-shutdown lets in-flight chats drain before SIGKILL.
exec .venv/bin/uvicorn ragbot.interfaces.http.app:app \
  --host 0.0.0.0 \
  --port 3004 \
  --workers 2 \
  --limit-concurrency 200 \
  --timeout-keep-alive 30 \
  --timeout-graceful-shutdown 30 \
  --log-level info
