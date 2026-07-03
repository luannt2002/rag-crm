# Phase 0 Research — RAG Truth Audit

All Technical Context items were known; this file locks the 6 design decisions the plan
needs. Every decision cites existing code (verified 2026-07-03).

## D1. Number extraction & normalization (numeric-fidelity gate)

- **Decision**: Reuse `src/ragbot/shared/number_format.py` — `_SIGNIFICANT_NUMBER_RE` +
  `parse_money_vn()` + the `min_digits` guard (default `DEFAULT_NUMERIC_COVERAGE_MIN_DIGITS`)
  — for answer-side token extraction. Matching is two-level: (1) literal-substring against
  served context (same as ingest-side), (2) parsed-value equality against
  `document_service_index.price_primary/price_secondary` + numeric `attributes_json` values
  for entities in the served context (catches `1.242.000đ` vs `1242000` formatting drift).
- **Rationale**: `find_dropped_numbers()` (`number_format.py:208`) already implements the
  ingest-side mirror of this check (source numbers missing from chunks), deterministic,
  observe-only, currency/language-neutral. The answer-side gate is its inverse — numbers in
  answer missing from context/DB. Reusing the module keeps one SSoT for numeric parsing
  (document_stats.py already delegates `parse_money_vn` there).
- **Alternatives considered**: new regex in guard node (rejected: duplicate parsing logic =
  drift risk, violates zero-hardcode SSoT); LLM-judged grounding (rejected: P-IV requires
  model-independence — that layer already exists as the grounding judge and proved
  insufficient alone).

## D2. Derived-arithmetic policy (gate)

- **Decision**: Allow-list exactly two derivations, validated by recomputation over grounded
  numbers: (a) pairwise difference `|a−b|`, (b) pairwise sum `a+b`, over any two grounded
  values in the same answer. A number is then: `grounded` (level 1/2 match) →
  `derived_valid` (allow-list recompute hits) → `unsupported` (flag). Multiplication /
  percentages are NOT allow-listed in v1 (no observed legitimate case in the 60Q corpus;
  the one real derived case is "cao hơn 432.000đ" = 1.602.000 − 1.170.000).
- **Rationale**: Edge case in spec requires the policy BEFORE observe metrics are read.
  Smallest allow-list that covers the observed legitimate pattern; anything wider inflates
  false-negatives (a fabricated number that coincidentally equals some product would pass).
- **Alternatives**: flag all derived (rejected: known false-positive on valid comparison
  answers → noise destroys observe-mode signal); full expression search (rejected:
  combinatorial, unneeded).

## D3. Cache-bypass assertion (repeated-run harness)

- **Decision**: Harness sends `bypass_cache=true` AND asserts per run that the response
  debug cache status equals `"bypassed"` (`chat_routes.py:586-591` exposes
  `bypassed|hit|miss`). Any run whose status ≠ `bypassed` aborts the whole batch with a
  named error (constitution edge case: contaminated runs are not independent samples).
- **Rationale**: field already exists; assertion is one line; silent cache hits were the
  known killer of repeated-run validity.
- **Alternatives**: flush semantic_cache before each batch (rejected: mutates shared state,
  slower, and still doesn't PROVE per-run independence).

## D4. Corpus-version stamp (run comparability)

- **Decision**: Each RunRecord stores `corpus_version` computed DB-side at batch start AND
  batch end: `(count(chunks), max(documents.updated_at), md5(string_agg(content_hash)))`
  for the bot. Start≠end → batch invalid. Cross-version comparisons are rejected by the
  report generator.
- **Rationale**: spec edge case (mid-audit re-ingest invalidates comparisons); the platform
  has a per-bot `corpus_version` Redis memo (`document_service/__init__.py:276-288`) but the
  audit needs a DB-derivable, restart-proof value — content-hash aggregate is reproducible
  from the DB alone.
- **Alternatives**: trust the Redis memo (rejected: cache is invalidation-driven, not a
  stable identifier); git-style manifest file (rejected: duplicates what the DB already
  knows).

## D5. Truth-table evidence method (12 stages)

- **Decision**: Grade each stage from three existing evidence sources, in priority order:
  (1) runtime traces from the pinned 60Q run (`reports/rag_trace_60.json` — retrieve/rerank/
  grade/generate/guard live data), (2) `request_steps` per-step instrumentation rows for a
  sampled request, (3) unit/pin tests + `file:line` for stages with no runtime signal (those
  cap at L1 or "disabled"). One targeted probe run fills stages the 60Q set never exercised
  (e.g. neighbor-expand, fallback stages) — probe list enumerated in tasks.md.
- **Rationale**: P-X requires artifacts; reusing committed traces means the table is
  reproducible without new infrastructure. `request_steps` exists
  (memory: 12 step_names instrumented).
- **Alternatives**: build a per-stage synthetic-benchmark harness (rejected: audit-scope
  creep; the goal is grading existing behavior, not new benchmarks).

## D6. Shell-entity options costing (Phase C decision record inputs)

- **Decision**: present exactly 3 options with these verified cost profiles:
  - **(a) Marker in formatter** — touch `query_graph.py` stats formatter + 1 constant
    (`shared/constants.py`); no re-ingest; covers stats-synthetic path ONLY (raw-chunk path
    q20 needs Phase D gate as backstop). Effort S. Risk: LLM-obedience layer (P-IV: not
    sufficient alone).
  - **(b) Retrieval filter** — 1 WHERE clause (`price_primary IS NOT NULL OR
    price_secondary IS NOT NULL`) in customer-facing stats queries
    (`stats_index_repository.py`), config-gated per-bot opt-out; no re-ingest; removes the
    attack surface entirely for the stats path; shell entities stay admin-visible. Effort S.
    Risk: existence questions ("có bán X không?") lose the stats hit → must measure coverage
    delta on the pinned set.
  - **(c) `pending_price` status** — alembic column + ingest change + retrieval filter +
    admin surfacing. Effort L. Benefit: first-class lifecycle (owner can list what needs
    prices).
- **Rationale**: options must be costed for the owner gate (FR-006); all three verified
  against the actual code paths this session.
- **Alternatives folded into (c)**: separate "pending" table (rejected: join cost, same
  semantics).

## Open items deliberately deferred to tasks

- Probe-set control-question final list (needs 3 chuẩn + 2 pure-gap controls — candidates
  named in plan; freeze happens when `chinh-sach-xe_probe9.json` is written).
- Numeric-fidelity structlog event name (constant to be added; contract fixes the schema,
  name lives in `shared/constants.py`).
