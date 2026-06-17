#!/usr/bin/env bash
# 01_re_embed_missing_chunks.sh — operator-runnable re-embed for NULL-vector chunks.
#
# Purpose:
#   When document_chunks.embedding IS NULL across a tenant, vector retrieval is
#   dead (BM25-only path). This wraps scripts/reembed_null_chunks.py with:
#     - 3-key identity preconditions (LOADTEST_BOT_ID/TENANT_ID/CHANNEL_TYPE)
#     - server + DB reachability check
#     - resolves bot 3-key -> record_bot_id via psql
#     - dry-run count + apply phase
#     - structured log to /var/log/ragbot/owner_action_<ts>.log
#
# Idempotent: re-running on already-embedded chunks is a no-op (count=0 -> exit 0).
# Domain-neutral: bot identity only from env. NO bot/tenant/brand literals here.
#
# Required env:
#   LOADTEST_BOT_ID, LOADTEST_TENANT_ID, LOADTEST_CHANNEL_TYPE
#   DATABASE_URL_SYNC (or DATABASE_URL) — for psql 3-key resolve
#   RAGBOT_BASE_URL (default http://localhost:3004) — server health probe
#
# Optional env:
#   PYTHON_BIN  (default ./.venv/bin/python3)
#   REEMBED_BATCH_SIZE (default 32)
#   OWNER_ACTION_LOG_DIR (default /var/log/ragbot)
#
# Usage:
#   scripts/owner_action/01_re_embed_missing_chunks.sh [--dry-run] [--apply]
#
# Exit codes:
#   0  success (count=0 OR re-embed all OK)
#   1  precondition failure (env missing / server down / DB unreachable)
#   2  bot 3-key did not resolve
#   3  re-embed apply phase had failures

set -euo pipefail

cd "$(dirname "$0")/../.."

# --- args --------------------------------------------------------------------
MODE="apply"
for arg in "$@"; do
    case "$arg" in
        --dry-run) MODE="dry-run" ;;
        --apply)   MODE="apply" ;;
        -h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *)
            echo "ERROR: unknown arg: $arg" >&2; exit 1 ;;
    esac
done

# --- preconditions -----------------------------------------------------------
: "${LOADTEST_BOT_ID:?LOADTEST_BOT_ID env REQUIRED (bot slug)}"
: "${LOADTEST_TENANT_ID:?LOADTEST_TENANT_ID env REQUIRED (int)}"
: "${LOADTEST_CHANNEL_TYPE:?LOADTEST_CHANNEL_TYPE env REQUIRED}"

PY="${PYTHON_BIN:-./.venv/bin/python3}"
BASE_URL="${RAGBOT_BASE_URL:-http://localhost:3004}"
BATCH_SIZE="${REEMBED_BATCH_SIZE:-32}"
LOG_DIR="${OWNER_ACTION_LOG_DIR:-/var/log/ragbot}"
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/owner_action_01_reembed_${TS}.log"

log() {
    local msg="$1"
    printf '%s | 01_reembed | %s\n' "$(date -Is)" "$msg" | tee -a "$LOG_FILE"
}

log "START mode=${MODE} bot_id=${LOADTEST_BOT_ID} tenant_id=${LOADTEST_TENANT_ID} channel=${LOADTEST_CHANNEL_TYPE}"

if [[ ! -x "$PY" ]]; then
    log "ERROR python_bin_not_executable path=$PY"
    exit 1
fi

# Server health
if ! curl -fsS -o /dev/null --max-time 5 "${BASE_URL}/health" 2>/dev/null; then
    log "ERROR server_unreachable url=${BASE_URL}/health"
    exit 1
fi
log "OK server_health url=${BASE_URL}/health"

# DB reachability + 3-key resolve to record_bot_id
DSN="${DATABASE_URL_SYNC:-${DATABASE_URL:-}}"
if [[ -z "$DSN" ]]; then
    log "ERROR db_dsn_missing — set DATABASE_URL_SYNC or DATABASE_URL"
    exit 1
fi

# Resolve record_bot_id via 3-key (NOT NULL on all 3) — pure psql, no Python.
# Uses python -c to drive psycopg2 from the same venv (avoids pinning psql binary).
export DSN
RESOLVE_OUT=$("$PY" - <<'PY' 2>>"$LOG_FILE"
import os, sys, urllib.parse
dsn = os.environ["DSN"]
# psycopg2 wants a libpq DSN — strip SQLAlchemy driver prefix
if dsn.startswith("postgresql+psycopg2://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg2://"):]
elif dsn.startswith("postgresql+asyncpg://"):
    dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
import psycopg2
conn = psycopg2.connect(dsn)
cur = conn.cursor()
cur.execute(
    "SELECT id::text FROM bots "
    "WHERE tenant_id=%s AND bot_id=%s AND channel_type=%s "
    "  AND is_deleted=false",
    (int(os.environ["LOADTEST_TENANT_ID"]),
     os.environ["LOADTEST_BOT_ID"],
     os.environ["LOADTEST_CHANNEL_TYPE"]),
)
row = cur.fetchone()
if not row:
    print("RESOLVE_FAIL", file=sys.stderr)
    sys.exit(2)
print(row[0])
PY
) || RC=$?
RC="${RC:-0}"

if [[ "$RC" -eq 2 ]]; then
    log "ERROR bot_3key_unresolved tenant=${LOADTEST_TENANT_ID} bot_id=${LOADTEST_BOT_ID} channel=${LOADTEST_CHANNEL_TYPE}"
    exit 2
fi
if [[ "$RC" -ne 0 ]] || [[ -z "$RESOLVE_OUT" ]]; then
    log "ERROR db_unreachable_or_resolve_failed rc=$RC"
    exit 1
fi
RECORD_BOT_ID="$RESOLVE_OUT"
log "OK bot_resolved record_bot_id=${RECORD_BOT_ID}"
export RECORD_BOT_ID

# Count NULL-embedding chunks scoped to this tenant
NULL_COUNT=$("$PY" - <<'PY' 2>>"$LOG_FILE"
import os, sys
dsn = os.environ["DSN"]
if dsn.startswith("postgresql+psycopg2://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg2://"):]
elif dsn.startswith("postgresql+asyncpg://"):
    dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
import psycopg2
conn = psycopg2.connect(dsn)
cur = conn.cursor()
cur.execute(
    "SELECT count(*) FROM document_chunks dc "
    "JOIN documents d ON dc.record_document_id = d.id "
    "JOIN bots b ON d.record_bot_id = b.id "
    "WHERE dc.embedding IS NULL "
    "  AND d.deleted_at IS NULL "
    "  AND b.id = %s",
    (os.environ["RECORD_BOT_ID"],),
)
print(cur.fetchone()[0])
PY
) || { log "ERROR count_failed"; exit 1; }

log "INFO null_embedding_chunks count=${NULL_COUNT}"

if [[ "$NULL_COUNT" -eq 0 ]]; then
    log "OK no_op_already_embedded — exit 0 (idempotent)"
    exit 0
fi

if [[ "$MODE" == "dry-run" ]]; then
    log "DRY-RUN — would re-embed ${NULL_COUNT} chunks for record_bot_id=${RECORD_BOT_ID}"
    "$PY" scripts/reembed_null_chunks.py --bot-uuid "$RECORD_BOT_ID" 2>&1 | tee -a "$LOG_FILE"
    exit 0
fi

# Apply phase — invokes the canonical Python script (does its own retry / batch).
log "APPLY invoking reembed_null_chunks.py --apply --bot-uuid=${RECORD_BOT_ID} batch=${BATCH_SIZE}"
if "$PY" scripts/reembed_null_chunks.py \
        --bot-uuid "$RECORD_BOT_ID" \
        --batch-size "$BATCH_SIZE" \
        --apply 2>&1 | tee -a "$LOG_FILE"; then
    log "OK reembed_apply_done"
else
    log "FAIL reembed_apply_partial — see log for failed batch detail"
    exit 3
fi

# Verify post-condition: NULL count should now be 0
NULL_AFTER=$("$PY" - <<'PY' 2>>"$LOG_FILE"
import os, sys
dsn = os.environ["DSN"]
if dsn.startswith("postgresql+psycopg2://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg2://"):]
elif dsn.startswith("postgresql+asyncpg://"):
    dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
import psycopg2
conn = psycopg2.connect(dsn)
cur = conn.cursor()
cur.execute(
    "SELECT count(*) FROM document_chunks dc "
    "JOIN documents d ON dc.record_document_id = d.id "
    "WHERE dc.embedding IS NULL AND d.deleted_at IS NULL "
    "  AND d.record_bot_id = %s",
    (os.environ["RECORD_BOT_ID"],),
)
print(cur.fetchone()[0])
PY
) || { log "ERROR post_count_failed"; exit 3; }

log "INFO post_apply_null_count=${NULL_AFTER}"
if [[ "$NULL_AFTER" -gt 0 ]]; then
    log "WARN ${NULL_AFTER} chunks still NULL after apply — investigate log"
    exit 3
fi

log "DONE all_chunks_embedded — exit 0"
exit 0
