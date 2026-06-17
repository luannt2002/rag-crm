#!/usr/bin/env bash
# scripts/audit_agent_diff.sh — Auditor-Chief pre-merge gate for coder /
# agent branches. Runs the standing CLAUDE.md grep guards (version-ref,
# zero-hardcode, brand literal, model-name literal, resolver fallback,
# domain-neutral diacritics) against the diff between a feature branch
# and a base branch. Designed to wrap existing helpers so the rule set
# stays in one place (DRY).
#
# Two modes:
#   --regression-only (default)  Fail only if a guard's hit count
#                                INCREASED versus base. Baseline-tolerant:
#                                lets pre-existing violations through so
#                                a coder branch is not blocked for
#                                problems they did not introduce.
#   --strict                     Fail on ANY non-zero hit, ignoring base.
#                                Use after the baseline is clean.
#
# This script is the CI entrypoint invoked from
# .github/workflows/audit-agent-diff.yml. It can also be run locally
# pre-merge to validate a coder branch before opening a PR.
#
# Usage:
#   bash scripts/audit_agent_diff.sh [--strict|--regression-only] \
#        <feature_branch> [base_branch]
#
#   feature_branch  required — branch under audit (e.g.
#                   agent-260518-A2-cont-ci-mindset). May also be a
#                   commit SHA. Resolved with git rev-parse.
#   base_branch     optional — defaults to "main".
#
# Behaviour:
#   - Resolves both refs (local branch / origin/<name> / SHA).
#   - Worktree-checkouts both feature and base into temp dirs.
#   - Runs four guards at each ref:
#       1. anti_hardcode_check.sh — zero-hardcode + version-ref +
#                                   model-name + mock fixture + delegates
#                                   to grep_domain_literals.sh.
#       2. audit_resolver_fallback.sh — every per-bot resolver MUST fall
#                                       back to system_config.
#       3. audit_domain_neutral.sh — VN diacritics in logic code = fail.
#       4. tenant-literal scan (inline) — DB DSN with embedded password,
#                                         brand hostname, IPv4 literal in
#                                         src/ragbot/.
#   - In --regression-only mode, computes (feature_hits − base_hits) per
#     guard; PASS if delta <= 0 for every guard.
#
# Exit codes:
#   0  PASS — no regression vs base (or strict mode + all guards green).
#   1  FAIL — one or more guards regressed (or strict mode + a hit).
#   2  invocation error (wrong arg count, missing branch, missing helper).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="regression-only"

while [ $# -gt 0 ]; do
    case "$1" in
        --strict)
            MODE="strict"
            shift
            ;;
        --regression-only)
            MODE="regression-only"
            shift
            ;;
        -h|--help)
            sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "audit_agent_diff: unknown flag: $1" >&2
            exit 2
            ;;
        *)
            break
            ;;
    esac
done

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi

FEATURE_REF="$1"
BASE_REF="${2:-main}"

cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Resolve refs. We allow either branch name, remote ref (origin/<name>),
# or commit SHA. `git rev-parse --verify` exits non-zero on bad refs.
# ---------------------------------------------------------------------------
resolve_ref() {
    local ref="$1"
    if git rev-parse --verify --quiet "$ref" >/dev/null 2>&1; then
        echo "$ref"
        return 0
    fi
    if git rev-parse --verify --quiet "origin/$ref" >/dev/null 2>&1; then
        echo "origin/$ref"
        return 0
    fi
    return 1
}

FEATURE_RESOLVED=$(resolve_ref "$FEATURE_REF" || true)
BASE_RESOLVED=$(resolve_ref "$BASE_REF" || true)

if [ -z "$FEATURE_RESOLVED" ]; then
    echo "audit_agent_diff: cannot resolve feature ref: $FEATURE_REF" >&2
    exit 2
fi
if [ -z "$BASE_RESOLVED" ]; then
    echo "audit_agent_diff: cannot resolve base ref: $BASE_REF" >&2
    exit 2
fi

echo "audit_agent_diff: mode=$MODE feature=$FEATURE_RESOLVED base=$BASE_RESOLVED"

# Informational: diff file count.
DIFF_FILE_COUNT=$(
    git diff --name-only --diff-filter=ACMR "${BASE_RESOLVED}...${FEATURE_RESOLVED}" 2>/dev/null \
    | grep -cE '\.(py|md|sh|yml|yaml|json|toml|ini|cfg)$' \
    || true
)
echo "audit_agent_diff: $DIFF_FILE_COUNT candidate files in diff"

# ---------------------------------------------------------------------------
# Temp worktrees — both feature and base. Cleaned on exit.
# ---------------------------------------------------------------------------
TMP_DIRS=()
cleanup() {
    local d
    for d in "${TMP_DIRS[@]+"${TMP_DIRS[@]}"}"; do
        if [ -d "$d" ]; then
            git -C "$REPO_ROOT" worktree remove --force "$d" 2>/dev/null || true
            rm -rf "$d" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT

# Count "violation marker" lines per guard. Markers chosen from each
# helper's existing FAIL output.
count_lines() {
    local out="$1"
    local pattern="$2"
    local n
    n=$(grep -cE "$pattern" "$out" 2>/dev/null || true)
    # grep -c always prints exactly one integer; guard against empty.
    if [ -z "$n" ]; then n=0; fi
    echo "$n"
}

run_guards_at() {
    # Args: ref_label, output_dir
    local ref="$1"
    local outdir="$2"
    local wd
    wd="$(mktemp -d -t agent-audit.XXXXXX)"
    rm -rf "$wd"
    TMP_DIRS+=("$wd")

    if ! git -C "$REPO_ROOT" worktree add --quiet --detach "$wd" "$ref" >/dev/null 2>&1; then
        echo "audit_agent_diff: failed to worktree $ref" >&2
        return 1
    fi

    mkdir -p "$outdir"

    # Guard 1 — anti_hardcode_check.sh (may not exist at older refs).
    if [ -f "$wd/scripts/anti_hardcode_check.sh" ]; then
        bash "$wd/scripts/anti_hardcode_check.sh" >"$outdir/anti_hardcode.out" 2>&1 || true
    else
        : >"$outdir/anti_hardcode.out"
    fi
    # Guard 2 — audit_resolver_fallback.sh.
    if [ -f "$wd/scripts/audit_resolver_fallback.sh" ]; then
        bash "$wd/scripts/audit_resolver_fallback.sh" >"$outdir/resolver.out" 2>&1 || true
    else
        : >"$outdir/resolver.out"
    fi
    # Guard 3 — audit_domain_neutral.sh.
    if [ -f "$wd/scripts/audit_domain_neutral.sh" ]; then
        bash "$wd/scripts/audit_domain_neutral.sh" >"$outdir/domain.out" 2>&1 || true
    else
        : >"$outdir/domain.out"
    fi

    # Guard 4 — tenant-identifier / secret literals.
    # CLAUDE.md § Tenant-identifier section forbids brand hostnames,
    # customer subdomains, hardcoded DB DSNs with embedded creds, IPv4
    # literals, and bearer/api-key strings in any tracked .py/.md/.sh/...
    # Scoped to src/ragbot only — docs/, plans/, reports/, alembic/ are
    # legitimate consumers of such strings (history, docs).
    {
        grep -rnE 'postgresql://[^"]+:[^"@]+@' \
            "$wd/src/ragbot/" --include='*.py' 2>/dev/null || true
        grep -rnE 'https?://[a-z0-9.-]+\.(vn|com|net|io)(:[0-9]+)?' \
            "$wd/src/ragbot/" --include='*.py' 2>/dev/null \
            | grep -vE 'localhost|127\.0\.0\.1|example\.com|<[a-z-]+>' || true
        grep -rnE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' \
            "$wd/src/ragbot/" --include='*.py' 2>/dev/null \
            | grep -vE '127\.0\.0\.1|0\.0\.0\.0' || true
    } >"$outdir/tenant.out"

    # Count individual VIOLATION LINES, not FAIL category headers. The
    # helpers emit grep-style file:lineno:snippet lines for each hit.
    # Resolver helper prints "FAIL: <path>" per missing resolver.
    local hh rh dh th
    hh=$(count_lines "$outdir/anti_hardcode.out" "[^[:space:]]+:[0-9]+:")
    rh=$(count_lines "$outdir/resolver.out" "^FAIL:")
    dh=$(count_lines "$outdir/domain.out" "[^[:space:]]+\.py:[0-9]+:")
    th=$(count_lines "$outdir/tenant.out" "[^[:space:]]+:[0-9]+:")
    printf "%d %d %d %d\n" "$hh" "$rh" "$dh" "$th" >"$outdir/counts.txt"
}

OUT_FEATURE="$(mktemp -d -t agent-audit-feature.XXXXXX)"
OUT_BASE="$(mktemp -d -t agent-audit-base.XXXXXX)"

echo ""
echo "── Running guards at FEATURE ($FEATURE_RESOLVED) ──"
run_guards_at "$FEATURE_RESOLVED" "$OUT_FEATURE"
read -r F_AH F_RES F_DOM F_TEN <"$OUT_FEATURE/counts.txt"
echo "  anti_hardcode violation lines:  $F_AH"
echo "  resolver missing-fallback rows: $F_RES"
echo "  domain-neutral violation lines: $F_DOM"
echo "  tenant-literal violation lines: $F_TEN"

if [ "$MODE" = "strict" ]; then
    if [ "$F_AH" -gt 0 ] || [ "$F_RES" -gt 0 ] || [ "$F_DOM" -gt 0 ] || [ "$F_TEN" -gt 0 ]; then
        echo ""
        echo "audit_agent_diff: STRICT FAIL — see helper output:" >&2
        echo "  anti_hardcode:  $OUT_FEATURE/anti_hardcode.out" >&2
        echo "  resolver:       $OUT_FEATURE/resolver.out" >&2
        echo "  domain-neutral: $OUT_FEATURE/domain.out" >&2
        echo "  tenant-literal: $OUT_FEATURE/tenant.out" >&2
        exit 1
    fi
    echo ""
    echo "audit_agent_diff: PASS (strict)"
    exit 0
fi

echo ""
echo "── Running guards at BASE ($BASE_RESOLVED) ──"
run_guards_at "$BASE_RESOLVED" "$OUT_BASE"
read -r B_AH B_RES B_DOM B_TEN <"$OUT_BASE/counts.txt"
echo "  anti_hardcode violation lines:  $B_AH"
echo "  resolver missing-fallback rows: $B_RES"
echo "  domain-neutral violation lines: $B_DOM"
echo "  tenant-literal violation lines: $B_TEN"

DELTA_AH=$((F_AH - B_AH))
DELTA_RES=$((F_RES - B_RES))
DELTA_DOM=$((F_DOM - B_DOM))
DELTA_TEN=$((F_TEN - B_TEN))

echo ""
echo "── Regression deltas (feature − base) ──"
echo "  anti_hardcode:   $DELTA_AH"
echo "  resolver:        $DELTA_RES"
echo "  domain-neutral:  $DELTA_DOM"
echo "  tenant-literal:  $DELTA_TEN"

REGRESSED=0
if [ "$DELTA_AH" -gt 0 ]; then
    echo ""
    echo "REGRESSION — anti_hardcode delta = $DELTA_AH" >&2
    echo "  feature output: $OUT_FEATURE/anti_hardcode.out" >&2
    REGRESSED=1
fi
if [ "$DELTA_RES" -gt 0 ]; then
    echo ""
    echo "REGRESSION — resolver fallback delta = $DELTA_RES" >&2
    echo "  feature output: $OUT_FEATURE/resolver.out" >&2
    REGRESSED=1
fi
if [ "$DELTA_DOM" -gt 0 ]; then
    echo ""
    echo "REGRESSION — domain-neutral delta = $DELTA_DOM" >&2
    echo "  feature output: $OUT_FEATURE/domain.out" >&2
    REGRESSED=1
fi
if [ "$DELTA_TEN" -gt 0 ]; then
    echo ""
    echo "REGRESSION — tenant-literal delta = $DELTA_TEN" >&2
    echo "  feature output: $OUT_FEATURE/tenant.out" >&2
    REGRESSED=1
fi

if [ "$REGRESSED" = "0" ]; then
    echo ""
    echo "audit_agent_diff: PASS — no regression vs base"
    exit 0
fi
echo ""
echo "audit_agent_diff: FAIL — see deltas above" >&2
exit 1
