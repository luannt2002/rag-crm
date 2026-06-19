# Ingest/Upload Flow Deep-Dive — Debug Trace & Backward Verification

**Date**: 2026-06-18  
**Scope**: READ-ONLY. No code changed. Evidence: file:line citations throughout.  
**Goal**: Per-run debug trace capabilities + backward verification (given wrong answer → trace gold chunk creation).

---

## 1. Pipeline Node Map (U0 → U7)

Reference canonical: `RAGBOT_STEP_PIPELINE.md` (U0/U0.5 + U1-U7).

### U0 — IDENTITY_VALIDATE

- **Input**: HTTP body `(workspace_id, bot_id, channel_type)` + JWT bearer `record_tenant_id`
- **Output**: `request.state.record_tenant_id` (UUID) lifted by `TenantContextMiddleware`
- **Code**: `src/ragbot/interfaces/http/schemas/document_schema.py` (Pydantic + WorkspaceIdValidator)
- **Errors**: 422 `WORKSPACE_ID_INVALID` on bad slug; missing tenant → `record_tenant_id=None` logged as warning

### U0.5 — BOT_RESOLVE_4KEY

- **Input**: `(record_tenant_id, workspace_id, bot_id, channel_type)` — 4-key tuple
- **Output**: `BotConfig` containing `record_bot_id` (UUID)
- **Code**: `src/ragbot/application/services/bot_registry_service.py`
- **Redis cache key**: `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}`

### U1 — VALIDATE

- **Code entry**: `src/ragbot/application/services/document_service/ingest_core.py:177` (ingest method signature)
- **Input args**: `record_bot_id`, `title`, `content`, `source_url`, `mime_type`, `raw_bytes`, `file_name`, `blocks`, `language`, etc.
- **What runs**:
  1. Optional MIME sniff: `shared/mime_sniff.py::sniff_real_mime()` — corrects `octet-stream` → real type. Log event: `ingest_mime_sniff_corrected` (`ingest_core.py:267`)
  2. `_phase_d_step(step_tracker, "ingest_validate")` — writes `request_steps` row with `n_bytes`, `mime_detected`, `language_in`, `channel_type`, `is_reindex` (`ingest_core.py:277`)
  3. Source allowlist gate: `_maybe_validate_source_allowlist()` — raises `SourceNotAllowedError` (HTTP 422) on reject (`ingest_core.py:296`)
  4. Size guard: `len(content) > max_ingest_content_chars` → raises `ValueError` (`ingest_core.py:382`)
  5. Source-URL dedup (SELECT by `record_bot_id` + `source_url`): reuses `existing_doc_id` if match found (`ingest_core.py:393-417`)
  6. Content-hash dedup (SHA-256 of full content): raises `DocumentDuplicateError` for fresh exact-duplicate docs (`ingest_core.py:429-454`)
  7. **Document INSERT/UPDATE**: inserts row into `documents` table or updates if `is_reindex` (`ingest_core.py:457-536`)
  8. `_audit.log("ingest_started")` event written (`ingest_core.py:360`)
- **Output (persisted)**: `documents` row with fields: `id`, `record_bot_id`, `record_tenant_id`, `workspace_id`, `source_url`, `document_name`, `tool_name`, `mime_type`, `language`, `state='active'`, `version`, `content_hash` (SHA-256 of full content), `acl`, `metadata_json`, `content_chars`, `raw_content`
- **Key derived**: `doc_id` (new UUID4 or existing), `content_hash` (SHA-256 hex), `is_reindex` bool

### U2 — PARSE (ingest_parse)

- **Code**: `ingest_core.py:305` wrapped by `_phase_d_step(step_tracker, "ingest_parse")`
- **Input**: `raw_bytes` + `mime_type` + `file_name`
- **Logic**: Calls `self._route_through_parser()` → `infrastructure/parser/registry.py::detect_parser()` → first matching parser
- **Parsers registered** (`registry.py:38-45`): `null`, `excel_openpyxl`, `google_sheets`, `pdf`, `docx`, `markdown`
- **Output**: `(extracted_text: str, parser_row_chunks: list[dict] | None)` — row_chunks populated only for `excel_openpyxl` / `google_sheets` (row-shaped)
- **Miss**: Falls through to passthrough (content unchanged)
- **Step metadata**: `parser_provider`, `mime_type`, `n_chars_in`, `n_chars_out`

Also in U1 boundary: PII redaction (`_maybe_redact_ingest_content()` — `ingest_core.py:347`)

### U3 — CLEAN (ingest_clean)

- **Code**: `ingest_stages.py::_StageChunkMixin::_stage_u3_clean()`, `_phase_d_step(step_tracker, "ingest_clean")`
- **Input**: `content` string
- **Operations**:
  1. CleanBase Tier-0 sanitizer (HTML strip + NFC + ZWS remove + prompt-inject blacklist) — gated `cleanbase_tier0_enabled`. Log event: `cleanbase_tier0_scrub` (`ingest_stages.py:261`)
  2. `_clean_document_text(content)` — hyphenation fix, repeated header strip, whitespace collapse (`ingest_stages.py:289`)
  3. Optional LLM metadata extraction (`metadata_extraction_enabled` flag, default OFF)
- **Output**: cleaned `content` + `extracted_metadata` dict stored to `ctx`
- **Step metadata**: `cleaning_enabled`, `cleanbase_tier0_enabled`, `n_chars_in`, `n_chars_out`, `n_chars_stripped`

### U4 — CHUNK (ingest_chunk)

- **Code**: `ingest_stages.py::_stage_u4_chunk()`
- **Input**: `content` (cleaned), `parser_row_chunks`
- **Decision tree**:
  1. `whole_doc_enabled` + `len(content) < whole_doc_threshold_chars` + `not _is_csv_format()` + `topic_signals <= max_topic_signals` → single chunk = whole doc. Log: `whole_document_single_chunk`
  2. `parent_child_enabled` → `generate_parent_child_chunks()` from `shared/chunking/__init__.py`
  3. Else → standard path:
     - Policy resolve: `_resolve_chunking_policy()` → `table_strategy` + optional `force_strategy`
     - Block pipeline gate: `adapchunk_block_pipeline_enabled` (default `True` per `constants/_12_...:185`) — BUT **`parsed_blocks: list = []` is hardcoded empty** (`ingest_stages.py:501`), so `attach_context_buffer` and `analyze_document_blocks` are dead-wired. Both calls are guarded by `if parsed_blocks:` / `if _analyze_blocks is not None and parsed_blocks:` → both fall to legacy `analyze_document(content)` text-flatten path
     - `analyze_document(content)` → `select_strategy(profile, table_strategy=...)` → `(strategy, confidence)` (`shared/chunking/analyze.py:357`)
     - Optional `apply_cross_check()` override (`shared/chunking/analyze.py:527`)
     - `force_strategy` wins over auto-detect if set
     - Row-preserve shortcut: `parser_row_chunks` from `excel_openpyxl` / `google_sheets` bypasses `smart_chunk` entirely, sets `_chunking_strategy = "parser_preserve"` (`ingest_stages.py:672-677`)
     - `smart_chunk(content, chunk_size, chunk_overlap, strategy=_chunking_strategy)` for all other paths
     - `merge_orphan_chunks()` runs unless strategy is `table_csv` / `table_dual_index` / `parser_preserve`
- **Output**: `ctx.chunks` (list of str), `ctx.chunking_strategy`, `ctx.chunking_confidence`, `ctx.is_whole_document`, `ctx.parent_child_enabled`, `ctx.pc_hierarchy`
- **Audit log**: `chunking_strategy_selected` (`ingest_stages.py:791`) — written by `_audit.log()` (NOT structlog)
- **Step metadata** (`ingest_chunk`): `strategy_used`, `n_chunks_out`, `chunk_size_avg`, `language`, `topic_signals`, `orphans_merged_count`, `duration_ms_actual`, `blocks_by_type` (M25 histogram)

### U5 — ENRICH (ingest_enrich)

- **Code**: `ingest_stages_enrich.py::_StageEnrichMixin::_stage_u5_enrich()`
- **Input**: `ctx.chunks`, `content`, `doc_id`
- **Operations**: Contextual Retrieval (Anthropic CR) enrichment — LLM generates per-chunk context prefix. Gated by `contextual_retrieval_enabled` + `plan_limits.cr_enhanced_enabled`
- **Output**: `ctx.enriched_chunks`, `ctx.cr_raw_chunks` (pre-enrichment snapshot), `ctx.chunk_contexts`, `ctx.cr_active`, `ctx.persist_chunks`, `ctx.enriched_persist_enabled`
- **Key**: when CR is active, `persist_chunks[i]` = `"{context_prefix}\n\n{raw_chunk}"`, `cr_raw_chunks[i]` = original chunk text

### U6 — VN_SEGMENT (ingest_vn_segment)

- **Code**: `ingest_stages.py::_stage_u6_vn_segment()`
- **Input**: `ctx.chunks` / `ctx.persist_chunks`, `_vi_seg_lang_eligible`
- **Operations**: Vietnamese compound segmentation via `underthesea` (`shared/vi_tokenizer.py::segment_vi_compounds`). Only runs for VI-language bots (`VI_DOMAIN_LANGUAGES`). U5+U6 may run concurrently via `asyncio.gather` when CR is active.
- **Output**: `ctx.segmented_chunks` (list of str or None — only populated where segmentation changed the text)

### U7 — EMBED + STORE (ingest_embed_store)

- **Code**: `ingest_stages_store.py::_StageStoreMixin::_stage_u7_embed_store()`
- **Input**: all ctx fields from U1-U6
- **Embedding**:
  - Resolves `EmbeddingSpec` (model, dim, provider)
  - Embedding-text strategy: `auto` → `raw_only` for structural strategies (HDT), `prefix_plus_raw` for others
  - Narrate-then-embed (Wave E3): optional `_narrate_service` transforms TABLE/FORMULA/IMAGE chunks before embed
  - Late chunking: optional Jina-style context-aware embedding
  - Fallback: standard `_embed_in_doc_batches()` → `EmbedderPort.embed()`
  - **Incremental re-index**: only embeds changed/new chunks (hash comparison on enriched text)
- **DB write**: `_bulk_insert_chunks()` (`ingest_helpers.py:145`) — single multi-row INSERT
- **Columns written** to `document_chunks`:
  - `id` (UUID)
  - `record_document_id` (FK → `documents.id`)
  - `record_bot_id` (FK → `bots.id`, denormalized, alembic 0108)
  - `chunk_index` (INT, 0-based ordinal)
  - `content` (TEXT — persisted_text = enriched if CR+`enriched_persist_enabled`, else raw)
  - `content_segmented` (TEXT NULL — VN tokenized text if changed, else NULL)
  - `content_hash` (CHAR(64) — SHA-256 of enriched text at embed time)
  - `{embedding_column}` (vector — 1280-dim for zembed-1 default)
  - `metadata_json` (JSONB — see section 2)
  - `parent_chunk_id` (UUID NULL — FK self-referential, parent-child mode only)
  - `chunk_chars` (INT)
  - `chunk_type` (VARCHAR(32) — `text`/`table`/`table_row`/`code`, alembic 010k)
  - `chunk_context` (VARCHAR(1024) NULL — CR situated context, alembic 010l)
- **Post-store**: semantic cache invalidation (`DELETE FROM semantic_cache WHERE record_bot_id=:bid`)
- **Audit log**: `embedding_generated` event

### Finalize

- **Code**: `ingest_stages_final.py::_StageFinalizeMixin::_stage_finalize()`
- **Checks**: COUNT(embedding IS NOT NULL) vs total non-parent chunks → `state='active'` or `state='failed'`
- **Log events**: `document_ingested` (structlog, on success) or `document_ingest_failed` (structlog, on failure)
- **Background**: GraphRAG entity extraction (asyncio task), Stats Index update
- **Audit log**: `ingest_completed` event with `document_id`, `total_chunks`, `chunks_new`, `chunks_unchanged`, `chunks_deleted`, `avg_chunk_len`, `embedded`, `duration_ms`
- **Returns**: `IngestResult(document_id, title, chunks, embedded, chunks_new, chunks_unchanged, chunks_deleted)`

---

## 2. Chunk Identity Lineage (Backward Verification Anchor)

### Table: `document_chunks`

Created: `alembic/versions/20260416_0013_pgvector_chunks.py`  
Column renames: `alembic/versions/20260421_0034_rename_record_prefix.py` (`document_id` → `record_document_id`)  
`record_bot_id` added: `alembic/versions/20260516_0108_chunks_record_bot_id.py`  
`parent_chunk_id` added: `alembic/versions/20260420_0023_parent_child_chunks.py`  
`content_segmented` added: `alembic/versions/20260429_0046_chunks_content_segmented.py`  
`chunk_type` added: `alembic/versions/20260518_010k_chunk_type_metadata.py`  
`chunk_context` added: `alembic/versions/20260520_010l_chunk_context.py`

### Full column list persisted at ingest (from `_bulk_insert_chunks`, `ingest_helpers.py:187-241`):

| Column | Type | Source |
|---|---|---|
| `id` | UUID PK | `_make_chunk_id(persisted_text)` — UUID5 deterministic if `chunk_hash_id_enabled`, else UUIDv7 time-ordered |
| `record_document_id` | UUID FK → documents.id | `doc_id` from `ingest()` |
| `record_bot_id` | UUID FK → bots.id | `record_bot_id` arg (denormalized) |
| `chunk_index` | INT | `chunk_idx` (0-based loop counter over `chunks_to_embed`) |
| `content` | TEXT | `persisted_text` = `persist_chunks[chunk_idx]` (enriched if CR active) or raw `chunk_text` |
| `content_segmented` | TEXT NULL | VN segmentation result if changed, else NULL |
| `content_hash` | CHAR(64) | SHA-256 of **enriched** text (`new_chunk_hashes[i]`) |
| `{embedding_column}` | vector | float array from embedder |
| `metadata_json` | JSONB | see fields below |
| `parent_chunk_id` | UUID NULL | parent row UUID in parent-child mode |
| `chunk_chars` | INT | `len(persisted_text)` |
| `chunk_type` | VARCHAR(32) | `chunk_type_for(persisted_text, is_table_row=...)` |
| `chunk_context` | VARCHAR(1024) NULL | CR context string (`chunk_contexts[chunk_idx]`) |

### `metadata_json` JSONB fields (flat mode — `ingest_stages_store.py:863`):

| Key | Set when | Value |
|---|---|---|
| `chunk_index` | always | int ordinal |
| `total_chunks` | always | int total in document |
| `document_title` | always | str title |
| `enriched_prefix` | always | CR prefix extracted from enriched text (may be empty) |
| `chunking_strategy` | always | e.g. `"recursive"`, `"hdt"`, `"table_csv"`, `"parser_preserve"`, `"whole_document"` |
| `chunking_confidence` | always | float 0.0–1.0 |
| `quality_score` | if quality gate enabled | float |
| `contextual_retrieval` | if CR active | bool True |
| `structural_path` | if strategy=`hdt` | dict `{full, parts}` |
| `extracted_metadata` | if LLM metadata extraction enabled | dict |
| `is_full_document` | if whole_document path | bool True |
| `original_char_count` | if whole_document | int |
| `chunk_strategy` | if whole_document | `"whole_document"` |
| `raw_chunk` | if `enriched_persist_enabled` | pre-enrichment raw text |
| `enriched_prefix_persisted` | if above | bool True |
| `narrated_text` | if narrate service wired | narrated embed target |
| `block_type` | if narrate service wired | `TEXT`/`TABLE`/`FORMULA`/`IMAGE` |
| `article_no`, `chapter_no`, etc. | if structured ref extraction enabled | legal article refs |
| `is_parent_chunk` | parent-child mode parent rows | bool True |
| `is_child_chunk` | parent-child mode child rows | bool True |
| `parent_chunk_index` | parent-child child rows | parent ordinal |
| `chunk_strategy` (parent-child) | parent-child | `"parent_child"` |

### Given `chunk_id` (UUID), backward verification queries:

```sql
-- (a) Exact text
SELECT content, content_segmented, chunk_index, chunk_chars, chunk_type, chunk_context, metadata_json
FROM document_chunks WHERE id = '<chunk_id>';

-- (b) Which document/source
SELECT d.document_name, d.source_url, d.raw_content, d.content_hash, d.mime_type, d.language
FROM document_chunks dc
JOIN documents d ON d.id = dc.record_document_id
WHERE dc.id = '<chunk_id>';

-- (c) Position in document
SELECT chunk_index, metadata_json->>'total_chunks' AS total_chunks
FROM document_chunks WHERE id = '<chunk_id>';

-- (d) Chunking strategy used
SELECT metadata_json->>'chunking_strategy' AS strategy,
       metadata_json->>'chunking_confidence' AS confidence
FROM document_chunks WHERE id = '<chunk_id>';

-- (e) Was CR enriched? Get raw pre-enrichment text:
SELECT metadata_json->>'raw_chunk' AS raw_text,
       metadata_json->>'contextual_retrieval' AS cr_active,
       chunk_context
FROM document_chunks WHERE id = '<chunk_id>';

-- (f) Which request retrieved this chunk
SELECT rcr.record_request_id, rcr.rank, rcr.score
FROM request_chunk_refs rcr
WHERE rcr.record_chunk_id = '<chunk_id>'
ORDER BY rcr.created_at DESC;
```

---

## 3. Chunking Strategies

### Strategies in the registry (from `ingest_stages.py:_stage_u4_chunk` + `shared/chunking/strategies.py`):

| Strategy | Function | Description |
|---|---|---|
| `recursive` | `_chunk_recursive_with_tables()` | Default LangChain recursive + table-atomic protection |
| `hdt` | `_chunk_hdt()` | Heading Document Tree — splits by heading hierarchy, preserves `[Chapter > Section]` path prefix |
| `semantic` | `_chunk_semantic()` | Sentence-level similarity boundaries (lexical Jaccard) |
| `semantic_embed` | `_chunk_semantic_embed()` | Async embedding-based semantic chunking |
| `proposition` | `_chunk_proposition()` | Atomic self-contained statements |
| `hybrid` | `_chunk_hybrid()` | HDT macro + proposition micro |
| `table_csv` | `_chunk_table_csv_with_context()` | CSV/table-aware: 1 row = 1 chunk |
| `table_dual_index` | (from csv_chunker) | Table rows + whole-table group chunks |
| `parser_preserve` | (bypass path) | `excel_openpyxl`/`google_sheets` parser output, no rechunking |
| `whole_document` | (special path) | Entire doc as single chunk for small docs |
| `parent_child` | `generate_parent_child_chunks()` | Small-to-big retrieval hierarchy |

### DEFAULT strategy selection:

1. `table_csv` fast-path: CSV format + no headings + no VN markers → `table_strategy` (default `table_csv`)
2. HDT fast-path: `(total_headings + vn_hierarchical_markers) >= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES` → `hdt`
3. Else: weighted score across `hdt`, `semantic`, `recursive`, `hybrid`, `proposition` → best wins
4. `force_strategy` from per-bot policy overrides all auto-detect
5. `DEFAULT` fallback when confidence < threshold → `recursive`

**Source**: `shared/chunking/analyze.py:select_strategy()` lines 388–492

---

## 4. Block Pipeline — Dead-Wire Status

### Claim: `parsed_blocks` is hardcoded empty → Block pipeline Layer 2/3 is dead-wired

**Evidence** (`ingest_stages.py:501-503`):
```python
parsed_blocks: list = []
if parsed_blocks:
    parsed_blocks = attach_context_buffer(parsed_blocks)
```

The `if parsed_blocks:` branch **never executes** because `parsed_blocks` is always initialized as `[]` in the block-pipeline code path. Similarly at line 516:
```python
if _analyze_blocks is not None and parsed_blocks:
    _doc_profile = _analyze_blocks(parsed_blocks)
else:
    _doc_profile = analyze_document(content)  # always runs
```

**Root cause**: The `blocks` field from the parser IS threaded to `_IngestCtx.blocks` (`ingest_core.py:556`), and the `ingest_block_stream_received` log event fires when `blocks` is non-empty (`ingest_core.py:234`). However, at the U4 stage (`_stage_u4_chunk`), the code creates a fresh `parsed_blocks: list = []` local variable instead of reading `ctx.blocks`. This is documented as intentional: *"Wave B1 will surface a parser-produced `blocks` list on the ingest scope; until then no upstream variable carries it"* — but `ctx.blocks` IS available and holds the parser blocks. The live-wire is `ctx.blocks`; the block-pipeline path reads a hardcoded-empty local.

**Status**: `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED = True` (`constants/_12_...:185`) — flag is ON by default, but the pipeline degrades to `analyze_document(content)` (text-flatten) in every case because `parsed_blocks` is always empty.

**Consequence for backward verification**: Chunking strategy is always derived from text analysis, never from parser-surfaced blocks. The `analyze_document_blocks()` path (which would use richer block-type data from the parser) is unreachable in production.

---

## 5. Existing Logging at Ingest

### Structlog events emitted (all via `logger.*`):

| Event | Level | Location | Key fields |
|---|---|---|---|
| `ingest_block_stream_received` | info | `ingest_core.py:239` | `block_count`, `block_types` |
| `ingest_mime_sniff_corrected` | info | `ingest_core.py:267` | `declared`, `sniffed`, `file_name`, `bytes_len` |
| `ingest_missing_tenant_id` | warn | `ingest_core.py:279` | `bot_id` |
| `ingest_parser_registry_failed` | warn | `ingest_core.py:327` | `mime_type`, `file_name`, `error` |
| `ingest_reusing_existing_document` | info | `ingest_core.py:411` | `record_bot_id`, `source_url`, `document_id` |
| `ingest_duplicate_content_hash` | warn | `ingest_core.py:448` | `record_bot_id`, `content_hash[:16]`, `title` |
| `incremental_indexing` | info | `ingest_core.py:651` | `title`, `total`, `unchanged`, `to_embed`, `stale`, `is_reindex` |
| `cleanbase_tier0_scrub` | info | `ingest_stages.py:261` | `n_chars_in/out`, `html_tags_stripped`, `injection_patterns_matched` |
| `whole_document_single_chunk` | info | `ingest_stages.py:408` | `title`, `char_count`, `threshold` |
| `parent_child_chunking` | info | `ingest_stages.py:443` | `title`, `parents`, `children` |
| `ingest_blocks_by_type` | info | `ingest_stages.py:742` | `blocks_by_type`, `n_blocks_total`, `doc_id`, `record_bot_id` |
| `adapchunk_b2_block_pipeline_override` | info | `ingest_stages.py:543` | `override_reason`, `strategy`, `confidence` |
| `embedding_text_strategy_applied` | info | `ingest_stages_store.py:221` | `strategy`, `n_chunks` |
| `narrate_then_embed_applied` | info | `ingest_stages_store.py:263` | `n_chunks`, `n_meta_populated` |
| `narrate_timeout_fallback_raw_embed` | error | `ingest_stages_store.py:270` | `timeout_s`, `n_chunks`, `document_id` |
| `late_chunking_applied` | info | `ingest_stages_store.py:374` | `title`, `chunks`, `context_chars` |
| `late_chunking_sliding_applied` | info | `ingest_stages_store.py:345` | `title`, `doc_chars`, `chunks` |
| `embedding_failed_aborting_ingest` | error | `ingest_stages_store.py:419` | `doc`, `document_id`, `chunk_count`, `error` |
| `semantic_cache_invalidated` | info | `ingest_stages_store.py:969` | `record_bot_id`, `document_id`, `rows_deleted` |
| `ingest_zero_chunks_persisted_failed` | error | `ingest_stages_final.py:188` | `document_id`, `title` |
| `ingest_partial_embedding_marking_failed` | warn | `ingest_stages_final.py:197` | `document_id`, `chunks_null_leaf` |
| `document_ingested` | info | `ingest_stages_final.py:252` | `title`, `chunks`, `bot_id`, `chunks_new`, `chunks_unchanged`, `chunks_deleted` |
| `document_ingest_failed` | error | `ingest_stages_final.py:261` | `title`, `document_id`, `final_state`, `flip_committed` |

### Audit logger events (written to `request_logs`/audit trail via `self._audit.log()`):

| Event | Location |
|---|---|
| `ingest_started` | `ingest_core.py:360` — `title`, `source_url`, `source_type`, `mime_type`, `language`, `raw_len`, `channel_type`, `is_reindex` |
| `chunk_created` | `ingest_core.py:598` (per-chunk loop) — `chunk_index`, `len_chars`, `approx_tokens`, `preview_head`, `preview_tail`, `content_hash[:16]` |
| `chunking_strategy_selected` | `ingest_stages.py:791` — `strategy`, `why`, `total_raw_chars`, `n_chunks`, `topic_signals`, `orphans_merged_count` |
| `embedding_generated` | `ingest_stages_store.py:484` — `model`, `provider`, `dim`, `n_embedded`, `late_chunking`, `late_chunking_sliding` |
| `ingest_completed` | `ingest_stages_final.py:351` — `document_id`, `title`, `total_chunks`, `chunks_new/unchanged/deleted`, `avg_chunk_len`, `embedded`, `duration_ms`, `is_reindex` |

### `request_steps` rows (via `_phase_d_step(step_tracker, ...)` in `ingest_phases.py:_phase_d_step`):

Step names: `ingest_validate`, `ingest_parse`, `ingest_clean`, `ingest_chunk`, `ingest_enrich`, `ingest_vn_segment`, `ingest_embed_store`  
Each row records: step name, `step_kind="ingest"`, duration_ms, plus stage-specific metadata from `set_metadata()` calls.  
**Condition**: these rows only write if a `step_tracker` is injected by the caller. Worker path must wire it.

### Per-upload debug artifact:

**NOT FOUND.** There is no existing code that dumps per-upload debug info to a file. The audit logger writes to DB; structlog events go to the log stream. There is no `open()` / `write()` / `/tmp/` pattern in the ingest path.

---

## 6. Gap Analysis for Per-Run Debug Trace File

A per-upload debug file capturing `"doc X → N chunks, each chunk's text + position + strategy + embedding-dim"` would need the following:

### Fields available NOW (no code change):

| Field | Source at ingest |
|---|---|
| `document_id` | `ctx.doc_id` |
| `title` | `ctx.title` |
| `source_url` | `ctx.source_url` |
| `mime_type` | `ctx.mime_type` |
| `chunking_strategy` | `ctx.chunking_strategy` |
| `chunking_confidence` | `ctx.chunking_confidence` |
| `is_whole_document` | `ctx.is_whole_document` |
| `total_chunks` | `len(ctx.chunks)` |
| For each chunk: index | `chunk_idx` |
| For each chunk: raw text | `ctx.chunks[i]` |
| For each chunk: enriched text | `ctx.enriched_chunks[i]` |
| For each chunk: CR context | `ctx.chunk_contexts[i]` |
| For each chunk: chunk_type | `chunk_type_for(persisted_text)` |
| For each chunk: content_hash | `ctx.new_chunk_hashes[i]` |
| For each chunk: chunk_id | `row["id"]` (available in `ctx.rows` after U7) |
| For each chunk: embedding dim | `len(embed_results[local_idx])` |
| Structural path | `extract_structural_path(chunk_text)` for HDT chunks |

### Fields NOT captured now (would need additions):

| Field | Gap | Effort |
|---|---|---|
| Char offset of each chunk in original document | Not tracked at ingest; only ordinal `chunk_index` is stored | Would need to track `content.find(chunk_text)` before enrichment |
| Page number / page_idx | Parsers don't surface page count (`n_pages=None` in step metadata) | Requires parser protocol change |
| Which embedding model / dim per chunk | Available as `spec.model_name` / `spec.dimension` in `ctx.spec` after U7, but not written per-chunk in `metadata_json` (only in audit event) | Low effort — add to metadata |
| Token count per chunk | Only approximated via `len(txt) // 4`; actual tiktoken count not captured | Moderate — lazy tiktoken call |
| Block modality histogram per chunk | `blocks_by_type` is document-level (`ingest_stages.py:742`), not per-chunk | Would need per-chunk block analysis |

### Minimal hook point for debug trace:

After `_stage_u7_embed_store()` returns (`ingest_core.py:701`), `ctx.rows` contains all inserted rows with `id`, `idx`, `content`, `hash`, `chunk_type`, `chunk_context`. Combined with `ctx.chunks`, `ctx.enriched_chunks`, `ctx.chunking_strategy`, and `ctx.spec`, a complete per-upload trace can be assembled and written to `/tmp/ingest_debug_{doc_id}.json` with no upstream changes needed. The hook site is:

```python
# ingest_core.py line ~701, after _stage_u7_embed_store(ctx)
await self._stage_u7_embed_store(ctx)
# [INSERT DEBUG DUMP HERE if flag enabled]
return await self._stage_finalize(ctx)
```

A single `system_config` flag `ingest_debug_trace_enabled` (default OFF) would gate file writes without touching the hot path.

---

## 7. Backward-Verify Anchor

### Primary anchor key: `document_chunks.id` (UUID)

This is the stable PK of every stored chunk. It is:
- Unique and immutable (never changes after insert)
- Propagated to `request_chunk_refs.record_chunk_id` (alembic 0109) — the relational join table linking query requests to retrieved chunks
- Returned in query pipeline's chunk dicts as `chunk_id` field (`retrieve.py:348`, `query_graph.py:2067`)
- Deterministic (UUID5 = same content → same UUID) when `chunk_hash_id_enabled` is ON; otherwise time-ordered UUIDv7

### The backward-verify join chain:

```
wrong answer
    ↓ query_request_id (from request_logs)
request_chunk_refs WHERE record_request_id = <request_id>
    → record_chunk_id (UUIDs of chunks that were retrieved)
    ↓
document_chunks WHERE id = <record_chunk_id>
    → content (what text was shown to LLM)
    → metadata_json->>'chunking_strategy' (how it was split)
    → metadata_json->>'chunk_index', total_chunks (position)
    → metadata_json->>'raw_chunk' (pre-CR text, if persisted)
    → chunk_context (CR situated context, if populated)
    ↓ record_document_id
documents WHERE id = <record_document_id>
    → raw_content (full original document text)
    → source_url, document_name (provenance)
    → content_hash (SHA-256 fingerprint of what was ingested)
```

### What can be recovered given `chunk_id`:
- **(a) Exact text**: `document_chunks.content` — enriched/persisted text (what BM25 + dense search scored)
- **(b) Pre-enrichment raw text**: `document_chunks.metadata_json->>'raw_chunk'` (if `enriched_persist_enabled=True`) or `document_chunks.content` if CR was not active
- **(c) Source document**: `documents.document_name`, `documents.source_url`, `documents.raw_content` via `record_document_id` FK
- **(d) Position**: `document_chunks.chunk_index` + `metadata_json->>'total_chunks'`
- **(e) Chunking strategy**: `document_chunks.metadata_json->>'chunking_strategy'` + `->>'chunking_confidence'`
- **(f) How it was embedded**: `document_chunks.metadata_json->>'contextual_retrieval'` + `chunk_context` column
- **(g) Which requests retrieved it**: `request_chunk_refs WHERE record_chunk_id = <id>` → `record_request_id`

### Secondary anchor for content dedup: `document_chunks.content_hash`

SHA-256 of the **enriched** text used for embedding. Same chunk text → same hash → idempotent UPSERT in re-index path. Used for incremental re-index skip logic (`ingest_core.py:641`).

---

## 8. Summary Table

| Item | Finding |
|---|---|
| Ingest entry point | `ingest_core.py::_IngestMixin.ingest()` — stages call `_stage_u3_clean → _stage_u4_chunk → _stage_u5_enrich → _stage_u6_vn_segment → _stage_u7_embed_store → _stage_finalize` |
| Pipeline step names | `ingest_validate`, `ingest_parse`, `ingest_clean`, `ingest_chunk`, `ingest_enrich`, `ingest_vn_segment`, `ingest_embed_store` |
| Chunk table | `document_chunks` — PK `id` UUID, FK `record_document_id`, FK `record_bot_id`, `chunk_index`, `content`, `content_hash`, `embedding`, `metadata_json`, `chunk_type`, `chunk_context` |
| Strategies available | `recursive`, `hdt`, `semantic`, `semantic_embed`, `proposition`, `hybrid`, `table_csv`, `table_dual_index`, `parser_preserve`, `whole_document`, `parent_child` |
| Default strategy selection | `recursive` fallback; `table_csv` for CSV docs; `hdt` for VN legal docs with ≥ N hierarchy markers; scored selection otherwise |
| Block pipeline dead-wire | **CONFIRMED**: `parsed_blocks: list = []` at `ingest_stages.py:501` — always empty → `analyze_document_blocks()` and `attach_context_buffer()` never called; production always uses `analyze_document(content)` text-flatten |
| Existing per-upload debug artifact | **NOT FOUND** — no per-run file dump; only structlog stream events + audit logger DB writes |
| Backward-verify anchor | `document_chunks.id` (UUID) ← `request_chunk_refs.record_chunk_id` ← `request_logs.request_id` |
| Gap to enable debug trace | Hook at `ingest_core.py:701` after `_stage_u7_embed_store`; serialize `ctx.rows` + `ctx.chunks` + `ctx.enriched_chunks` + `ctx.chunking_strategy` + `ctx.spec` to JSON file; gate with `ingest_debug_trace_enabled` system_config flag |
