#!/usr/bin/env bash
# scripts/grep_domain_literals.sh — pre-commit hook helper.
#
# Fail fast if hot-path source under src/ contains industry-specific or
# brand-specific literals that should live in per-tenant config (DB column or
# bots.system_prompt) instead of code.
#
# Domain-neutral mandate (CLAUDE.md): ragbot is a multi-tenant + multi-industry
# RAG platform. Spa is the FIRST vertical, NOT the only. Code that hardcodes
# spa / massage / clinic / brand vocabulary blocks expansion into finance,
# healthcare, retail, education, legal.
#
# Allowed locations for these literals:
#   - tests/fixtures/*  (per-vertical golden Qs, allowed)
#   - tests/unit/test_*  (when explicitly testing tokenisation of VN/spa terms)
#   - scripts/*  (scripts that generate per-tenant content, allowed)
#   - reports/*, plans/*, docs/*  (documentation, allowed)
#   - bots.system_prompt + bots.custom_vocabulary in DB (NOT in code)
#
# Forbidden locations:
#   - src/  (the hot-path application code)
#
# Words that look domain-specific but are common technical terms (spaCy,
# whitespace, lifespan, span, sparse) are excluded by the explicit allowlist
# regex below.
#
# Usage:
#   bash scripts/grep_domain_literals.sh             # exit 0 = clean, 1 = hits
#   bash scripts/grep_domain_literals.sh --staged    # only check staged files
#

set -u

SRC_ROOT="src/ragbot"

# Industry / VN-specific / brand literals that must NOT appear in src/.
FORBIDDEN_PATTERNS=(
    'massage'
    'chăm sóc da'
    'triệt lông'
    'gội đầu'
    'medispa'
    'beauty[ -]salon'
    'spa[ -]tenant'
    '<Brand Name>'
)

# Allowlist: word patterns that LOOK like a hit but are technical.
ALLOWLIST_RE='spaCy|whitespace|lifespan|namespace|SPLADE|sparse|span_|spans|spawn|dispatch|displace'

if [ "${1:-}" = "--staged" ]; then
    FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -E "^${SRC_ROOT}/.*\.py$" || true)
else
    FILES=$(find "$SRC_ROOT" -name '*.py' -type f 2>/dev/null)
fi

if [ -z "$FILES" ]; then
    echo "grep_domain_literals: no python files to check"
    exit 0
fi

# Build alternation for FORBIDDEN
ALT=$(printf "|%s" "${FORBIDDEN_PATTERNS[@]}")
ALT=${ALT:1}  # strip leading |

# Use grep -i so we catch case variants (Spa, MASSAGE, etc.)
HITS=$(echo "$FILES" | xargs grep -inE "$ALT" 2>/dev/null | grep -ivE "$ALLOWLIST_RE" || true)

if [ -z "$HITS" ]; then
    echo "grep_domain_literals: PASS (no industry literals found in $SRC_ROOT)"
    exit 0
fi

echo "grep_domain_literals: FAIL — domain-specific literals found in $SRC_ROOT:"
echo ""
echo "$HITS"
echo ""
echo "Resolution:"
echo "  - Move the literal to bots.system_prompt (per-bot owner config)"
echo "  - Or to bots.custom_vocabulary JSONB (per-tenant glossary)"
echo "  - Or to system_config DB key (platform-wide, language-keyed)"
echo "  - Tests / fixtures / scripts: allowed; ensure file is under those dirs"
exit 1
