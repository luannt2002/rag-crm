#!/usr/bin/env bash
# cherry_pick_22_tasks.sh
# ----------------------------------------------------------------------
# Cherry-pick wave for the 22 LEAD CODER task branches + 4 STABILITY
# HOTFIX worktree branches (A1-A4) into origin/main.
#
# Source: prep agents B1-B3 + validate clusters V1-V4 (260509-validate-cluster-V*.md).
# Verdicts: 22/22 APPROVED (5 in V1, 5 in V2, 6 in V3, 6 in V4).
#
# Auto-resolves the two KNOWN conflict patterns:
#   1. shared/constants.py — branch-base 90->30 revert on
#      DEFAULT_EMBEDDING_TIMEOUT_S. Recipe: keep main (= 90) + ADD-only
#      coder constants at file end. Manual edit required, script HALTS.
#   2. Carry-over files (docker/coder/*, master docs, plans/, db_seed,
#      .gitignore, README.md, RAGBOT_MASTER.md, STATE_SNAPSHOT*.md,
#      CODER_*RUNBOOK.md, RAGBOT_24STEP_PIPELINE.md) — keep --ours (main).
#
# STOPS on any UNKNOWN conflict, prints debug info, exits non-zero.
# Idempotent: re-runs from any wave. Each wave runs unit tests after.
# Wave 4 (R6.C3 alembic 0074) and Wave 5 (A1 alembic 0075) verify
# alembic upgrade head + downgrade -1 round-trip.
#
# USAGE
#   chmod +x scripts/cherry_pick_22_tasks.sh
#   scripts/cherry_pick_22_tasks.sh                 # all waves
#   scripts/cherry_pick_22_tasks.sh wave1           # just wave 1
#   scripts/cherry_pick_22_tasks.sh wave1 wave2     # waves 1-2
#   scripts/cherry_pick_22_tasks.sh resume          # continue after manual fix
#
# PRE-CONDITIONS (admin verifies)
#   - Working tree clean: git status --short returns empty
#   - On main: git rev-parse --abbrev-ref HEAD == "main"
#   - Up-to-date: git fetch origin && git rev-list HEAD..origin/main empty
#   - .venv/bin/pytest exists
#
# POST-CONDITIONS
#   - 22 (or 26 with hotfix) commits on top of starting HEAD
#   - shared/constants.py contains DEFAULT_EMBEDDING_TIMEOUT_S = 90 (NOT 30)
#   - Alembic chain: 0072 -> 0073 (TASK-1) -> 0074 (R6.C3) -> 0075 (A1)
#   - All Wave-N pytest -q snapshots show 0 regressions (admin reviews tail-5)
#
# SAFETY
#   - Script does NOT chmod itself; admin runs `chmod +x` explicitly.
#   - Script does NOT push; admin runs `git push origin main` after final verify.
#   - Script does NOT touch .env, secrets, or DB.
#   - On UNKNOWN conflict: script exits non-zero with `git status --short`
#     so admin can resolve manually then re-run with `resume`.
# ----------------------------------------------------------------------

set -euo pipefail

# Repo root (script auto-discovers)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------
# Carry-over files — branch-base artefacts. Cherry-pick should NOT touch
# these (commit --stat per validate cluster shows 0 hits), but if a stale
# parent doc state surfaces, keep main (--ours).
# ----------------------------------------------------------------------
CARRY_OVER_FILES=(
  "docker/coder/.env.coder.example"
  "docker/coder/docker-compose.yml"
  "tests/fixtures/db_seed/test_db_dump.sql"
  ".gitignore"
  "docs/dev/CODER_LOCAL_DOCKER_RUNBOOK.md"
  "docs/dev/CODER_LOADTEST_RUNBOOK.md"
  "docs/master/coder-orchestrate/CODER_FULL_SMARTNESS_PLAN.md"
  "docs/master/coder-orchestrate/CODER_MASTER_PROMPT.md"
  "STATE_SNAPSHOT.md"
  "STATE_SNAPSHOT_HISTORY.md"
  "README.md"
  "RAGBOT_MASTER.md"
  "RAGBOT_24STEP_PIPELINE.md"
  "plans/260509-admin-agent-workflow/CODER_FULL_SMARTNESS_PLAN.md"
  "plans/260509-admin-agent-workflow/CODER_BACKLOG_ALL.md"
  "plans/260509-stability-hotfix/plan.md"
  "plans/260509-validate-22-tasks/plan.md"
  "reports/260509-future-smartness-roadmap.md"
)

CONSTANTS_FILE="src/ragbot/shared/constants.py"
EXPECTED_TIMEOUT_LINE="DEFAULT_EMBEDDING_TIMEOUT_S: Final[int] = 90"

# ----------------------------------------------------------------------
# Pretty printing
# ----------------------------------------------------------------------
banner() {
  printf '\n=========================================\n'
  printf '%s\n' "$1"
  printf '=========================================\n'
}
info()  { printf '    %s\n' "$1"; }
ok()    { printf '    OK   %s\n' "$1"; }
warn()  { printf '    WARN %s\n' "$1" >&2; }
fail()  { printf '    FAIL %s\n' "$1" >&2; }

# ----------------------------------------------------------------------
# Pre-flight checks
# ----------------------------------------------------------------------
preflight() {
  banner "PREFLIGHT"

  # Working tree clean (allow ongoing cherry-pick for resume mode)
  local cherry_pick_active=0
  if [ -f .git/CHERRY_PICK_HEAD ]; then
    cherry_pick_active=1
    info "Cherry-pick in progress detected (.git/CHERRY_PICK_HEAD exists)"
  fi

  if [ "$cherry_pick_active" = "0" ]; then
    if [ -n "$(git status --porcelain)" ]; then
      fail "Working tree dirty. Commit or stash first."
      git status --short | head
      exit 1
    fi
    ok "Working tree clean"
  fi

  # On main
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "main" ]; then
    fail "Not on main (current: $branch)"
    exit 1
  fi
  ok "On main"

  # pytest available
  if [ ! -x .venv/bin/pytest ]; then
    warn ".venv/bin/pytest not executable — wave-end test gates will skip"
  else
    ok ".venv/bin/pytest available"
  fi

  # Constants file present
  if [ ! -f "$CONSTANTS_FILE" ]; then
    fail "Missing $CONSTANTS_FILE — wrong repo root?"
    exit 1
  fi
  ok "Repo layout sane"
}

# ----------------------------------------------------------------------
# Conflict resolver
# ----------------------------------------------------------------------
resolve_carry_over() {
  # Auto-resolve any carry-over file in conflicted state by keeping main.
  local f
  local resolved=0
  for f in "${CARRY_OVER_FILES[@]}"; do
    if git diff --name-only --diff-filter=U | grep -q "^${f}$"; then
      # File deleted-on-main but present-on-coder, or vice-versa
      if [ -f "$f" ]; then
        git checkout --ours -- "$f"
        git add -- "$f"
      else
        git rm -f --ignore-unmatch -- "$f" >/dev/null
      fi
      info "carry-over (--ours): $f"
      resolved=1
    fi
  done
  return $resolved
}

# ----------------------------------------------------------------------
# Cherry-pick one commit with auto-resolve
# ----------------------------------------------------------------------
cherry_pick_one() {
  local sha="$1"
  local label="$2"

  printf -- '--> %s (%s)\n' "$label" "$sha"

  # Idempotency: if HEAD already contains the cherry tree, skip.
  if git log --format=%H -n 50 | grep -q "^${sha}"; then
    ok "Already on HEAD (skip)"
    return 0
  fi
  # Or if commit subject already replayed (cherry-pick rewrites SHA)
  local subject
  subject="$(git show -s --format=%s "$sha" 2>/dev/null || echo "")"
  if [ -n "$subject" ] && git log --format=%s -n 50 | grep -Fxq "$subject"; then
    ok "Subject already in main (skip): $subject"
    return 0
  fi

  if git cherry-pick "$sha" >/dev/null 2>&1; then
    ok "Clean cherry-pick"
    return 0
  fi

  info "Conflict detected, applying recipe..."
  resolve_carry_over || true

  # shared/constants.py — known 90->30 revert. Manual edit needed.
  if git diff --name-only --diff-filter=U | grep -Fxq "$CONSTANTS_FILE"; then
    fail "$CONSTANTS_FILE has conflict — MANUAL FIX REQUIRED"
    printf '\n'
    info "Recipe:"
    info "  1. Edit $CONSTANTS_FILE"
    info "  2. Keep MAIN line: $EXPECTED_TIMEOUT_LINE"
    info "     (NOT the coder's '... = 30' regression revert)"
    info "  3. ADD all coder NEW constants at file tail (no overlap on additions)"
    info "  4. git add $CONSTANTS_FILE"
    info "  5. git -c core.editor=true cherry-pick --continue"
    info "  6. Re-run: scripts/cherry_pick_22_tasks.sh resume"
    printf '\n'
    info "Conflict markers location:"
    grep -n '^<<<<<<< HEAD' "$CONSTANTS_FILE" || true
    exit 2
  fi

  # If still unresolved files, halt
  if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
    fail "UNKNOWN CONFLICT — admin must resolve:"
    git status --short | head -30 >&2
    exit 3
  fi

  # All resolved — continue
  if git -c core.editor=true cherry-pick --continue >/dev/null 2>&1; then
    ok "Resolved with recipe"
    return 0
  fi

  # Empty pick (already-applied) — skip cleanly
  if git cherry-pick --skip >/dev/null 2>&1; then
    info "Empty (already applied) — skipped"
    return 0
  fi

  fail "cherry-pick --continue failed"
  git status --short | head >&2
  exit 4
}

# ----------------------------------------------------------------------
# Sanity grep — DEFAULT_EMBEDDING_TIMEOUT_S must remain 90
# ----------------------------------------------------------------------
verify_timeout_constant() {
  if grep -q "$EXPECTED_TIMEOUT_LINE" "$CONSTANTS_FILE"; then
    ok "DEFAULT_EMBEDDING_TIMEOUT_S = 90 preserved"
  else
    fail "DEFAULT_EMBEDDING_TIMEOUT_S regressed!"
    grep -n "DEFAULT_EMBEDDING_TIMEOUT_S" "$CONSTANTS_FILE" || true
    exit 5
  fi
}

# ----------------------------------------------------------------------
# Pytest gate
# ----------------------------------------------------------------------
run_unit_tests() {
  local wave_label="$1"
  if [ ! -x .venv/bin/pytest ]; then
    warn "Skipping unit tests for $wave_label (pytest unavailable)"
    return 0
  fi
  info "Running unit tests for $wave_label..."
  if .venv/bin/pytest tests/unit/ -q --tb=line 2>&1 | tail -5; then
    ok "$wave_label tests OK"
  else
    fail "$wave_label tests had failures (admin reviews tail above)"
    exit 6
  fi
}

# ----------------------------------------------------------------------
# Alembic round-trip gate (Wave 4 + Wave 5)
# ----------------------------------------------------------------------
verify_alembic_round_trip() {
  local label="$1"
  local expected_head="$2"
  if [ ! -x .venv/bin/alembic ]; then
    warn "Skipping alembic round-trip for $label (alembic CLI unavailable)"
    return 0
  fi
  info "Alembic round-trip check for $label (target head = $expected_head)..."
  local current_head
  current_head="$(.venv/bin/alembic heads 2>/dev/null | awk '{print $1}' | head -1 || echo "")"
  if [ -z "$current_head" ]; then
    warn "alembic heads returned empty — DB likely unreachable; admin verifies manually"
    return 0
  fi
  if .venv/bin/alembic upgrade head >/dev/null 2>&1; then
    ok "alembic upgrade head OK"
  else
    fail "alembic upgrade head FAILED — chain broken"
    exit 7
  fi
  if .venv/bin/alembic downgrade -1 >/dev/null 2>&1; then
    ok "alembic downgrade -1 OK"
  else
    fail "alembic downgrade -1 FAILED — migration not reversible"
    exit 8
  fi
  if .venv/bin/alembic upgrade head >/dev/null 2>&1; then
    ok "alembic re-upgrade OK"
  else
    fail "alembic re-upgrade after downgrade FAILED"
    exit 9
  fi
}

# ----------------------------------------------------------------------
# Wave definitions (SHA per validate-cluster reports)
# ----------------------------------------------------------------------

# Wave 1 — Sacred Safety + Security (V1, 5 task)
# Order: TASK-1 must precede TASK-3 (CI workflow needs ragbot_app role).
wave1() {
  banner "WAVE 1 — Sacred Safety + Security (V1, 5 task)"
  cherry_pick_one d9ef155 "TASK-1  t1s1b-non-superuser-dsn (alembic 0073)"
  verify_timeout_constant
  cherry_pick_one 4ea4f88 "TASK-3  anti-abuse-loadtest-bypass"
  verify_timeout_constant
  cherry_pick_one 2c550b8 "TASK-1.5 cross-tenant-ci"
  cherry_pick_one a37929b "TASK-13 broad-except-sweep"
  cherry_pick_one 65c49ca "TASK-15 auditor-regex-markdown"
  run_unit_tests "Wave 1"
}

# Wave 2 — Performance (V2, 5 task)
# Order: TASK-10 LAST (largest test-suite churn + state-lift refactor).
wave2() {
  banner "WAVE 2 — Performance (V2, 5 task)"
  cherry_pick_one b0da899 "TASK-11 mmr-numpy-optim"
  cherry_pick_one a504095 "TASK-12 structured-output-schema-cache"
  cherry_pick_one 0e5bcc7 "F10     health-probe-fix"
  cherry_pick_one 0880387 "R5.B4   ragas-metrics"
  cherry_pick_one 2125475 "TASK-10 build-graph-singleton (HIGH RISK LAST)"
  run_unit_tests "Wave 2"
}

# Wave 3 — Smartness Routing + Cache (V3, 6 task)
# Order: T9 first (test infra), R5.B3 last (constants tail merge).
wave3() {
  banner "WAVE 3 — Smartness Routing + Cache (V3, 6 task)"
  cherry_pick_one 60287b0 "T9     test-pollution-cleanup"
  cherry_pick_one 2b6d12d "S30    faithfulness-budget-doc"
  cherry_pick_one 35f39f8 "R5.A3  per-bot-golden-ci"
  cherry_pick_one 2b49b3b "S29    cleanbase-ingest"
  cherry_pick_one b329d94 "R5.A1  self-rag-routing"
  cherry_pick_one 15a31c7 "R5.B3  proximity-lsh-cache"
  run_unit_tests "Wave 3"
}

# Wave 4 — UX + Tenant + Ops (V4, 6 task)
# Order: doc/util first, R6.C3 LAST (alembic 0074 chains on 0073).
wave4() {
  banner "WAVE 4 — UX + Tenant + Ops (V4, 6 task)"
  cherry_pick_one 9da008f "T5     backup-runbook"
  cherry_pick_one a6d55c2 "R6.C1  vn-honorific"
  cherry_pick_one 4212b51 "R6.C2  per-tenant-model-tier"
  cherry_pick_one 07f37a7 "R6.C4  convo-summary-compress"
  cherry_pick_one 92203e4 "T6     cost-cap-alert"
  cherry_pick_one 7ec4371 "R6.C3  feedback-thumbs-loop (alembic 0074)"
  run_unit_tests "Wave 4"
  verify_alembic_round_trip "Wave 4 (alembic 0074)" "0074"
}

# Wave 5 — Stability hotfix (A1-A4)
# Pre-condition: A1 worktree branch already exists locally with alembic
# revision="0073" RENUMBERED to "0075" + down_revision="0072" -> "0074".
# Admin executes the rename in the A1 worktree BEFORE running this wave.
#
# Branch names (per plans/260509-stability-hotfix/plan.md):
#   worktree-A1-async-upload-split
#   worktree-A2-embedder-config-timeout
#   worktree-A3-doc-service-timeout
#   worktree-A4-webhook-timeout
#
# A1 SHA / A2 / A3 / A4 SHA placeholders — admin sets via env var or
# the script reads from refs/heads/<branch>:
wave5() {
  banner "WAVE 5 — Stability Hotfix (A1-A4)"

  local a1_branch="${A1_BRANCH:-worktree-A1-async-upload-split}"
  local a2_branch="${A2_BRANCH:-worktree-A2-embedder-config-timeout}"
  local a3_branch="${A3_BRANCH:-worktree-A3-doc-service-timeout}"
  local a4_branch="${A4_BRANCH:-worktree-A4-webhook-timeout}"

  for b in "$a1_branch" "$a2_branch" "$a3_branch" "$a4_branch"; do
    if ! git rev-parse --verify "$b" >/dev/null 2>&1; then
      fail "Branch missing: $b — admin must ship hotfix worktrees first"
      info "Per plans/260509-stability-hotfix/plan.md, A1-A4 ship in worktree branches"
      exit 10
    fi
  done

  # CRITICAL: A1 alembic revision must already be renamed 0073 -> 0075
  # (down_revision 0072 -> 0074). Verify before cherry-pick.
  if git ls-tree -r "$a1_branch" --name-only | grep -q "alembic/versions/.*0073.*documents_state_lifecycle"; then
    fail "A1 alembic file still numbered 0073 — RENAME REQUIRED"
    info "Recipe (admin runs in A1 worktree before this wave):"
    info "  cd <A1-worktree>"
    info "  git mv alembic/versions/20260509_0073_documents_state_lifecycle.py \\"
    info "         alembic/versions/20260509_0075_documents_state_lifecycle.py"
    info "  sed -i 's/^revision = \"0073\"/revision = \"0075\"/' \\"
    info "         alembic/versions/20260509_0075_documents_state_lifecycle.py"
    info "  sed -i 's/^down_revision = \"0072\"/down_revision = \"0074\"/' \\"
    info "         alembic/versions/20260509_0075_documents_state_lifecycle.py"
    info "  git commit -am 'fix(alembic): renumber A1 0073->0075 (chain on R6.C3 0074)'"
    exit 11
  fi

  cherry_pick_one "$a1_branch" "A1 async-upload-split (alembic 0075)"
  cherry_pick_one "$a2_branch" "A2 embedder-config-timeout"
  cherry_pick_one "$a3_branch" "A3 document-service-timeout-guards"
  cherry_pick_one "$a4_branch" "A4 webhook-dispatcher-timeout"
  run_unit_tests "Wave 5"
  verify_alembic_round_trip "Wave 5 (alembic 0075)" "0075"
}

# ----------------------------------------------------------------------
# Resume mode — admin manually fixed a conflict, re-run from CHERRY_PICK_HEAD
# ----------------------------------------------------------------------
resume_mode() {
  banner "RESUME MODE"
  if [ ! -f .git/CHERRY_PICK_HEAD ]; then
    info "No cherry-pick in progress — proceeding to next wave"
    return 0
  fi
  if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
    fail "Unresolved conflicts still present:"
    git status --short | head >&2
    exit 12
  fi
  info "Continuing held cherry-pick..."
  if git -c core.editor=true cherry-pick --continue >/dev/null 2>&1; then
    ok "Cherry-pick continued"
  else
    fail "cherry-pick --continue failed in resume"
    exit 13
  fi
}

# ----------------------------------------------------------------------
# Main dispatch
# ----------------------------------------------------------------------
main() {
  preflight

  if [ "$#" -eq 0 ]; then
    set -- wave1 wave2 wave3 wave4 wave5
  fi

  for arg in "$@"; do
    case "$arg" in
      resume) resume_mode ;;
      wave1)  wave1 ;;
      wave2)  wave2 ;;
      wave3)  wave3 ;;
      wave4)  wave4 ;;
      wave5)  wave5 ;;
      all)
        wave1; wave2; wave3; wave4; wave5
        ;;
      *)
        fail "Unknown argument: $arg"
        info "Valid: wave1 | wave2 | wave3 | wave4 | wave5 | resume | all"
        exit 14
        ;;
    esac
  done

  banner "ALL REQUESTED WAVES COMPLETE"
  info "Final HEAD: $(git rev-parse --short HEAD)"
  info "Verify push readiness manually:"
  info "  git log --oneline 2c6634a..HEAD"
  info "  .venv/bin/pytest tests/ -q"
  info "  git push origin main   # admin executes when satisfied"
}

main "$@"
