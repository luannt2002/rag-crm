# Implementation Plan: RAG Truth Audit

**Branch**: `001-rag-truth-audit` | **Date**: 2026-07-03 | **Spec**: [spec.md](spec.md)

**Tier**: [T1-Smartness] — bot trả lời chính xác hơn (CORE MVP ưu tiên cao nhất).

**Input**: Feature specification from `/specs/001-rag-truth-audit/spec.md`

## Summary

Evidence-only re-verification of the Ragbot answer pipeline on reference bot `chinh-sach-xe`:
grade all 12 stages L1/L2/L3 (truth table), statistically measure the shell-entity
hallucination class (N=15 repeated runs) BEFORE fixing, force the owner decision on 69
price-less shell entities, add a deterministic numeric-fidelity guard (observe-mode first),
then ship remediations one-at-a-time with pinned-set deltas. Technical approach: reuse the
existing trace harness (`scripts/rag_trace_capture.py`) and DB-anchored grading pattern;
add a `--repeat` mode with cache-bypass assertion; implement the numeric-fidelity check as a
new observe-only component in the output-guard stage; all remediation code schema-keyed and
platform-neutral.

## Technical Context

**Language/Version**: Python 3.12 (existing `.venv`)

**Primary Dependencies**: FastAPI, LangGraph (`orchestration/query_graph.py` + `nodes/`),
SQLAlchemy/asyncpg (pgvector), Redis, httpx (harness), structlog

**Storage**: PostgreSQL — `document_chunks`, `document_service_index` (stats entities),
`bots.plan_limits` (per-bot knobs); audit artifacts as JSON/MD files under
`specs/001-rag-truth-audit/evidence/` + `reports/`

**Testing**: pytest (unit + regression pins); live harness via
`scripts/rag_trace_capture.py` against `POST /api/ragbot/test/chat` (loopback, bypass token)

**Target Platform**: Linux server (existing deployment), single-node

**Project Type**: Brownfield audit program — analysis artifacts + minimal guarded code
changes to one shared pipeline path

**Performance Goals**: harness N=15×9 probe questions ≤ ~20 min wall-clock (parallel,
semaphore ≤ 6 — respects RAGAS-parallel rule); numeric-fidelity observe check adds ≤ 5 ms
p50 per answer (pure string/set ops, no model call, no extra DB round-trip beyond served
context)

**Constraints**: sacred #10 — no answer modification in observe mode (structlog + trace
field only); HALLU=0 release bar; corpus version stamped per run; one remediation per step;
platform-neutral (schema-keyed, zero-hardcode, no Vietnamese literals in `src/`)

**Scale/Scope**: 1 reference bot, corpus 401 chunks / 242 stats entities; pinned 60-question
set + 9-question probe set; 12 pipeline stages to grade; ≤ 3 code-touching remediations
in scope

**Reference anchors (verified this session — evidence baseline B1–B5 in spec)**:
- Bot `chinh-sach-xe` `record_bot_id=c6e1fc56-d070-439d-99a6-c8b4964b4d2d`, corpus
  2026-07-03: 4 docs / 401 chunks (all embedded) / 242 entities (172 priced, 69 shell,
  6 with stray `date1:"26"`)
- Sparse-drop: `src/ragbot/shared/document_stats.py:664-666`
- Silent null-price formatter: `src/ragbot/orchestration/query_graph.py` ~2465–2500
- Grounding gate (observe/block, per-bot): `src/ragbot/orchestration/nodes/guard_output.py`
  (commit `c0c0dea`)
- Two answer paths: stats-synthetic chunk (score=1.0) vs raw document chunk (score<1.0) —
  every fix declares which path it covers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — Constitution
v1.0.0, all 10 principles mapped:

| Principle | Plan compliance |
|---|---|
| P-I EXISTS≠WORKS≠VERIFIED-GOOD | Phase A truth table grades L1/L2/L3/disabled per stage; template forbids ungraded claims |
| P-II No-Guess | Every plan claim above cites session evidence or file:line; unknowns → research.md, labeled |
| P-III Statistical evidence | Phase B harness N=15/question runs BEFORE any remediation (hard ordering in tasks) |
| P-IV Deterministic gates | Phase D numeric-fidelity component is model-independent; prompt-level fixes classified "probability layer" only |
| P-V Business-question-first | Phase C decision record (3 costed options) BLOCKS remediation scoping (gate) |
| P-VI Red-test-first, one-at-a-time | Phase E ladder:每 step = red test + single toggle + pinned re-run + rollback rule; grounding-block isolated in its own step (FR-010) |
| P-VII Platform-neutral | All remediation keyed on `price_primary IS NULL`; constants in `shared/constants.py`; no bot/brand literals |
| P-VIII HALLU=0 + Coverage | Every re-run reports both HALLU and coverage columns (chuẩn/refuse-oan/deflect-oan) |
| P-IX Blast-radius | Each code change ships with consuming-flows statement + pin tests (template section in tasks) |
| P-X Evidence artifacts | All verdicts link `reports/` / `evidence/` paths; harness outputs are committed JSON |

**Initial check: PASS** (no violations; Complexity Tracking empty).
**Post-design re-check (after Phase 1 artifacts below): PASS** — design adds no new
projects, no new frameworks, one observe-only component in an existing node.

## Project Structure

### Documentation (this feature)

```text
specs/001-rag-truth-audit/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions: normalization, derived-arithmetic policy,
│                        #   cache-bypass assertion, corpus-version stamp, truth-table method
├── data-model.md        # Phase 1 — audit artifact schemas (RunRecord, ProbeQuestion, …)
├── quickstart.md        # Phase 1 — how to run baseline / re-run pinned set / read outputs
├── contracts/
│   ├── harness-cli.md            # rag_trace_capture --repeat contract (args + output JSON)
│   └── numeric-fidelity-event.md # observe-mode structlog event + trace-field schema
├── checklists/requirements.md    # spec quality gate (PASS)
├── evidence/            # committed run outputs (baseline + per-step re-runs)
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
scripts/
└── rag_trace_capture.py            # EXTEND: --repeat N, cache-bypass assert,
                                    #   corpus-version stamp, fabricated-value extraction

src/ragbot/
├── shared/
│   ├── constants/                  # ADD: numeric-fidelity + marker constants (SSoT)
│   └── document_stats.py           # (Phase E candidate — only if option (a)/(c) chosen)
├── orchestration/
│   ├── query_graph.py              # (Phase E candidate — stats formatter, option (a))
│   └── nodes/guard_output.py       # ADD: numeric-fidelity observe check (Phase D)
└── infrastructure/repositories/
    └── stats_index_repository.py   # (Phase E candidate — retrieval filter, option (b))

tests/
├── unit/                           # red tests per remediation + gate unit tests
└── scenarios/
    ├── chinh-sach-xe_deepdive60.json   # pinned set (stable)
    └── chinh-sach-xe_probe9.json       # NEW: 9-question probe set (Phase B)
```

**Structure Decision**: single existing project; audit artifacts under the feature dir;
code touches limited to the four candidate paths above, each behind its own ladder step.

## Phase Map (execution order, each ends at an OWNER GATE)

| Phase | Deliverable | Spec story | Blocks |
|---|---|---|---|
| A — Truth Table | 12-stage L1/L2/L3 table + evidence links | US1 | — |
| B — Statistical Baseline | probe9 set + `--repeat` harness + baseline report + stray-number verdict | US2 | all remediation |
| C — Shell-Entity Decision | 3-option costed decision record, owner signs | US3 | remediation scoping |
| D — Numeric-Fidelity Gate | observe-mode component + false-positive/catch report | US4 | any blocking mode |
| E — Remediation Ladder | red-test + single change + pinned-set deltas per step | US5 | — (needs B, C) |
| F — Coverage Recovery | 9 case root-causes + fixes/deferrals | US6 | — (needs E discipline) |

## Complexity Tracking

> No constitution violations — table intentionally empty.
