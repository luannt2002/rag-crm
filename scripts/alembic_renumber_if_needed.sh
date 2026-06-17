#!/usr/bin/env bash
# scripts/alembic_renumber_if_needed.sh — pre-merge alembic collision auto-fix.
#
# Purpose: when sequentially merging feature branches, two branches that both
# branched from the same alembic head will create *parallel* revisions with
# different IDs but the same `down_revision`. After the first one merges,
# `alembic upgrade head` on the second one fails with "Multiple head revisions".
#
# This script:
#   1. Scans alembic/versions/ on the CURRENT branch for revs whose
#      `down_revision` points at a revision that no longer matches the live
#      head AFTER recent merges.
#   2. For each such rev, rewrites the `down_revision` field to the live head
#      and renames the rev file numerically (XXNN_*.py → next free XXNN).
#
# Conservative: read-only by default (--dry-run). Real edit only with --apply.
# Designed to be invoked from admin_merge_all.sh post-checkout, pre-upgrade.
#
# Usage:
#   bash scripts/alembic_renumber_if_needed.sh [--apply]
#
# Exit codes:
#   0  no collision OR collision auto-resolved (--apply)
#   1  collision detected but --dry-run (do not merge)
#   2  unrecoverable / invocation error

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSIONS_DIR="$REPO_ROOT/alembic/versions"

APPLY=0
if [ $# -gt 0 ]; then
    case "$1" in
        --apply) APPLY=1 ;;
        --dry-run) APPLY=0 ;;
        -h|--help)
            sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "alembic_renumber: unknown arg: $1" >&2
            exit 2
            ;;
    esac
fi

if [ ! -d "$VERSIONS_DIR" ]; then
    echo "alembic_renumber: $VERSIONS_DIR not found — skip" >&2
    exit 0
fi

# Find the live head (most recent rev with no descendant).
# A rev is a head iff its `revision` does not appear as any other rev's
# `down_revision`.
declare -A REV_OF_FILE
declare -A DOWN_OF_FILE
declare -A IS_DOWN

for f in "$VERSIONS_DIR"/*.py; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    # Skip __init__.py etc.
    case "$base" in __*) continue ;; esac
    rev="$(grep -m1 -E "^revision[[:space:]]*=" "$f" | sed -E "s/.*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/")"
    down="$(grep -m1 -E "^down_revision[[:space:]]*=" "$f" | sed -E "s/.*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/")"
    [ -z "$rev" ] && continue
    REV_OF_FILE["$f"]="$rev"
    DOWN_OF_FILE["$f"]="$down"
    if [ -n "$down" ] && [ "$down" != "None" ]; then
        IS_DOWN["$down"]=1
    fi
done

# Heads = revs not pointed at by any down_revision
declare -a HEADS=()
for f in "${!REV_OF_FILE[@]}"; do
    rev="${REV_OF_FILE[$f]}"
    if [ -z "${IS_DOWN[$rev]:-}" ]; then
        HEADS+=("$rev|$f")
    fi
done

if [ "${#HEADS[@]}" -le 1 ]; then
    echo "alembic_renumber: single head OK (${#HEADS[@]} head[s])"
    exit 0
fi

echo "alembic_renumber: MULTIPLE HEADS detected (${#HEADS[@]}):"
for h in "${HEADS[@]}"; do
    rev="${h%|*}"
    f="${h#*|}"
    echo "  - $rev  ($(basename "$f"))"
done

# Heuristic: the head that is also reachable via the longest chain wins.
# Pick the lexicographically last filename (numeric prefix) as the canonical
# head and reparent all other heads onto it.
declare -a HEAD_FILES=()
for h in "${HEADS[@]}"; do
    HEAD_FILES+=("${h#*|}")
done
IFS=$'\n' SORTED=($(printf '%s\n' "${HEAD_FILES[@]}" | sort))
unset IFS
CANONICAL_FILE="${SORTED[-1]}"
CANONICAL_REV="${REV_OF_FILE[$CANONICAL_FILE]}"

echo ""
echo "Canonical head:  $CANONICAL_REV  ($(basename "$CANONICAL_FILE"))"
echo "Will reparent the other head(s) onto $CANONICAL_REV"

if [ "$APPLY" -eq 0 ]; then
    echo ""
    echo "[DRY-RUN] no changes written. Re-run with --apply to fix."
    exit 1
fi

# Apply: rewrite down_revision of each non-canonical head to point at canonical.
for h in "${HEADS[@]}"; do
    f="${h#*|}"
    if [ "$f" = "$CANONICAL_FILE" ]; then continue ; fi
    rev="${h%|*}"
    old_down="${DOWN_OF_FILE[$f]}"
    echo "  rewriting: $(basename "$f")"
    echo "    down_revision: $old_down → $CANONICAL_REV"
    # POSIX sed in-place (BSD/GNU portable via -i.bak)
    sed -E -i.bak "s/^(down_revision[[:space:]]*=[[:space:]]*)['\"][^'\"]+['\"]/\1'$CANONICAL_REV'/" "$f"
    rm -f "$f.bak"
done

echo ""
echo "alembic_renumber: applied. Verify with 'alembic heads' and 'alembic upgrade head'."
exit 0
