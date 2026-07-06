# DEEPDIVE — src/ragbot/application/services/document_service/ (ingest core)

Date: 2026-07-02 · Reader: subagent (full-file read, all 9 files, 6,330 lines)
Scope: `src/ragbot/application/services/document_service/` — every file, every line.
Method: full Read of all files + cross-verification greps into parsers, constants, schema (`alembic/squashed_baseline.sql`), worker, and routes. Every claim carries `file:line`. Labels: **FACT** (code/schema evidence), **HYPOTHESIS** (needs runtime verify — CHƯA verify).

Runtime caveat: live DB `system_config` rows could not be queried in this session (psql auth unavailable) — all "flag default" statements are code-default evidence (`shared/constants/*`), not live-row evidence.

---

## 1. File-by-file: what each file actually does

### 1.1 `__init__.py` (1,094 lines) — service skeleton + resolvers + delete family
- `DocumentService(_IngestMixin)` — DI'd with `session_factory, embedder, settings, config_service, audit_logger, parser_detector, model_resolver, pii_redactor, bot_repo, source_validator, chunk_context_enricher, stats_index_repo, narrate_service, corpus_version_service` (`__init__.py:194-274`). **No `sanitizer` parameter exists** (relevant to finding F3).
- `_CONTENT_TABLES = ("document_chunks", "document_service_index")` + `_purge_content_tables()` (`__init__.py:164-188`) — single source of truth for content purge; whitelist-guarded f-string table names (injection-safe). Used by all 3 delete paths.
- Embedding resolution: `_embedding_spec()` (`:358-424`) — resolver-first (per-bot `bot_model_bindings` via `model_resolver`), fallback system_config → settings; forces `task=passage` (`:393-394`); `_apply_language_embedding_override()` (`:426-462`) swaps model NAME per `system_config.embedding_model_by_language` (F12).
- `_embed_in_doc_batches()` (`:464-562`) — doc-level batching (default 100/batch, `_04_jwt_auth.py:77`), per-batch `asyncio.wait_for` timeout, inter-batch sleep, and **`canonicalize_embed_text` (URL-strip) applied here only** (`:518`).
- Per-bot resolvers, each doing its own `SELECT plan_limits FROM bots`: `_resolve_embedding_passage_prefix` (`:564-614`), `_resolve_chunking_policy` (`:616-654`), `_resolve_embedding_text_strategy_name` (`:656-710`), `_resolve_chunk_hash_id_enabled` (`:712-770`).
- `_extract_metadata_llm()` (`:865-901`) — direct `litellm.acompletion` call (no LLM port).
- Delete family: `replace_documents_for_bot` (`:903-986`, UPSERT-safe URL-scoped purge + soft-delete + semantic_cache purge), `delete_all_for_bot` (`:988-1028`, purge + **HARD** `DELETE FROM documents` `:1009-1012`), `delete_document` (`:1030-1091`, purge + **SOFT** delete). All three bust corpus_version.
- Stats-index writers: `_insert_stats_index` (`:297-328`), `_upsert_doc_summary` (`:330-356`) — best-effort, swallow errors.

Pipeline connection: constructed at 3 sites — `document_worker.py:597-610` (canonical async path), `sync.py:519-531` & `:714+` (BE sync bulk), `test_chat/_shared.py:326-336` (internal harness).

### 1.2 `ingest_core.py` (825 lines) — `_IngestMixin.ingest()` U1–U7 orchestrator
- mime sniff correction for ambiguous declared mime (`:262-273`, only when `raw_bytes` present).
- U1 validate (`:277-286`): warns (does not fail) on missing tenant.
- Source-allowlist gate (`:296-303`) → `_maybe_validate_source_allowlist` — correctly passes `config_service=self._cfg`.
- U2 parse (`:305-344`): `_route_through_parser` (defined `__init__.py:794-863`) runs the parser registry **only when `raw_bytes is not None`**; keeps `parser_row_chunks` side-channel for row-shaped sources; optional markdown normalizer (default OFF, `_11:65`).
- PII redaction call (`:346-353`) → `_maybe_redact_ingest_content` — **does NOT pass `config_service`** (finding F2).
- Size gate `max_ingest_content_chars` (default 500,000 — `_03:78`), source-URL dedup → re-index reuse (`:392-417`), content-hash dedup → `DocumentDuplicateError` (`:429-454`).
- Document row upsert: re-index UPDATE (`:461-483`, does NOT refresh `language`/`source_url`/`mime_type`) vs INSERT `ON CONFLICT ON CONSTRAINT uq_doc_tool DO UPDATE` with `RETURNING id` reconciliation (`:500-550`); `tool_name = title.lower().replace(" ", "_")[:64]` (`:525`); `state='active'` set at INSERT time (`:505`).
- Builds `_IngestCtx`, runs stages U3→U4→U5→U6, computes enriched-text chunk hashes (`:600`), incremental diff (`:650-663`), dead diff-reingest telemetry block (`:681-707` — finding F1), then U7 + finalize.
- `_extract_graph_entities` (`:720-825`) — background GraphRAG triple extraction; direct `litellm.acompletion` via inline `_MiniLLM` (`:754-771`); sequential per-chunk LLM calls, no semaphore/timeout; broad-except best-effort (`:819`).

### 1.3 `ingest_stages.py` (1,033 lines) — `_IngestCtx` + U3 clean / U4 chunk / U6 VN-segment
- `_ROW_PRESERVE_PROVIDERS = {"excel_openpyxl", "google_sheets"}` (`:131`) + `_parser_row_shaped()` (`:134-144`) — reads the parser stamp from `parser_row_chunks[0].metadata.parser`.
- `_should_store_whole_doc()` (`:147-173`) — whole-doc single-chunk gate; documents the 2026-07-01 xe-bot bug (3077-char sheet collapsed → `col_N` stats rows) and guards it with the parser stamp.
- U3 (`:274-390`): CleanBase Tier-0 sanitize (needs `self._sanitizer` — never wired, finding F3) → `_clean_document_text` → optional LLM metadata extraction.
- U4 (`:392-952`): whole-doc gate → tenant style normalize (`apply_tenant_style`, per-bot `style_profile`) → parent-child OR block-pipeline/legacy analyze + `select_strategy` (policy-resolved `table_strategy`, per-bot `force_strategy` wins `:673-675`) → **row-preserve bypass** (`:763-768`, strategy literal `"parser_preserve"`) → `smart_chunk` → orphan-merge (skipped for row-atomic strategies `:794-798`) → M25 block histogram → P0-2 numeric + char coverage observe-only gates (`:868-905`) → progress checkpoint 20%.
- U6 (`:954-1033`): VN compound segmentation — precomputed in U5 (CR path) or gathered post-enrich; gated on `effective_language in VI_DOMAIN_LANGUAGES` (`("vi",)`, `_02:232`).

### 1.4 `ingest_stages_enrich.py` (633 lines) — U5 enrich
- Parallel config gather (8 keys, `:150-178`), row gate (`should_skip_row_enrich`) for tabular strategies.
- Inline CR (Anthropic contextual retrieval) — flag `contextual_retrieval_enabled` **code default OFF** (`_11:111`); doc-size cost guard; U5∥U6 per-chunk gather with prompt-cache warm-up (`:381-403`); on CR the `chunks` list is REPLACED by enriched text (`:420`).
- WA-3 Enhanced-CR (`cr_enhanced_enabled`, per-bot via `resolve_bot_limit`) → separate `chunk_contexts` storage column.
- Legacy prefix enrichment (`enrichment_enabled`) — direct `litellm.acompletion` closure (`:479-494`); small-doc skip (`enrichment_skip_below_chars`).
- Quality scoring: observability-only (`_chunk_quality_skip_indices` intentionally always empty `:586`).
- `enriched_prefix_persist_in_content` default ON (`_11:95`) → `persist_chunks = enriched_chunks`.
- Big banner comments document the "5 nano-in-ingest paths" all seeded OFF in DB (alembic 0228/0230/0231 per comments `:201-210`, `:232-242`, `:445-452`, store `:680-689`) — the operative kill-switches are DB rows, while several in-code constant defaults remain ON (`DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED` fallback True per store comment `:685-688`) — a config-default drift trap on a fresh DB.

### 1.5 `ingest_stages_store.py` (1,097 lines) — U7 embed + store
- `_atomic_original_meta()` (`:121-147`) — F5 dual-read: persists `original_content` + shape-detected `block_types`.
- Embedding-text strategy per-bot (`raw_only` / `prefix_plus_raw` / `field_selective`; `auto` derives from chunk structure `:224-235`).
- Narrate-then-embed (Wave E3) with `asyncio.timeout` (`:283-317`).
- Passage prefix prepend (`:319-327`) → empty-embed substitution (`:329-346`) → **late chunking** (default-ON inline `True` at `:359`) / sliding variant → fallback `_embed_in_doc_batches` with fail-loud mark-failed+soft-delete on error (`:469-513`) and length-mismatch abort (`:521-548`).
- Chunk-ID factory: deterministic UUID5 per-bot opt-in vs UUIDv7 (`:614-637`).
- Three insert loops (parent / child / flat) build metadata (structural path for HDT, structured refs, narrate meta, F5 originals, WA-3 context) → `_bulk_insert_chunks` (single multi-row INSERT).
- Stale/changed chunk deletes (`:710-727`), semantic_cache invalidation on mutation (`:1049-1070`).

### 1.6 `ingest_stages_final.py` (608 lines) — finalize + stats index
- `_dedup_stats_entities()` (`:139-186`) — collapse dual-index duplicate entities (name+price key, richest wins, priced-beats-unpriced).
- `_decide_ingest_state()` (`:189-216`) — active/failed with leaf-embed coverage floor (default 0.8, `_20:244`).
- Atomic state flip + `deleted_at` clear on re-activate (`:328-360`); corpus-version bust; honest `document_ingested` vs `document_ingest_failed` logging (`:382-400`).
- GraphRAG background task (skipped in lazy mode).
- Stats-index write (`:443-572`): raw-row preference (`meta.raw_chunk`), ADR-0006 per-bot `custom_vocabulary.column_roles`, G4 header-quality advisory, delete-before-insert idempotency (`:546-556`), `aggregate_summary` → `documents.summary_json`.

### 1.7 `ingest_helpers.py` (494 lines) — persistence + safety free functions
- `_bulk_insert_chunks()` (`:145-241`) — ONE multi-row INSERT for all rows; embedding column whitelist-validated; 11–12 bind params per row (finding F8).
- `_maybe_redact_ingest_content()` (`:244-338`) — two-knob PII gate; **when `config_service is None` → `feature_enabled = DEFAULT_RECAP_PII_ENABLED` (False, `_19:108`)** (finding F2).
- `_maybe_validate_source_allowlist()` (`:341-487`) — PoisonedRAG defence, two-knob, correctly wired.

### 1.8 `ingest_phases.py` (345 lines) — Phase-D observability
- `INGEST_STEP_NAMES` 7 canonical U-step names (`:153-161`); `_phase_d_step()` fail-soft async CM with careful body-exception re-raise contract (`:167-267`); `IngestResult` dataclass (`:270-283`); `_update_doc_progress()` best-effort (`:286-336`).

### 1.9 `text_processing.py` (201 lines) — pure text helpers
- `_fix_hyphenation` (VN-aware), `_strip_prompt_injection` (compiled regex over `PROMPT_INJECTION_PATTERNS`), `_clean_document_text` (NFC, hyphenation, injection strip, blank-line collapse, **repeated-line strip `:97-102`** — finding F10, whitespace collapse), `canonicalize_embed_text` (URL strip for embed-only text), `chunk_type_for` (block-type → `chunk_type` column; `is_table_row` short-circuit ONLY for `table_csv` — see F16), `should_skip_row_enrich`.

---

## 2. FULL ingest flow per format — where each format diverges

Two structurally different entry paths exist (this is the single most important fact in this report):

**Path A — service-parses (raw_bytes present)**: `test_chat` upload route (`document_routes.py:516-527`), `sync.py:558-569` (only when caller declares a mime). U2 runs the registry inside `ingest()`; `parser_row_chunks` preserved; row-shaped guard + `parser_preserve` strategy available.

**Path B — canonical B2B worker path** (`POST /api/ragbot/documents/create` → outbox → `document_worker`): the worker parses via the same registry **but flattens**: `full_text = "\n\n".join(c["content"] ...)` (`document_worker.py:465-467`), then calls `ingest(content=full_text, blocks=parsed_blocks, ...)` with **NO `raw_bytes`, NO `file_name`** (`document_worker.py:613-625`). Inside `ingest()`, `parser_row_chunks` stays `None` (`ingest_core.py:314-337` gate `raw_bytes is not None`) → row-preserve and the whole-doc row-shape guard can never fire (finding F4).

Registry coverage (FACT, `infrastructure/parser/registry.py:45-61` + each `supports()`):

| Format | Parser | Output shape | Path A rating | Path B (canonical) rating |
|---|---|---|---|---|
| PDF (text) | `kreuzberg_markdown` (mimes/exts `kreuzberg_markdown_parser.py:46-56`) | structured markdown, 1 chunk | production-ready | production-ready (same flatten, 1 chunk anyway) |
| PDF (scanned) | — worker OCR fallback only (`document_worker.py:492-506`) | typed Blocks + flat text | **broken on Path A** (no OCR in service; `ingest_no_parser_match` fallback to empty `content`) | happy-case (OCR quality-dependent) |
| PPTX | `kreuzberg_markdown` | markdown | happy-case (no dedicated slide semantics) | happy-case |
| HTML/XHTML | `kreuzberg_markdown` | markdown | happy-case | happy-case |
| DOCX | `docx` (`docx_parser.py:64-68`, .docx/mime only) | markdown | production-ready | production-ready |
| **DOC (legacy)** | **none** (docx=.docx only; kreuzberg excludes it; `mime_sniff` has no OLE signature `mime_sniff.py:127-149`) | — | **broken** (parser miss → content fallback = raw bytes never decoded) | **broken/happy-case** (OCR fallback if URL) |
| XLSX | `excel_openpyxl` (`excel_openpyxl_parser.py:59-63`) | **row-per-chunk** (`parser` stamp, `:115-122`) | production-ready (row-atomic + stats index) | **degraded — rows flattened** (F4) |
| **XLS (legacy)** | **none** (xlsx mime/ext only) | — | **broken** | **broken/happy-case** |
| CSV | `google_sheets` (supports `text/csv` + `.csv`, `google_sheets_parser.py:57-60`) | **row-per-chunk** | production-ready | **degraded — rows flattened** (F4) |
| Google Sheets | `google_sheets` (worker rewrites viewer URL → CSV export, `document_worker.py:404-417`) | row-per-chunk | production-ready | **degraded — rows flattened** (F4) |
| MD | `markdown` (H1/H2 section split, `markdown_parser.py:84-127`) | section chunks | production-ready | production-ready |
| TXT | `markdown` (text/plain fall-through, `markdown_parser.py:20-30`) | 1 section | production-ready | production-ready |
| Images | `vlm_image` (explicit build only, `registry.py:56-60`) | caption | n/a on Path A | happy-case (needs VLM enabled) |

Why "degraded" on Path B for tabular formats (evidence chain): pipe-markdown from `rows_to_structured_markdown` has no commas → `_is_csv_format` is comma-based (`shared/chunking/analyze.py:35-65`) → False; the sheets markdown carries `##` section headings → `select_strategy` table fast-path requires `is_csv and total_headings == 0` (`analyze.py:454`) → never taken → weighted scorer picks hdt/recursive/hybrid (`analyze.py:484-542`) → multiple rows packed per ~1000-char chunk (cross-row conflate class of bug), and a small sheet (< whole-doc threshold) can still collapse to ONE whole-doc chunk because `parser_is_row_shaped=False` on this path (`ingest_stages.py:433-440`) — i.e. **the 2026-07-01 xe-bot fix only protects Path A**.

---

## 3. Stats-index path vs chunk path

- Chunk path: U4 chunks → U5 enrich → U7 rows → `document_chunks`.
- Stats path (`ingest_stages_final.py:443-572`): runs AFTER the chunk insert, feeds the **inserted rows only** (`ctx.rows`) to `parse_table_chunks` (deterministic, delimiter-gated, `document_stats.py:937-977`), prefers `meta.raw_chunk`, dedups (`_dedup_stats_entities`), then `delete_by_document(doc_id)` (deletes ALL rows for the doc, `stats_index_repository.py:158-170`) + `bulk_insert` + `documents.summary_json` upsert. Table `document_service_index` (baseline.sql:299-312): tenant/workspace/bot/doc/chunk keys + `entity_name/category/price_primary/price_secondary/attributes_json`.
- Coupling defect: because `ctx.rows` contains only **changed** chunks on a re-index, the delete-all + insert-subset sequence destroys entities of unchanged rows (finding F5).

## 4. Re-ingest / upsert / purge semantics

- Dedup ladder: source_url match → adopt doc_id (re-index) (`ingest_core.py:392-417`); content-hash duplicate (new doc) → 409 (`:429-454`); `uq_doc_tool` name collision → silent adopt-and-overwrite (finding F6).
- Incremental re-index: per-`chunk_index` hash diff over **enriched** text (`:600`, pinned by test); unchanged → skip embed; stale (`idx >= len(chunks)`) + changed indices deleted then re-inserted (`store.py:710-727`). Consequences: (a) any knob that changes only the EMBEDDED text (embedding_text_strategy, passage prefix, narrate, language-model override, canonicalize) does NOT re-embed on re-ingest — stale vectors under matching hashes (finding F12); (b) when CR/enrich LLM paths are ON, hashes are LLM-nondeterministic → every re-ingest re-embeds everything (cost).
- Purge: all delete/replace paths route through `_purge_content_tables` (chunks + stats index) — good consolidation; semantic_cache purged per-bot on every mutation; corpus_version busted. Residual inconsistency: `delete_all_for_bot` hard-deletes `documents` (`__init__.py:1009-1012`) contradicting the "metadata soft-deleted (forensic)" policy stated at `__init__.py:158-163`.
- Embedding column: single `DEFAULT_EMBEDDING_COLUMN = "embedding"` (`_02:85`), whitelist `ALLOWED_EMBEDDING_COLUMNS` validated in `_bulk_insert_chunks` (`ingest_helpers.py:181-185`) — no version-ref columns. Clean.

---

## 5. FINDINGS (ranked)

### F1 — FACT/CONFIRMED · Dangling `_diff_reingest_compute` → NameError on config flip
`ingest_core.py:695,701` call `_diff_reingest_compute` / `_diff_reingest_log_event`. These names are defined NOWHERE: `shared/diff_reingest.py` is a dead-code module with every function commented out (its own header `diff_reingest.py:1-22` claims "helpers copy-pasted inline into document_service.py" — false, grep 0 hits), and the three star-imported modules' `__all__` lists exclude them (verified via AST). Guarded by `is_reindex and cfg.get("diff_based_reingest_enabled")` (default False, `_04:118`, not seeded in alembic). **Failure scenario**: operator flips `diff_based_reingest_enabled=true` → every re-ingest raises `NameError` after the doc row is committed → doc stuck, recovery loops. A T2 cost feature whose flag is a landmine.

### F2 — FACT/CONFIRMED · Ingest-boundary PII redaction is dead (built-but-not-wired)
`ingest_core.py:347-353` calls `_maybe_redact_ingest_content` WITHOUT `config_service`; `ingest_helpers.py:319-320` then sets `feature_enabled = bool(DEFAULT_RECAP_PII_ENABLED)` = **False** (`constants/_19:108`) → `RecapPiiDetector.detect(feature_enabled=False, ...)` → always `skipped_flag_off`. **Failure scenario**: bot owner sets `plan_limits.pii_redaction_enabled=true` AND operator sets `system_config.recap_pii_enabled=true` → raw PII is still chunked/embedded/persisted; the DB kill-switch is unreadable from this call site. The docstring contract at `__init__.py:224-228` ("raw document content is masked at the ingest boundary") is not honored.

### F3 — FACT/CONFIRMED · CleanBase Tier-0 sanitizer never wired anywhere
`ingest_stages.py:310-311` reads `getattr(self, "_sanitizer", None)`; `DocumentService.__init__` has no such parameter (`__init__.py:194-235`) and repo-wide grep shows zero assignment sites — only the orphan factory `infrastructure/safety/registry.py:88 build_sanitizer`. Flag `cleanbase_tier0_enabled` default True (`_20:40`) is a permanent no-op ("no_sanitizer_wired" debug path). **Failure scenario**: HTML-tag strip / zero-width removal / NFC / blacklist Tier-0 never runs for ANY tenant; only the legacy regex sweep in `_clean_document_text` defends. Sanitize-report observability永远 empty.

### F4 — FACT · Canonical worker path flattens row-shaped parses — `parser_preserve` unreachable on Path B
`document_worker.py:465-467` joins parser chunks to `full_text`; `document_worker.py:613-625` calls `ingest()` without `raw_bytes`/`file_name`; `ingest_core.py:314-337` only populates `parser_row_chunks` when `raw_bytes is not None`. Chain evidence in §2 shows pipe-markdown defeats `_is_csv_format` (`analyze.py:35-65`) and the table fast-path (`analyze.py:454`). **Failure scenario**: XLSX/CSV/Sheets ingested via `POST /api/ragbot/documents/create` lose 1-row-per-chunk atomicity (multi-row chunks → cross-row value conflate; small sheets can whole-doc collapse → `col_N` stats regression that the 2026-07-01 fix was supposed to close — the fix only covers the raw_bytes path). This is the #1 multi-format/happy-case gap: the row-preserve feature works in the internal test harness but not on the canonical B2B path. HYPOTHESIS on exact live strategy pick per doc (needs one traced ingest per `ingest-backward-trace-debug`), FACT that `parser_preserve` cannot trigger there.

### F5 — FACT · Partial re-ingest wipes stats-index entities of unchanged rows
`ingest_stages_final.py:443` gates on `rows` = ctx.rows = **only inserted (changed/new) chunks** (built from `chunks_to_embed`, `store.py:944-1046`); `:548` `delete_by_document(doc_id)` deletes ALL entities for the doc (`stats_index_repository.py:158-170`); `:561-567` re-inserts only entities parsed from the changed subset. **Failure scenario**: re-ingest a 100-row price sheet with 1 edited row → 99 unchanged rows keep their chunks (hash match) but their `document_service_index` entities are deleted and never re-inserted → count/price/aggregate answers collapse to ~1 entity until a full re-ingest. Inverse edge: if the changed chunk parses to 0 entities, the delete never runs → stale entities survive.

### F6 — FACT · `tool_name` collision silently merges two different documents
`tool_name = title.lower().replace(" ", "_")[:64]` (`ingest_core.py:525`); `uq_doc_tool = UNIQUE(record_tenant_id, record_bot_id, tool_name)` (`models.py:315`, `squashed_baseline.sql:1061`); `ON CONFLICT DO UPDATE ... RETURNING id` adopts the EXISTING doc id (`ingest_core.py:506-550`) while `is_reindex` stays False → `existing_hashes` never loaded (`:629-637` gated on `is_reindex`) → old chunks are never deleted and new chunks INSERT with overlapping `chunk_index` (no unique constraint on `document_chunks` beyond PK id — `squashed_baseline.sql:940-941`). **Failure scenario**: bot uploads "Bảng giá 2025" (PDF) then "bảng giá 2025" (sheet, different source_url, different content hash) → one `documents` row whose metadata is the second doc but whose chunks are BOTH docs interleaved with duplicate chunk_index → retrieval mixes corpora; subsequent re-index diffs against a chimera. Multi-doc axis: title is the only namespace; two legitimately distinct docs colliding on 64 lowercase chars is a realistic multi-doc catalog scenario.

### F7 — FACT · `_bulk_insert_chunks` exceeds asyncpg's 32,767 bind-param ceiling on large row-per-chunk docs
One INSERT statement binds 11 params/row (+1 shared; 12 with parent_chunk_id) for ALL rows (`ingest_helpers.py:200-241`) → hard ceiling ≈ 2,978 rows/statement (asyncpg int16 protocol limit). `MAX_DOCUMENT_CONTENT_CHARS = 500,000` (`_03:78`) permits sheets with 3,000–5,000 row-chunks (~100-170 chars/row), and the code's own docstring cites a real "3851-chunk document" (`__init__.py:477`). **Failure scenario**: 500K-char catalog sheet on the raw_bytes path → 4,000 row chunks → single INSERT fails (`too many arguments`) after embed cost was already paid → doc marked stuck/failed. FACT for the math/protocol limit; HYPOTHESIS that a live doc has hit it (needs one load test with a >3k-row sheet).

### F8 — FACT · Default embed path (late chunking ON) bypasses canonicalization, doc-batch timeout, and pacing
`late_chunking_enabled` read with inline default `True` (`store.py:359`) → `late_chunk_embed` (`late_chunking.py:54-99`) embeds `[Document context: …]{chunk}` in ONE `embed_batch` await — no `canonicalize_embed_text` (URL strip lives only in `_embed_in_doc_batches`, `__init__.py:518`), no per-batch timeout (`DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S` guard only at `__init__.py:520-544`), no progress events. **Failure scenario**: the URL-stuffed warehouse sheet documented in `text_processing.py:113-129` ("appears to stall the embedder on URL-heavy chunks") still embeds raw URLs on the DEFAULT path — the fix only protects the fallback path. Additionally `store.py:322-327` prepends the asymmetric passage prefix BEFORE `late_chunk_embed` wraps its own prefix in front → passage head is no longer at position 0 (latent — default prefix empty).

### F9 — FACT · Stats rows written under a fabricated tenant UUID
`ingest_stages_final.py:562`: `record_tenant_id=record_tenant_id or uuid.uuid4()`. **Failure scenario**: any legacy/edge caller without a tenant produces `document_service_index` rows keyed to a random nonexistent tenant — permanently invisible to tenant-scoped readers, silent data corruption instead of fail-loud. Multi-tenant hygiene violation (the row's tenant is an integrity key, not a default-able field).

### F10 — FACT · Repeated-line strip silently deletes legitimate repetitive content (happy-case cleaner)
`_clean_document_text` removes ANY line occurring ≥3 times and <100 chars (`text_processing.py:97-102`), applied to every text-path format when `ingestion_cleaning_enabled` (default True, `ingest_stages.py:296-298`). The heuristic assumes repeats = PDF headers/footers. **Failure scenario**: a TXT/DOCX menu where "Giá: 500.000đ" or a repeated size label appears ≥3 times → all occurrences stripped BEFORE chunking → numbers unreachable at retrieval; only the observe-only `chunk_numeric_coverage_gap` warning (`ingest_stages.py:869-879`) would hint at it. This is precisely the "silently dropped value = number-HALLU" class P0-2 warns about, caused by our own cleaner.

### F11 — FACT · No language detection: `language="auto"` hardcodes to `vi` (single-locale happy case)
`ingest_core.py:532` and `enrich.py:302` map `"auto"` → `DEFAULT_LANGUAGE = "vi"` (`_02:230`); the worker sends `parsed_language or "auto"` where `parsed_language` is set only on the OCR fallback (`document_worker.py:497-506,619`). **Failure scenario**: an English/Japanese doc ingested via the registry path is recorded `language='vi'`, becomes VN-segmentation-eligible (`VI_DOMAIN_LANGUAGES=("vi",)`, underthesea runs on non-VN text — CPU waste + token mutation risk), and the F12 `embedding_model_by_language` override keys on the wrong language. Multi-locale tenants must remember to set `bots.language` per doc; the platform never verifies. Re-index UPDATE also never refreshes `language` (`ingest_core.py:461-483`).

### F12 — FACT · "Re-embed required" knobs silently no-op on re-ingest (hash-diff blind spot)
Content hash = sha256(enriched text) only (`ingest_core.py:600`, `__init__.py:772-784`). Embedded bytes additionally depend on embedding_text_strategy, passage prefix, narrate output, language model override, canonicalize (`store.py:215-327`). Resolver docstrings say "Re-embedding REQUIRED for changes to take effect" (`__init__.py:578,673-675`) — but a re-ingest of unchanged content skips every chunk (hash match) so there is NO supported way to re-embed short of deleting the doc. **Failure scenario**: owner flips `embedding_text_strategy` to `raw_only` to fix keyword dilution, re-uploads the doc, sees "chunks_unchanged=N", vectors unchanged, conclusion "feature broken".

### F13 — FACT · Legacy DOC/XLS have no parser (CLAUDE.md declares them first-class)
No `supports()` accepts `application/msword` / `application/vnd.ms-excel` (docx: `docx_parser.py:64-68`; excel: `excel_openpyxl_parser.py:59-63`; kreuzberg exclusion comment `kreuzberg_markdown_parser.py:45-46`); `mime_sniff` has no OLE-compound signature (`mime_sniff.py:124-149`). **Failure scenario**: .doc upload on the raw_bytes path → `ingest_no_parser_match` → falls back to caller `content` (empty for binary uploads) → 0 chunks; on the worker path best case is OCR. CLAUDE.md architecture section lists "DOCX/DOC · XLSX/XLS" as first-class formats.

### F14 — FACT · Application layer hardcodes infra parser names + strategy literals (T3 / open-closed)
`_ROW_PRESERVE_PROVIDERS = frozenset({"excel_openpyxl", "google_sheets"})` in the application stage (`ingest_stages.py:131`) — adding the next row-shaped parser (e.g. a future xls adapter) requires editing core, violating the "add format = 1 adapter file, orchestrator untouched" contract. Similarly strategy string literals `"parser_preserve"` (`ingest_stages.py:767`), `("table_csv","table_dual_index","parser_preserve")` (`:794-796`), `"table_csv"` (`store.py:819,923,1031`), `"hdt"` (`store.py:758,861,968`) bypass existing constants (`ROW_PRESERVE_CHUNK_STRATEGY`, `CR_ROW_GATED_STRATEGIES`, `STRUCTURAL_CHUNK_STRATEGIES`). A capability flag on the parser port (`emits_row_chunks`) would be the expert fix.

### F15 — FACT · Zero-hardcode violations (inline defaults that belong in `shared/constants.py`)
- `store.py:350` `late_ctx_chars = 200`; `:359` `get("late_chunking_enabled", True)`; `:360` `get_int("late_chunking_context_chars", 200)` (constants exist for the SLIDING variant only, imported at `:361-366`).
- `store.py:577-584` `validation_min_chars = 20` + `get_int("ingestion_min_chunk_chars", 20)`.
- `ingest_core.py:742` `get_int("graph_rag_max_triples_per_chunk", 10)`.
- `ingest_stages.py:412/416` `whole_doc_enabled` default **True with cfg, False without** — inconsistent fallback pair.
- `__init__.py:418` fallback `provider="litellm"` literal in the spec constructor.

### F16 — FACT · `chunk_type` misses row semantics for `parser_preserve` / `table_dual_index`
`chunk_type_for(..., is_table_row=(_chunking_strategy == "table_csv"))` at all three insert sites (`store.py:817-821, 921-925, 1029-1032`) — Excel/Sheets rows (strategy `parser_preserve`) and dual-index rows fall through to the heuristic classifier → labeled `table`/`text`, not `table_row`. **Failure scenario**: any downstream consumer keying on `chunk_type='table_row'` (modality rerank, analytics) treats parser-preserved sheet rows differently from CSV rows that carry identical semantics.

### F17 — FACT · Direct `litellm.acompletion` calls in application service (Strategy+DI bypass)
Three sites: `_extract_metadata_llm` (`ingest_core.py:865-901`), `_extract_graph_entities._MiniLLM` (`:745-777`), U5 `_enrich_llm` closure (`enrich.py:479-494`). All bypass the LLM router port (no circuit breaker, no per-bot binding, no cost accounting, no API-key pool). Graph extraction is additionally sequential per chunk with no semaphore or timeout in a background task.

### F18 — FACT · Per-ingest bot-config chatter: ~7 sequential lookups of the same bot row
Four private resolvers each run their own `SELECT plan_limits FROM bots` (`__init__.py:580-614, 616-654, 656-710, 712-770`) + three `bot_repo.get_by_id` calls (PII `ingest_helpers.py:284`, WA-3 `enrich.py:260-274`, column_roles `final.py:470-477`). All sequential awaits (violates gather-first Rule 1 for independent reads). T2: adds 6 avoidable round trips per document; T3: no single `BotIngestPolicy` resolution.

### F19 — MEDIUM/FACT · Duplicate-content rows break deterministic chunk IDs (opt-in)
`deterministic_chunk_id(record_bot_id, document_id, content)` (`store.py:623-631`): two identical rows in one sheet (real catalogs repeat rows) → identical UUID5 → PK violation aborts the entire multi-row INSERT. Default OFF (`_15:93`) so latent; the flag's promise ("idempotent UPSERT") is also not honored — the INSERT has no ON CONFLICT (`ingest_helpers.py:237-241`).

### F20 — LOW/FACT · Misc contract drift
- `original_content` (F5 dual-read) stores CR-ENRICHED text when CR is active: `enrich.py:420` replaces `chunks`; `ingest_core.py:650` builds `chunks_to_embed` from it; `store.py:795/905/1014` labels it "pre-transform raw". Latent (CR default OFF `_11:111`).
- `delete_all_for_bot` docstring still describes a "3-key triple" (`__init__.py:997-999`) vs 4-key identity; `ingest()` dedup comment cites `uq_bots_tenant_bot_channel` (`ingest_core.py:390-391`) — stale vs 4-key unique name.
- Re-index UPDATE never refreshes `source_url`/`mime_type`/`language`/`workspace_id` (`ingest_core.py:461-483`).
- `documents_stream_upload.py` exists but is unmounted with a DISABLED header (`:3`) — compliant with the one-canonical-ingest rule, kept for reference (not a violation, noted for completeness).

### Broad-except audit (policy check)
All `except Exception` sites in scope carry `# noqa: BLE001` with best-effort/observability justifications matching the 3 allowed categories (e.g. `__init__.py:322,350,396,899`; `final.py:362,549`; `ingest_phases.py:215,235,257,328`; `ingest_helpers.py:287,310,394,413,448`; worker `document_worker.py:484` fetch fallback). Two weaker ones: `enrich.py:521` and `:539` catch `Exception` for a simple config read where `(ValueError, TypeError)` would do (other identical reads in `final.py:288-293` are correctly narrow). No un-noqa'd violations found. No version-refs, no brand/tenant literals, no `if provider ==` ladders found in scope (parser/embedding/narrate all go through registries — F14's provider-name frozenset is the one boundary leak).

---

## 6. Axis summaries

- **Multi-format**: strongest on Path A; the canonical Path B silently downgrades every row-shaped format (F4). Legacy DOC/XLS unsupported (F13). Scanned PDF/images only work on Path B. Format parity is therefore path-dependent — the exact opposite of "MỌI format đi CÙNG 1 luồng canonical".
- **Multi-doc**: `tool_name` 64-char lowercase namespace can merge distinct docs (F6); content-hash dedup is per-bot and correct; stats aggregation is per-doc with cross-doc reads at query time — no cross-doc joins at ingest (by design).
- **Multi-bot**: per-bot config is honored broadly (chunking policy, force strategy, style profile, embed strategy, passage prefix, hash-id, CR-enhanced, column_roles, allowlist) — genuinely good; but `parent_child_enabled`, `whole_doc_enabled`, `late_chunking_*`, CR model/limits are platform-global system_config only (one operator flip changes all bots), and the 7-lookup chatter (F18) taxes every bot equally.
- **Multi-tenant**: sessions consistently `session_with_tenant`-scoped; stats repo `delete_by_document` deliberately unscoped (documented, PK-global); the `uuid.uuid4()` tenant fallback (F9) is the one integrity hole; `record_tenant_id=None` passed to embed calls is harmless for the litellm embedder (param is `ARG002`-unused, `litellm_embedder.py:140`) — CHƯA verify other embedders.
- **T1 smartness**: F4 (row conflate on canonical path), F10 (cleaner data loss), F5 (stats wipe) are the three that directly corrupt answers/ground truth. F12 blocks tuning recovery.
- **T2**: F8 (no timeout/pacing on default path), F18 (config chatter), full-bot semantic-cache nuke per doc mutation (`store.py:1053-1070` — intended but coarse).
- **T3**: F14/F15/F17 pattern violations; the mixin/star-import architecture (5 near-identical 100-line import blocks across stage files) makes the F1-class dangling-name failure mode easy to reproduce.
