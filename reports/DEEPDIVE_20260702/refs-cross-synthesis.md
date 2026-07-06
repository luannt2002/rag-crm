# CROSS-SYNTHESIS — all 6 `_external_refs` repos vs the RAG pipeline, stage by stage

- **Slug**: refs-cross-synthesis · **Date**: 2026-07-02
- **Scope**: architecture-level survey of ALL repos under `/var/www/html/ragbot/_external_refs/` — `adaptive-chunking`, `RAG-Anything`, `open-notebook`, `tldw_server`, `PDF2Audio`, `llama-cookbook` — synthesized into a per-stage comparison (ingest → parse → chunk → index → retrieve → rerank → generate → ground/cite → eval) and a composite "best-of-all" architecture mapped onto ragbot.
- **Builds on** (deep reports already in this directory, all read in full this session):
  - `reports/DEEPDIVE_20260702/refs-adaptive-chunking.md` (265 lines)
  - `reports/DEEPDIVE_20260702/refs-rag-anything.md` (204 lines)
  - `reports/DEEPDIVE_20260702/refs-open-notebook.md` (251 lines)
  - `reports/DEEPDIVE_20260702/refs-tldw-server.md` (195 lines)
  PDF2Audio and llama-cookbook had no prior report — surveyed directly this session (READMEs, module layout, entry points, notebook cells).
- **Method / rule #0 discipline**: every claim carries evidence — `file:line` for code read this session, or a citation `[refs-X §n]` into one of the four deep reports (whose own `file:line` evidence was spot-verified this session; see §0.2). **FACT** = read in code / verified artifact. **HYPOTHESIS** = labeled inference, not runtime-verified. Nothing was executed; this is a read-only static synthesis. The only file created is this report.

---

## 0. Sources and verification

### 0.1 The six repos — one-line identity (all FACT)

| Repo | Identity | Size (measured) | Tenancy model | RAG pipeline coverage |
|---|---|---|---|---|
| **adaptive-chunking** (ekimetrics, LREC 2026) | research code for evaluate-then-select chunking with 5 intrinsic metrics | ~9,055 LOC Py [refs-adaptive-chunking header] | none (offline corpus) | parse + chunk + eval only |
| **RAG-Anything** (HKUDS, on LightRAG) | multimodal (text/image/table/equation) ingest + KG library | 23,866 LOC Py [refs-rag-anything header] | none — 0 "tenant" hits [refs-rag-anything §preamble] | full ingest→query, retrieval delegated to LightRAG |
| **open-notebook** (lfnovo) | single-user NotebookLM alternative, SurrealDB | 19,866 LOC Py backend [refs-open-notebook header] | single-user, "insecure dev-only" auth [refs-open-notebook §9.6] | full product loop: ingest→chunk→embed→search→ask→cite |
| **tldw_server** (rmusser01) | single-box personal research assistant, SQLite-first, GPLv3 | `app/` = 329,374 LOC / 2,147 files; RAG core 47,877 LOC [refs-tldw-server header] | per-user DB files, not RLS [refs-tldw-server §preamble] | the widest: every stage incl. rerank calibration + verification + eval |
| **PDF2Audio** (lamm-mit/MIT) | Gradio app: PDF→podcast/lecture/summary audio | single `app.py`, 978 LOC (`wc -l` this session) | none (stateless app) | parse (flat) + generate only — **not a RAG system** |
| **llama-cookbook** (meta-llama) | official Llama recipes — **vendored copy is a 26-file sparse subset** (`find` count this session: 0 `.py`, 7 `.ipynb`): NotebookLlama (PDF→podcast), video_summary, build_with_llama_4 (5M ctx), build_with_llama_api | 26 files total | n/a (notebooks) | parse (flat) + generate + long-context alternative; **the upstream repo's RAG recipes are NOT in the vendored subset** (README.md:37 references `getting-started/RAG` — directory absent on disk) |

Snapshot anchors: PDF2Audio `e30e588` (2025-04-18), llama-cookbook `2f22a9e` (2025-11-03) — `git log -1` this session; other four anchored in their respective reports.

### 0.2 Spot-verification of the prior reports (rule #0)

Before building on the four existing reports, 8 load-bearing claims were re-verified against source this session — all confirmed exact:

- adaptive-chunking `find_best_method` weighted-NaN-skipping argmax docstring at `paper/analysis.py:294-299`; `repair_gaps_between_chunks` at `postprocessing.py:128-135` ✔
- RAG-Anything typed `content_list` contract at `raganything/processor.py:2100-2110`; extension-only routing + "attempting to parse as PDF" fallback at `raganything/parser.py:1400-1421` ✔
- open-notebook citation-ID allowlist (`ids = [r["id"] for r in results]` → prompt payload) at `open_notebook/graphs/ask.py:104-110` ✔
- tldw sentinel document `id="sentinel:irrelevant"` appended to rerank pool at `advanced_reranking.py:1568-1580`; `check_numeric_fidelity` at `guardrails.py:237-250`; `unified_rag_pipeline` mega-signature at `unified_pipeline.py:1160` ✔

Ragbot-side anchors re-verified: byte-sniff detector `src/ragbot/infrastructure/parser/registry.py:123-132`; text-API chunk call `ingest_stages.py:770`; observe-only coverage gate `ingest_stages.py:889-895`; ~21-node query graph (`ls src/ragbot/orchestration/nodes/` = 28 node files incl. retrieve/rerank/generate/grade/rewrite_retry/rrf_round_robin/neighbor_expand/mmr_dedup) ✔ — all FACT this session.

### 0.3 New surveys this session (the two repos without prior reports)

**PDF2Audio** (FACT, all from `app.py` read this session):
- Parse = flat `pypdf` text: `PdfReader` + `page.extract_text()` joined with `\n\n` (`app.py:614-620`); routing by extension only, `.pdf/.txt/.md/.mmd` (`app.py:612-621`). No structure, no tables, no offsets — weakest parse of the six.
- **Schema-constrained generation with retry-on-validation**: output typed as Pydantic `Dialogue(BaseModel)` of `DialogueItem(speaker, text)` (`app.py:509-515`), the LLM call wrapped `@retry(retry=retry_if_exception_type(ValidationError))` over the promptic `@llm` decorator (`app.py:626-627`) — malformed model output automatically re-asked until schema-valid.
- **Instruction templates as data**: `INSTRUCTION_TEMPLATES` dict with podcast/lecture/summary/short-summary variants plus FR/DE/ES/PT/HI/ZH localized template sets (`app.py:33-428`) — template-per-use-case + per-language, though hardcoded in source (their anti-pattern; ragbot's `language_packs` DB is the corrected form).
- **Human-in-the-loop regeneration**: user-edited transcript + feedback re-enter the prompt wrapped in explicit `<edited_transcript>` / `<requested_improvements>` tags (`app.py:654-659`), driving iterative regeneration (`edit_and_regenerate`, `app.py:729-734`).
- Per-line TTS fan-out over `ThreadPoolExecutor` with ordered reassembly (`app.py:682-695`).

**llama-cookbook (vendored subset)** (FACT, notebook cells extracted this session):
- **NotebookLlama** 4-stage PDF→podcast: pypdf extract with 100k-char cap (Step-1 cell 10) → word-bounded 1,000-char chunks (cells 18, 22) → **Llama-3.2-1B as text normalizer** with the prompt "DO NOT START SUMMARIZING THIS, YOU ARE ONLY CLEANING UP THE TEXT" (cell 16) → 70B transcript writer → 8B dramatizer → TTS (`NotebookLlama/README.md:19-22`).
- **Per-stage model tiering** is the architectural idea: smallest model that can do the job per stage (1B clean / 70B create / 8B rewrite) — the same shape as ragbot's `bot_model_bindings.purpose` and the Haiku-partial-only policy.
- **video_summary.ipynb**: the canonical long-document ladder — `stuff` (fails over 8k ctx, cell 27) vs `refine` vs `map_reduce` over `RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=1000)` (cells 20-25).
- **build_with_llama_4.ipynb**: 5M-token context window used to ingest an entire repo directly — the **long-context-instead-of-RAG** branch, the external validation for a "pinned full-context" mode (cf. open-notebook's Chat mode, §7 below).
- Limitation stated per rule #0: conclusions about llama-cookbook apply to the **vendored 26-file subset only**; the upstream repo's RAG/fine-tuning recipe trees are not on disk here and were not surveyed.

---

## 1. Stage-by-stage cross-cutting comparison

Legend for the per-stage tables: 🥇 strongest of the six for that stage; ✋ ragbot already ahead — do not regress; ✗ nothing to take. Ragbot baseline included as the reference row.

### 1.1 INGEST (acquisition, validation, idempotency, lifecycle)

| Repo | Approach | Verdict |
|---|---|---|
| RAG-Anything | parse cache keyed md5(path+mtime+parser+config); content-based `doc_id` (per-type content signature); 6-state doc status with **two independent completion axes** (text vs multimodal) + stage-labeled errors (`processor.py:48-96, 200-237, 648-676` [refs-rag-anything §4]) | 🥇 idempotency + lifecycle |
| open-notebook | ONE canonical funnel (file/URL/text → one `content_state` → one graph); async 202+job / sync modes; **soft-failure sentinel detection** (extractor error-as-content → raise, `graphs/source.py:82-91`); **retry/refresh from persisted asset** through the same pipeline (`sources.py:823-951`) [refs-open-notebook §2] | 🥇 funnel + retry semantics |
| tldw_server | `FileValidator`: blocked-ext denylist → per-type config → size → MIME sniff with "**detected-MIME-wins, never downgrade to extension**" → YARA scan → archive/zip-bomb guards (`Upload_Sink.py:410-745` [refs-tldw-server P8]) | 🥇 security hardening |
| adaptive-chunking | offline file loop, no service ingest | ✗ |
| PDF2Audio | Gradio upload, ext-check only (`app.py:612-621`) | ✗ |
| llama-cookbook | notebook file paths | ✗ |
| **ragbot today** | canonical `POST /api/ragbot/documents/create`, `X-Idempotency-Key`, mime→ext→byte-sniff (`registry.py:123-179`), Redis Streams worker | ahead on funnel + detection; missing: parse-cache/content-doc_id discipline, per-stage status axes, security tier, retry-from-asset endpoint |

**Stage verdict**: no single winner — the composite ingest is **open-notebook's lifecycle × RAG-Anything's idempotency × tldw's validation**, on ragbot's existing funnel. Ragbot's type detection is the strongest of all seven systems (RAG-Anything routes by extension only and tries unknown files "as PDF", `parser.py:1415-1421` — the exact bug class ragbot's byte-sniff kills).

### 1.2 PARSE (format → structured representation)

| Repo | Output contract | Verdict |
|---|---|---|
| adaptive-chunking | `{pages, full_text, split_points[char offsets], titles[{start,end,level}]}` — **parser-emitted gold block boundaries + heading interval tree in char space**; suppression rules encode chunk wisdom at parse time (no split after heading / before footnote); `<Table>/<Figure>/<Formula>/<!-- PageBreak -->` tags survive the markdown hop (`parsing.py:12-23, 433-440` [refs-adaptive-chunking §2.2-2.3]) | 🥇 structural/offset contract |
| RAG-Anything | typed `content_list`: `text/image/table/equation/<generic>` blocks with `page_idx`, captions, footnotes; **open taxonomy** (unknown type → GenericProcessor); **alias-tolerant readers** at the choke point; **per-block sentinel degradation** (failed image → `[Image processing failed]` text block) + **zero-block fail-loud** floor (`processor.py:2100-2110, 567-568`; `parser.py:1884-1907` [refs-rag-anything §2.4, §3]) | 🥇 modality contract + degradation |
| tldw_server | normalized envelope `{status, parser_used, content, metadata, warnings[]}`; 9-backend OCR registry; **per-page OCR fallback** (`ocr_min_page_text_chars=40`) with `ocr_confidence` carried as metadata and **gated at retrieval time**, not discarded at ingest (`PDF_Processing_Lib.py:493-620` [refs-tldw-server P9]) | 🥇 OCR economics + confidence provenance |
| open-notebook | delegated to external `content-core` lib; sentinel detection is its contribution (counted under ingest) | — |
| PDF2Audio | flat `page.extract_text()` (`app.py:614-620`) | ✗ weakest |
| llama-cookbook | flat pypdf + **LLM-as-normalizer** (1B model cleans encoding garbage under a strict no-summarize prompt, Step-1 cell 16) | pattern noted; see warning below |
| **ragbot today** | registry parsers → structured markdown; OCR-path `Block` dataclass (`is_atomic`, `context_before`, `page_number`) but **no char spans, no modality for embedded figures/equations** (`document.py:41-51`; grep: 0 image/equation handling in `infrastructure/parser/` [refs-rag-anything §9]) | mid-field: ahead of 4 repos, behind the composite |

**Stage verdict**: the composite parse contract is the **union of two orthogonal winners** — RAG-Anything's *modality* axis (typed blocks, open taxonomy, degradation) and adaptive-chunking's *offset* axis (gold split_points + title spans). Neither repo has the other's half. tldw adds the third axis: *confidence* (ocr_confidence per block). Ragbot's `Block` entity already has the slots for 2 of 3 axes and none of the data flowing (Layer-6 block pipeline unwired, `ingest_stages.py:770` still calls the string API [refs-adaptive-chunking H2/H3]).

**Warning (HYPOTHESIS, HALLU-adjacent)**: NotebookLlama's LLM-as-normalizer is fine for a podcast product but is an anti-pattern for a RAG *corpus*: an LLM rewriting pass before chunking mutates ground truth (numbers, names) with no lossless-coverage check possible afterwards. Ragbot's lossless char-coverage invariant is incompatible with it — do not adopt for corpus ingest.

### 1.3 CHUNK

| Repo | Approach | Verdict |
|---|---|---|
| adaptive-chunking | **evaluate-then-select**: chunk each doc with N methods, score the *real outputs* with 5 intrinsic metrics (SC/ICC/DCC/BI/RC), weighted argmax per doc; lossless invariant: `check_chunk_gaps` + `repair_gaps_between_chunks` + **assert after every strategy** (`analysis.py:294-327`; `postprocessing.py:66-151`; `split_documents.py:112-155`); token-budgeted everything incl. binary-search token-boundary splits; `group_chunks` packs pre-chunked blocks without cutting them [refs-adaptive-chunking §2.5-2.8] | 🥇 selection loop + lossless discipline |
| tldw_server | **chunking templates as data in DB**, tenant-editable via CRUD API: `{preprocessing[], chunking{method,config}, postprocessing[]}` + metadata **classifier block** auto-picking a template (media_types + regex, ReDoS-guarded); 11 methods behind a flat strategy registry (`templates.py:97-812` [refs-tldw-server P4]) | 🥇 templates-as-config governance |
| open-notebook | tiktoken-sized (400 tok = documented 20% headroom under 512-tok embedders), markdown/HTML header split → secondary re-split, **never-return-zero-chunks guard** (`chunking.py:33-118, 483-491` [refs-open-notebook §3]) | calibration rationale + invariant |
| RAG-Anything | multimodal blocks atomic 1:1 block→chunk; text flattened heading-blind into LightRAG token chunker (`utils.py:101-119` [refs-rag-anything §2.5]) | atomicity yes, text chunking weak |
| PDF2Audio | none (whole text into one prompt) | ✗ |
| llama-cookbook | word-bounded 1,000-char chunks (Step-1 cell 18); tiktoken recursive splitter in video_summary (cell 20) | ✗ baseline only |
| **ragbot today** | AdapChunk: profile→rule-select→strategy dispatch (hdt/semantic/recursive/hybrid/proposition/table_csv), multi-row table-header merge (**exceeds all six**), VN structural, coverage gate **observe-only**, selector never scores real output — own bake-off: 0/8 oracle agreement, +0.001 lift (`reports/bakeoff_chunking_20260620.md` [refs-adaptive-chunking H1/H6]) | strongest table/multilingual machinery; weakest link = selection feedback + no repair |

**Stage verdict**: **adaptive-chunking wins the loop, tldw wins the governance, ragbot wins tables/multilingual.** Composite = ragbot's strategy library + adaptive-chunking's evaluate-then-select (offline bake-off cadence writing per-doc `chunking_policy`) + gap **repair** (not just detect) + assert-per-strategy + tldw's DB-template/classifier surface as the per-bot config schema. Token accounting replaces char accounting (adaptive-chunking `chunking_utils.py:4-16`; open-notebook's headroom rationale).

### 1.4 INDEX (storage, embedding, derived representations)

| Repo | Approach | Verdict |
|---|---|---|
| open-notebook | job-queue-first embedding; **blocklist retry** (`stop_on=[ValueError, ConfigurationError]`, retry everything else w/ exp-jitter); idempotent re-embed (delete+bulk insert, count-mismatch = hard error); **mixed-dimension guard in vector SQL** (`array::len(embedding)=array::len($query)`); **insights layer** — LLM summaries stored as separately-embedded, separately-citable rows unioned into the same search (`embedding_commands.py:173-465`; `9.surrealql` [refs-open-notebook §4, §1.3]) | 🥇 embed ops + derived-artifact layer |
| RAG-Anything | **dual-representation modal chunk** (raw structure + LLM caption in one chunk → BM25 hits cells, dense hits narrative); modal entity + `belongs_to` edges (w=10.0) into KG; 4-way storage of modal analysis (`prompt.py:328-353`; `processor.py:1391-1453` [refs-rag-anything §5]) | 🥇 multimodal representation |
| tldw_server | FTS5 + Chroma; pgvector adapter = table-per-collection (anti-pattern vs RLS); multi-vector spans; per-collection Prometheus histograms [refs-tldw-server §2 row 5] | mixed; metrics idea only |
| adaptive-chunking | n/a (offline; jina embedder shim only) | ✗ |
| PDF2Audio / llama-cookbook | none | ✗ |
| **ragbot today** | pgvector HNSW + BM25, per-bot embed bindings, RLS-scoped `document_chunks`, Anthropic-CR `chunk_context` enrichment | structurally ahead of all six (only production-grade multi-tenant store in the set); missing: dimension guard, doc-level summary rows, table-description chunks, page metadata persisted (`ingest_helpers.py:188-198` has no page column [refs-adaptive-chunking M4]) |

**Stage verdict**: ragbot's store is the strongest base; the imports are **representation enrichments**: (a) insights/summary rows per doc (RAPTOR-lite, open-notebook), (b) dual-representation table/figure chunks (RAG-Anything), (c) one-line dimension guard during re-embed windows (open-notebook — ragbot burned on exactly this in the Jina→ZE migration, memory `feedback_v2_bug_lessons`), (d) per-chunk page metadata (adaptive-chunking `get_page_info`).

### 1.5 RETRIEVE

| Repo | Approach | Verdict |
|---|---|---|
| tldw_server | the deepest stack of the six: **alpha-weighted RRF** (`(1-alpha)*fts + alpha*vec`, per-intent adaptive hook, `database_retrievers.py:1881-1966`); **query→granularity router** (broad/specific/factoid → param bundles: top_k, parent expansion, span size — rule-based, 0 LLM calls, `granularity_router.py:60-279`); **retrieval-time late chunking** (doc-level FTS hit → re-chunk parent on the fly, rescue when stored boundaries are wrong, `database_retrievers.py:842-970`); parent/sibling/window expansion enum; HyDE; 5-strategy expansion; decomposition with per-subquery time/doc budgets [refs-tldw-server P5, P12, P13] | 🥇 overall |
| open-notebook | **agentic ask plan**: LLM strategy node emits ≤5 searches each carrying *extraction instructions* for the downstream answerer; parallel fan-out; **parent-doc aggregation** (chunk hits → parent ranked by best chunk, matching fragments as evidence, `ask.py:29-95`; `4/9.surrealql` [refs-open-notebook §5.2, §6]); fail-loud floor on search outage | 🥇 answer-shaped search plan + citation-ready result shape |
| RAG-Anything | delegate to LightRAG modes; **VLM-enhanced query**: retrieve prompt → swap image paths for base64 → generator sees actual pixels (`query.py:349-420` [refs-rag-anything §6]) | multimodal query composition |
| llama-cookbook | **the null hypothesis**: 5M-token context stuffing instead of retrieval (build_with_llama_4) | keeps the composite honest: small pinned corpora don't need retrieval |
| adaptive-chunking / PDF2Audio | none | ✗ |
| **ragbot today** | hybrid BM25+vector, unweighted RRF (`rrf_round_robin.py`), multi-query fan-out, CRAG grade, rewrite_retry, neighbor_expand, mmr_dedup — 28 node files (`ls orchestration/nodes/` this session) | already a strong graph; missing the four tldw levers + pinned-context mode |

**Stage verdict**: **tldw_server wins retrieve.** Composite adds to ragbot, in effort order: alpha-weighted RRF (per-intent alpha), granularity param-bundles on the existing intent enum, late-chunk rescue as a gated fallback stage, per-doc **inclusion policy** (`pinned/retrievable/excluded` — open-notebook's Chat mode + llama-cookbook long-context, directly attacking ragbot's known "corpus HAS answer, retrieval missed" refuse class).

### 1.6 RERANK

| Repo | Approach | Verdict |
|---|---|---|
| tldw_server | **TwoTierReranker with sentinel calibration**: inject known-irrelevant sentinel doc into pool → CE shortlist → LLM rescore → logistic fusion of 3 score features → **gate**: generation blocked when `top_prob < threshold` OR `top_prob − sentinel_prob < margin` (`advanced_reranking.py:1476-1704`, sentinel at :1568-1580 — re-verified this session) | 🥇 uncontested |
| other five | none has a reranker (RAG-A delegates to LightRAG, ON has no rerank, adaptive-chunking offline, PDF2Audio/cookbook n/a) | ✗ |
| **ragbot today** | reranker Port + registry (ZE zerank / Jina / Null), cliff-detect strategy, 0.30 absolute threshold — recalibrated by hand at every model swap (memory `feedback_threshold_drift_post_migration`) | working, but threshold is model-coupled |

**Stage verdict**: the sentinel pattern is the single highest-leverage rerank import in the whole set: it converts an **absolute, model-coupled threshold into a per-query, per-model relative margin** ("is the best real chunk meaningfully better than known garbage on THIS model for THIS query"), eliminating the recalibration ritual ragbot has performed at every embedder/reranker migration. Fits as a decorator strategy on the existing reranker port; sentinel text must live in `language_packs` (domain/locale-neutral). HYPOTHESIS on lift size — needs A/B per rule #0.

### 1.7 GENERATE

| Repo | Approach | Verdict |
|---|---|---|
| open-notebook | **three independently configurable model roles per flow** (strategy/answer/final_answer); **token-threshold escalation to a long-context model** (>105k → large_context role, `provision.py:19-59` [refs-open-notebook §8]) | 🥇 model-role orchestration |
| PDF2Audio | **schema-constrained output + retry-on-ValidationError** (Pydantic `Dialogue` + tenacity, `app.py:509-515, 626-627` — read this session); human-in-the-loop regeneration via tagged edit blocks (`app.py:654-659`) | 🥇 structured-output discipline |
| llama-cookbook | **per-stage model tiering** (1B/70B/8B, NotebookLlama README:19-22); refine/map_reduce ladder for over-context inputs (video_summary cells 23-27) | tiering validation |
| RAG-Anything | VLM-enhanced generation (real retrieved-figure pixels to the VLM, `query.py:391-407`); enrichment-side **JSON parse ladder + think-tag strip** (`modalprocessors.py:577-718` [refs-rag-anything §3.5]) | multimodal + robust parsing |
| tldw_server | graceful stage degradation around generation; **answer-overwrite behaviors (`ask/decline`) = sacred-rule-#10 violation, explicitly NOT importable** (`unified_pipeline.py:5825-5830` [refs-tldw-server §2 row 2]) | budgets yes, overrides banned |
| **ragbot today** | LangGraph generate node, per-bot `system_prompt` = single source of truth, SysPromptAssembler append-only exception (ADR-W1-S10), no app-inject/override | the governance model is stronger than all six; imports are mechanics only |

**Stage verdict**: split win. Composite: keep ragbot's rule-#10 governance untouched; add (a) `long_context` binding purpose + token-threshold escalation (config-driven, not the hardcoded 105k), (b) schema-constrained JSON outputs with validation-retry for every *internal* structured LLM call (decomposer, grader, enrichment — not the user-facing answer), (c) the JSON parse ladder as a shared helper for enrichment calls.

### 1.8 GROUND / CITE

| Repo | Approach | Verdict |
|---|---|---|
| tldw_server | the verification arsenal: **hard citations** (per answer-sentence → supporting `(doc_id, start, end)` spans → coverage ratio, `guardrails.py:456-527`); **quote verification** byte-offset-exact; **numeric-fidelity check** with tolerance modes + targeted re-retrieval on missing numbers (`guardrails.py:117-423`, re-verified :237-250 this session); claims extraction + **FVA counter-evidence retrieval** (VERIFIED/REFUTED/CONTESTED/UNVERIFIED); **fast-groundedness gate that skips expensive verification at confidence ≥0.9** (cost ladder) [refs-tldw-server P2, P3, P6, P7] | 🥇 verification detectors |
| open-notebook | the provenance loop: **citation-ID allowlist injected into the retrieval prompt** ("if you are citing, it should be one of these", `ask.py:108-110` re-verified) + typed ID prefixes (`source:/note:/insight:`) + frontend regex parse to clickable chips + **context manifest** (IDs actually placed in the prompt returned on the API) [refs-open-notebook §6.1, §7.2] | 🥇 end-to-end provenance contract |
| adaptive-chunking | the substrate: per-chunk **page lists** (interval overlap) + **titles_context as metadata never mutating chunk text** (`postprocessing.py:8-64` [refs-adaptive-chunking §2.6]) | provenance metadata source |
| RAG-Anything | context-grounded enrichment (±N-page window into caption prompts) = anti-hallucination at ingest [refs-rag-anything §5] | ingest-side grounding |
| PDF2Audio / llama-cookbook | none | ✗ |
| **ragbot today** | chunk-level citations; HALLU=0 sacred enforced via sysprompt + load-test traps; Coverage metric; no server-side citation validation, no span-level cites, no page anchors, no numeric detector | gates strong, **detectors missing** |

**Stage verdict**: this is where the composite gains most. No repo has the full chain; ragbot can assemble it end-to-end because it owns every layer: **parser offsets (adaptive-chunking) → page/title metadata persisted (adaptive-chunking) → byte-offset citations `(chunk_id, start, end)` (tldw) → citation-ID allowlist in bot-owner template + server-side post-hoc validation metric "% citations not in retrieved set" (open-notebook) → numeric-fidelity + hard-citation coverage as observe-only per-turn metrics (tldw) → context manifest on the chat response (open-notebook)**. All detector-side, zero answer mutation — sacred rule #10 preserved. tldw's overwrite behaviors stay banned.

### 1.9 EVAL

| Repo | Approach | Verdict |
|---|---|---|
| adaptive-chunking | **5 intrinsic chunk metrics computed per (doc, method) on real outputs**, incremental parquet + resumability; paper-grade bake-off methodology (`compute_metrics.py:78-210` [refs-adaptive-chunking §2.7-2.8]) | 🥇 chunking eval |
| tldw_server | **embeddings A/B harness** (arms = provider:model, per-arm query embedding, hit@k/recall/MRR/nDCG vs ground-truth IDs); **CI gate split: deterministic metrics fail hard, LLM-judged metrics warn only** (`quality_gating.py:1-100` [refs-tldw-server P10]) | 🥇 retrieval eval + CI discipline |
| RAG-Anything | **failure-modes runbook** (6 named classes w/ concrete checks) + **modality probe questions** ("answerable only from an image/table") [refs-rag-anything §3.8] | 🥇 eval design for multimodal |
| open-notebook | none (no eval harness in repo [refs-open-notebook §10]) | ✗ |
| PDF2Audio / llama-cookbook | none | ✗ |
| **ragbot today** | 2,000+ unit tests, load-test harness, HALLU=0 + Coverage gates, RAGAS | strongest *gates*; missing per-stage instruments: chunk intrinsics on real outputs, embed-migration A/B arms, modality probes, hard/soft CI split |

**Stage verdict**: ragbot's end-to-end gates + the three references' *stage-local instruments*. The tldw stable/unstable CI split is the direct fix for LLM-judge flakiness in ragbot's gate wiring; the embed A/B harness makes the next embedder migration (Voyage pending, memory `project_5phase_shipped_20260512`) measured instead of ad-hoc.

---

## 2. The composite "best-of-all" architecture

Skeleton rule first (binding, per ACTIVE PROGRAM stance): **ragbot's hexagonal Port+Registry+DI frame, 4-key identity, RLS, canonical funnel, and rule-#10 governance stay — every import below lands as an adapter, node, config schema, or metric inside that frame.** None of the six references has a multi-tenant story (FACT: RAG-Anything 0 "tenant" hits; open-notebook single-user; tldw per-user files; others n/a) — so *no wiring pattern* is importable, only stage mechanics, and each must be re-scoped to `record_tenant_id`/`record_bot_id` on entry.

```
                       ┌─ INGEST ─────────────────────────────────────────────┐
 file/URL/bytes ──►    │ canonical /documents/create (ragbot, keep)           │
                       │ + FileValidator tier: blocked-ext, MIME-wins,        │
                       │   archive scan                     [tldw P8]         │
                       │ + parse-cache (content-hash × parser-config)         │
                       │   + content-based doc_id           [RAG-A]           │
                       │ + per-stage status axes + stage-labeled errors       │
                       │   (parse|chunk|embed|enrich)       [RAG-A]           │
                       │ + /documents/{id}/retry from persisted asset [O-N]   │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ PARSE ──────▼───────────────────────────────────────┐
                       │ mime→ext→byte-sniff (ragbot, keep — best of set)     │
                       │ typed Block contract = modality [RAG-A]              │
                       │   × char-offset split_points/titles [adaptive-chunk] │
                       │   × ocr_confidence per block        [tldw P9]        │
                       │ per-block sentinel degradation + zero-block          │
                       │   fail-loud floor                   [RAG-A]          │
                       │ embedded figures → context-grounded VLM caption      │
                       │   (per-bot opt-in)                  [RAG-A]          │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ CHUNK ──────▼───────────────────────────────────────┐
                       │ ragbot strategy library (keep: tables/VN exceed all) │
                       │ + block-API wiring (Layer 6) + gap REPAIR + assert   │
                       │   per strategy                [adaptive-chunk]       │
                       │ + evaluate-then-select via offline bake-off →        │
                       │   per-doc chunking_policy     [adaptive-chunk]       │
                       │ + DB chunking-templates + classifier block as the    │
                       │   per-bot config schema       [tldw P4]              │
                       │ + token (not char) budgets    [adaptive-chunk, O-N]  │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ INDEX ──────▼───────────────────────────────────────┐
                       │ pgvector+HNSW+BM25+RLS (ragbot, keep — best of set)  │
                       │ + page/title metadata persisted  [adaptive-chunk]    │
                       │ + doc-level insight rows (RAPTOR-lite)  [O-N]        │
                       │ + dual-representation table/figure chunks [RAG-A]    │
                       │ + vector_dims guard during re-embeds     [O-N]       │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ RETRIEVE ───▼───────────────────────────────────────┐
                       │ ragbot 28-node graph (keep)                          │
                       │ + alpha-weighted RRF, per-intent alpha   [tldw P13]  │
                       │ + granularity→param-bundle routing       [tldw P12]  │
                       │ + late-chunk rescue fallback             [tldw P5]   │
                       │ + per-doc inclusion policy pinned/retrievable/       │
                       │   excluded (+ token-cost preview)  [O-N + cookbook]  │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ RERANK ─────▼───────────────────────────────────────┐
                       │ ragbot reranker port (keep)                          │
                       │ + sentinel-calibrated margin gate decorator          │
                       │   (sentinel text from language_packs)    [tldw P1]   │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ GENERATE ───▼───────────────────────────────────────┐
                       │ rule-#10 governance (ragbot, keep — strictest known) │
                       │ + long_context binding purpose + token-threshold     │
                       │   escalation (config-driven)             [O-N]       │
                       │ + schema-constrained internal LLM calls w/           │
                       │   validation-retry     [PDF2Audio] + JSON ladder     │
                       │   for enrichment       [RAG-A]                       │
                       │ + per-purpose model tiering (already ragbot policy;  │
                       │   externally validated by NotebookLlama)             │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ GROUND/CITE ▼───────────────────────────────────────┐
                       │ (chunk_id, start, end) span citations    [tldw P3]   │
                       │ + page anchors from parse offsets [adaptive-chunk]   │
                       │ + citation-ID allowlist (bot-owner template) +       │
                       │   server-side validation metric          [O-N]       │
                       │ + numeric-fidelity observe-only (VN-locale           │
                       │   normalizer)                            [tldw P2]   │
                       │ + fast-groundedness cost ladder          [tldw P6]   │
                       │ + context manifest on chat response      [O-N]       │
                       │ NO answer overwrite ever (tldw behaviors banned)     │
                       └──────────────┬───────────────────────────────────────┘
                       ┌─ EVAL ───────▼───────────────────────────────────────┐
                       │ ragbot load-test + HALLU=0 + Coverage gates (keep)   │
                       │ + chunk intrinsics on real outputs [adaptive-chunk]  │
                       │ + embeddings A/B arms harness            [tldw P10]  │
                       │ + modality probe questions               [RAG-A]     │
                       │ + CI split: deterministic hard-fail /                │
                       │   LLM-judged warn                        [tldw P10b] │
                       └──────────────────────────────────────────────────────┘
```

### 2.1 The one load-bearing insight

Across all six repos, the single pattern that compounds through every stage is a **richer per-block data contract emitted at parse time and never dropped**: modality type + char span + page + confidence. Every downstream capability that ragbot currently lacks is blocked on exactly this contract, not on algorithms:

- true Block-Integrity metric + offset-based title attach ← needs char spans [refs-adaptive-chunking M2]
- page-anchored + span-level citations ← needs page + spans [refs-adaptive-chunking M4; refs-tldw-server P3]
- figure/equation retrieval ← needs modality types [refs-rag-anything §9]
- OCR-quality gating at retrieval ← needs confidence [refs-tldw-server P9]
- evaluate-then-select chunking ← needs gold boundaries to score against [refs-adaptive-chunking M1]

Ragbot already owns both halves in-tree — the `Block` entity (`domain/entities/document.py:41-51`) and the structured-markdown discipline — but flattens to a string before chunking (`ingest_stages.py:770`, worker flatten `document_worker.py:463-466`). **The composite architecture is, for ragbot, mostly a wiring project, not a rewrite** — consistent with the program's EVOLVE stance and with the four deep reports' independent conclusions.

### 2.2 Do-NOT-import register (consolidated from all six)

| Anti-pattern | Repo(s) | Violated ragbot rule |
|---|---|---|
| Answer overwrite on failed checks (`decline`/`ask` replaces/append English text) | tldw `unified_pipeline.py:5825-5830` | sacred #10 app-override |
| Extension-only detection, unknown→"try as PDF" | RAG-Anything `parser.py:1415-1421` | type-detection mandate |
| Everything-to-PDF normalization (destroys structure, then 758-LOC OMML recovery to undo it) | RAG-Anything [refs-rag-anything §2.3] | structured-markdown contract |
| Fabricated metadata (`page_idx = cnt // 10`) | RAG-Anything `parser.py:1856+` | HALLU=0 (fabricated provenance) |
| LLM rewrite of corpus text pre-chunk | llama-cookbook NotebookLlama Step-1 | lossless coverage (HYPOTHESIS on damage, ban regardless) |
| 229-param mega-function pipeline | tldw `unified_pipeline.py:1160` | config-chain / testability |
| Process-global prompt language, env-only config, config clobbering in nodes | RAG-Anything prompt_manager; open-notebook `source.py:35-51` | multi-tenant + zero-hardcode |
| Hardcoded English heuristics (granularity regexes, stopwords, templates) | tldw, open-notebook, PDF2Audio (`INSTRUCTION_TEMPLATES` in source) | domain-neutral / multilingual-no-vocab — all vocab → language_packs |
| Client-supplied prompt context | open-notebook `chat.py:64-69` | headless B2B trust boundary |
| Auto-tuning semantic-cache threshold by hit-rate | tldw `semantic_cache.py:530-611` | HALLU-adjacent (wrong-answer-from-cache) |
| Brute-force cosine, no index; table-per-collection vector store | open-notebook; tldw pgvector adapter | scale + RLS |
| Broad `except Exception` as default idiom | RAG-Anything, open-notebook throughout | broad-except policy |

---

## 3. Ranked adoption sequence (tier-tagged; all lift figures = HYPOTHESIS until measured per rule #0)

Ordered by (T1 impact × prerequisite structure), consolidating the four reports' lists into one sequence with duplicates merged:

1. **[T1] Block contract v2** — extend parser output with `block_type`, `char_span`, `page`, `confidence`; emit split_points/titles from the OCR block walk; wire Layer-6 block chunking. *(prereq for #2, #5, #7; sources: adaptive-chunking M2/H2, RAG-A §10.1, tldw P9)*
2. **[T1] Ground/cite chain** — persist page metadata; span citations; citation-allowlist validation metric; context manifest. *(adaptive-chunking M4, tldw P3, O-N P1/P13)*
3. **[T1] Numeric-fidelity observe-only node** with VN-locale normalizer (`1.499.000đ`, `1tr499`) + load-test gate `missing==0` on factoid turns. *(tldw P2 — smallest change, direct HALLU instrumentation)*
4. **[T1] Sentinel-calibrated rerank margin gate** as reranker-port decorator; sentinel text in language_packs. *(tldw P1 — kills threshold-drift class)*
5. **[T1] Coverage gap REPAIR + assert-per-strategy** (uncovered spans → prepend to next chunk). *(adaptive-chunking M3/M9 — ~15-line change, closes silent corpus loss)*
6. **[T1] Per-doc inclusion policy** pinned/retrievable/excluded + token-cost preview. *(O-N P2 + cookbook long-context — attacks the refuse-when-corpus-has-answer class)*
7. **[T1] Multimodal enrichment** — embedded-figure VLM captions (context-grounded) + table-description chunks, per-bot opt-in. *(RAG-A §10.2-3)*
8. **[T2] Ingest robustness pack** — parse cache + content doc_id, per-stage status axes, sentinel blocks + zero-block fail-loud, retry endpoint, JSON ladder, upload hardening. *(RAG-A §10.5-9, O-N P5/P6, tldw P8)*
9. **[T1/T2] Retrieve levers** — alpha-weighted RRF per intent; granularity bundles; late-chunk rescue behind the sentinel margin. *(tldw P13/P12/P5)*
10. **[test-health] Eval instruments** — embed A/B arms; chunk intrinsics on real outputs (bake-off as selector feedback); modality probes; deterministic/LLM-judged CI split. *(tldw P10, adaptive-chunking M1, RAG-A §10.4)*
11. **[T2] Ops polish** — dimension guard SQL; blocklist retry semantics; long_context escalation purpose; why_these_sources metadata; budget guards on multi-LLM features. *(O-N P7/P9/P10, tldw P11 + budgets)*
12. **[T3] Governance surfaces** — chunking templates in DB + classifier; registry registration guards; `content_blocks` direct-injection API variant. *(tldw P4, RAG-A §10.11-12)*

---

## 4. FACT / HYPOTHESIS register

**FACT basis**: (a) all four prior deep reports read in full; 8 of their load-bearing `file:line` claims re-verified verbatim against vendored source this session (§0.2) — sampling, not exhaustive re-verification; (b) PDF2Audio `app.py` and llama-cookbook notebooks read directly this session with line/cell evidence; (c) ragbot-side anchors (`registry.py:123-132`, `ingest_stages.py:770`, `ingest_stages.py:889-895`, orchestration node listing) re-verified this session on branch `fix-260623-ingest-expert`.

**HYPOTHESES carried (labeled inline)**: every adoption-lift claim (§3) — no A/B was run; NotebookLlama LLM-normalizer corpus damage (§1.2 warning); sentinel-gate lift size (§1.6); unverified-at-runtime items inherited from the prior reports (their §-registers apply: docling adapter bug, tenant-sentinel risk, etc.).

**Known limits**: llama-cookbook conclusions cover only the 26-file vendored subset; tldw was surveyed via its deep report + 3 source spot-checks, not a fresh full read; no runtime execution, no DB queries — static synthesis as mandated.

— end of report —
