#!/usr/bin/env bash
# 02_sysprompt_loosen.sh — DB UPDATE bots.system_prompt from operator file.
#
# ⚠️ DEPRECATED — VI PHẠM CLAUDE.md Application MINDSET rule 7 (2026-05-25).
# psql UPDATE vào bots.system_prompt = out-of-band drift, không reproduce
# được khi clone DB, không có audit_log trail, không rollback được. Lịch sử:
# Wave M3.6-L2 (2026-05-20) ship sysprompt qua script này → bug K1
# "1tr499 có những dịch vụ nào" 2026-05-25 vì DB content không sync với
# alembic head. Đường đi mới:
#   - Bot owner edit qua admin UI (audit_log trail tự động).
#   - Hoặc alembic migration tracked trong git (rare, chỉ cho seed).
# Script này GIỮ làm reference cho emergency rollback only; KHÔNG chạy trừ
# khi có incident yêu cầu rollback nhanh bằng văn bản.
#
# CLAUDE.md app-mindset rule (legacy):
#   - Application reads bots.system_prompt; NEVER injects.
#   - Sysprompt CONTENT belongs to the bot owner. This script is operator-side
#     plumbing only; the new content lives in a file the operator controls
#     (RAGBOT_SYSPROMPT_PATH env var). NO sysprompt body in this script.
#
# Steps:
#   1. Load env (3-key + DB DSN + sysprompt file path)
#   2. Resolve 3-key -> record_bot_id via psql
#   3. Backup current bots.system_prompt to /tmp/sysprompt_backup_<ts>.txt
#   4. UPDATE bots.system_prompt = file content WHERE 3-key match
#   5. Verify rowcount == 1
#   6. Bust 2 Redis cache keys (registry + sysprompt)
#   7. Log new char_length
#
# Idempotent: re-running with same file = same content (UPDATE no-op'ish — bumps updated_at).
# Domain-neutral: zero brand/sysprompt content in this script.
#
# Required env:
#   LOADTEST_BOT_ID, LOADTEST_TENANT_ID, LOADTEST_CHANNEL_TYPE
#   DATABASE_URL_SYNC (or DATABASE_URL)
#   RAGBOT_SYSPROMPT_PATH — path to file containing the NEW system_prompt text
#   REDIS_URL (for cache bust; default redis://localhost:6379/0)
#
# Optional env:
#   PYTHON_BIN  (default ./.venv/bin/python3)
#   REDIS_CLI_BIN (default redis-cli)
#   OWNER_ACTION_LOG_DIR (default /var/log/ragbot)
#   SYSPROMPT_BACKUP_DIR (default /tmp)
#
# Usage:
#   scripts/owner_action/02_sysprompt_loosen.sh [--dry-run]
#
# Exit codes:
#   0 success
#   1 precondition failure (env / file / DB)
#   2 3-key did not resolve
#   3 UPDATE rowcount != 1

set -euo pipefail

cd "$(dirname "$0")/../.."

DRY_RUN="false"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="true" ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown arg: $arg" >&2; exit 1 ;;
    esac
done

: "${LOADTEST_BOT_ID:?LOADTEST_BOT_ID env REQUIRED}"
: "${LOADTEST_TENANT_ID:?LOADTEST_TENANT_ID env REQUIRED (int)}"
: "${LOADTEST_CHANNEL_TYPE:?LOADTEST_CHANNEL_TYPE env REQUIRED}"
: "${RAGBOT_SYSPROMPT_PATH:?RAGBOT_SYSPROMPT_PATH env REQUIRED — path to new sysprompt text file}"

PY="${PYTHON_BIN:-./.venv/bin/python3}"
REDIS_CLI="${REDIS_CLI_BIN:-redis-cli}"
LOG_DIR="${OWNER_ACTION_LOG_DIR:-/var/log/ragbot}"
BACKUP_DIR="${SYSPROMPT_BACKUP_DIR:-/tmp}"
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR" "$BACKUP_DIR"
LOG_FILE="${LOG_DIR}/owner_action_02_sysprompt_${TS}.log"

log() {
    printf '%s | 02_sysprompt | %s\n' "$(date -Is)" "$1" | tee -a "$LOG_FILE"
}

log "START dry_run=${DRY_RUN} bot_id=${LOADTEST_BOT_ID} tenant=${LOADTEST_TENANT_ID} channel=${LOADTEST_CHANNEL_TYPE}"

if [[ ! -f "$RAGBOT_SYSPROMPT_PATH" ]]; then
    log "ERROR sysprompt_file_not_found path=${RAGBOT_SYSPROMPT_PATH}"
    exit 1
fi
NEW_SYSPROMPT_CHARS=$(wc -c < "$RAGBOT_SYSPROMPT_PATH" | tr -d ' ')
log "OK new_sysprompt_loaded path=${RAGBOT_SYSPROMPT_PATH} chars=${NEW_SYSPROMPT_CHARS}"

DSN="${DATABASE_URL_SYNC:-${DATABASE_URL:-}}"
if [[ -z "$DSN" ]]; then
    log "ERROR db_dsn_missing — set DATABASE_URL_SYNC or DATABASE_URL"
    exit 1
fi

# Resolve 3-key + read current sysprompt for backup
export DSN
RESOLVE_OUT=$("$PY" - <<'PY' 2>>"$LOG_FILE"
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
    "SELECT id::text, coalesce(system_prompt,'') "
    "FROM bots WHERE tenant_id=%s AND bot_id=%s AND channel_type=%s "
    "  AND is_deleted=false",
    (int(os.environ["LOADTEST_TENANT_ID"]),
     os.environ["LOADTEST_BOT_ID"],
     os.environ["LOADTEST_CHANNEL_TYPE"]),
)
row = cur.fetchone()
if not row:
    print("RESOLVE_FAIL", file=sys.stderr)
    sys.exit(2)
record_bot_id, current_sp = row
# stdout: 1st line = record_bot_id, then a marker, then full current sysprompt
sys.stdout.write(record_bot_id + "\n---SYSPROMPT-BEGIN---\n")
sys.stdout.write(current_sp)
PY
) || RC=$?
RC="${RC:-0}"

if [[ "$RC" -eq 2 ]]; then
    log "ERROR bot_3key_unresolved"
    exit 2
fi
if [[ "$RC" -ne 0 ]]; then
    log "ERROR resolve_query_failed rc=$RC"
    exit 1
fi

RECORD_BOT_ID="$(printf '%s' "$RESOLVE_OUT" | head -n 1)"
CURRENT_SP="$(printf '%s' "$RESOLVE_OUT" | sed -n '/^---SYSPROMPT-BEGIN---$/,$p' | tail -n +2)"
CURRENT_CHARS="$(printf '%s' "$CURRENT_SP" | wc -c | tr -d ' ')"
log "OK bot_resolved record_bot_id=${RECORD_BOT_ID} current_sysprompt_chars=${CURRENT_CHARS}"

# Backup current sysprompt
BACKUP_FILE="${BACKUP_DIR}/sysprompt_backup_${LOADTEST_TENANT_ID}_${LOADTEST_BOT_ID}_${LOADTEST_CHANNEL_TYPE}_${TS}.txt"
printf '%s' "$CURRENT_SP" > "$BACKUP_FILE"
log "OK backup_written path=${BACKUP_FILE} chars=${CURRENT_CHARS}"

if [[ "$DRY_RUN" == "true" ]]; then
    log "DRY-RUN — would UPDATE bots.system_prompt to ${NEW_SYSPROMPT_CHARS} chars and bust Redis cache"
    log "DRY-RUN summary: backup=${BACKUP_FILE}"
    exit 0
fi

# Apply UPDATE — parameterised, scoped by 3-key, returns rowcount
export RECORD_BOT_ID
export NEW_SP_PATH="$RAGBOT_SYSPROMPT_PATH"
ROWCOUNT=$("$PY" - <<'PY' 2>>"$LOG_FILE"
import os, sys
dsn = os.environ["DSN"]
if dsn.startswith("postgresql+psycopg2://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg2://"):]
elif dsn.startswith("postgresql+asyncpg://"):
    dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
import psycopg2
with open(os.environ["NEW_SP_PATH"], "r", encoding="utf-8") as f:
    new_sp = f.read()
conn = psycopg2.connect(dsn)
conn.autocommit = False
cur = conn.cursor()
cur.execute(
    "UPDATE bots SET system_prompt=%s, updated_at=now() "
    "WHERE tenant_id=%s AND bot_id=%s AND channel_type=%s "
    "  AND is_deleted=false",
    (new_sp,
     int(os.environ["LOADTEST_TENANT_ID"]),
     os.environ["LOADTEST_BOT_ID"],
     os.environ["LOADTEST_CHANNEL_TYPE"]),
)
print(cur.rowcount)
conn.commit()
PY
) || { log "ERROR update_failed"; exit 1; }

log "INFO update_rowcount=${ROWCOUNT}"
if [[ "$ROWCOUNT" -ne 1 ]]; then
    log "ERROR update_rowcount_not_1 (${ROWCOUNT}) — investigate; rollback uses ${BACKUP_FILE}"
    exit 3
fi

# Bust Redis cache — 2 keys per CLAUDE.md naming:
#   ragbot:bot:<tenant_id>:<bot_id>:<channel_type>   (BotConfig JSON)
#   ragbot:sysprompt:<record_bot_id>                  (resolved sysprompt cache)
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
KEY_REGISTRY="ragbot:bot:${LOADTEST_TENANT_ID}:${LOADTEST_BOT_ID}:${LOADTEST_CHANNEL_TYPE}"
KEY_SYSPROMPT="ragbot:sysprompt:${RECORD_BOT_ID}"

if command -v "$REDIS_CLI" >/dev/null 2>&1; then
    set +e
    "$REDIS_CLI" -u "$REDIS_URL" DEL "$KEY_REGISTRY" >>"$LOG_FILE" 2>&1
    R1=$?
    "$REDIS_CLI" -u "$REDIS_URL" DEL "$KEY_SYSPROMPT" >>"$LOG_FILE" 2>&1
    R2=$?
    set -e
    log "INFO redis_bust registry_rc=${R1} sysprompt_rc=${R2}"
else
    log "WARN redis_cli_not_found — caches will expire on TTL; consider manual DEL"
fi

log "DONE sysprompt updated chars=${NEW_SYSPROMPT_CHARS} backup=${BACKUP_FILE}"
exit 0
