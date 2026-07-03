<!--
Sync Impact Report
- Version change: (template, unversioned) → 1.0.0
- Modified principles: n/a (initial ratification — template placeholders filled)
- Added sections: Core Principles (10), Truth-Grading Scale, Audit Workflow & Quality Gates, Governance
- Removed sections: none (template slots consumed)
- Templates requiring updates:
  ✅ .specify/memory/constitution.md (this file)
  ⚠ pending: .specify/templates/plan-template.md (Constitution Check gates reference generic
    principles; audit specs must cite principles by number from this file)
  ⚠ pending: .specify/templates/spec-template.md (audit specs must carry a Truth-Grading table)
- Follow-up TODOs: none — all placeholders resolved.
-->

# Ragbot Truth-Audit Constitution

Governs the truth-audit program under `specs/`: a systematic, evidence-only re-verification of
a brownfield multi-tenant RAG platform (Python/FastAPI/LangGraph/pgvector) that grew via
vibe-coding — where many capabilities EXIST but were never verified to WORK WELL.

## Core Principles

### I. EXISTS ≠ WORKS ≠ VERIFIED-GOOD (NON-NEGOTIABLE)

Every capability claim MUST be labeled exactly one of three levels:

- **L1 EXISTS** — code is present (`file:line` citable).
- **L2 WORKS** — executes without error on at least one real request (trace/log citable).
- **L3 VERIFIED-GOOD** — measured on real load-test/DB evidence meeting a declared numeric
  target (report artifact citable).

Reporting L1 as L3 is the gravest violation of this constitution. Audit outputs MUST show the
level per capability; "đã có" (it exists) is never evidence of "đã làm tốt" (it works well).

### II. No-Guess Rule #0

No conclusion, cause, or impact statement without verifiable evidence: log/trace line, psql
query result, test output, `file:line`, or DB row. Every claim MUST be labeled **SỰ THẬT**
(fact, evidence attached) or **GIẢ THUYẾT** (hypothesis, verification step named). "Sẽ fix
được", "chắc là do X", "có vẻ ổn" without measurement are forbidden phrasings.

### III. Statistical Evidence Over Anecdote

A behavior/hallucination claim from 1–2 observed runs is a GIẢ THUYẾT, never a SỰ THẬT.
Causal claims (e.g. "the model grabs the stray `date1:26`") REQUIRE repeated runs (N ≥ 10)
with measured rates and, for fixes, a before/after comparison on the same question set.
Non-deterministic failures MUST be reported as rates, not single examples.

### IV. Deterministic Gates Over LLM Obedience

Any anti-hallucination measure that only instructs the LLM (sysprompt rule, sentinel marker,
prompt formatting) is probability reduction — NOT a fix. Each such measure MUST be paired with
an LLM-independent deterministic check (e.g. numeric-fidelity: every number in the answer must
exist in the served context or the stats DB; entity-price join checks). The deterministic layer
is the gate; the prompt layer is the optimization.

### V. Business-Question-First

Before patching code, the audit MUST ask whether the data/feature should exist at all
(e.g. should price-less shell entities be customer-retrievable, or filtered/flagged
`pending_price` at the index?). A one-line filter at the business layer beats a three-tier
code patch. Every remediation proposal MUST record the business decision it depends on and
who owns that decision.

### VI. Red Test First, Measured Rollout

Every bug fix REQUIRES a failing regression test reproducing the bug BEFORE the patch.
Fixes MUST roll out one at a time — never enable two remediations simultaneously, otherwise
improvement cannot be attributed. Each rollout step MUST re-run the affected question set and
record deltas (pass rate, HALLU rate, over-refuse rate).

### VII. Platform-Neutral, Zero-Hardcode

No per-bot/per-customer logic in core paths; fixes key on schema signals
(e.g. `price_primary IS NULL`), never brand/corpus literals. All constants come from
`shared/constants.py` or DB config (`system_config`, per-bot columns); response text comes
from `language_packs`/bot config, never string literals in `src/`. No version-refs in names.

### VIII. HALLU=0 Sacred, Coverage Tracked

A fabricated number is a release blocker, full stop. Equally: Coverage — the share of
questions where the corpus has the answer AND the bot answered correctly — MUST be tracked
beside HALLU. A bot that refuses everything is not honest; it is blind. Both metrics appear
in every audit verdict; over-refuse (refuse-oan) and persona-deflect are counted as coverage
losses, not safety wins.

### IX. Blast-Radius Declared

Every change to shared code (formatters, retrieval stages, chunking, graph nodes) MUST state:
which flows and which bots consume the changed path, what behavior may shift, and which
existing tests pin the old behavior. Rollout without a blast-radius statement is forbidden.

### X. Evidence Artifacts Required

Every audit verdict links to a reproducible artifact: script path, report file, DB query,
trace JSON, or test name. A verdict without an artifact is a GIẢ THUYẾT and MUST be labeled
as such. Artifacts live in `reports/`, `tests/scenarios/`, or `specs/<feature>/evidence/`.

## Truth-Grading Scale

All audited capabilities and all answers in question-set runs are graded on fixed scales:

- **Capability scale**: L1 EXISTS / L2 WORKS / L3 VERIFIED-GOOD (Principle I).
- **Answer scale**: `chuẩn` (correct+grounded) / `chưa chuẩn` (grounded but extrapolates) /
  `thiếu` (incomplete, expected fact missing) / `sai-bịa` (fabricated) / `lệch`
  (conflation — real value, wrong entity) / `refuse-đúng` / `refuse-oan` / `deflect-oan`.
- **Numbers are DB-anchored**: every price/quantity in an answer is checked against the
  structured stats index, never against truncated chunk text alone.

## Audit Workflow & Quality Gates

- Audit phases follow spec-kit flow: constitution → specify → clarify → plan → tasks →
  implement → converge. Phase gates require owner approval; auditors do not drift past gates.
- READ-only phases produce reports; only approved remediation tasks may modify `src/`.
- Every remediation PR MUST pass: red-test-first (VI), blast-radius (IX), platform-neutral
  grep gates (VII), and re-run of the pinned question set with deltas reported (III, VIII).
- The root `CLAUDE.md` Quality Gate (11 items) applies to all code changes in addition to
  this constitution.

## Governance

- This constitution governs all truth-audit specs under `specs/`. Amendments require explicit
  owner (project owner) approval, a version bump per semantic versioning (MAJOR: principle
  removal/redefinition; MINOR: new principle/section; PATCH: clarification), and a Sync Impact
  Report prepended to this file.
- The project root `CLAUDE.md` remains the superordinate document and wins on any conflict.
- Compliance review: every audit report and every remediation PR MUST cite the principles it
  satisfies by number; reviewers reject documents that assert compliance "in general" without
  per-principle citation.

**Version**: 1.0.0 | **Ratified**: 2026-07-03 | **Last Amended**: 2026-07-03
