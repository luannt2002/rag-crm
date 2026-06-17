#!/bin/bash
# Audit gate — every per-bot resolver MUST fall back to system_config / ai_models
# (the shared platform default) when bot_model_bindings yields no row.
#
# Bug history (2026-05-14): reranker_resolver._lookup_db() returned NullReranker
# when binding was missing → bot "thong-tu-09-2020" answered 0 chunks even
# though system_config.reranker_model was wired and ai_models had the row.
# Fix added _lookup_platform_default() to read system_config + JOIN ai_models.
# This script enforces the pattern so a future resolver can't regress.
#
# Run pre-commit. Exit 0 = pass, exit 1 = a resolver queries bot_model_bindings
# but does NOT have the fallback hook.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAGBOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$RAGBOT_ROOT"

fail=0

# Every resolver file under application/services/*resolver*.py that queries
# bot_model_bindings must contain at least one of these tokens:
#   - _lookup_platform_default     → reranker_resolver pattern (DB fallback)
#   - system_config                → inline platform-default query
#   - resolve_fallback_chain       → model_resolver fallback cascade pattern
#   - "# fail-loud"                → explicit decision to NOT fall back
required_tokens='(_lookup_platform_default|system_config|resolve_fallback_chain|# fail-loud)'

mapfile -t resolver_files < <(grep -rlE "FROM bot_model_bindings" \
    "$RAGBOT_ROOT/src/ragbot/application/services/" \
    --include="*resolver*.py" 2>/dev/null || true)

if [ ${#resolver_files[@]} -eq 0 ]; then
    echo "audit_resolver_fallback: no resolver files matched — skipping"
    exit 0
fi

for f in "${resolver_files[@]}"; do
    rel="${f#"$RAGBOT_ROOT"/}"
    if ! grep -qE "$required_tokens" "$f"; then
        echo "FAIL: $rel — queries bot_model_bindings but lacks fallback hook" >&2
        echo "      Add _lookup_platform_default() or document why fail-loud is intentional." >&2
        fail=1
    else
        echo "OK:   $rel"
    fi
done

if [ "$fail" -ne 0 ]; then
    echo ""
    echo "Pattern lesson: 2026-05-14 bug — bot trả 0 chunks vì resolver chỉ JOIN" >&2
    echo "bot_model_bindings, không fall back system_config + ai_models. Memory:" >&2
    echo "  feedback_resolver_must_fallback_system_config.md" >&2
    exit 1
fi

exit 0
