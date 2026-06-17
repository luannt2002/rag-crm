#!/usr/bin/env bash
# perf_baseline.sh — capture a 7-day p95 baseline snapshot.
#
# Wraps scripts/diagnose_p95_bottleneck.py with a fixed 168 h window and
# writes the JSON report to reports/perf_baseline_<TIMESTAMP>.json.
#
# Use before/after a deploy or sysprompt change to compare p95 deltas.
#
# Usage:
#   set -a && source .env && set +a   # if .env not auto-loaded
#   scripts/perf_baseline.sh
set -euo pipefail

if [ ! -f .env ]; then
  echo "[perf_baseline] .env not found in cwd" >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
source .env
set +a

TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p reports
OUT="reports/perf_baseline_${TS}.json"

python scripts/diagnose_p95_bottleneck.py \
  --hours 168 \
  --top 30 \
  --top-bots 20 \
  --json-out "$OUT"

echo "[perf_baseline] written: $OUT"
