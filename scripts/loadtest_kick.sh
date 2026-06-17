#!/usr/bin/env bash
# Stream Y — fire-and-forget load-test runner.
#
# Runs agent_d_loadtest.py (or any long script) in background with nohup,
# writes PID + status + stderr files, and exits immediately so the caller
# (interactive Claude session, cron, CI) does not waste tokens watching
# the process for ~30 minutes.
#
# Usage:
#   bash scripts/loadtest_kick.sh agent_d_loadtest.py --bot-id X --tenant-id 32 \
#        --channel-type web --questions-file fixtures/90q.md
#   bash scripts/loadtest_kick.sh reclassify_loadtest.py --input X.json --output Y.md
#
# Outputs (under reports/_async/):
#   loadtest_<timestamp>.pid         PID of background process
#   loadtest_<timestamp>.status      "running" | "done" | "error: <msg>"
#   loadtest_<timestamp>.stderr      stderr capture
#   loadtest_<timestamp>.stdout      stdout capture
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASYNC_DIR="$REPO_ROOT/reports/_async"
mkdir -p "$ASYNC_DIR"

if [ $# -lt 1 ]; then
  echo "usage: bash scripts/loadtest_kick.sh <script_under_scripts/> [args...]" >&2
  echo "       writes PID + status to $ASYNC_DIR/loadtest_<ts>.{pid,status,stderr,stdout}" >&2
  exit 1
fi

SCRIPT="$1"
shift

if [ ! -f "$REPO_ROOT/scripts/$SCRIPT" ]; then
  echo "[kick] script not found: scripts/$SCRIPT" >&2
  exit 2
fi

TS="$(date +%Y%m%d_%H%M%S)"
TAG="${SCRIPT%.py}_$TS"
PID_FILE="$ASYNC_DIR/${TAG}.pid"
STATUS_FILE="$ASYNC_DIR/${TAG}.status"
OUT_FILE="$ASYNC_DIR/${TAG}.stdout"
ERR_FILE="$ASYNC_DIR/${TAG}.stderr"

# Prefer the project venv if it exists (consistent dep set).
PY_BIN="python3"
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY_BIN="$REPO_ROOT/.venv/bin/python"
fi

echo "running" > "$STATUS_FILE"

# Wrap the actual run so we can flip status to done/error after the
# foreground command exits inside the background subshell.
nohup bash -c "
  cd '$REPO_ROOT'
  '$PY_BIN' 'scripts/$SCRIPT' $*
  rc=\$?
  if [ \$rc -eq 0 ]; then
    echo 'done' > '$STATUS_FILE'
  else
    echo \"error: exit \$rc\" > '$STATUS_FILE'
  fi
" > "$OUT_FILE" 2> "$ERR_FILE" &

PID=$!
echo "$PID" > "$PID_FILE"

cat <<EOF
[kick] launched scripts/$SCRIPT in background
       PID:    $PID                      (stop: kill \$(cat $PID_FILE))
       status: $STATUS_FILE
       stdout: $OUT_FILE
       stderr: $ERR_FILE

Read result later:
       python scripts/read_loadtest_result.py --latest
       python scripts/read_loadtest_result.py --tag $TAG
EOF
