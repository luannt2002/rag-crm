# DEEPDIVE: tldw_server — RAG-relevant subsystem study (refs-tldw-server)

- **Date**: 2026-07-02
- **Studied repo**: `/var/www/html/ragbot/_external_refs/tldw_server` — upstream `https://github.com/rmusser01/tldw_server.git`, HEAD `be2e7f8686e49d95e83827d3c2006ed37f29de58` (2026-06-20) [FACT: `git log -1`]
- **Size**: `tldw_Server_API/app` = 329,374 LOC across 2,147 Python files (whole repo incl. tests/scripts = ~1.74M lines / 5,602 .py files) [FACT: `wc -l` run 2026-07-02]. The task prompt's "~68k LOC" undercounts; RAG core alone: `app/core/RAG` = 47,877 LOC, `app/core/Chunking` = 14,991 LOC, `app/core/Ingestion_Media_Processing` = 42,330 LOC.
- **What it is**: single-box "personal research assistant" (media ingest → transcribe/parse → FTS5+Chroma RAG → OpenAI-compatible APIs). SQLite-first, per-user DB files, GPLv3. NOT a multi-tenant SaaS platform — tenancy model is physical per-user DB isolation, not RLS.
- **Method**: direct file reads with `file:line` evidence. Skipped: audio/video transcription internals, TTS, MCP, character chat (per mandate). All claims below labeled FACT (code evidence) or HYPOTHESIS (my assessment).

---

## 0. Executive orientation — how tldw's RAG is shaped

The RAG core is one **mega-function**: `unified_rag_pipeline()` in `app/core/RAG/rag_service/unified_pipeline.py:1160` with **229 keyword parameters** (counted mechanically) [FACT]. Docstring philosophy: "One function to rule them all... No configuration files, no presets, just parameters" (`unified_pipeline.py:1-14`). Execution is a linear staged flow with `# ==========` section banners (clarification → spell check → classification/reformulation → research loop → expansion → intent routing → granularity routing → cache → HyDE → temporal filters → retrieval → multi-vector → numeric boost → gap analysis → filters (keyword/injection/chunk-type/content-policy/security) → table processing → VLM late chunk → evidence accumulation → CRAG grading → rewrite loop → rerank → web fallback → why-these-sources → siblings → citations → knowledge strips → evidence chains → generation → hard citations → quote citations → fast groundedness → claims → numeric fidelity → post-verification → utility grading → feedback → highlight → cost → cache store → observability) [FACT: grep of section banners, lines 1582–6428].

**HYPOTHESIS (design judgment)**: the 229-param signature is an anti-pattern for ragbot (violates zero-hardcode/config-chain and is untestable at the surface), but tldw itself mitigates it with two bundling layers — `profiles.py` (production/research/cheap/fast/balanced/accuracy presets, `profiles.py:1-80`) and `search_depth_mode` presets (speed/balanced/quality, `unified_pipeline.py:1582-1632`). The *individual feature stages* are mostly small, well-isolated modules that are the real value for ragbot. Ragbot's equivalent of "profile" = per-bot `plan_limits`/pipeline_config — already better-governed.

---

## 1. STRONGEST PATTERNS RAGBOT SHOULD ADOPT (ranked)

### P1 — Sentinel-document rerank calibration + gated generation (TwoTierReranker)
**File**: `app/core/RAG/rag_service/advanced_reranking.py:1476-1704` [FACT].

Mechanism:
1. Inject a synthetic known-irrelevant "sentinel" document (`id="sentinel:irrelevant"`, generic filler text) into the rerank pool (`advanced_reranking.py:1568-1578`).
2. Stage 1 cross-encoder (default `BAAI/bge-reranker-v2-m3`) scores pool → shortlist top-N (50–100); Stage 2 LLM-scorer rescores shortlist+sentinel (`:1583-1621`).
3. Final score = logistic calibration over 3 features: `logit = w0 + w1*orig + w2*ce + w3*llm` → probability (`:1632-1668`).
4. **Gate**: answer generation is gated when `top_prob < threshold` OR `top_prob - sentinel_prob < margin` (`:1683-1690`); gating result + sentinel scores exported in `last_metadata` for the pipeline to consume (`:1692-1703`).

**Why it matters for ragbot**: solves the exact problem in memory note *feedback_threshold_drift_post_migration* — absolute score thresholds break every time embedder/reranker changes (Jina→ZE→…, 0.30 threshold recalibration pain). The sentinel gives a **per-query, per-model dynamic floor**: "is the best real chunk meaningfully better than known garbage *for this query on this model*?" No recalibration needed across model swaps. Also directly upgrades the existing cliff-detect rerank node (V16) from gap-shape heuristic to a calibrated margin.
**Ragbot fit**: implement inside the reranker port pipeline as an optional strategy decorator; per-bot enable via `plan_limits`; sentinel text must come from language_packs (domain/locale-neutral), not hardcoded English. T1-Smartness (reduces false-answer on weak evidence AND false-refuse on strong evidence).

### P2 — Numeric-fidelity post-check with tolerance modes (anti-HALLU-4-loại-số as *detector*, not overrider)
**File**: `app/core/RAG/rag_service/guardrails.py:117-423`; wired at `unified_pipeline.py:5741-5830` [FACT].

- `_extract_numeric_tokens()` normalizes numbers from answer + source docs: thousands separators, `%`/`k`/`m`/`b` suffixes, word multipliers ("3 million"→`3m`), currency-symbol strip, and canonical expansion (`2.5k` also matches `2500`) (`guardrails.py:119-214`).
- `check_numeric_fidelity(answer, docs)` → `{present, missing, union_source_numbers}` (`:237-271`).
- `check_numeric_precision(..., mode="standard|strict|academic")` computes per-number **deviation percent vs closest source value** with tolerance 5%/1%/0% and returns a `NumericDeviation` list (claim_value, closest source_value, deviation_percent, is_match) (`:301-423`).
- Pipeline records `metadata["numeric_fidelity"] = {present, missing, source_numbers}` + Prometheus counter `rag_numeric_mismatches_total`; corrective behavior is a config enum `continue|ask|decline|retry`, default **continue** (observe-only). `retry` does *targeted retrieval on the missing numbers* (`query + missing_token`) then regenerates (`unified_pipeline.py:5759-5820`).

**Why it matters for ragbot**: HALLU=0 is sacred and the 4-number-hallu taxonomy (fabricate/misinterpret/extrapolate/conflate) is currently enforced only by sysprompt + load-test judging. This gives a cheap deterministic **detector** producing a per-turn `numeric_fidelity` metric that can be logged in `request_steps`/load-test gates without violating sacred rule #10 — adopt in **observe-only mode** (behavior="continue"). The `ask/decline` overwrite behaviors in tldw (`:5825-5830` literally replaces `generated_answer` with English hardcoded text) **must NOT be ported** — that is app-override of the LLM answer. The `retry` behavior (re-retrieve on missing number, regenerate) is compatible if the regeneration is still bot-prompt-driven. Vietnamese number formats (`1.499.000đ`, `1tr499`) need locale-aware normalizer extension — the K1 "1tr499" bug (2026-05-25) is exactly this class.
**Ragbot fit**: T1. New pipeline node post-generate, flag default OFF → observe → gate load tests on `missing==0` for factoid turns.

### P3 — Hard-citation coverage metric (per-sentence support ratio)
**File**: `guardrails.py:456-527`; wired at `unified_pipeline.py:5360-5395` [FACT].

`build_hard_citations(answer, docs, claims_payload)` maps every answer sentence (or extracted claim) to supporting `(doc_id, start, end)` spans — exact substring first, then longest-64-char-window fallback (`guardrails.py:431-453`) — and returns `{sentences[], total, supported, coverage}`. Pipeline exports `rag_hard_citation_coverage` gauge; `require_hard_citations` + coverage<1.0 triggers configurable behavior (again default continue). Companion `build_quote_citations` verifies quoted spans byte-offset-exact with a `verified` bool per quote (`guardrails.py:547-585`).

**Why it matters for ragbot**: gives a *deterministic, LLM-free* groundedness proxy per turn — complements Coverage-rate metric (CLAUDE.md) and RAGAS faithfulness (LLM-judge, expensive). Byte-offset citations `(chunk_id, start, end)` are also the payload B2B consumers need from a headless platform. Ragbot's citations today are chunk-level; sentence→span mapping is a strict upgrade and cheap. T1 + T2 (observability).

### P4 — Chunking templates as data: DB-stored per-doctype pipelines + metadata classifier
**Files**: `app/core/Chunking/templates.py` (812 LOC), `Docs/Chunking/Chunking_Templates.md`, strategy registry `app/core/Chunking/strategies/__init__.py:18-45` [FACT].

- Template = JSON in DB: `{preprocessing[], chunking:{method,config}, postprocessing[]}`; methods = `words|sentences|paragraphs|tokens|semantic|structure_aware|json|xml|ebook_chapters|rolling_summarize|propositions` (doc), pluggable ops registry (`normalize_whitespace`, `extract_sections`, `clean_markdown`, `detect_language` / `add_overlap`, `filter_empty`, `merge_small`, `format_chunks`) (`templates.py:97-534`).
- Full CRUD API `/api/v1/chunking/templates` + validate endpoint + startup seeding of builtin library (doc: "Built-in templates are shipped under template_library and seeded into the DB on startup").
- **`TemplateClassifier.score()`** (`templates.py:759-794`): each template carries an optional `classifier` block (`media_types[]`, `filename_regex`, `title_regex`, `url_regex`, `min_score`) and the system scores templates against incoming doc metadata to auto-pick — weight 0.5 media-type + 0.5 regex hits, regex length-capped at 128 chars with ReDoS guard (`_rx_check`).
- **`TemplateLearner.learn_boundaries()`** (`templates.py:797-812`): derive boundary regexes from an example document (chapter/section/abstract/ATX-header patterns).
- Strategy dispatch is a flat `STRATEGY_REGISTRY: dict[str, type[ChunkingStrategy]]` (`strategies/__init__.py:18`) — same shape as ragbot's Port+Registry.

**Why it matters for ragbot**: this is the reference implementation of ragbot's own `template-per-doctype-chunking` skill, taken further: templates live in **DB, tenant-editable via API** (bot-owner-owns-everything compliant), selection is **config/classifier-driven not `if doctype==`**, and pre/post ops are a composable registry. Ragbot's AdapChunk B1–B4 completion (program charter) can adopt: (a) template JSON schema, (b) classifier block scored on structural metadata (NOT vocab — replace tldw's English `TemplateLearner` patterns with structural detection per ragbot's skills), (c) validate endpoint. T1+T3.

### P5 — Two-stage retrieval-time "late chunking" (media-level FTS hit → chunk on the fly)
**File**: `app/core/RAG/rag_service/database_retrievers.py:842-970` (`_late_chunk_media_documents`) [FACT].

When FTS matches at document level, the retriever chunks the parent text *at query time* (`chunk_text_hierarchical_flat`), scores each derived chunk against query terms, combines `0.1*parent_score + 0.9*chunk_score` with deterministic rank/index tie-breakers, propagates `start_char/end_char/section_path/ancestry_titles` metadata, and IDs chunks as `late_chunk:{media_id}:{idx}` (`:948-960`).

**Why it matters for ragbot**: a rescue path when stored chunking failed a given query (chunk boundaries cut the answer, or doc ingested before a chunking fix) **without re-ingesting the corpus**. Ragbot's known failure class "corpus has answer / retrieval misses because chunk granularity wrong" (spa-07 lesson, 2026-06-03) gets a runtime fallback. Cost: CPU-only re-chunk of a handful of parent docs. HYPOTHESIS: best gated per-bot as a fallback stage when top rerank score is below sentinel margin (combine with P1). T1.

### P6 — Self-correcting RAG as 6 explicitly-staged, independently-flagged nodes (CRAG-family)
**Files**: `document_grader.py` (Stage 1, LLM relevance grading with batch+timeout+fallback-to-score `grading_fallback_min_score`), rewrite loop (Stage 2, `unified_pipeline.py:4203+`), `web_fallback.py` (Stage 3, threshold + merge strategy prepend/append/interleave), `knowledge_strips.py` (Stage 4: partition docs into ~100-token sentence-boundary strips, grade each, pass only relevant strips to generation, `knowledge_strips.py:1-80+`), `quality_graders.py:577` `check_fast_groundedness` (Stage 5: 5s-timeout cheap groundedness check that can **skip** full claims extraction if confidence ≥0.9, `unified_pipeline.py:5405-5436`), utility grading (Stage 6, `:6184-6206`) [FACT].

**Why it matters for ragbot**: ragbot already has CRAG grader + rewrite_retry, but two pieces are new and valuable: (a) **knowledge strips** — sub-chunk relevance filtering between rerank and prompt assembly reduces noise tokens (T2 cost + T1 precision; ragbot's context-cliff cap is a cruder version); (b) **fast-groundedness-check-then-skip-expensive-verification** — a cost ladder for verification (cheap check gates expensive claims pipeline), directly applicable to keeping p95 down while adding P2/P3 checks. Also `grading_fallback_to_score` (if LLM grader times out, fall back to retrieval scores rather than dropping everything, `document_grader.py:25-51`) fixes the exact "CRAG grader rejects all → chunks_used=0" failure recorded in ragbot memory (project_multi_query_fix_20260515). T1.

### P7 — Post-generation claim verification with bounded self-repair + FVA (counter-evidence) 
**Files**: `post_generation_verifier.py` (497 LOC), `Claims_Extraction/` module (26 files: extractor registry heuristic/NER/LLM, span alignment, NLI/LLM/hybrid verifier, adjudicator, budget guard, falsification), `Docs/RAG/FVA_Pipeline.md` [FACT].

- `PostGenerationVerifier.verify_and_maybe_fix()` extracts claims from the answer, does **per-claim retrieval** to find support, computes `unsupported_ratio`, and if > threshold (default 0.15) runs ONE bounded repair pass under a time budget (`post_generation_verifier.py:120-274`); repair uses HyDE + multi-strategy rewrites when enabled.
- FVA pipeline (paper arXiv:2512.07015, per doc) adds **anti-context retrieval**: generate negation/contrary counter-queries per claim, retrieve contradicting evidence, adjudicate → statuses `VERIFIED|REFUTED|CONTESTED|UNVERIFIED`, with per-claim-type forcing (statistics/causal always falsified) and `max_budget_usd` (`Docs/RAG/FVA_Pipeline.md`).
- Claims budget guard: token/cost estimation + throttling + concurrency suggestion (`Claims_Extraction/budget_guard.py`, `monitoring.py` imports at `claims_engine.py:23-55`).

**Why it matters for ragbot**: this is the architectural home for HALLU=0 enforcement *above* sysprompt: verification emits **evidence-backed metrics** (unsupported_ratio per turn) and the repair loop re-retrieves rather than rewriting the answer text — compatible with sacred #10 if regeneration goes through the bot's own prompt. FVA's CONTESTED status is a principled way to handle corpus self-contradiction (multi-doc conflicts — a first-class ragbot concern). HYPOTHESIS: full FVA is heavy for p95≤8s target; adopt as async/offline eval + only-on-low-confidence online path (tldw itself gates by `enable_post_verification` + `adaptive_time_budget_sec`). T1.

### P8 — Upload validation defense-in-depth (multi-format ingest hardening)
**File**: `app/core/Ingestion_Media_Processing/Upload_Sink.py:410-745` (`FileValidator.validate_file`) [FACT].

Order: blocked-extension denylist (39 executable/script types, checked on BOTH claimed filename and on-disk name, `:538-562`) → per-media-type config (allowed ext/mime/size from config, "no rules configured → reject" `:583-593`) → size check with early short-circuit → claimed-extension check → **MIME sniff** (puremagic → python-magic → `mimetypes.guess_type` fallback, source recorded as `magic|python-magic|fallback`) with the hard rule *"if magic-detected MIME is disallowed, do NOT fall back to extension"* (`:669-681`) → controlled per-type relaxations (code/document/json may accept ambiguous MIME if extension is allowed — mirrors ragbot's "metadata refines, never dictates") → **YARA malware scan** (`:735-739`) → archive content validation (zip-bomb/nesting guards, `validate_archive_contents:745+`) → HTML/XML sanitization helpers (`:1068-1178`).

**Why it matters for ragbot**: ragbot's canonical ingest has mime→ext→byte-sniff but no security tier. For a headless B2B platform accepting arbitrary tenant uploads, the blocked-extension + "detected-MIME-wins, no downgrade to extension" + YARA hook + archive-content scan are the missing production hardening. Media-type validation configs are **data** (per-type dict), not code branches. T2/GA-hardening.

### P9 — Pluggable OCR/VLM backend registries + per-page OCR fallback mode
**Files**: `Ingestion_Media_Processing/OCR/registry.py` (9 backends: tesseract_cli, nemotron_parse, points_reader, deepseek_ocr, hunyuan_ocr, dots_ocr, dolphin_ocr, llamacpp_ocr, chatllm_ocr — dict registry, auto-detection priority = dict order, config override), `PDF/PDF_Processing_Lib.py:493-620` [FACT].

`process_pdf()` params: `parser: "pymupdf4llm"|"pymupdf"|"docling"` (+ separate `mineru_adapter.py`), `ocr_mode="fallback"` with `ocr_min_page_text_chars=40` — i.e., OCR runs **only on pages whose native text layer is under 40 chars**, and results carry `ocr_confidence` metadata that retrieval can gate on (`guardrails.py:702-721` `gate_docs_by_ocr_confidence` drops low-confidence OCR docs at query time). VLM backends (docling_vlm, hf_table_transformer) with `vlm_detect_tables_only=True` for table-region extraction, plus **VLM late chunking** at retrieval time on top-k docs only (`unified_pipeline.py:3885+`, `vlm_late_chunk_top_k_docs=3`).

**Why it matters for ragbot**: mixed scanned+digital PDFs are a real multi-format gap; per-page-fallback OCR is the right cost shape (never OCR whole doc), and **ocr_confidence as chunk metadata gated at retrieval** (not ingest-time discard) preserves lossless-coverage while protecting HALLU. Registry shape is ragbot-native (Port+Registry). T1 multi-format.

### P10 — Embeddings A/B harness with per-arm query embedding + stable IR metrics
**Files**: `Evaluations/embeddings_abtest_runner.py` (arms = `provider:model`, L2-normalized, embed query set per arm, vector-only comparison "without modifying retrievers"), `Evaluations/metrics_retrieval.py` (pure-function `hit_at_k/recall_at_k/mrr/ndcg`), plus jobs/worker/repository/service files for async A/B runs [FACT: file list + heads].
**And** CI gating that splits metrics by determinism: `rag_service/quality_gating.py:1-100` — **stable** metrics (precision/recall/MRR/nDCG/latency) fail CI hard (exit 1); **unstable** LLM-judged metrics (faithfulness/relevance/hallucination) only warn (exit 2), with `lower_is_better` list for hallucination/latency [FACT].

**Why it matters for ragbot**: two direct hits on ragbot pain points. (a) Every embedder migration (Jina→ZE→Voyage pending) was ad-hoc re-verified; an arms-based A/B harness embedding the golden question set per candidate model + hit@k/MRR vs ground-truth chunk IDs makes migrations measured (rule #0 compliant). (b) The stable/unstable gate split is exactly how to wire ragbot's load-test gates into CI without LLM-variance flakiness — HALLU-fabricate stays a hard gate (deterministic trap set), RAGAS faithfulness becomes warn-tier. test-health + T2.

### P11 — "Why these sources" explainability block
**File**: `unified_pipeline.py:4712-4791` [FACT]. Computes per-answer `diversity` (unique hosts/sources ÷ n), `freshness` (fraction of docs ≤90 days), `topicality` (min-max normalized score mass), + top-10 context digest `{id,title,score,url,source}` into `metadata.why_these_sources`.
**Why it matters**: cheap, deterministic, zero-LLM answer-audit metadata for B2B consumers and for debugging refuse/answer decisions in load tests. Trivial to add to ragbot's response metadata. T2/UX.

### P12 — Query→granularity router (rule-based, no LLM)
**File**: `rag_service/granularity_router.py:60-279` [FACT]. Classifies broad/specific/factoid by regex+length+wh-word heuristics → maps to retrieval parameter bundles: BROAD→document-level (top_k=5, parent expansion ON, parent_max_tokens=2000), FACTOID→passage-level (top_k=15, multi-vector spans 200 chars), SPECIFIC→chunk-level (top_k=10) (`:209-252`). Decision object carries confidence + reasoning.
**Why it matters**: ragbot has intent enum but doesn't vary retrieval *granularity* per intent; the type→param-bundle mapping (esp. factoid→smaller spans+higher k; summary→parent docs) is a smartness lever that costs ~0 latency. **Caveat [FACT]**: patterns are hardcoded English (`granularity_router.py:22-47`) — ragbot must move patterns into language_packs per multilingual-no-vocab skill. T1.

### P13 — Reciprocal-rank-fusion hybrid with weighted alpha + adaptive weights hook
**File**: `database_retrievers.py:1881-1966` [FACT]. FTS and vector run under `asyncio.gather`, then RRF (k=60) with **alpha-weighted combination** `(1-alpha)*fts_rrf + alpha*vec_rrf` — one continuous knob 0=FTS-only…1=vector-only, per-request; `adaptive_hybrid_weights` + `enable_intent_routing` flags at pipeline level tune alpha by query intent (`unified_pipeline.py:1181-1184, 2620+`). Ragbot's RRF is unweighted; alpha-per-intent (e.g., factoid/numeric → lean BM25, conceptual → lean vector) is a small change with measurable retrieval lift potential. HYPOTHESIS on lift size — must A/B with P10 harness before claiming. T1.

---

## 2. Patterns worth knowing but NOT recommended for ragbot (with reasons)

| Pattern | Evidence | Why not |
|---|---|---|
| 229-param mega-function pipeline | `unified_pipeline.py:1160-1502` (param count measured) | Violates ragbot config-chain/zero-hardcode; ragbot's LangGraph nodes + per-bot config already superior. Adopt the *stages*, not the shape. |
| Answer overwrite on failed checks ("ask" appends English note; "decline" replaces answer) | `unified_pipeline.py:5825-5830, 5384-5387` | Direct violation of sacred rule #10 (app never injects/overrides LLM answer). Port detectors only; behaviors must go through bot-owner config/refusal templates. |
| Hardcoded English heuristics everywhere (granularity patterns, injection regexes, table serializer templates "Row X … is …", proposition markers, stopword lists) | `granularity_router.py:22-47`, `guardrails.py:57-67`, `table_serialization.py:283-374`, `strategies/propositions.py:44-60`, `rag_evaluator.py:84-89` | Violates domain-neutral/multilingual-no-vocab. Every adopted module needs the vocab lifted into language_packs. |
| Per-user SQLite DB files as tenancy | CLAUDE.md of tldw ("per-user content DB under `Databases/user_databases/<user_id>/`"), `database_retrievers.py` path-validation error taxonomy `:49-119` | Ragbot is Postgres+RLS multi-tenant; physical-file isolation doesn't transfer. The *path-validation error class hierarchy* is nice but N/A. |
| ChromaDB-centric vector store w/ vs_<collection> tables in pgvector adapter | `vector_stores/pgvector_adapter.py:1-8` ("separate table named vs_<sanitized_collection>") | Table-per-collection conflicts with ragbot's single `document_chunks` + RLS scoping. Their Prometheus histograms per collection (`pgvector_adapter.py:60-82`) are a good idea though. |
| AdaptiveCache auto-tuning semantic-cache threshold by hit-rate (±0.05 every 100 lookups) | `semantic_cache.py:530-611` | HYPOTHESIS: dangerous for ragbot — loosening similarity threshold on low hit-rate raises wrong-answer-from-cache risk (HALLU-adjacent). Ragbot semantic cache thresholds should stay explicit per-bot config. Track the *stats*, skip the auto-tune. |
| Web-search fallback (Stage 3) | `web_fallback.py`, `unified_pipeline.py:4626+` | Ragbot bots are corpus-grounded by contract; injecting web results would break faithfulness guarantees. Interesting only for a future explicit "research bot" product tier. |
| Injection down-weighting (multiply doc score ×0.5 on regex hit) | `guardrails.py:85-114` | Concept fine (docs-as-untrusted-input), but regex list is English + easily bypassed; ragbot should treat as observe/annotate metric, not score mutation, until measured. |

---

## 3. Axis-by-axis findings

### multi-format ingest
- Formats: video/audio (yt-dlp+whisper family), PDF (pymupdf/pymupdf4llm/docling/mineru), EPUB (ebook_chapters strategy 718 LOC), DOCX, HTML, Markdown, XML, MediaWiki dumps, Email (`Ingestion_Media_Processing/Email/Email_Processing_Lib.py` 1,250 LOC), Plaintext [FACT: dir listing + tldw CLAUDE.md].
- Every processor returns one normalized envelope: `{status, input_ref, media_type, parser_used, content, metadata, chunks, analysis, error, warnings}` (`PDF_Processing_Lib.py:546-585`) — same "one canonical output contract" idea as ragbot's structured-markdown contract, but **contract = dict shape, content = flat markdown/text**, weaker than ragbot's structural-block direction. `RAG/block_to_chunks.py:243` shows the newer path: normalize external **Block payloads** (with bbox quads, timestamps ms, citation spans) into `{text, metadata}` chunks — evidence they are mid-migration toward block-based ingest [FACT].
- **Adopt**: envelope's `parser_used` + `warnings[]` + per-stage counters (`log_counter("pdf_text_extraction_attempt", labels={parser})`, `PDF_Processing_Lib.py:407`) for ragbot ingest observability; P8 validation; P9 OCR.

### multi-doc / corpus-level
- Parent/sibling/window expansion strategies enum (`parent_retrieval.py:21-27`: PARENT_ONLY, SIBLINGS, WINDOW, HIERARCHICAL, SEMANTIC_FAMILY) with `ParentDocumentIndex` chunk-position maps [FACT]. Ragbot has parent_chunk_id JOIN; the *strategy enum + window API* is a cleaner surface.
- Evidence chains (`evidence_chains.py` 548 LOC) + progressive evidence accumulation with round/time budgets (`unified_pipeline.py:4019+`, `accumulation_max_rounds=3`) for multi-hop cross-doc questions [FACT files exist; not fully traced]. FVA CONTESTED status handles cross-doc contradiction (P7).

### multi-bot / multi-tenant
- AuthNZ is genuinely mature for a single-box app: JWT+API-key dual mode, `org_rbac.py`, `orgs_teams.py`, `llm_budget_guard.py` + budget middleware (per-user LLM spend caps), `api_key_crypto/rotation`, `ip_allowlist.py`, MFA, lockout tracker, audit integrity [FACT: file listing `app/core/AuthNZ/`]. **Budget-guard-as-middleware** is the adoptable idea (per-tenant LLM budget enforcement before dispatch); ragbot has rate limits but not spend caps.
- RAG isolation = per-user DB path + `index_namespace` collections (`database_retrievers.py:1596-1608` incl. wildcard multi-namespace query) — weaker than ragbot RLS; nothing to adopt for isolation itself.

### T1-smartness (retrieval/generation quality)
- P1 sentinel gate, P2 numeric fidelity, P3 hard citations, P5 late chunking, P6 strips + fast-groundedness ladder, P7 claim verification/FVA, P12 granularity routing, P13 weighted RRF (all above).
- Also present: HyDE (`hyde.py` 109 LOC), PRF term mining metadata-only (`prf.py:1-11` — deliberately observe-first: "callers can introspect suggested expansions without changing retrieval behaviour" — good rollout discipline), 5-strategy query expansion (synonym/multi-query/acronym/domain/entity, `query_expansion.py:26-509`), query decomposition with per-subquery time/doc budgets + concurrency cap (`unified_pipeline.py:1307-1312`), graph-augmented retrieval flags (`:1313-1319`).

### T2-cost-perf
- **Time/doc/cost budgets on every expensive feature** is the systemic pattern: `subquery_time_budget_sec`, `accumulation_time_budget_sec`, `adaptive_time_budget_sec`, `synthesis_time_budget_sec`, claims `max_budget_usd` + token estimator, `grading_timeout_sec` [FACT: signature + budget_guard.py]. Ragbot has timeouts but not per-feature *cost* budgets — worth adopting for the multi-LLM-call features (decomposer, CRAG, verification).
- Cost ladder: fast groundedness (5s) gates claims extraction (P6); two-tier rerank = cheap CE shortlist before LLM scoring (P1); rewrite_cache.py caches query rewrites.
- `track_cost` per-request cost tracking flag + `rag_phase_duration_seconds{phase}` histograms (`advanced_reranking.py:1590-1596`).

### T3-design
- Registry pattern used consistently (chunking strategies, OCR backends, VLM backends, vector-store factory, claims extractor registry, reranker factory `create_reranker:1435`) — validates ragbot's Port+Registry doctrine at scale [FACT].
- Narrow-exception discipline is mixed: many curated exception tuples (`_RAG_EVAL_NONCRITICAL_EXCEPTIONS`, `rag_evaluator.py:23-40`) but also `# noqa: BLE001` broad catches; the giant pipeline wraps nearly every stage in try/except-with-`result.errors.append` — **graceful stage degradation**: any stage failure downgrades to metadata error, answer path continues [FACT: pattern throughout unified_pipeline.py]. HYPOTHESIS: for ragbot, this is right for *observability* stages, wrong for *safety* stages (verification failing silently must not unlock an ungated answer).

### test-health
- 3,141 test files repo-wide; 121 in tests/RAG + tests/RAG_NEW (unit/integration/property split + TEST_STRATEGY.md) [FACT: find|wc]. Property-based tests present for pipeline. DI-for-tests visible in TwoTierReranker (`cross_reranker/llm_reranker` injectable, `advanced_reranking.py:1491`) and PostGenerationVerifier (`claims_runner` injectable). Not executed here — no claim on pass rate [FACT: not run].

---

## 4. Concrete adoption shortlist for ragbot (effort-ordered)

| # | What | Ragbot change | Tier | Effort (HYPOTHESIS) |
|---|---|---|---|---|
| 1 | numeric_fidelity observe-only node (P2, VN-locale normalizer) | new post-generate node + `request_steps` metric + load-test gate | T1 | S (1 file + tests) |
| 2 | hard-citation coverage metric (P3) | post-generate node; expose in response metadata | T1 | S |
| 3 | why_these_sources metadata (P11) | assemble from existing chunk metadata | T2 | XS |
| 4 | stable/unstable CI gate split (P10b) | wrap existing load-test gates | test-health | S |
| 5 | sentinel-calibrated rerank gate (P1) | reranker-port decorator strategy + per-bot flag; sentinel text in language_packs | T1 | M |
| 6 | embeddings A/B arms harness (P10a) | scripts/ + golden set + hit@k/MRR | test-health | M |
| 7 | granularity→param-bundle routing (P12, patterns in language_packs) | extend intent router output | T1 | M |
| 8 | alpha-weighted RRF + per-intent alpha (P13) | rrf_fuse node param | T1 | S code + M eval |
| 9 | knowledge strips between rerank and prompt (P6) | new node, flag OFF | T1/T2 | M |
| 10 | upload hardening: blocked-ext + MIME-wins rule + archive scan (P8) | documents/create validation layer | T2/GA | M |
| 11 | per-page OCR fallback + ocr_confidence gating (P9) | parser adapter + chunk metadata + retrieve filter | T1 multi-format | L |
| 12 | chunking templates in DB + classifier block (P4) | AdapChunk B-series alignment | T1/T3 | L |
| 13 | post-gen claim verification w/ bounded repair, then FVA offline (P7) | verification service, async first | T1 | L–XL |
| 14 | retrieval-time late chunking rescue (P5) | fallback stage gated by P1 margin | T1 | L |

All effort/lift figures are HYPOTHESIS until measured per rule #0 — each item must land behind a flag with A/B load-test evidence before "adopted" can be claimed.

---

## 5. Cross-check vs ragbot sacred rules (for anything ported)

1. **No app-inject/override (rule #10)**: tldw's `ask/decline/retry-note` behaviors and CRAG "decline" answers are override patterns — port only detectors + metadata; behaviors route to bot-owner config (`oos_answer_template`, guardrail `response_message`). [Violating examples: `unified_pipeline.py:5825-5830`, `:5384-5387`.]
2. **Domain-neutral / multilingual**: every regex/wordlist named in §2 row 3 must become language_pack data.
3. **Zero-hardcode**: tldw uses env-vars + literal defaults (`_get_float_env("RAG_MIN_RELEVANCE_PROB", 0.35)`, `advanced_reranking.py:1683`); ragbot equivalents go to `shared/constants.py` + `system_config`/`plan_limits`.
4. **Strategy+DI**: registry shapes are compatible; wrap as ports (reranker decorator, ocr_port, verification_port).
5. **4-key identity / RLS**: nothing in tldw touches this; all adopted nodes operate post-resolve on `record_bot_id`-scoped data.

— end of report —
