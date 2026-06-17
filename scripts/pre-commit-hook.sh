#!/usr/bin/env bash
# scripts/pre-commit-hook.sh — pre-commit guard.
#
# Detects 5 classes of CLAUDE.md violations on staged hot-path Python files
# (src/ragbot/**.py only — tests/, scripts/, alembic/, plans/, docs/ skipped):
#
#   1. Magic numbers outside constants.py / settings.py
#   2. Hardcoded model names (gpt-/claude-/jina-/cohere/...)
#   3. Brand literals (env-driven denylist via RAGBOT_BRAND_DENYLIST)
#   4. Inline intent strings outside DTO/constants/intent_*/tests
#   5. Broad `except Exception:` without `# noqa: BLE001` justification
#
# Install (per-clone, since .git/hooks is not tracked):
#
#   ln -sf "$PWD/scripts/pre-commit-hook.sh" .git/hooks/pre-commit
#   chmod +x scripts/pre-commit-hook.sh
#
# Bypass (emergency only — logs warning to stderr):
#
#   RAGBOT_PRECOMMIT_BYPASS=1 git commit ...
#
# Exit 0 = clean. Exit 1 = at least one violation; commit is aborted.

set -euo pipefail

# ---------------------------------------------------------------------------
# Bypass switch
# ---------------------------------------------------------------------------
if [ "${RAGBOT_PRECOMMIT_BYPASS:-0}" = "1" ]; then
    echo "WARN  ragbot pre-commit hook BYPASSED via RAGBOT_PRECOMMIT_BYPASS=1" >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Resolve repo root (works whether invoked by git or directly)
# ---------------------------------------------------------------------------
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Collect staged hot-path Python files (src/ragbot only)
# ---------------------------------------------------------------------------
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
    | grep -E '^src/ragbot/.*\.py$' || true)

if [ -z "$STAGED_FILES" ]; then
    # Nothing to check — invoked outside a commit or only non-hot-path changes.
    exit 0
fi

VIOLATIONS_FOUND=0
REPORT=""

# Helper: append a violation line to REPORT and bump the counter.
report_hit() {
    local file="$1"
    local line="$2"
    local rule="$3"
    local snippet="$4"
    REPORT="${REPORT}${file}:${line}  [${rule}]  ${snippet}"$'\n'
    VIOLATIONS_FOUND=$((VIOLATIONS_FOUND + 1))
}

# Helper: extract only the staged hunks for a file (avoid unstaged false-pos).
# We grep the *file on disk* but only against line numbers introduced/modified
# by this commit. Simpler: grep the whole file but allow staged content via
# `git show :FILE` (the staged blob).
staged_blob() {
    git show ":$1" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

# Numbers tracked in shared/constants.py. Inline use anywhere else = magic.
# Source: CLAUDE.md zero-hardcode self-verify section.
MAGIC_NUM_RE='\b(1024|256|500|450|1000|2000|3000|4000|5000|8000|300|60|30|512|768|1536|3072)\b'

# Model name literals must live in constants.py / settings.py only.
MODEL_NAME_RE='"(gpt-[0-9]|claude-[0-9]|claude-opus|claude-sonnet|claude-haiku|text-embedding-|jina-embeddings-|jina-reranker|cohere/rerank|llama-|gemini-)'

# Intent string literals — single-quoted to avoid f-string false-pos.
INTENT_STR_RE="'(factoid|chitchat|comparison|multi_hop|aggregation|out_of_scope|vu_vo|greeting)'"

# Broad-except pattern (must include `# noqa: BLE001` to be allowed).
# Matched against `nl`-numbered lines, so we anchor after the leading
# `<lineno>\t` rather than at start-of-line.
BROAD_EXCEPT_RE='[[:space:]]*except[[:space:]]+Exception([[:space:]]+as[[:space:]]+[A-Za-z_][A-Za-z0-9_]*)?[[:space:]]*:'

# Skip helpers — paths that are exempt per rule.
is_constants_or_settings() {
    case "$1" in
        */constants.py|*/settings.py|*/config/settings.py) return 0 ;;
        *) return 1 ;;
    esac
}

is_intent_module_or_dto() {
    # DTOs typically live under domain/dtos/, application/dtos/, or any *_dto.py.
    # intent_*.py files (extractor, classifier) are also exempt — they own the
    # canonical intent strings.
    case "$1" in
        */constants.py|*/settings.py) return 0 ;;
        */intent_*.py|*intent_*.py) return 0 ;;
        */dtos/*.py|*_dto.py|*/schemas/*.py) return 0 ;;
        *) return 1 ;;
    esac
}

# Brand denylist from env (comma-separated). Empty = skip rule (no false-pos).
BRAND_DENYLIST="${RAGBOT_BRAND_DENYLIST:-}"

# ---------------------------------------------------------------------------
# Per-file scan
# ---------------------------------------------------------------------------
while IFS= read -r FILE; do
    [ -z "$FILE" ] && continue
    [ ! -f "$FILE" ] && continue

    BLOB=$(staged_blob "$FILE")
    [ -z "$BLOB" ] && continue

    # Pre-process blob: strip trailing inline `# ...` comments per-line so
    # numbers / model-name strings inside comments don't false-positive.
    # Heuristic: cut at the first `#` that follows whitespace; preserves
    # `#` that appears inside string literals only imperfectly, but for
    # Python this catches the dominant case (inline comments after code).
    # Triple-quoted docstring bodies are detected separately by the
    # `^\s*"""` filter on the grep -v step.
    BLOB_NOCOMMENT=$(echo "$BLOB" | sed -E 's/[[:space:]]+#[^"'"'"']*$//')

    # ---- Rule 1: magic numbers ---------------------------------------------
    if ! is_constants_or_settings "$FILE"; then
        while IFS= read -r HIT; do
            [ -z "$HIT" ] && continue
            LINE_NO=$(echo "$HIT" | cut -d: -f1)
            SNIPPET=$(echo "$HIT" | cut -d: -f2- | sed 's/^[[:space:]]*//' | cut -c1-100)
            report_hit "$FILE" "$LINE_NO" "magic-number" "$SNIPPET"
        done < <(echo "$BLOB_NOCOMMENT" | grep -nE "$MAGIC_NUM_RE" \
            | grep -vE '^[0-9]+:\s*#|^[0-9]+:\s*"""|^[0-9]+:\s*"' \
            || true)
    fi

    # ---- Rule 2: hardcoded model names -------------------------------------
    if ! is_constants_or_settings "$FILE"; then
        while IFS= read -r HIT; do
            [ -z "$HIT" ] && continue
            LINE_NO=$(echo "$HIT" | cut -d: -f1)
            SNIPPET=$(echo "$HIT" | cut -d: -f2- | sed 's/^[[:space:]]*//' | cut -c1-100)
            report_hit "$FILE" "$LINE_NO" "model-literal" "$SNIPPET"
        done < <(echo "$BLOB_NOCOMMENT" | grep -nE "$MODEL_NAME_RE" \
            | grep -vE '^[0-9]+:\s*#|^[0-9]+:\s*"""' \
            || true)
    fi

    # ---- Rule 3: brand literals (env-driven denylist) ----------------------
    if [ -n "$BRAND_DENYLIST" ]; then
        # Convert comma-separated list to grep alternation.
        BRAND_RE=$(echo "$BRAND_DENYLIST" | sed 's/,/|/g' | sed 's/[[:space:]]//g')
        if [ -n "$BRAND_RE" ]; then
            while IFS= read -r HIT; do
                [ -z "$HIT" ] && continue
                LINE_NO=$(echo "$HIT" | cut -d: -f1)
                SNIPPET=$(echo "$HIT" | cut -d: -f2- | sed 's/^[[:space:]]*//' | cut -c1-100)
                report_hit "$FILE" "$LINE_NO" "brand-literal" "$SNIPPET"
            done < <(echo "$BLOB" | grep -inE "($BRAND_RE)" || true)
        fi
    fi

    # ---- Rule 4: inline intent strings outside DTO/constants/intent_*/-----
    if ! is_intent_module_or_dto "$FILE"; then
        while IFS= read -r HIT; do
            [ -z "$HIT" ] && continue
            LINE_NO=$(echo "$HIT" | cut -d: -f1)
            SNIPPET=$(echo "$HIT" | cut -d: -f2- | sed 's/^[[:space:]]*//' | cut -c1-100)
            report_hit "$FILE" "$LINE_NO" "inline-intent" "$SNIPPET"
        done < <(echo "$BLOB" | grep -nE "$INTENT_STR_RE" || true)
    fi

    # ---- Rule 5: broad-except without noqa BLE001 --------------------------
    # Need 2-line lookback: a line above the `except` may carry the noqa.
    # Approach: numerate the blob, then for every match line check (a) same
    # line contains `# noqa: BLE001` OR (b) prior line contains it.
    BLOB_NUM=$(echo "$BLOB" | nl -ba -w1 -s$'\t')
    while IFS= read -r HIT; do
        [ -z "$HIT" ] && continue
        LINE_NO=$(echo "$HIT" | cut -f1)
        LINE_TEXT=$(echo "$HIT" | cut -f2-)
        # Same-line noqa?
        if echo "$LINE_TEXT" | grep -q '# noqa: BLE001'; then
            continue
        fi
        # Prior-line noqa?
        if [ "$LINE_NO" -gt 1 ]; then
            PRIOR_NO=$((LINE_NO - 1))
            PRIOR_LINE=$(echo "$BLOB_NUM" | awk -F'\t' -v n="$PRIOR_NO" '$1 == n {print $2}')
            if echo "$PRIOR_LINE" | grep -q '# noqa: BLE001'; then
                continue
            fi
        fi
        SNIPPET=$(echo "$LINE_TEXT" | sed 's/^[[:space:]]*//' | cut -c1-100)
        report_hit "$FILE" "$LINE_NO" "broad-except" "$SNIPPET"
    done < <(echo "$BLOB_NUM" | grep -E "^[0-9]+"$'\t'"$BROAD_EXCEPT_RE" || true)

done <<< "$STAGED_FILES"

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
if [ "$VIOLATIONS_FOUND" -eq 0 ]; then
    echo "ragbot pre-commit: PASS ($(echo "$STAGED_FILES" | wc -l | tr -d ' ') hot-path files scanned)"
    exit 0
fi

echo ""
echo "ragbot pre-commit: FAIL — $VIOLATIONS_FOUND violation(s) detected"
echo ""
printf "%s" "$REPORT"
echo ""
echo "Resolution paths:"
echo "  magic-number  -> move literal into src/ragbot/shared/constants.py and import"
echo "  model-literal -> move into src/ragbot/shared/constants.py or settings.py"
echo "  brand-literal -> move into .env (RAGBOT_*) and read via os.getenv()"
echo "  inline-intent -> import from src/ragbot/shared/constants.py INTENT_* symbols"
echo "  broad-except  -> narrow exception type OR add  '# noqa: BLE001 — <reason>'"
echo ""
echo "Emergency bypass (use sparingly): RAGBOT_PRECOMMIT_BYPASS=1 git commit ..."
exit 1
