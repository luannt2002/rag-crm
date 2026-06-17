#!/usr/bin/env bash
# scripts/anti_hardcode_check.sh — pre-commit hook for the zero-hardcode +
# anti-mock + anti-comment-rac rules in CLAUDE.md.
#
# Sprint 0 / MoM 00c-analytics. Runs in seconds — designed for inline use
# in `git commit` hooks. Read-only: never edits source, only greps.
#
# What this enforces (CLAUDE.md sections referenced in []):
#   1. Zero-hardcode rule [§ Zero hardcode rule]
#        Inline magic constants in src/ragbot/ are forbidden. Defaults must
#        live in src/ragbot/shared/constants.py and be imported. Whitelist:
#        0/0.0, 1/1.0, 100 (percent), small index literals, tests/.
#   2. Anti-mock rule [§ OBSERVABILITY-MATRIX §8]
#        `mock_data = [...]` and similar fixture-style literals must not
#        appear under src/ragbot/ (tests/ allowed).
#   3. Anti-comment-rac rule [§ No version-ref rule, § OBSERVABILITY §8]
#        TODO / FIXME / "tạm thời" / "hardcoded for now" / "_v2 / _legacy"
#        comment crumbs must not ship in tracked production code.
#   4. Model-name literals [§ Domain-neutral rule]
#        Hardcoded LLM model names ("gpt-4", "claude-3", "claude-haiku",
#        "gemini-1.5", etc.) inside src/ragbot/ are a Strategy/DI violation
#        — use the registry + config-driven provider key.
#   5. Brand / customer literals [§ Domain-neutral rule]
#        Delegates to scripts/grep_domain_literals.sh so the brand patterns
#        live in one place (DRY). This script invokes it.
#
# Allowed locations (whitelist):
#   - src/ragbot/shared/constants.py          (THE single source for defaults)
#   - alembic/versions/                       (immutable migration history)
#   - tests/                                  (mocks and fixtures legitimate)
#   - scripts/                                (operator tools)
#   - docs/ plans/ reports/ *.md              (documentation)
#
# Usage:
#   bash scripts/anti_hardcode_check.sh                  # full repo scan
#   bash scripts/anti_hardcode_check.sh --staged         # staged files only
#   bash scripts/anti_hardcode_check.sh --src-root path  # override scan root
#
# Exit codes:
#   0  PASS
#   1  one or more violations found
#   2  invocation error (unknown arg, missing dep)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_ROOT="${REPO_ROOT}/src/ragbot"
STAGED_ONLY=0
SRC_ROOT_OVERRIDDEN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --staged)
            STAGED_ONLY=1
            shift
            ;;
        --src-root)
            SRC_ROOT="$2"
            SRC_ROOT_OVERRIDDEN=1
            shift 2
            ;;
        -h|--help)
            sed -n '1,/^set -u/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "anti_hardcode_check: unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [ ! -d "$SRC_ROOT" ]; then
    echo "anti_hardcode_check: src root not found: $SRC_ROOT" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Build the candidate file list.
# ---------------------------------------------------------------------------
if [ "$STAGED_ONLY" = "1" ]; then
    FILES=$(
        git -C "$REPO_ROOT" diff --cached --name-only --diff-filter=ACM \
        | grep -E '^src/ragbot/.*\.py$' \
        | grep -v '/shared/constants\.py$' \
        | grep -v '^src/ragbot/.*/alembic/versions/' \
        || true
    )
else
    FILES=$(
        find "$SRC_ROOT" -type f -name '*.py' \
        | grep -v '/shared/constants\.py$' \
        | grep -v '/__pycache__/' \
        || true
    )
fi

if [ -z "$FILES" ]; then
    echo "anti_hardcode_check: no python files to check"
    exit 0
fi

FAIL=0

# ---------------------------------------------------------------------------
# Rule 1 — inline magic numbers on common config-shaped variables.
#
# We flag assignments like ``timeout = 30`` / ``chunk_size = 1024`` where the
# RHS is a bare integer >= 2 (0, 1, 100 are explicitly allowed per
# whitelist). The pattern intentionally narrow: variable names that are
# almost always config-shaped, not loop counters. Lines that import from
# shared.constants or use os.getenv on the same line are skipped.
# ---------------------------------------------------------------------------
MAGIC_NAMES='timeout|max_tokens|max_size|min_size|chunk_size|chunk_overlap|threshold|batch_size|retry|retries|max_retries|pool_size|max_overflow|ttl|cache_ttl|window_seconds|rate_limit|backoff_seconds|page_size|max_results|top_k|top_p|temperature|max_concurrent|cooldown'
# Matches module-level (``timeout = 30``) and instance-attr (``self.timeout = 30``)
# style assignments. Excludes 0/1/100 by demanding RHS be either a two-or-more-
# digit integer or a single digit in [2-9].
MAGIC_RE="^[[:space:]]+(self\.)?(${MAGIC_NAMES})[[:space:]]*=[[:space:]]*(-?[2-9]|-?[0-9]{2,})([[:space:]]|$|#)"

MAGIC_HITS=$(
    echo "$FILES" | xargs grep -nE "$MAGIC_RE" 2>/dev/null \
    | grep -v 'shared/constants' \
    | grep -v 'os\.getenv' \
    | grep -v 'os\.environ' \
    || true
)
if [ -n "$MAGIC_HITS" ]; then
    echo "anti_hardcode_check: FAIL — inline magic numbers detected" >&2
    echo "$MAGIC_HITS" >&2
    echo "" >&2
    echo "  Resolution: move the default to src/ragbot/shared/constants.py" >&2
    echo "  and import it. Per-tenant override goes to system_config DB key." >&2
    echo "" >&2
    FAIL=1
fi

# ---------------------------------------------------------------------------
# Rule 2 — anti-mock: literals like ``mock_data = [`` / ``fake_response = {``
# inside production code. Tests/ is excluded via the path filter above so any
# hit here is a real violation.
# ---------------------------------------------------------------------------
MOCK_RE='^[[:space:]]*(mock_data|fake_data|fake_response|stub_response|dummy_data)[[:space:]]*='

MOCK_HITS=$(echo "$FILES" | xargs grep -nE "$MOCK_RE" 2>/dev/null || true)
if [ -n "$MOCK_HITS" ]; then
    echo "anti_hardcode_check: FAIL — mock fixture literals in production code" >&2
    echo "$MOCK_HITS" >&2
    echo "" >&2
    echo "  Resolution: move fixtures to tests/fixtures/ or tests/unit/." >&2
    echo "" >&2
    FAIL=1
fi

# ---------------------------------------------------------------------------
# Rule 3 — anti-comment-rac. Comments must be WHY-only per CLAUDE.md
# § No version-ref rule. Forbidden crumbs:
#   TODO, FIXME, XXX, HACK, "tạm thời", "hardcoded for now",
#   "Sprint S<n>", "Round V<X>", "post-V<n>", "v<n>" version refs.
# ---------------------------------------------------------------------------
COMMENT_RE='#.*\b(TODO|FIXME|XXX|HACK|hardcoded for now|t[aạ]m th[oơ]i)\b'

COMMENT_HITS=$(echo "$FILES" | xargs grep -niE "$COMMENT_RE" 2>/dev/null || true)
if [ -n "$COMMENT_HITS" ]; then
    echo "anti_hardcode_check: FAIL — forbidden comment crumbs (TODO/FIXME/...)" >&2
    echo "$COMMENT_HITS" >&2
    echo "" >&2
    echo "  Resolution: file a plan entry; the comment must explain WHY," >&2
    echo "  not 'fix later'. Open an issue if the work is deferred." >&2
    echo "" >&2
    FAIL=1
fi

VERSIONREF_RE='Sprint S?[0-9]+|Round V[A-Z]|post-V[0-9]+|V[0-9]+\.[0-9]+\.[0-9]+'

VERSIONREF_HITS=$(echo "$FILES" | xargs grep -nE "$VERSIONREF_RE" 2>/dev/null || true)
if [ -n "$VERSIONREF_HITS" ]; then
    echo "anti_hardcode_check: FAIL — version-ref comment crumbs" >&2
    echo "$VERSIONREF_HITS" >&2
    echo "" >&2
    echo "  Resolution: drop the version reference; name reflects PURPOSE," >&2
    echo "  not VERSION (CLAUDE.md § No version-ref rule)." >&2
    echo "" >&2
    FAIL=1
fi

# ---------------------------------------------------------------------------
# Rule 4 — model name literals. Strategy + DI mandates provider names live
# in system_config, not inline. Catches "gpt-4", "claude-3-...", "claude-haiku",
# "gemini-1.5", "command-r", "rerank-v3.5", "voyage-3", "cohere/...".
# ---------------------------------------------------------------------------
MODEL_RE='"(gpt-[34]|gpt-4o|claude-[2-4]|claude-haiku|claude-sonnet|claude-opus|gemini-[12]\.[0-9]|command-r|cohere/rerank|rerank-v[0-9]|voyage-[0-9])'

MODEL_HITS=$(echo "$FILES" | xargs grep -nE "$MODEL_RE" 2>/dev/null || true)
if [ -n "$MODEL_HITS" ]; then
    echo "anti_hardcode_check: FAIL — hardcoded model name literal" >&2
    echo "$MODEL_HITS" >&2
    echo "" >&2
    echo "  Resolution: read the model from ai_models / model_bindings or" >&2
    echo "  system_config. Provider strings flow through the registry." >&2
    echo "" >&2
    FAIL=1
fi

# ---------------------------------------------------------------------------
# Rule 5 — delegate to grep_domain_literals.sh for brand/industry literals,
# so the brand alphabet lives in one place. Skipped silently when the helper
# does not exist on this branch, or when ``--src-root`` is overridden (the
# helper hardcodes the real ``src/ragbot`` and would scan the wrong tree
# under a unit-test temp dir).
# ---------------------------------------------------------------------------
DOMAIN_HELPER="$REPO_ROOT/scripts/grep_domain_literals.sh"
if [ -f "$DOMAIN_HELPER" ] && [ "$SRC_ROOT_OVERRIDDEN" = "0" ]; then
    if [ "$STAGED_ONLY" = "1" ]; then
        if ! bash "$DOMAIN_HELPER" --staged >/dev/null 2>&1; then
            echo "anti_hardcode_check: FAIL — domain literal hits" >&2
            bash "$DOMAIN_HELPER" --staged >&2 || true
            FAIL=1
        fi
    else
        if ! bash "$DOMAIN_HELPER" >/dev/null 2>&1; then
            echo "anti_hardcode_check: FAIL — domain literal hits" >&2
            bash "$DOMAIN_HELPER" >&2 || true
            FAIL=1
        fi
    fi
fi

if [ "$FAIL" = "0" ]; then
    echo "anti_hardcode_check: PASS"
    exit 0
fi
exit 1
