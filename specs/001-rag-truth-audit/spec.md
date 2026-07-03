# Feature Specification: RAG Truth Audit

**Feature Branch**: `001-rag-truth-audit`

**Created**: 2026-07-03

**Status**: Draft

**Input**: User description: "RAG Truth Audit — a systematic, evidence-only re-verification
program for the existing Ragbot platform (brownfield). The platform grew via vibe-coding:
many capabilities EXIST and 'sound good' in reports, but were never verified to work well.
The audit must separate 'đã có' (exists) from 'đã làm tốt' (verified-good) across the whole
answer pipeline, statistically measure the known hallucination class before fixing it, force
the unasked business decision on price-less shell entities, add a deterministic
numeric-fidelity gate, and ship every fix one-at-a-time with before/after deltas."

**Constitution**: `.specify/memory/constitution.md` v1.0.0 — every requirement below cites
the principles it enforces (P-I … P-X).

## Known Evidence Baseline *(facts already established — SỰ THẬT, artifacts linked)*

| # | Fact | Artifact |
|---|------|----------|
| B1 | 60-question deep-dive on bot `chinh-sach-xe`: 42 chuẩn / 6 HALLU (1 sai-bịa + 5 lệch-conflation) / 9 coverage losses (5 deflect-oan, 3 thiếu-retrieval, 1 refuse-oan) | `reports/DEEPDIVE_60Q_chinh-sach-xe_20260703.md`, `reports/rag_trace_60.json` |
| B2 | All 6 HALLU share one root: 69/242 stats entities (28.5%) have no price and no quantity ("shell entities"); empty source cells are key-dropped at ingest; the formatter silently omits the price field when null | `document_stats.py:664`, `query_graph.py:~2470`, DB queries in report |
| B3 | Fabricated values varied across runs on the same question (26.000.000đ vs 1.250.000đ) — fabrication is non-deterministic | 2 observed runs (GIẢ THUYẾT on mechanism — see FR-004) |
| B4 | Grounding-block (already coded, per-bot opt-in, default observe) blocked the fabricated-price answer but also blocked 1 valid comparison answer in the same 20-question run | `a1_measure.py` run log |
| B5 | The bot's system prompt already contains explicit anti-fabrication rules (incl. "no number present → do not invent") — instruction alone did not prevent the 6 HALLU | bot system_prompt, 8911 chars |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pipeline Truth Table (Priority: P1)

As the platform owner, I receive a single table grading EVERY stage of the answer pipeline
(ingest → chunk → embed → index → retrieve → rerank → grade → stats-format → prompt-build →
generate → guard → answer) at L1 EXISTS / L2 WORKS / L3 VERIFIED-GOOD, each grade linked to a
reproducible evidence artifact — so I finally know what is real versus what merely "sounds
good". (Enforces P-I, P-II, P-X.)

**Why this priority**: This is the core deliverable — the owner's explicit demand ("tôi cần
xem sự thật"). Every later remediation decision depends on knowing which stages are actually
verified.

**Independent Test**: Pick any row of the delivered table; an independent reviewer can open
the linked artifact and reproduce the grade without talking to the auditor.

**Acceptance Scenarios**:

1. **Given** the delivered truth table, **When** a reviewer opens any stage graded L3,
   **Then** the linked artifact contains a measured numeric result meeting the declared target.
2. **Given** a stage graded L1 only, **When** the reviewer reads the row, **Then** the table
   states explicitly what runtime evidence is missing to reach L2/L3.
3. **Given** any claim in the table, **When** it lacks an artifact link, **Then** it is
   labeled GIẢ THUYẾT, not graded.

---

### User Story 2 - Statistical Hallucination Measurement BEFORE Fixing (Priority: P1)

As the platform owner, I receive a measured fabrication-rate baseline (N ≥ 10 repeated runs
per probe question) on shell-entity questions — fabrication rate, which numbers get
fabricated, and whether fabricated values correlate with stray record numbers (e.g. the
delivery-date "26") — BEFORE any fix ships, so fixes are judged against a real baseline, not
an anecdote. (Enforces P-III.)

**Why this priority**: The external review showed the current causal claim rests on 2 runs.
Without a statistical baseline, no post-fix comparison is attributable and the "stray number"
mechanism stays a guess — which changes which fix is sufficient.

**Independent Test**: Re-running the published harness with the same inputs reproduces the
baseline rates within expected sampling noise.

**Acceptance Scenarios**:

1. **Given** the probe set (fabrication-type and conflation-type questions), **When** the
   harness runs N ≥ 10 iterations per question with cache bypassed, **Then** the report shows
   per-question fabrication rate, the distribution of fabricated values, and a stated verdict
   on the stray-number correlation hypothesis (confirmed / refuted / inconclusive).
2. **Given** the baseline exists, **When** any remediation later ships, **Then** the same
   harness re-runs and the delta is reported against this baseline.

---

### User Story 3 - Business Decision on Shell Entities (Priority: P1)

As the platform owner, I am presented a costed decision record for the 69 price-less shell
entities — (a) keep retrievable with an explicit "no value" marker, (b) exclude from
customer-facing retrieval, (c) keep with a pending-price status that retrieval surfaces
differently — and MY choice is recorded before remediation work is scoped. (Enforces P-V.)

**Why this priority**: This decision determines whether the remediation is a one-line filter
or a multi-layer formatting/gating program. Skipping it risks building the expensive path
when the cheap one was wanted.

**Independent Test**: The decision record names the chosen option, the owner, the date, and
the options rejected with their costs; remediation tasks reference it.

**Acceptance Scenarios**:

1. **Given** the decision record, **When** remediation planning starts, **Then** every
   remediation task cites the chosen option and no task implements a rejected option.
2. **Given** the owner has not yet decided, **When** remediation planning is attempted,
   **Then** the process blocks (gate) rather than assuming a default.

---

### User Story 4 - Deterministic Numeric-Fidelity Gate (Priority: P2)

As the platform owner, I get an always-on, model-independent check that every number in a
customer answer exists in the served context or the structured stats data — first in
observe/report mode so its own false-positive rate is measured before any blocking is enabled.
(Enforces P-IV; complements, never replaces, the prompt-level rules.)

**Why this priority**: All current anti-fabrication measures depend on the model obeying
instructions (proven insufficient — B5). This is the first hard, deterministic layer. It is
P2 only because its rollout depends on the P1 baseline to measure against.

**Independent Test**: Feed the gate a recorded answer containing a number absent from its
context → it flags; feed it a fully grounded answer → it stays silent. Both cases run without
any model call.

**Acceptance Scenarios**:

1. **Given** observe mode on the pinned question set, **When** the gate runs, **Then** its
   flag rate on known-good answers (false positives) and known-bad answers (catches) is
   reported.
2. **Given** the measured false-positive rate exceeds the declared threshold, **When**
   enabling blocking is proposed, **Then** the proposal is rejected until the gate is tuned.

---

### User Story 5 - Measured Remediation Ladder (Priority: P2)

As the platform owner, every fix ships alone: a failing regression test reproducing the bug
comes first, exactly one remediation is enabled at a time, the pinned 60-question set re-runs
after each step, and per-step deltas (chuẩn / HALLU / refuse-oan / deflect-oan rates) plus
rollback criteria are recorded. (Enforces P-VI, P-IX.)

**Why this priority**: Without one-at-a-time rollout the program cannot attribute improvement
— repeating the original vibe-coding failure mode the audit exists to end.

**Independent Test**: The remediation log shows, for each step: red test (failing before,
passing after), the single toggle/change enabled, the re-run deltas, and the rollback rule.

**Acceptance Scenarios**:

1. **Given** two proposed fixes, **When** rollout is scheduled, **Then** they occupy separate
   steps with separate re-runs (never combined).
2. **Given** a step's re-run shows HALLU > 0 or chuẩn-rate regression beyond the declared
   tolerance, **Then** the step is rolled back per its pre-declared criteria.

---

### User Story 6 - Coverage Recovery (Priority: P3)

As the platform owner, the 9 known coverage losses (5 persona-deflects on answerable
questions, 3 retrieval size-misses, 1 over-refuse) are root-caused under the same evidence
discipline and fixed via the same measured ladder — because a bot that refuses or deflects
answerable questions is blind, not safe. (Enforces P-VIII.)

**Why this priority**: Important for answer quality, but scheduled after the hallucination
class is measured and gated — safety-critical work first.

**Independent Test**: Each of the 9 cases has a root-cause note with evidence and either a
shipped fix with deltas or an explicit deferral with reason.

**Acceptance Scenarios**:

1. **Given** the 5 deflect-oan cases, **When** root-caused, **Then** the analysis states the
   failing stage with evidence (not "the model felt conservative").
2. **Given** any coverage fix ships, **Then** HALLU stays 0 on the re-run (coverage recovery
   must not reopen fabrication).

### Edge Cases

- Probe question hits an entity that has BOTH a stray number and a same-size sibling with a
  real value → harness must classify the failure as fabrication vs conflation, not lump them.
- Repeated-run harness with cache accidentally enabled → runs are not independent; harness
  MUST verify cache bypass per run and abort otherwise.
- Numeric-fidelity gate meets legitimately derived numbers (sums, differences computed by the
  model, e.g. "cao hơn 432.000đ") → gate policy for derived arithmetic must be declared
  (allow-listed derivations vs flagged) before observe-mode metrics are read.
- Numbers formatted differently between answer and source (1.242.000đ vs 1242000 vs 1,242,000)
  → normalization rules must be part of the gate design and tested.
- A stage cannot reach L2 because it is config-disabled platform-wide → truth table must
  record "disabled" as its own state, distinct from "exists but unverified".
- Shell-entity decision (User Story 3) changes the corpus shape mid-audit → baseline and
  post-fix runs must record the corpus version they ran against; mixed-corpus comparisons are
  invalid.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The audit MUST produce a pipeline truth table covering all 12 stages (ingest,
  chunk, embed, index, retrieve, rerank, grade, stats-format, prompt-build, generate, guard,
  answer), each graded L1/L2/L3 with at least one evidence artifact link per grade. (P-I, P-X)
- **FR-002**: Every statement in audit outputs MUST be labeled SỰ THẬT (with artifact) or
  GIẢ THUYẾT (with the named verification step that would resolve it). (P-II)
- **FR-003**: The truth table MUST record config-disabled capabilities as "disabled",
  distinct from L1. (P-I)
- **FR-004**: The audit MUST run a repeated-run harness — N ≥ 10 iterations per probe
  question, cache bypass verified per run — over a probe set containing at minimum the known
  fabrication-type question (price-less entity with stray number) and two conflation-type
  questions (price-less entity with priced same-size sibling), reporting per-question
  fabrication rate and fabricated-value distribution, and delivering a verdict on the
  stray-number hypothesis. (P-III)
- **FR-005**: The statistical baseline (FR-004) MUST complete before any remediation ships;
  post-fix comparisons MUST use the same harness, question set, and corpus version. (P-III,
  P-VI)
- **FR-006**: The audit MUST deliver a business decision record for shell entities with ≥ 3
  costed options; remediation planning MUST block until the owner records a choice. (P-V)
- **FR-007**: The program MUST deliver a numeric-fidelity gate: model-independent,
  comparing every number token in an answer against the served context and structured stats
  values, with declared normalization rules and a declared policy for model-derived
  arithmetic; it MUST launch in observe mode and report false-positive/catch rates on the
  pinned set before any blocking mode is considered. (P-IV)
- **FR-008**: Every remediation MUST follow red-test-first: a regression test reproducing the
  bug, failing before the change and passing after. (P-VI)
- **FR-009**: Remediations MUST be enabled one at a time; each step MUST re-run the pinned
  60-question set and record deltas for chuẩn, sai-bịa, lệch, thiếu, refuse-oan, deflect-oan;
  each step MUST declare rollback criteria before enabling. (P-VI)
- **FR-010**: The grounding-block capability MUST NOT be enabled in the same step as any
  other remediation, and MUST NOT move past observe/opt-in until its over-refuse impact on
  comparison-type questions is measured on the pinned set. (P-VI, B4)
- **FR-011**: All remediation code MUST key on schema signals, contain no per-bot/per-brand
  logic, no hardcoded response text, and no new inline constants. (P-VII)
- **FR-012**: Every audit run report MUST include both HALLU metrics and coverage metrics
  (chuẩn rate, refuse-oan count, deflect-oan count); a change that improves HALLU by
  destroying coverage MUST be flagged, not celebrated. (P-VIII)
- **FR-013**: Every shared-code change MUST ship with a blast-radius statement naming
  consuming flows and pinning tests. (P-IX)
- **FR-014**: All evidence artifacts MUST live at stable repo paths (`reports/`,
  `tests/scenarios/`, `specs/001-rag-truth-audit/evidence/`) and be referenced by relative
  path from audit documents. (P-X)
- **FR-015**: Each audit phase (truth table → baseline → decision → gate design → remediation
  ladder → coverage recovery) MUST end at an owner gate; work MUST NOT drift past a gate
  without recorded approval. (Constitution: Audit Workflow)

### Key Entities

- **Pipeline Stage**: one of the 12 named stages; carries a truth grade (L1/L2/L3/disabled),
  evidence links, and missing-evidence notes.
- **Probe Question**: a question with known ground-truth state (answerable / trap /
  shell-entity) used for repeated-run measurement; carries its entity reference and expected
  correct behavior.
- **Shell Entity**: a catalog record with a name but no value data (no price, no quantity);
  subject of the business decision; carries any stray numeric attributes.
- **Run Record**: one harness execution — corpus version, cache-bypass proof, per-question
  answers, extracted numbers, DB-anchored verdicts.
- **Remediation Step**: one enabled change — red test, toggle/change identity, re-run deltas,
  rollback criteria, blast-radius statement.
- **Decision Record**: a business choice — options with costs, chosen option, owner, date.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 12/12 pipeline stages carry a truth grade with at least one working evidence
  link each; 0 graded claims without artifacts.
- **SC-002**: Fabrication baseline exists with N ≥ 10 runs per probe question; the
  stray-number hypothesis has a recorded verdict (confirmed / refuted / inconclusive).
- **SC-003**: After the remediation ladder completes, fabrication rate on the probe set is 0
  across N ≥ 10 runs per question (HALLU=0, statistically — not anecdotally).
- **SC-004**: Chuẩn rate on the pinned 60-question set does not regress below the 42/60
  baseline at any ladder step; any step that regresses is rolled back per its criteria.
- **SC-005**: Every shipped remediation has attributable before/after deltas (one change per
  step, same harness, same corpus version) — 0 steps with combined changes.
- **SC-006**: The shell-entity decision record exists with owner sign-off BEFORE the first
  shell-entity remediation ships.
- **SC-007**: The numeric-fidelity gate reports measured false-positive and catch rates on
  the pinned set in observe mode; blocking is enabled nowhere until those numbers are
  reviewed at an owner gate.
- **SC-008**: The 9 coverage-loss cases each have a root-cause note with evidence; at least
  the 5 deflect-oan cases have either a shipped fix with deltas or an explicit owner-approved
  deferral.

## Assumptions

- The pinned question set is the existing 60-question deep-dive set
  (`tests/scenarios/chinh-sach-xe_deepdive60.json`); it may be extended, but the 60 base
  questions stay stable for comparability.
- Bot `chinh-sach-xe` remains the reference corpus for the audit; findings generalize because
  all remediations are platform-neutral (P-VII), but verification on a second bot is a
  post-program follow-up, not in scope.
- N = 10–20 repeated runs per probe question is accepted as the statistical bar (per external
  review); full significance testing is not required at this scale — rates + distributions
  suffice.
- The owner is the sole decision authority at gates (single-owner project).
- Corpus re-ingest during the program creates a new corpus version; harness runs record the
  version they ran against.
- Existing project rules (root CLAUDE.md) stay in force; this spec adds audit-specific
  discipline on top, and CLAUDE.md wins on conflict.
- NOT IN SCOPE: new features, streaming, webhooks, action/booking flows, model upgrades,
  multi-bot rollout of remediations.
