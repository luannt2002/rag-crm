# RAGBOT INGEST/RAG — FINAL CONSOLIDATED TRUTH & BUILD PLAN (2026-06-27)

> **THE ONE DOC.** Supersedes the 8 theme analyses (each merged ~9 reports) and the 9 underlying reports. Self-contained. Every load-bearing claim in §0/§4/§5/§7 was **re-verified LIVE this session** against `src/` (grep + file:line below). Findings are labelled **SỰ THẬT** (code-evidence, STATIC) vs **GIẢ THUYẾT** (unmeasured, needs runtime numbers) per rule#0. STATIC code-evidence ≠ VERIFIED runtime — nothing here is "fixed/works/pass" until the §8 verify-gate runs.

---

## 0. EXECUTIVE SUMMARY

- **Where we stand:** the framework is **expert-grade** (Hexagonal/DDD · Port+Registry+DI+Null · zero `if provider==` · byte-sniff funnel · 4-key+RLS isolation · async-202 · idempotency · HALLU=0 + sacred-#10 hold on the answer path). The problem is **"dây chưa nối hết"** (wiring unfinished) + a **domain-coupled table/currency/language layer**. **EVOLVE, not rewrite.** [SỰ THẬT]
- **THE ONE LAW** (all 9 sources converge): **structure** — header/table/heading-level/element-type — is decided by **FORM/SHAPE** (separator line, `#`-count, column count, label-vs-value contrast, byte magic), **NEVER by a domain/single-language vocabulary word-list.** We are the **only** reference that gates header-ness lexically. [SỰ THẬT]
- **THE SINGLE P0 CRUX = `col_N` from a dual-oracle header drift.** `document_stats._is_header_row` (`:275-300`) decides header by VOCAB (`_HEADER_EXACT_TOKENS :191-205` + `parse_money_vn :294`); the converter `tabular_markdown._looks_header` (`:90-99`) already decides STRUCTURALLY and emits `| --- |`, but the extractor **throws the separator away** (`document_stats.py:917`) and re-judges by vocab. A non-VN/non-VND header (`MARKS|CARGO DESCRIPTION`, `Producto|Precio`, `Điều|Khoản`) matches nothing → role unbound → positional `col_N`. **Two header oracles = guaranteed drift.** [SỰ THẬT]
- **The fix-seam already exists in our own code.** The correct structural detector (`tabular_markdown._looks_header`) and the locale-as-data convention (`_24_structural_markers_by_lang.py`, keyed vi/en/ja) are already shipped — they are just **bypassed**. Fix = promote `_looks_header` to a shared SSoT + extract a locale-neutral `_is_value_cell` + demote vocab to an optional **HINT**. **No rewrite.** [SỰ THẬT]
- **The HALLU=0-on-numbers keystone is verified BROKEN.** `original_content` read-back in `orchestration/` + `infrastructure/vector/` = **0 hits**; its sole live producer `smart_chunk_atomic` is **test-only** ("Wave B2 will wire", `__init__.py:676`). The narrate-then-embed lever is **inert** today — closing this round-trip is the single highest-value number-HALLU step. [SỰ THẬT]
- **Design decisions RESOLVED:** (1) Header by **structural floor FIRST, vocab as optional HINT** — neither pure-shape (misfires on ID/SKU/year/phone) nor vocab-gated. (2) Strategy routing = **deterministic block-level profile** (already wired); the file-level **LLM Strategy Selector is orphan dead code → DELETE**. (3) Number-HALLU = a **4-layer observe/route-only stack** (narrate+`original_content` → metadata-filter → dual-read → typed-enum verify) + upstream reranker gate. (4) Tabular model = **attribute-generic late-binding** (every column = labelled attribute; price = derived view), **NOT** the convo's single `value_col`/`PRICE_MIN_VND` (that re-introduces the bug).
- **What is STRONGER than every reference (DO NOT REGRESS):** 4-key+RLS isolation (vs convo `bot_id`-only and tldw namespace), X-Idempotency-Key dedupe, single canonical `POST /documents/create`, 3-tier reranker resolver with `system_config` fallback. [SỰ THẬT]
- **Measured counter-evidence (rule#0 honoured):** the convo's headline `key: value` row rendering was **already trialled** (`csv_chunker.py:33`) and measured **neutral-to-slightly-negative** on the price-table load test. The convo's `-90% token` / `+35% Context Precision` / "HALLU=0 guaranteed" are **invented sales-device benchmarks** — quarantine, never repeat as fact. [SỰ THẬT they are unmeasured]
- **What is GIẢ THUYẾT:** every coverage-loss **magnitude** and post-fix **lift** (col_N rate, header-recovery rate, number-HALLU rate, pure-shape false-positive rate on ID/year columns, LLM-selector/LLM-narrate ablation deltas, ekimetrics lift on our VN corpus). None measured. Must run the verify-gate before any "fixed" claim.
- **Lead order:** P0 CRUX trio (structural header SSoT · lossless-coverage assert · de-VN/de-VND the structure path) → P1 wire-the-orphans → P2 polish + convo-NEW. Every step verify-gated.

---

## 1. THE EXPERT MINDSET (unified rules)

1. **THE ONE LAW — structure by FORM, never VOCABULARY.** Header-ness, table-ness, heading-level, element-type are decided by markup tokens, char-shape, column count, separator line, byte magic. A word-list never gates structure. (Vocab is allowed only as an *optional HINT* for column-ROLE, after the structural decision.)
2. **Domain-neutral.** No brand / industry / customer literal anywhere in a structure-deciding path. No `startswith('Chương'/'Điều')`. Brand-grep is clean today — keep it clean.
3. **Multilingual = language as DATA.** Per-locale config packs keyed by ISO code (mirror `_24_structural_markers_by_lang.py`); thread doc/bot language through every structure-deciding function; detect by **Unicode script-range + stopword frequency**, not a VN-diacritic binary. *Add a language = add a config row, not code.*
4. **Currency/scale-neutral.** Detect a VALUE by `\p{Sc}` currency-symbol category + digit-group SHAPE; unit suffixes (tr/trieu/k/M/$/€) live in an injected per-locale unit pack, never baked. No FX/scale assumption.
5. **Block-level adaptive, not file-level.** Real docs are mixed-content (intro=semantic, clauses=proposition, specs=tabular in one PDF). Route per-block on a computed profile.
6. **Two physical lanes.** Prose → vector (cosine). Numbers/tables → **stats/metadata hard filter** (SQL arithmetic). Numbers are NEVER answered by cosine.
7. **Async-first.** API accepts (202) and returns fast; workers parse/chunk/embed. Never parse in the request thread.
8. **Deterministic routing + LLM-local-only.** The router is a **$0 code decision tree on a Document Profile** with a deterministic cross-check/veto. LLM is reserved for **local** ingest tasks (narrate-a-table, propositionize-a-clause), default to the $0 rule narrator, LLM opt-in per-bot. A per-file LLM selector is the **death-trap** (context-window blow + 429s).
9. **Infra-isolation non-negotiable.** 4-key identity `(record_tenant_id, workspace_id, bot_id, channel_type)` at the boundary → `record_bot_id` internally; enforced at **both** app-filter and DB-RLS layers; fail-loud, never silently skip. Never downgrade to `bot_id`-only.
10. **Sacred-#10 — no app-inject / no app-override.** Ingest is deterministic; narration is ingest-time storage, not answer injection; grounding/claims-verify are **observe/score-only**; refusal text from `bots.oos_answer_template`. The whole anti-HALLU stack stays observe/route-only.
11. **Measure, don't guess (rule#0).** STATIC code-evidence ≠ VERIFIED. Every "fixed/lift/HALLU=0" needs an ingest backward-trace + a load-test reporting Coverage + Faithfulness + HALLU=0. Two-track eval: intrinsic = hypothesis, extrinsic = evidence; both must move the same direction before ship.
12. **Lossless-coverage invariant.** A chunker that drops a digit = a fabricated/missing number while Faithfulness reads 1.0 ("honest but blind"). `assert check_chunk_gaps()` after every strategy.

---

## 2. THE TARGET END-TO-END FLOW

```
                                  ┌─────────────────────────────────────────────┐
   B2B caller ──POST /documents/create──▶│ HTTP 202 ACCEPT (<200ms)               │
   (bytes / URL / any format)            │ X-Idempotency-Key dedupe · enqueue     │  documents.py:94,231,270
                                  └───────────────────┬─────────────────────────┘
                                                      │ Redis Streams (XACK/XCLAIM recovery)
                                                      ▼
                                  ┌─────────────────────────────────────────────┐
                                  │ WORKER = thin adapter over DocumentService   │  document_worker.py
                                  └───────────────────┬─────────────────────────┘
                                                      ▼
   ① TYPE DETECT  mime → ext → BYTE-SNIFF  (metadata REFINES, never DICTATES)     skill type-detection-mime-sniff
                                                      ▼
   ② PARSE  detect_parser registry (Port+Strategy) → structured-markdown          parser-adapter-pattern
            born-digital text-layer vs scanned = parse MODE, native bypass OCR
                                                      ▼
   ③ RECONSTRUCT  (the genuine gap) multi-page header propagation across          [P2 — feasibility-gate]
            page-split tables · intra-doc multi-row header merge
                                                      ▼
   ④ BLOCK ROUTING  deterministic Document Profile (headings/tables/markers/lang) select_strategy analyze.py:382
            structural fast-paths (CSV→table, ≥N hier markers→HDT) · cross-check  apply_cross_check analyze.py:551
            ambiguous prose → ekimetrics intrinsic argmax (flag-gated)            ekimetrics_select analyze.py:448-457
                                                      ▼
        ┌──────────────────────────────┬──────────────────────────────────────┐
        ▼ PROSE LANE                    ▼ TABULAR LANE (THE ONE HEADER ORACLE)
   semantic/proposition/HDT chunk       looks_header: separator-law → structural floor → vocab HINT
   assert check_chunk_gaps (lossless)   col{i} ONLY for genuinely blank column (never col_N)
        │                               every column = labelled ATTRIBUTE (late-binding)
        ▼                               narrate-then-embed: embed NL narration (recall)
   embed prose                          persist VERBATIM original_content (exact numbers)
        │                               extract to STATS DB (attribute-generic NUMERIC cols)
        ▼                                       │
   pgvector (cosine)                    stats_index_repository (SQL gt/lt/eq)  [4-key scoped]
        └──────────────┬───────────────────────┘
                       ▼ QUERY
   HYBRID: Vector(...) AND hard-filter(record_bot_id AND attr<X)                 query_range_parser + generic_llm_extractor
   numbers → SQL arithmetic, NOT cosine
                       ▼
   RERANK (3-tier resolver + optional sentinel/decoy gate) → GRADE (CRAG) → topK
                       ▼
   GENERATE: LLM sees embedded narration + DUAL-READ verbatim original_content   [BROKEN today: 0 read-back]
                       ▼
   VERIFY (observe-only): typed-enum claims + numeric-precision + grounding       sacred-#10 — never override
                       ▼
   ISOLATION everywhere: 4-key boundary → record_bot_id internal;
   RLS GUC SET LOCAL app.tenant_id (fail-loud) + mandatory bot-filter raise       engine.py:174 · pgvector_store.py:311,388
```

---

## 3. DEEP FLOW-BY-FLOW ANALYSIS

For each flow: **what it does (file:line)** + **standard-vs-not verdict**.

| # | Flow | What it does (our file:line) | Standard? |
|---|---|---|---|
| 1 | **Ingest accept** | `documents.py:94,231,270` returns `HTTP_202_ACCEPTED`; X-Idempotency-Key dedupe (`:11`); Redis Streams + XACK/XCLAIM recovery in worker | ✅ **STANDARD** — stronger than tldw `media_id`-reprocess + convo `uuid4` upsert |
| 2 | **Type detect** | Canonical funnel does `mime→ext→byte-sniff`; **BUT** worker has a duplicate funnel `document_worker.py:392-468` that skips byte-sniff (`:428`) and lets URL-string dictate mime (`:404-413`) | ⚠️ **PARTIAL** — canonical path OK; worker duplicate funnel NOT standard (FIX) |
| 3 | **Parse** | `detect_parser` registry, Port+Strategy adapters → structured-markdown | ✅ **STANDARD** (parser-adapter is the only permitted rewrite zone) |
| 4 | **Reconstruction** | Intra-doc 2-row header merge `document_stats.py:783 _merge_header_rows`, `:804 _premerge_split_headers`. **No** multi-page header propagation (grep propagat/bbox = 0); merge is gated behind the vocab `_is_header_row` | ❌ **GAP** — genuine net-new; partial credit for intra-doc merge but vocab-gated |
| 5 | **Block routing** | `select_strategy` (`analyze.py:382`) + `apply_cross_check` (`analyze.py:551`), wired `ingest_stages.py:563/576/608`; structural fast-paths CSV→table / ≥N markers→HDT; ekimetrics intrinsic argmax flag-gated OFF (`analyze.py:448-457`) | ✅ **STANDARD** (deterministic+cross-check); ekimetrics is correct upgrade, flag OFF pending VN measure |
| 6 | **Prose chunk** | semantic/proposition/HDT strategies in `shared/chunking/` | ⚠️ **PARTIAL** — strategies OK; **no `check_chunk_gaps`** lossless assert (0 hits) |
| 7 | **Tabular chunk / header** | `_is_header_row` (`document_stats.py:275-300`) vocab-gated; `_looks_header` (`tabular_markdown.py:90-99`) structural but bypassed; separator thrown away (`:917`) | ❌ **THE CRUX — NOT STANDARD** (dual-oracle drift → col_N) |
| 8 | **Narrate** | producers persist `original_content` (`chunking/__init__.py:868,919`); `llm_narrate.py:53` says "preserve source language" but `:60,65,71` hardcode "tiếng Việt" | ❌ **NOT STANDARD** — self-contradicting prompt (FIX) |
| 9 | **Embed** | pgvector dense + sparse; `embedding_cache.py:22-31` key omits provider; `query_graph.py:1351/1257` model default literal `'unknown'` | ⚠️ **PARTIAL** — cache identity incomplete (provider/dim swap poisons) |
| 10 | **Vector store** | `pgvector_store.py:311,388` raise on missing `record_bot_id`; RLS-scoped | ✅ **STANDARD** — isolation stronger than all refs |
| 11 | **Retrieve** | hybrid; structural pre-filter `retrieve.py:1066-1071` never threads bot language; **no `original_content` read-back** (0 hits) | ⚠️ **PARTIAL** — dual-read keystone BROKEN; lang not threaded |
| 12 | **Rerank** | 3-tier `reranker_resolver.py:188,203` with `system_config` fallback; `_modality_boost.py:67-158` mutates score by hardcoded English intent vocab | ⚠️ **PARTIAL** — resolver ✅ stronger than tldw (none); modality-boost is a sacred-#10-adjacent smell |
| 13 | **Grade (CRAG)** | per-bot grader; observe-only | ✅ **STANDARD** |
| 14 | **Query metadata-filter** | `query_range_parser.py` (regex) + `generic_llm_extractor.py` (LLM, Port+DI) + `stats_index_repository.py` (SQL, 4-key scoped) | ✅ **STANDARD lane**, ❌ **price-centric columns** (`price_primary/secondary :20-21`) = de-price needed |
| 15 | **Multi-tenant isolation** | 4-key `(record_tenant_id, workspace_id, bot_id, channel_type)` → `record_bot_id`; RLS GUC `engine.py:174 SET LOCAL app.tenant_id` (fail-loud `:163`); every stats row carries 3 keys | ✅ **STANDARD** — strongest in the whole survey |
| 16 | **Generate** | `guard_output.py:66-69` "application does NOT regex-check + override"; `math_lockdown.py:166,210` observe-only | ✅ **STANDARD** — sacred-#10 holds; but dual-read not fed in |
| 17 | **Eval** | intrinsic `score_chunks_intrinsic.py` (lexical, audit-only) + extrinsic `eval_gate.py` (Coverage + HALLU=0, no-LLM-judge); leaf-embed-coverage state gate `ingest_stages_final.py:194-216` | ⚠️ **PARTIAL** — both tracks exist but **disconnected**; no lossless-coverage / split-point block-integrity gate |

---

## 4. ✅ KEEP LIST — DO NOT TOUCH (anti-regress)

Everything here is standard-mindset + best-practice and **verified live**. Regressing any of these = the most severe error.

| Keep | Why already expert | file:line |
|---|---|---|
| **Port + Registry + Strategy + Null DI** | Open-Closed; add provider = +1 file; zero `if provider==` in orchestration | `application/ports/*`, `infrastructure/*/registry.py` |
| **4-key + RLS isolation (DUAL layer, fail-loud)** | App-filter AND DB-RLS; raises rather than silently skip; stronger than convo `bot_id`-only + tldw namespace | `engine.py:163,174` · `pgvector_store.py:311,388` |
| **X-Idempotency-Key request dedupe** | Correctness invariant convo (`uuid4` upsert dupes chunks) + tldw both LACK | `documents.py:11` |
| **Single canonical `POST /documents/create`** | One funnel for all formats/sources | `documents.py:94` |
| **Async-202 accept + Redis-Streams worker** | API responds, workers process; XACK/XCLAIM crash recovery | `documents.py:94,231,270` · `document_worker.py` |
| **3-tier reranker resolver** (binding → system_config → Null) | tldw has no resolver fallback; honors "shared default + paid override" | `reranker_resolver.py:188,203` |
| **`tabular_markdown._looks_header` structural detector** | The CORRECT shape-based oracle (≥2 label cells, no pure-money, emits `| --- |`) — **promote it, don't rebuild it** | `tabular_markdown.py:90-99` |
| **`_24_structural_markers_by_lang.py` locale-as-data pack** | The correct multilingual convention (vi/en/ja keyed) already in the codebase | `_24_…:24,53` |
| **HALLU=0 / sacred-#10 on answer path** | App does NOT regex-check+override; grounding observability-only; math_lockdown de-fanged (returns lists, never replaces) | `guard_output.py:66-69` · `math_lockdown.py:166,210` |
| **Numeric-answer cache-skip on cosine path** | Semantic cache never serves a wrong-number answer | `persist.py` (extract_numeric_claims gate) |
| **Metadata-filter two-lane (SQL, not Pinecone)** | The right anti-HALLU-on-numbers architecture, already domain-neutral | `query_range_parser.py` · `generic_llm_extractor.py` · `stats_index_repository.py` |
| **Deterministic profile router + cross-check/veto** | $0 ground-truth router; cross-check already implements the Tầng-5 veto the spec wanted | `analyze.py:382,551` · wired `ingest_stages.py:563,576,608` |
| **`original_content` producers + narrate primitive** | The dual-payload primitive is built (just not read back) | `chunking/__init__.py:868,919` |
| **Byte-sniff type detection (canonical path)** | `mime→ext→byte-sniff` rescues octet-stream URLs | canonical funnel |
| **Narrow-except hierarchy + leaf-coverage state gate** | Retrievability floor (loosened 2026-06-20 to avoid permanent-dark on transient 429) | `shared/errors.py` · `ingest_stages_final.py:194-216` |
| **eval_gate.py no-LLM-judge deterministic Coverage+HALLU** | Honors "no ChatGPT scoring" | `eval_gate.py` |

---

## 5. ❌ CUT / FIX LIST — EVERYTHING that is NOT standard

Hold nothing back. Two buckets. All file:line **verified live this session**.

### 5a. CUT — remove / dead-code / forbidden

| # | What | Why it violates standard | file:line | Correction |
|---|---|---|---|---|
| C1 | **Parallel `documents_stream_upload.py` endpoint** (13,925 bytes, present) | CLAUDE.md HEADLESS §2: "CẤM thêm endpoint upload song song … phải gỡ"; data-loss risk; two funnels | `interfaces/http/routes/documents_stream_upload.py` | Remove; fold streaming into canonical `POST /documents/create` |
| C2 | **Orphan file-level LLM Strategy Selector** | 0 runtime callers (`resolve_strategy`/`build_chunking_resolver` referenced only inside its own package + Port def + tests); zero measured benefit; the "death-trap" (context blow + 429s) | `infrastructure/chunking_strategy/{llm_resolver.py,registry.py,rule_resolver.py}` · `strategy_ports.py:78` | DELETE; keep deterministic profile router; `apply_cross_check` already gives the veto |
| C3 | **5 version-ref constant filenames** | No-version-ref TUYỆT ĐỐI (filenames encode Sprint/Wave/Phase/date) | `constants/_13_adapchunk_layer_1_ocr_parser.py`, `_16_prompt_token_squeeze_phase_b.py`, `_17_260509_a1_pipeline_audit_6_c.py`, `_19_sprint3_ekimetrics_selector_.py`, `_21_streaming_upload_wb_2_p1_5.py` | Rename to purpose names (`_ocr_parser`, `_strategy_selector`, `_stats_index`…); strip Sprint/Wave/Phase/date from docstrings (WHY-only) |
| C4 | **SSoT duplication `DEFAULT_PRICE_BUCKETS_VND` / `DEFAULT_PRICE_MIN_VND`** | Defined in BOTH files → drift risk (`_22` comment even references "the canonical" set, proving awareness) | `_21_…:57,64` AND `_09_message_feedback_thumbs_verd.py:141,150` | Collapse to ONE canonical definition, re-export, unit-test identity; rename `PRICE_*_VND`→`VALUE_*` scale-neutral |
| C5 | **Worker duplicate ingest funnel** | Skips byte-sniff (`:428` plain `detect_parser`), URL-string dictates mime (`:404-413`), flattens (`:444-446`) — bypasses canonical funnel | `document_worker.py:392-468` | Collapse worker into a thin adapter over `DocumentService` (byte-sniff included) |
| C6 | **Dead chunk-quality DI module** | Carries `DEAD-CODE NOTICE — 2026-06-03`, commented out, not in `bootstrap.py` — implies a gate that does not run | `infrastructure/chunk_quality/heuristic_chunk_quality_scorer.py` · `shared/chunk_quality.py` | DELETE the dead DI path (or revive behind `system_config` flag with a true split-point block-integrity metric); the live `score_chunk_quality` in ingest is observability-only-by-design — demote to regression canary |

### 5b. FIX — wrong-but-needed (the EVOLVE work)

| # | What is wrong | Why it violates standard/mindset | file:line | Domain-neutral correction |
|---|---|---|---|---|
| **F1** | **Vocab header gate (THE CRUX)** — `_is_header_row` returns header-ness from `_HEADER_EXACT_TOKENS` (~70 VN/EN words) + rejects any money cell via `parse_money_vn` | Header-ness by VOCAB violates THE ONE LAW; non-VN/non-VND header → col_N. We are the only ref that does this | `document_stats.py:191-205,275-300,294,298,917` | Extract locale-neutral `_is_value_cell` (`\p{Sc}`+digit-shape, per-locale unit pack HINT); promote `_looks_header` to shared SSoT (separator-law → structural floor → vocab HINT); trust the `\| --- \|` separator; rename `parse_money_vn`→`parse_amount` |
| **F2** | **Single-currency value-cell test** — `_MONEY_UNIT_RE` (`triệu\|nghìn\|vnd\|tr\|đ\|k\|m`) + `_is_pure_money` gate the table/row classifier; two divergent money tests (`parse_money_vn` vs `_is_pure_money`) | Single-VND coupling; "tr" IS a VN word (triệu); both 500k and $500→500000, no FX/scale; guaranteed drift | `tabular_markdown.py:40-43,57-67` · `number_format.py:46 _SUFFIX_MULT` (baked `Final` dict) | One currency/scale-neutral value test shared by both oracles; lift `_SUFFIX_MULT` into per-locale config pack; VND = seeded default 'vi' pack |
| **F3** | **Hardcoded VN narrate prompts** — line 53 "Preserve the source language exactly" vs `:60,65,71` "tiếng Việt" three times | Self-contradicting wired prompt; Spanish/English doc narrated into Vietnamese → cross-lingual recall drop | `llm_narrate.py:53,60,65,71` | Source `_BLOCK_PROMPTS` from per-locale pack; body says "reply in the source/document language" |
| **F4** | **`strategy_used="SEMANTIC"` literal for EVERY doc** | Record-of-truth bug; masks real per-doc strategy from telemetry; needed to interpret any ablation | `document_worker.py:623` | Surface resolved `_chunking_strategy` out of `ingest()` (IngestResult.strategy_used) into `DocumentIngested` |
| **F5** | **BROKEN `original_content` dual-read** — 0 read-back at retrieval/generate; sole producer `smart_chunk_atomic` test-only | The narrate-then-embed HALLU=0 lever is **inert**; verbatim number never reaches the LLM | `chunking/__init__.py:653,676,868` · `orchestration/nodes/{retrieve,generate}.py` (0 hits) | Wire `smart_chunk_atomic` as live Block→Chunk producer; read `original_content` back (fenced, config-gated, domain-neutral) at generate |
| **F6** | **Missing lossless-coverage invariant** — `check_chunk_gaps` = 0 hits | A dropped digit/span = silent number-HALLU while Faithfulness reads 1.0 ("honest but blind") | `shared/chunking/` (whole pkg) | Emit `(start_char,end_char)` per chunk; `assert check_chunk_gaps()` + `repair_gaps` as L6 exit gate after EVERY strategy; pin unit test |
| **F7** | **Price-centric stats DB** — `price_primary`/`price_secondary` NUMERIC columns; single-value-col model | Re-introduces the col_N bug; can't serve non-priced/non-VND corpora | `stats_index_repository.py:20-21,97-98,131-132` · `document_stats._column_roles` | Attribute-generic late-binding: every column = labelled numeric/text attribute; price = one derived view; keep 3-key scoping |
| **F8** | **Multilingual single-VN structure path** — `has_toc 'mục lục'` (`analyze.py:278,346`); `detected_language` VN-vs-`'auto'` binary (`rule_based_doc_profile.py`); `vn_structural._STRUCT_MARKERS` import-bound to the vi slot (`:55-56`), call sites pass no locale → en/ja dicts unreachable | Single-language coupling; the correct by-lang pack exists but is bypassed | `analyze.py:278,346` · `rule_based_doc_profile.py:58-69` · `vn_structural.py:55-89` | Thread doc/bot language; detect by script-range; resolve markers per-call-locale; structural TOC (dotted-leader + page-number) not literal |
| **F9** | **`_modality_boost` reranker score override** — mutates reranker SIGNAL by hardcoded English intent vocab | Sacred-#10-adjacent (app override of model signal); single-language coupling on a relevance path | `_modality_boost.py:67-74,130-158,192-206` | Move intent→boost map to `system_config`, gate behind per-bot flag default-OFF + audit pre/post delta; unit-test byte-identical when OFF, or remove |
| **F10** | **Incomplete embed/semantic-cache identity** — key omits provider; model default `'unknown'`; semantic cache not scoped by provider/model/dim | Provider/dim swap serves stale cross-distribution vectors | `embedding_cache.py:22-31` · `query_graph.py:1351,1257` · `semantic_cache.py:417-435` | Complete identity tuple `provider:model:sha256(text)[:base_url]`; scope semantic cache by provider/model/dim |

---

## 6. RESOLVED DESIGN DECISIONS

### 6.1 Shape-detector canonical algorithm (the col_N fix, incl. the k/tr/m fix)

**Verdict: structural floor FIRST, vocab as optional HINT.** Neither pure-shape (`numeric_ratio>0.7` misfires on ID/SKU/year/phone — GIẢ THUYẾT false-positive rate) nor vocab-gated (the bug).

**PRIMITIVE 1 — `is_value_cell(cell, unit_hints=())`** (locale-neutral, replaces `_is_pure_money`/`parse_money_vn`-as-gate): strip whitespace/grouping; strip leading `\p{Sc}`; strip `unit_hints` LONGEST-first (so `trieu` before `tr`); collapse `[\d.,\s/]`; if any `\p{L}` residue → NAME-carrying-a-number (`"Gói 6 triệu"`) → False; else digit-group present → True. Engine holds **zero** unit literals; VN suffix dict becomes the seeded 'vi' pack HINT. *Evidence:* `tabular_markdown.py:40-69`, `number_format.py:46`; convo + our code BOTH bake single-currency (`k\|tr\|m\|b\|vnd`) — neither does FX (EXPERT_BUILD:140).

**PRIMITIVE 2 — `is_label_cell(cell)`** (already correct): short (≤ config max chars), no bullet-lead, not ending in a (locale-sourced) sentence terminator, word-count ≤ config, and NOT `is_value_cell` = current `_is_label_like:76-87` with constants lifted + locale-keyed.

**PRIMITIVE 3 — `is_separator_line(line)`** (reuse): `^[\s\|:\-]+$` pipe form OR `---,---` comma form = current `document_stats._is_separator_line:303-314`; tldw `structure_aware.py:494`.

**THE ONE HEADER ORACLE — `looks_header(row, next_row, separator_below, declared_labels)`** — FORM first, vocab last:
1. **Separator law (highest):** if `separator_below` → HEADER, return True regardless of vocab/language (trust the `| --- |` the converter emits; never re-judge).
2. **Structural floor:** ≥2 non-empty cells AND no `is_value_cell` AND majority `is_label_cell` AND (if next_row) next_row carries value cells with compatible col-count (value-CONTRAST) → True.
3. **Vocab fast-path (optional acceleration):** cell ∈ `declared_labels` (per-bot custom_roles, AUTHORITATIVE) or per-locale pack → True. Built-in VN/EN `_HEADER_EXACT_TOKENS` becomes a locale-pack entry, never the sole gate.
4. Else False. `col{i}` fallback ONLY for a genuinely blank column — NEVER synthesize `col_N` for a vocab miss (RAG-Anything `utils.py:34-58`: pad to `max(len(row))` + emit grid headerless).

**Column-ROLE** (separate, runs AFTER header bound, HINT-driven, never shape-alone): VALUE = majority-`is_value_cell` column; NAME = first majority-non-value short-text column (ambiguity-skip preserved); CATEGORY = low-cardinality. Per-bot custom_roles AUTHORITATIVE; built-in tokens = optional Tier-1.5 HINT keyed by locale. Every column = a labelled attribute; "price" = one configured numeric role, NOT a single hardcoded `value_col` (CONFLICT-AVOID, EXPERT_BUILD:157).

### 6.2 LLM-selector vs deterministic routing

**Verdict: DETERMINISTIC block-level profile router wins on all four axes (cost, context-window safety, domain-neutrality, the only measured accuracy evidence). DELETE the file-level LLM selector.**
- Measured: ekimetrics intrinsic argmax beats best-fixed (mean 91.07 vs 90.7; answered 65/99 vs 49/99; zero query overhead) — but on the **LREC English benchmark**, GIẢ THUYẾT on our VN corpus (flag default OFF, never load-tested with flag ON; reference-completeness metric is English-pinned, drop it for VN).
- The LLM-selector side has **no measured lift anywhere**; orphan dead code (C2); the death-trap on big docs.
- tldw confirms: a scored DATA `TemplateClassifier` (`templates.py:762`), not an LLM, dispatches strategy.
- **Keep:** AdapChunk's cross-check/veto principle (already shipped as `apply_cross_check`); the validate-then-degrade contract (`re.compile→repair→fallback`) only IF a local LLM output is ever consumed. The router itself stays code.
- **LLM confined to LOCAL tasks** (narrate-table / propositionize-clause), default to $0 rule narrator, LLM opt-in per-bot — decided by §8.3 ablation, not guess.

### 6.3 HALLU=0-on-numbers stack (the 4-layer + 1 gate)

**Verdict: defense-in-depth, all observe/route-only (sacred-#10). The metadata-filter & observe-only grounding are SHIPPED; the dual-read keystone is verified BROKEN — fix it before any HALLU=0 claim.**
- **L0 INGEST** narrate-then-embed dual-payload (embed NL narration for recall; persist verbatim `original_content`) — producers shipped (`chunking/__init__.py:868,919`), sole live producer `smart_chunk_atomic` unwired.
- **L1 QUERY** metadata/stats hard filter (`price<=X` via SQL, not cosine) — SHIPPED domain-neutrally, the **load-bearing lever**; silently fails if extractor recall is low OR col_N unbinds the price column (so F1 is a prerequisite).
- **L2 GENERATE** dual-read = the BROKEN keystone (0 read-back) — the verbatim-number lever is inert; **single highest-value fix**.
- **L3 VERIFY** two-phase typed-enum claims verify + numeric-precision (tldw `claims_engine.py:417,1278`) — highest-value ADOPT, correct-tier fix for the 2026-06-03 spa-07 lesson (verification-tier, NOT sysprompt-tier); maps 1:1 onto our Anti-HALLU 4-loại-số. Adopt **observe/score-only**; we already own the $0 deterministic primitive `extract_numeric_claims`/`find_ungrounded_numbers` — promote it to a load-test grounding/Coverage metric BEFORE spending on an LLM judge. ⚠️ tldw clamps/tokenizer drop VN diacritics (`re.findall(r'[a-z0-9]+')`) — fix the VN tokenizer + route templates through `language_packs` first; alignment/NLI/LLM-judge layers stay language-agnostic.
- **UPSTREAM GATE** sentinel/decoy-calibrated reranker gate (inject known-irrelevant doc through same reranker; gate on `top_prob - sentinel_prob < margin`) — a wrong chunk that never reaches the LLM cannot fabricate a number; optional per-bot default-OFF.
- **Two preconditions:** lossless-coverage assert (F6) so digits aren't silently dropped; structural header fix (F1) so price columns bind. **Residual un-catchable HALLU:** an OCR-corrupted digit becomes faithful-but-wrong `original_content` — native formats must bypass OCR (parse MODE, not forced pass).
- ⚠️ "metadata-filter ⇒ HALLU=0" is **OVER-SOLD/GIẢ THUYẾT**: a mis-inferred value-col → confident wrong filter is itself a fabricate/conflate HALLU. The architecture REDUCES, never GUARANTEES 0.

### 6.4 Reconstruction layer (multi-page header propagation)

**Verdict: the one genuine net-new gap; feasibility-gate it (P2).**
- Verified absent: grep `propagat`/`multi.page`/`repeat.header`/`bbox`/`page_idx` in chunking/parser/ocr = 0 hits. Partial credit: intra-doc 2-row merge exists (`document_stats.py:783,804`) but operates within one block and is gated behind the vocab `_is_header_row`.
- **Self-undermining risk:** the convo's BBox/X-coord approach depends on parser coordinates the convo itself (L1366-1371) calls unreliable; our Kreuzberg path exposes **no BBox** (grep = 0). The EVOLVE-compatible alternative is **coords-free**: col-count + separator-line continuity across consecutive table blocks with no intervening prose. Lift unmeasured.

---

## 7. COMPLETE GAP REGISTER (deduped)

| ID | Gap | Severity | file:line | Status |
|---|---|---|---|---|
| G1 | Vocab header gate → col_N (CRUX) | **P0** | `document_stats.py:294,298,917` · `tabular_markdown.py:90-99` | SỰ THẬT |
| G2 | Single-currency value-cell test + 2 divergent money tests | **P0** | `tabular_markdown.py:40-67` · `number_format.py:46` | SỰ THẬT |
| G3 | No lossless-coverage `check_chunk_gaps` (0 hits) | **P0** | `shared/chunking/` | SỰ THẬT |
| G4 | Hardcoded VN narrate prompts (self-contradiction) | **P0** | `llm_narrate.py:53,60-71` | SỰ THẬT |
| G5 | Parallel `documents_stream_upload.py` endpoint | **P0** | `routes/documents_stream_upload.py` | SỰ THẬT |
| G6 | Version-ref constant filenames (×5) | **P0** | `constants/_13,_16,_17,_19,_21` | SỰ THẬT |
| G7 | Multilingual single-VN structure path | **P0/P1** | `analyze.py:278,346` · `vn_structural.py:55-89` · `rule_based_doc_profile.py:58-69` | SỰ THẬT |
| G8 | BROKEN `original_content` dual-read (0 read-back) | **P1** | `chunking/__init__.py:653,676,868` · `retrieve.py`/`generate.py` | SỰ THẬT |
| G9 | `smart_chunk_atomic` test-only ("Wave B2 will wire") | **P1** | `chunking/__init__.py:676` | SỰ THẬT |
| G10 | `strategy_used="SEMANTIC"` literal | **P1** | `document_worker.py:623` | SỰ THẬT |
| G11 | Orphan L4 LLM Strategy Selector (0 callers) | **P1** | `infrastructure/chunking_strategy/*` · `strategy_ports.py:78` | SỰ THẬT |
| G12 | Price-centric stats DB (single value_col) | **P1** | `stats_index_repository.py:20-21,97-98,131-132` | SỰ THẬT |
| G13 | SSoT dup `DEFAULT_PRICE_*_VND` | **P1** | `_21_…:57,64` + `_09_…:141,150` | SỰ THẬT |
| G14 | `_SUFFIX_MULT` baked `Final` dict (not config) | **P1** | `number_format.py:46` | SỰ THẬT |
| G15 | Worker duplicate funnel skips byte-sniff | **P1** | `document_worker.py:392-468,428` | SỰ THẬT |
| G16 | Embed/semantic-cache identity incomplete | **P1** | `embedding_cache.py:22-31` · `query_graph.py:1351,1257` · `semantic_cache.py:417-435` | SỰ THẬT |
| G17 | `_modality_boost` reranker score override (English vocab) | **P1** | `_modality_boost.py:67-158` | SỰ THẬT (smell) |
| G18 | Chunk-quality DI dead-code; live score observability-only | **P2** | `infrastructure/chunk_quality/…` · `ingest_stages_enrich.py:579` | SỰ THẬT |
| G19 | `_compute_bi` is a size-proxy, NOT char-span block-integrity | **P2** | `intrinsic_metrics.py:235` | SỰ THẬT (terminology) |
| G20 | Two eval tracks disconnected (no joined ablation) | **P1** | `eval_gate.py` · `score_chunks_intrinsic.py` | SỰ THẬT |
| G21 | Multi-page table header propagation absent | **P2** | chunking/parser/ocr (grep=0) | SỰ THẬT (feasibility-gate) |
| G22 | ekimetrics selector default-OFF, unmeasured on VN | **P1** | `analyze.py:448-457` · `intrinsic_metrics.py` | GIẢ THUYẾT (lift) |
| G23 | Async-202 discipline unaudited as SLA gate | **P2** | `documents.py:94` · `document_worker.py` | GIẢ THUYẾT |
| G24 | Misc polish (broad-except ×2, `'hdt'` literals, env BASE, `context_prefix_chars`/`max_blocks` magic, `vn_*` renames, docx heading-level whitelist, litellm dim-check) | **P2** | various | SỰ THẬT |

---

## 8. THE UNIFIED BUILD PLAN — P0→P2, EVOLVE-not-rewrite

**Governing law: structure by FORM not VOCAB. Lead with the CRUX trio. Every step verify-gated.**

### P0 — CRUX (do first; everything downstream depends on these)

**[P0-1] Kill the vocab header gate → ONE structural SSoT** (G1, G2)
- *What:* extract locale-neutral `is_value_cell(cell, unit_hints)` (`\p{Sc}` + digit-group shape; per-locale unit pack HINT) into a shared module; call from BOTH `_is_header_row` AND `_looks_header`; promote `_looks_header` to the structural oracle (separator-law → structural floor → vocab fast-path); trust the `| --- |` separator; rename `parse_money_vn`→`parse_amount`.
- *Files:* `document_stats.py:191-205,275-300,917` · `tabular_markdown.py:40-95` · `number_format.py:46`
- *Standard-ref:* tldw `structure_aware.py:494`; RAG-Anything `utils.py:34-58`
- *Skill:* `table-header-detect-structural`, `metadata-optional-hint`
- *VERIFY-GATE:* TDD failing test on `MARKS|CARGO DESCRIPTION` + separator → named header, 0 col_N; then ingest English shipping manifest + Spanish `Producto|Precio` → assert 0 col_N + header recovered + chunk reaches topK; then re-ingest 9 real docs, MEASURE header-recovery rate.

**[P0-2] Add the lossless-coverage gate** (G3)
- *What:* each strategy emits `(start_char,end_char)`; `check_chunk_gaps(spans,len(src),tol)` + `repair_gaps` as L6 exit gate after EVERY strategy; pin unit test. Decide fail-loud-vs-degrade + pin it.
- *Files:* `shared/chunking/` (0 hits today)
- *Standard-ref:* adaptive-chunking `postprocessing.py:66,128`
- *Skill:* `block-integrity-quality-gate`
- *VERIFY-GATE:* unit test fails on a dropped digit/prose span; report % source chars uncovered per bot/strategy on the 9 docs.

**[P0-3] Locale-key all word-lists + thread language** (G7)
- *What:* move `_HEADER_EXACT_TOKENS`/role-tokens/discourse/TOC/refusal lists + narrate prompts into per-locale packs keyed by language code (mirror `_24`); thread doc/bot language into every structure-deciding fn; detect by Unicode script-range (replace VN-diacritic binary); resolve `vn_structural._STRUCT_MARKERS` per-call-locale not import-bound; structural TOC (dotted-leader + page-number).
- *Files:* `document_stats.py:155-205,75-93` · `analyze.py:278,346` · `vn_structural.py:55-89` · `rule_based_doc_profile.py:58-69` · `_24_structural_markers_by_lang.py`
- *Skill:* `multilingual-no-vocab`
- *VERIFY-GATE:* en/ja dicts become reachable (no longer dead); byte-identical output on VN corpus regression (default 'vi'/VND).

**[P0-4] Fix wired narrate prompts** (G4)
- *What:* source `_BLOCK_PROMPTS` from the locale pack; body says "reply in the source/document language" not "tiếng Việt"; reconcile EN-constant vs VN-inline duplication.
- *Files:* `llm_narrate.py:53,60-71`
- *VERIFY-GATE:* narrate a non-VN table → output in source language (load-test).

**[P0-5] Remove parallel upload endpoint + worker duplicate funnel** (C1, C5)
- *What:* remove `documents_stream_upload.py` + its `_21_*` streaming constants; fold into canonical `POST /documents/create`; collapse worker funnel into a thin adapter over `DocumentService` (byte-sniff included; no URL-string-dictates-mime).
- *Files:* `routes/documents_stream_upload.py` · `document_worker.py:392-468,428,444-446`
- *Skill:* `canonical-ingest-flow`, `type-detection-mime-sniff`
- *VERIFY-GATE:* one canonical ingest route only; URL PDF with octet-stream mime still routes via byte-sniff.

**[P0-6] No-version-ref hygiene sweep** (C3)
- *What:* rename 5 version-ref constant files to purpose names; strip Sprint/Wave/Phase/Bug#/date tokens from comments (WHY-only).
- *Files:* `constants/_13,_16,_17,_19,_21`
- *VERIFY-GATE:* `grep -rnE "_v[0-9]|sprint|wave|phase|_[0-9]{6}"` over constants = 0 hits.

### P1 — WIRE THE ORPHANS ("nối dây")

**[P1-7] Land attribute-generic late-binding table model** (G12) — every column = labelled attribute; price = derived numeric-role view; drop single `value_col`/`PRICE_MIN_VND`; generalise `ParsedEntity` to `{name, group, values: dict[role,number], attributes}`. **CONFLICT-AVOID:** do NOT adopt the convo's single `value_col`. *Files:* `stats_index_repository.py:20-21` · `document_stats._column_roles` · `csv_chunker.py` · `plans/260626-fix-all/plan.md:50`. *VERIFY-GATE:* a price-less/non-VND table indexes its columns; N+1 load-test on another-domain bot.

**[P1-8] Fix `strategy_used` record-of-truth** (G10) — surface resolved `_chunking_strategy` out of `ingest()` into `DocumentIngested`. *Files:* `document_worker.py:623` · `ingest_phases.py` (IngestResult). *VERIFY-GATE:* DB row reflects real per-doc strategy.

**[P1-9] DELETE the orphan L4 LLM selector** (C2, G11) — 0 callers; deterministic `rule_resolver` stays the per-block tree; `apply_cross_check` already gives the veto. *Files:* `infrastructure/chunking_strategy/*` · `strategy_ports.py:78`. *VERIFY-GATE:* grep confirms no two parallel selection paths.

**[P1-10] Wire `smart_chunk_atomic` as canonical Block→Chunk producer** (G8, G9) — persist `original_content`/`block_types`/`structural_path` for ALL atomic blocks via one `_build_chunk_meta()` across flat+parent+child write paths. *Files:* `chunking/__init__.py:653,676,868` · `ingest_stages_store.py`. *VERIFY-GATE:* backward-trace a table chunk shows rich fields persisted.

**[P1-11] Close the dual-read round-trip** (G8) — at retrieval/context-assembly, append the fenced raw block to LLM-visible content (config-gated, domain-neutral). *Files:* `orchestration/nodes/retrieve.py` + `generate.py` (0 reads today). *Standard-ref:* AdapChunk §7.3. *Skill:* `ingest-backward-trace-debug`. *VERIFY-GATE:* backward-trace shows verbatim `original_content` reaching the LLM; load-test HALLU=0 on exact-number questions.

**[P1-12] Wire entity Document Profile + thread `detected_language` into embedding selection** — replace telemetry-only dict path. *Files:* `ingest_stages.py:648,669` · `analyze.py:156-201`.

**[P1-13] Currency/scale-neutral value axis + collapse SSoT** (G13, G14) — lift `_SUFFIX_MULT` into per-locale pack; collapse duplicated `PRICE_BUCKETS_VND`/`MIN_VND` to one SSoT; rename `PRICE_*_VND`→`VALUE_*`. *Files:* `number_format.py:46-53` · `_21_…:57-69` vs `_09_…:141,150`.

**[P1-14] Complete embed/semantic-cache identity tuple** (G16) — `provider:model:sha256(text)[:base_url]`; scope semantic cache by provider/model/dim. *Files:* `embedding_cache.py:22-31` · `query_graph.py:1351,1257` · `semantic_cache.py:417-435`.

**[P1-15] Wire-or-remove the $0 rule `table_narrator`** — as registry 'rule' provider default; LLM narrate opt-in per-bot; decide by measured §8.3 ablation. *Files:* `narrate/__init__.py:19`.

**[P1-16] Gate-or-remove `_modality_boost`** (G17) — move intent→boost map to `system_config` + per-bot flag default-OFF + audit pre/post delta; unit-test byte-identical when OFF; or remove (sacred-#10). *Files:* `_modality_boost.py:67-206`.

**[P1-17] Measure-before-flipping ekimetrics selector** (G22) — load-test VN corpus with flag ON vs OFF; report Block-Integrity + size-compliance (intrinsic) AND Coverage/Faithfulness (extrinsic); flip default-ON only if BOTH move up; drop English-pinned reference-completeness for VN. *Files:* `analyze.py:448-457` · `intrinsic_metrics.py` · `ingest_stages.py:517-524`.

**[P1-18] Join the two eval tracks** (G20) — 6-config ablation (Baseline-512/HDT/SEMANTIC/PROPOSITION/AdapChunk-full/no-crosscheck) requiring BOTH intrinsic + downstream lift; no-LLM-judge deterministic. *Files:* `eval_gate.py` · `score_chunks_intrinsic.py` · `eval_collect.py`.

**[P1-19] Promote the $0 numeric grounding to a first-class load-test metric** (L3 cheap tier) — per-answer ungrounded-number count + per-claim Coverage from `extract_numeric_claims`/`find_ungrounded_numbers`; measure its catch-rate before any LLM-judged verifier; keep observe/score-only, OUT of the answer. *Files:* `math_lockdown.py:166,210` · `ragas_metrics.py` · rag-loadtest harness.

### P2 — POLISH + convo-NEW (defer behind CRUX; T2/future-T1)

**[P2-20]** Multi-page table header propagation (G21) — feasibility-gate FIRST (does Kreuzberg expose BBox/col-count? grep=0 today); if not, coords-free reconstruction (col-count + separator-continuity across consecutive table blocks, no intervening prose). *Skill:* `multi-row-header-merge`, `multilingual-no-vocab`.
**[P2-21]** Upgrade `_compute_bi` from size-proxy to true char-span block-integrity (G19) — requires P0-2 spans; predicted-split-not-inside-gold-block within `tolerance_chars`. *Files:* `intrinsic_metrics.py:235`.
**[P2-22]** Resolve chunk-quality dead-vs-live (G18, C6) — delete dead DI module OR revive behind `system_config.chunk_quality_scoring_enabled`; demote live `score_chunk_quality` to regression canary. *Files:* `infrastructure/chunk_quality/…` · `ingest_stages_enrich.py:579`.
**[P2-23]** Aggregate-row by sum-equality math as a FLAG not delete (domain-neutral, float-tolerant, measured).
**[P2-24]** Async-202 discipline SLA gate (G23) — assert <200ms accept + stream-to-store + never-parse-in-request.
**[P2-25]** Native formats bypass OCR (parse MODE: born-digital text-layer vs scanned) — keep parser behind swappable Port + circuit-breaker. *Files:* `infrastructure/ocr/` · `parser/registry.py`.
**[P2-26]** Optional per-bot sentinel/decoy reranker gate (default OFF). *Files:* `reranker/registry.py` · `retrieval_filter.py:165-210`.
**[P2-27]** Misc (G24): narrow 2 broad-excepts; replace `'hdt'` literals with a STRATEGY_NAMES membership; env-source eval BASE (`eval_collect.py:11`); lift `context_prefix_chars=200` (`late_chunking.py:59`) + `max_blocks=60` (`llm_resolver.py:87`) to constants; rename `vn_*`/`_VN_` symbols locale-neutral with back-compat aliases; docx heading-level by trailing-int parse not 1..3 whitelist (`docx_parser.py:36`); litellm dim-check in `health_check` (`litellm_embedder.py:114-133`); `register_reranker` validation.

### NON-NEGOTIABLE GUARDRAILS (every step)
zero-hardcode (lift every magic number/word-list to config/locale-pack) · domain-neutral (no `startswith('Chương')`, no vocab in structure paths) · no-version-ref (header `X-Schema-Version`, not `/v1`) · sacred-#10 (no app-inject/override; XML-wrap stays governed/owner-opt-in) · 4-key+RLS never downgraded to bot_id-only · content-hash idempotency never `uuid4()` upsert · EVOLVE-not-rewrite (parser-adapter is the ONLY permitted rewrite zone).

### MASTER VERIFY-GATE (rule#0)
NO step may be reported "fixed/works/pass" until **(a)** an ingest backward-trace on a **non-VND/non-VN sheet** shows the header recognized + chunk reaching topK (skill `ingest-backward-trace-debug`), AND **(b)** a load-test reports **Coverage + Faithfulness + HALLU=0** with real numbers (skill `rag-loadtest`). Current findings are SỰ THẬT at code-evidence (STATIC) level; coverage-loss magnitude + post-fix lift remain GIẢ THUYẾT until measured.

---

## 9. OPEN QUESTIONS / GIẢ THUYẾT TO MEASURE

1. **col_N coverage-loss magnitude** — actual col_N rate on the 9 real docs / 3 bots, and post-fix header-recovery lift. Not measured (EXTREF's "~9 docs/3 bots" not re-measured this session).
2. **Pure-shape value-detection false-positive rate** on ID/SKU/year/phone/quantity columns — justifies the vocab HINT layer; unmeasured.
3. **Number-HALLU rate impact of the broken dual-read** — round-trip verified broken (SỰ THẬT, 0 read-back); the wrong-number-rate magnitude is GIẢ THUYẾT until backward-trace + load-test.
4. **Metadata-extractor RECALL** — does `generic_llm_extractor` + `query_range_parser` catch "dưới 2 triệu", "<= 2M", "$500", percent, dates? On a miss, the entire number protection silently disappears. Unmeasured.
5. **ekimetrics lift on our VN corpus** — flag default OFF, never load-tested with flag ON; reference-completeness metric English-pinned.
6. **LLM-selector-vs-rule ablation** and **LLM-narrate-vs-rule ablation** deltas — both GIẢ THUYẾT; lead toward DELETE / $0-rule-default pending numbers.
7. **Multi-page header propagation feasibility** — does Kreuzberg-markdown expose BBox/col-count (grep=0 today)? coords-free continuity lift unmeasured.
8. **Cross-lingual recall** — narrate language ≠ query language may drop recall while faithfulness holds; unmeasured on VN corpus.
9. **Fail-loud vs degrade for check_chunk_gaps** — adaptive-chunking says raise; our live leaf-coverage gate was deliberately loosened 2026-06-20 to a floor. Decision + pinned test needed.
10. **Quarantined over-claims** — convo's `-90% token` / `+35% Context Precision` / "HALLU=0 guaranteed" are invented sales devices (L865 admits it); must be load-tested on OUR corpus, never reported as lift.
11. **Methodology caveat** — survey PDF 2402.13116v4 is the WRONG paper (Knowledge Distillation); the real §8 eval source is the ekimetrics Adaptive Chunking poster. Any claim 2402.13116 validates the eval = fabrication.

---

## 10. INDEX OF SOURCE REPORTS

- `reports/MASTER_4PHASE_30AGENT_20260627.md` — 14-row gap table, 15-step fix, VERIFY GATE (:148), wrong-paper caveat (§2:79), risks (:74,76,77).
- `reports/MASTER_INGEST_FLOW_REPORT_20260627.md` — F1-F8 fix plan, 16-gap inventory (§3+§8), verify-gate (§8), §7 anti-HALLU.
- `reports/INPUT_DATA_RAG_STANDARD_VS_CODE_*.md` — §1 ONE LAW, §4 header-fix (lines 106-125), §5 ranked adoption, §K multilingual, §I metadata-filter.
- `reports/EXTREF_DATA_INPUT_AUDIT_*.md` — V1-V5 root-cause, F1-F5 fixes, "cruel irony" two-oracle drift (V5:41-45).
- `reports/EXPERT_BUILD_BLUEPRINT_20260627.md` — convo cross-read, death-trap (:15-16,123), CONFLICT-AVOID single value_col (:157), over-sold claims (:140,142,143,147), §3 convo-NEW, §4 RISKY, §5 build plan.
- `reports/TLDW_RAG_CORE_DEEPDIVE_20260627.md` — separator-law (§2.2 :494,:384), scored TemplateClassifier (§3 :762), structural steal #1, RLS-absent in tldw (:113).
- `reports/TLDW_RAG_SURFACE_DEEPDIVE_20260627.md` — two-phase claims-verify (§4 :86-104), typed VerificationStatus enum (`claims_engine.py:417,1278`), VN-tokenizer caveat (:15,104), monolith anti-pattern (§0:9).
- `reports/ADAPTIVE_RAGANYTHING_DEEPDIVE_20260627.md` — ekimetrics intrinsic argmax (§1:22), lossless-coverage ref (`postprocessing.py:66,128`), compute_block_integrity (`metrics.py:264`), two-track eval (§1), English-pinned caveat (:40,111).
- `docs/design/ADAPCHUNK_ARCHITECTURE.md` — 7-layer target spec; LLM selector + cross-check (§4-5, :98-132); narrate-then-embed + original_content (§7.1-7.3, :151-173); file-level granularity limitation (:104-110).
- `plans/260626-fix-all/plan.md` — S1-A late-binding (:50), program decision register.

---

*Every load-bearing claim in §0/§4/§5/§7 re-verified LIVE this session: `check_chunk_gaps`=0, `original_content` read-back=0, `strategy_used="SEMANTIC"` (document_worker.py:623), header vocab gate (document_stats.py:294,298), structural `_looks_header` (tabular_markdown.py:90-99), narrate contradiction (llm_narrate.py:53 vs 60/65/71), parallel upload endpoint (13,925 bytes), 5 version-ref files, orphan selector package, RLS GUC (engine.py:174), pgvector raises (311,388), async-202 (94,231,270), metadata-filter lane present, price_primary/secondary columns, smart_chunk_atomic "Wave B2 will wire" (676), SSoT dup PRICE_BUCKETS (_21+_09), `_24` by-lang pack present, vn_structural import-bound (55-56), detected_language binary, has_toc literal (278,346), observe-only guards (guard_output.py:66-69, math_lockdown.py:166,210). STATIC = SỰ THẬT; magnitude/lift = GIẢ THUYẾT until the verify-gate runs.*