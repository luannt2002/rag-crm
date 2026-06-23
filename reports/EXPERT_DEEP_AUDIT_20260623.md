# Ragbot — Expert Deep Audit (ALL flows) · 2026-06-23 · FULL DETAIL

> **No summarization** — this file contains the complete verbatim findings of every audit agent.
> **Method**: read-only specialist agents, one per flow. Each agent re-read the real code and
> **self-verified every CRIT/HIGH finding at file:line** before reporting (false-positive guard, CLAUDE.md rule#0).
> **Nothing in code was changed** — diagnosis only. Every fix is a proposal at the correct layer with an A/B metric.

## Agent roster (who did what)

~13 agents spawned; 10 produced flow reports (3 workflow readers harvested before the workflow was stopped to
raise effective concurrency, + 7 independent agents). Full detail per agent in the sections below.

| Section | Agent task | Grade |
|---|---|---|
| A | Ingest / Upload (multi-format) — full-detail agent | HAS_GAPS |
| B | Chunking / AdapChunk L1–L7 — full-detail agent | C+ (6.0) |
| C | Answer / Generation (sacred-10) — full-detail agent | A− (9.2) |
| D | Chat-entry (prod B2B) + Test-chat harness — 1 agent, 2 scopes | OK_MINOR / HAS_GAPS |
| E | Retrieval (hybrid/RRF/rerank/CRAG) | OK_MINOR |
| F | Multi-tenant / Identity / RLS isolation | B+ (7.8) |
| G | Cost-Log Center (token_ledger / RAG+CRM reporting) | C+ (6.0) |
| H | Domain-neutral / Zero-hardcode / Per-bot sweep | 8.5 STRONG |
| I | Multi-language support | B (7.5) |
| J | Cost / Performance / Latency | B− |

---
---

# SECTION A — INGEST / UPLOAD FLOW (independent full-detail agent) — grade HAS_GAPS

**Summary (verbatim):** The canonical path is architecturally sound and the disabled orphan endpoint was correctly
neutralized, but the **async worker path silently diverges from the sync path's robust type-detection** (no
`detect_parser_robust`, no `sniff_real_mime`), legacy binary Office formats (`.doc`/`.xls`) are unsupported with no
graceful handling, and the default `late_chunking` embed path sends the entire chunk list in one in-memory call.

**Canonical path (Q1) — mostly CLEAN.** `POST /api/ragbot/documents/create` (`documents.py:91`) is the one canonical
ingest route: resolves 4-key identity, enforces idempotency (`X-Idempotency-Key`, lines 117-161) + ingest quota (line
167), then `IngestDocumentUseCase.execute` writes a DRAFT `Document`, a `jobs` row, and a `DocumentUploaded` outbox
event in one UoW (`ingest_document.py:90-143`). The outbox publisher drains to Redis Stream `document.uploaded.v1`;
`document_worker.py:672` subscribes and does the heavy parse/chunk/embed. Correct 202 + outbox → worker drain. The
parallel streaming route (`documents_stream_upload.py`) is **correctly disabled**: not included in `router.py:64`
(commented out 2026-06-19), and its target stream `document.upload_stream.v1` has **no consumer**. `/sync/documents`
(`sync.py:417`) is a separate **already-parsed-text** bulk path (NestJS upstream sends `content` strings), legitimately
distinct.

**Type detection (Q2) — ASYMMETRIC, the core gap.** Two code paths with different robustness:
- **Sync `DocumentService.ingest()`** (when `raw_bytes` provided): robust. `ingest_core.py:262-273` calls
  `sniff_real_mime(raw_bytes,...)` (magic-byte: `%PDF-`, `PK\x03\x04`+zip-manifest peek, UTF-8/CSV heuristic), then
  `_route_through_parser` falls back to `detect_parser_robust` (`__init__.py:737`). Order mime → ext → byte-sniff. Correct.
- **Async worker path** (canonical production): **NOT robust.** Worker calls `doc_service.ingest(content=full_text,
  blocks=parsed_blocks, ...)` with **`raw_bytes=None`** (`document_worker.py:544-557`), so BOTH `sniff_real_mime` and
  `_route_through_parser`'s byte-sniff are **bypassed**. The worker parses earlier using **`detect_parser(mime_type or
  "", _ext)`** (`document_worker.py:379`) — the non-robust variant. A URL PDF as `application/octet-stream` with empty
  ext misses the registry but is rescued by the OCR fallback (`document_worker.py:426`, Kreuzberg sniffs `%PDF`).
  **DOCX/XLSX/CSV URLs as octet-stream are NOT rescued**: registry miss → OCR fallback → Kreuzberg `_suffix_for_mime`
  maps any `PK\x03\x04` zip to `.docx` (`kreuzberg_parser.py:105`) regardless of actual type → misroute.

**Per-format output quality (Q3) — GOOD for the supported set.** PDF/PPTX/HTML → Kreuzberg structured markdown
(`OutputFormat.MARKDOWN`, 72 `#` headings on a legal doc vs 0 flat). DOCX → document-order heading+table markdown
(`docx_parser.py:100-119`). XLSX → `rows_to_structured_markdown` state-machine. CSV/Sheets → same. MD/TXT → section-split.
**Weak/degraded:** legacy `.doc`/`.xls` (unsupported), and the registry path emits dict chunks with **no typed Block
list** — `parsed_blocks` stays `None` except on the OCR fallback (`document_worker.py:431`).

**raw_content reuse (Q4) — CORRECT but narrow.** Worker reuses `documents.raw_content` ONLY for non-refetchable
`local://` sources (`document_worker.py:297`). Refetchable http(s)/Google URLs are always re-fetched + re-parsed, with
`to_export_url` rewriting Google viewer links to `export?format=csv|docx` first (`:352`). `_route_through_parser` is
bypassed on the async path (raw_bytes=None) — the asymmetry root.

**Oversized doc (Q5) — PARTIAL risk, NOT silent DRAFT.** Embedding failure marks `documents.state='failed'` and raises
(`ingest_stages_store.py:451-462`) — loud. BUT the **default** path is `late_chunking_enabled=True`
(`ingest_stages_store.py:319`) → `late_chunk_embed` sends **ALL chunks in one `embedder.embed_batch(...)` call**
(`late_chunking.py:99`), holding full lists in memory; the embedder internally slices per HTTP call but the
orchestrator-side list is whole-doc. A 224KB sheet → thousands of chunks → large memory spike + slow single await.

**Sacred (Q6) — mostly clean.** No per-bot hardcode; `rows_to_structured_markdown` shape-based/domain-neutral; broad-
excepts at entrypoint/best-effort boundaries with `noqa: BLE001` + reasons. One contradiction: `ocr_factory.py`
docstring claims "fail-loud, no fallback" but the Kreuzberg branch (`:57-74`) silently falls back to SimpleTextParser
on ImportError (WARN only) — doc/behavior drift.

### Per-format quality table
| Format | Routed by | Parser | Output | Block stream | Grade |
|---|---|---|---|---|---|
| PDF | mime/ext + OCR-magic rescue | KreuzbergMarkdown (→ pdf_parser fallback) | Structured MD | None on registry; typed on OCR | GOOD |
| DOCX | mime/ext | DocxParser | Structured MD (heading+table) | None | GOOD |
| **DOC (legacy)** | — | **none** | **unsupported** | — | **MISSING** |
| XLSX | mime/ext | ExcelOpenpyxl → `rows_to_structured_markdown` | Structured MD (multi-table) | None | GOOD |
| **XLS (legacy)** | — | **none** | **unsupported** | — | **MISSING** |
| CSV | mime/ext | GoogleSheetsParser | Structured MD | None | GOOD |
| Google Sheets | `to_export_url`→csv | GoogleSheetsParser | Structured MD | None | GOOD |
| Google Docs | `to_export_url`→docx | DocxParser | Structured MD | None | GOOD |
| PPTX | mime/ext | KreuzbergMarkdown | Structured MD | None | GOOD |
| HTML | mime/ext | KreuzbergMarkdown | Structured MD | None | GOOD |
| TXT | ext/mime | MarkdownParser (degrades) | Section/flat | None | OK |
| MD | ext/mime | MarkdownParser | Section MD | None | GOOD |
| Image | VLM (opt-in) / OCR | VlmImage / Kreuzberg OCR | Caption / OCR text | typed on OCR | OK |
| **octet-stream URL (DOCX/XLSX/CSV)** | worker non-robust `detect_parser` miss → OCR | Kreuzberg `_suffix_for_mime` → forces `.docx` | **misroute** | — | **BROKEN** |

### Findings
- **[HIGH] A-I1 — Async worker path skips byte-sniff (`detect_parser`, not `detect_parser_robust`).**
  evidence: `document_worker.py:379` (non-robust); `:544-557` ingest() called with `raw_bytes=None` so
  `ingest_core.py:264` sniff_real_mime + `__init__.py:737` detect_parser_robust both bypassed.
  detail: the canonical PRODUCTION ingest path selects the parser over declared (mime, ext) only. A DOCX/XLSX/CSV URL
  as octet-stream + empty ext misses the registry; PDF/image saved by OCR magic sniff, tabular/Office octet-stream URLs
  are not. fix: replace with `detect_parser_robust(mime, ext, _raw, detector=detect_parser)` AFTER fetch (bytes `_raw`
  already in hand at `:390`), or thread raw_bytes into `ingest()` and delete the worker-local parse block (single SoT).

- **[HIGH] A-I2 — octet-stream XLSX/CSV/PPTX URL misroutes to DOCX in OCR fallback.**
  evidence: `kreuzberg_parser.py:105` `_suffix_for_mime` maps any `PK\x03\x04` zip with empty mime → `.docx`; reached
  because `document_worker.py:426` OCR fallback gets octet-stream after the non-robust registry miss. `mime_sniff.py:
  _peek_zip_office_subtype` already solves this but is not on the worker path. fix: route the worker through
  sniff_real_mime/_peek_zip_office_subtype (zip-manifest peek) before parser selection; or extend `_suffix_for_mime` to
  peek `[Content_Types].xml`. Best: unify on the ingest_core robust path.

- **[MED] A-I3 — Legacy binary Office (.doc, .xls) unsupported, no graceful degradation.**
  evidence: no `application/msword` or `application/vnd.ms-excel` handler; `registry.py:44` _REGISTRY has none;
  `mime_sniff.py` has no OLE2 magic (`\xd0\xcf\x11\xe0`). CLAUDE.md lists DOC/XLS as first-class. A `.doc`/`.xls`:
  registry miss → OCR fallback → `_suffix_for_mime` returns `.bin` → empty/garbage → 'empty document text' RuntimeError
  (`document_worker.py:442`); no clear 'unsupported legacy format, convert to .docx/.xlsx' signal. fix: OLE2 magic check
  + a dedicated adapter (olefile / LibreOffice headless), OR fail fast with explicit UNSUPPORTED_LEGACY_FORMAT.

- **[MED] A-I4 — Default `late_chunking` embeds the whole-doc chunk list in one in-memory call.**
  evidence: `late_chunking.py:99` `return await embedder.embed_batch(contextualized_chunks, **kwargs)` (no slicing);
  `ingest_stages_store.py:319` late_chunking_enabled defaults True, `:385-408` runs before the batched
  `_embed_in_doc_batches`. detail: the progress-emitting, loop-yielding `_embed_in_doc_batches`
  (DEFAULT_EMBED_DOC_BATCH_SIZE=100) only runs when late chunking is disabled/failed. On default a 224KB sheet → one
  giant embed_batch; embedder slices to DEFAULT_EMBEDDING_MAX_BATCH per HTTP call (won't 413) but memory peak is
  whole-doc, zero mid-doc progress. Not a silent DRAFT (embed failure → state='failed' loud). fix: chunk-count ceiling
  per document (config-gated) + run late_chunk_embed in doc_batch_size slices.

- **[LOW] A-I5 — Structured registry parsers emit no typed Block stream (`parsed_blocks=None`).**
  evidence: all registry parsers return `list[dict]` content+metadata only (`docx_parser.py:134`,
  `kreuzberg_markdown_parser.py:161`); `document_worker.py:290` parsed_blocks stays None on registry path, only set
  `:431` on OCR fallback. detail: ADR-W3-D1's structure-aware Block stream is only populated via OCR — documents parsed
  by the preferred structured registry parsers lose the typed-block signal (re-detected from markdown downstream — the
  exact mis-narration root). **This is the upstream cause of AdapChunk C2 (Section B).** fix: have structured parsers
  also emit a typed Block list and thread it as `blocks=`.

- **[LOW] A-I6 — `ocr_factory` docstring claims fail-loud but Kreuzberg branch silently falls back.**
  evidence: `ocr_factory.py:11-21` docstring 'Fail-loud … raises ImportError'; `:57-74` catches ImportError → returns
  SimpleTextParser with only a WARN. fix: honor the docstring (re-raise) or fix the docstring + add a /health-style
  preflight that resolved engine == configured engine.

### Non-findings (verified clean)
- No live parallel/orphan upload endpoint (`documents_stream_upload.router` not mounted, stream has no consumer).
- Idempotency + outbox + 202 correct; quota charged after idempotency replay, before queue.
- raw_content reuse correct (only `local://`; http/Google always re-fetch — avoids HTML-login-interstitial bug).
- Sync path byte-sniff robust (`sniff_real_mime` + `detect_parser_robust` when raw_bytes present).
- Embedding failure loud, not silent DRAFT; transient errors re-raised for XCLAIM retry.
- Tenant isolation + 4-key preserved across route/worker/sync; workspace slug threaded to RLS GUC.
- Domain-neutral tabular parsing; `to_export_url` rewrites Google viewer URLs before fetch.

**Method note (rule#0):** the octet-stream-XLSX-misroute and oversized-sheet memory claims are **GIẢ THUYẾT
(code-evidenced, NOT runtime-verified)** — need a load-test/curl repro (octet-stream XLSX URL ingest; 224KB multi-table
sheet ingest with RSS sampling) to become VERIFIED.

---
---

# SECTION B — CHUNKING / AdapChunk L1–L7 (independent full-detail agent) — grade C+ (6.0/10)

**Summary (verbatim):** The individual layer implementations (L4 rule selector, L5 cross-check, L6 atomic helpers, L7
narrate) are genuinely expert-grade in isolation: Port+Strategy+Registry+DI, config-driven thresholds, graceful
degradation, zero-hardcode. **But the spine is severed in two places**: (1) the brand-new LLM Strategy Selector
(`230d041`/`8371017`) is an **orphan** — zero production callers; (2) the Block pipeline (`block_pipeline_enabled`,
default ON) **no-ops for every format that goes through the canonical registry parser**, because that path returns
`list[dict]`, never typed `Block`s — so `ctx.blocks` is empty and the whole L2→L3→L6→L7 block-native chain silently
degrades to the legacy text-flatten path. Atomic protection is **default OFF**. Narrate is **default OFF** and, even if
on, re-detects block types heuristically from markdown (the very mis-narration it was built to fix). This is the exact
"khung đã expert, dây chưa nối hết" diagnosis — and it's worse than the telemetry suggests because the flag reads ON
while doing nothing.

### L1–L7 Spec-vs-Reality MAP
| Layer | Spec | Code | Status | Evidence |
|---|---|---|---|---|
| **L1** parse→structured-markdown | Multi-format → unified structured markdown | Registry parsers return `list[dict]{content,metadata}`; OCR returns `ParseResult.blocks` | **LIVE (split contract)** | `document_parser_port.py:29-38`; `document_worker.py:391-432` |
| **L2** block detect & tag (atomic) | Typed Block list w/ atomicity + context buffer | `attach_context_buffer` + `_split_into_blocks_with_atomic` exist; fed only from `ctx.blocks` | **dead-code-flag (no-op on registry path)** | `ingest_stages.py:515-517`; blocks only set `document_worker.py:431` (OCR only) |
| **L3** feature/stats (doc profile) | 10-feature `DocumentProfile` drives selector | `analyze_document_blocks` (entity) + `rule_based_doc_profile` exist | **orphan/telemetry-only** — entity logged but NOT fed to `select_strategy` | `ingest_stages.py:595-602` comment: "entity is NOT yet wired into select_strategy" |
| **L4** LLM strategy selector + new resolver | LLM picks strategy from profile+block-list | `LLMChunkingStrategyResolver` fully built (`230d041`) | **ORPHAN — zero src callers** | grep `build_chunking_resolver`/`LLMChunkingStrategyResolver` in src/ = 0; U4 calls pure-rule `select_strategy()` `ingest_stages.py:538` |
| **L5** rule cross-check | Override selector blindspots | `apply_cross_check` 5 priority rules, config-driven, default **ON** | **LIVE** | `analyze.py:551-688`; `DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED=True` |
| **L6** executor + atomic protection | smart_chunk_atomic; atomic blocks never cut | str `smart_chunk` LIVE; atomic-protect gated **OFF**; block-native `smart_chunk_atomic` **orphan** | **partial — protection OFF; block-native orphan** | `__init__.py:490` gate; `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False`; `smart_chunk_atomic` 0 callers |
| **L7** narrate-then-embed (carry language) | Linearize TABLE/FORMULA/IMAGE before embed | `LLMNarrateGenerator` wired on embed path; default **OFF** | **dead-code-flag (OFF) + mis-typed input when ON** | `ingest_stages_store.py:243-262`; `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`; block_type re-detected `text_processing.py:166-173` |

**LIVE & solid:** 6 production strategies (recursive/hdt/semantic/proposition/hybrid + table_csv); parent/child
small-to-big (`ingest_stages.py:440-444`); VN-legal heading promotion (`vn_structural.py:267`); structural-anchor
breadcrumb `[Chương N > Mục M > Điều K]` (`vn_structural.py:241-251`, retrieval-side matched).

### KEY questions answered
- **(a) Does `block_pipeline_enabled` no-op because registry returns blocks=None?** — **YES, confirmed.** Flag defaults
  ON (`DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True`). `ingest_stages.py:515` reads `parsed_blocks = list(ctx.blocks or
  [])`. `ctx.blocks` ← `blocks=` param of `DocumentService.ingest` ← worker `document_worker.py:555`. `parsed_blocks`
  assigned at **exactly one line — `document_worker.py:431` inside the OCR-fallback branch only**. The registry path
  (`parser.parse()` → `_chunks`, `:391-398`) sets `full_text` but leaves `parsed_blocks=None`, because
  `DocumentParserPort.parse()` returns `list[dict]`. → For DOCX/XLSX/CSV/Sheets/HTML/MD/TXT, `parsed_blocks` empty →
  context buffer skipped, block-aware profile skipped → falls to `analyze_document(content)` text-flatten. **The block
  pipeline only ever runs for OCR-routed PDFs/images.**
- **(b) New LLM Strategy Selector wired into U4 or never called?** — **NEVER CALLED (orphan).**
  `build_chunking_resolver` + `LLMChunkingStrategyResolver` have **zero references in src/** (only the unit test).
  `bootstrap.py` has no provider. U4 selects via raw `select_strategy()` (`ingest_stages.py:538,583`), not the Port.
- **(c) Atomic blocks protected from mid-cut in production?** — **NO (default OFF).** `_atomic_protect_enabled()` reads
  `formula_image_atomic_protect_enabled`, default False. `smart_chunk` (`__init__.py:490`) skips
  `_smart_chunk_with_atomic_protect`. Block-native `smart_chunk_atomic` (`__init__.py:653`) has zero callers → a
  TABLE/FORMULA can be cut mid-block.
- **(d) Narrate-then-embed live?** — **NO (default OFF).** `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`. Even when on,
  block_type is **not** carried from the parser: `narrate_chunks_for_embed` re-derives heuristically with
  `classify_chunk_block_type` from markdown (`text_processing.py:166-173`) — the documented "P2-B mis-narration root."
  Language IS honored implicitly (prompt: "Preserve the source language exactly", `llm_narrate.py:53`).

### Findings
- **[CRIT] B-1 — New LLM Chunking Strategy Selector is a complete orphan (zero production callers).**
  evidence: `infrastructure/chunking_strategy/{llm_resolver,registry,rule_resolver}.py` built in `230d041`/`8371017`;
  grep over src/ = 0 hits (only `tests/unit/test_llm_chunking_strategy_resolver.py`); `bootstrap.py` no provider; U4
  calls `select_strategy()` directly `ingest_stages.py:538,583`. detail: production-quality Port+DI registry + LLM/rule
  adapters, unreachable. Telemetry/tests pass so it reads as "shipped" while delivering 0 runtime effect — the spa-07
  "capability exists, flow doesn't reach it" anti-pattern. fix: wire `build_chunking_resolver` into bootstrap as
  `providers.Singleton` keyed on `system_config 'chunking_strategy_provider'` (default 'rule'), inject into
  DocumentService, replace the direct `select_strategy()` in U4 with `await resolver.resolve_strategy(record_bot_id, …,
  document_profile=<entity>, blocks=parsed_blocks)`, keep `apply_cross_check` (L5) on the result. This also forces L3
  (entity) to become the selector input.
- **[CRIT] B-2 — Block pipeline (default ON) no-ops for all registry-parsed formats — blocks=None.**
  evidence: `DocumentParserPort.parse -> list[dict]` (`document_parser_port.py:29-38`); registry path sets full_text only
  (`document_worker.py:391-411`); parsed_blocks assigned ONLY `document_worker.py:431` (OCR fallback);
  `ingest_stages.py:515` reads ctx.blocks; `analyze_document_blocks` gated on truthy parsed_blocks `:530`. detail: flag
  reads ON so operators believe L2-L7 block path is live, but for DOCX/XLSX/CSV/Sheets/HTML/MD/TXT it falls to
  `analyze_document(content)` text-flatten. Only OCR-routed PDFs/images exercise the block path. fix: extend
  `DocumentParserPort` to optionally return a typed Block list (or `parse_blocks()` / `ParsedDocument.blocks`) and have
  dict-emitting registry parsers populate it (REWRITE cục bộ parser adapter only, per charter). Until then the flag
  default should arguably be OFF (a flag that reads ON while doing nothing violates measure-before-claim); at minimum
  emit a structlog warning when `block_pipeline_enabled=True AND parsed_blocks empty`.
- **[HIGH] B-3 — Atomic-block protection default OFF + block-native `smart_chunk_atomic` orphan.**
  evidence: `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False` (`_00_app_env_taxonomy.py:95`); gate `__init__.py:490`;
  `smart_chunk_atomic` `__init__.py:653` 0 callers. detail: prod `smart_chunk` runs the bare strategy splitter with no
  atomic guard → a TABLE/FORMULA/CODE block can be cut mid-block (the spa-07 cross-row price-conflate class). fix: once
  blocks reach ingest (B-2), route the executor through `smart_chunk_atomic(blocks, ...)` so atomicity comes from the
  parser's is_atomic flag; independently flip `formula_image_atomic_protect_enabled` ON after a load-test soak.
- **[HIGH] B-4 — L3 DocumentProfile entity computed + logged but not fed to the selector.**
  evidence: `ingest_stages.py:611-646` builds profile_entity + logs 10 features; `:595-602` comment "the entity is NOT
  yet wired into select_strategy"; `select_strategy` reads the legacy dict from `analyze_document()` `:538/582`. detail:
  the 10-feature entity (heading_ratio, mixed_content_score, table_avg_rows, formula/image/code counts,
  detected_language) is pure telemetry; the selector scores on the older flat dict, so richer signals never influence
  strategy choice. Couples to B-1. fix: make `profile_entity` (or its `profile_to_dict`) the single selector input;
  delete the parallel `analyze_document(content)` dict to remove dual-source-of-truth.
- **[MED] B-5 — Narrate default OFF and re-detects block_type from markdown when ON (P2-B mis-narration).**
  evidence: `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`; narrate guarded `ingest_stages_store.py:243`; block_type via
  `classify_chunk_block_type` heuristic `text_processing.py:166-173`. detail: even when enabled, narrate receives plain
  chunk strings and guesses block_type (a markdown table row can mis-label as TEXT and skip linearization). Hard timeout
  + raw fallback correct. fix: carry block_type from the typed Block stream (depends on B-2); eligibility keys on
  parser-asserted type, not regex.
- **[LOW] B-6 — block_pipeline branch uses dynamic `__import__`/getattr to reach `analyze_document_blocks`.**
  evidence: `ingest_stages.py:523-529` `getattr(__import__('ragbot.shared.chunking', fromlist=['*']),
  'analyze_document_blocks', None)`. detail: leftover scaffolding from the "Wave D1 not merged" era; silently degrades to
  `analyze_document(content)` if the symbol is renamed, masking a real wiring break. fix: replace with a direct
  `from ragbot.shared.chunking import analyze_document_blocks` so a rename fails loud at import.

### Non-findings (verified clean)
- L5 cross-check — 5 priority-ordered rules, config-driven thresholds, mixed-content warn-only (no silent override),
  default ON, called in both U4 and `smart_chunk`. Clean.
- VN-legal HDT fast-path + heading promotion + breadcrumb anchor — promotion runs before `analyze_document` in both
  branches; HDT fast-path on `(headings + vn_markers) >= threshold`; breadcrumb matches the retrieval-side LIKE clauses
  (regression-pinned). Clean end-to-end.
- 6 strategies present; weighted scorer + CSV/HDT fast-paths. Clean.
- LLM resolver graceful degradation (narrow-then-broad except, falls back to rule resolver, never raises into ingest) —
  correct; it's just never called (B-1).
- doc_profile / chunk_quality / narrate registries follow Port+Registry+Null+DI correctly.
- Zero-hardcode / domain-neutral — thresholds, narrate timeout, atomic multiplier from constants + system_config; LLM
  selector sees SHAPE-only stats, no brand vocab.
- Orphan-merge guard for row-atomic strategies (`table_csv`/`table_dual_index`/`parser_preserve` skip
  `merge_orphan_chunks`) — spa-07 lesson encoded.

**Bottom line:** L5, VN-legal, the 6 strategies, parent/child, breadcrumb are LIVE and solid. L4-new-resolver is orphan;
L2/L3/L6-block-native/L7 are present-but-not-flowing because the registry parser contract emits dicts, not blocks —
**one upstream parser-adapter fix (Block emission, = A-I5) simultaneously unblocks B-2, B-3, B-4, B-5.** B-1 (wire the
resolver into bootstrap+U4) is independent and small. Both are "connect the dye, don't rebuild the frame."

---
---

# SECTION C — ANSWER / GENERATION FLOW (independent full-detail agent) — grade A− (9.2/10)

**Summary (verbatim):** The answer/generation path is sacred-rule-10 **compliant**: the LLM answer is read verbatim,
the system_prompt is assembled APPEND-only by a governed assembler, refusal text is DB-driven with empty-string default,
and `math_lockdown` is fully dead-code in the answer path. Citation validation, per-intent token/context caps, and
grounding sync-XOR-async are all correctly implemented. Deductions are for (1) one Vietnamese spa-domain literal baked
as a dict-key string in platform core (`price_buoi_le`), and (2) stale/misleading docstrings that advertise removed
override mechanisms — a maintenance hazard that could mislead a future engineer into re-wiring an override.

**Prompt assembly (10b — no inject):** `generate.py:595` reads `state["bot_system_prompt"]` **verbatim** as the system
message; when empty, falls back to `_lang(state).prompt_generator` (i18n LanguagePack generic — domain-neutral, pinned
by `test_empty_bot_prompt_falls_back_to_i18n_generic_no_promo`). The user message is a pure
`<documents>…</documents>\n\n<question>…</question>` wrapper (`generate.py:624-628`) — zero instruction text injected.
`SysPromptAssembler` is **APPEND-only** (`sysprompt_assembler.py:126` → `return base + platform_rules`), runs at the
HTTP/worker boundary (chat_stream / chat_routes / chat_worker pipeline / admin effective-prompt), strips disabled rules
via `plan_limits.sysprompt_rules_disabled`, degrades to `base` on any port error. 20 pin tests
(`test_generate_no_app_injection.py` + `test_sysprompt_assembler_pin.py`) **PASS**.

> Corrected premise: the assembler is NOT called in `query_graph.py`; it is invoked at the 3 chat entry points
> (`chat_stream.py:278`, `test_chat/chat_routes.py:431` & `:906`, `chat_worker/pipeline.py:582`) + the admin route.
> query_graph reads the already-assembled prompt off `state["bot_system_prompt"]`. Correct layering.

**Answer override (10c):** The LLM answer flows verbatim from `_invoke_llm_node`→`payload["text"]`→`state["answer"]`.
`_invoke_llm_node` (query_graph.py:981) only sets temperature + a token cap that **narrows-only, never enlarges**
(`:1045-1048`); no text post-processing. `math_lockdown.find_ungrounded_numbers` is **never called in src/** (only
tests) — NO regex-check+replace in the answer path. `extract_numeric_claims` is used **only** in `persist.py:146` to
decide cache-write strategy, never to alter the answer. The sync grounding judge returns `severity="warn",
action="hitl"` (`local_guardrail.py:543`) → only a `guardrail_flags` entry, **never** substitutes the answer. Async
grounding is fire-and-forget logging only. The one genuine answer-substitution path is `guard_output` on
`GuardrailBlocked` (`:408-413, 511-516`), which fires **only** for `severity="block"` = `system_leak`/`secret_leak`
(security defenses) and substitutes the bot's **own** `oos_answer_template` (DB-resolved) — a security boundary, not a
content override.

**Refuse text (10d):** All refuse paths resolve via `_oos_text`/`_resolved_oos_template` → 7-tier DB chain (bot column
→ plan_limits → workspace → tenant → system_config → language_packs → constants). `DEFAULT_OOS_ANSWER_TEMPLATE = ""`
(`_04_jwt_auth.py:37`) — no hardcoded i18n refusal phrase.

**Citation validation (10e):** Both structured (`generate.py:734-764`) and free-form (`:784-806`) paths drop any
LLM-claimed `chunk_id` not in the retrieved `chunk_ids_allowed` set + increment `citation_validation_fail_total`.
Per-intent token cap via `compute_output_cap` + `generate_context_chars_cap_by_intent`; grounding sync suppressed
(`llm_fn=None`) when async is eligible (`guard_output.py:159`) — correctly XOR.

### Findings
- **[MED] C-1 — Domain literal `price_buoi_le` (spa term) hardcoded as state dict-key in platform core.**
  evidence: `jsonb_conversation_state.py:200` (live), `:23` & `:198` (docstring/comment). detail: `locked_price =
  locked.get("price_primary") or locked.get("price_buoi_le")` — 'buoi_le' = VN 'buổi lẻ' (single-session spa price)
  baked as a JSONB key. The generic `price_primary` is canonical; `price_buoi_le` is a backward-compat fallback for
  state rows written by an older spa-specific build. grep confirms 3 hits, all in this one file.
  *(Note: the domain-neutral sweep agent classified this as OK config-driven backward-compat; this agent flags it MED.
  Both views captured — it IS a VN domain literal but only a legacy fallback.)* fix: remove the
  `or locked.get("price_buoi_le")` fallback once a one-shot migration renames legacy state keys, OR if legacy rows are
  TTL-bounded they expire on their own — drop the fallback now + scrub the docstrings; add a pre-commit grep guard for
  `buoi_le|price_goc`.
- **[LOW] C-2 — Stale docstrings advertise a removed math-lockdown answer-override (`find_ungrounded_numbers` + SSE
  'replace' event).** evidence: `chat_routes.py:762-767 + 772` docstring describes a math-lockdown 'replace' event that
  replaces streamed tokens with a 'câu trả lời chuẩn' — an answer-OVERRIDE that sacred-10 forbids and the code no longer
  performs (grep for 'replace' emission = only docstrings). The 2 integration tests
  (`test_chat_stream_production.py:97/126`) still assert a math_lockdown replace reason for a mechanism the prod path no
  longer invokes. detail: a future engineer could re-wire the override believing it intended. fix: delete the docstring
  paragraph + 'replace' line; either delete `math_lockdown.find_ungrounded_numbers` (keep `extract_numeric_claims` for
  the cache decision) or add `# RETAINED: cache-skip util only — MUST NOT be wired into the answer path (sacred-10)`;
  re-point/retire the 2 tests.
- **[LOW] C-3 — `_extract_locked_prices` runs price-extraction over chunk text inside generate.py (flagged for
  transparency).** evidence: `generate.py:90-109, :239-243`. detail: a regex price-cell extractor (`_PRICE_CELL_RE`)
  parses prices out of retrieved chunks to pin `service_locked.price_primary/secondary`. SHAPE-only (no brand literal),
  domain-neutral, runs ONLY when `action_config.enabled` (default OFF). Does NOT inject into the prompt nor override the
  answer — only writes conversation state used by the drift detector (warn-default). Acceptable, but business-logic
  price parsing lives in the generate node rather than a Port. fix: no action for sacred-10; if the action framework
  expands, relocate behind the conversation_state Port.

### Sacred-10 compliance checklist
| # | Rule | Verdict | Evidence |
|---|------|---------|----------|
| 1 | App does NOT inject text/template into LLM prompt | **PASS** | `generate.py:595` verbatim; user msg bare wrapper `:624-628`; assembler APPEND-only `:126`; 20 pin tests pass |
| 2 | App does NOT override LLM answer; no math_lockdown regex-replace | **PASS** | `find_ungrounded_numbers` 0 src callers; `extract_numeric_claims` only cache decision `persist.py:146`; answer verbatim `payload["text"]`; grounding warn/hitl only `local_guardrail.py:543` |
| 3 | Refusal text from `oos_answer_template` DB, empty default | **PASS** | `_oos_text`→7-tier chain `query_graph.py:625-669`; `DEFAULT_OOS_ANSWER_TEMPLATE=""` `_04_jwt_auth.py:37` |
| 4 | Math safety = owner sysprompt, app does NOT regex-check+override | **PASS** | `guard_output.py:62-67` documents + honors; no replace path live |
| 5 | HALLU=0 sacred + refusal traps honored | **PASS** | grounding fail-open degraded-counted `local_guardrail.py:515-527`; critique fail-open; citation drops ungrounded IDs |
| 6 | Zero-hardcode (no magic / brand) in answer path | **FAIL (minor)** | `price_buoi_le` spa literal dict-key `jsonb_conversation_state.py:200`. All numeric thresholds via `_pcfg`+constants. |
| 7 | No app answer-substitution except security block→owner oos_template | **PASS** | Only `severity="block"` (system_leak/secret_leak) substitutes the bot's OWN DB oos_template `guard_output.py:408-413,511-516` |
| 8 | Citation validation against retrieved chunk_ids | **PASS** | structured `:734-764` + free-form `:784-806` reject non-retrieved IDs + metric |
| 9 | Per-intent token + context caps | **PASS** | `compute_output_cap` `:666` narrows-only `:1046-1048`; `generate_context_chars_cap_by_intent` `:501-522` |
| 10 | Grounding sync XOR async (not both) | **PASS** | `llm_fn` set only when `not _grounding_async` `guard_output.py:159` |

### Non-findings (verified clean)
- generate.py contains NO per-bot/domain literal (grep `spa|medispa|legal|gisbot|botox|filler|thông tư` = NONE);
  `_extract_locked_prices` SHAPE-only.
- No prepend/mid-answer injection — assembler guarantees owner prompt is the exact prefix (`base + platform_rules`).
- `reflect.py:177 answer=""` is a re-generate trigger, NOT an override.
- `critique_parser` clean_text strips only owner-instructed `[Supported]`/`[Unsupported]` tokens; default OFF.
- drift detection returns warn-default; block only on owner-configured severity + non-info booking intents; substitutes
  DB oos_template. Gated on `action_config.enabled` (default OFF).
- Async grounding fire-and-forget structlog only; token cap never enlarges the resolver budget.

**Method note (rule #0):** dropped the initial "guard_output overrides the answer" suspicion to PASS after confirming it
substitutes the bot's OWN DB oos_template only on security-block severity; dropped "math_lockdown wired into answer
path" to a docs-only LOW after grep proved 0 src callers. Pin tests green (20 passed).

---
---

# SECTION D — CHAT-ENTRY + TEST-CHAT (independent agent, full report)

## D.1 — Chat HTTP entry flow (production B2B) — grade OK_MINOR

**Summary (verbatim):** The production chat surface is well-architected. Three distinct transports exist:
`POST /api/ragbot/chat` (202 + queued worker, `chat.py`), `POST /api/ragbot/chat/stream` (in-request SSE,
`chat_stream.py`), and the async polling pair under `/test/*` (`chat_async.py`). All three resolve the 4-key identity
at the boundary with `record_tenant_id` lifted from JWT (never body), and all converge on the single canonical
`query_graph` via `get_graph(**build_graph_di_kwargs(container))`. Tenant isolation is enforced both at the vector
store (mandatory `record_bot_id`, raises `ValueError` if None — `pgvector_store.py:310-311`) and at the DB session via
`SET LOCAL app.tenant_id` RLS GUC (`engine.py:174`), which fail-loud `RuntimeError` if tenant unbound
(`engine.py:163`). The token_ledger cost row IS emitted on the answer path (`dynamic_litellm_router.py:756`), keyed off
contextvars that the chat_worker pipeline binds (`chat_worker/pipeline.py:232-234`). Error handling is disciplined —
broad-excepts are confined to request entrypoints / background drivers / aux sinks, all with `exc_info=True` and
noqa-justification. The main gaps are a streaming-path ledger omission and an inconsistency in tenant-strictness
between transports.

### Finding A1 — Streaming answer path does NOT emit a token_ledger row — cost under-reporting for every SSE turn
- **severity: HIGH** · category: cost-observability / token_ledger
- **evidence**: `dynamic_litellm_router.py:806-809` (`complete_runtime_stream` docstring: *"Token / cost accounting is
  NOT emitted here — streaming responses don't expose final usage deltas… the caller is responsible for any post-stream
  accumulation"*); contrast `dynamic_litellm_router.py:756` where non-streaming `complete_runtime` DOES emit. The SSE
  consumer `_sse_helper.py:269-316` reads `final_state.tokens` for the wire `done` event and structlog
  `streaming_response_completed`, but never calls `self._ledger.emit(...)`.
- **detail**: `/chat/stream` and `/test/chat/stream` (real-LLM path) run the LLM through the streaming router, which by
  design skips ledger emission. The non-streaming `complete_runtime` is the only path that writes the `token_ledger`
  table. Result: every streamed answer is invisible to `token_ledger`-based cost reporting (`monitoring_log`/`request_logs`
  finalize still capture it via `final_state.tokens`, so it's not a total blackout — but the dedicated cost ledger has a
  hole). This is a real cost-attribution gap, not a hypothesis.
- **expert_fix**: After the stream drains and `final_state.tokens` is populated, have the streaming caller (or a
  post-stream hook in `_sse_helper.on_complete`) emit a single `TokenLedgerEntry` with `action="llm"`, mode from
  `mode_ctx`, using the accumulated usage. Centralize so streaming + non-streaming share one emit helper; do NOT
  duplicate the math.

### Finding A2 — Tenant-claim strictness diverges across the three transports
- **severity: MED** · category: tenant isolation / auth boundary
- **evidence**: `chat.py:53-55` and `chat_stream.py:96-98` → 403 `missing tenant context` when `record_tenant_id is None`
  (STRICT). But `chat_async.py:82-99` `_tenant_uuid()` falls back to `_PLATFORM_TENANT_FALLBACK_UUID = uuid.UUID(int=1)`
  when the claim is absent, and `test_chat/chat_routes.py:117-119` does the same fallback.
- **detail**: The async `/test/chat-async` and sync `/test/chat` accept tenant-less callers by resolving them to a fixed
  platform-fallback UUID. The docstring (`chat_async.py:86-90`) frames this as deliberate parity with the demo path, and
  production `/api/ragbot/chat` is strict. This is acceptable IF the `/test/*` surface is never externally reachable —
  but see Scope B finding #1: the gating is by middleware path-allowlist + RBAC inside handlers, NOT a hard
  network/auth boundary. A misconfigured deployment that exposes `/test/*` would let an unauthenticated caller chat as
  `UUID(int=1)`.
- **expert_fix**: Keep the strict path as canonical. For `/test/*`, the fallback is acceptable only under the same
  env-gate discipline as the dev-token endpoint (`RAGBOT_DEV_TOKEN_*`). Consider gating the fallback behind an explicit
  `RAGBOT_TEST_HARNESS_ENABLED` flag so production deployments fail-closed.

### Finding A3 — `bypass_rate_limit` body parse in middleware swallows all exceptions silently
- **severity: LOW** · category: narrow-except / observability
- **evidence**: `tenant_context.py:205-206` (`except Exception: pass` with `# noqa: BLE001 — best-effort`).
- **detail**: The 4-key bot-cache lookup for bypass detection swallows everything with a bare `pass` (no log). Unlike
  the rest of this file which logs on degrade, this branch is fully silent. A malformed body or Redis hiccup silently
  disables bypass detection. Not a correctness bug (fail-safe = no bypass), but violates the project's "degrade silent
  but observable" norm.
- **expert_fix**: `logger.debug("bot_bypass_probe_failed", err=...)` instead of bare `pass`, matching the sibling
  branches in the same file.

**Non-findings (verified clean):** 4-key identity correctly enforced (`chat.py:64-68`, body never carries tenant UUID);
SSE disconnect handled (`chat_stream.py:356-360` re-raises `CancelledError`, `finally` always pushes sentinel `:375`;
bounded `asyncio.Queue(maxsize=...)` backpressure `:286`); history/log persist are best-effort with narrow
`SQLAlchemyError` + `logger.exception` (`chat_stream.py:172-176, 203-207, 403-406`); no per-bot hardcode, no
app-injected answer text, refusal text from `oos_template_resolver` DB chain (sacred-10 honored — `chat_async.py:210-218`,
`chat_routes.py:251-259`); SysPrompt assembled via governed `SysPromptAssembler` (append-only, ADR-W1-S10 exception),
not prepended.

## D.2 — Test-chat harness — grade HAS_GAPS

**Summary (verbatim):** The harness correctly stays on the SAME canonical `query_graph` as production (no divergent
answer path — `test_chat/chat_routes.py:344-349` imports the identical `get_graph` + `build_graph_di_kwargs`), and
`chat_stream.py` even re-uses `test_chat._build_pipeline_config` as the single source of truth (`chat_stream.py:75-79`),
so the demo and prod pipeline-config can't drift. The dev self-token endpoint is hard-gated (env flag default OFF +
loopback-only, `pages.py:118-134`). However, the harness is NOT gated as a unit: `test_chat.router` is mounted with
**no** route-level `dependencies=[...]` (`router.py:101`), and RBAC is applied **per-handler inconsistently** — several
destructive endpoints have NO `_require_owner` gate, relying only on tenant row-scoping. Per CLAUDE.md the "never expose
externally" rule is enforced **by convention / network-gateway**, not at the route/auth layer.

### Finding B1 — Destructive test endpoints lack RBAC — `reinit-bots` (wipe), `DELETE /chat` (clear), `DELETE /bots/{uuid}` all ungated
- **severity: HIGH** · category: RBAC / destructive-endpoint protection
- **evidence**:
  - `monitoring_routes.py:55-97` `reinit_bots` — `wipe: bool = True` default; executes `DELETE FROM document_chunks` +
    `DELETE FROM documents` (`:76-82`). **No `_require_owner`** (the only `_require_owner` in the file is on `monitoring`
    at `:129`).
  - `chat_routes.py:1182-1222` `test_chat_clear` (`DELETE /chat`) — `DELETE FROM chat_histories`, `model_invocations`,
    `request_logs`. **No `_require_owner`** (`_require_owner` in this file is only on `test_chat_stream` at `:778`).
  - `bot_admin_routes.py:444-508` `delete_bot` — hard-deletes bot row + chunks + documents + histories. **No
    `_require_owner`** (relies only on `_tenant_scope(request)` at `:457`).
- **detail**: `reinit_bots` and `test_chat_clear` are gated by NOTHING except `TenantContextMiddleware` having bound
  *some* tenant (and the `/test/*` path even allows the `UUID(int=1)` fallback). Any authenticated caller — including
  the tenant-fallback demo identity — can wipe a bot's entire corpus or clear all chat history. `delete_bot` at least
  scopes by tenant (cross-tenant safe) but any non-admin user within the tenant can hard-delete bots. Contrast:
  `update_bot` requires `check_min_level(request, 80)` (`bot_admin_routes.py:351`) and `create_bot` calls
  `_require_owner` (`:159`) — so the gating is genuinely inconsistent, not uniformly absent.
- **expert_fix**: Add `_require_owner(request)` (level 100) — or at minimum `check_min_level(request, 80)` to match
  `update_bot` — at the top of `reinit_bots`, `test_chat_clear`, and `delete_bot`. Better: mount the whole
  `test_chat.router` with a router-level `dependencies=[Depends(require_min_level_dep(80))]` so the entire harness is
  fail-closed by default and individual handlers can't forget the gate.

### Finding B2 — Harness "never-external" rule enforced by convention, not at route/auth layer
- **severity: MED** · category: auth-boundary / defense-in-depth
- **evidence**: `router.py:101` `router.include_router(test_chat.router, prefix=f"{BASE}/test")` — no `dependencies=`.
  `tenant_context.py:92-97` explicitly allowlists `/demo-ragbot`, `/static/`, and `/api/ragbot/test/tokens/self` as
  PUBLIC (bypasses auth entirely). The HTML demo pages (`pages.py:36-106`) are on `pages_router` mounted at root with
  no auth.
- **detail**: CLAUDE.md says "keep the code, never deploy the UI to external — block at gateway/network/auth-scope."
  The code confirms there is **no application-layer** scope check that distinguishes "internal QA caller" from "external
  consumer" on the `/test/*` API routes — it's the same JWT auth as production, plus the public allowlist for the demo
  pages. If the gateway/network block is ever misconfigured, the harness (including the destructive endpoints in finding
  #1) is reachable by any external party with a valid tenant token.
- **expert_fix**: Add an application-layer guard: gate the entire `test_chat.router` + `pages_router` behind an env flag
  (`RAGBOT_TEST_HARNESS_ENABLED`, default OFF in prod) checked at mount time in `router.py`, so production deployments
  don't serve these routes at all unless explicitly opted-in.

### Finding B3 — `reinit_bots`/test_chat hardcodes a Vietnamese demo-refusal string path (harness-only, not shared)
- **severity: LOW** · category: domain-neutral (contained)
- **evidence**: `chat_routes.py:300-303` — `test_chat` returns a hardcoded Vietnamese `"⏳ Tài liệu đang được chuẩn
  bị..."` literal as a `blocked` answer.
- **detail**: This is an app-generated literal returned to the caller (not via the bot's `oos_answer_template`). It is
  technically an app-injected response string. It lives only in the test harness handler (not shared `src/`
  orchestration), and only fires on the documents-not-ready guard, so it does not violate sacred-10 on the production
  answer path. But it IS a hardcoded domain/locale literal that would surface verbatim if the harness were ever
  externally exposed (ties to finding #2).
- **expert_fix**: If this guard is kept, source the message from a language pack / `system_config` key rather than an
  inline literal, consistent with the platform's no-hardcode-refusal-text rule. Low priority while harness stays internal.

**Non-findings (verified clean):** Test-chat uses the SAME `query_graph` — no divergent code path (confirmed
`chat_routes.py:344-349`, `:391`); pipeline-config SSoT shared with production stream (`chat_stream.py:75-79`); dev
self-token double-gated (env flag + loopback, `pages.py:118-134`); admin config/api-key/redis/models endpoints all
properly `_require_owner` gated (`admin_routes.py:31,48,81,144,186,227,247,302`); per-bot circuit breaker + ledger
contextvars bound to the actual bot UUID (`chat_routes.py:149-153`), no per-bot branching in shared code.

---
---

# SECTION E — RETRIEVAL FLOW (independent agent, full report) — grade OK_MINOR

**Summary (verbatim):** The retrieval flow is **architecturally expert-grade**. Port + Strategy + Registry + Null-Object
+ DI is applied consistently across every swappable component (reranker, vector store, lexical/BM25, retrieval-fallback
stages, metadata-filter). I found **zero `if provider==` / `if bot_id==` branching in orchestration** (the only grep
hit, `per_chunk_grader.py:6`, is a docstring). Node-extraction is clean: DI handles threaded as kwargs via
`functools.partial`, all thresholds resolved through `_pcfg(state, key, DEFAULT_*)` with constants imported from
`shared/constants/`. Tenant isolation (`session_with_tenant` + `record_bot_id` mandatory filter), HALLU-safety
(`retrieval_degraded` fail-loud, refuse gates emit DB template not injected text), and graceful degradation are all
correctly implemented. The hybrid dense+BM25+RRF SQL (`pgvector_store.hybrid_search`) is sophisticated and correct:
symmetric VN tokenization (`segment_vi_compounds` on both ingest+query), symbol-phrase OR-branch for code tokens
(`range(5)` → `range <-> 5`), structural-anchor OR-branch with graceful no-match fallback, weighted RRF via
`FULL OUTER JOIN`, HNSW pushdown via local `record_bot_id` column (alembic 0108). The cliff filter + retrieval
safety-net (re-union top-N BM25 the cross-encoder under-ranked) directly address the documented zerank-2 burial bug.
The findings are minor: one real dead-code gap with a latent quality cost, three instances of a hardcoded `5` where the
constant exists, and a couple of latency-vs-accuracy observations.

### Finding E1 — Entity-fairness RRF (`rrf_round_robin`) is dead code — minority-entity starvation unguarded on comparison intents
- **severity: HIGH** · category: coverage-gap / dead-code
- **evidence**: `rrf_round_robin.py` (180 lines, fully implemented + `tests/unit/test_rrf_round_robin.py`); **zero
  production call sites** (grep across `src/` = 0 imports outside the test). Live MQ/decompose merge uses plain RRF
  `mq_rrf_merge_chunks` at `retrieve.py:1335` and `:1399`, whose body (`multi_query_expansion.py:557-605`) ranks
  **purely by fused score with no per-entity quota**.
- **detail**: `rrf_round_robin`'s entire docstring describes the exact failure the live path still has: for a comparison
  query where one compared entity has far more matching chunks, the minority entity's chunks get pushed below `top_k`
  and the answer silently drops half the comparison. The decompose path (one sub-query per entity → RRF-merge → grade)
  is precisely where this bites. The `_remap_grade_for_intent` leniency (`retrieval_filter.py:46`) partially
  compensates downstream by promoting `irrelevant→ambiguous` for compound intents, but that does not fix the `top_k`
  *truncation* that happens before grade. This is a T1-Smartness coverage gap (corpus has both entities, retrieval can
  drop one).
- **expert_fix**: Wire `rrf_round_robin` into the `rrf_fuse` step (`retrieve.py:1334`) for comparison/multi_hop intents,
  supplying `entity_of` from the decompose sub-query→entity map and `per_entity_quota` from a per-bot config key
  (default 1, 0=plain-RRF preserves current behavior). Gate by intent so balanced single-entity queries stay bit-exact.
  If the decision is to keep it deferred, delete the module + test per the repo's own dead-code-notice convention so it
  isn't mistaken for live coverage.

### Finding E2 — Hardcoded `bm25_normalization_flags=5` in 3 sites where `DEFAULT_BM25_NORMALIZATION_FLAGS` (=5) exists
- **severity: MED** · category: zero-hardcode-violation
- **evidence**: `pgvector_store.py:364` (`bm25_normalization_flags: int = 5`); `test_chat/_pipeline_config.py:725`
  (`raw.get(...), 5`); `chat_worker/pipeline_config.py:135` (`_cfg_int(_cfg, ..., 5)`). The constant is defined at
  `shared/constants/_00_app_env_taxonomy.py:183` and correctly imported in the sibling `pg_bm25_retrieval.py:60`.
- **detail**: `5` is a magic number outside the whitelist. Live requests always pass the resolved value
  (`retrieve.py:1012` reads `_pcfg(...DEFAULT_BM25_NORMALIZATION_FLAGS)`), so this is a fallback-default drift risk, not
  a live bug — but it violates the zero-hardcode rule and could silently diverge if the constant is ever retuned.
- **expert_fix**: Import `DEFAULT_BM25_NORMALIZATION_FLAGS` and use it as the default param / coercion fallback in all
  three sites.

### Finding E3 — Retrieval safety-net score-stamping can mis-rank re-injected chunks relative to the CRAG floor
- **severity: MED** · category: correctness / coverage-gap
- **evidence**: `rerank.py:457-493`. Re-injected top-BM25 chunks are stamped to `_stamp = min(_kept_scores)` (lowest
  *surviving* rerank score), or keep their raw RRF score (~0.01) when the surviving pool is empty.
- **detail**: When the pool is non-empty, the safety chunk inherits the min surviving cross-encoder score — fine. But
  when min-score/cliff already emptied the pool, the safety chunk keeps its raw RRF ~0.01, which is below
  `DEFAULT_CRAG_MIN_FALLBACK_SCORE` (0.3) — so the very chunk re-injected to rescue an exact-answer can be dropped again
  at the CRAG fallback gate (`grade.py:486-489`, `rerank_score_mode=="rerank"` branch uses absolute floor). The code
  comment (lines 463-472) acknowledges this M18 history but the empty-pool branch still leaves the chunk vulnerable.
- **expert_fix**: When the surviving pool is empty, stamp the safety chunk to at least `crag_min_fallback_score` (or mark
  `_safety_injected` chunks exempt from the CRAG absolute floor) so a genuinely top-retrieved chunk the cross-encoder
  buried survives end-to-end. Add a load-test assertion on the zerank-2-burial reproduction case before claiming the fix.

### Finding E4 — HyDE and query_router infra registries are commented-out stubs (documented dead code)
- **severity: LOW** · category: dead-code
- **evidence**: `hyde/registry.py` (entire `_REGISTRY`+`build_hyde` commented; 0 active lines) with header "HyDE infra
  never wired. Active HyDE path uses application/services/hyde_generator.py"; `query_router/__init__.py:1-16` explicit
  DEAD-CODE NOTICE.
- **detail**: NOT a violation — explicitly documented defers with the active HyDE path wired via `bootstrap.py:577` →
  `hyde_generator` kwarg → `query_graph.py:1458`. Flagged so a future reader doesn't assume the Port pattern is broken.
- **expert_fix**: None required; optionally delete the stub registry files per the repo's own dead-code-deletion
  convention to reduce confusion.

### Finding E5 — Latency nodes with conditional accuracy payoff (observation, not defect)
- **severity: LOW** · category: latency-vs-accuracy
- **evidence**: `multistage_retrieval` (`retrieve.py:1479-1573`) and `diacritic_restoration` supplementary BM25
  (`retrieve.py:1576+`) both add a full embed+search round-trip.
- **detail**: Both are **default-OFF** and multistage only fires when `not chunks or top_score < early_exit_threshold`
  (`retrieve.py:1488`) — correctly gated to the failing-retrieval case, cost flat on happy path. No fix needed; they
  satisfy the T2 "node must lift accuracy or be gated per-bot" rule. CRAG grade itself has a smart-skip
  (`grade.py:120-162`) above `crag_skip_retry_above_score` and a timeout fallback — also correctly gated.

**Compliance check**: Sacred rule #10 PASS (refuse gates only empty the chunk list → downstream emits
`bots.oos_answer_template`; never inject refuse text — `retrieval_filter.py:184-186`); Strategy+DI PASS (registries
exemplary; no infra imports in orchestration except Null-object isinstance check `rerank.py:99` + intentional lazy
imports of concrete services); Zero-hardcode 1 MED violation (bm25 flags ×3); Tenant isolation PASS
(`session_with_tenant`, mandatory `record_bot_id`); HALLU-safety PASS.

---
---

# SECTION F — MULTI-TENANT / IDENTITY / RLS (independent agent, full report) — grade B+ (7.8/10)

**Summary (verbatim):** App-level `record_bot_id`/`record_tenant_id` filtering on the hot read paths (vector retrieval,
semantic cache, conversation/message reads) is solid and consistent; this is what actually carries isolation today.
RLS is real DDL but fully inert in production. Two genuine IDOR-write paths exist (low practical exploitability, but
should be DB-fenced). Workspace-as-entity and quota cascade are only half-wired (expected strangler-fig state).

1. **RLS is 100% inert in production — confirmed, not theoretical.** `.env` shows `DATABASE_URL=…@10.0.1.160`
   (superuser), `DATABASE_URL_APP`/`DATABASE_URL_SYSTEM` **unset**, and the escape hatch
   `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` is **active**. `create_engine_app` (engine.py:67-81) falls back to the superuser
   admin DSN. The `after_begin` SET-LOCAL hook is attached (bootstrap.py:185-187) but a superuser/`rolbypassrls`
   connection ignores every policy, so all 20-21 live policies are dead. The CREATE POLICY DDL the README cites
   (`0069/0141/0187`) is **no longer in git** — squashed into the `20260618` baseline; only the two
   `20260619_rls_*_role_grants.py` provisioning migrations remain. Honestly documented in
   `20260619_rls_app_role_grants.py:14-25` and `engine.py:60-92`.
2. **Is inert RLS a real leak risk?** Honest answer: **the mandatory `record_bot_id` app-filter IS solid enough for the
   hot path, but inert RLS removes the defense-in-depth backstop for the IDOR-write paths below.** Every
   retrieval/cache read carries an explicit application WHERE filter — `record_bot_id` (globally-unique UUID) is
   sufficient for cross-tenant *read* isolation on its own. RLS being inert does not open a read-leak on those paths.
   The risk concentrates on **PK-only write paths** where, with RLS inert, nothing fences a cross-tenant overwrite if a
   foreign UUID is supplied.
3. **4-key identity is correctly enforced at the boundary** (chat.py:51-66, bot_registry_service.py): tenant UUID lifted
   from JWT only, 4-key resolve → `record_bot_id`, internal queries use `record_bot_id`. Worker re-binds
   `tenant_id_ctx` from the trusted enqueued payload (chat_worker/pipeline.py:97-98). Cross-tenant workers correctly
   use the separate `system_session_factory` (bootstrap.py:484-485).
4. **Workspace-as-entity (D2) is half-wired:** `workspaces` table + `WorkspaceRepository` exist but workspace remains a
   pass-through slug for identity; **quota does NOT cascade tenant→ws→bot** — `quota_repository.py:26` scopes on
   `record_tenant_id` only. D2's quota-cascade DoD is unmet.

### Finding F1 — Cross-tenant document overwrite — save() guards incoming object, not existing DB row
- **severity: HIGH** · category: tenant-isolation / IDOR-write
- **evidence**: `document_repository.py:96-114`
- **detail**: `save()` checks `document.record_tenant_id != tid` (line 97, the INCOMING object's tenant) then does
  `session.get(DocumentModel, document.id)` (line 100, PK-only) and overwrites all columns in place if a row exists. The
  guard validates the attacker-supplied object, NOT the fetched row's tenant. A caller who knows a victim's document
  UUID can craft `Document(id=victim_uuid, record_tenant_id=own_tid)` — guard passes, then the victim's row is fetched
  by PK and overwritten. With RLS inert (superuser), no DB fence stops this. Mitigant: document ids are server-assigned
  uuid4 (122-bit unguessable). Same structural pattern in `conversation_repository.py:158-186` (save() — guard at 158
  checks incoming object, get() at 163 is PK-only).
- **expert_fix**: Replace `session.get(Model, pk)` + in-place mutate with a fenced statement:
  `UPDATE documents SET ... WHERE id = :id AND record_tenant_id = :tid RETURNING id` and assert rowcount==1 (the same
  pattern `ai_config_repository.update_binding` already uses). Apply identically to `conversation_repository.save()`.

### Finding F2 — job_repository.update_status fires UPDATE with no tenant fence when record_tenant_id is None
- **severity: MED** · category: tenant-isolation / IDOR-write
- **evidence**: `job_repository.py:47-78` (esp. 74-76)
- **detail**: `UPDATE jobs ... WHERE id = :job_id`; the `AND record_tenant_id = :tid` clause is added ONLY when
  record_tenant_id is not None (line 75-76). The fail-before-lookup paths call it with None (commented as H4 at line 61).
  With RLS inert, a None-tenant update on a known job UUID clobbers any tenant's job status/result/error. MEDIUM not
  HIGH: job ids are uuid4 and the None path is internal (worker error handling), not user-reachable.
- **expert_fix**: Split into `update_status_scoped(job_id, record_tenant_id)` (always fenced) and a `system_fail(job_id)`
  that runs on the BYPASSRLS system_session_factory and is explicitly a trusted-internal call.

### Finding F3 — request_steps / guardrail_events workspace_id inherited via PK-only lookup (no tenant co-filter)
- **severity: LOW** · category: data-integrity / forensic-attribution
- **evidence**: `request_log_repository.py:289-292` (add_step), 346-349 (add_steps_batch); `guardrail_repository.py`
  ~54-56 (insert)
- **detail**: `SELECT workspace_id FROM request_logs WHERE request_id = :id` has no `AND record_tenant_id`. The child
  row is still stamped with the CALLER's tid (line 301), only the workspace_id slug is inherited — NOT a cross-tenant
  data leak, at worst a forensic-attribution glitch if a foreign request_id were supplied (request_id is server-generated
  uuid4, never user-supplied). Flagged for completeness; not a security hole.
- **expert_fix**: Add `AND record_tenant_id = :tid` to the parent SELECT for belt-and-suspenders (cosmetic given
  request_id provenance).

### Finding F4 — stats_index read/delete methods use plain session_factory — RLS-blind even after DSN cut-over
- **severity: LOW** · category: rls-readiness
- **evidence**: `stats_index_repository.py:146-168` (delete_by_document), 235-266 (query_by_price_range), 268-334
- **detail**: All read methods filter on `record_bot_id = :bot_id` ONLY and open via `self._sf()` (no
  session_with_tenant). record_bot_id is a unique UUID → cross-bot/cross-tenant SAFE for reads TODAY. But because they
  never bind app.tenant_id, the moment ops flips DATABASE_URL_APP to the NOBYPASSRLS role these queries fail-closed to
  ZERO rows (price-range/entity queries silently return empty). Latent reliability regression, not a leak.
- **expert_fix**: Route stats_index reads through `session_with_tenant` (thread record_tenant_id from the bot config).
  `delete_by_document` is ingest-internal — move onto system_session_factory.

### Finding F5 — RLS CREATE POLICY DDL absent from git (squashed) — README §6 over-claims 'provisioned + wired'
- **severity: MED** · category: governance / reproducibility
- **evidence**: `alembic/versions/` contains only `20260619_rls_app_role_grants.py` +
  `20260619_rls_system_role_grants.py`; no migration matching grep 'CREATE POLICY' / policy DDL; README cites
  0069/0141/0187 which do not exist post-squash
- **detail**: The 21 policies exist on the LIVE dev DB (created pre-squash) but a fresh clone from the 20260618 squash
  baseline gets the role grants WITHOUT the policies — so RLS would be even more inert (no policies at all) on a new
  environment. The docstring asserts policies are 'present' but that is DB-state, not migration-reproducible. Violates
  CLAUDE.md 'no out-of-band DB drift' — the security control is not reproducible from git.
- **expert_fix**: Add an idempotent migration that re-asserts ENABLE/FORCE ROW LEVEL SECURITY + CREATE POLICY ... USING
  (record_tenant_id = current_setting('app.tenant_id',true)::uuid) for all 20 tenant tables, pinned by a test that
  introspects pg_policies. Prerequisite for any future DSN cut-over.

### Finding F6 — Workspace-as-entity + quota cascade (D2 ADR) unimplemented — quota stops at tenant
- **severity: LOW** · category: architecture / D2-DoD
- **evidence**: `quota_repository.py:26,56,60` (all WHERE record_tenant_id only); `workspace_repository.py` docstring
  'does NOT replace the 4-key identity'; `program/decisions/00-DECISION-REGISTER.md:11` (D2)
- **detail**: workspaces table + WorkspaceRepository exist but identity still flows via bots.workspace_id slug
  pass-through, and quota is scoped tenant-only — no tenant→workspace→bot cascade. Expected strangler-fig in-progress
  state, but the D2 ADR should not be marked done.
- **expert_fix**: If workspace-level quota is a real product requirement, add workspace_id to QuotaModel + a resolve
  chain (bot quota → workspace quota → tenant quota → system default), mirroring bot_limits.py. Otherwise close D2 as
  'slug pass-through is sufficient' and drop the cascade clause.

**Highest-value finding:** No cross-tenant *read* leak exists on the hot path — `record_bot_id` filtering is consistent
and correct (verified pgvector_store.py, semantic_cache.py, conversation_repository.py, quota_repository.py). The real
exposure is the **PK-only IDOR-write** in `document_repository.save()` / `conversation_repository.save()` (HIGH).

**False-positives dropped** after re-reading: H3 (request_log finalize — has a real `TenantIsolationViolation` guard at
line 120), M1/M2 (message reads — transitively safe behind tenant-scoped parent SELECT), H5/H6/H7 downgraded
LOW→cosmetic (child rows stamped with caller tid, request_id is internal).

---
---

# SECTION G — COST-LOG CENTER (independent agent, full report) — grade C+ (6.0/10)

**Summary (verbatim):** The codebase has **THREE parallel per-call/per-turn cost stores**, not one:

| Store | Granularity | Coverage | Roll-up reporting |
|---|---|---|---|
| `token_ledger` (new, audited target) | per-CALL, rich schema (in/out/cached unit-price snapshot, action, mode, finish_reason, document_id, duration) | **INCOMPLETE** — streaming answer MISSING; only `jina` embed/rerank emit | timeseries only; **NO** workspace/tenant roll-up totals, **NO** bot-count |
| `model_invocations` (older) | per-CALL, has `purpose` | **COMPLETE-ish** — streaming answer covered via `invoke_model`; all wrapped purposes | consumed by `cost_audit` feature roll-up |
| `request_logs` (oldest) | per-TURN (1 row/request, UPSERT) | turn-level total (captures streaming answer cost) | **FULL** per-bot/workspace/tenant/all-tenants + `bot_count`/`workspace_count` via `tenant_analytics_service` |

The `token_ledger` dashboard endpoint `GET /metrics/usage/timeseries` therefore reports **materially less than actual
spend** for any tenant whose traffic is normal SSE chat on the active retrieval stack. `request_logs` has the true turn
totals but cannot break cost down per-purpose; `token_ledger` was meant to be the per-purpose breakdown but is the one
with holes.

### Finding G1 — Streaming answer path (complete_runtime_stream) emits NOTHING to token_ledger
- **severity: CRIT** · category: emit-coverage
- **evidence**: `dynamic_litellm_router.py:790-991` — method body has 0 `_ledger.emit` calls (awk-verified).
  Self-documented at 806-809 and 1091-1095. `query_graph.py:1050,1093,1150` route `purpose=='generation'` through
  complete_runtime_stream; the `_capture_usage` sink (1129-1138) writes only to `ctx.record()`/model_invocations, NOT
  to ledger. The two emit sites (router 756 in `_complete_runtime_one`, 1101 in `_complete_via_llmport`) are the
  NON-streaming paths only.
- **detail**: For every normal SSE chat turn the answer-generation LLM call (largest output-token cost) produces no
  token_ledger row. token_ledger systematically under-counts output tokens and cost. Because request_logs (turn-level)
  and model_invocations (per-call) DO capture it, the bug is invisible unless you cross-check totals — the silent-drift
  class CLAUDE.md warns about.
- **expert_fix**: Emit at the post-stream accounting point that already exists: `complete_runtime_stream` lines 954-991
  already compute prompt_total/completion_total/cached_total/cost_decimal/finish_reason + started/finished wall-clock.
  Add `self._ledger.emit(TokenLedgerEntry(...))` wrapped in try/except pass (mirror 754-779). Also pass `purpose` into
  the non-streaming emit at 756 (currently omitted).

### Finding G2 — Only `jina` embedder/reranker emit; active ZeroEntropy/Voyage/OpenAI/litellm/bkai adapters emit nothing
- **severity: CRIT** · category: emit-coverage
- **evidence**: grep emit_aux_usage → ONLY `jina_embedder.py:328` + `jina_reranker.py:282`. `zeroentropy_embedder.py` /
  `zeroentropy_reranker.py` / `openai_embedder.py` / `litellm_embedder.py` / `voyage_reranker.py` /
  `viranker_local_reranker.py` / `bkai_vn_embedder.py` have 0 emit. `zeroentropy_reranker.py:78-88` __init__ does not
  even accept a `ledger` kwarg, so bootstrap.py:394's `ledger=token_ledger` is filtered out. (DEFAULT_RERANKER_PROVIDER
  constant is 'jina' but system_config.reranker_provider DB SSoT governs runtime.)
- **detail**: Whichever embed/rerank provider is actually active in DB config decides whether ANY embed/rerank cost
  reaches token_ledger. If not jina, embed+rerank spend (a large share of ingest and every retrieval) is fully absent.
  Re-ingest of a large corpus could be thousands of embed calls, none recorded.
- **expert_fix**: Move the emit out of the concrete adapter into a Port-level decorator/mixin so it fires for EVERY
  provider (Strategy+DI: behavior at the boundary). A LedgerEmittingEmbedderDecorator / LedgerEmittingRerankerDecorator
  wrapping the resolved adapter in build_embedder/build_reranker, reading usage from a small UsageResult the Port
  returns. Short-term stopgap: add emit to zeroentropy_* + accept ledger kwarg (but decorator is the correct tier).

### Finding G3 — token_ledger analytics offers NO per-workspace/per-tenant ROLL-UP totals and NO cross-tenant admin summary
- **severity: HIGH** · category: reporting-gap
- **evidence**: `token_ledger_analytics_repository.py` has exactly ONE method `usage_timeseries` (date_trunc bucket +
  optional model/action/provider breakdown). Its only consumer is `admin_metrics.py:112` GET /metrics/usage/timeseries.
  There is NO sum-totals-by-workspace, NO sum-by-tenant, NO all-tenant leaderboard, NO bot-count over token_ledger.
  Those roll-ups exist ONLY over request_logs in `tenant_analytics_service.py` (workspace_aggregate /
  all_tenants_summary with WorkspaceSummary.bot_count, TenantSummary.bot_count/workspace_count) exposed at
  `admin_analytics.py:410` /admin/analytics/all-tenants and :483 /analytics/workspace-aggregate.
- **detail**: The requested hierarchy (per-bot → per-workspace → per-tenant → system-admin-all-tenant, time-range
  queryable) is only partially served, split across two incompatible data sources with different cost numbers. A FinOps
  user cannot get an authoritative per-workspace cost from token_ledger today.
- **expert_fix**: Add roll-up methods (per-workspace with bot_count, per-tenant with workspace_count/bot_count,
  all-tenant leaderboard) — see reporting_design below.

### Finding G4 — Two non-streaming emit sites disagree on started_at/duration_ms and purpose
- **severity: HIGH** · category: schema-fidelity
- **evidence**: `dynamic_litellm_router.py:771-772` (runtime/answer non-stream emit) sets started_at=finished_at →
  duration_ms NULL/0. The `_complete_via_llmport` emit at 1115-1117 correctly sets started_at=_finished-latency +
  duration_ms=latency_ms. Neither the 756 emit NOR aux_usage carry `purpose` for LLM calls. aux_usage.py never sets
  input_unit_price/output_unit_price/cost_usd, so embed/rerank rows always have cost_usd=NULL. (DDL is good —
  `squashed_baseline.sql:842-871` has all columns; the EMIT side under-fills it.)
- **expert_fix**: Thread real wall-clock start into the runtime emit; add purpose to the LLM emits; in aux_usage accept
  unit prices + compute cost_usd so embed/rerank cost is non-NULL.

### Finding G5 — Three overlapping cost stores with divergent totals and no reconciliation
- **severity: HIGH** · category: architecture
- **evidence**: `model_invocations` (squashed_baseline.sql:481+) per-call with purpose/cost, populated for EVERY
  invoke_model incl. streaming generation. `request_logs` (645+) per-turn UPSERT-on-request_id. `token_ledger` newest
  but has the 2 CRIT holes.
- **detail**: FinOps cannot trust any single number: request_logs turn-cost ≠ Σ token_ledger ≠ Σ model_invocations for
  the same window. The silent-drift / no-single-source-of-truth failure mode.
- **expert_fix**: Decide ONE authoritative per-call ledger (token_ledger has the richest schema) — make it complete
  (G1+G2+G4), then derive request_logs turn-cost as SUM over token_ledger by request_id OR document it as denormalised
  + add a reconciliation test. Consider retiring model_invocations once token_ledger is authoritative.

### Finding G6 — token_ledger has NO request_id-level population, so per-call rows can't be tied to a turn
- **severity: MED** · category: schema-fidelity
- **evidence**: TokenLedgerEntry has request_id field (token_ledger_port.py:32) and DDL has request_id uuid
  (squashed_baseline.sql:854), but NEITHER emit site populates it: router 756/1101 omit request_id; aux_usage.py:57-77
  omits it.
- **expert_fix**: Add request_id_ctx contextvar (set at the route/worker entrypoint alongside trace_id_ctx) and snapshot
  it in both router emits and aux_usage. Add ix_token_ledger_request_id. Enables the reconciliation test (G5).

### Finding G7 — AsyncDBTokenLedger drops rows silently on queue-full and on any flush error
- **severity: MED** · category: reliability
- **evidence**: `async_db_token_ledger.py:92-96` QueueFull → self._dropped++ (warn every 100th). :133-135 _flush
  broad-except → warn + swallow whole batch (up to 200 rows lost). No dead-letter, no dropped-count metric exported.
- **detail**: Fire-and-forget is correct per graceful-degradation (aux sink must not kill money-path), but for a
  billing-grade cost report you need to KNOW the drop count to put an error bar on reported spend.
- **expert_fix**: Export self._dropped + flush-failure count as a Prometheus gauge + surface a 'ledger_completeness'
  field in the dashboard meta. Keep drop-on-full; just make the loss observable.

### Finding G8 — usage_timeseries breakdown whitelist omits `purpose`
- **severity: MED** · category: reporting-gap
- **evidence**: `token_ledger_analytics_repository.py:24-29` _BREAKDOWN_COLS = {none, model, action, provider};
  `purpose` (the column distinguishing generate/grade/grounding/embed/rerank) is NOT an allowed breakdown key.
- **expert_fix**: Add 'purpose': 'purpose' to _BREAKDOWN_COLS (safe — closed whitelist). Also add a multi-key breakdown
  (action+purpose) for the cost dashboard.

### reporting_design (verbatim, to close the gaps)

**Target**: per-bot → per-workspace → per-tenant → system-admin-all-tenant, time-range + bucketed, on a SINGLE
authoritative per-call ledger (`token_ledger`, once G1/G2/G4/G6 land).

**1. Fix emit completeness first** (prerequisite — no report is trustworthy until done): streaming emit (G1);
Port-boundary emit decorator for ALL embed/rerank (G2); request_id + purpose + real started_at/duration_ms/unit-price
on every row (G4/G6).

**2. New repository roll-up methods**, all time-range bounded `started_at >= :from AND started_at < :to`:

```sql
-- per-bot (tenant-scoped)
SELECT record_bot_id, bot_id,
       sum(input_tokens) in_, sum(output_tokens) out_, sum(total_tokens) tot,
       round(coalesce(sum(cost_usd),0)::numeric,8) cost, count(*) calls
FROM token_ledger
WHERE record_tenant_id = :tenant AND started_at >= :from AND started_at < :to
GROUP BY record_bot_id, bot_id ORDER BY cost DESC;

-- per-workspace (tenant-scoped) + CRM bot-count
SELECT workspace_id,
       count(DISTINCT record_bot_id) AS bot_count,
       sum(input_tokens), sum(output_tokens), sum(total_tokens),
       sum(cost_usd) cost, count(*) calls
FROM token_ledger
WHERE record_tenant_id = :tenant AND started_at >= :from AND started_at < :to
GROUP BY workspace_id ORDER BY cost DESC;

-- per-tenant + system-admin cross-tenant (NO tenant filter, RBAC 100)
SELECT record_tenant_id,
       count(DISTINCT workspace_id) AS workspace_count,
       count(DISTINCT record_bot_id) AS bot_count,
       sum(total_tokens) tot, sum(cost_usd) cost, count(*) calls
FROM token_ledger
WHERE started_at >= :from AND started_at < :to
GROUP BY record_tenant_id ORDER BY cost DESC LIMIT :n;

-- per-purpose attribution (the per-call payoff)
... GROUP BY purpose  (or action, purpose)
```
All hit existing indexes `ix_token_ledger_tenant_started` / `ix_token_ledger_bot_started` / `ix_token_ledger_started`.

**3. New endpoints** (admin_metrics.py): `GET /metrics/usage/rollup?group=bot|workspace|tenant&from&to&breakdown=purpose|model|action`
— `require_min_level(60)`, JWT-tenant-scoped; `GET /metrics/usage/all-tenants?from&to&sort_by=cost&limit=n` —
`require_min_level(100)`, no tenant filter, returns bot_count/workspace_count per tenant. Extend the existing
`/metrics/usage/timeseries` breakdown whitelist with `purpose`. RBAC: level 60 = own-tenant rollups; level 100 =
cross-tenant + may target any record_tenant_id (reuse the `_resolve_tenant_scope` 403-on-mismatch pattern in
admin_analytics.py:82).

**4. Reconciliation guard**: a test asserting, for a sample window,
`SUM(token_ledger.cost_usd) GROUP BY request_id ≈ request_logs.cost_usd` within tolerance — fails loudly if a new code
path stops emitting (would have caught G1 & G2 at CI).

**Note on duplication**: once token_ledger is authoritative, model_invocations and the independent request_logs.cost_usd
write are redundant; derive or document as denormalised — do NOT keep three independently-written cost numbers.

**Key file:line anchors**: emit gap `dynamic_litellm_router.py:790-991` (streaming) vs `:756`/`:1101` (non-stream);
adapter coverage `jina_embedder.py:328`, `jina_reranker.py:282` (only these emit); analytics
`token_ledger_analytics_repository.py:38` (only method); endpoint `admin_metrics.py:112`; parallel roll-up system
`tenant_analytics_service.py:318,427` + `admin_analytics.py:410,483`; DDL `squashed_baseline.sql:842-871` (token_ledger),
`:481+` (model_invocations), `:645-680` (request_logs).

---
---

# SECTION H — DOMAIN-NEUTRAL / ZERO-HARDCODE / PER-BOT SWEEP (independent agent, full report) — grade 8.5/10 STRONG

**Summary (verbatim):** The user's main complaint — "code supports the 3 demo bots individually too much" — is **NOT
substantiated by evidence**. I found **zero** executable branches keyed on a demo-bot slug (`test-spa-id` /
`chinh-sach-xe` / `thong-tu-09-2020-tt-nhnn`), brand name (`medispa`), or domain string (`triệt lông`, `buổi lẻ`).
Every such literal lives in **comments/docstrings** (forensic notes citing which load-test case motivated a generic
fix) or in **value-shape heuristics**, never in control flow. The codebase that *looks* bot-specific (VN legal markers,
price-lock keys, service-drift detection) has in fact been correctly generalized: markers moved to
`DEFAULT_STRUCTURAL_MARKERS_BY_LANG[lang]`, price key renamed `price_buoi_le → price_primary` with backward-compat
fallback, drift detection keyed on CSV-row shape. The one **real** category of violation is generic zero-hardcode
hygiene (bare numeric literals as config fallback defaults), which is orthogonal to the per-bot complaint and LOW/MED
severity because the values are still `system_config`-driven at runtime.

- **Per-bot / brand drift in core: 0 real violations.** The "drift" perception is driven by Vietnamese forensic
  comments + illustrative examples, not by code.
- **Zero-hardcode hygiene: 1 real cluster** — `pipeline_config` builders duplicate literal fallbacks instead of
  importing existing named constants. Config-driven, so no answer-behavior change; SSoT-rule gap only.
- **Provider branches in orchestration: 0.** Only two `if provider == "null"` null-object sentinel checks in the parser
  registry/worker (registry dispatch pattern, not a real-provider branch).
- **Version-ref: 0 real.** All `_new`/`_legacy` hits are local variable names (`_action_state_new`, `was_new`,
  `chunks_new`) or commented-out dead code in `vi_tokenizer.py`.

### Finding H1 — pipeline_config builders use bare numeric literals instead of importing existing named constants
- **severity: MED** · category: zero-hardcode (SSoT)
- **evidence**: `chat_worker/pipeline_config.py:73-78,97,123,135,386-387,435-436`; mirrored in
  `test_chat/_pipeline_config.py`. `DEFAULT_CONDENSE_HISTORY_LIMIT`, `DEFAULT_REFLECT_ANSWER_PREVIEW_CHARS`,
  `DEFAULT_CRAG_FALLBACK_COUNT`, `DEFAULT_CRAG_MAX_GRADE_RETRIES`, `DEFAULT_MAX_REFLECT_RETRIES`,
  `DEFAULT_GROUNDING_CHECK_THRESHOLD`, `DEFAULT_MMR_SIMILARITY_THRESHOLD`, `DEFAULT_MMR_LAMBDA`,
  `DEFAULT_SEMANTIC_CACHE_TTL`, `DEFAULT_BM25_NORMALIZATION_FLAGS`, `DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK` all
  EXIST in `shared/constants/` but are not imported here — the builder writes `_cfg_int(_cfg,
  "pipeline_condense_history_limit", 6)` with bare `6`.
- **detail**: Does NOT change answer behavior per-bot: `_cfg_int` reads `system_config` first (config.py:53), the literal
  is only the last-resort fallback. Risk is drift — if the constant changes, these duplicated fallbacks silently
  diverge. Violates "ALL defaults declared in shared/constants.py and imported."
- **expert_fix**: Import the existing constants and pass them as the `_cfg_int/_cfg_float` default. For the ~5 keys with
  NO backing constant (`grade_chunk_preview`/500, `graph_recursion_limit`/50, `whole_doc_threshold_chars`/8000,
  `graph_rag_max_hops`/2, `short_query_word_threshold`/5), add `DEFAULT_*` to `shared/constants/` then import.

### Finding H2 — `autocut_min_gap_ratio = 0.3` has no backing named constant
- **severity: LOW** · category: zero-hardcode
- **evidence**: `retrieve.py:1773` (`float(_pcfg(state, "autocut_min_gap_ratio", 0.3))`) and `retrieval_filter.py:83`
  (function-signature default `min_gap_ratio: float = 0.3`). `DEFAULT_AUTOCUT_MIN_GAP_RATIO` does NOT exist.
- **detail**: Config-driven via `_pcfg`. Autocut is default-OFF (`autocut_enabled, False`), so the literal rarely fires.
  Generic threshold, not per-bot.
- **expert_fix**: Add `DEFAULT_AUTOCUT_MIN_GAP_RATIO: Final[float] = 0.3` to `shared/constants/`, import in both sites.

### Finding H3 — Parser-registry null-object sentinel uses string compare `if provider == "null"`
- **severity: LOW (borderline / acceptable)** · category: provider-branch (false-alarm-adjacent)
- **evidence**: `parser/registry.py:105`, `document_worker.py:145`.
- **detail**: `"null"` is an internal null-object registry key, not a real provider name — Strategy+Registry null-object
  pattern, not domain branching. The registry already does `isinstance(parser, NullParser)` elsewhere. Not a real
  violation.
- **expert_fix**: Optional polish — replace the string sentinel with an `isinstance(..., NullParser)` check. Not required.

### false_alarms_dismissed: 36
Breakdown of dismissed hits (all read and classified as NOT violations):
- **17 Vietnamese/brand literals in comments & docstrings** (`triệt lông`, `medispa`, `spa q11`, `Dr. Medispa`,
  `price_buoi_le`) — forensic notes citing the load-test case that motivated a *generic* fix; zero in control flow.
- **3 bot-slug mentions** (`test-spa-id`, `thong-tu-09-2020-tt-nhnn`) — all in docstrings/comments
  (`conversation_state_port.py:11`, `schemas.py:32,64`); the slugify example is a transformation illustration, not a
  branch.
- **`tenant_analytics_service.py:355`** `record_bot_id IN (...)` — parameterized subquery scoped by record_tenant_id,
  generic analytics filter.
- **`jsonb_conversation_state.py:200`** `locked.get("price_primary") or locked.get("price_buoi_le")` — generic primary
  key with backward-compat fallback for in-flight state; comment explains the rename. Domain-neutral.
- **`vn_structural.py` Chương/Mục/Điều markers** — lifted to `DEFAULT_STRUCTURAL_MARKERS_BY_LANG[lang]`, keyed by
  language code with explicit "not customer/brand-specific, platform-level" rationale; non-VN bots resolve their own
  (empty/EN) set.
- **`stats_index_repository.py` price ordering / reverse-token fallback** — keyed on value shape (priced-row-first,
  char-length) and corpus header column names, currency-neutral; VN strings only in explanatory comments.
- **`query_graph.py:2350-2419` synthetic stats chunk** — `_is_field_like` is a domain-neutral word-count + char-cap
  gate; price label is the schema column concept, explicitly currency-neutral (raw number, no "VND").
- **`vi_tokenizer.py` `_legacy` refs** — commented-out dead code, not executable.
- **Local var names** `_action_state_new`, `was_new`, `chunks_new`, `bot_version_new`, `version_new` — purpose-named
  state vars, not version-refs.
- **`auditor_agent.py:44-45,80`** — the audit tool's own *commented-out* detection regexes (grep matched its own
  pattern strings).

**Bottom line for the user:** the per-bot-drift concern is largely a *readability* artifact — the code is densely
commented with Vietnamese load-test forensics that make it *look* spa/legal-specific, while the actual logic is
generalized correctly. Highest-leverage change to reduce that perception: (a) close the bare-literal fallback cluster
in the two pipeline_config builders (H1), and optionally (b) trim brand/case names out of comments. **No core logic
needs to be de-hardcoded for a specific bot, because none is.**

---
---

# SECTION I — MULTI-LANGUAGE SUPPORT (independent agent, full report) — grade B / 7.5

**Summary (verbatim):** Language IS a per-bot config: `bots.language` VARCHAR(8) column (`models.py:221`, default
`DEFAULT_LANGUAGE="vi"`), resolved through the chain at `chat_stream.py:263` → `graph_assembly.py:196` →
`state["language"]` → `query_graph.py:_lang()` (480-495) → DB-backed `LanguagePack`. The query-side VI tokenizer
(`shared/vi_tokenizer.py`) is correctly gated on `VI_DOMAIN_LANGUAGES=("vi",)` everywhere — EN/ZH/JP queries skip
underthesea entirely and skip the VN abbreviation/teencode seed (which contains bare ASCII tokens like `"k"`, `"v"` that
WOULD corrupt EN). Embedders (jina-v3, zembed-1) and rerankers are hosted multilingual with no language-specific
preprocessing. SysPromptAssembler, condense, HyDE, multi-query, superlative-enricher, boilerplate-compression, and
OOS/refuse text are all locale-driven or `vi`-gated. The **one core violation**: `llm_narrate.py` `_BLOCK_PROMPTS`
hardcodes Vietnamese user-prompts for TABLE/FORMULA/IMAGE linearization, and the narrate port/registry/dispatch carry
NO language parameter. Plus a MEDIUM: `get_pack()` silently falls back to Vietnamese for any language not in {vi, en}.

### Finding I1 — llm_narrate.py hardcodes Vietnamese block-prompts — English bot's TABLE/FORMULA/IMAGE chunks narrated with a Vietnamese instruction
- **severity: HIGH** · category: language-hardcode-in-core
- **evidence**: `llm_narrate.py:58-73` `_BLOCK_PROMPTS` — TABLE='Diễn giải bảng/dòng dữ liệu dưới đây thành 1-2 câu
  tiếng Việt tự nhiên...', FORMULA/IMAGE likewise '...thành 1-2 câu tiếng Việt...'. The narrate path carries NO
  language: `narrate(content, block_type)` signature (line 105) has no language arg; `build_narrate(provider, **kwargs)`
  (registry.py:29) and the call site `document_worker.py:492` + `sync.py:471` pass only llm/spec/tenant/trace — never
  the bot language, even though bot_id/tenant_id are in scope and bots.language is resolvable.
- **detail**: When `narrate_then_embed_enabled=True` for a non-VN bot, every TABLE/FORMULA/IMAGE chunk is sent to the
  enrichment LLM with an explicit Vietnamese instruction ('thành 1-2 câu tiếng Việt tự nhiên'). The system instruction
  (line 46-56) says 'Preserve the source language exactly (do not translate)' — but the per-block user prompt directly
  contradicts it by DEMANDING Vietnamese output. Result for an English-corpus bot: the embedded narration text is either
  Vietnamese (degrading EN retrieval — the narration vector lands far from English queries) or the model gets
  contradictory instructions. Violates the domain-neutral / multi-language mandate and the module's own docstring claim
  (line 25-27 'Domain-neutral'). Self-verified by re-reading lines 58-73 (VN literals present) and tracing the call
  chain build_narrate→LLMNarrateGenerator.__init__ (88-99, no language field) → narrate (105-182, routes only on
  block_type). Feature is default-OFF (DEFAULT_NARRATE_THEN_EMBED_ENABLED), which caps blast radius.
- **expert_fix**: Thread bot language into narrate: (1) add `language: str = DEFAULT_LANGUAGE` to
  LLMNarrateGenerator.__init__ and to narrate(); (2) make _BLOCK_PROMPTS locale-driven — pull the per-block
  linearization instruction from language_packs[locale] (new prompt keys narrate_table/narrate_formula/narrate_image,
  alembic-seeded, vi+en) the same way multi_query._resolve_intent_prompt resolves from the DB; for unseeded locales fall
  back to a domain-neutral ENGLISH scaffold rather than Vietnamese. (3) Pass bot language at the two build_narrate sites
  (document_worker.py:492, sync.py:471). Short-term cheap patch: replace the VN _BLOCK_PROMPTS with English/domain-neutral
  scaffolds (the system instruction already says 'preserve source language'). Long-term: language_packs-driven per
  ADR-W1-S10 governance.

### Finding I2 — get_pack() silently falls back to Vietnamese for any language code outside {vi, en}
- **severity: MEDIUM** · category: default-leak
- **evidence**: `i18n.py:402` `PACKS = {"vi": _VI_PACK, "en": _EN_PACK}`; line 405-407 `get_pack(language) ->
  PACKS.get(language, PACKS[DEFAULT_LANGUAGE])` — DEFAULT_LANGUAGE='vi'. `query_graph.py:_lang (488-495)` uses DB rows if
  present, else get_pack(language).
- **detail**: Only `vi` and `en` are seeded in-memory. A bot with language_code='km'/'th'/'fr'/'zh' that has NO
  language_packs DB rows resolves to the Vietnamese pack (grader/condense/understand/rewriter/decompose prompts all in
  Vietnamese) — not English, not empty. So a Khmer or French bot whose locale isn't seeded gets Vietnamese internal
  prompts driving its retrieval/condense/grading. Mitigation: any locale CAN be fully supported by seeding
  language_packs DB rows (DB path takes precedence, lines 488-494) — operator-completeness gap, not an architectural
  block.
- **expert_fix**: Change the in-memory last-resort fallback for unknown locales from DEFAULT_LANGUAGE(vi) to the English
  pack (the de-facto neutral lingua franca for LLM prompts), OR emit a structured warning
  'language_pack_missing_falling_back' so operators see un-seeded locales. Vietnamese-as-fallback is a domain-bias leak
  for a multi-tenant platform.

### Finding I3 — VI tokenizer + abbreviation/teencode seed correctly gated — EN retrieval NOT corrupted (positive verification)
- **severity: INFO** · category: tokenizer-fallback
- **evidence**: `vi_tokenizer.py:156` `if language not in VI_DOMAIN_LANGUAGES: return text`; line 326-329
  expand_abbreviations skips the VN ASCII seed for non-VN; get_abbreviations:427 seeds only when language=='vi';
  _init_tokenizer (70-114) degrades to lowercase fallback on ImportError/AttributeError. VI_DOMAIN_LANGUAGES=('vi',).
- **detail**: The live tokenizer is `shared/vi_tokenizer.py` (`infrastructure/tokenizer/*` is DEAD CODE — both carry a
  2026-06-03 DEAD-CODE NOTICE header and are fully commented out). underthesea is invoked ONLY for vi; non-VN bots
  return text unchanged, so EN/ZH/JP BM25 tokens are never shattered and the VN teencode seed never rewrites English
  words. Graceful null-fallback. Correct multi-language design — flagged as positive evidence offsetting the narrate
  violation.
- **expert_fix**: None needed. Optionally delete the dead infrastructure/tokenizer/ files.

### Finding I4 — OOS/refuse + sysprompt-default-rules + multi-query/condense/superlative all locale-driven (positive verification)
- **severity: INFO** · category: refuse-text-origin
- **evidence**: `retrieval_filter.py:184-185` gate 'NEVER injects refuse text' — bot's oos_answer_template emitted
  downstream (sacred #10 honored). `sysprompt_assembler.py:131-148` _fetch_platform_rules(locale) reads
  language_packs[locale][sysprompt_default_rules], _resolve_locale = explicit>bot.language>DEFAULT_LANGUAGE.
  `multi_query_expansion.py:66-77` _resolve_intent_prompt resolves (intent,language) from language_packs DB table
  (alembic 0099). `superlative_context_enricher.py:184-195` language-gated regex pack. `prompt_compression.py:62-73`
  DEFAULT_BOILERPLATE_PATTERNS_VI applied ONLY when language=='vi'. condense_question.py:69 _pack=_lang(state).
- **detail**: DEFAULT_LOADTEST_REFUSE_PATTERNS (Vietnamese phrases) is imported ONLY by scripts/ load-test scorers —
  never in src/ runtime, so it does NOT inject/override answers. i18n.py _VI_MQ_* prompts are the vi-pack boot fallback
  only; _EN_PACK has its own English equivalents; DB path overrides both. SysPromptAssembler append is the single
  governed exception (ADR-W1-S10) and is locale-keyed. All sub-questions PASS.
- **expert_fix**: None. Ensure new prompt surfaces (incl. the narrate fix) go through the same language_packs path.

**Bottom line on the 5 questions:** (1) Language IS per-bot config through the chain — EXCEPT `llm_narrate.py` which
hardcodes Vietnamese in core. (2) Embedding+rerank handle EN+VI (hosted multilingual). (3) VI tokenizer falls back
gracefully and is gated — EN retrieval is NOT corrupted. (4) Refuse/OOS text comes from `bots.oos_answer_template`,
never injected; boilerplate VN is `vi`-gated. (5) SysPromptAssembler `language_packs[locale].sysprompt_default_rules`
IS locale-driven.

---
---

# SECTION J — COST / PERFORMANCE / LATENCY (independent agent, full report) — grade B−

**Summary (verbatim):** The pipeline is a 20-node LangGraph with genuinely good per-intent gating and a well-built
2-tier cache. Per-intent skipping is correct for rewrite/decompose/reflect (factoid/greeting skip all three). The 2-tier
cache (L1 exact-hash Redis + L2 pgvector cosine @0.97) is correctly tenant+bot+corpus_version+bot_version scoped — no
cache-key versioning bug found, no cross-tenant leak. `asyncio.gather` is used in all the right independent-await places
(check_cache, retrieve fan-out, guard_output, pre-retrieval triple, MQ expansion). **No Rule-7 shared-session violation
exists** — `PgVectorStore` opens a fresh `session_with_tenant()` per call and gather child-tasks get isolated ContextVar
copies (verified `engine.py:135-168`, `pgvector_store.py:108,396`). But against the 5-criteria target the verdict is
harsh on T1 (p95<1s for factoid): a **factoid by default still pays rerank + a batched grade-LLM call (unless
top_score≥0.7) + generation + a SYNCHRONOUS grounding-judge LLM call before persist**. That last call is the dominant
hot-path cost the codebase already has a fix for (async grounding) but ships OFF. Two more cost-wins
(`speculative_retrieve`, `multi_query_speculative`) and CAG/LSH-proximity are all OFF/dead. Cache-hit ≥30% target
**cannot be verified from code** (rule #0) — no measurement was run; L2 @0.97 is very strict and L1 is exact-hash, so
real-world hit-rate is unproven.

### Finding J1 — Synchronous grounding-judge LLM call on the hot path for factoid/comparison/aggregation/multi_hop, while the async-off-p95 path exists but ships OFF
- **severity: HIGH** · category: T1/T2 latency+cost
- **evidence**: `guard_output.py:68` `_grounding_enabled = DEFAULT_GROUNDING_CHECK_ENABLED(True)`; :89-95
  `_grounding_eligible = _current_intent in _grounding_intents`; constants _15:112-117
  DEFAULT_GROUNDING_INTENTS=(factoid,comparison,aggregation,multi_hop); constants _14:212
  DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=False; _14:222 DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS=('factoid',). Sync judge
  fired as awaited task: `guard_output.py:347-358` `grounding_task = create_task(OutputGuardrail.llm_grounding_check(...))`
  then `await asyncio.gather(regex_task, grounding_task)`.
- **detail**: For the most common intent (factoid) the pipeline blocks the user response on a full grounding-judge LLM
  round-trip BEFORE persist. The parallel guard flag (default ON) only overlaps it with the cheap regex check — it does
  NOT remove it from p95. The codebase already built the correct fix: `_schedule_grounding_check_background`
  (query_graph.py:848-896) fires the judge as a fire-and-forget asyncio.Task AFTER the response ships, and `factoid` is
  already in the async-eligible set. It is simply disabled by default. Net effect: every grounded factoid pays ~1-2.5s
  of judge latency on p95 that the code is designed to move off-path.
- **expert_fix**: Flip DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED → True for the factoid intent (the async set already scopes
  it to factoid only, the safest intent). HALLU=0 preserved because the async judge still logs breaches at WARNING for
  out-of-band alerting; sync grounding stays for comparison/aggregation/multi_hop where a wrong number is higher-risk.
  Measure factoid p95 before/after (rule #0) — do not claim the win until a load-test shows it.

### Finding J2 — Two shipped cost-win flags (speculative_retrieve, multi_query_speculative) and both CAG + LSH-proximity caches are OFF/dead
- **severity: MEDIUM** · category: T2 cost+perf — built-but-not-enabled / dead infra
- **evidence**: constants _20:129 DEFAULT_SPECULATIVE_RETRIEVE_ENABLED=False; _20:145
  DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED=False; _20:23 DEFAULT_CAG_MODE_ENABLED=False / _20:28
  DEFAULT_CAG_PROVIDER='null'; `infrastructure/cag/*.py` and `infrastructure/proximity_cache/*.py` are 100%
  commented-out with 'DEAD-CODE NOTICE 2026-06-03' headers, never imported in bootstrap.py. ON-by-default wins
  confirmed: DEFAULT_PIPELINE_PARALLEL_{CACHE_UNDERSTAND,REWRITE_MQ,OUTPUT_GUARDS}_ENABLED=True,
  DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED=True, DEFAULT_PIPELINE_MULTI_QUERY_EMBED_BATCH_ENABLED=True.
- **detail**: The parallelization wins ARE on by default — good. But speculative retrieve (race embed+hybrid_search of
  the raw query against understand/rewrite to skip the 2nd retrieve round-trip) and speculative MQ expansion are OFF,
  and the two whole-corpus-in-prompt / LSH approximate caches are dead code. This is the gap between 'capable of low
  cost' and 'configured for low cost'. plain speculative_retrieve only reuses chunks when cosine(raw,rewritten) >= 0.85
  (retrieve.py:599-611) so it is answer-neutral.
- **expert_fix**: A/B speculative_retrieve per-bot first (lowest-risk, answer-neutral). Leave CAG/LSH dead unless a
  corpus-fits-in-context bot motivates CAG. Do not bulk-enable; measure each. No lift may be claimed without a load-test.

### Finding J3 — Retrieve multi-query/decompose fan-out gather is unbounded over the variant list (Rule 6)
- **severity: MEDIUM** · category: T2 perf — DB pool spike under concurrency (Async Rule 6)
- **evidence**: `retrieve.py:1307` `results = await asyncio.gather(*[_run_hybrid_for_query(q,...) for i,q in
  enumerate(queries)], return_exceptions=True)` with NO semaphore; same pattern at `retrieve.py:1381` (relax retry).
  Each _run_hybrid_for_query → vector_store.hybrid_search → fresh `session_with_tenant(self._sf,...)`
  (pgvector_store.py:396, engine.py:168). Variant bound: constants _11:232 DEFAULT_MULTI_QUERY_MAX_VARIANTS=7;
  decompose _14:97 DEFAULT_DECOMPOSE_MAX_SUB_QUERIES=5. DB pool: constants _05:52 DEFAULT_DB_POOL_SIZE=20, pool_timeout=30s.
- **self-verify**: Confirmed independent — each gather child opens its OWN session via async_sessionmaker (NOT shared, so
  Rule 6 not Rule 7). Bound ≤7 (max_variants) / ≤5 (decompose), multiplied by concurrent requests.
- **detail**: Per request the fan-out grabs up to 7 pool connections simultaneously. At pool_size=20, ~3 concurrent
  multi-query requests saturate the pool and the 4th waits up to 30s (pool_timeout) — a tail-latency cliff under load,
  not visible in single-request testing. Only bites multi_hop/comparison/aggregation intents; factoid/greeting issue 1
  query so unaffected.
- **expert_fix**: Wrap the fan-out with a bounded `asyncio.Semaphore(DEFAULT_RETRIEVE_FANOUT_CONCURRENCY_N)` exactly like
  `grade.py:322` already does (`_grade_sem = asyncio.Semaphore(max(1, crag_grade_concurrency))`). Add the constant
  (zero-hardcode). The grade node is the model to copy.

### Finding J4 — Rerank always runs after retrieve for factoid/greeting unless the bot configures rerank_skip_intents — the per-intent rerank skip defaults empty (inert)
- **severity: MEDIUM** · category: T1/T2 — node adds latency/cost on cheap intents by default
- **evidence**: `routing.py:219-241` `_retrieve_route` returns 'rerank' for any intent with chunks (only 0-chunks or
  stats-mode escape to generate). `rerank.py:134` `_skip_set_raw = _pcfg(state,'rerank_skip_intents', ()) or ()` —
  default empty tuple ⇒ skip gate inert. The intent whitelist (rerank.py:111) is also None by default. Contrast with
  rewrite/reflect which DO skip by default via DEFAULT_SKIP_REWRITE_INTENTS=(factoid,greeting,out_of_scope) and
  DEFAULT_SKIP_REFLECT_INTENTS=(factoid,greeting,feedback,chitchat,vu_vo,out_of_scope) — constants _01:247-261.
- **detail**: rewrite and reflect correctly skip for factoid/greeting out of the box; rerank does NOT — its skip set
  ships empty so a factoid pays the reranker call (Jina/Cohere API or local) on every turn even when the top retrieved
  chunk is already obviously the answer. The cliff/threshold filter still runs, but the reranker network call is
  incurred. Inconsistent with the rewrite/reflect default posture.
- **expert_fix**: Seed DEFAULT_RERANK_SKIP_INTENTS = ('greeting','chitchat','vu_vo','out_of_scope') (NOT factoid —
  factoid benefits from rerank precision on price/number lookups). Mirrors the existing reflect/rewrite default pattern.
  Measure refuse-rate + HALLU unchanged before claiming.

### Finding J5 — L2 semantic cache @0.97 + L1 exact-hash: correctness verified, but real cache-hit ≥30% is UNVERIFIED and the threshold is strict
- **severity: LOW** · category: T2 cache — correctness good, hit-rate unproven (rule #0)
- **evidence**: `semantic_cache.py:117/172/199` threshold=SEMANTIC_CACHE_THRESHOLD=0.97. L2 slow-path SQL filters
  record_bot_id + record_tenant_id + bot_version + corpus_version + expires_at (semantic_cache.py:475-497). L1 fast-path
  = SHA-256 of strip().lower(query) with same scope (:415-435). bot_version = _compute_bot_cache_version(system_prompt,
  oos_template, custom_vocabulary) (check_cache.py:70-76) — edits bust the key correctly.
  DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC=True, SKIP_MULTI_TURN=True.
- **detail**: No cache-key versioning bug: corpus_version + bot_version + tenant + bot all in the key, so
  stale-after-reingest and stale-after-prompt-edit both handled (M19 lesson encoded). The risk is purely hit-RATE:
  exact-hash L1 only hits on byte-identical queries; semantic L2 @0.97 cosine is very tight (paraphrases at 0.95 miss).
  Numeric + multi-turn queries skip cache entirely. For a Vietnamese RAG bot with high query diversity, 30% hit-rate is
  plausible only if traffic is repetitive — unmeasured.
- **expert_fix**: Instrument cache_status hit/miss counters into the existing structlog perf_timer/audit stream (already
  present at check_cache.py:91 cc_ctx.set_metadata(hit=...)) and read the real ratio from a load-test or 7-day rollup
  before tuning. If hit-rate <30%, consider per-bot lowering L2 threshold to 0.95 for non-numeric intents ONLY (numeric
  stays skipped — a 0.95 match on '500k vs 700k' would serve a wrong number, breaking HALLU=0). Do NOT lower globally.

### Finding J6 — Circuit breakers fail LOUD rather than degrade — correct for LLM/DB, but verify the LLM breaker has a failover sibling
- **severity: LOW** · category: T2 resilience
- **evidence**: `retry_policy.py:195` `__enter__` raises CircuitBreakerOpen when OPEN+cooldown-not-elapsed; defaults
  fail_max=5, reset=30s, adaptive step=15s cap=120s. `failover_orchestrator.py` fans LLM breakers per provider_code
  ('llm:anthropic' vs 'llm:openai' separate instances). DEFAULT_CIRCUIT_BREAKER_ENABLED=True.
- **detail**: Per-provider LLM breakers are the right design. Correct per CLAUDE.md graceful-degradation: transport
  errors degrade, but a fully-OPEN LLM breaker on the answer path WILL fail the turn loud — acceptable since you cannot
  answer without an LLM. No bug; flagged so the failover-to-alternate-provider wiring is confirmed exercised.
- **expert_fix**: No change required. Confirm (via a fault-injection test) that dynamic_litellm_router catches
  CircuitBreakerOpen on the primary provider and retries the bound alternate before surfacing failure. If it does not,
  the per-provider breaker fan-out buys nothing on the answer path.

**Cross-cutting notes (evidence-backed):**
- **Per-intent gating trace (factoid):** `understand_query → _understand_query_route` (or query_complexity if L1
  enabled) → `_router_route` factoid∈skip_rewrite ⇒ **retrieve** (skips rewrite+MQ rewrite, skips decompose) →
  `_retrieve_route` ⇒ **rerank** (NOT skipped by default) → mmr_dedup → neighbor_expand (no-op, default OFF) → grade
  (batch 1-call, or skipped if top_score≥0.7) → generate → critique_parse (no-op, self_rag OFF) → guard_output
  (**sync grounding judge — factoid∈grounding_intents**) → `_output_blocked` factoid∈skip_reflect ⇒ **persist**. So
  factoid correctly skips rewrite/decompose/reflect/self-RAG/neighbor-expand, but NOT rerank and NOT the sync grounding
  judge. Those two are the T1 p95 levers.
- **LLM-call count per factoid turn (default config):** understand(1) + [rewrite skipped] + rerank-API(1) +
  grade-batch(0 or 1) + generate(1) + grounding-judge(1) = **4-5 model/API calls**. The grade smart-skip (≥0.7) and
  async-grounding (if enabled) would drop this to 3.
- **No Rule-1 sequential-independent-await bug found** in the hot paths inspected — the independent awaits are already
  gathered.
- **Dead code:** `infrastructure/cag/` and `infrastructure/proximity_cache/` are fully commented out (2026-06-03
  notice) — not a perf bug, but they inflate the apparent cost-win surface; either wire or delete.

---
---

# APPENDIX — Deliverables & disposition

- **2 agent skills** (`.claude/commands/`): `/rag-flow-debug` (deep-debug any flow, 4-phase evidence protocol + sacred
  self-audit) · `/doc-format-control` (10-format + table-shape taxonomy, happy-case contract, checker/normalizer).
- **This report** (full detail, no summarization).
- **Nothing in code was modified.** Every fix above is a proposal at the correct layer with an A/B metric to run
  (HALLU must stay 0; p95 ceilings) — per CLAUDE.md rule#0, no lift is claimed without a load-test.

---
---

# SYNTHESIS — consolidated findings, roadmap, direct answers

## S.1 Master findings (deduped across all 10 sections, by severity)

### 🔴 CRIT
| ID | Flow | Finding | Evidence |
|---|---|---|---|
| B-1 | AdapChunk | New LLM Strategy Selector orphan — 0 production callers | `infrastructure/chunking_strategy/*` built `230d041/8371017`, grep src/ = 0; U4 calls `select_strategy()` `ingest_stages.py:538` |
| B-2 | AdapChunk | Block-pipeline no-op (parser returns `blocks=None`) → registry formats text-flatten | `document_parser_port.py:29-38`; parsed_blocks set only `document_worker.py:431` (OCR) |
| G-1 | Cost-log | Streaming answer emits no `token_ledger` row | `dynamic_litellm_router.py:790-991` (0 emit) vs `:756` |
| G-2 | Cost-log | Only `jina` embed/rerank emit (per-adapter, not Port boundary) | only `jina_embedder.py:328` + `jina_reranker.py:282` |

### 🟠 HIGH
| ID | Flow | Finding | Evidence |
|---|---|---|---|
| A-I1 | Ingest | Worker path skips byte-sniff (`detect_parser` not robust) | `document_worker.py:379`; ingest `raw_bytes=None` `:544-557` |
| A-I2 | Ingest | octet-stream XLSX/CSV/PPTX URL misroutes to DOCX in OCR fallback | `kreuzberg_parser.py:105` `_suffix_for_mime` |
| B-3 | AdapChunk | Atomic-block protection default OFF + `smart_chunk_atomic` orphan | `_00_app_env_taxonomy.py:95`; `__init__.py:653` 0 callers |
| B-4 | AdapChunk | L3 DocumentProfile entity computed but not fed to selector | `ingest_stages.py:595-602` comment |
| D-A1 | Chat | Streaming SSE answers invisible to cost ledger (= G-1) | `dynamic_litellm_router.py:806-809` |
| D-B1 | Test-chat | Destructive endpoints ungated (`reinit-bots` wipe, `DELETE /chat`, `DELETE /bots`) | `monitoring_routes.py:55-97`, `chat_routes.py:1182-1222`, `bot_admin_routes.py:444-508` |
| E-1 | Retrieval | Entity-fairness RRF dead code → comparison coverage gap | `rrf_round_robin.py` 0 prod callers |
| F-1 | Multi-tenant | IDOR-write: document/conversation save() PK-only guard | `document_repository.py:96-114`, `conversation_repository.py:158-186` |
| G-3 | Cost-log | No per-ws/tenant rollup over token_ledger; 3 overlapping cost stores | `token_ledger_analytics_repository.py` (1 method) |
| G-4 | Cost-log | Non-stream emit sites disagree on duration/purpose | `dynamic_litellm_router.py:771-772` vs `:1115-1117` |
| G-5 | Cost-log | 3 cost stores, none authoritative, no reconciliation | `model_invocations` / `request_logs` / `token_ledger` |
| I-1 | Multi-lang | `llm_narrate.py` VN-hardcoded prompts + no language param | `llm_narrate.py:58-73` |
| J-1 | Cost/perf | Sync grounding judge on factoid hot path (async built but OFF) | `guard_output.py:347-358`; `DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=False` |

### 🟡 MED (consolidated)
A-I3 legacy .doc/.xls unsupported · A-I4 late_chunking whole-doc in-memory · B-5 narrate mis-types block when ON ·
C-1 `price_buoi_le` literal in `jsonb_conversation_state.py:200` · D-A2 tenant strictness divergence ·
D-B2 harness never-external convention-only · E-2 bm25_flags=5 ×3 · E-3 safety-net stamp vs CRAG floor ·
F-2 job_repo no tenant fence when None · F-5 RLS CREATE POLICY DDL absent from git · G-6 no request_id on rows ·
G-7 ledger drops silent · G-8 purpose not in breakdown whitelist · H-1 pipeline_config bare-literal fallbacks ·
I-2 get_pack VN fallback for unseeded locales · J-2 speculative/CAG OFF/dead · J-3 unbounded fan-out gather (Rule 6) ·
J-4 rerank_skip_intents default empty.

### 🔵 LOW (consolidated)
A-I5 registry parsers no typed Block (= upstream of B-2) · A-I6 ocr_factory silent fallback vs docstring ·
B-6 dynamic __import__ guard · C-2 stale math-lockdown docstrings · C-3 _extract_locked_prices in generate node ·
D-A3 bypass-probe silent except · D-B3 harness VN string · E-4 HyDE/query_router dead stubs · E-5 gated latency nodes ·
F-3 request_steps PK-only workspace inherit · F-4 stats_index RLS-blind · F-6 D2 workspace quota cascade unmet ·
J-5 cache-hit ≥30% unverified · J-6 circuit-breaker failover to verify.

## S.2 The single highest-leverage fix
**A-I5 / B-2 are the same root**: the registry parser emits `list[dict]`, not a typed Block list. **One upstream
parser-adapter change (emit Block stream)** simultaneously: unblocks AdapChunk L2 (B-2), enables atomic-protect (B-3),
fixes narrate mis-typing (B-5), and feeds L3→L4 (B-4). This is the "REWRITE cục bộ 1 module parser adapter" the program
charter explicitly sanctions. **Do this first for T1.**

## S.3 Roadmap toward 5-criteria Expert RAG (T1 > T2 > T3)

**T1 — ĐÚNG/ĐỦ (smartness)**: (1) parser→Block-list (A-I5/B-2) → then atomic-protect (B-3) + feed L3→L4 (B-4);
(2) wire-or-delete orphan LLM selector (B-1); (3) wire entity-fairness RRF for comparison/multi_hop (E-1); (4) fix
`llm_narrate` VN-hardcode (I-1) before enabling narrate for any non-VN bot; (5) keep generation's clean sacred-10.

**T2 — Cost/Perf/UX**: (1) close streaming ledger hole + Port-boundary emit (G-1/G-2) + ws/tenant/time-range rollup
(G-3, see Section G reporting_design); (2) flip async grounding for factoid (J-1) — measure p95 first; (3) bound the
multi-query fan-out gather (J-3); seed rerank_skip_intents (J-4); A/B speculative_retrieve (J-2); (4) lift
pipeline_config bare literals (H-1).

**T3 — Hardening**: (1) RBAC + env-gate the test-chat harness (D-B1/D-B2/D-A2); (2) IDOR-write fence on
document/conversation save (F-1); (3) re-assert RLS policies in a git migration + pin with pg_policies test (F-5);
finish RLS Phase-3 DSN flip; route stats_index reads through session_with_tenant (F-4).

## S.4 Direct answers to your questions
- **"Code còn hardcode/support riêng 3 bot?"** → **KHÔNG** (Section H: 8.5/10, 0 real per-bot branch in core, 36
  false-alarms dismissed). The look-of-hardcode = dense VN forensic comments + value-shape heuristics. The only
  surviving domain literal is `price_buoi_le` as a legacy backward-compat dict-key fallback (C-1) — not control flow.
- **"Luồng chunking/upload ngon chưa?"** → upload canonical path solid but worker type-detection asymmetric (A-I1/A-I2);
  **chunking runs flat-text** because AdapChunk L2/L4/L6/L7 are OFF/orphan (Section B). The framework is right, the
  switches are off.
- **"Luồng trả lời ngon chưa?"** → generation is the **most compliant** flow, A− 9.2 (Section C, sacred-10 9 PASS/1
  minor FAIL); retrieval expert-DI (Section E). Gaps are coverage (E-1) + p95 (J-1), not correctness/HALLU.
- **"Log token/cost per bot→workspace→tenant→admin?"** → schema is rich and ready, but emit is incomplete (G-1/G-2) and
  rollup is per-bot only (G-3). Section G reporting_design is the full plan (SQL + endpoints + RBAC + reconciliation).
- **"Multi-tenant/workspace/language?"** → 4-key + read-isolation solid (Section F, no read-leak); IDOR-write +
  inert-RLS-not-in-git are the gaps; workspace quota-cascade half-wired (F-6); language per-bot correct except I-1/I-2.

## S.5 Total agents used
~13 spawned; 10 produced flow reports = the 10 sections above (3 re-run as full-detail agents to replace the earlier
short workflow-reader traces; 7 independent full agents). The stopped-workflow verify-agents were NOT re-run — each
full agent self-verified its own CRIT/HIGH at file:line.

---
---

# SESSION REMEDIATION LOG — Agent 1 (INGEST / UPLOAD) · 2026-06-23

> What was actually DONE this session on the Ingest flow: which plans, which 22 files were read,
> which files were modified and exactly what changed, the unused-code disposition (NOTED, not deleted),
> and the expert/clean-code/perf/tech-debt verdict. All claims verified (rule#0).

## A. Plans / docs used
- `plans/20260623-expert-remediation/plan.md` — MASTER (all 10 flows, bug-fix + clean-code combined, 6 waves T1>T2>T3, done-criteria + verification gate).
- `plans/20260623-ingest-flow-clean/plan.md` — comment-standardization + clean-code plan.
- `docs/dev/INGEST_FLOW_LATEST.md` — latest flow + per-file expert scorecard (/10).
- `docs/dev/INGEST_FILE_BY_FILE_REVIEW.md` — 4-axis per-file review (functional · comment · clean/OOP/pattern · dead-code).
- `reports/INGEST_UNUSED_FUNCS_20260623.md` — dead-code report.

## B. Files READ by the Ingest agent (22)
HTTP entry: `documents.py · documents_stream_upload.py · sync.py · http/router.py` ·
Use case: `use_cases/ingest_document.py` · Worker: `interfaces/workers/document_worker.py` ·
Service U1–U7: `document_service/ingest_core.py · ingest_stages_store.py · document_service/__init__.py` ·
Parsers: `parser/registry.py · docx_parser.py · excel_openpyxl_parser.py · google_sheets_parser.py · kreuzberg_markdown_parser.py · markdown_parser.py · pdf_parser.py · null_parser.py` ·
OCR: `ocr/kreuzberg_parser.py · ocr/ocr_factory.py` ·
Shared: `shared/mime_sniff.py · shared/tabular_markdown.py · application/services/google_link_service.py`.

## C. Files MODIFIED this session + exact change

### C.1 — Bug fix A-I2 (TDD, code-logic change)
| File | Change |
|---|---|
| `shared/mime_sniff.py` | + `_MIME_PPTX` constant + `presentationml → pptx` branch in `_peek_zip_office_subtype` (the sniffer now distinguishes xlsx/docx/**pptx**). |
| `infrastructure/ocr/kreuzberg_parser.py` | `_extract_blocks` now sniffs the REAL mime from bytes (`sniff_real_mime`) and uses it for BOTH the `extract_bytes` mime arg AND `_suffix_for_mime`; `_suffix_for_mime` gained `spreadsheetml→.xlsx` + `presentationml→.pptx` branches. Also cleaned a no-version-ref comment ("Wave I … 2026-05-19" → WHY-only EN). |
| `tests/unit/test_kreuzberg_parser.py` | + 2 regression tests: octet-stream XLSX/PPTX must route to `.xlsx`/`.pptx`, not `.docx`/`.bin`. |
**Result**: TDD red→green; 28 pass, 0 regression, 0 new ruff (kreuzberg 5=5, mime_sniff 3=3). Lifts `ocr/kreuzberg_parser.py` 6.5 → 9.0. *Note: this hardens the OCR-fallback path; the deeper root A-I1 (worker primary path) is queued.*

### C.2 — Comment-standardization (8 files · AST-IDENTICAL = ZERO logic change)
| File | VN→EN | verRef stripped | docstrings added | note |
|---|---:|---:|---:|---|
| `document_service/ingest_core.py` | 15 | 4 | 0 | hot-path U1; AST identical |
| `document_service/__init__.py` | 8 | 9 | 0 | god-file; all 17 fn already had docstrings |
| `services/google_link_service.py` | 9 | ~2 | 0 | pure helper |
| `shared/tabular_markdown.py` | (prose) | ~4 | 4 | 7 VN **data-examples kept on purpose** + EN gloss |
| `routes/sync.py` | 7 | 1 | 0 | "Tầng 6"→"Narrate-then-Embed stage" |
| `routes/documents.py` | 0 | 1 | 3 | "260525 Bug #2"→behaviour-described |
| `http/router.py` | 1 | 5 | 0 | dropped `B.6/D12/G26`/dates |
| `routes/documents_stream_upload.py` | 0 | 1 | DISABLED note | module-top DISABLED docstring added |
**Verification**: AST-compare (docstrings stripped) HEAD vs now = **IDENTICAL for all 8** → mathematically only comments/docstrings changed. 1342 passed. The 3 collection errors + 3 route-test failures are **PRE-EXISTING** (reproduce on clean HEAD with all changes stashed — FastAPI `_EffectiveRouteContext` import mismatch in `tests/unit/_helpers_routes.py`); this session introduced **0 new failures**.

## D. Unused / dead code — NOTED, NOT DELETED (per instruction "chưa phải lúc xóa")
- **0 truly-dead local functions** in the 22 files. The 3 naive "0-call-site" candidates are LIVE and were **NOT commented out** (doing so would break the app / interface):
  - `rechunk_document_by_id` (`documents.py:277`) — FastAPI route handler (framework-invoked).
  - `delete_documents` (`sync.py`) — FastAPI route handler.
  - `supported_mimes` (`ocr/kreuzberg_parser.py:154`) — `OCRPort` Protocol method (polymorphic contract; 3 impls).
- **Module-level disabled/orphan (retained + documented, NOT deleted):**
  - `documents_stream_upload.py` — DISABLED (not mounted in `router.py`, no Redis consumer); now carries a DISABLED module docstring.
  - `infrastructure/chunking_strategy/` (LLM selector, B-1) · `smart_chunk_atomic()` · `rrf_round_robin()` — orphan elsewhere in the codebase; tracked in `reports/INGEST_UNUSED_FUNCS_20260623.md`, dispositioned by ADR, not deleted now.

## E. Language scope — clarification HONORED (no confusion)
- **Code inline comments / docstrings → ENGLISH** (this session's work).
- **The BOT's supported-language scope stays Vietnamese + English** — UNTOUCHED. No edit to `language_packs`, `bots.language`, `i18n.py` runtime strings, or any user-facing template. Comment-language ≠ bot-language-support.
- `tabular_markdown.py` deliberately keeps **7 Vietnamese data-example tokens** in comments (e.g. `"6 triệu"`, `"1tr499"`, `"Giá: …"`) — they document the exact byte-shapes the shape-based matcher parses; translating them would make the examples technically wrong. English glosses added.

## F. 22-file expert verdict — does any need logic / flow rewrite?
- **EXPERT — leave logic** (15): `ingest_document` · `registry` · `null_parser` · `mime_sniff` · `documents` · `router` · `ingest_core` · `__init__` (logic) · `google_link_service` · `tabular_markdown` · `sync` · `markdown_parser` · `pdf_parser` · `kreuzberg_markdown_parser` · `ocr/kreuzberg_parser` (now, post A-I2).
- **FIX — logic/flow change still needed** (5): `document_worker` (A-I1 robust-detect — the root score-dragger) · 4 structured parsers (A-I5 emit typed Block) · `ocr_factory` (A-I6 drift) · `ingest_stages_store` (A-I4 memory bound).
- **No full REWRITE needed** (strangler-fig charter): the ONLY sanctioned local rewrite is the parser→Block emission (A-I5). **No flow redesign** — the canonical 2-action async + Port/Registry framework is correct.

## G. Clean-code · performance · tech-debt verdict
- **Clean-code: EXPERT pattern health** — Port + Strategy + Registry + Null Object + DI applied consistently; shared helpers (`mime_sniff`, `tabular_markdown`) reused, not duplicated. After this session: 0 VN comments (except intentional data examples) + version-refs stripped on the 8 cleaned files.
- **Tech-debt (T3, deferred, behavior-preserving)**: two god-files — `ingest_stages_store.py` (1022 LOC) and `document_service/__init__.py` (999 LOC) — should be split per-stage; `document_worker.py` (692 LOC) does parse-routing that belongs in the service (folds into A-I1). These are debt, **not bugs** — tracked in the master plan.
- **Performance debt (one real item)**: A-I4 — default `late_chunking` embeds the whole-doc chunk list in memory (RSS spike on a 224KB sheet). Bounded-batch fix is queued. No other hot-path perf red flag in ingest (HTTP-layer embed batching + embedder circuit-breaker present).
- **Bottom line**: NOT "still lots of tech debt" — the framework is expert and clean; the remaining work is **5 scoped FIX items + 2 deferred god-file splits**, all tracked, none blocking. The score gap (8.2 architecture vs ~7.0 effective) closes when A-I1 + A-I5 land.
