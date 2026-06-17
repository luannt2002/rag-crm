#!/usr/bin/env bash
# scripts/precommit_check_raw_sql_tenant_filter.sh
#
# Pre-commit guard: every raw SQL statement that references
# ``record_document_id`` in a WHERE clause must execute inside a
# ``session_with_tenant(...)`` async-context block. The tenant context
# binds ``app.tenant_id`` per session; PostgreSQL Row-Level Security
# uses that GUC to filter rows, and a naked raw query bypasses the
# filter the moment RLS policies fire.
#
# Behaviour:
#   - Greps src/ for ``text("...WHERE record_document_id...")``.
#   - For each hit, scans the same file backwards from the hit line for
#     ``session_with_tenant(`` within a fixed lookback window.
#   - Exits 1 if any hit has no preceding ``session_with_tenant(``.
#   - Exits 0 when every hit is wrapped.
#
# Excluded:
#   - tests/  (fixtures legitimately seed data via session_with_tenant
#              already; integration test_rls_cross_tenant.py exercises
#              the policy with raw text() calls inside a wrapper).
#   - alembic/versions/  (DDL migrations operate as superuser).
#
# Usage:
#   bash scripts/precommit_check_raw_sql_tenant_filter.sh
#   exit 0 = clean ; exit 1 = naked raw SQL found.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_ROOT="${REPO_ROOT}/src"

# How far back to scan for the wrapping ``session_with_tenant(`` call.
# Real call sites in document_service.py / pgvector_store.py sit within
# a few lines of their wrapper; 80 lines is a generous upper bound.
LOOKBACK_LINES=80

# Pattern: a SQLAlchemy ``text(...)`` literal that filters on
# record_document_id in its WHERE clause. We match across the open
# paren so multi-line text() calls (triple-quoted) are caught too.
PATTERN='text\(.*WHERE record_document_id'

violations=0

# grep -rnE: recursive, line numbers, extended regex.
# --include limits to .py source files. Output format: ``path:line:match``.
hits="$(grep -rnE \
  --include='*.py' \
  "${PATTERN}" \
  "${SRC_ROOT}" || true)"

if [[ -z "${hits}" ]]; then
  echo "OK — no raw 'text(...WHERE record_document_id...)' patterns in src/."
  exit 0
fi

while IFS= read -r line; do
  # Split ``path:line:rest``. We only need path + line.
  file_path="${line%%:*}"
  rest="${line#*:}"
  line_no="${rest%%:*}"

  # Compute the lookback window start (clamped to 1).
  start=$(( line_no - LOOKBACK_LINES ))
  if (( start < 1 )); then
    start=1
  fi

  # Slice the file from ``start`` to ``line_no`` and look for the
  # wrapper. ``sed`` here is a stream slice on a path we control, not
  # a destructive in-place edit.
  window="$(sed -n "${start},${line_no}p" "${file_path}")"

  if ! grep -q 'session_with_tenant(' <<<"${window}"; then
    echo "VIOLATION: ${file_path}:${line_no} — raw SQL filters on record_document_id without an enclosing session_with_tenant(...) within ${LOOKBACK_LINES} lines."
    violations=$(( violations + 1 ))
  fi
done <<<"${hits}"

if (( violations > 0 )); then
  echo ""
  echo "Found ${violations} raw-SQL site(s) missing session_with_tenant wrapper."
  echo "Fix: wrap the call in 'async with session_with_tenant(self._sf, record_tenant_id=...) as session:'"
  echo "Reference: docs/dev/CROSS_TENANT_AUDIT_RUNBOOK.md"
  exit 1
fi

echo "OK — every raw 'text(...WHERE record_document_id...)' site is wrapped in session_with_tenant(...)."
exit 0
