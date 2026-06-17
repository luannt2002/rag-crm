# P1-B — CHUNKING & ADAPCHUNK EXPERT · Phase 1 context report

> Agent: P1-B (document understanding & chunking) · Date: 2026-06-10
> Mode: READ + REPORT ONLY. Every claim carries `file:line` or commit-hash evidence.
> Anchor: branch `fix-260604-action-slotmachine-dead-key`, HEAD `7dd1f84`.

---

## (a) Domain map — ingest U0–U7 + strategy inventory

### U0–U7 flow (file:line verified)

| Step | What | Where (evidence) |
|---|---|---|
| **U0 consume** | Redis Stream → worker; fetch source_url; registry parser first, OCR fallback | `src/ragbot/interfaces/workers/document_worker.py:228-298` |
| **U1 validate** | tenant guard + sanity | `src/ragbot/application/services/document_service.py:1405-1434` (`_phase_d_step "ingest_validate"` :1407) |
| **U2 parse** | DocumentParserPort registry routing (excel/sheets/docx/md/pdf) | `document_service.py:1435-1474`; `_route_through_parser` :1246-1294 |
| **U2b OCR** | Kreuzberg engine (default) → `list[Block]` w/ `is_atomic` + heading `context_before` | `infrastructure/ocr/kreuzberg_parser.py:157-309`; factory `ocr_factory.py:48`; default engine `shared/constants/_13_adapchunk_layer_1_ocr_parser.py:11` (`DEFAULT_PARSER_ENGINE="kreuzberg"`, flipped from "simple" per Wave C2, :12-14) |
| **U3 clean** | CleanBase Tier-0 + legacy cleaner | `document_service.py:1668-1679` |
| **U4 chunk** | strategy select + smart_chunk + orphan merge | `document_service.py:1780-2187` (detail below) |
| **U5 enrich** | Anthropic Contextual Retrieval (ChunkContextEnricher, LLM per-chunk context) | `document_service.py:2552-2616`; enricher built in `document_worker.py:365-371`; flag `DEFAULT_CR_ENHANCED_ENABLED=True` `constants/_11_table_csv_chunking_strategy.py:68` |
| **U6 vn_segment** | VN compound segmentation, ∥ with U5 via gather | `document_service.py:2617-2640` (U5∥U6 gather :2215, :2359) |
| **U7 embed+store** | embedding-text strategy → narrate-then-embed → passage prefix → bulk INSERT | `document_service.py:2801-3604` (embed-text strategy :2829-2846; narrate :2891-2907; passage prefix :2912-2917; batch insert helper :446) |

### CRITICAL structural fact — Block stream is flattened TWICE before chunking

1. Worker OCR path: `full_text = "\n\n".join(b.content for b in parsed.blocks)` — **`document_worker.py:295`**. The Kreuzberg `Block` objects (with `is_atomic`, `context_before/after`) are destroyed here; `DocumentService.ingest()` receives a plain string.
2. Registry parser path: `joined = "\n\n".join(c["content"] for c in chunks ...)` — **`document_service.py:1293`** (row-chunks survive only via the `parser_preserve` side-channel :2078-2089, restricted to `{"excel_openpyxl","google_sheets"}`).

→ Consequence: anything downstream that wants `list[Block]` (AdapChunk Layer 2/6) has no input. See dead-code hunt in (b).

### U4 chunking branch detail (`document_service.py`)

- whole_document fast-path + parent_child branch: :1840-1865 (`generate_parent_child_chunks` defined `chunking.py:2256`).
- **Wave B2 "Block pipeline" gate** `adapchunk_block_pipeline_enabled` (:1898-1969): when ON runs `promote_vn_hierarchical_headings` → `analyze_document` → `select_strategy` → `apply_cross_check`. BUT `parsed_blocks: list = []` hard-coded empty at **:1921** ("Wave B1 will surface a parser-produced blocks list … until then no upstream variable carries it") — so `attach_context_buffer` (:1923) and `analyze_document_blocks` (:1936-1937) never execute. The "Block pipeline" is in practice the same text-flatten path + an extra `apply_cross_check` call.
- AdapChunk L3 DocumentProfile entity gate `adapchunk_layer3_doc_profile_enabled` (:1999-2063) — telemetry-only; comment :1994-1996: "the entity is NOT yet wired into select_strategy".
- `parser_preserve` row bypass :2078-2096; `smart_chunk()` call :2091-2096; orphan-merge (skipped for `table_csv`/`parser_preserve`) :2110-2123.
- U4 audit row + block-type histogram (M25) :2131-2180.

### Strategy inventory — wired vs dead (all in `src/ragbot/shared/chunking.py`)

| Strategy | Defined | Reachable in production? | Evidence |
|---|---|---|---|
| `table_csv` (row-as-chunk + header) | `_chunk_table_csv` :1268, `_chunk_table_csv_with_context` :1456, multi-region `_detect_csv_regions_all` :1368 | **LIVE** — selector fast-path :724-725; header/footer chunks flag ON in DB (`system_config.table_csv_emit_header_footer_chunks_enabled=true`, psql verified 2026-06-10) | commit ab79b81 (multi-table) |
| `recursive` (table-aware) | `_chunk_recursive_with_tables` :1580 | **LIVE** — default/fallback :789-790, :2637 | |
| `hdt` (Heading Document Tree) | `_chunk_hdt` :1793 | **LIVE** — VN legal fast-path :732-733 (`("hdt", 1.0)` when markers ≥ threshold) | de8d145 VN promote |
| `semantic` (lexical sentence-similarity) | `_chunk_semantic` :1917 | **LIVE** via weighted selector :749-756 | 53defa7 |
| `semantic_embed` (true embedding-based, async) | `_chunk_semantic_embed` :1988 | **DEAD** — zero production callers (grep: only tests `tests/unit/test_embedding_semantic_chunk.py`); `DEFAULT_EMBEDDING_SEMANTIC_CHUNK_ENABLED=False` `constants/_06_llm_defaults.py:110`; `smart_chunk` is sync → cannot dispatch the async strategy | 2811be9 built Port+Registry+Adapters, never wired |
| `proposition` | `_chunk_proposition` :2132 | **LIVE** via selector :776-783 | bc39e07 |
| `hybrid` (HDT macro + PROP micro) | `_chunk_hybrid` :2197 | **LIVE** via selector :767-774 | bc39e07 |
| `parent_child` | `generate_parent_child_chunks` :2256 | **LIVE**, per-bot opt-in (`document_service.py:1840-1865`) | |
| `whole_document` | document_service branch | **LIVE** (threshold-guarded post f3073cc) | |
| `parser_preserve` | document_service :2084-2089 | **LIVE** for excel/sheets only | |
| `legal_hybrid` selector branch | — | **REMOVED** 2026-06-09 | e86c0f6 revert (see (b)) |

Selector: `select_strategy` :663-792 (weighted scores + 2 fast-paths); `analyze_document` :508; `analyze_document_blocks` :602 (Block overload — only caller is the dead B2 branch + tests); L5 `apply_cross_check` :827.

---

## (b) AdapChunk spec-layer × code-state × git-history table

| AdapChunk layer (spec) | Code current state | Verdict | Git history |
|---|---|---|---|
| **L1 — OCR/parse (structure-aware)** | Kreuzberg default engine emits typed `Block` w/ `is_atomic` + heading context (`kreuzberg_parser.py:157-309`); fallback chain kreuzberg→docling→simple (`_13:5-17`). BUT worker flattens blocks to string (`document_worker.py:295`) | **LIVE at parser, LOST at boundary** | Wave C2 flipped default simple→kreuzberg (`_13:12-14`); a9eb301/7021dfe merges 2026-05-14 |
| **L2 — atomic-block protect + context-binding (§2.4)** | Two implementations: (i) `_smart_chunk_with_atomic_protect` `chunking.py:2425` behind flag `formula_image_atomic_protect_enabled` — **default OFF** (`DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False`, `constants/_00_app_env_taxonomy.py:95`), no `system_config` row (psql verified), no alembic seed (grep alembic = 0) → **OFF in production**; (ii) `attach_context_buffer` (`shared/context_buffer.py:110`) — called by docling/simple parsers (`docling_parser.py:73`, `simple_text_parser.py:121`) and by the dead B2 branch (`document_service.py:1923`, input always `[]`). Legacy partial: TABLE-only isolation in `smart_chunk` :2613-2627 (live); FORMULA/IMAGE/CODE protection dead | **BUILT, NOT ENABLED** (formula/image/code); TABLE-only legacy live | 62a1a05 (2026-05-13) shipped flag default OFF, ship-dark pattern: "Adds feature flag (default OFF)… flag-off regression (legacy text/table emission unchanged)". Never flipped since |
| **L3 — rule-based document profile** | dict profile `analyze_document` :508 LIVE; full 10-feature `DocumentProfile` entity via `build_doc_profile_analyzer` (`infrastructure/doc_profile/`) behind `adapchunk_layer3_doc_profile_enabled` — default OFF (`_18:73`), telemetry-only, "NOT yet wired into select_strategy" (`document_service.py:1994-1996`) | **dict LIVE / entity DEAD-ish (log-only)** | d673cac Wave D1 (2026-05-14) |
| **L4 — strategy selection** | Weighted scorer + 2 fast-paths LIVE (`select_strategy` :663-792). Ekimetrics 5-metric selector (RC/ICC/DCC/BI/SC, `shared/intrinsic_metrics.py`) exists as opt-in param `ekimetrics_enabled` :667 — **zero callers pass it** (grep: only the def); no config-read path exists for `ekimetrics_5metric_selector_enabled` | **weighted LIVE / Ekimetrics DEAD** | 108a8f2 (2026-05-13); Databricks complexity detector 76c3dca |
| **L5 — rule cross-check** | `apply_cross_check` :827, gated `adapchunk_layer5_cross_check_enabled` — constant `True` (`_12:140`) AND `system_config` row `true` (psql verified) → runs inside `smart_chunk` :2556-2576 and again in B2 branch :1954-1960 | **LIVE** | a6ff98a merge of 91cdfb4-lineage (2026-05-14); default flipped ON later (`_12:139-140` shows old `False` commented) |
| **L6 — chunk signature `list[Block]→list[Chunk]` (narrate-aware atomic chunking)** | `smart_chunk_atomic` `chunking.py:2737-2894` + `_chunk_text_blocks_to_chunks` :2897 + `_block_to_atomic_chunk` :2964, preserves `original_content` + `context_before/after` on `Chunk` entity | **DEAD — zero production callers** (grep src = only definition + docstrings); the B2 gate that was supposed to call it never does (`document_service.py:1921` empty list) | 67b9883 Wave B1 (2026-05-14) built it; e7d4f41 Wave B2 (2026-05-14) added the gate WITHOUT the call; flag default flipped ON (`_12:176` `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True`) claiming "deps landed" — claim is wrong w.r.t. blocks (see (d)) |
| **Narrate-then-embed keeping original_content (§spec "Tầng 6/narrator")** | `NarrateService` (`application/services/narrate_service.py`), dispatch `narrate_dispatch.py:105-156`, providers `infrastructure/narrate/{llm,null}_narrate.py`. Eligible types `("TABLE","FORMULA","IMAGE")` (`_18:177`); TEXT passthrough. Embed-target swapped (`document_service.py:2891-2902`), `content` column NOT overwritten; raw_chunk+narrated_text+block_type persisted to metadata_json (:3417-3435). Defaults ON: `DEFAULT_NARRATE_THEN_EMBED_ENABLED=True` (`_20:80`), provider "llm" (`_20:57`), wired in `document_worker.py:315-354` | **LIVE** (caveat: classifies block-type post-hoc from flattened text via `classify_chunk_block_type`, `narrate_dispatch.py:68-102` — not from real parser blocks) | 1bb87ec (2026-05-13) port; e0c74c0 E3 wire (2026-05-14); 2bab6cc default ON + vi prompts (2026-05-30) |
| **§8 — eval by question-type** | Prod-test framework defines L1 factoid / L2 conditional-factoid / L4 multi-hop + category (aggregation…) (`plans/260609-prod-test-framework/FRAMEWORK.md:22-25,117`); graded per-bot reports `reports/GRADED_*.json` (13 bots, working tree). No automated per-question-type × per-chunk-strategy A/B harness in scripts/ | **PARTIAL** (manual SOP, not chunking-feedback loop) | plans 260609-prod-test-framework |

### Dead-code hunt — the 3 named symbols, precise

| Symbol | DEFINED | WIRED into ingest()? | Missing wiring |
|---|---|---|---|
| `_smart_chunk_with_atomic_protect` | `chunking.py:2425` | **YES code-path** (`smart_chunk` :2586-2594) but **gate always False in prod**: `_atomic_protect_enabled()` :1239 reads `formula_image_atomic_protect_enabled` → no DB row + constant `False` (`_00_app_env_taxonomy.py:95`) | Flip flag (alembic seed or constant) + load-test A/B. Zero code change needed |
| `smart_chunk_atomic` | `chunking.py:2737` | **NO — never called anywhere in src/** (grep = definition + comments only) | (1) worker must stop flattening at `document_worker.py:295` and pass `parsed.blocks` through; (2) `ingest()` needs a `blocks` parameter; (3) B2 branch `document_service.py:1921` must receive real blocks and call `smart_chunk_atomic` instead of falling through to text `smart_chunk` :2091; (4) downstream U5/U6/U7 consume `list[str]` — need `Chunk`-entity adaptation |
| `_narrate_service` | injected attr `document_service.py:800,859`; built `document_worker.py:322-354` | **YES — LIVE** (`document_service.py:2891-2902`), default ON since 2bab6cc | none (wired). Residual gap: block-type classification is heuristic-on-flattened-text, not parser truth |

### atomic_protect default-OFF commit + WHY

`git log -S atomic_protect` → **62a1a05** (2026-05-13, "Atomic FORMULA / IMAGE Chunks…"). Commit message states the WHY explicitly: shipped as opt-in feature flag with "flag-off regression (legacy text/table emission unchanged)" tests — i.e. deliberate ship-dark to guarantee zero behavior change at merge time (one of 24 parallel mom-260514 streams; merging dark avoided cross-stream breakage). **No follow-up commit or alembic ever flipped it ON** (grep `formula_image` in alembic/versions = 0 hits; psql system_config = no row). The companion flag `adapchunk_block_pipeline_enabled` DID get flipped to default ON (`_12:170-176`, "Default ON: the deps … have landed") — but that flip is cosmetic because of the empty `parsed_blocks` (see L6 row).

---

## (c) Plans done / doing / not-done re chunking

| Plan | Chunking-relevant content | Status (evidence) |
|---|---|---|
| `plans/260514-adapchunk-reorg-migration` (Waves B1/B2/D1/E3, 7-layer reorg) | origin of smart_chunk_atomic, B2 gate, DocumentProfile, narrate wire | **Removed from plans/** in cleanup 15c2a8d (survives in `.claude/worktrees/*/plans/`); report `ef7c466` "AdapChunk reorg migration report 20260514". Code: B1 ✅ built / B2 gate ✅ but block feed ❌ / D1 ✅ built not wired to selector / E3 ✅ live |
| `plans/260604-deepaudit-rootcause-fix/plan.md` | :25 spa-07 CSV conflate → structure-aware 1-row/chunk (arXiv 2605.00318); :52 F5 CSV bypass orphan-merge | **DONE** — `document_service.py:2110-2123` skips orphan-merge for table_csv/parser_preserve |
| `plans/260605-rag-full-fix-master/plan.md` | :34 F1 CR-prompt "COPY verbatim số hiệu" rule + re-ingest; :39-40 F3 per-bot `chunk_strategy=proposition` for legal | F1 shipped in `contextual_chunk_enrichment.py` lineage; F3 = config-only (per-bot), no dedicated code |
| `plans/260608-rag-quality-rootcause/plan.md` | :46-53 — HDT fast-path short-circuits legal docs → legal-hybrid selector branch, flag `adapchunk_legal_hybrid_enabled` default OFF; warns hybrid micro-chunk ↑chunk-count → p95 (:146) | **DONE → REVERTED**: 14ec96d (2026-06-08) shipped, edb9fa0 calibration (2026-06-09), **e86c0f6 (2026-06-09) full removal** — clean A/B showed no real Coverage lift (0.60/0.72/0.78 within noise) + reproducible HALLU breach Q1+Q5 + domain-named feature violates domain-neutral. alembic 0190 (flag off) + 0191 (drop keys) |
| `plans/260609-prod-test-framework/` | L1–L4 question taxonomy + grading SOP (FRAMEWORK.md:22-25) | **DOING** — 13 GRADED_* reports modified in working tree; not yet an automated chunking-eval loop |
| `plans/260610-ga-hardening/` | RLS P0 + retrieval determinism (dcaf504) | not chunking-core; pending |
| NOT-DONE (no plan exists) | wire `smart_chunk_atomic` end-to-end; flip/decide `formula_image_atomic_protect_enabled`; wire Ekimetrics selector or delete; wire/delete `_chunk_semantic_embed`; wire DocumentProfile entity into selector | — charter D14–D17 "AdapChunk engine fixes" reserved for Phase 3 decisions (`program/00-charter.md` Phạm vi mở rộng) |

---

## (d) vs AdapChunk spec + SOTA chunking 2026 — HAS / LACKS (objective, no judgment)

### HAS (verified live)
1. Rule-based structure classification + weighted strategy selection + 2 deterministic fast-paths (`chunking.py:663-792`).
2. L5 rule cross-check post-selector, flag ON in prod DB (`apply_cross_check` :827; psql `adapchunk_layer5_cross_check_enabled=true`).
3. Row-atomic CSV/table chunking incl. multi-table region detection + header/footer synthetic chunks (ON in DB) (`:1268-1556`).
4. TABLE block isolation in mixed docs (legacy path `:2613-2627`).
5. VN legal hierarchy: Roman↔Arabic normalization :311-418, heading promotion :467, HDT fast-path :732.
6. Narrate-then-embed for TABLE/FORMULA/IMAGE with dual-content persistence (original `content` + metadata raw/narrated) — LIVE default ON (`document_service.py:2891-2902`, 2bab6cc).
7. Anthropic Contextual Retrieval per-chunk context (U5) + embedding-text strategy incl. structural `raw_only` auto mode (`:2829-2846`).
8. Parent-child hierarchy, orphan-merge with row-atomic exemption, parser_preserve row semantics.
9. Kreuzberg layout-aware parse to typed Blocks with `is_atomic` + heading context (parser layer).
10. Per-step observability: U1–U7 `request_steps` rows + block-type histogram M25 (`:2131-2180`).

### LACKS (vs spec / SOTA 2026)
1. **Block stream does not survive the parser→chunker boundary** — flattened at `document_worker.py:295` and `document_service.py:1293`; spec L6 signature `list[Block]→list[Chunk]` is implemented (`smart_chunk_atomic`) but unreachable.
2. **FORMULA/IMAGE/CODE atomic protection OFF in production** (flag default False, no seed) — only TABLE protected via legacy path.
3. **Context-binding §2.4** (`attach_context_buffer`) executes only inside docling/simple parsers; never on the ingest path actually used (kreuzberg output flattened; B2 input `[]`).
4. **DocumentProfile 10-feature entity not feeding the selector** (telemetry-only, `document_service.py:1994-1996`).
5. **Ekimetrics 5-metric selector dead** (no caller, no config key read).
6. **True embedding-based semantic chunking dead** (`_chunk_semantic_embed` async, sync `smart_chunk` can't dispatch; flag default OFF).
7. **No eval-by-question-type × chunking-strategy feedback loop** (§8): grading SOP exists, but no automated harness correlating chunk strategy ↔ per-question-type pass rate.
8. **The B2 flag default-ON claim ("deps landed") is inconsistent with the code** — `_12:172-176` vs `document_service.py:1921`; the test `tests/unit/test_adapchunk_b2_block_pipeline.py:39-46` pins the flag value, not the block feed.
9. No late-chunking / multi-vector (ColBERT-style) or layout-PDF table-structure recovery beyond Kreuzberg element types (SOTA 2025-26 directions; engine-swap territory per charter).
10. Wrong-attribution risk on messy owner uploads documented (`docs/master/14-N:38-77`) — addressed via enrichment model upgrade, not via structure-aware chunk-level attribution checks (spec L5 cross-check is strategy-level only, not fact-level).

---

## (e) 10 open questions for Phase 2

1. **Q-block-feed**: Wire `smart_chunk_atomic` end-to-end (worker passes `parsed.blocks`, ingest gains `blocks=` param) — or delete it and extend `_smart_chunk_with_atomic_protect`'s text-regex partition instead? Both implement L2/L6; carrying two is drift risk (they already disagree: regex `_split_into_blocks_with_atomic` :1135 vs parser truth `Block.is_atomic`).
2. **Q-atomic-flag**: Flip `formula_image_atomic_protect_enabled` ON? Need a corpus measurement: how many production chunks today actually contain formula/image/code blocks (M25 histogram in `request_steps` can answer without re-ingest).
3. **Q-B2-honesty**: `adapchunk_block_pipeline_enabled=True` runs `apply_cross_check` a second time at :1954 AND again inside `smart_chunk` :2556 (L5 flag also ON) — is double cross-check idempotent for all 5 rules, or can it double-override?
4. **Q-ekimetrics**: Wire the Ekimetrics selector behind a real config key, A/B vs weighted scorer (claimed +5-8pp Answer Correctness, `chunking.py:812-816`), or remove ~600 lines (`intrinsic_metrics.py` + `_19` constants)?
5. **Q-semantic-embed**: Same decision for `_chunk_semantic_embed` — wiring requires making the U4 chunk dispatch async-aware; is the lexical `_chunk_semantic` measurably worse on VN prose?
6. **Q-profile-entity**: Should `DocumentProfile` entity replace the dict profile as `select_strategy` input (one schema instead of dict+entity dual), and is `analyze_document_blocks` :602 kept?
7. **Q-narrate-truth**: Narrate classifies block type from flattened text (`narrate_dispatch.py:94`) — once real blocks flow, should classification come from `Block.type`? What % of TABLE chunks are currently mis-classified TEXT (measurable from metadata_json `block_type` vs M25 histogram)?
8. **Q-legal-lesson**: e86c0f6 proved domain-named selector branches fail. The underlying multi-fact-recall problem moved to `structured_subanswer_enabled` (generation layer). Is there a domain-NEUTRAL chunking lever left (e.g. proposition micro-chunks gated by density, not by "legal"), or is chunking done here?
9. **Q-eval-loop**: Build the §8 harness — per-question-type (L1/L2/L4 × category) pass-rate keyed by `chunking_strategy` from `request_steps`/audit (`chunking_strategy_selected` event :2200) so strategy changes get measured automatically?
10. **Q-CR-cost-interaction**: U5 CR (~1 LLM call/chunk) + narrate (LLM for TABLE/FORMULA/IMAGE) + decomposer enrichment all hit ingest cost; proposition/hybrid micro-chunking multiplies chunk count. What is the cost/quality frontier per strategy (charter RẺ axis: cost per-tenant measurable)?

---
*P1-B Phase 1 complete. No src/alembic/tests modified. Single file written: this one.*
