#!/usr/bin/env bash
# cleanup_old_worktrees.sh — prune merged + aged git worktrees safely.
#
# Default mode is dry-run: lists every candidate but removes nothing.
# Pass `apply` as the third positional argument to actually remove
# the worktrees that are both merged into the base branch AND older
# than the age threshold.
#
# Usage:
#   scripts/cleanup_old_worktrees.sh                              # dry-run, defaults
#   scripts/cleanup_old_worktrees.sh main                         # base = main
#   scripts/cleanup_old_worktrees.sh main 14                      # 14-day threshold
#   scripts/cleanup_old_worktrees.sh main 7 apply                 # actually delete
#
# Safety:
#   - Main worktree (cwd of the repo) is always skipped.
#   - Branch must merge-base --is-ancestor into BASE before removal.
#   - Worktree path mtime must be older than MAX_AGE_DAYS.
#   - `git worktree remove --force` is only invoked in `apply` mode.
set -euo pipefail

BASE="${1:-coder-260518-W1-recovery-worker}"
MAX_AGE_DAYS="${2:-7}"
MODE="${3:-dry-run}"   # dry-run | apply

if ! command -v git >/dev/null 2>&1; then
  echo "[cleanup_old_worktrees] git not on PATH" >&2
  exit 2
fi

if [[ "$MODE" != "dry-run" && "$MODE" != "apply" ]]; then
  echo "[cleanup_old_worktrees] mode must be 'dry-run' or 'apply' (got: $MODE)" >&2
  exit 2
fi

echo "[cleanup_old_worktrees] base=$BASE max_age_days=$MAX_AGE_DAYS mode=$MODE"

# Resolve the main worktree path so we never remove it even if the user
# names it on the worktree-list output.
MAIN_WORKTREE="$(git rev-parse --show-toplevel 2>/dev/null || true)"

# Capture worktree-list output into a variable first so a SIGPIPE from
# the downstream Python process does not trip ``set -o pipefail``.
WORKTREE_LIST="$(git worktree list --porcelain)"

# Disable pipefail just for the heredoc pipeline: bash funnels the
# here-string through a transient FD that can race with python's exit.
set +o pipefail
printf '%s\n' "$WORKTREE_LIST" | BASE="$BASE" MAX_AGE_DAYS="$MAX_AGE_DAYS" MODE="$MODE" MAIN_WORKTREE="$MAIN_WORKTREE" python3 - <<'PY'
import os
import subprocess
import sys
import time
from pathlib import Path

base_branch = os.environ["BASE"]
max_age_seconds = int(os.environ["MAX_AGE_DAYS"]) * 86400
mode = os.environ["MODE"]
main_worktree = os.environ.get("MAIN_WORKTREE", "")
now = time.time()

worktrees: list[dict[str, str]] = []
current: dict[str, str] = {}
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if not line:
        if current:
            worktrees.append(current)
            current = {}
        continue
    key, _, value = line.partition(" ")
    current[key] = value
if current:
    worktrees.append(current)

removed = 0
listed = 0
for wt in worktrees:
    path = wt.get("worktree", "")
    branch = wt.get("branch", "").replace("refs/heads/", "")
    if not path or not branch:
        continue
    # Always skip the main worktree of the repo.
    if main_worktree and Path(path).resolve() == Path(main_worktree).resolve():
        continue
    p = Path(path)
    if not p.exists():
        print(f"  [MISSING ] {branch} → {path} (run: git worktree prune)")
        continue
    age = now - p.stat().st_mtime
    if age < max_age_seconds:
        continue
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, base_branch],
            check=True,
            capture_output=True,
        )
        merged = True
    except subprocess.CalledProcessError:
        merged = False
    days = int(age // 86400)
    status = "MERGED" if merged else "OPEN  "
    print(f"  [{status}] {branch} ({days}d old) → {path}")
    listed += 1
    if mode == "apply" and merged:
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", path],
            capture_output=True,
        )
        if result.returncode == 0:
            print(f"    REMOVED {path}")
            removed += 1
        else:
            print(f"    REMOVE FAILED: {result.stderr.decode(errors='replace').strip()}")

print()
print(f"[cleanup_old_worktrees] listed={listed} removed={removed} mode={mode}")
PY
PYTHON_RC=$?
set -o pipefail
exit "$PYTHON_RC"
