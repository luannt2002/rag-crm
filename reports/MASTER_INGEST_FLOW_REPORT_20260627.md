# MASTER — RAGBOT INGEST/CHUNK FLOW: EXPERT STANDARD vs OUR CODE + FIX PLAN

> 2026-06-27 · Consolidation of a multi-agent program (6 workflows, ~70 agents, ~8M tokens) reading `_external_refs` (tldw_server full RAG surface, adaptive-chunking, RAG-Anything, open-notebook, llama-cookbook) + papers, distilling the GOLD STANDARD, and auditing our code against it + CLAUDE.md sacred mindset + the AdapChunk 7-layer target spec.
>
> **This file is self-contained and durable** — it survives even if the remaining 2 workflows are interrupted. All claims are `file:line`-grounded (rule#0: evidence, not guess).

---

## 0. Artifact index (all on disk)

| Artifact | Path |
|---|---|
| Target architecture spec | `docs/design/ADAPCHUNK_ARCHITECTURE.md` |
| GOLD STANDARD + our-code audit + verdict | `reports/INPUT_DATA_RAG_STANDARD_VS_CODE_20260627.md` |
| tldw RAG core pipeline deep-dive | `reports/TLDW_RAG_CORE_DEEPDIVE_20260627.md` |
| tldw full RAG surface deep-dive | `reports/TLDW_RAG_SURFACE_DEEPDIVE_20260627.md` |
| adaptive-chunking + RAG-Anything deep-dive | `reports/ADAPTIVE_RAGANYTHING_DEEPDIVE_20260627.md` |
| extref data-input audit (header/table) | `reports/EXTREF_DATA_INPUT_AUDIT_20260627.md` |
| 10 input-data skills | `.claude/skills/*/SKILL.md` |
| **THIS master** | `reports/MASTER_INGEST_FLOW_REPORT_20260627.md` |
| (pending) 4-phase master + agent scorecard | workflow `wiquyr8hs` — append when done |
| (pending) tldw full-coverage manifest | workflow `w1y543ara` — append when done |

---

## 1. THE ONE LAW (the gold standard, distilled from every mature ref)

> **Structure is decided by FORM, never by VOCABULARY.**
> Header/table/section/heading/element-type are decided by markup tokens, typography, char-shape, column count, byte magic — **NEVER** by a domain/brand/single-language word-list in any structure-deciding code path.

Proven in working reference code (none of them hardcode header vocab):
- tldw `structure_aware.py:494` — table header = markdown separator line `^[\s\|:\-]+$` after a `|`-row; heading level = `len(match.group(1))` = count of `#` (`:384`).
- RAG-Anything `modalprocessors.py:228` — `text_level` → `'#'*level` (language-neutral heading reconstruction); tables → markdown grid with `| --- |` (`utils.py:34`).
- adaptive-chunking `parsing.py:429-442` — split rules keyed on structural ROLE tags (TITLE/TABLE/FOOTNOTE/PAGE_BREAK), zero brand/industry literals.

### GOLD STANDARD checklist A–K (condensed)
- **A Canonical funnel:** one ingest entrypoint; every format converges; one structured-markdown output; content-hash idempotency; safe-replace not batch-wipe; permanent-vs-transient retry split; soft-failure sentinel (Error/login-page body caught, never embedded).
- **B Type detection:** layered `mime → ext → byte-sniff`; declared trusted first; byte-sniff rescues octet-stream/no-ext; metadata REFINES, never DICTATES.
- **C Parser adapter:** Port(Protocol)+Registry(dict on config-string)+Strategy(one file)+Null Object; new format = +1 file +1 row, orchestrator untouched.
- **D Structural detection:** heading level = COUNT of marker; element type by shape (pipe-row, `|---|`, fence, image syntax); separator = all-empty row; col count structural with `col{i}` fallback; preserve source order.
- **E Chunking/template:** strategy via registry/dispatch on generic names (no `if doctype==`); atomic blocks (TABLE/FORMULA/IMAGE/CODE) never cut; header re-attached on row-split; never nuke doc to zero chunks.
- **F Quality gate:** label-free block-integrity (fraction of parser split-points not cut); **lossless-coverage INVARIANT — `assert check_chunk_gaps()` after EVERY strategy, fail loud**; intrinsic cohesion as regression gate.
- **G Embedding/cache/warmup:** cache identity = `provider:model:sha256(text)[:base_url]`; per-batch cap; idempotent embed (DELETE-then-INSERT + deterministic id); adaptive batching keyed `(provider,model,config-SHA)`; fallback-vector poisoning guarded.
- **H Dim-drift:** self-describing dim check at warmup, not at INSERT.
- **I Retrieval/rerank/grade:** RRF rank-based k=60 + dedupe; language-NEUTRAL FTS (never `to_tsquery('english')`); reranker Strategy+DI; sentinel/decoy-calibration GATE (strongest anti-HALLU); parent/child + neighbor expand; tiered grade fallback; granularity router.
- **J Metadata as hint:** refines/hints, never dictates; language = pass-through, behavior keyed by locale; numeric/currency policy = config, not hardcoded default.
- **K Multi-lang / zero-literal:** zero hardcoded literals in any structure path; language auto-detected by script-range; per-language behavior = DATA in config (add language = add config, not code); all knobs single-SSoT; multi-format parity.

---

## 2. VERDICT — are we expert-grade?

**Structurally expert** (DI/Ports/Registry/Null clean, zero `if provider==`, HALLU=0 holds, sacred-rule #10 holds on answer path, ingest is no-LLM deterministic), **BUT THE ONE LAW is violated on the table/CSV backbone (P0)** and multi-language/coverage/quality-gate are P1 gaps.

→ **NOT yet expert-grade on input-data correctness for non-VN, non-VND, non-priced corpora.** The fix is EVOLVE-not-rewrite: the correct structural detector already exists (`tabular_markdown._looks_header`); the bug is that the extractor bypasses it and re-judges by vocab.

---

## 3. THE 8 GAPS (file:line evidence)

| # | Gap | File:line | Sev | Standard |
|---|---|---|---|---|
| **1** | **Header-vs-data gated by hardcoded VN/EN currency+column VOCAB** (`_HEADER_EXACT_TOKENS`, `_MONEY_UNIT_RE = triệu\|nghìn\|vnd\|tr\|đ\|k\|m`). Non-VN/non-VND or no `custom_roles` sheet → positional col-0 / every-row-is-header. SOLE structural path for every XLSX/CSV/Sheets. | `document_stats.py:290-300` + `tabular_markdown.py:40-43,57-99,200` | **P0** | D, K |
| 2 | **No lossless-coverage invariant** — `check_chunk_gaps` / start_char-end_char assert does not exist (grep=0); a strategy can silently drop source text. | `shared/chunking/` (whole pkg) | P1 | F |
| 3 | **Chunk-quality / block-integrity gate is DEAD CODE** — `shared/chunk_quality.py` 0 callers; `infrastructure/chunk_quality/*` commented out 2026-06-03. Implies a gate that doesn't run. | `shared/chunk_quality.py:1-330` | P1 | F |
| 4 | **Worker duplicate ingest funnel** — `fetch→detect_parser→parse→flatten` skips byte-sniff, flattens parser row-chunks, lets URL string dictate mime. Violates canonical-funnel + no-parallel-upload. | `document_worker.py:392-468` (428 no-sniff, 404-413 URL-dictates-mime, 444-446 flatten) | P1 | A, B |
| 5 | **Embed/semantic cache poisoning** — Redis embed key omits provider, model defaults to literal `'unknown'`; semantic-cache vector not scoped by provider/model/dim → provider/dim swap serves stale cross-distribution vectors. | `embedding_cache.py:22-31` + `query_graph.py:1351,1257` + `semantic_cache.py:417-435` | P1 | G |
| 6 | **Multilingual path de-facto single-VN** — `has_toc` literal `'mục lục'/'table of contents'`; `vn_structural` regex bound to `'vi'` at import → shipped `en`/`ja` marker dicts are DEAD; retrieve structural pre-filter never threads bot language. | `analyze.py:278,346` + `vn_structural.py:55-57,86` + `retrieve.py:1066-1071` | P1 | K |
| 7 | **Reranker score mutated post-hoc** by app multiplier keyed on hardcoded English intent vocab — adjacent to sacred-rule #10 (no app-override of model signal) + single-language coupling on a relevance path. | `_modality_boost.py:67-74,130-158,192-206` | P1 | I, #10 |
| 8 | **Price/value axis baked single-currency (VND)** — `PRICE_*_VND` + VND-only `parse_money_vn` + VND bucket labels + 4-digit floor + price-shaped ParsedEntity; `DEFAULT_PRICE_BUCKETS_VND/MIN_VND` DUPLICATED across two constants modules (SSoT drift). | `_21_*.py:51-69,146-149` + `number_format.py:46-53` + `document_stats.py:222-272,1040-1099` + dup `_09_*.py:141,150` | P1 | J, zero-hardcode |

---

## 4. CLAUDE.md mindset compliance

| Sacred rule | Status | Evidence |
|---|---|---|
| Domain-neutral (no brand) | ✅ PASS | brand-literal grep clean everywhere |
| Domain-neutral (no single-domain logic) | ❌ FAIL | price/currency axis = VND-only (gap 8); header vocab VN-commercial (gap 1) |
| Multi-language | ❌ FAIL | structure path single-VN (gap 6); header vocab single-lang (gap 1) |
| Multi-format (one canonical funnel) | ⚠️ PARTIAL | service path conform; worker duplicate funnel (gap 4) |
| Zero-hardcode | ❌ FAIL | money regex, price constants, SSoT dup (gaps 1,8) |
| Strategy+DI (Port/Registry/Null) | ✅ PASS | clean, zero `if provider==` |
| No app-override of LLM answer (#10) | ✅ PASS (answer path) | `guard_output` no override; refusal from `bots.oos_answer_template` |
| No app-override of model SIGNAL | ⚠️ SMELL | `_modality_boost` mutates reranker score (gap 7) |
| HALLU=0 sacred | ✅ HOLD | ingest deterministic no-LLM |
| EVOLVE-not-rewrite | ✅ feasible | correct detector exists; fix = wire it, not rewrite |

---

## 5. FIX PLAN — domain-neutral, EVOLVE-not-rewrite (ordered by ROI)

### F1 (P0) — Promote structural header detection as SSoT; demote vocab to optional role-HINT
**Root cause:** `document_stats._is_header_row` (`document_stats.py:290-300`) requires a cell to EXACTLY MATCH `_HEADER_EXACT_TOKENS` (VN-commercial vocab) OR per-bot `declared_labels`. Headers outside that vocab (e.g. `MARKS | CARGO DESCRIPTION | NGÀY VỀ`) → not-header → col_N. The converter `tabular_markdown.py` ALREADY emits `| --- |` separators + a structural `_looks_header` (`:90-99`), but the extractor throws the separator away (`:899,917` just `continue`) and re-judges by vocab → 2-stage drift.

**Fix (structural, FORM-only):**
1. In `document_stats.parse_table_chunks`, **trust the markdown `| --- |` separator** as the authoritative header anchor: the row immediately above a separator line IS the header, regardless of cell vocabulary or language.
2. When no separator exists, use **structural header heuristic** (already in `tabular_markdown._looks_header`): a row is a header iff its cells are label-shaped (short, non-numeric, non-money) AND the NEXT row has value-contrast (numbers/longer text). Shape only — no word-list.
3. **Demote `_HEADER_EXACT_TOKENS` / `_MONEY_UNIT_RE`** from a GATE to an optional **role-HINT** for column ROLE (price/quantity/name) — never for the "is this a header?" decision. Keep `custom_roles`/`declared_labels` as authoritative per-bot override (already exists).
4. **Never emit `col_N` when a header label exists** for that column; `col{i}` is the fallback ONLY for a genuinely headerless column.
5. **Unify**: make `tabular_markdown._looks_header` the single header detector used by BOTH the converter and `document_stats` (kill the drift).

**Skill:** `.claude/skills/table-header-detect-structural/SKILL.md`. **Guard:** N+1 canary test — ingest a clean non-VN, non-VND table (e.g. English shipping manifest) → assert 0 `col_N`, headers recovered. Then re-ingest the 9 real docs and measure header-recovery rate (rule#0: measure before claiming fixed).

### F2 (P1) — Lossless-coverage invariant
Add `check_chunk_gaps(chunks, full_text)` + `assert` after EVERY chunking strategy run (adaptive-chunking `postprocessing.py:66,128` pattern). A chunker that drops a digit = a missing/fabricated number downstream → protects HALLU=0. Pin in a unit test. **Skill:** `block-integrity-quality-gate`.

### F3 (P1) — Revive or remove the chunk-quality gate
Either wire `shared/chunk_quality.py` into the live ingest path (compute label-free block-integrity from parser split-points as a regression canary) or delete the dead code so it doesn't imply a gate that doesn't run.

### F4 (P1) — Collapse worker into a thin adapter over the service
Remove the duplicate funnel in `document_worker.py:392-468`; route the worker through the SAME `document_service` canonical path (byte-sniff included). One funnel, no URL-string-dictates-mime. **Skill:** `canonical-ingest-flow` + `type-detection-mime-sniff`.

### F5 (P1) — Complete the embed cache identity tuple
Make the embed/semantic cache key = `provider:model:sha256(text)[:base_url]`; scope semantic-cache vectors by provider/model/dim. Stops cross-distribution poisoning on provider/dim swap. **Skill:** none yet (covered in standard §G).

### F6 (P1) — De-VN the structure path (language as DATA)
`has_toc`, `vn_structural`, retrieve pre-filter must be keyed by detected/bot language with config packs per locale; detect by script-range, not literal `'mục lục'`. Thread bot language through. **Skill:** `multilingual-no-vocab`.

### F7 (P1) — Currency/scale-neutral value axis + fix SSoT drift
Make price parsing currency-agnostic (config-driven units, not VND-baked); collapse the duplicated `PRICE_BUCKETS_VND/MIN_VND` to one SSoT. **Skill:** `metadata-optional-hint`.

### F8 (P1, watch) — Reranker modality boost
Review `_modality_boost` mutating the reranker score by app-chosen English-vocab multiplier — either remove (let model signal stand, sacred #10) or make it config-driven + language-neutral.

---

## 6. What we are ALREADY ahead of tldw on — DO NOT regress
- `reranker_resolver` real 3-tier fallback (binding → `_lookup_platform_default`/system_config → NullReranker) — `reranker_resolver.py:188/203`. tldw embedding registry has NO resolution fallback + NO dim enforcement.
- `X-Idempotency-Key` request dedupe (`documents.py:11,133`) — stronger than tldw's media_id-reprocess idempotency.
- Single canonical `POST /api/ragbot/documents/create` — tldw splits into per-format endpoints.

## 7. Highest-value ADOPT from tldw (beyond the fix plan)
- **Claims two-phase decompose→verify** with typed status enum `HALLUCINATION/NUMERICAL_ERROR/MISQUOTED/MISLEADING/CITATION_NOT_FOUND/CONTESTED` (`claims_engine.py:417`) — maps 1:1 to our Anti-HALLU 4-loại-số; localizes WHICH sentence is unsupported (stronger than whole-answer grounding float). Directly answers the 2026-06-03 spa-07 lesson.
- **FileValidator** 3-layer `puremagic→python-magic→mimetypes` + "detected-MIME beats extension" hard-fail (`Upload_Sink.py:639-690`) = the byte-sniff layer F4 needs.
- **Content-hash → vector-invalidate atomically** (`synced_document_update_ops.py:64-69`) = structural fix for the embedding-NULL/stale bug class.
- **Sentinel/decoy-calibrated reranker gate** (`advanced_reranking.py:1567-1688`) = self-calibrating refuse signal, beats fixed cosine cutoff.

---

## 8. 4-phase 30-agent program — DONE (resumed after session-limit)

Full report: **`reports/MASTER_4PHASE_30AGENT_20260627.md`** (30 agents, 4 phases, 15-step fix plan). Agent effectiveness: **28/30 high, 2 medium, 0 low** (medium = Tầng3-ideal slightly broad; eval-methodology agent — correctly flagged paper `2402.13116` as the WRONG paper for chunking).

### Additional gaps the 4-phase found (beyond §3's 8) — all VERIFIED file:line
| # | Gap | File:line | Sev |
|---|---|---|---|
| 9 | **Wired LLM narrate prompts hardcoded Vietnamese** (contradicts own "preserve source language") | `infrastructure/narrate/llm_narrate.py:53,59-72` | **P0** |
| 10 | **Parallel `/documents/upload-stream` endpoint EXISTS** — CLAUDE.md HEADLESS §2 forbids (data-loss orphan) | `routes/documents_stream_upload.py` | **P0** |
| 11 | **Version-ref filenames** in constants (`_13_…layer_1`, `_19_sprint3`, `_21_…wb_2_p1_5`, `_16_…phase_b`, `_17_260509`) | `shared/constants/` | **P0** |
| 12 | `strategy_used` hardcoded `"SEMANTIC"` for EVERY doc (record-of-truth bug) | `document_worker.py:623` | P1 |
| 13 | **Tầng-4 LLM Strategy Selector Port = ORPHAN dead code** (0 runtime callers; `chunking_strategy_provider` read by nothing) | `infrastructure/chunking_strategy/*` | P1 |
| 14 | `smart_chunk_atomic` (sole producer of `original_content`/`block_types`/`structural_path`) is **test-only, never wired** ("Wave B2 will wire") | `shared/chunking/__init__.py:653,676` | P1 |
| 15 | `original_content` (§7.3 HALLU=0 anchor) **never read back at retrieval** — dual-read round-trip broken end-to-end | `orchestration/nodes/retrieve.py` (0 raw_chunk reads) | P1 |
| 16 | `detected_language` computed but **never selects embedding model** (multi-lang routing absent) | `ingest_stages.py:669` (logged only) | P1 |

### Verify-gate (rule#0) before ANY "fixed" claim
Ingest backward-trace a **non-VND / non-VN-vocab** sheet → assert 0 `col_N` + headers recovered → re-ingest the 9 real docs, measure header-recovery rate + Block-Integrity → load-test Coverage/Faithfulness. SỰ THẬT only after runtime numbers. Skills: `ingest-backward-trace-debug` + `block-integrity-quality-gate`.

### Still pending (low value)
- `w1y543ara` (coverage sweep) FAILED at session-limit (19/23 agents done; 3 survey + manifest unrun). Lowest value (only certifies non-RAG files were opened). Resume after limit reset — 19 agents cached, only 4 re-run.
- Honest note: `wqtkvuzcl` had 1/10 P3 agent fail (retry-cap); the 4-phase `code:*` agents re-covered that slice — findings unaffected.
