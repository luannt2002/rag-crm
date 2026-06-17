# aidlc Role Pipeline — Spec → Plan → Build → Review → Ship

> Map 4 role (`plan-by-role.md` ở `00-overview/`) vào 9-phase aidlc.

---

## 9-phase artifact gate

```
Phase 1: SPEC          ← Architect writes 01-spec.md
   ↓ Auto-Reviewer (Sonnet schema check)
Phase 2: DESIGN        ← Architect writes 02-design.md
   ↓ Auto-Reviewer
Phase 3: TASKS         ← Architect writes 03-tasks.md (file inventory + acceptance)
   ↓ Auto-Reviewer + USER APPROVE GATE
Phase 4: IMPL PLAN     ← Implementer writes 04-impl-plan.md (TDD strategy)
   ↓ Auto-Reviewer
Phase 5: CODE          ← Implementer commits Phase 0 + 1+ atomic
   ↓ pytest gate (all green or xfail)
Phase 6: TESTS         ← Implementer adds integration test
   ↓ pytest gate
Phase 7: REVIEW        ← Reviewer writes 07-review.md (Quality Gate 11-item)
   ↓ APPROVE / APPROVE-WITH-FIX / REJECT
Phase 8: VERIFICATION  ← Auditor writes 08-verify.md (load test result, HALLU=0)
   ↓ acceptance gate
Phase 9: SHIP          ← Auditor writes 09-ship.md (STATE_SNAPSHOT update, push)
```

---

## Per-phase artifact schema

### Phase 1 — `01-spec.md`

```markdown
# Spec — <stream-name>

## Problem
<concrete pain point with evidence file:line or load test number>

## Why now
<urgency: GA blocker, cost up, regression>

## Sacred contract impact
- HALLU=0: <safe / risky / sacred breach risk>
- 4-key bot identity: <touched / untouched>
- Domain-neutral: <impact>
- Zero-hardcode: <impact>

## Stakeholders
- Owner: <role>
- Implementer: <role>
- Reviewer: <role>
```

**Auto-Reviewer schema**:
- ✓ Has Problem section ≥ 50 chars
- ✓ Has Sacred contract impact (4 items)
- ✓ Has Stakeholders (3 roles)

### Phase 2 — `02-design.md`

```markdown
# Design — <stream-name>

## Approach
<high-level algorithm>

## File inventory
NEW:
- <path>

UPDATE:
- <path>:<line> — <what change>

## Risk
1. <risk>: <mitigation>
2. <risk>: <mitigation>

## Rollback
<step-by-step revert plan>
```

**Auto-Reviewer schema**:
- ✓ Has Approach
- ✓ Has File inventory (NEW + UPDATE)
- ✓ Has ≥ 1 risk + mitigation
- ✓ Has rollback steps

### Phase 3 — `03-tasks.md`

```markdown
# Tasks — <stream-name>

| # | Task | File | Effort |
|---|---|---|---|
| 1 | <task> | <file:line> | 30min |
| 2 | <task> | <file:line> | 1h |

## Acceptance
- [ ] <measurable criterion>
- [ ] <measurable criterion>
```

**Auto-Reviewer schema**:
- ✓ Has Tasks table ≥ 3 rows
- ✓ Has Acceptance ≥ 3 measurable items

### Phase 4 — `04-impl-plan.md`

```markdown
# Implementation Plan — <stream-name>

## Phase 0 — TDD failing tests
File: tests/unit/test_<name>.py
Tests:
- test_<contract>: <description>

## Phase 1+ — surgical code
Plan per phase with file:line.

## Test strategy
- Unit
- Integration (skip-by-default if needs DB/Redis)
```

### Phase 5 — CODE (multi-commit)

KHÔNG file artifact riêng. Implementer commit theo workflow CLAUDE.md atomic per phase. aidlc tracks bằng git log.

**Auto-Reviewer check** post-Phase 5:
- ✓ ≥ 1 commit per phase
- ✓ Commit message match pattern `<type>(stream-X): Phase N — <summary>`
- ✓ pytest passes for new tests
- ✓ No version-ref / no domain literal new in src/

### Phase 6 — TESTS (post-implementation)

Integration tests + smoke tests. Có thể defer skip-by-default (Stream L Phase 4 pattern).

### Phase 7 — `07-review.md`

```markdown
# Review — <stream-name> commit <hash>

## Quality Gate 11-item
1. Logic + edge cases: <PASS/FAIL>
2. Zero-hardcode: <PASS/FAIL + evidence>
3. Strategy + DI: <PASS/FAIL>
4. Tenant isolation: <PASS/FAIL>
5. RBAC: <PASS/FAIL>
6. 4-key bot identity: <PASS/FAIL>
7. Tests real: <PASS/FAIL>
8. Domain-neutral: <PASS/FAIL>
9. T1/T2/T3 declared: <PASS/FAIL>
10. App KHÔNG inject: <PASS/FAIL>
11. Model tier match: <PASS/FAIL>

## Verdict
APPROVE | APPROVE-WITH-FIX | REJECT

## Follow-up tasks (if APPROVE-WITH-FIX or REJECT)
- <task>
```

### Phase 8 — `08-verify.md`

```markdown
# Verification — <stream-name>

## Load test results (V_n vs V_(n-1))
| Metric | V_(n-1) | V_n | Δ |
|---|---|---|---|
| HALLU=0 | 15 round | 16 round | ✓ extended |
| p95 | 27.2s | 14s | -13.2s ✓ acceptance |
| Refuse rate | 37/90 | 35/90 | -2 (no recall regression) |

## Acceptance verification
| Criterion | Status |
|---|---|
| <criterion 1 from 03-tasks.md> | ✓ |
| <criterion 2> | ✓ |

## Verdict
SHIP | ROLLBACK | FOLLOW-UP
```

### Phase 9 — `09-ship.md`

```markdown
# Ship — <stream-name>

## STATE_SNAPSHOT update
Diff applied: <commit hash>

## Push status
git push origin main: <commit range>
Tag (if release): <tag>

## Owner notification
- Slack/email: <draft message>
- Doc updates: STATE_SNAPSHOT, MASTER_BACKLOG

## Residual issues for next sprint
- <issue>
- <issue>
```

---

## Role transition gates

Mỗi gate Auto-Reviewer (Sonnet) check schema. Nếu fail → REJECT, role hiện tại fix.

```
SPEC (Architect) ──schema check──▶ DESIGN (Architect)
                                          ↓
                                     TASKS (Architect)
                                          ↓ + USER APPROVE
                                   IMPL PLAN (Implementer)
                                          ↓
                                       CODE (Implementer)
                                          ↓ pytest gate
                                       TESTS (Implementer)
                                          ↓
                                     REVIEW (Reviewer)
                                          ↓ Quality Gate 11-item
                                  VERIFICATION (Auditor)
                                          ↓ load test
                                       SHIP (Auditor)
```

---

## Apply cho Stream D PROPER (example walkthrough)

### Phase 1 — Spec

Architect (em) writes `claude-ops/30-aidlc/epics/stream-d/01-spec.md`:
- Problem: V13 p95 27.2s vs GA 8s
- Sacred: HALLU=0 low risk
- Stakeholders: Anh (owner), Claude (architect + reviewer + auditor), implementer session (focused)

### Phase 2 — Design

Architect writes `02-design.md`:
- Approach: parallel intent fan-out via asyncio.gather
- Files: query_graph.py + constants.py + new test
- Risk: race in pgvector pool → mitigation share session_factory

### Phase 3 — Tasks

Architect writes `03-tasks.md` với task list + acceptance.

→ User approves gate.

### Phase 4-6 — Implementer (focused session)

Implementer ships Phase 0 TDD → Phase 1 helpers → Phase 2 parallel dispatch → Phase 3 merge → Phase 4 observability. Atomic commit per phase.

### Phase 7 — Review

Reviewer (em) writes `07-review.md` với Quality Gate 11-item.

### Phase 8-9 — Auditor

Auditor (em + anh) kick V14 load test, verify acceptance, update STATE_SNAPSHOT, ship.

---

## When to skip role split

| Scenario | Use aidlc 9-phase | Use plan tay |
|---|---|---|
| Big effort > 3 day | ✅ | |
| Multi-phase TDD | ✅ | |
| Schema migration | ✅ | |
| Doc-only change | | ✅ |
| Single-file refactor | | ✅ |
| Test pollution fix | | ✅ |

---

## Reference

- aidlc: https://github.com/hueanmy/aidlc-extension
- 9-phase pattern: native to aidlc framework
- Plan tay alternative: `plans/<id>/plan.md` (current Ragbot pattern)
- Memory: `reference_hueanmy_repos.md`
