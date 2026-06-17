#!/usr/bin/env bash
# run_all.sh — owner-action master script. Chains S1 -> S2 -> S3 -> S4.
#
# ⚠️ Step 1 (02_sysprompt_loosen.sh) is DEPRECATED per CLAUDE.md Application
# MINDSET rule 7 (2026-05-25). psql UPDATE vào bots.system_prompt = drift
# infra. Bot owners phải edit qua admin UI. The flag --skip-sysprompt is
# now the default-recommended path; pass --apply-sysprompt only for
# emergency rollback from an operator file.
#
# Order:
#   1. 02_sysprompt_loosen.sh   (DEPRECATED — only on --apply-sysprompt)
#   2. 03_corpus_upload.sh      (creates docs + chunks)
#   3. 01_re_embed_missing_chunks.sh  (sweep any NULL embeddings post-upload + legacy)
#   4. 04_smoke_after_action.sh (verify health + 5-question smoke)
#
# Flags:
#   --yes       skip confirm prompts (CI mode)
#   --dry-run   show steps + payloads without executing destructive actions
#   --skip-sysprompt
#   --skip-corpus
#   --skip-reembed
#   --skip-smoke
#
# Required env (forwarded to children):
#   LOADTEST_BOT_ID, LOADTEST_TENANT_ID, LOADTEST_CHANNEL_TYPE
#   DATABASE_URL_SYNC (or DATABASE_URL)
#   RAGBOT_TOKEN
#   RAGBOT_SYSPROMPT_PATH      (S2)
#   RAGBOT_CORPUS_DIR          (S3)
#
# Optional env:
#   RAGBOT_BASE_URL    (default http://localhost:3004)
#   OWNER_ACTION_LOG_DIR (default /var/log/ragbot)

set -euo pipefail

cd "$(dirname "$0")/../.."

YES="false"
DRY_RUN="false"
SKIP_SYSPROMPT="false"
SKIP_CORPUS="false"
SKIP_REEMBED="false"
SKIP_SMOKE="false"

for arg in "$@"; do
    case "$arg" in
        --yes) YES="true" ;;
        --dry-run) DRY_RUN="true" ;;
        --skip-sysprompt) SKIP_SYSPROMPT="true" ;;
        --skip-corpus)    SKIP_CORPUS="true" ;;
        --skip-reembed)   SKIP_REEMBED="true" ;;
        --skip-smoke)     SKIP_SMOKE="true" ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown arg: $arg" >&2; exit 1 ;;
    esac
done

: "${LOADTEST_BOT_ID:?LOADTEST_BOT_ID env REQUIRED}"
: "${LOADTEST_TENANT_ID:?LOADTEST_TENANT_ID env REQUIRED}"
: "${LOADTEST_CHANNEL_TYPE:?LOADTEST_CHANNEL_TYPE env REQUIRED}"
: "${RAGBOT_TOKEN:?RAGBOT_TOKEN env REQUIRED (admin/service bearer)}"

LOG_DIR="${OWNER_ACTION_LOG_DIR:-/var/log/ragbot}"
TS="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${LOG_DIR}/owner_action_${TS}"
mkdir -p "$RUN_DIR"
export OWNER_ACTION_LOG_DIR="$RUN_DIR"
LOG_FILE="${RUN_DIR}/run_all.log"

log() {
    printf '%s | run_all | %s\n' "$(date -Is)" "$1" | tee -a "$LOG_FILE"
}

log "START dry_run=${DRY_RUN} yes=${YES} run_dir=${RUN_DIR}"
log "IDENTITY tenant=${LOADTEST_TENANT_ID} bot_id=${LOADTEST_BOT_ID} channel=${LOADTEST_CHANNEL_TYPE}"
log "SKIP_FLAGS sysprompt=${SKIP_SYSPROMPT} corpus=${SKIP_CORPUS} reembed=${SKIP_REEMBED} smoke=${SKIP_SMOKE}"

confirm() {
    local prompt="$1"
    if [[ "$YES" == "true" || "$DRY_RUN" == "true" ]]; then
        log "AUTO-CONFIRM: ${prompt}"
        return 0
    fi
    printf '%s [y/N]: ' "$prompt"
    read -r ans
    case "$ans" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# Pre-flight
BASE_URL="${RAGBOT_BASE_URL:-http://localhost:3004}"
if ! curl -fsS -o /dev/null --max-time 5 "${BASE_URL}/health" 2>/dev/null; then
    log "ERROR server_unreachable url=${BASE_URL}/health"
    exit 1
fi
log "OK preflight_health"

DSN="${DATABASE_URL_SYNC:-${DATABASE_URL:-}}"
if [[ -z "$DSN" ]]; then
    log "ERROR db_dsn_missing"
    exit 1
fi
log "OK preflight_db_dsn"

OPTS=()
if [[ "$DRY_RUN" == "true" ]]; then
    OPTS+=("--dry-run")
fi

# ---- Step 1: sysprompt loosen ------------------------------------------------
if [[ "$SKIP_SYSPROMPT" != "true" ]]; then
    if [[ -z "${RAGBOT_SYSPROMPT_PATH:-}" ]]; then
        log "SKIP step_1_sysprompt — RAGBOT_SYSPROMPT_PATH unset"
    else
        if confirm "Step 1: UPDATE bots.system_prompt from ${RAGBOT_SYSPROMPT_PATH}?"; then
            log "RUN step_1_sysprompt"
            scripts/owner_action/02_sysprompt_loosen.sh "${OPTS[@]}" 2>&1 | tee -a "$LOG_FILE"
            STEP1_RC=${PIPESTATUS[0]}
            log "STEP1 rc=${STEP1_RC}"
            if [[ "$STEP1_RC" -ne 0 ]]; then
                log "FAIL step_1_sysprompt rc=${STEP1_RC} — aborting"
                exit "$STEP1_RC"
            fi
        else
            log "SKIP step_1_sysprompt — user declined"
        fi
    fi
fi

# ---- Step 2: corpus upload ---------------------------------------------------
if [[ "$SKIP_CORPUS" != "true" ]]; then
    if [[ -z "${RAGBOT_CORPUS_DIR:-}" ]]; then
        log "SKIP step_2_corpus — RAGBOT_CORPUS_DIR unset"
    else
        if confirm "Step 2: POST /api/ragbot/sync/documents from ${RAGBOT_CORPUS_DIR}?"; then
            log "RUN step_2_corpus"
            scripts/owner_action/03_corpus_upload.sh "${OPTS[@]}" 2>&1 | tee -a "$LOG_FILE"
            STEP2_RC=${PIPESTATUS[0]}
            log "STEP2 rc=${STEP2_RC}"
            if [[ "$STEP2_RC" -ne 0 ]] && [[ "$STEP2_RC" -ne 3 ]]; then
                # rc=3 means upload OK but some chunks NULL embedding — Step 3 will fix.
                log "FAIL step_2_corpus rc=${STEP2_RC} — aborting"
                exit "$STEP2_RC"
            fi
        else
            log "SKIP step_2_corpus — user declined"
        fi
    fi
fi

# ---- Step 3: re-embed sweep --------------------------------------------------
if [[ "$SKIP_REEMBED" != "true" ]]; then
    if confirm "Step 3: re-embed any NULL-embedding chunks?"; then
        log "RUN step_3_reembed"
        scripts/owner_action/01_re_embed_missing_chunks.sh "${OPTS[@]}" 2>&1 | tee -a "$LOG_FILE"
        STEP3_RC=${PIPESTATUS[0]}
        log "STEP3 rc=${STEP3_RC}"
        if [[ "$STEP3_RC" -ne 0 ]]; then
            log "FAIL step_3_reembed rc=${STEP3_RC} — continuing to smoke for diagnostic"
        fi
    else
        log "SKIP step_3_reembed — user declined"
    fi
fi

# ---- Step 4: smoke -----------------------------------------------------------
if [[ "$SKIP_SMOKE" != "true" ]]; then
    log "RUN step_4_smoke"
    scripts/owner_action/04_smoke_after_action.sh 2>&1 | tee -a "$LOG_FILE"
    STEP4_RC=${PIPESTATUS[0]}
    log "STEP4 rc=${STEP4_RC}"
    if [[ "$STEP4_RC" -ne 0 ]]; then
        log "FAIL step_4_smoke rc=${STEP4_RC}"
        exit "$STEP4_RC"
    fi
fi

log "DONE all steps OK run_dir=${RUN_DIR}"
exit 0
