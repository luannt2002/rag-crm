#!/usr/bin/env bash
# scripts/validate_constants.sh — fast guard chạy sau mỗi Edit/Write
# vào package src/ragbot/shared/constants/ (split từ file monolith cũ).
#
# Race-lesson (memory project_fix_all_complete.md): nhiều orchestrator chạy
# song song có thể clobber constants — hook này verify ngay sau Edit để
# fail-loud thay vì để bug lặng silent vào commit.
#
# Checks (quét toàn package, mọi module _NN_*.py + __init__.py):
#   1. No version-ref tokens (_v1, _legacy, EMBEDDING_COLUMN_V3, ...)
#   2. No Sprint/Round/post-V/alembic-numbered comments
#   3. ruff format check (if ruff available in .venv)
#
# Exit 0 = clean. Non-zero = block.
set -u
cd "$(dirname "$0")/.." || exit 2

# Package dir (monolith constants.py was split into a package). Guard the whole
# tree so version-ref / temporal comments in ANY module are caught, not just one
# file — the original single-file target silently no-op'd after the split.
CONSTANTS_DIR=src/ragbot/shared/constants
if [ ! -d "$CONSTANTS_DIR" ]; then
  echo "[validate_constants] $CONSTANTS_DIR not found" >&2
  exit 2
fi

fail=0

# 1. version-ref tokens (CLAUDE.md rule: TUYỆT ĐỐI no version-ref).
# Exclude module filenames themselves (the _NN_ prefix is a sort key, not a
# version-ref) by scanning file *contents* only.
hits=$(grep -rnE "(_v[0-9]|_legacy|EMBEDDING_COLUMN_(V[0-9]|LEGACY))" \
  --include="*.py" "$CONSTANTS_DIR" || true)
if [ -n "$hits" ]; then
  echo "[validate_constants] ✗ version-ref tokens in $CONSTANTS_DIR:" >&2
  echo "$hits" >&2
  fail=1
fi

# 2. Sprint/Round/post-V comments (CLAUDE.md rule: WHY-only, no temporal)
hits=$(grep -rnE "Sprint S?[0-9]+|V[0-9]+\.[0-9]+\.[0-9]+|Round V[A-Z]|post-V[0-9]+" \
  --include="*.py" "$CONSTANTS_DIR" || true)
if [ -n "$hits" ]; then
  echo "[validate_constants] ✗ temporal/version comments in $CONSTANTS_DIR:" >&2
  echo "$hits" >&2
  fail=1
fi

# 3. ruff format check (advisory, best-effort, skip silently if missing).
# Non-blocking: the hook fires per-Edit but scans the whole package, so an
# unrelated module's format drift must NOT block an edit elsewhere. The
# load-bearing gates are 1+2 (version-ref / temporal). Surface format drift as
# a warning so the editor can run `ruff format` on demand.
RUFF=""
if [ -x ".venv/bin/ruff" ]; then RUFF=".venv/bin/ruff"; fi
if [ -z "$RUFF" ] && command -v ruff >/dev/null 2>&1; then RUFF="ruff"; fi
if [ -n "$RUFF" ]; then
  if ! "$RUFF" format --check --quiet "$CONSTANTS_DIR" >/dev/null 2>&1; then
    echo "[validate_constants] ⚠ ruff format would change files in $CONSTANTS_DIR — run: $RUFF format $CONSTANTS_DIR" >&2
  fi
fi

if [ $fail -eq 0 ]; then
  echo "[validate_constants] ✓ $CONSTANTS_DIR clean"
fi
exit $fail
