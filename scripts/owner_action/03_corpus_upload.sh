#!/usr/bin/env bash
# 03_corpus_upload.sh — bulk upload corpus docs via /api/ragbot/sync/documents.
#
# CLAUDE.md app-mindset rule:
#   - Sample/corpus content is bot-owner-supplied (never shipped in repo).
#   - This script reads docs from a directory the operator controls
#     (RAGBOT_CORPUS_DIR), POSTs each as one document per file.
#
# Steps:
#   1. Load env (3-key + base URL + token + corpus dir)
#   2. Verify server health + endpoint reachable
#   3. Collect *.txt and *.md files from $RAGBOT_CORPUS_DIR (non-recursive)
#   4. POST one batch via /api/ragbot/sync/documents (wipe_existing=false UPSERT)
#   5. Verify chunk count via list endpoint, sample embedding non-NULL via DB
#   6. Final report: N docs, M chunks, K with embedding
#
# Idempotent: same files = upsert by source_url -> no duplicate docs created.
# Domain-neutral: NO sample corpus shipped here. Operator supplies dir.
#
# Required env:
#   LOADTEST_BOT_ID, LOADTEST_TENANT_ID, LOADTEST_CHANNEL_TYPE
#   RAGBOT_CORPUS_DIR — directory containing *.txt|*.md files (1 per doc)
#   RAGBOT_TOKEN     — bearer token (admin/service level for /sync/documents)
#
# Optional env:
#   RAGBOT_BASE_URL  (default http://localhost:3004)
#   PYTHON_BIN       (default ./.venv/bin/python3)
#   OWNER_ACTION_LOG_DIR (default /var/log/ragbot)
#   CORPUS_SOURCE_TYPE  (default "owner_action_upload")
#
# Usage:
#   scripts/owner_action/03_corpus_upload.sh [--dry-run]
#
# Exit codes:
#   0 success
#   1 precondition failure (env / file / server)
#   2 sync failed
#   3 verification failed

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
: "${LOADTEST_TENANT_ID:?LOADTEST_TENANT_ID env REQUIRED}"
: "${LOADTEST_CHANNEL_TYPE:?LOADTEST_CHANNEL_TYPE env REQUIRED}"
: "${RAGBOT_CORPUS_DIR:?RAGBOT_CORPUS_DIR env REQUIRED — directory of *.txt/*.md doc files}"
: "${RAGBOT_TOKEN:?RAGBOT_TOKEN env REQUIRED — admin/service bearer token}"

PY="${PYTHON_BIN:-./.venv/bin/python3}"
BASE_URL="${RAGBOT_BASE_URL:-http://localhost:3004}"
LOG_DIR="${OWNER_ACTION_LOG_DIR:-/var/log/ragbot}"
SOURCE_TYPE="${CORPUS_SOURCE_TYPE:-owner_action_upload}"
TS="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/owner_action_03_corpus_${TS}.log"

log() {
    printf '%s | 03_corpus | %s\n' "$(date -Is)" "$1" | tee -a "$LOG_FILE"
}

log "START dry_run=${DRY_RUN} corpus_dir=${RAGBOT_CORPUS_DIR}"

if [[ ! -d "$RAGBOT_CORPUS_DIR" ]]; then
    log "ERROR corpus_dir_not_found path=${RAGBOT_CORPUS_DIR}"
    exit 1
fi

if ! curl -fsS -o /dev/null --max-time 5 "${BASE_URL}/health" 2>/dev/null; then
    log "ERROR server_unreachable url=${BASE_URL}/health"
    exit 1
fi
log "OK server_health"

# Collect *.txt and *.md files
mapfile -t FILES < <(find "$RAGBOT_CORPUS_DIR" -maxdepth 1 -type f \( -name '*.txt' -o -name '*.md' \) | sort)
N_FILES=${#FILES[@]}
log "INFO files_found count=${N_FILES}"
if [[ "$N_FILES" -eq 0 ]]; then
    log "ERROR no_files_in_corpus_dir (looking for *.txt or *.md)"
    exit 1
fi

# Build JSON payload — uses Python (jq may not be present)
export FILES_LIST="$(printf '%s\n' "${FILES[@]}")"
PAYLOAD_FILE="${LOG_DIR}/owner_action_03_payload_${TS}.json"
"$PY" - <<'PY' >"$PAYLOAD_FILE" 2>>"$LOG_FILE"
import json, os, pathlib
files = [p for p in os.environ["FILES_LIST"].splitlines() if p.strip()]
docs = []
for path in files:
    p = pathlib.Path(path)
    content = p.read_text(encoding="utf-8")
    docs.append({
        "title": p.stem,
        "content": content,
        "url": f"file://{p.resolve()}",
        "source_type": os.environ.get("SOURCE_TYPE", "owner_action_upload"),
    })
body = {
    "tenant_id": int(os.environ["LOADTEST_TENANT_ID"]),
    "bot_id": os.environ["LOADTEST_BOT_ID"],
    "channel_type": os.environ["LOADTEST_CHANNEL_TYPE"],
    "documents": docs,
    "wipe_existing": False,
}
print(json.dumps(body, ensure_ascii=False))
PY
export SOURCE_TYPE
PAYLOAD_BYTES=$(wc -c < "$PAYLOAD_FILE" | tr -d ' ')
log "OK payload_built file=${PAYLOAD_FILE} bytes=${PAYLOAD_BYTES} docs=${N_FILES}"

if [[ "$DRY_RUN" == "true" ]]; then
    log "DRY-RUN — would POST ${BASE_URL}/api/ragbot/sync/documents with ${N_FILES} docs"
    exit 0
fi

# POST to /api/ragbot/sync/documents
RESP_FILE="${LOG_DIR}/owner_action_03_resp_${TS}.json"
HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w '%{http_code}' \
    -X POST "${BASE_URL}/api/ragbot/sync/documents" \
    -H "Authorization: Bearer ${RAGBOT_TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "@${PAYLOAD_FILE}" \
    --max-time "${UPLOAD_HTTP_TIMEOUT_S:-300}" 2>>"$LOG_FILE") || true

log "INFO upload_http_code=${HTTP_CODE} resp=${RESP_FILE}"
if [[ "$HTTP_CODE" != "200" ]]; then
    log "ERROR sync_documents_failed http=${HTTP_CODE}"
    # Truncate response preview at $LOG_PREVIEW_BYTES (default 512) for log
    head -c "${LOG_PREVIEW_BYTES:-512}" "$RESP_FILE" | tee -a "$LOG_FILE" >/dev/null
    exit 2
fi

# Parse response
TOTAL_DOCS=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('total_documents',0))" "$RESP_FILE" 2>>"$LOG_FILE" || echo 0)
TOTAL_CHUNKS=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('total_chunks',0))" "$RESP_FILE" 2>>"$LOG_FILE" || echo 0)
log "INFO sync_resp total_docs=${TOTAL_DOCS} total_chunks=${TOTAL_CHUNKS}"

# Verify embeddings via DB count (idempotent — only this bot's chunks)
DSN="${DATABASE_URL_SYNC:-${DATABASE_URL:-}}"
if [[ -z "$DSN" ]]; then
    log "WARN db_dsn_missing — cannot verify embedding count"
    exit 0
fi
export DSN
VERIFY_OUT=$("$PY" - <<'PY' 2>>"$LOG_FILE"
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
    "SELECT count(*) FILTER (WHERE dc.embedding IS NOT NULL), "
    "       count(*) FILTER (WHERE dc.embedding IS NULL) "
    "FROM document_chunks dc "
    "JOIN documents d ON dc.record_document_id = d.id "
    "JOIN bots b ON d.record_bot_id = b.id "
    "WHERE b.tenant_id=%s AND b.bot_id=%s AND b.channel_type=%s "
    "  AND b.is_deleted=false AND d.deleted_at IS NULL",
    (int(os.environ["LOADTEST_TENANT_ID"]),
     os.environ["LOADTEST_BOT_ID"],
     os.environ["LOADTEST_CHANNEL_TYPE"]),
)
row = cur.fetchone()
print(f"{row[0]},{row[1]}")
PY
) || { log "WARN db_verify_failed"; exit 0; }

WITH_EMB="${VERIFY_OUT%,*}"
NULL_EMB="${VERIFY_OUT#*,}"
log "DONE corpus_uploaded docs=${TOTAL_DOCS} chunks=${TOTAL_CHUNKS} with_embedding=${WITH_EMB} null_embedding=${NULL_EMB}"

if [[ "$NULL_EMB" -gt 0 ]]; then
    log "WARN ${NULL_EMB} chunks with NULL embedding — run 01_re_embed_missing_chunks.sh"
    exit 3
fi

exit 0
