# Plan by Role — Template viết plan theo role rõ ràng

> **Trigger**: anh hỏi "có thể viết plan theo role hay gì đó". File này = template chia plan ra 4 role (Architect / Implementer / Reviewer / Auditor) để Claude session khác nhau pickup đúng phần của mình, atomic commit, scope rõ.

---

## Why role-based plan?

### Vấn đề "plan thông thường"

Một plan như `plans/260506-streamA-doc-pipeline/plan.md` mô tả TẤT CẢ phase + files + acceptance criteria trong 1 file. Khi agent pickup:
- Không rõ Phase nào "thiết kế" vs Phase nào "code"
- Có thể ship Phase 1 nhưng KHÔNG check Phase 0 TDD trước (bundling risk)
- Plan = monolith → review khó
- Risk scope drift (commit subject claims paper N nhưng diff thật là subset)

### Solution: tách plan thành 4 role artifact

Mỗi plan = 4 file rõ scope per role. Agent đọc đúng phần của mình.

---

## 4 role + artifact format

### Role 1 — ARCHITECT (Spec + Acceptance)

**Output artifact**: `plans/<id>/01-architect.md`

**Trách nhiệm**:
- Identify problem (root cause analysis, evidence file:line)
- Define acceptance criteria (measurable: HALLU=0 hold, p95 ≤14s, etc.)
- Identify sacred contract impact (HALLU/4-key/domain-neutral/zero-hardcode)
- List file inventory (what NEW, what UPDATE)
- Risk assessment + rollback plan

**KHÔNG làm**:
- Code
- Implementation detail
- Test code

**Model tier**: Opus 4.7 (deepdive analysis)

**Example**:
```markdown
# Architect — Stream D PROPER Paper 26

## Problem
V13 90Q load test p95 = 27.2s (cold) / 23.8s (warm). GA target 8s.
3× quá target = blocker.

## Evidence
- reports/LOADTEST_90Q_FULLMINI_1778018956.json `latency.p95_ms = 27200`
- Paper 26 RAGO arXiv 2503.14649: parallel intent fan-out → -55% TTFT

## Acceptance
| Metric | Trước | Sau |
|---|---|---|
| p95 cold | 27.2s | ≤ 14s |
| HALLU=0 | hold | hold |
| Refuse rate | 37/90 | ≤ 37/90 |

## Sacred contract impact
- HALLU=0: low risk (answer-path unchanged, LITM reorder unchanged)
- 4-key: untouched
- Domain-neutral: ✅ intent groups in constants.py

## File inventory
- NEW: tests/unit/test_rago_parallel_fanout.py
- UPDATE: src/ragbot/shared/constants.py (INTENT_PARALLEL_GROUPS)
- UPDATE: src/ragbot/orchestration/query_graph.py (3 helpers + 1 node)

## Risk + rollback
Feature flag `parallel_intent_fanout_enabled` default OFF first deploy.
Phase-by-phase commit; revert if HALLU > 0 or refuse rate +5pp.
```

---

### Role 2 — IMPLEMENTER (Code + TDD)

**Output artifact**: `plans/<id>/02-implementer.md` (tracking) + actual code commits

**Trách nhiệm**:
- Phase 0 — failing tests TDD (xfail markers)
- Phase 1+ — surgical code per file inventory ARCHITECT defined
- Atomic commit per phase: `feat(stream-X): Phase N — <accurate scope>`
- Run pytest sau mỗi phase
- Self-grep verify (zero-hardcode, no version-ref, broad-except annotation)

**KHÔNG làm**:
- Modify acceptance criteria (đó là ARCHITECT scope)
- Skip Phase 0 TDD
- Bundle multi-stream commits

**Model tier**: Opus 4.7 (write code = T-A WRITE per CLAUDE.md)

**Workflow**:
```
1. Đọc 01-architect.md → confirm scope
2. Phase 0: write failing tests, pytest -v → confirm RED
3. Commit: test(stream-X): Phase 0 — failing tests TDD
4. Phase 1: implement, pytest -v → green
5. Commit: feat(stream-X): Phase 1 — <summary>
6. Repeat per phase
7. Update 02-implementer.md checkbox
```

---

### Role 3 — REVIEWER (Quality Gate + Sacred audit)

**Output artifact**: `plans/<id>/03-reviewer.md`

**Trách nhiệm**:
- Run 11-item Quality Gate trên mỗi commit (CLAUDE.md)
- Self-grep audit:
  - `grep -rnE "(_v[0-9]|_legacy|EMBEDDING_COLUMN_(V[0-9]|LEGACY))" src/` → 0 hits
  - `grep -rnE "Sprint S?[0-9]+|V[0-9]+\.[0-9]+\.[0-9]+|Round V[A-Z]" src/` → 0 hits
  - `bash scripts/grep_domain_literals.sh` → no NEW hits
  - `grep "except Exception" src/ | grep -v "noqa: BLE001"` → 0 hits (sacred)
- Verify acceptance criteria từ 01-architect.md đáp ứng đủ
- Verify sacred contracts intact (HALLU/4-key/domain-neutral/zero-hardcode)
- APPROVE / APPROVE-WITH-FIX / REJECT verdict

**KHÔNG làm**:
- Write code
- Modify scope
- Override sacred (escalate user nếu conflict)

**Model tier**: Sonnet 4.6 OK (subagent read-only review) — nhưng bị harness limitation hiện tại, dùng Opus.

**Output format**:
```markdown
# Review Stream D — commit <hash>

| Item | Status | Evidence |
|---|---|---|
| 1. Logic + edge cases | ✅ | tests pass 21/21 |
| 2. Zero-hardcode | ✅ | INTENT_PARALLEL_GROUPS in constants.py |
| 3. Strategy + DI | ✅ | helpers added, no provider hardcode |
| 4. Tenant isolation | ✅ | scoped record_tenant_id |
| 5. RBAC | N/A (no new endpoint) | |
| 6. 4-key bot identity | ✅ | unchanged |
| 7. Tests real | ✅ | assert wall-clock < sum, real timing assertion |
| 8. Domain-neutral | ✅ | no brand literal |
| 9. T1/T2/T3 declared | ✅ | T2 perf in commit body |
| 10. App KHÔNG inject | ✅ | runtime prompt unchanged |
| 11. Model tier match | ✅ | main session edit |

VERDICT: APPROVE
```

---

### Role 4 — AUDITOR (Post-deploy verify)

**Output artifact**: `plans/<id>/04-auditor.md`

**Trách nhiệm**:
- Sau ship, kick load test verify acceptance criteria thật sự đạt
- Compare V_n vs V_(n-1): HALLU rate, refuse rate, p95, cost
- Update STATE_SNAPSHOT.md với số mới
- Document residual issues for next sprint
- Final ship verdict

**KHÔNG làm**:
- Write production code
- Override architect acceptance (re-define scope)

**Model tier**: Opus 4.7 (deepdive judge)

**Workflow**:
```
1. bash scripts/loadtest_kick.sh agent_d_loadtest.py …
2. python scripts/read_loadtest_result.py --latest
3. bash scripts/loadtest_kick.sh reclassify_loadtest.py …
4. Compare expected vs actual:
   - HALLU=0? → sacred
   - p95 ≤ 14s? → architect acceptance
   - Refuse rate ≤ baseline? → no recall regression
5. Verdict: SHIP / ROLLBACK / FOLLOW-UP
```

---

## Coordination giữa role

```
ARCHITECT
   ↓ writes 01-architect.md
   ↓ user approves scope
IMPLEMENTER
   ↓ Phase 0 TDD → Phase N+
   ↓ commits atomic per phase
REVIEWER
   ↓ Quality Gate 11-item per commit
   ↓ APPROVE → push
   ↓ REJECT → loop back IMPLEMENTER
AUDITOR
   ↓ post-ship load test verify
   ↓ SHIP / ROLLBACK / FOLLOW-UP
```

**Realistically** — 1 Claude session có thể play multiple role nếu không bundling. Vd:
- Em (Claude Opus 4.7 main): ARCHITECT + REVIEWER + AUDITOR
- External implementer agent (nếu có): IMPLEMENTER role
- Anh: AUDITOR final + override decisions

---

## Apply cho Ragbot

### Stream D PROPER (đang chờ ship)

| Role | File | Owner | Status |
|---|---|---|---|
| Architect | `plans/260506-streamD-rago-pareto/plan.md` | Claude Opus 4.7 | ✅ DONE |
| Implementer | (commits when phase ships) | Claude focused session | ⏳ pending |
| Reviewer | `plans/260506-streamD-rago-pareto/03-reviewer.md` | Claude Opus 4.7 main | post-implementer |
| Auditor | `plans/260506-streamD-rago-pareto/04-auditor.md` | Claude Opus 4.7 + load test | post-ship |

### Stream L Phase 4

| Role | File | Owner |
|---|---|---|
| Architect | (skip — small change, plan.md đủ) | — |
| Implementer | `@pytest.mark.integration` skip-by-default | Em (focused 2-3h) |
| Reviewer | inline trong commit message | Em + anh |
| Auditor | full-suite fail count → ≤30 | Em |

---

## When NOT to use role-based plan

- **Trivial task** (typo, đổi tên var) — skip role split, atomic commit đủ
- **Doc-only** changes — Architect optional, Implementer + Reviewer đủ
- **Owner action** (kick scripts) — không có role split, just runbook

Stream D PROPER + Stream E Cache là use case lý tưởng cho role split (>3 day effort, multi-phase, schema impact).

---

## Reference

- CLAUDE.md "MANDATORY: /plan + Honest verification"
- CLAUDE.md atomic commit rule + Quality Gate 11-item
- `plans/260506-streamD-rago-pareto/plan.md` (current Architect doc — em gộp 4 role thành 1 file vì stream nhỏ-medium)
