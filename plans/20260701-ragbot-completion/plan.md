# [T1-Smartness] Ragbot Completion — Robust Multi (bot/doc/tenant/format)

> Execution plan for the 50-problem completion roadmap
> ([reports/RAGBOT_COMPLETION_ROADMAP_20260701.md](../../reports/RAGBOT_COMPLETION_ROADMAP_20260701.md)).
> **Stance: EVOLVE-not-rewrite** (strangler-fig). Frame is already expert (Hexagonal · Port+Registry+DI ·
> `rows_to_structured_markdown` state-machine hardened). Problem = **unconnected wires**, not wrong frame.
> Discipline: shape-based / domain-neutral · no app-override (#10) · RLS 4-key · fail-loud · no-guess
> (every phase gated on **15-case messy golden test + live A/B** before "shipped").

## Inventory (50 problems) — by layer
L1-structure-recovery 9 · L2-chunking 5 · analytical 10 · multi-doc 3 · data-quality 3 · robustness 8 ·
semantic-layer 3 · infra 9.  Status: OPEN 40 · PROVEN-NOT-SHIPPED 3 · DIAGNOSED 4 · PARTIAL 3.

## Cross-cutting verify-gate (BẮT BUỘC mỗi phase — rule #0)
1. **TDD** — failing test FIRST, RED confirmed, then GREEN.
2. **15-case messy golden test** (real breaking formats) = acceptance oracle; regression-locked so
   "sửa format là lỗi" never recurs silently.
3. **A/B 3 demo bot** (xe tabular · spa · legal) `bypass_cache=true`, parallel gather N=8-10 — measure
   Coverage + Faithfulness + HALLU BEFORE/AFTER. Coverage lift real, HALLU=0 held.
4. **Grep-guard** — 0 brand/vocab literal in logic, 0 version-ref, 0 magic-number, no app-override.

---

## Phase 0 — UNBLOCK  `[T1-enabler / infra]`  ⭐ highest leverage (~1 ngày)
> Nothing downstream is measurable until these land. Fix the ROOT (duplicated inline purge), not the symptom.

- **P0.1 — Fix `DQ-REINGEST-PURGE-BUG` at ROOT (centralized purge helper).**
  Precise finding: the re-ingest replace path (`__init__.py:905-949`) hard-DELETEs `document_chunks` (:916)
  but **NOT `document_service_index`** → stale `col_N` stats-rows survive re-ingest (the col5..col10
  residual). Purge is **duplicated inline** across 3 paths (:916 re-ingest, :971 bot-purge, :1033/:1040
  single-delete) → drift → forgotten table.
  **Fix (single-source-of-truth):** extract `_purge_document_content(session, doc_ids)` enumerating ALL
  content-state tables (`document_chunks` + `document_service_index` + embeddings-if-separate); call it from
  ALL 3 paths. Adding a future content table = edit ONE place. (DRY — [[feedback_code_quality]].)
  **3-tier data lifecycle (standard):** Content (chunks/service_index/embedding) = **HARD purge** (derived,
  rebuildable). Metadata (`documents` row) = **SOFT delete** (`deleted_at`, forensic/rollback). Audit/cost
  (`audit_log`/`request_logs`/`request_steps`/token/cost) = **IMMUTABLE append-only, NEVER deleted** by
  content ops.
- **P0.2 — Commit `Phase 1a` count-path** (uncommitted, at-risk-of-loss): `query_graph.py` count branch +
  `stats_index_repository.count_by_name_keyword` + parser `operation="count"` + `test_count_operation_dispatch.py`.
- **Files**: `document_service/__init__.py` (extract helper + wire 3 paths); (commit) `query_graph.py` ·
  `stats_index_repository.py` · `query_range_parser.py` · `tests/unit/test_count_operation_dispatch.py`.
- **Gate** (TDD): clean 3-bot re-ingest → **0 duplicate `document_chunks` AND 0 stale `document_service_index`
  row** per doc_id; **`audit_log`/cost rows PRESERVED** after re-ingest (assert count unchanged); count repro
  "có bao nhiêu loại Landspider" no longer fabricates `1.020.000` (A/B live).

## Phase 1 — L1 BRITTLENESS  `[T1-Smartness]`  (fix 6/15 breaking formats)
> The current pain. 2 fixes already PROVEN this session (throwaway) — land them in the shared converter.

- **1.1** Build the **15-case messy golden test** (`tests/unit/test_tabular_robustness_golden.py`) — real
  breaking docs (blank-row, merged-cell, headerless, empty-cols, 2-level-group, alias-flood...). RED first.
- **1.2** Ship converter (`shared/tabular_markdown.py`), all FORM-only / domain-neutral:
  - **skip-blank + gap-K trim** (used-range trim; run<K = spacer skip; run≥K = table boundary; K = constant
    `DEFAULT_TABLE_GAP_ROWS`). *(PROVEN: fixes case 02, 03.)*
  - **forward-fill** sparse/empty category cells (rowspan recovery) + pure-shape gate so col0 stub isn't
    stolen as entity name. *(PROVEN: fixes case 04.)*
  - **trim leading+trailing empty header cols** (fix col_N in header, cases 07/08).
  - **fail-loud DTO** — unassigned/col_N columns surface in the ingest result for the owner (not a silent log).
- **1.3** Wire the parsers that BYPASS the converter through it (source: TLDW one-block contract):
  - `docx_parser.py:110-119` — route `table.rows` matrix through `rows_to_structured_markdown` (3-line wire).
  - `kreuzberg_markdown_parser.py` — reconstruct typed-block list by FORM (heading regex / pipe fences /
    blank-runs), route table blocks through converter (local adapter rewrite, sanctioned).
- **Files**: `tabular_markdown.py` · `docx_parser.py` · `kreuzberg_markdown_parser.py` ·
  `shared/constants/*` (K const) · new golden test.
- **Gate**: **≥12/15 messy PASS** (from 3/15); `col_N` residual = 0 on live xe re-ingest; Coverage lift
  measured; HALLU=0 held.

## Phase 2 — L2 ADAPCHUNK COMPLETION  `[T1-Smartness]`
> AdapChunk covers L2 but assumed clean OCR markdown; wire it for the spreadsheet path.

- Atomic row block (never split a row); **labeled linearize** (`header: value` per cell — kills the
  `col_4:214 | col_6:26` guessing); **breadcrumb** (`# Doc > ## Section`) on table-row chunks; **dual-read
  `original_content`** metadata (embedded NL for semantics + original for exact numbers, §7.3 AdapChunk);
  `check_chunk_gaps` hard assert (lossless). Relax fast-path gate `headings==0` → block-type predicate.
- **Files**: `document_stats.py` (synthetic-chunk render) · `ingest_stages_enrich.py` (breadcrumb) ·
  `analyze.py` (gate) · chunker strategies.
- **Gate**: row-mixing binding bug gone; block-integrity metric ≥ threshold; A/B Coverage lift on
  table-question set.
- **Sources**: AdapChunk §2/§7 · TLDW `table_serialization.serialize_to_entities` · Anthropic Contextual Retrieval.

## Phase 3 — ANALYTICAL ENGINE  `[T1-Smartness]`
> RAG top-K can't aggregate; add a structured-aggregate substrate. SQL computes, LLM narrates (no #10 breach).

- Re-land **F7 numeric index** (attribute-generic) additively; **SUM/AVG/GROUP-BY/COUNT(DISTINCT)**;
  **COUNT(\*) exact** + **capped-honesty** ("N of M (capped)" = retrieval metadata, NOT answer override);
  **local-vs-global router**; roles beyond NAME via owner **glossary** (ADR-0006, opt-in).
- **B-SERIES** "5 loại" = GROUP-BY recurring-token/series-key (series-key from `column_roles`, gate per-bot).
- **B-TRUNC** enumerate cap → repo cap + truncation marker (257 not 100).
- **Files**: `stats_index_repository.py` (SUM/AVG/GROUP BY/COUNT DISTINCT) · `query_range_parser.py`
  (sum/avg signal) · `query_graph.py` (dispatch + capped-honesty render) · `document_stats.py` (series-key).
- **Gate**: B-SERIES "5", B-TRUNC "257", SUM/AVG cases PASS live; HALLU=0 on numeric traps.
- **Sources**: TableRAG (NeurIPS 2410.04739, EMNLP 2506.10380) · TAG 2408.14717.

## Phase 4 — CROSS-DOC LINKAGE  `[T1-Smartness]`
> Answers spanning docs (price@sheet1 + date@sheet3). No cross-doc join exists today.

- **Normalized shape-key** `(record_bot_id, workspace_id, lower(spec|code))` + **alembic unique index** +
  **query-time reconcile**: `GROUP BY shape_key` across the bot's docs, merge fragments preferring non-NULL
  attribute values. Requires the source to expose a **consistent key** (data pre-req; else glossary-declared).
- **Files**: `ingest_stages_final.py` (`_dedup_stats_entities` → cross-doc key) · `stats_index_repository.py`
  (reconcile query) · alembic migration (unique index).
- **Gate**: B-FRAG (Davanti 98 not 26) PASS; **no cross-tenant leak** (RLS test).
- **Sources**: RAG-Anything entity-merge · HippoRAG 2405.14831 entity resolution · TLDW group_by_source.

## Phase 5 — ROBUSTNESS + SECURITY hardening  `[T2-CostPerf + security]`
- CB-4xx exclusion (client bug fail-loud, don't trip shared breaker); list-500 shrink-retry; legal-clause
  ACR prefix; SSRF guard; tsquery locale; EN measure-unit seed; obs qwen3 tokens.
- **Audit-trail preservation** — `audit_log FK ON DELETE CASCADE` (`bot_admin_routes.py:201`) DELETEs audit
  when a bot is hard-deleted → violates append-only audit standard. Alembic: change FK to `ON DELETE SET NULL`
  (or soft-delete the bot) so forensic/billing audit survives bot deletion.
- **Ops (parallel)**: switch prod DSN to `ragbot_app` role → **enforce RLS** (`INFRA-RLS-SUPERUSER`: live
  `.env` connects as postgres superuser + `RAGBOT_ALLOW_SUPERUSER_RUNTIME` → RLS inert in prod).
- **Gate**: legal-clause factoid recovered; 500 degrades gracefully; RLS enforced (superuser fallback gone);
  audit_log survives bot-delete (assert row count).

## Phase 6 — DEFER (ADR-gated)  `[T3-Refactor]`
- Transposed/pivot orientation-detect; function-calling ad-hoc tail; god-node split; full entity-graph
  (GraphRAG 2404.16130 / HippoRAG) for corpus multi-hop. Each = hard-to-reverse → **ADR required**.

---

## Sacred compliance (mọi phase)
- **Shape-based / domain-neutral** — every L1 heuristic (skip-blank, forward-fill, trim, header-detect,
  date-role) is FORM-only, 0 vocab, 0 brand/industry literal. Roles beyond NAME = owner-glossary opt-in.
- **#10 no app-override** — SQL aggregate + cross-doc reconcile produce **source facts**; LLM narrates.
  Capped-honesty / numeric-fidelity = observability metadata, NEVER answer-replacement.
- **RLS 4-key** — shape-key + reconcile scoped `(record_bot_id, workspace_id)`; Phase 5 enforces at DB.
- **Fail-loud-not-silent** — col_N / unassigned columns surface in ingest DTO; client 4xx fail-loud;
  transport errors degrade silent (graceful degradation).
- **No-guess / measure-before-claim (#0)** — every phase gated on 15-case golden + live A/B; no % lift claim
  without measurement.
- **No version-ref · zero-hardcode · Port+DI preserved.**

## Ưu tiên
**Phase 0 → 1 → 2 → 3 → 4 → 5**, defer 6. Phase 0 unblocks measurement; Phase 1 lands the 2 PROVEN fixes +
golden oracle (cuts "sửa format là lỗi" ngay). Làm tuần tự, A/B mỗi phase trước khi sang phase sau.
