# Deepdive: `_external_refs/adaptive-chunking` vs ragbot AdapChunk — full architecture map + adoption audit

- **Slug**: refs-adaptive-chunking
- **Date**: 2026-07-02
- **Reference repo**: `github.com/ekimetrics/adaptive-chunking` @ `81a9f47` ("Adaptive Chunking: Optimizing Chunking-Method Selection for RAG", LREC 2026, arXiv 2603.25333) — vendored at `/var/www/html/ragbot/_external_refs/adaptive-chunking` (~9,055 LOC Python: `wc -l` over 24 .py files)
- **Ragbot side audited**: `src/ragbot/shared/chunking/*` (5,397 LOC incl. metrics/tabular), `src/ragbot/shared/intrinsic_metrics.py`, `src/ragbot/shared/chunking/coverage.py`, `src/ragbot/infrastructure/{chunk_quality,chunking_strategy,ocr,parser}/`, `src/ragbot/application/services/document_service/ingest_stages.py`, `src/ragbot/interfaces/workers/document_worker.py`
- **Method**: read-only source audit, both sides read in full for the chunking-relevant modules. Every claim carries `file:line`. Labels: **FACT** (verified in code/report artifacts) vs **HYPOTHESIS** (inference, not runtime-verified).

---

## 1. Executive summary

The reference repo's core idea is **evaluate-then-select**: chunk each document with N candidate methods, score each method's *actual output* with 5 intrinsic metrics, and pick the argmax weighted score per document (`paper/analysis.py:294-327 find_best_method`, weights = 0.2 each, `paper/replicate.py:92`). Ragbot adopted the *vocabulary* of this design (5 metric names, "AdapChunk" layer naming, a bake-off harness, a coverage gate, atomic blocks, header-preserving table splits) but **not the core loop**: production strategy selection happens *before* chunking, from a document profile, via hand-tuned weighted rules (`shared/chunking/analyze.py:407-541`) + a Layer-5 rule cross-check (`analyze.py:576-713`). The flag-gated "Ekimetrics selector" (`shared/intrinsic_metrics.py`) computes its metrics on the **raw text with simulated equal-split chunks** (`intrinsic_metrics.py:291-296`), so its scores are constant across strategies — it cannot do what the paper does even when the flag is turned on. Ragbot's own offline bake-off (`reports/bakeoff_chunking_20260620.md`) measured the consequence: **adaptive pick == oracle best in 0/8 documents, +0.001 composite lift over plain recursive, 0.103 headroom** (FACT — project's own artifact).

Beyond the selector, three reference invariants are half-adopted: (a) **lossless coverage** exists (`shared/chunking/coverage.py`) and is wired (`ingest_stages.py:889-905`) but is observe-only — the reference *repairs* gaps and hard-asserts (`postprocessing.py:128-151`, `pipeline.py:112-118`); (b) the **Block pipeline (Layer 6)** `smart_chunk_atomic(list[Block]) -> list[Chunk]` is fully implemented (`shared/chunking/__init__.py:653-810`) but has **zero production callers** — ingest still flattens text and calls the string API (`ingest_stages.py:770`); (c) two Strategy/DI registries related to this design are explicitly disabled/dead in-tree (`infrastructure/chunk_quality/registry.py:1-23`, `infrastructure/chunking_strategy/registry.py:1-10`). The most valuable **missed** patterns are: parser-emitted gold `split_points`/`titles` char offsets (the data contract that makes a real Block-Integrity metric and title/page metadata possible), gap *repair*, per-chunk page/title metadata columns, and token-based (vs char-based) size accounting.

Where ragbot **exceeds** the reference: multi-row stacked header merge for tables (`shared/tabular_markdown.py:105-138` — reference `ExcelParser._clean_headers` handles only single-row headers, `parsing.py:506-525`), VN/multilingual structural handling, a much larger test surface (41 chunk-related unit-test files vs 4 in the reference), and multi-tenant chunk identity.

---

## 2. Reference repo — complete architecture map

### 2.1 Package layout (all FACT, from source)

```
src/adaptive_chunking/
├── splitters.py        (618)  RecursiveSplitter + group_chunks/group_pages/combine_blocks/regex_splitter
├── parsing.py         (1111)  BaseParser ABC + AzureDIParser / DoclingParser / PyMuPDFParser / ExcelParser
├── postprocessing.py   (512)  gap check/repair, page+title metadata, oversize split, small-merge
├── metrics.py          (934)  SC / ICC / DCC / BI / RC(+coref) + semantic/lexical dissimilarity extras
├── compute_metrics.py  (238)  per-doc metric orchestration, incremental parquet save + resumability
├── split_documents.py  (241)  run all splitters per doc, assert gap-free after EVERY method
├── pipeline.py         (145)  chunk_files() = parse → split → repair → assert → attach page/title meta
├── extract_mentions.py  (80)  coreference mention extraction entry point
├── chunking_utils.py    (25)  count_tokens (tiktoken o200k / gpt-4o)
├── jina_embedder.py    (137)  Jina REST drop-in for SentenceTransformer
└── paper/
    ├── splitters.py    (577)  SemanticChunkerWrapper / SentenceSplitter / LongContextSemanticSplitter / LLMRegexSplitter
    ├── replicate.py    (889)  8-method CLI, SEPARATORS, WEIGHTS, few-shot LLM-regex prompt
    ├── analysis.py     (883)  output_best_chunks + find_best_method (the SELECTOR)
    ├── rag_eval.py/rag_utils.py  Haystack hybrid-retrieval RAG eval
    └── visualization.py       HTML split overlays
```

### 2.2 The parsed-document data contract (the keystone)

Every parser must emit (`parsing.py:12-23`, `LLM.md:79`):

```json
{"document_name": str,
 "pages": {page_num: markdown},
 "full_text": str,
 "split_points": [int],          // char offsets of GOLD block boundaries
 "titles": [{"title", "start", "end", "level"}]}   // heading spans w/ char offsets
```

`split_points` are **parser-derived gold boundaries** ("never legitimate to cut inside") emitted while walking the layout tree, with suppression rules that encode chunking wisdom at *parse* time:

- no split after `TITLE`/`SECTION_HEADING` (title glues to following text) — `parsing.py:433-435`
- no split between two consecutive short (<100-token) TEXT blocks — `parsing.py:436-438`
- no split before a `FOOTNOTE` (footnote glues to parent text) — `parsing.py:439-440`
- a split after each table sub-chunk — `parsing.py:392-397`

`titles` spans get their `end` computed as the start of the next same-or-higher-level heading (`parsing.py:463-471`) — i.e. a *section interval tree* in char space.

### 2.3 Block model

Blocks are typed dicts produced by a DFS over the layout tree (`AzureDIParser._walk_section`, `parsing.py:127-178`): `TEXT` (with role: TITLE / SECTION_HEADING / FOOTNOTE / PAGE_HEADER / PAGE_FOOTER / PAGE_NUMBER), `TABLE`, `FIGURE`, `FORMULA_BLOCK`, `PAGE_BREAK`; each carries `page_number` + `depth`. Rendering to markdown wraps non-text semantics in explicit tags — `<Table>…</Table>` (`parsing.py:389`), `<Figure>…</Figure>` (`parsing.py:401`), `<Formula>…</Formula>` (`parsing.py:403`), `<!-- PageBreak -->` (`parsing.py:419`) — so structure survives the flat-markdown hop *and* is machine-recoverable. Page headers/footers get merged runs and comment-tagged so they never pollute chunks (`parsing.py:351-382`).

### 2.4 Table handling

Three mechanisms, all token-budgeted:

1. **AzureDIParser** — DataFrame → HTML → markdownify; if the whole table exceeds `max_tokens_per_block` (default 1000), rebuild sub-DataFrames row-group by row-group, each with full columns, each `<= max_tokens` (`parsing.py:70-101`). Caption appended to *every* sub-table (`parsing.py:154-157`).
2. **DoclingParser `_split_table_markdown`** — keep markdown lines 0-1 (header + separator) and re-prepend them to every row-group split (`parsing.py:926-953`).
3. **ExcelParser** — per sheet: split on blank rows into blocks, **promote first block row to header** with `_clean_headers` (blank/dupe → `col_N`, `parsing.py:506-525`), oversized blocks split by rows with header + `"{sheet} - part n"` synthetic heading per part; each part registered in `titles` + `split_points` (`parsing.py:567-662`).

Note: header promotion is **single-row only** — a 2-row stacked header would be mangled (FACT from `parsing.py:594-599`; ragbot handles this case, §4.1).

### 2.5 RecursiveSplitter (`splitters.py:7-392`)

- **Separator ladder as regexes**: markdown headings `#{1}` → `#{6}` (with `(?<=\n)` lookbehind), numbered/bulleted list markers, `\n{2,}`, `\n`, sentence punct, comma, whitespace, `""` (`paper/replicate.py:56-71`).
- `attach_separator_to: start|end` — separator survives with the chunk (`splitters.py:65-126`).
- **Empty-separator = binary search on the token function** to find the largest prefix ≤ budget (`splitters.py:142-181`) — precise token-boundary hard split, no char-count guessing.
- Two merge modes: `to_chunk_size` (packs splits up to budget, overlap built by **backtracking over constituent parts**, with `min_overlap = 0.5*overlap` and recursive re-split of an oversized tail part, `splitters.py:207-278`) and `small_only` (keep semantic boundaries, only merge chunks `< min_chunk_tokens` into neighbours, `splitters.py:280-374`).
- `merging_order: forward|backward`, `max_tokens_strategy: chunk_size | chunk_size_plus_overlap`.
- `group_chunks()` — pack **pre-chunked blocks** into chunks without ever cutting a block, block-count overlap, oversized block cropped via binary search (`splitters.py:395-497`).

### 2.6 Postprocessing invariants (`postprocessing.py`)

- `find_chunks_start_and_end` — locate every chunk in the source with backward-then-forward search (overlap-tolerant) (`postprocessing.py:100-126`).
- `check_chunk_gaps` — True iff chunks cover **every char** of the source, overlap allowed (`postprocessing.py:66-98`).
- `repair_gaps_between_chunks` — **prepend each dropped gap onto the next chunk** and append any tail to the last chunk (`postprocessing.py:128-151`).
- `split_oversized_chunks` + `merge_small_chunks_to_neighbours` / `merge_small_chunks_smallest_first` — normalize size distribution post-hoc, then repair + re-assert (`postprocessing.py:153-291`, driven by `paper/replicate.py:272-318`).
- **Enforcement discipline**: after *every* splitter run and every post-process, `assert check_chunk_gaps(...) == True` (`split_documents.py:95-96, 112-113, 133-134, 154-155`; `compute-time`, `postprocessing.py:339-340, 452-453`); the packaged pipeline raises `RuntimeError("Chunk gap recovery failed … This is a bug")` (`pipeline.py:114-118`).
- `get_page_info` — per chunk, the list of page numbers it overlaps (interval intersection in char space, `postprocessing.py:8-40`).
- `get_title_info` — per chunk, the **enclosing headings whose text is NOT already inside the chunk**, returned as a separate `titles_context` string (`postprocessing.py:42-64`) — heading breadcrumbs as *metadata*, never mutating chunk text.

### 2.7 The five intrinsic metrics (`metrics.py`)

| Metric | Reference implementation | Needs |
|---|---|---|
| **SC** size compliance | fraction of chunks with `min_tokens ≤ len ≤ max_tokens` (100/1100 tokens) (`metrics.py:16-34`) | token counter |
| **ICC** intrachunk cohesion | mean cosine(chunk-sentence embedding, whole-chunk embedding); sentences reconstructed from **parser split_points** clipped to the chunk (`metrics.py:53-148`) | embedder (jina-v3) + split_points |
| **DCC** contextual coherence | cosine(chunk embedding, sliding ~3000-token *window* embedding); windows built to never duplicate overlapped text (`metrics.py:150-262`) | embedder |
| **BI** block integrity | fraction of **gold blocks** (from parser split_points) with **no predicted chunk-start strictly inside** them, ±5 chars tolerance (`metrics.py:264-307`) | split_points + located chunk starts |
| **RC** references completeness | 1 − filtered missing-reference error: coreference **entity→pronoun pairs** (maverick-coref + spaCy) severed by any chunk boundary (`metrics.py:485-583`, `extract_entity_pronoun_pairs:585-671`) | coref model (GPU / non-commercial licence) |

All are computed **per (document, chunking_method) on the method's real output** (`compute_metrics.py:78-210`) with incremental parquet saves + resumability.

### 2.8 The selector — the actual "Adaptive Chunking"

`output_best_chunks` (`paper/analysis.py:167-292`): pivot metrics per doc → `find_best_method` = **NaN-skipping weighted mean over the 5 metrics per method, `idxmax` over methods** (`analysis.py:294-327`), fallback `default_method="page"` when metrics missing. Default weights: uniform 0.2 (`paper/replicate.py:92`). There is **no rule-based/threshold selector anywhere in the reference code** (FACT — grepped; only the semantic chunker's internal breakpoint thresholds exist, `paper/splitters.py:362-386`). Result (paper Table 5): Retrieval Completeness 67.7 vs 58-59, Answer Correctness 78.0 vs 70-73 (`README.md:33-49`).

### 2.9 LLM-regex splitter (8th method)

`LLMRegexSplitter` (`paper/splitters.py:532-554`): send the first 8k tokens of the doc + few-shot example, ask for **one document-specific regex delimiter** in `<regex>…</regex>`; validate/compile with a hyphen-escape repair pass (`extract_llm_regex`, `paper/splitters.py:557-577`); fall back to `[text]` when invalid. Prompt guidelines encode the atomic-block rules ("Do not split tables … figures … lists … titles from the text that follows them … footnotes", `paper/replicate.py:112-121`).

### 2.10 Tests

4 files, 288 lines: splitter coverage/size/overlap/merging/regex-separator invariants (incl. **"joined chunks == original text"**, `tests/test_splitters.py:33-41`), gap check/repair (`tests/test_postprocessing.py`), metric sanity, parsing. Small but they pin exactly the lossless invariants.

---

## 3. Ragbot AdapChunk — what actually exists (map)

Production ingest chunking path (U4): `DocumentService.ingest` (`ingest_core.py:177`) → `_stage_u4_chunk` (`ingest_stages.py`) →

1. **Profile** `analyze_document(text)` — rule-based counts: headings, tables (`_is_table_line`), avg block words, mixed score, TOC (literal + dotted-leader), CSV shape, VN legal markers, formula/image/code counts (`shared/chunking/analyze.py:215-315`).
2. **Select** `select_strategy(profile)` — fast-paths (CSV+no-headings → `table_csv`/`table_dual_index`; ≥N VN/heading markers → `hdt`), else weighted rule scores over {hdt, semantic, recursive, hybrid, proposition} with `DEFAULT_STRATEGY_WEIGHTS`, low-confidence → recursive (`analyze.py:407-541`).
3. **Layer-5 cross-check** `apply_cross_check` — 5 priority-ordered override rules (low-conf→hybrid; hdt-but-few-headings→semantic; semantic-but-short-blocks→proposition; proposition-but-long-structured→hdt; mixed-content warn-only), flag `adapchunk_layer5_cross_check_enabled` **default ON** (`constants/_12_multi_stage_retrieval_fallba.py:149`; logic `analyze.py:576-713`).
4. **Chunk** `smart_chunk(text, …, strategy=…)` (`ingest_stages.py:770`) — dispatch to `_chunk_hdt` (heading-path `[H1 > H2]` prefix, `strategies.py:277-357`), `_chunk_semantic`, `_chunk_hybrid`, `_chunk_proposition`, `_chunk_recursive_with_tables` (H1 pre-split + table blocks atomic; oversized tables split by row-groups **with header re-prepended**, `strategies.py:64-179`), `_chunk_table_csv_with_context` / `_chunk_table_dual_index` (`csv_chunker.py`). Non-HDT chunks get their nearest preceding `##` heading re-prepended (`_prefix_section_headings`, `chunking/__init__.py:375-409`).
5. **Post-process** `merge_orphan_chunks` (skip for row-atomic strategies) (`ingest_stages.py:794-805`).
6. **Observe-only gates**: dropped-number gate (`ingest_stages.py:868-879`) + char-coverage gate `check_chunk_gaps` (`ingest_stages.py:889-905`, impl `shared/chunking/coverage.py:141-255`).

Parallel, non-wired surfaces: `smart_chunk_atomic` Layer 6 (`chunking/__init__.py:653-810`), `analyze_document_blocks` (`analyze.py:318-404`), `attach_context_buffer` Layer 2 (`shared/context_buffer.py`), Ekimetrics selector (`shared/intrinsic_metrics.py`), chunk-quality scorer registry (`infrastructure/chunk_quality/`), LLM/rule strategy-resolver registry (`infrastructure/chunking_strategy/`).

Parsers: registry parsers (`infrastructure/parser/*`) return flat `[{"content", "metadata"}]` (`application/ports/document_parser_port.py:29-40`); OCR engines (`infrastructure/ocr/*`, default `kreuzberg`, `constants/_13_adapchunk_ocr_parser.py:11`) return typed `Block` lists with `is_atomic`, `context_before` (active heading), `page_number` (`kreuzberg_parser.py:279-315`; entity `domain/entities/document.py:41-51`).

---

## 4. Adoption audit

### 4.1 ADOPTED (working analogs in the production path)

| # | Reference pattern | Ragbot analog | Evidence | Verdict |
|---|---|---|---|---|
| A1 | `check_chunk_gaps` lossless char coverage (`postprocessing.py:66-98`) | `coverage.check_chunk_gaps` — whitespace-normalized interval union, uncovered spans mapped to original offsets | `shared/chunking/coverage.py:141-255`; wired `ingest_stages.py:889-905`; test `tests/unit/test_coverage_gate_wired.py` | FACT — adopted, *observe-only* (see H6) |
| A2 | Header-preserving row-group split of oversized tables (`parsing.py:926-953`, `parsing.py:70-101`) | `_chunk_recursive_with_tables` — header lines re-prepended to every row group (>3× chunk_size trigger); also csv row chunking with doc header (`csv_chunker._doc_table_header:217`) | `strategies.py:140-166` | FACT — adopted (char-budget not token-budget) |
| A3 | Small-chunk merge to neighbours (`postprocessing.py:235-291`) | `merge_orphan_chunks` (forward pending→next, max_size cap, trailing fold-back) | `chunking/__init__.py:597-636`; wired `ingest_stages.py:800-805` | FACT — adopted, simpler (no loop-until-stable, no smallest-first variant) |
| A4 | `titles_context` heading breadcrumb per chunk (`postprocessing.py:42-64`) | HDT `[H1 > H2]` inline prefix (`strategies.py:277-357`); `_prefix_section_headings` for non-HDT (`chunking/__init__.py:375-409`); kreuzberg `Block.context_before` = active heading (`kreuzberg_parser.py:298-309`); optional `with_metadata=True → parent_headings` (`chunking/__init__.py:562-588`) | FACT — adopted, but **injected into chunk text** rather than a separate metadata field (see M5 for consequences) |
| A5 | Typed block model with atomic semantics (`parsing.py` block dicts, `<Table>/<Figure>/<Formula>` tags) | `Block` frozen dataclass (type, is_atomic, context_before/after, page_number, ocr_metadata) + `_split_into_blocks_with_atomic` (text/table/formula/image/code) + table-footer merge | `domain/entities/document.py:41-51`; `shared/chunking/blocks.py:184-276`, `blocks.py:21-64` | FACT — adopted and extended (footer-merge M18 has no reference analog) |
| A6 | Multi-method offline bake-off (paper Table 3 replication loop) | `scripts/bakeoff_chunking_strategies.py` — re-chunk every live doc with 5 strategies, score with the 5-metric lexical suite, compare adaptive pick vs oracle | `scripts/bakeoff_chunking_strategies.py:1-30,147-170`; output `reports/bakeoff_chunking_20260620.md` | FACT — adopted **offline only** |
| A7 | ExcelParser blank/dupe header cleanup (`parsing.py:506-525`) | `tabular_markdown._normalize_rows` + **stacked multi-row header merge** `_is_header_continuation`/`_merge_header_fill` (shape-only, fills empty positions, rejects overlap) | `shared/tabular_markdown.py:105-138` | FACT — ragbot **exceeds** the reference (reference is single-row-header only) |
| A8 | Docling as a parse backend (`parsing.py:712-924`) | `infrastructure/ocr/docling_parser.py` (opt-in engine) | `ocr_factory.py:80-84` | FACT adopted; see H8 for a likely porting bug |

### 4.2 HALF-ADOPTED (built-but-not-wired / flag-off / semantically hollow)

**H1 — The evaluate-then-select loop is replaced by pre-chunk rules; the flagged "Ekimetrics selector" cannot discriminate strategies.**
- Reference: metrics computed **per method on real chunk output**, weighted argmax (`compute_metrics.py:78-210`, `analysis.py:294-327`).
- Ragbot: `select_strategy(…, ekimetrics_enabled=…)` calls `compute_intrinsic_metrics(text)` with **no blocks and no chunks** → blocks default to paragraphs, chunks default to a **simulated equal char-split** (`intrinsic_metrics.py:286-296`; call site `analyze.py:473-482`). The metric vector is therefore a function of the *document only* — identical for every candidate strategy — and the selection is a fixed threshold ladder (`BI<0.6→semantic; RC>0.8→proposition; DCC<0.5→semantic; SC<0.7→recursive; else hybrid`, `intrinsic_metrics.py:344-357`). FACT.
- The docstring claims this follows "per LREC 2026 paper, section 'Rule-Based Selector'" (`intrinsic_metrics.py:319`). The reference implementation contains **no rule-based selector** — selection is weighted-mean argmax (`analysis.py:294-327`); grep for rule/threshold selection logic comes back empty. FACT for the code; HYPOTHESIS: the paper PDF also has no such section (not verifiable locally — the vendored repo carries no paper text beyond README).
- Flag default OFF: `ekimetrics_5metric_selector_enabled` resolved with `False` fallback (`ingest_stages.py:574-580`).
- **Measured consequence** (project's own artifact, FACT): `reports/bakeoff_chunking_20260620.md` — "Adaptive == oracle_best: **0/8** (0%)", "Adaptive lift over recursive baseline: **+0.001**", "Selector headroom (oracle − adaptive): **0.103**". Caveat: the bake-off scores only the 5 prose strategies, so rows where production picked `table_csv` are compared against a prose oracle (the table_csv output itself is unscored) — the 0/8 headline slightly overstates, but the +0.001-over-recursive aggregate is strategy-set-consistent.

**H2 — Layer 6 `smart_chunk_atomic` (list[Block] → list[Chunk]): implemented, tested, zero production callers.**
- Implementation: `shared/chunking/__init__.py:653-810` (+ helpers `:813-931`) — emits atomic blocks as standalone `Chunk`s with `context_before/after`, runs legacy `smart_chunk` on TEXT runs.
- Callers: only `tests/unit/test_smart_chunk_atomic.py` and the module-split pin test (grep across `src/` = definition only). Ingest calls the *string* API: `raw_chunks = smart_chunk(content, …)` (`ingest_stages.py:770`). FACT.
- Even with `adapchunk_block_pipeline_enabled` **default ON** (`constants/_12_multi_stage_retrieval_fallba.py:185`), the "Block pipeline" branch only affects Layers 2-5 (profile + selection, `ingest_stages.py:582-647`); chunking itself still flattens to text. The in-code comment admits it: "Until merged we still call the existing text-API" (`ingest_stages.py:544-559`).
- Latent multi-tenant risk if ever wired carelessly: when identity params are not plumbed, it fabricates **sentinel `uuid4()` tenant/bot/doc IDs** on every chunk (`chunking/__init__.py:719-721`). FACT that the code does this; HYPOTHESIS: a future wiring that forgets `record_tenant_id` would persist chunks with random tenant UUIDs, breaking RLS scoping silently.

**H3 — Block stream from parsers: exists only on the OCR-fallback path, and its Layer-2 output is discarded.**
- Only OCR engines construct `Block` objects (`kreuzberg_parser.py:304-315`, `docling_parser.py:125`, `simple_text_parser.py:144-222`); **no registry parser does** (grep `Block(` under `infrastructure/parser/` = 0 hits). FACT.
- In the worker, registry-parsed docs are immediately flattened: `full_text = "\n\n".join(c["content"] …)` (`document_worker.py:463-466`); even OCR blocks are flattened for `content` (`document_worker.py:501`) with the block list passed alongside (`:500`, `:624`).
- At ingest, `parsed_blocks` feeds `attach_context_buffer` (Layer 2) and `analyze_document_blocks` (Layer 3) (`ingest_stages.py:597-613`) — but since chunking is text-based (H2), the computed `context_before/context_after` **never reach a persisted chunk**. Layer 2 runs and its output is dropped. FACT (code path); consequence HYPOTHESIS (no runtime trace of a doc through this path was captured in this audit).

**H4 — Chunk-quality scorer registry: explicitly DEAD.**
- `infrastructure/chunk_quality/registry.py:1-23` carries a "DEAD-CODE NOTICE — 2026-06-03 … NOT reachable from any production entry point … never wired in bootstrap or graph"; entire body commented out. Port still exported (`application/ports/chunk_quality_port.py`). A *different*, wired quality path exists (`ingest_stages_enrich.py:579-625` using `shared/chunk_quality`-style `score_chunk_quality`). FACT. This is the "revive or remove" item the reference's per-output scoring would have justified.

**H5 — LLM/rule chunking-strategy resolver registry: explicitly DISABLED.**
- `infrastructure/chunking_strategy/registry.py:1-10`: "DISABLED — UNUSED … ZERO runtime callers; strategy routing is done by the deterministic profile router". Tests exist (`tests/unit/test_llm_chunking_strategy_resolver.py`). FACT.
- Contrast: the reference's LLM contribution is an **LLM-generated document-specific regex delimiter** (`paper/splitters.py:532-577`) — a *finer* artifact (a splitter) than ragbot's disabled resolver (which only picks one of 5 fixed strategy names). The regex-splitter idea itself is unadopted (see M7).

**H6 — Coverage gate: detect without repair.**
- Reference: `repair_gaps_between_chunks` re-attaches every dropped span, then re-asserts; the packaged pipeline hard-fails on unrecoverable gaps (`postprocessing.py:128-151`, `pipeline.py:112-118`, asserts at `split_documents.py:112-155`).
- Ragbot: `check_chunk_gaps` result → `logger.warning("chunk_char_coverage_gap", …)` + step metadata only; docstring: "OBSERVE-only … NEVER raises" (`ingest_stages.py:891-905`, `coverage.py:36-38`). A detected dropped span (the exact silent-failure class the module's own docstring warns about, `coverage.py:5-13`) is logged but **stays lost**. FACT.

**H7 — Atomic FORMULA/IMAGE/CODE protection: implemented, default OFF.**
- `_smart_chunk_with_atomic_protect` routes atomic blocks around strategy splitters (`chunking/__init__.py:283-369`), gated by `formula_image_atomic_protect_enabled` = **False** (`constants/_00_app_env_taxonomy.py:105`). Default path protects tables only (via `_split_into_blocks` in recursive / table-isolation branches, `chunking/__init__.py:521-535`), so a `$$…$$` formula or fenced code block can still be cut by semantic/recursive splitters when the flag is off. FACT (flag value + dispatch); impact HYPOTHESIS (no corpus sample with mid-formula cut was reproduced in this audit).

**H8 — Docling backend: ported with a probable API-contract bug.**
- Reference iterates `for item, level in dl_doc.iterate_items():` — the docling API yields `(item, level)` **tuples** (`_external_refs/.../parsing.py:799`).
- Ragbot iterates `for item in doc.iterate_items():` and calls `getattr(item, "text", None)` on the tuple (`infrastructure/ocr/docling_parser.py:114-116`). A tuple has no `.text` → every item yields `content == ""` → `continue` → **zero blocks**. Also no `TableItem.export_to_dataframe` handling, so even unpacked, tables would flatten to `.text`. HYPOTHESIS (docling not installed here; cannot execute) but the two call shapes are directly contradictory in-tree. Bounded impact: docling is opt-in (`kreuzberg` is default, `constants/_13_adapchunk_ocr_parser.py:11`).

### 4.3 MISSED (valuable reference patterns with no ragbot analog)

**M1 — Per-document evaluate-then-select in production.** The reference's entire headline gain (Answer Correctness 78.0 vs 70-73, README Table 5) comes from scoring *real chunk outputs* per doc and picking argmax. Ragbot has every ingredient in-tree (multi-strategy dispatcher, 5-metric suite, bake-off harness proving 0/8 selector-oracle agreement) but the production selector never looks at a single real chunk. Cost of the reference approach is bounded and offline-amortizable: chunking 5 strategies is pure CPU (the bake-off already does it against the live corpus, `scripts/bakeoff_chunking_strategies.py:23` "Pure CPU re-chunking… Exit 0").

**M2 — Parser-emitted gold `split_points` + `titles` char-offset contract.** No ragbot parser emits boundary offsets; `Block` has no char span (`domain/entities/document.py:42-51` — fields end at `ocr_metadata`). Without gold boundaries: (a) a *true* BI metric is impossible — ragbot's BI is "fraction of blocks whose `len() <= chunk_size`" (`intrinsic_metrics.py:235-245`), which measures the *document*, not the *chunking*; (b) title spans can't be assigned by offset, forcing the fingerprint-search heuristics in `_prefix_section_headings` (`chunking/__init__.py:390-397` — 60-char prefix `text.find`, breaks on duplicated section headers); (c) ICC's sentence reconstruction from split_points is impossible. The local skill `block-integrity-quality-gate` already names this gap ("a label-free block-integrity metric from parser split-points").

**M3 — Gap repair.** `repair_gaps_between_chunks` (`postprocessing.py:128-151`) — see H6. Ragbot's `CoverageResult.uncovered_spans` already carries the original-offset spans needed to implement it (`coverage.py:83,238-245`); the repair function is a ~15-line addition at the call site.

**M4 — Per-chunk page metadata.** Reference: `get_page_info` interval-overlap page lists per chunk (`postprocessing.py:8-40`), carried into every chunk record (`pipeline.py:127-137`). Ragbot: `Block.page_number` and `Chunk.page_number` exist in the domain (`document.py:50,68`) but the persisted `document_chunks` column list has **no page column** (`ingest_helpers.py:188-198`: id, doc, bot, index, content, content_segmented, hash, embedding, metadata_json, [parent_chunk_id], chunk_chars, chunk_type, chunk_context) and no ingest code path writes page info into `metadata_json` (grep `page_number` under `application/services/document_service/` + `infrastructure/repositories/` = 0 hits). Citations therefore cannot point at a page. FACT.

**M5 — Title context as *metadata*, not content mutation.** Reference keeps `titles_context` separate from `chunk_text` (`postprocessing.py:42-64`), preserving the "joined chunks == source" invariant its tests pin (`tests/test_splitters.py:33-41`). Ragbot prepends `[H1 > H2]\n` / `## heading\n` **into the chunk content** (`strategies.py:309-311`, `chunking/__init__.py:405-407`). Consequences (FACT by construction): chunk `content_hash` changes when headings change even if the body didn't (defeats incremental re-embed dedup, `ingest_core.py:602` hashes enriched text); coverage locating gets `unlocated_chunks` for synthetic-prefixed chunks (`coverage.py:74-77` documents exactly this); and the same body under two headings is two distinct rows. Ragbot *does* have the right slot for this — `Chunk.contextual_prefix` + `text_for_embedding()` (`document.py:65,75-81`) — another built-but-unused surface on the legacy path.

**M6 — Token-based size accounting.** Reference budgets everything in tokens (tiktoken `o200k_base`, `chunking_utils.py:4-16`), including binary-search token-boundary hard splits (`splitters.py:142-181`) and token-budgeted table row-groups. Ragbot budgets in **chars** everywhere (`DEFAULT_CHUNK_SIZE = 1024` chars, `constants/_00_app_env_taxonomy.py:38`; `smart_chunk` docstring "max chars per chunk", `chunking/__init__.py:422`). For VN text char≈token ratios drift hard vs EN; embedder truncation limits are token-denominated (the project already burned on this once — jina 8k-token truncation noted in the reference's own `LLM.md:112`). HYPOTHESIS on impact size; FACT on the unit mismatch.

**M7 — LLM-regex per-document splitter.** A cheap 1-call-per-document LLM artifact (a validated regex delimiter) that scored #2 overall in the paper's Table 3 (89.80 mean, `README.md:41-49`). Ragbot's only LLM-in-chunking surface is the disabled strategy-name resolver (H5). Fits ragbot's Port+Registry pattern as one more strategy adapter; HALLU-neutral (regex splits text; it cannot fabricate content), though it does add an ingest-path LLM call (cost gate per CLAUDE.md T2).

**M8 — Embedding-based ICC/DCC (even offline).** Ragbot's Jaccard/gist proxies (`intrinsic_metrics.py:195-232`) are self-admittedly weak in absolutes (`scripts/bakeoff_chunking_strategies.py:17-21` trusts only relative ordering). The reference runs ICC/DCC through the *already-deployed* embedding stack. Ragbot has embedders wired for ingest; scoring K candidate chunkings of one doc offline (bake-off cadence, not per-ingest) would cost ~K× one doc's embed tokens. RC-via-coreference is reasonably skipped (maverick-coref is CC BY-NC-SA + GPU, `README.md:289-296` — incompatible with a commercial multi-tenant platform).

**M9 — Assert-per-strategy discipline.** Reference asserts the lossless invariant after **every** splitter and post-process independently (`split_documents.py:112-155`). Ragbot checks once, post-merge, per ingest — a strategy that drops text can be masked by a later merge, and per-strategy attribution is lost. (The local skill `block-integrity-quality-gate` mandates exactly the reference discipline.)

**M10 — `<Table>/<Figure>/<Formula>/<!-- PageBreak -->` tagged-markdown contract.** The reference makes structure machine-recoverable after the markdown hop. Ragbot's structured-markdown contract relies on pipe-table shape detection re-derivation (`analyze._is_table_line`), which is exactly the fragile step its own `_is_table_line` carve-outs keep patching (`analyze.py:186-210`). Explicit tags at parser output would let `_split_into_blocks` be a parser of tags, not heuristics. HYPOTHESIS on net benefit (would require re-ingest migration).

### 4.4 Where ragbot exceeds the reference (for balance; all FACT)

- **Stacked multi-row table header merge** (`tabular_markdown.py:105-138`) — reference mangles 2-row headers.
- **Table-footer preservation** (`blocks.py:21-64`) — no reference analog.
- **Multilingual/VN structural promotion** (`vn_structural.py`, `promote_vn_hierarchical_headings`) vs the reference's `skip_non_english` filter (`split_documents.py:63-77`).
- **Multi-tenant chunk identity** (Chunk carries `record_tenant_id`/`record_bot_id`/versions, `document.py:55-73`) — reference is single-corpus offline.
- **Test surface**: 41 chunking-related unit-test files (ls `tests/unit | grep -c chunk` = 41) vs 4 in the reference; includes pins for the L5 cross-check, coverage gate wiring, orphan merge, header dedup, dual-index.
- **Anthropic-CR chunk_context enrichment** (`chunk_context` column, `ingest_helpers.py:191-198`) — beyond the reference's scope.

---

## 5. Ranked findings

| # | Sev | Axis | Finding | Evidence |
|---|---|---|---|---|
| 1 | HIGH | T1-smartness | Core evaluate-then-select loop not adopted; production selector never scores real chunk output; own bake-off: 0/8 oracle agreement, +0.001 lift over recursive | `analyze.py:407-541`; `intrinsic_metrics.py:291-296`; `reports/bakeoff_chunking_20260620.md` |
| 2 | HIGH | T1-smartness | Coverage gate detects dropped source spans but never repairs them (reference repairs + asserts); silent content loss persists after detection | `ingest_stages.py:889-905`; `coverage.py:36-38` vs `postprocessing.py:128-151`, `pipeline.py:112-118` |
| 3 | HIGH | multi-format | Layer 6 `smart_chunk_atomic` + Block stream built-but-not-wired: ingest flattens to text; registry parsers emit no Blocks; Layer-2 context buffer output discarded | `chunking/__init__.py:653`; `ingest_stages.py:770`; `document_worker.py:463-466,500-501`; grep `Block(` in `infrastructure/parser/` = 0 |
| 4 | HIGH | sota-pattern | Ekimetrics selector metrics are strategy-invariant (computed on raw text + simulated chunks) and cite a "Rule-Based Selector" that doesn't exist in the reference code; flag default OFF anyway | `intrinsic_metrics.py:286-296,319,344-357`; `analysis.py:294-327`; `ingest_stages.py:574-580` |
| 5 | MED | T1-smartness | BI metric is a document property (block ≤ chunk_size), not a chunking property; root cause: no parser emits gold split_points/title offsets (Block has no char span) | `intrinsic_metrics.py:235-245` vs `metrics.py:264-307`; `document.py:42-51`; `parsing.py:12-23` |
| 6 | MED | multi-format | Per-chunk page metadata dropped: domain fields exist, DB column list has none, no ingest write path | `document.py:50,68`; `ingest_helpers.py:188-198` |
| 7 | MED | T3-design | Heading context injected into chunk *content* instead of `Chunk.contextual_prefix` metadata → content_hash churn, coverage unlocated chunks, dedup weakened | `strategies.py:309-311`; `chunking/__init__.py:405-407`; `document.py:65,75-81`; `coverage.py:74-77` |
| 8 | MED | sota-pattern | Docling adapter probably yields zero blocks: iterates `(item, level)` tuples without unpacking; no TableItem→dataframe export | `docling_parser.py:114-116` vs reference `parsing.py:799,805-811` (HYPOTHESIS — not executed) |
| 9 | MED | T3-design | Two AdapChunk DI registries explicitly dead/disabled in-tree with live tests pinning them (chunk_quality, chunking_strategy resolver) | `infrastructure/chunk_quality/registry.py:1-23`; `infrastructure/chunking_strategy/registry.py:1-10` |
| 10 | LOW | T2-cost-perf | Char-based (not token-based) size budgets everywhere; reference binary-searches token boundaries | `constants/_00_app_env_taxonomy.py:38`; `chunking_utils.py:4-16`; `splitters.py:142-181` |
| 11 | LOW | T1-smartness | FORMULA/IMAGE/CODE atomic protection default OFF — only tables protected on the default path | `constants/_00_app_env_taxonomy.py:105`; `chunking/__init__.py:490-535` |
| 12 | LOW | multi-tenant | `smart_chunk_atomic` fabricates sentinel uuid4 tenant/bot IDs when identity not plumbed — latent RLS hazard if wired without plumbing | `chunking/__init__.py:719-721` |
| 13 | INFO | test-health | Ragbot test surface (41 chunk test files) far exceeds reference (4), incl. wiring pins; but no test pins "joined chunks reconstruct source" per strategy (reference `test_splitters.py:33-41` invariant) | `ls tests/unit`, `split_documents.py:112-155` |
| 14 | INFO | multi-format | Ragbot exceeds reference on tables: stacked 2-row header merge + footer preservation | `tabular_markdown.py:105-138`; `blocks.py:21-64` |

---

## 6. Recommendations (tier-tagged, NOT executed — this audit is read-only)

1. **[T1-Smartness] Wire repair into the coverage gate.** Reuse `CoverageResult.uncovered_spans` to prepend dropped spans to the following chunk (reference `postprocessing.py:128-151` port), keep observe-log, add per-strategy assert in tests per skill `block-integrity-quality-gate`. Smallest change with direct HALLU-adjacent payoff (silent corpus loss → Coverage metric).
2. **[T1-Smartness] Make the bake-off the selector's feedback loop.** Short-term: run `scripts/bakeoff_chunking_strategies.py` per corpus on ingest-idle cadence and persist per-doc `oracle_best` as a `chunking_policy` override (evaluate-then-select, amortized offline — the reference's loop at zero per-ingest latency). Mid-term: score real candidate outputs at ingest for docs above a value threshold. Blocker to honesty: current lexical BI/ICC/DCC are weak (finding 5) — fix M2 first or use embed-based scoring offline (M8).
3. **[T1-Smartness] Emit char-offset `split_points`/`titles` from the kreuzberg/OCR block walk** (offsets are computable while concatenating block content) → unlocks true BI, offset-based title attach, and page mapping. This is the reference's keystone contract (`parsing.py:12-23`).
4. **[T3-Refactor] Move heading/path context from chunk content to `Chunk.contextual_prefix`/`metadata_json.parent_headings`** (slot already exists, `document.py:65`) — restores hash stability and coverage locateability. Requires an embed-text assembly change (`text_for_embedding` already handles it) + re-ingest.
5. **[T2-CostPerf] Decide the two dead registries** (chunk_quality, chunking_strategy resolver): revive behind the bake-off (as the scorer it needs) or delete per the in-file headers — carrying tested dead code taxes every audit.
6. **[T1-Smartness] Fix or fence the docling adapter** (`for item, level in …`; TableItem markdown export) before anyone flips `RAGBOT_PARSER_ENGINE=docling`; add a unit test with a stub docling doc that would fail today.
7. **[T2-CostPerf] Persist `page_number`** into `metadata_json` at minimum (no schema migration) so citations can carry page anchors like the reference's `chunk_pages`.

---

## 7. FACT/HYPOTHESIS register (rule #0 compliance)

- FACTs: every file:line above was read in this session; bake-off numbers quoted verbatim from `reports/bakeoff_chunking_20260620.md`; flag defaults quoted from `shared/constants/*` as of branch `fix-260623-ingest-expert`.
- HYPOTHESES (labelled inline): H8 docling zero-block behavior (needs `pip install docling` + fixture run); M6 char-vs-token impact size (needs VN corpus token-ratio measurement); H2 tenant-sentinel risk (needs a wiring attempt to manifest); H3 runtime consequence (needs a traced OCR-path ingest); paper-PDF section-name claim in finding 4 (only the *code* absence is FACT).
- NOT verified at runtime: no ingest was executed, no DB rows queried — this is a static architecture audit as scoped by the task (read-only mandate).
