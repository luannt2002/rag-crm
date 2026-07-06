# DEEPDIVE 2026-07-02 — Web Research: SOTA Chunking & Structured/Tabular Document Understanding for RAG

**Slug**: `web-chunking-tables` · **Scope**: 2024–2026 SOTA in chunking + table/spreadsheet understanding, mapped against ragbot's current implementation, with concrete recommendations for corpora dominated by **Vietnamese price-list spreadsheets + legal documents**.

**Evidence discipline (rule #0)**: every external claim carries a URL; every codebase claim carries `file:line`. Claims are labelled **FACT** (verifiable source in hand) or **HYPOTHESIS** (inference, not yet measured on ragbot corpora). No runtime measurements were run in this session — all "expected lift" statements below are HYPOTHESIS until load-tested per CLAUDE.md.

---

## 0. Executive summary

1. Ragbot is **already implementing a large fraction of 2025-SOTA table practice** — row-as-chunk with header re-attachment, multi-row header merge, merged-cell forward-fill, atomic table blocks, table+footer folding, dual-index option, enrich-row-gate. Several of these are **shipped-but-default-OFF** (`table_dual_index`, markdown normalizer, header/footer chunks), which is where the cheapest wins sit.
2. The 2025-2026 chunking literature converges on: **structure-aligned chunking beats semantic/embedding-similarity chunking** for structured corpora (legal, tables); **context augmentation (contextual retrieval / summary prefix) beats naive chunking** for prose; and **evaluation is corpus-specific** — one public benchmark even shows recursive-512 beating semantic chunking outright.
3. For **spreadsheets**, SOTA splits into two complementary layers: (a) *representation* (SpreadsheetLLM/SheetCompressor-style structural encoding; row/schema/cell indices per TableRAG) and (b) *execution* (SQL/program over an extracted relational form for aggregation queries — the EMNLP-2025 TableRAG result). Ragbot has (a) partially and (b) only in embryo (the B-AGG `count` dispatch).
4. For **legal docs**, two 2025-2026 papers directly validate ragbot's direction: structure-aligned (section/Điều-level) chunking wins for statutes, and *generic* document-summary-augmented chunks (SAC) beat both plain chunks and expert-engineered legal context. Ragbot's `markdown_normalize` (Chương/Mục/Điều → headings) is exactly this — but default OFF.
5. Parser stack: kreuzberg 4.x (ragbot L1 primary) wins on speed/footprint; **Docling (TableFormer) is the reference open-source choice for complex/scanned PDF tables**; the 2025-2026 frontier is RL-trained 7B VLM parsers (olmOCR 2, MinerU 2.5) benchmarked on OmniDocBench. Ragbot's Port+Registry parser design means adopting any of these = 1 adapter file.

---

## 1. Chunking SOTA 2024–2026

### 1.1 The sobering baseline result: simple recursive splitting is hard to beat

- **FACT (web)**: Vecta's February 2026 benchmark of 7 chunking strategies over 50 academic papers ranked **recursive 512-token splitting first at 69% accuracy**, with **semantic chunking at 54%** after it produced fragments averaging 43 tokens — reported in Firecrawl's 2026 chunking guide: <https://www.firecrawl.dev/blog/best-chunking-strategies-rag>.
- **FACT (web)**: the same guide's practical recommendation is "RecursiveCharacterTextSplitter with 512 tokens and 50 overlap … works for 80% of RAG applications" (same URL).
- **FACT (web)**: conversely, a clinical-domain study found adaptive chunking aligned to logical topic boundaries hitting 87% vs 13% for fixed-size (MDPI Bioengineering, Nov 2025, via the same Firecrawl survey) — i.e. **results flip per corpus**; chunking gains are not transferable without eval.
- **Implication for ragbot**: this validates the existing eval-first posture (Coverage metric, load-test gates). Any chunking change must go through `rag-loadtest` before default-flip — consistent with `DEFAULT_MARKDOWN_NORMALIZE_ENABLED: False` being gated on "re-ingest validation" (`src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py:60-65`).

### 1.2 Proposition chunking (Dense X Retrieval)

- **FACT (web)**: Dense X Retrieval (EMNLP 2024) introduced *propositions* — atomic, self-contained factoids — as the retrieval unit; proposition-level indexing outperformed passage/sentence units on five QA datasets, with the biggest lift in the 100–200-word retrieval budget range. Paper: <https://arxiv.org/abs/2312.06648>, ACL anthology: <https://aclanthology.org/2024.emnlp-main.845/>, project: <https://chentong0.github.io/factoid-wiki/>.
- **Cost caveat — HYPOTHESIS**: propositionization requires an LLM pass over the whole corpus at ingest (the paper used a fine-tuned "Propositionizer"); for ragbot's price-list rows this is wasted money because a row **already is** an atomic proposition (`"STT,Tên dịch vụ,Giá\n10,Item A,1.234.000"` — exactly the argument encoded in `DEFAULT_ENRICH_ROW_GATE_ENABLED` rationale, `_11_table_csv_chunking_strategy.py:167-180`).
- **FACT (code)**: ragbot already reserves the strategy name (`CHUNK_STRATEGY_PROPOSITION: Final[str] = "proposition"`, `_11_table_csv_chunking_strategy.py:47`).

### 1.3 Late chunking (Jina)

- **FACT (web)**: Late Chunking (arXiv 2409.04701, v3 July 2025) embeds the full document through a long-context embedder, then pools per-chunk token embeddings; it needs no training and gave ~2.7–3.6% average relative retrieval improvement over naive chunking across boundary types in the paper's evaluation. Paper: <https://arxiv.org/abs/2409.04701>; code: <https://github.com/jina-ai/late-chunking>.
- **FACT (code)**: ragbot's `shared/late_chunking.py` is explicitly a **practical approximation, not true late chunking** — cloud embedding APIs expose no token-level embeddings, so it prepends a 200-char document/window context prefix per chunk before embedding (`src/ragbot/shared/late_chunking.py:1-33`, `59`, `154-259`).
- **HYPOTHESIS (flagged)**: the module docstring's "+24.47% average nDCG on BeIR" (`late_chunking.py:23`) is materially higher than the ~3% average relative gains in the paper's own tables (arXiv 2409.04701 v3); the in-code citation likely reflects a single best-case dataset, not the average — worth correcting to avoid over-claiming (doc-only fix).
- **FACT (web)**: an independent comparison (arXiv 2504.19754, "Reconstructing Context") concludes **contextual retrieval preserves semantic coherence better but costs more compute; late chunking is cheaper but "tends to sacrifice relevance and completeness"** — <https://arxiv.org/abs/2504.19754>.

### 1.4 Contextual Retrieval (Anthropic) — the numbers

- **FACT (web)** from <https://www.anthropic.com/engineering/contextual-retrieval>:
  - Contextual embeddings alone: top-20 retrieval failure rate 5.7% → 3.7% (**−35%**).
  - - contextual BM25: → 2.9% (**−49%**).
  - - reranking: → 1.9% (**−67%**).
  - One-time cost with prompt caching: **≈ $1.02 per million document tokens** (800-token chunks, 8k-token docs, 50-token instruction, 100-token context).
  - Guidance: skip RAG entirely under ~200k tokens; retrieve top-20, not top-5/10.
- **FACT (code)**: ragbot shipped CR then flipped it default OFF on 2026-06-17 because per-chunk whole-doc prompts were an "O(n²) token storm", asserting the late-chunking prefix now covers cross-chunk context (`_11_table_csv_chunking_strategy.py:106-119`, `134-145`; alembic 0228/0231 referenced there). The same file's window analysis correctly records that **prompt caching, not windowing, is the paper-backed cost fix** (`:121-132`).
- **HYPOTHESIS**: given §1.3's independent finding that prefix-style approximation under-performs CR on relevance/completeness, "late chunking supersedes CR" is currently an **unmeasured claim** for ragbot's corpora; a Coverage A/B (CR-on-with-caching vs prefix-only) on the legal bot is the cheapest way to resolve it. The row-gate should stay ON either way (tables don't need CR — §3.4).

### 1.5 Hierarchical & summary-augmented chunking

- **FACT (web)**: HiChunk (arXiv 2509.11552) — fine-tuned-LLM multi-level document structuring + Auto-Merge retrieval; introduces **HiCBench** with human-annotated multi-level chunk points and evidence-dense QA, showing chunking quality materially moves RAG quality: <https://arxiv.org/pdf/2509.11552>.
- **FACT (web)**: Summary-Augmented Chunking (SAC) for large legal datasets (arXiv 2510.06999) — prepend a **document-level synthetic summary** to every chunk; "greatly reduces" Document-Level Retrieval Mismatch (retriever picking the wrong *document*) and improves precision/recall; notably **generic summaries beat legal-expert-engineered summaries**: <https://arxiv.org/abs/2510.06999>.
- **FACT (web)**: hierarchical chunking (small chunks for finding, parents for reading) is described as the dominant production pattern 2025-2026: <https://www.firecrawl.dev/blog/best-chunking-strategies-rag>, <https://atlan.com/know/chunking-strategies-rag/>.
- **Fit to ragbot — FACT (code)**: SAC is architecturally ⊂ ragbot's existing prefix machinery — it is `late_chunk_embed(document_summary=<LLM summary>)` instead of `document_summary=<first 200 chars>` (`late_chunking.py:54-99`). Cost = **one** LLM summary per document (vs CR's one call per chunk), and the persisted-prefix option already exists (`DEFAULT_ENRICHED_PREFIX_PERSIST: True`, `_11_table_csv_chunking_strategy.py:95`) so BM25 sees it too — matching Anthropic's "contextual BM25" half of the −49%.

### 1.6 Legal-document chunking specifically

- **FACT (web)**: Chunking German Legal Code (arXiv 2605.19806): across structural/fixed/contextual/semantic/RAPTOR strategies, **chunking aligned to the statute's own structure (section/subsection) achieved highest recall**; simpler domain-preserving methods beat LLM-heavy semantic approaches on cost too: <https://arxiv.org/abs/2605.19806>.
- **FACT (web)**: LawRAG (Indonesian statutes) found article-level chunking (title + article + paragraph) beats sequential chunking on accuracy: <https://www.emerald.com/dta/article/60/2/330/1353532/LawRAG-Indonesian-legal-document-retrieval>.
- **FACT (web)**: Massive Legal Embedding Benchmark (MLEB, Oct 2025) — 10 datasets across jurisdictions/tasks for legal embedding evaluation: <https://huggingface.co/blog/isaacus/legal-rag-bench>.
- **FACT (code)**: ragbot's Phase-C normalizer promotes plain-text VN `Chương/Mục/Điều` markers to ATX headings pre-chunking — **default OFF** (`DEFAULT_MARKDOWN_NORMALIZE_ENABLED: False`, `_11_table_csv_chunking_strategy.py:60-65`). Past bug history (Điều 11 miss for missing `article_number` metadata — memory `project_5phase_shipped_20260512`) is the same failure class these papers close.

---

## 2. Document parsing / layout models 2025-2026

### 2.1 Library benchmarks

- **FACT (web)**: Procycons 2025 benchmark (Docling vs Unstructured vs LlamaParse): LlamaParse ~6s/doc at any page count (cloud); Unstructured 51s/1p → 141s/50p; **Docling recommended for precision + structural fidelity**, esp. table-heavy enterprise docs: <https://procycons.com/en/blogs/pdf-data-extraction-benchmark/>.
- **FACT (web)**: Reducto's comparison notes Docling's IBM-backed transformer table extraction (recommended "if your documents are 90% tables"), LlamaParse's DocLayNet+TableFormer lineage: <https://llms.reducto.ai/document-parser-comparison>. Unstructured's own benchmark counterpoint: <https://unstructured.io/benchmarks>.
- **FACT (web)**: kreuzberg's own 2025 benchmark suite: 71MB install vs Docling 1GB+, throughput up to 35 files/s vs minutes/file, multi-language incl. CJK: <https://benchmarks.kreuzberg.dev/>, <https://goldziher.github.io/python-text-extraction-libs-benchmarks/>.
- **FACT (web)**: Docling technical report (TableFormer table-structure model, DocLayNet layout): <https://arxiv.org/pdf/2408.09869>.
- **FACT (code)**: ragbot's L1 primary parser is kreuzberg ≥4.3 ("97 formats", `src/ragbot/infrastructure/parser/kreuzberg_markdown_parser.py:9`), with byte-sniff via `kreuzberg.detect_mime_type_from_bytes` (`:84`), and a full Port+Registry parser set (`docx_parser.py`, `excel_openpyxl_parser.py`, `google_sheets_parser.py`, `markdown_parser.py`, `pdf_parser.py`, `vlm_image_parser.py`, `null_parser.py`, `registry.py` — `src/ragbot/infrastructure/parser/`).
- **Verdict — HYPOTHESIS**: kreuzberg is the right default for ragbot's speed/footprint envelope; the gap is **complex/scanned PDF tables** (bordered/borderless, spanning cells) where TableFormer-class models lead. Registry design makes a `docling` adapter a 1-file, per-bot-opt-in addition (matches `parser-adapter-pattern` skill).

### 2.2 Table structure recognition (TSR) models

- **FACT (web)**: TATR (Microsoft Table Transformer) — DETR-based detection + structure recognition; official repo also hosts **PubTables-1M** and the **GriTS** metric: <https://github.com/microsoft/table-transformer>; HF model: <https://huggingface.co/microsoft/table-transformer-structure-recognition>. TATR explicitly models **spanning cells (merged cells), column/row headers, projected row headers**.
- **FACT (web)**: TABLET (2025, arXiv 2506.07015) — encoder-only Split-Merge transformer, ~0.94 structural F1, optimized for large dense tables; merging handles spanning cells as grid-cell classification: <https://arxiv.org/abs/2506.07015>.
- **FACT (web)**: POTATR extends TATR to jointly detect tables + rows/columns/header cells/spanning cells/captions/footers with hierarchy: <https://www.emergentmind.com/topics/page-object-table-transformer-potatr>. gmft is the light-weight TATR packaging commonly used in Python RAG stacks: <https://gmft.readthedocs.io/en/latest/formatters/tatr.html>.

### 2.3 VLM (OCR-free) parsing wave

- **FACT (web)**: OmniDocBench (CVPR 2025) is the de-facto parsing benchmark — 1651 pages, 10 doc types, block+span annotations incl. tables: <https://github.com/opendatalab/OmniDocBench>.
- **FACT (web)**: MinerU 2.5 — decoupled VLM for high-res parsing, benchmarked vs GPT-4o/Gemini-2.5-Pro/Qwen2.5-VL/dots.ocr/MonkeyOCR/olmOCR: <https://arxiv.org/pdf/2509.22186>; MonkeyOCR v1.5 technical report: <https://arxiv.org/html/2511.10390v2>; olmOCR 2 = RL-trained 7B on Qwen2.5-VL-7B with verifiable rewards (tables/equations/multi-column): <https://atul4u.medium.com/beyond-text-extraction-the-2025-open-ocr-revolution-powered-by-vision-language-models-89ad33d36bbf>; Qwen2.5-VL technical report: <https://arxiv.org/pdf/2502.13923>.
- **FACT (code)**: ragbot already has a `vlm_image_parser.py` adapter slot (`src/ragbot/infrastructure/parser/vlm_image_parser.py`) — the registry can host an olmOCR/MinerU-class adapter for scanned VN legal PDFs without touching the orchestrator.

---

## 3. Tables & spreadsheets for RAG

### 3.1 Table serialization: table-to-text formats

- **FACT (web)**: NAACL-2024 industry study (arXiv 2402.12869) compared Markdown / template / TPLM / LLM table-to-text inside DSFT vs RAG: **in the RAG paradigm, Markdown was "unexpectedly efficient"**, LLM-generated narration also strong: <https://aclanthology.org/2024.naacl-industry.41/>.
- **FACT (web)**: format-vs-task evidence — JSON/DFLoader better for fact-finding/transformation; **HTML/XML better understood by GPT-family for tabular QA**; and "no single best serialization — it must be tailored to the embedding model": <https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/>; survey: <https://assets.amazon.science/f1/0c/5f8a587c452d9b1c687f70b731ab/large-language-models-llms-on-tabular-data-prediction-generation-and-understanding-a-survey.pdf>.
- **FACT (code)**: ragbot's canonical form is structured markdown pipe-tables everywhere (`rows_to_structured_markdown`, `src/ragbot/shared/tabular_markdown.py:214`; parser contract in `excel_openpyxl_parser.py:2-8`) — aligned with the RAG-paradigm evidence. No change recommended (T3-neutral).

### 3.2 Row-level vs cell-level vs schema-level indexing

- **FACT (web)**: TableRAG (Google, NeurIPS 2024, arXiv 2410.04739) — for million-token tables, index **schema** (column name + type + range) and **cells** (column,value pairs with frequency-aware truncation) as separate retrieval indices; query-expand then retrieve a small budget from each; SOTA on Arcade/BIRD-SQL-derived benchmarks: <https://arxiv.org/abs/2410.04739>, <https://neurips.cc/virtual/2024/poster/96701>.
- **FACT (web)**: FT-RAG (2026, arXiv 2605.01495) pushes finer-grained *entry-level* retrieval with row-and-column-level (RCL) and hierarchical H-RCL summaries capturing multi-level header + cell dependencies: <https://arxiv.org/html/2605.01495>; topic survey: <https://www.emergentmind.com/topics/retrieval-augmented-generation-over-tables-rag>.
- **FACT (code)**: ragbot indexes **row-level** (row-as-chunk with header + section bound in — `excel_openpyxl_parser.py:109-122`, `split_markdown_to_row_chunks` at `tabular_markdown.py:358`), and has a **whole-table group index** (`table_dual_index`: row chunks PLUS ≤4000-char group chunks, `src/ragbot/shared/chunking/csv_chunker.py:357`, cap at `_11_table_csv_chunking_strategy.py:53-57`) — but the **platform default is row-only** (`DEFAULT_TABLE_STRATEGY = "table_csv"`, `_11:28`), with the file itself documenting the known failure: *"aggregation/'list-all' queries miss rows after top-k/rerank cap"* (`_11:21-23`).
- **Assessment — HYPOTHESIS**: for price-list bots, row-level is the right primary unit (each row = atomic proposition, §1.2), and dual-index is the correct SOTA-consistent answer to list-all queries; cell-level indexing (TableRAG-style) only pays off at very large tables (≥100k cells) which ragbot's per-sheet sizes (hundreds of rows, `_11:174`) do not reach.

### 3.3 Table-QA / program execution vs pure retrieval

- **FACT (web)**: TableRAG (EMNLP 2025, arXiv 2506.10380 — different paper, same name) shows flatten-and-chunk "disrupts the intrinsic tabular structure, leads to information loss"; its fix is a 4-step loop (query decomposition → text retrieval → **SQL programming & execution** → compositional answers) over tables extracted into a relational DB; SOTA on HeteQA + public benchmarks: <https://arxiv.org/abs/2506.10380>, ACL: <https://aclanthology.org/2025.emnlp-main.710/>.
- **FACT (web)**: SpreadsheetLLM's Chain-of-Spreadsheet similarly routes QA through structure-aware compression then targeted reading: <https://arxiv.org/abs/2407.09025>.
- **FACT (code)**: ragbot's first step in this direction exists — commit `949a3a4` "fix(stats): B-AGG count dispatch — op=count + count_by_name_keyword COUNT(*) + count-fact chunk" (git log, repo HEAD area) computes counts outside the LLM at ingest/stat layer. There is no general aggregation/execution path (no SQL-over-rows engine) — grep for pivot/aggregation execution over `document_chunks` finds none in `src/ragbot/` (see §3.5).
- **HYPOTHESIS**: for "tổng cộng bao nhiêu dịch vụ dưới 500k?"-class queries on price lists, retrieval-only pipelines will keep failing at top-k regardless of chunking; the SOTA-correct medium-term move is a **deterministic aggregation port** (rows already parse into cells at ingest) exposed as a tool/dispatch — NOT LLM answer-override (Quality Gate #10 compliant: it supplies grounded facts as context, the LLM still writes the answer).

### 3.4 Spreadsheet-specific: SpreadsheetLLM, TableSense, headers/merged cells/pivots

- **FACT (web)**: SpreadsheetLLM / SheetCompressor (Microsoft, arXiv 2407.09025): structural-anchor compression + inverse index + format-aware aggregation → **25× compression, 96% token reduction, 78.9 F1 table detection (+12.3 over prior SOTA)**: <https://arxiv.org/abs/2407.09025>, <https://www.microsoft.com/en-us/research/publication/encoding-spreadsheets-for-large-language-models/>.
- **FACT (web)**: TableSense (AAAI 2019, still the reference for sheet table detection): CNN over cell matrix, **91.3% recall / 86.5% precision (EoB-2)** for detecting *multiple tables + ranges on one sheet*: <https://ojs.aaai.org/index.php/AAAI/article/view/3770>.
- **FACT (web)**: multi-row headers are a first-class problem ("Temperature" spanning row above "Avg|Max" row) — header detection/classification literature: <https://www.researchgate.net/publication/286968486_Table_Header_Detection_and_Classification>; 2026 multi-agent multi-format spreadsheet reasoning: <https://arxiv.org/pdf/2604.12282>.
- **FACT (code)** — ragbot's existing coverage is unusually strong here:
  - **Multi-row/split header merge**: `_is_header_continuation` + `_merge_header_fill` (`tabular_markdown.py:105`, `:127`) build compound labels from consecutive label rows — the `multi-row-header-merge` skill codifies it.
  - **Merged-cell / rowspan forward-fill**: `_normalize_rows` forward-fills leading contiguous sparse columns left by merged group labels (`tabular_markdown.py:144-212`).
  - **Header detection by FORM not vocabulary**: `_looks_header` / `_is_label_like` / money-shape contrast (`tabular_markdown.py:60-103`) — matches the `table-header-detect-structural` skill and stays domain-neutral.
  - **Table+footer atomicity**: `_merge_table_footer_blocks` folds ≤N-char trailing notes ("Đơn giá đã bao gồm VAT") into the table block (`src/ragbot/shared/chunking/blocks.py:21-64`); atomic block protection for table/formula/image/code (`blocks.py:127-146`).
  - **Self-describing-row gate**: per-chunk LLM enrichment skipped for table strategies, "cuts ingest LLM calls ~80-90%" (`_11:167-180`) — consistent with SOTA cost logic (context augmentation is for prose, not rows).
- **GAP — FACT (code)**: **no pivot-table / non-relational layout detection exists** — `grep -rni pivot src/ragbot --include='*.py'` returns only two unrelated hits (`bot_management_service.py:325`, `reranker/_modality_boost.py:72`). A pivoted price sheet (services × branch-columns, values in the matrix) will be serialized as-is; row-as-chunk then binds each service to a *row* of branch prices with the branch names only in the header — recoverable — but a *stacked* pivot (metric rows × month columns with subtotal blocks) has no un-pivoting pass. TableSense/SpreadsheetLLM-class structural detection is the reference solution.
- **GAP — FACT (code)**: multi-table-per-sheet handling relies on section-title heuristics in `rows_to_structured_markdown` (`excel_openpyxl_parser.py:4-8`); there is no TableSense-style range detection for side-by-side tables (two tables sharing rows, separated by blank *columns*) — `_normalize_rows` operates on the full row width.

### 3.5 Table-to-text narration vs raw rows

- **FACT (web)**: LLM-narrated table descriptions improve RAG retrieval in hybrid-data QA (2402.12869, §3.1), and evidence-contextualization for conversational RAG over heterogeneous data: <https://arxiv.org/pdf/2412.10571>.
- **FACT (code)**: ragbot has a `narrate` infrastructure module (`src/ragbot/infrastructure/narrate/`, `application/services/narrate_service.py` — dir listing) — table narration exists as a capability; assessing its default wiring is out of this report's scope.

---

## 4. Vietnamese-language specifics

- **FACT (web)**: **VN-MTEB** (arXiv 2507.21500) — 41 datasets, 6 tasks (incl. retrieval), LLM-translated from MTEB with NE/code preservation; finding: larger models with **Rotary Positional Embedding outperform APE** models on Vietnamese embedding tasks; datasets on HuggingFace: <https://arxiv.org/abs/2507.21500>.
- **FACT (web)**: Vietnamese IR training/benchmark work (arXiv 2503.07470): <https://arxiv.org/abs/2503.07470>; Vietnamese legal QA system practice — RRF fusion + reranking + handling long-token VN legal text (arXiv 2409.13699): <https://arxiv.org/abs/2409.13699>; Vietnamese legal LLM benchmarks: VLegal-Bench <https://www.researchgate.net/publication/398766650_VLegal-Bench_Cognitively_Grounded_Benchmark_for_Vietnamese_Legal_Reasoning_of_Large_Language_Models>, arXiv 2512.14554 <https://arxiv.org/html/2512.14554>.
- **FACT (web)**: halong_embedding (multilingual-e5-base fine-tune, evaluated on Zalo legal retrieval) is the visible open VN embedding baseline: <https://model.aibase.com/models/details/1915693196304343042>.
- **FACT (memory/history)**: ragbot's worst retrieval bug class was **cross-lingual embedding mismatch** (corpus narrated in English + VN query; "700.000" chunk never reached top-K — CLAUDE.md lessons-learned section) and compound-word recall (bigram/trigram vocab expansion, `_11:214-229`).
- **HYPOTHESIS**: ragbot's current embedder (ZeroEntropy zembed-1 1280-dim, per project memory 2026-05-12) has, to this report's knowledge, **no published VN-MTEB score** — running the VN-MTEB retrieval subset (public HF datasets) against zembed-1 vs a multilingual-e5-large / bge-m3 / gemini-embedding baseline is a cheap, decisive experiment for the single highest-leverage retrieval variable on VN corpora.

---

## 5. Gap analysis → concrete recommendations

Ranked by T1 impact ÷ effort. **All "expected lift" values are HYPOTHESIS until measured** (rule #0); each rec names its measurement gate.

| # | Tier | Recommendation | Evidence base | Effort |
|---|------|----------------|---------------|--------|
| R1 | T1 | **Flip `table_dual_index` default for price-list bots** (per-bot `plan_limits.chunking_config.table_strategy`) after a list-all/aggregation Coverage A/B. Code is shipped (`csv_chunker.py:357`); default row-only is documented as failing aggregation queries (`_11:21-23`). | TableRAG 2410.04739 (multi-granularity indexing); FT-RAG 2605.01495 | config flip + load test |
| R2 | T1 | **Flip `markdown_normalize_enabled` for legal bots** (Chương/Mục/Điều → headings → structure-aligned chunks). Structure-aligned section chunking = highest recall for statutes. | arXiv 2605.19806 (German legal); LawRAG; code `_11:60-65` | config flip + re-ingest + load test |
| R3 | T1 | **Upgrade the late-chunking prefix from "first-200-chars" to SAC**: one LLM-generated document summary per doc as the shared prefix (persisted, so BM25 sees it — `_11:95`). O(1) LLM call/doc vs CR's O(n_chunks). Use *generic* summaries (expert-targeted summaries underperformed). | arXiv 2510.06999 (SAC); Anthropic CR numbers as ceiling (−49/67%) | ~1 day + eval |
| R4 | T1 | **Resolve the CR-vs-prefix question with data**: A/B CR-on (whole-doc prompt + prompt caching ≈ $1.02/Mtok) vs prefix/SAC on the legal bot; keep `enrich_row_gate` ON for tables. The 2026-06-17 "late chunking supersedes CR" decision is currently unmeasured, and independent eval says prefix-style loses to CR on completeness. | anthropic.com/engineering/contextual-retrieval; arXiv 2504.19754; code `_11:106-145` | load test only |
| R5 | T1 | **Add a `docling` parser adapter** (registry row, per-bot opt-in) for complex/scanned PDF tables where kreuzberg's markdown loses cell structure; TableFormer handles spanning/merged cells natively. Keep kreuzberg default (speed/footprint). | Docling report 2408.09869; Procycons benchmark; Reducto comparison; `parser-adapter-pattern` skill | 1 adapter file + goldens |
| R6 | T1/T2 | **Deterministic aggregation port for tabular corpora** (extend the B-AGG `count` dispatch of `949a3a4` toward sum/min/max/filter over ingested rows; SQL/pandas over cells captured at parse time). Supplies grounded facts to the LLM as context — no answer override. | TableRAG EMNLP-2025 2506.10380 (SQL execution beats flatten-chunk); SpreadsheetLLM CoS | plan-level (multi-day) |
| R7 | T2 | **Pivot / non-relational sheet layout detection**: shape-only heuristic pass (value-matrix with 2 label axes → unpivot to long form before row-chunking); currently zero coverage (grep FACT §3.4). Start heuristic; TableSense-class model only if heuristics miss in stress tests. | TableSense AAAI-2019; SpreadsheetLLM structural anchors; 2604.12282 | 2-3 days + table-taxonomy stress test |
| R8 | T2 | **Side-by-side multi-table-per-sheet detection** (blank-column table separation) in `_normalize_rows`/`rows_to_structured_markdown` — currently full-row-width assumption. | TableSense (multi-table + range detection) | 1-2 days |
| R9 | T1 | **Run VN-MTEB retrieval subset vs current embedder** (zembed-1) and 2-3 multilingual baselines; embedding choice dominates all chunking gains on VN corpora given history of cross-lingual misses. | VN-MTEB 2507.21500 (public datasets); CLAUDE.md 2026-06-03 case study | eval-only |
| R10 | T2 | **Flip `table_csv_emit_header_footer_chunks` ON after A/B** — "what is this table about" + trailing-promo retrieval currently drops pre/post-table prose by default (`_11:67-74`). | RAG-Anything atomic-unit mindset (code-cited arXiv 2503.13838 at `blocks.py:130`) | config flip + load test |
| R11 | T3 | **Doc-only**: correct the "+24.47% nDCG" citation in `late_chunking.py:23` to the paper's actual average relative gains (~2.7-3.6%) with the single-dataset best case noted separately. | arXiv 2409.04701 v3 | trivial |
| R12 | T3 | **Watch-list, no action**: proposition chunking for prose corpora only (never rows); VLM parser adapter (olmOCR-2/MinerU-2.5) behind `vlm_image_parser` slot when scanned VN PDFs appear; HiCBench-style chunk-boundary eval as a future `block-integrity-quality-gate` extension. | §1.2, §2.3, §1.5 | — |

### What NOT to change (already SOTA-consistent — FACT, code)

- Markdown pipe-table as canonical serialization (§3.1 evidence favors it for RAG).
- Row-as-chunk primary unit with header re-attachment (§3.2; `excel_openpyxl_parser.py:109-122`).
- Multi-row header merge + merged-cell forward-fill + form-based header detection (`tabular_markdown.py:105-212`) — ahead of most open-source stacks.
- Enrich-row-gate (skip LLM context on self-describing rows, `_11:167-180`) — exactly the cost logic SOTA implies.
- Atomic table/formula/image/code protection + footer folding (`blocks.py:21-146`).
- Port+Registry parser architecture — makes every recommendation above a 1-file or config-only change.

---

## 6. Source index

**Chunking**: [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) · [Late Chunking arXiv 2409.04701](https://arxiv.org/abs/2409.04701) · [jina-ai/late-chunking](https://github.com/jina-ai/late-chunking) · [Dense X Retrieval arXiv 2312.06648](https://arxiv.org/abs/2312.06648) · [Reconstructing Context arXiv 2504.19754](https://arxiv.org/abs/2504.19754) · [HiChunk arXiv 2509.11552](https://arxiv.org/pdf/2509.11552) · [Firecrawl chunking 2026](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) · [Atlan chunking guide](https://atlan.com/know/chunking-strategies-rag/)

**Legal**: [SAC arXiv 2510.06999](https://arxiv.org/abs/2510.06999) · [German legal chunking arXiv 2605.19806](https://arxiv.org/abs/2605.19806) · [LawRAG](https://www.emerald.com/dta/article/60/2/330/1353532/LawRAG-Indonesian-legal-document-retrieval) · [MLEB](https://huggingface.co/blog/isaacus/legal-rag-bench)

**Tables/spreadsheets**: [SpreadsheetLLM arXiv 2407.09025](https://arxiv.org/abs/2407.09025) · [TableRAG NeurIPS-24 arXiv 2410.04739](https://arxiv.org/abs/2410.04739) · [TableRAG EMNLP-25 arXiv 2506.10380](https://arxiv.org/abs/2506.10380) · [FT-RAG arXiv 2605.01495](https://arxiv.org/html/2605.01495) · [TableSense AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/3770) · [Table-to-text NAACL-24 arXiv 2402.12869](https://aclanthology.org/2024.naacl-industry.41/) · [Serialization Kitchen](https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/) · [LLM-tabular survey](https://assets.amazon.science/f1/0c/5f8a587c452d9b1c687f70b731ab/large-language-models-llms-on-tabular-data-prediction-generation-and-understanding-a-survey.pdf) · [RAG-over-tables survey](https://www.emergentmind.com/topics/retrieval-augmented-generation-over-tables-rag) · [Header detection](https://www.researchgate.net/publication/286968486_Table_Header_Detection_and_Classification) · [Spreadsheet multi-agent arXiv 2604.12282](https://arxiv.org/pdf/2604.12282)

**Parsing/TSR/VLM**: [Docling report arXiv 2408.09869](https://arxiv.org/pdf/2408.09869) · [TATR](https://github.com/microsoft/table-transformer) · [TATR HF](https://huggingface.co/microsoft/table-transformer-structure-recognition) · [TABLET arXiv 2506.07015](https://arxiv.org/abs/2506.07015) · [POTATR](https://www.emergentmind.com/topics/page-object-table-transformer-potatr) · [gmft](https://gmft.readthedocs.io/en/latest/formatters/tatr.html) · [Procycons benchmark](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/) · [Reducto comparison](https://llms.reducto.ai/document-parser-comparison) · [Unstructured benchmarks](https://unstructured.io/benchmarks) · [kreuzberg benchmarks](https://benchmarks.kreuzberg.dev/) · [goldziher benchmarks](https://goldziher.github.io/python-text-extraction-libs-benchmarks/) · [OmniDocBench](https://github.com/opendatalab/OmniDocBench) · [MinerU 2.5 arXiv 2509.22186](https://arxiv.org/pdf/2509.22186) · [MonkeyOCR 1.5 arXiv 2511.10390](https://arxiv.org/html/2511.10390v2) · [Qwen2.5-VL arXiv 2502.13923](https://arxiv.org/pdf/2502.13923) · [2025 open-OCR overview](https://atul4u.medium.com/beyond-text-extraction-the-2025-open-ocr-revolution-powered-by-vision-language-models-89ad33d36bbf)

**Vietnamese**: [VN-MTEB arXiv 2507.21500](https://arxiv.org/abs/2507.21500) · [VN IR arXiv 2503.07470](https://arxiv.org/abs/2503.07470) · [VN legal QA arXiv 2409.13699](https://arxiv.org/abs/2409.13699) · [VLegal-Bench](https://www.researchgate.net/publication/398766650_VLegal-Bench_Cognitively_Grounded_Benchmark_for_Vietnamese_Legal_Reasoning_of_Large_Language_Models) · [VN legal LLM bench arXiv 2512.14554](https://arxiv.org/html/2512.14554) · [halong_embedding](https://model.aibase.com/models/details/1915693196304343042)

**Ragbot code evidence**: `src/ragbot/shared/late_chunking.py:1-33,54-99,154-259` · `src/ragbot/shared/chunking/blocks.py:21-146` · `src/ragbot/shared/chunking/csv_chunker.py:357-451` · `src/ragbot/shared/tabular_markdown.py:56-358` · `src/ragbot/infrastructure/parser/excel_openpyxl_parser.py:75-122` · `src/ragbot/infrastructure/parser/kreuzberg_markdown_parser.py:3-98` · `src/ragbot/shared/constants/_11_table_csv_chunking_strategy.py:5-180` · git `949a3a4` (B-AGG count dispatch).
