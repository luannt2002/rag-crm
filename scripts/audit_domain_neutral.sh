#!/bin/bash
# Pre-commit gate — block commit nếu VN diacritics xuất hiện trong logic code
# (regex, keyword list, prompt text). Allowed: comment, docstring, error message,
# constants.py, i18n.py (DB fallback), tests/, alembic/.
#
# Pattern lesson 2026-05-14: code hardcode VN regex/stopwords làm bot Tenant
# English/Japanese bị áp VN config. Vi phạm CLAUDE.md domain-neutral.

set -u
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/ragbot"

# Allowed files (hardcode VN trong các file này là CỐ Ý)
ALLOWED_PATTERNS=(
  "shared/constants.py"       # DEFAULT_* seed
  "shared/i18n.py"            # DB fallback prompt pack
  "shared/errors.py"
  "shared/rbac.py"
)
allowed_regex=$(IFS='|'; echo "${ALLOWED_PATTERNS[*]}")

# Check 1: VN diacritics in logic code (non-comment, non-docstring)
# Heuristic: line có VN char + KHÔNG bắt đầu bằng # và KHÔNG nằm trong "..." docstring
violations=$(grep -rnP "[À-ỹĐđ]" "$SRC_ROOT" --include="*.py" \
    | grep -vE ":($allowed_regex):" \
    | grep -vE ":\\s*#" \
    | grep -vE "^\\s*\"\\\"\\\"" \
    | wc -l)

if [ "$violations" -gt 0 ]; then
    echo "❌ VN-specific hardcode detected in logic code:"
    grep -rnP "[À-ỹĐđ]" "$SRC_ROOT" --include="*.py" \
        | grep -vE ":($allowed_regex):" \
        | grep -vE ":\\s*#" \
        | head -20
    echo ""
    echo "Fix: move VN strings to system_config.<key>_by_language or language_packs DB."
    echo "Reference: plans/260514-domain-neutral-multitenant-fix/plan.md"
    exit 1
fi

# Check 2: re.compile() with VN string
regex_hits=$(grep -rnE "re\\.compile.*['\"].*[À-ỹĐđ]" "$SRC_ROOT" --include="*.py" \
    | grep -vE ":($allowed_regex):" | wc -l)
if [ "$regex_hits" -gt 0 ]; then
    echo "❌ Regex pattern with VN characters detected (must move to system_config):"
    grep -rnE "re\\.compile.*['\"].*[À-ỹĐđ]" "$SRC_ROOT" --include="*.py" \
        | grep -vE ":($allowed_regex):"
    exit 1
fi

echo "✅ Domain-neutral audit pass."
exit 0
