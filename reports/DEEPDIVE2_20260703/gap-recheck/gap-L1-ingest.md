# GAP RE-CHECK — LUỒNG 1 (INGEST) — post 9 audit-fix commits

Slug: `gap-L1-ingest` · Branch `fix-260623-ingest-expert` · HEAD `6caeb9c` (clean tree under `src/ragbot/`)
Method: RE-READ current source at HEAD; every claim carries `file:line`. FACT = verified in current source / test run. HYPOTHESIS = mechanism inferred, not runtime-traced.
Scope: LUỒNG 1 ingest chain (upload→parse→chunk→embed→pgvector→stats-index) + cross-cut guards touching ingest.

Commits re-checked: `da37778 · 143ff38 · 1485782 · 8e14205 · 8b792b9 · 3edc50c · a28d88b · ee6ccb2 · 6caeb9c`.

---

## §A. EXECUTIVE VERDICT (current state)

The 9 commits landed a coherent, correct **Phase-0 + safe-Phase-1/2** slice: the CI is un-broken (6678 tests collect, 0 collection errors — FACT), the S1 state-key drop is closed with a passing AST pin (FACT), and 3 of the 4 L1 items the register marked HANDLED are genuinely fixed at source (I2 OCR blocks, I4 PII provider wiring, I5 shape-only header, I17 diff-reingest landmine). One HANDLED item (I4) works functionally but its comment claims "PER-CALL" while it is a `providers.Singleton` (caches after first build) — a comment-drift, not a functional break. The heavy L1 correctness items remain OPEN exactly as the register scoped them for Phase 2/3/4: **I1 worker Path-A/B flatten is untouched** (worker still flattens to `full_text` and never passes `raw_bytes`), I3 (.doc/.xls/.ppt OLE2 parser) absent, I6 coverage-gate still observe-only (no repair), I12 asyncpg bind ceiling un-batched, I13 tool_name collision still overwrites via ON CONFLICT, I16 page_number computed-then-dropped, and I7/I8/I9/I10/I11/I14/I15/I18/I19/I20 all still open. The audit's core concern — silent degradation — is materially unchanged on the ingest path: locally-uploaded XLSX/CSV/DOCX/scan still degrade to flat text or empty-doc without a loud failure, and the coverage gate observes-but-does-not-repair. Net: guards + wiring fixed as intended; ingest *correctness* is not yet closed and matches the register's phase plan.

---

## §B. HANDLED — verified in current source

### I2 — OCR fallback returns blocks (CRITICAL) — CONFIRMED
`src/ragbot/infrastructure/ocr/kreuzberg_parser.py:245-248` uses `extract_bytes_sync` (falls back to `extract_bytes` only for <4.9). `:327-351` adds the content-fallback: when `.elements` is None it builds blocks from `result.content` split on `\n\n` (fail-loud floor — never 0 blocks when text was extracted). FACT (kreuzberg 13 tests pass). Bonus: blocks carry `page_number` (`:303-306, :314-325`) — but that page never persists (see I16).

### I4 — PII redactor provider wired (CRITICAL) — CONFIRMED (functional), comment-drift
`src/ragbot/bootstrap.py:450-455`: `pii = providers.Singleton(build_pii_redactor, provider=providers.Callable(lambda: get_boot_config("pii_redactor_provider", DEFAULT_PII_REDACTOR_PROVIDER)))`. The frozen-`"null"` constant is gone → `system_config.pii_redactor_provider` now takes effect. Consumed at `document_worker.py:315,603`, `chat_worker/pipeline.py:275`, `sync.py:525,720`. FACT.
Caveat (FACT): it is `providers.Singleton` (constructs once, caches) while the sibling `crag_grader_factory` at `:435` is `providers.Factory` (true per-call). The comment at `:441-442` says "resolved PER-CALL" — inaccurate for a Singleton. Functional impact: the DB provider is read once at first `.pii()` call, not per request. Not a breach; note for cleanup.

### I5 — shape-only header rescue (HIGH, owner's #1 concern) — CONFIRMED
`src/ragbot/shared/document_stats.py:390-427` `_is_shape_header` (shape-only, no vocab) + `:348-387` `_is_header_row` now has a `next_is_separator` structural floor. Wired into the extraction path at `:1063` (`or (not header and _is_shape_header(lines, _li, cols))`). Canary went 25-fail → 59-pass per commit. FACT (document_stats/tabular 184 pass; repro 13 pass / 1 xfail).

### I17 — diff-reingest NameError landmine (MED) — CONFIRMED
`src/ragbot/application/services/document_service/ingest_core.py:688-700`: flipping `diff_based_reingest_enabled` now logs `diff_reingest_telemetry_not_implemented` warning instead of calling the never-existent helpers → doc no longer stranded post-commit. FACT.

### Cross-cut guards / P1 confirmations touching ingest
- **S1/Q1 state-key drop** — `orchestration/state.py:211-218` declares `bot_extra_output_tokens_per_response`, `raw_user_message`, `rerank_score_mode`, `_total_graph_iterations`, `embedding_column`. AST pin `tests/unit/test_graphstate_key_pin.py` PASSES. FACT.
- **Q2 stats grounding HALLU-net** — `orchestration/nodes/guard_output.py:106-110`: grounding stays ON for stats by default (`DEFAULT_STATS_ROUTE_SKIP_GROUNDING=False`); revert `3097755` undone. FACT.
- **SEC-4 idempotency** — `domain/value_objects/idempotency_key.py:43-58`: `for_ingest_document` now folds `record_bot_id` + `workspace_id` into the key. FACT.
- **O5 webhook** — `infrastructure/notify/webhook_dispatcher.py:30,332,355` catches `(OSError, RedisError)`. FACT.
- **O4 audit-insert guard** — `infrastructure/observability/invocation_logger.py:249-253` wraps the finally-INSERT/commit in try/except. FACT.
- **Q13/Q17/Q3/Q5/Q18** (query-side) — confirmed landed: rerank passage→chunk index map (`litellm_reranker.py:75-108`), soft-delete `doc_deleted_at IS NULL` in `_doc_filter_sql` (`pgvector_store.py:270`), GraphRAG `record_bot_id=` kwarg (`graph_retriever.py:61`, `ingest_core.py:802`), ai_keys prefix drop (`ai_config_repository.py:664+`).
- **CI un-break (Q15/O1)** — 6678 tests collect, 0 collection errors. FACT.
- **Mirage-knobs / re-exports** — resolved (repro 13 pass / 1 xfail; touched-area suites green).

---

## §C. NOT-HANDLED — confirmed still OPEN in current source

| ID | Sev | Evidence (file:line) | Status |
|---|---|---|---|
| **I1** Path A/B worker flatten | CRIT | `interfaces/workers/document_worker.py:501` flattens `full_text="\n\n".join(b.content ...)`; `:463-467` flattens even the registry `_chunks`; `:613-626` calls `ingest()` with `content=full_text`, `blocks=parsed_blocks`, **no `raw_bytes=`** → `ingest_core.py:318` `if raw_bytes is not None` false → `parser_row_chunks=None`. Local:// path (`:350-368`) reuses flat `raw_content`, skipping the parser entirely. | OPEN (untouched) |
| **I3** .doc/.xls/.ppt no parser | HIGH | `infrastructure/parser/registry.py:45-61` `_REGISTRY` has no OLE2 adapter; `shared/mime_sniff.py` has no `\xd0\xcf\x11\xe0` OLE2 sniff (grep empty). Legacy formats fall to OCR — and for **local:// uploads** the OCR fallback is gated off (`document_worker.py:490` needs `_fetchable`) → empty-doc RuntimeError `:507`. | OPEN |
| **I6** coverage-gate no-repair | HIGH | `ingest_stages.py:864-905`: computes `_cov.uncovered_spans` (`:894,902,904`) then only `logger.warning("chunk_char_coverage_gap")` — comment "OBSERVE-only … NEVER raises". No append-span→tail-chunk repair. | OPEN |
| **I7** re-ingest wipes stats of that doc | MED | `stats_index_repository.py:158` `delete_by_document` per-doc; no dedup/incremental. Re-parsing a doc to a smaller non-empty set still deletes the good entities of that doc. | OPEN |
| **I8** AdapChunk not adaptive | HIGH | `shared/chunking/` untouched by all 9 commits (git log empty). `shared/chunking/analyze.py:407` `select_strategy` still chooses BEFORE chunk (no evaluate-then-select bake-off). | OPEN (Phase 4) |
| **I9** int(_price) truncation | MED | `orchestration/query_graph.py:2432,2454,2460` render price via `int(_price)`. VND-null today (no-op for integer VND); bites decimal currencies. | OPEN |
| **I10** cleaner strips repeated lines | MED | `application/services/document_service/text_processing.py:95-101`: `Counter` of lines, drops any `count >= 3 and len < 100` — content-based, not position-based. Called in ingest at `ingest_stages.py:342`. A repeated short data cell (label/price/"Có") in ≥3 table rows is deleted as boilerplate. | OPEN |
| **I11** language=auto→DEFAULT | MED | `ingest_core.py:185` default `language="auto"`; `:532` `language if language != "auto" else DEFAULT_LANGUAGE`. No script/lang detection. | OPEN |
| **I12** asyncpg 32k bind ceiling | MED | `ingest_helpers.py:200-241` `_bulk_insert_chunks` builds one `INSERT … VALUES (…),(…),…` for all rows in a single `session.execute` (`:241`). ~11-13 binds/row (has_parent_chunk_id) → >~2900 chunks exceeds asyncpg's 32767 ceiling. No per-batch chunking. | OPEN |
| **I13** tool_name collision → chimera | HIGH | `ingest_core.py:525` `tool_name = title.lower().replace(" ","_")[:64]`; unique `uq_doc_tool(tenant,bot,tool_name)` + `ON CONFLICT … DO UPDATE` (`:506-516`) **overwrites** a distinct doc sharing a title. No hash/discriminator in the key. | OPEN |
| **I14** litellm direct call bypasses router | MED | Direct `litellm.acompletion` at `ingest_core.py:738-749`, `query_intent_extractor.py:84-86`, `contextual_chunk_enrichment.py:167`. Not routed through the LLM Port. | OPEN |
| **I15** deterministic_chunk_id PK collision | LOW | `ingest_stages_store.py:625-631` seeds UUID5 with `(record_bot_id, document_id, content)` — no chunk_index. Two identical-content rows in one doc → same PK → collision. Gated by per-bot `chunk_hash_id_enabled` (default OFF → uuid7), so bounded. | OPEN |
| **I16** page_number not persisted | MED | `Chunk.page_number` set (`shared/chunking/__init__.py:921`) but the persisted `chunk_meta` (`ingest_stages_store.py:748-796` parent, `:847-865` child) and row dict (`:802-821`) omit page; `_bulk_insert_chunks` params (`ingest_helpers.py:218-235`) never bind a page field. Blocks' page (kreuzberg) also dropped. | OPEN |
| **I18** money-shape decides structure | MED | `document_stats.py:708,767,809` `_is_pure_money` gates header/role decisions. A money-shaped cell forces data-row classification. (I5 shape-header partially mitigates but the money-gate itself remains.) | OPEN |
| **I19** CSV `;`/tab/UTF-16 | MED | `shared/chunking/csv_chunker.py:114-146` counts only commas (`line.split(",")`, "comma count"). Semicolon/tab-delimited never detected as CSV-shape. | OPEN |
| **I20** stats fabricated tenant (demo) | LOW | `ingest_stages_final.py:562` `record_tenant_id=record_tenant_id or uuid.uuid4()` — fail-open fabricates a random tenant when None instead of fail-loud. Production always passes a real tenant → LOW. | OPEN |
| **O3** Redis-recovery no re-dispatch | HIGH | `infrastructure/events/redis_streams_bus.py:571-618` `recover_pending_messages` XCLAIMs (`:608`) and returns `len(claimed)` (`:615`) — never feeds the claimed payload back to `_dispatch_one`/re-reads the PEL. Poison→DLQ works; a transient-failed message is re-owned but not re-processed. | OPEN |

---

## §D. UNCONTROLLED — silent-degradation / no fail-loud (audit's core concern)

Includes NEW facets found while tracing I1/I2/I3.

1. **Worker re-flattens structured registry chunks** — `document_worker.py:463-467`. Even when a registry parser (XLSX/CSV/DOCX) DID structure the doc into row-chunks, the worker joins them into `full_text` and discards the structure before `ingest()`. Silent: no error, atomicity just lost. (Core facet of I1.)
2. **local:// upload bypasses structured parsing** — `document_worker.py:350-368`. A locally-uploaded XLSX/CSV/DOCX reuses the pre-stored FLAT `raw_content` (a "has-data" probe body) and never re-runs the registry parser (the `if not full_text.strip()` gate at `:386` is already satisfied). Silent flat-text ingest for the exact B2B upload path. (NEW facet of I1.)
3. **local:// legacy-format / scan → empty-doc** — `document_worker.py:490` gates the (now-fixed) OCR fallback behind `_fetchable`; a local:// `.doc`/`.xls`/image with no registry parser (I3) reaches `:503` empty → `RuntimeError("local upload has no stored raw_content …")` at `:507`. Fails loud but with zero coverage for that format on the upload path. (I2's fix only reaches http(s) sources.)
4. **Coverage gate observes, never repairs** — `ingest_stages.py:864-905`. A silently-dropped price/prose span is logged (`chunk_numeric_coverage_gap` / `chunk_char_coverage_gap`) but the answer-bearing text is still missing from every chunk → bot goes blind with Faithfulness 1.0. (I6.)
5. **Soft-delete resurrection in expansion fallbacks (Q17 PARTIAL)** — the primary vector/hybrid path got `doc_deleted_at IS NULL` (`pgvector_store.py:270`), but the parent-chunk fetch `orchestration/nodes/retrieve.py:1778-1785` and the neighbor fetch `orchestration/nodes/neighbor_expand.py:348-357` JOIN `documents` with **no `d.deleted_at IS NULL`** → a soft-deleted doc's chunk can be resurrected through expansion. NEW / not in the L1 register but on the retrieval side of the ingest→retrieve chain; worth a follow-up.
6. **PII Singleton caches first-resolved provider** — `bootstrap.py:450`. If ops flip `pii_redactor_provider` at runtime after the first `.pii()` call, the change is not picked up until restart. Degrades quietly (comment says PER-CALL). Minor.

---

## §E. RESIDUAL / DATA NEEDED (rule#0 — not guessed)
- I1 real-world Path-B degradation on `POST /documents/create` needs a runtime ingest trace (psql/runtime not available this session) to quantify blast radius vs the code-evidence above. Mechanism = FACT; production severity = needs measure.
- I12 bind-ceiling crash threshold (~2900 chunks) is arithmetic on bind count (FACT of the SQL shape), not a runtime-reproduced overflow.
- Q17 PARTIAL (item D.5) should be confirmed against a live soft-deleted doc + expansion query to prove resurrection end-to-end.
