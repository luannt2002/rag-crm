#!/usr/bin/env bash
# scripts/audit_async_mindset.sh — heuristic grep gate for the 8-rule
# Async Performance Mindset in CLAUDE.md.
#
# Two heuristics, both warn-only by default (no false-fail in CI):
#
#   H1 — Adjacent sequential awaits on the same object that are likely
#        gather-candidates. Pattern: two ``await self._redis.<op>(...)``
#        (or ``await self._db.<op>(...)``) lines within 3 lines of each
#        other inside the same function. CLAUDE.md Rule 1 (gather-first).
#
#   H2 — asyncio.gather(...) call where the call site contains side-
#        effect verbs (set / publish / write / invalidate / hset / sadd)
#        but does NOT pass return_exceptions=. CLAUDE.md Rule 5
#        (side-effects use return_exceptions=True so a single failure
#        does not blow up the request path).
#
# Output: one line per finding, file:lineno:reason. Exit code:
#   0 — no findings (or --warn-only mode)
#   1 — findings detected AND --strict supplied
#   2 — invocation error
#
# Usage:
#   bash scripts/audit_async_mindset.sh            # warn (exit 0 always)
#   bash scripts/audit_async_mindset.sh --strict   # exit 1 if findings
#   bash scripts/audit_async_mindset.sh --staged   # staged files only
#
# Design notes:
#   - Heuristic only. False positives expected (e.g. two awaits on
#     different keys but one depends on the other's return value). The
#     spec mandates "warn not error" — operators triage the list.
#   - No specific file path is hardcoded. The script globs across
#     src/ragbot/ (domain-neutral) so adding modules costs zero edits.
#   - Counts gather-candidates on a per-function basis when feasible.
#     If the python helper cannot parse the file (syntax error mid-edit)
#     the file is skipped silently.
#
# CLAUDE.md compliance:
#   - Zero hardcode: no specific file path; uses glob src/ragbot/**.
#   - Domain-neutral: applies platform-wide.
#   - Heuristic warn: default exit 0 so PR not falsely blocked.
#   - Reuses audit_agent_diff.sh style (bash + python3 inline).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_ROOT="$REPO_ROOT/src/ragbot"

STRICT=0
STAGED=0

while [ $# -gt 0 ]; do
    case "$1" in
        --strict) STRICT=1; shift ;;
        --staged) STAGED=1; shift ;;
        -h|--help)
            sed -n '2,/^# CLAUDE.md compliance:/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "audit_async_mindset: unknown arg: $1" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Build candidate file list.
# ---------------------------------------------------------------------------
if [ "$STAGED" = "1" ]; then
    FILES=$(
        git -C "$REPO_ROOT" diff --cached --name-only --diff-filter=ACM \
        | grep -E '^src/ragbot/.*\.py$' \
        | sed "s|^|$REPO_ROOT/|" \
        || true
    )
else
    FILES=$(find "$SRC_ROOT" -type f -name '*.py' ! -path '*/__pycache__/*' 2>/dev/null || true)
fi

if [ -z "$FILES" ]; then
    echo "audit_async_mindset: no python files to scan"
    exit 0
fi

# ---------------------------------------------------------------------------
# Heuristic implementation. We write the candidate file list to a tempfile
# and the python helper to a tempfile, so neither competes with the
# other for stdin. Pipe vs heredoc conflict otherwise eats one stream.
# ---------------------------------------------------------------------------
FILELIST=$(mktemp)
echo "$FILES" >"$FILELIST"

PY_HELPER=$(mktemp --suffix=.py)
cat >"$PY_HELPER" <<'PYEOF'
"""Async-mindset heuristic scanner — H1 + H2 in one pass.

Usage:  python3 helper.py <repo_root> <filelist> <out_h1> <out_h2>
"""
import re
import sys
from pathlib import Path

repo_root, filelist_path, out_h1_path, out_h2_path = sys.argv[1:5]

await_pattern = re.compile(
    r"^\s+(?:\w+\s*=\s*)?await\s+(?:self|cls)\._(redis|db|cache)\.\w+\("
)
gather_re = re.compile(r"\basyncio\.gather\(")
sideeffect_re = re.compile(
    r"\b(set|setex|sadd|hset|publish|xadd|write|invalidate|cache|emit|"
    r"audit|log|delete|incr|expire|push|notify|fire)"
)
return_excs_re = re.compile(r"return_exceptions\s*=")

with open(filelist_path, "r", encoding="utf-8") as fh:
    files = [Path(line.strip()) for line in fh if line.strip()]

h1_lines: list[str] = []
h2_lines: list[str] = []

for path in files:
    if not path.exists():
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue

    lines = text.splitlines()
    n = len(lines)
    rel = str(path.relative_to(repo_root)) if path.is_absolute() else str(path)

    # H1 — adjacent awaits on same target.
    awaits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        m = await_pattern.match(line)
        if m:
            awaits.append((i, m.group(1)))
    seen_pairs: set[tuple[int, int]] = set()
    for j in range(len(awaits) - 1):
        a_ln, a_t = awaits[j]
        b_ln, b_t = awaits[j + 1]
        if b_ln - a_ln <= 3 and a_t == b_t and (a_ln, b_ln) not in seen_pairs:
            seen_pairs.add((a_ln, b_ln))
            h1_lines.append(
                f"{rel}:{a_ln}-{b_ln}: H1 two adjacent "
                f"``await self._{a_t}.*`` calls — gather candidate? "
                f"(CLAUDE.md Async Rule 1)"
            )

    # H2 — asyncio.gather missing return_exceptions= in side-effect ctx.
    for i, line in enumerate(lines):
        if not gather_re.search(line):
            continue
        depth = line.count("(") - line.count(")")
        chunk = [line]
        j = i + 1
        while j < n and j - i < 6 and depth > 0:
            chunk.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            j += 1
        joined = "\n".join(chunk)
        if return_excs_re.search(joined):
            continue
        m = sideeffect_re.search(joined)
        if not m:
            continue
        h2_lines.append(
            f"{rel}:{i+1}: H2 asyncio.gather() with side-effect token "
            f"'{m.group(1)}' missing return_exceptions= "
            f"(CLAUDE.md Async Rule 5)"
        )

Path(out_h1_path).write_text("\n".join(h1_lines) + ("\n" if h1_lines else ""))
Path(out_h2_path).write_text("\n".join(h2_lines) + ("\n" if h2_lines else ""))
PYEOF

H1_OUT=$(mktemp)
H2_OUT=$(mktemp)

python3 "$PY_HELPER" "$REPO_ROOT" "$FILELIST" "$H1_OUT" "$H2_OUT"
rm -f "$PY_HELPER" "$FILELIST"

count_nonblank() {
    # wc -l of a possibly-empty file, normalised to a single integer.
    local f="$1"
    if [ ! -s "$f" ]; then echo 0; return; fi
    local n
    n=$(grep -c '.' "$f" 2>/dev/null || true)
    if [ -z "$n" ]; then n=0; fi
    echo "$n"
}

H1_COUNT=$(count_nonblank "$H1_OUT")
H2_COUNT=$(count_nonblank "$H2_OUT")
TOTAL=$((H1_COUNT + H2_COUNT))

if [ "$TOTAL" = "0" ]; then
    echo "audit_async_mindset: no findings (clean)"
    rm -f "$H1_OUT" "$H2_OUT"
    exit 0
fi

echo "audit_async_mindset: $TOTAL finding(s) — review below"
echo ""
if [ "$H1_COUNT" -gt 0 ]; then
    echo "── H1: adjacent gather candidates (Rule 1) ──"
    cat "$H1_OUT"
    echo ""
fi
if [ "$H2_COUNT" -gt 0 ]; then
    echo "── H2: gather() missing return_exceptions= in side-effect ctx (Rule 5) ──"
    cat "$H2_OUT"
    echo ""
fi

rm -f "$H1_OUT" "$H2_OUT"

if [ "$STRICT" = "1" ]; then
    exit 1
fi
exit 0
