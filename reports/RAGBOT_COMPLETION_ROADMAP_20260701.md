# RAGBOT Completion Roadmap — 2026-07-01

> Multi-agent workflow wlnr4crj4: 50-problem inventory + mined solutions (adaptive-chunking, RAG-Anything, tldw_server, RAG papers) + phased roadmap. EVOLVE-not-rewrite.

## Inventory (50 problems)

| id | layer | status | severity | title |
|---|---|---|---|---|
| L1-DOCX-BYPASS | L1-structure-recovery | OPEN | high | DOCX table parser bypasses rows_to_structured_markdown — hardcodes rows[0]=header, no mult |
| L1-KREUZBERG-NO-STRUCT | L1-structure-recovery | OPEN | high | Kreuzberg (PDF/HTML/PPTX) markdown parser does NOT route tables through rows_to_structured |
| L1-XLSX-SHEET-ONE-BLOB | L1-structure-recovery | OPEN | high | XLSX/Sheets parser emits whole sheet as ONE markdown blob (row-as-chunk depends on downstr |
| L1-COL-N-OOV-HEADER | L1-structure-recovery | PROVEN-NOT-SHIPPED | high | Out-of-vocab / blank / non-VN headers collapse to col_N placeholder (silent unlabeled colu |
| L1-MERGED-CELL-ROWSPAN | L1-structure-recovery | OPEN | medium | Merged-cell / rowspan group-empty rows lose items (no forward-fill); category-stub col0 st |
| L1-NAME-WITH-MONEY-DROP | L1-structure-recovery | OPEN | medium | Row dropped when name cell contains money text (parse_money_vn matches money inside name → |
| L1-TRANSPOSED-KV-PIVOT | L1-structure-recovery | OPEN | low | Transposed / key-value-vertical / 2-D pivot / year-column tables produce garbage entities  |
| L1-SECTION-IN-HEADER-LONGTITLE | L1-structure-recovery | OPEN | low | Section-in-header rows and long (>8 word) single-cell titles lose the section boundary |
| L1-EMPIRICAL-FORMATS-BREAK | L1-structure-recovery | DIAGNOSED | high | Empirical taxonomy audit: 12 of 27 structural cases BUG (sai/mất/rác); real-format re-inge |
| L2-ALIAS-FLOOD-EMBED | L2-chunking | OPEN | high | Aliases column dumped verbatim into embedded chunk content (dozens of spec variants) drown |
| L2-ROW-MIXING | L2-chunking | OPEN | high | Size-based chunker packs multiple table rows into one chunk → bot binds inventory/price of |
| L2-LINEARIZE-NO-COL-LABEL | L2-chunking | OPEN | high | Row-linearized synthetic stats chunk renders values WITHOUT column labels (col_4:214 | col |
| L2-TABLE-ROW-NO-BREADCRUMB | L2-chunking | OPEN | medium | U5 enrich SKIPS table-row chunks → table chunks carry no # Doc > ## Section breadcrumb (se |
| L2-DUAL-READ-CSV-DISQUAL | L2-chunking | OPEN | medium | CSV/Sheets row-as-chunk fast-path disqualified when parser emits ## headings (headings>0)  |
| ANALYTIC-COUNT-DISPATCH | analytical | PROVEN-NOT-SHIPPED | high | operation=count parsed but count-path (count_by_name_keyword COUNT(*) + dispatch branch) i |
| ANALYTIC-COUNT-DEAD-METHOD | analytical | OPEN | medium | count_by_price_range (real COUNT(*)) is DEAD — referenced only by its own unit test, never |
| ANALYTIC-SILENT-UNDERCOUNT | analytical | OPEN | high | List/count LIMIT-capped at DEFAULT_STATS_INDEX_LIMIT=100 → a set larger than the cap silen |
| ANALYTIC-NO-SUM-AVG | analytical | OPEN | medium | No SUM / AVG anywhere — no signal, no parser, no SQL |
| ANALYTIC-NO-GROUPBY-SERIES | analytical | OPEN | high | No GROUP BY / COUNT(DISTINCT) — B-SERIES 'có bao nhiêu loại Landspider'=5 series (group-by |
| ANALYTIC-SUMMARY-ORPHAN | analytical | OPEN | medium | summary_json is write-only ORPHAN — computed at ingest, 0 read sites at answer; matches_su |
| ANALYTIC-B-ROLE-QTY-DATE | analytical | OPEN | high | B-ROLE: quantity/date are NOT roles (_roles_def only name/category/aliases/price) → land a |
| ANALYTIC-TWO-CLASSIFIERS | analytical | OPEN | medium | Two decoupled classifiers never cross-validate: cosmetic intent label (only tunes grade/re |
| ANALYTIC-NO-FUNCTION-CALLING | analytical | OPEN | low | Function-calling not wired (supports_tools=false for all 3 LLMs, router passes no tools) — |
| ANALYTIC-EN-MEASURE-UNIT | analytical | OPEN | low | Locale bug: EN seed disables measure-unit carve-out (measure_unit_re='') → English 'how ma |
| MULTIDOC-NO-CROSS-DOC-JOIN | multi-doc | OPEN | high | #8 cross-sheet/cross-doc reconcile NOT implemented — entities stay flat per-chunk list; no |
| MULTIDOC-B-FRAG | multi-doc | OPEN | high | B-FRAG: one physical product = 2 rows across docs (price-doc col_4=98; other-doc col_4=26  |
| MULTIDOC-INCONSISTENT-KEYS | multi-doc | OPEN | medium | Inconsistent entity keys across docs (spec vs name vs code) prevent reconciliation — no ca |
| DQ-REINGEST-PURGE-BUG | data-quality | DIAGNOSED | high | Re-sync does not purge prior chunks → duplication (xe held 819=335 old+484 new under same  |
| DQ-STALE-INGEST-BLOCKS-RETEST | data-quality | DIAGNOSED | high | Purge bug BLOCKS clean re-ingest → col_N runtime lift + product-code lookup lift cannot be |
| DQ-NOISY-COLUMN-NAMES | data-quality | OPEN | medium | Noisy/ambiguous column names (blank, date1/date2, hình ảnh1/ẢNH 1/Ảnh 3, STT residual) bec |
| ROB-SILENT-COL-N-FALLBACK | robustness | OPEN | medium | col_N is a silent positional fallback (f"col{i+1}" / f"col_{idx}") emitted for any blank/u |
| ROB-HEADERLESS-WARN-LOG-ONLY | robustness | PARTIAL | medium | Headerless/unassigned-column advisory surfaces as logger.warning ONLY (ingest_data_quality |
| ROB-NO-MESSY-FORMAT-TESTS | robustness | OPEN | medium | No dedicated messy-real-format test suite (blank rows, merged cells, empty cols, headerles |
| ROB-OOM-REJECT-NO-SPLIT | robustness | OPEN | low | Large file only hard-REJECTs (size guard) — no map-reduce sub-document split; 224KB→2643 c |
| ROB-CODETOK-SPACE-SPLIT | robustness | OPEN | low | B-CODETOK: code/spec query '155 80 13' space-split tokenizes wrong (regex captures single  |
| ROB-LIST-500-NO-SHRINK | robustness | OPEN | medium | Aggregation prompt bloat (top_k 40 × MQ×3 + count-cap exempt + char-only cap 5500) exceeds |
| ROB-LEGAL-CLAUSE-MISS | robustness | OPEN | medium | Factoid legal clause dropped: cliff gap-cut (0.35/floor 0.05) + rerank min-score 0.30 + sa |
| ROB-CB-4XX-TRIPS-BREAKER | robustness | PARTIAL | medium | Only 429/RateLimitError excluded from breaker; general client 4xx (400/401/403/404/422) st |
| SEM-COLUMN-ROLES-LIMITED | semantic-layer | OPEN | medium | Owner glossary column_roles / structural inference limited to the minimal role set (NAME u |
| SEM-ROLES-VI-FROZENSET | semantic-layer | OPEN | medium | Column-role token sets are hardcoded vi frozensets, not read from language_packs[locale] — |
| SEM-CUSTOM-VOCAB-READ | semantic-layer | PARTIAL | low | Per-bot custom_vocabulary column_roles (ADR-0006 T2 authoritative) — read path exists (cus |
| INFRA-UNCOMMITTED-PHASE1A | infra | PROVEN-NOT-SHIPPED | high | Phase 1a count changes uncommitted in working tree (query_graph.py +33, stats_index_reposi |
| INFRA-RE-INGEST-NEEDED | infra | OPEN | high | Clean 3-bot re-ingest required (after purge fix + role/format fixes) to measure real col_N |
| INFRA-RLS-SUPERUSER-DSN | infra | DIAGNOSED | high | RLS inert in prod: live .env connects as postgres superuser + RAGBOT_ALLOW_SUPERUSER_RUNTI |
| INFRA-RQ1-TSQUERY-SIMPLE | infra | OPEN | medium | BM25 tsquery hardcoded 'simple' regconfig everywhere → blocks non-VN / locale-specific ste |
| INFRA-OBS2-QWEN3-TOKENS | infra | OPEN | low | OBS-2: qwen3 streaming meters 0 completion_tokens (completion_total only from provider usa |
| INFRA-SSRF-WEBHOOK | infra | OPEN | medium | SB-4: webhook dispatcher POSTs render_url() with zero IP/DNS-rebind/private-range guard (S |
| INFRA-PII-VS-SLOT | infra | OPEN | low | SB-5: PII redactor masks query at worker boundary but slot extractor reads raw message — n |
| INFRA-F7-ATTR-GENERIC-REVERTED | infra | OPEN | medium | F7 attribute-generic numeric stats index (every numeric column range-queryable) was built  |
| INFRA-S2A-GOD-NODE | infra | OPEN | low | retrieve.py god-node = 1852 lines, 2 decomposers both wired (decompose + adaptive_decompos |

## Sources mined

### Ekimetrics "Adaptive Chunking" (LREC 2026 official impl) at /var/www/html/ragbot
- UNIFIED PARSER OUTPUT CONTRACT with split_points as first-class output. BaseParser ABC (parsing.py:12-23) forces every format (PDF x3 + Excel) into {pages, full_text, split_points, titles}. split_points = char-offset list of structural block boundaries, computed once at parse time and reused by every downstream metric. This decouples structure-detection (parser's job, format-specific) from chunking (format-agnostic).
- STRUCTURE-AWARE SPLIT-POINT HEURISTICS, form-only, no vocabulary. When emitting split_points the parser refuses cuts that would break structure: heading->body glued (add_split=False when role in TITLE/SECTION_HEADING), footnote->parent glued, adjacent tiny TEXT blocks (<100 tok) glued (parsing.py:434-442). Pure structural form, domain-neutral — matches our multilingual-no-vocab + table-header-detect-structural skills.
- ATOMIC-BLOCK SENTINELS + never-cut contract. Tables/figures/formulas are wrapped in <Table>/<Figure>/<Formula> tags in the markdown AND the LLM-regex prompt explicitly forbids splitting inside them (replicate.py:116-118 'Do not split tables/figures/lists'). The atomic block is emitted as a whole and given its own split_point. Directly reusable for our L2 atomic-block-never-cut rule.
- HEADER-REPEATED TABLE SPLITTING. Oversized tables are split row-wise with the header row RE-ATTACHED to every sub-chunk: AzureDIParser._table_to_markdown (parsing.py:70-101) and DoclingParser._split_table_markdown (parsing.py:926-953, header=lines[0..1] prepended to each row-group), ExcelParser._split_by_rows (parsing.py:527-565) which re-emits the sheet heading + column headers per part. This is our table-handling L2 pattern: big table -> N chunks, each self-describing.
- MULTI-BLOCK -> HEADER PROMOTION for messy tabular (Excel). ExcelParser splits each sheet on blank rows into blocks, promotes the first row of each block to a header, and _clean_headers (parsing.py:506-525) replaces blank/dup headers with col_1..col_N and de-dups by suffixing. Directly relevant to our messy-XLSX/CSV structure recovery and multi-row-header-merge concerns.
- LABEL-FREE INTRINSIC QUALITY METRICS (5) to score chunkings without gold answers (metrics.py). Size Compliance (fraction in [min,max] tokens), Block Integrity (fraction of parser split_point blocks NOT cut by a chunk boundary, compute_block_integrity metrics.py:264-307 with tolerance_chars leeway), Intrachunk Cohesion (sentence-vs-chunk cosine), Contextual Coherence (chunk-vs-window cosine), Missing-Reference (coref chains not broken). These are runnable per-bot ingest as chunk-quality gates — Block Integrity especially needs only split_points + embeddings-free.

### RAG-Anything (HKUDS) v1.3.0 — /var/www/html/ragbot/_external_refs/RAG-Anything
- ONE canonical typed-block IR for every format: all parsers (PDF/office/image/text) emit an ordered `content_list` of `{type, page_idx, ...}` dicts (parser.py:657-681 base contract; utils.separate_content). Adding a format = new adapter emitting the SAME schema. This is exactly OUR 'one structured-markdown output' contract, but expressed as a block LIST rather than a markdown STRING — the block list preserves type + page_idx metadata our flat markdown loses.
- Extension-dispatch + in-process parser REGISTRY with register_parser/get_parser/unregister_parser and a `_CUSTOM_PARSERS` dict, collision-guarded against built-ins (parser.py:2393-2521). Same Port+Registry shape as our `infrastructure/parser/registry.py`, but they also expose runtime registration + `list_parsers()`/`get_supported_parsers()` introspection we could borrow for /health.
- Office->PDF normalization: .doc/.docx/.ppt/.xls converted to PDF via LibreOffice before parsing (parser.py:194-344), so all rich formats funnel through ONE high-fidelity path instead of N bespoke parsers.
- TABLE handling = enhanced-caption + dual-representation: keep the raw `table_body` markdown AND generate an LLM analysis of it (headers, key data points, trends, relationships), then store BOTH in one chunk via `table_chunk` template (prompt.py:336; modalprocessors.TableModalProcessor 1069-1263). `format_table_body` renders list-of-lists into a real markdown table (utils.py:34-58). The table gets its own KG entity so it's retrievable as a unit.
- Context-aware modal processing: before describing a table/image, `ContextExtractor` pulls a page/chunk WINDOW of surrounding text (config: context_window, context_mode=page|chunk, max_context_tokens) and feeds it into the `*_prompt_with_context` template (modalprocessors.py:55-215; prompt.py:184). This binds an isolated block to its narrative — directly analogous to our AdapChunk B3 'bind row to its section/heading'.
- Multi-doc / cross-block LINKAGE via knowledge graph: each modal chunk becomes an entity node; entities the LLM extracts from it are joined to it with weighted `belongs_to`/`part_of` edges (processor.py:1391-1454). Cross-document linkage is emergent — same entity name from different docs merges in the KG. Retrieval mode 'mix' = KG local+global + vector, so linkage is queryable, not just stored.

### tldw_server (_external_refs/tldw_server) — FastAPI research/media platform; RAG 
- Borrow #1 (structure recovery): adopt structure_aware.py's extract-in-priority-order + gap-fill-as-paragraph + char-offset model as the block-list contract for our Kreuzberg parser rewrite — it guarantees lossless coverage (every source span is in exactly one typed block) which is our block-integrity invariant.
- Borrow #2 (atomic tables): keep_intact for TABLE/CODE_BLOCK at grouping time — a table is one element, never split.
- Borrow #3 (breadcrumb re-attach): `_build_contextual_header` global-header-stack breadcrumbs (folder>title>H1>H2) prepended per chunk — fixes heading-only-chunk retrieval and gives table chunks their section context.
- Borrow #4 (table→multi-representation): TableSerializer per-row entity_blocks + sentences + combined search_text so individual table CELLS/ROWS become embeddable and BM25-findable (our top table-question gap).
- Borrow #5 (ragged/headerless normalization): pad-to-max-cols for ragged rows + union-of-keys headers for irregular JSON/CSV — cheap robustness we can add to our tabular_markdown normalizer.
- Borrow #6 (intent→retrieval routing): rule-based COMPARATIVE→group_by_source/multi_doc and ANALYTICAL→wider top_k, plus granularity_router factoid/broad/specific → passage/doc/chunk, all LLM-free — mirror per-bot in pipeline_config.

### Multi-source web research (2024-2026) — 5 areas: table-RAG/TAG, messy-table stru
- Table structure recovery via geometry-only (TATR/TableFormer): detect header rows, cell spans, multi-level header hierarchy on both axes with NO vocabulary; GriTS as label-free grid-recovery metric.
- Separate SCHEMA-index (col name+dtype+example values) and CELL-index (distinct column-value pairs, frequency-truncated to token budget B) per table — encoding O(min(D,B)) not O(N*M); query-expand into column-queries + cell-queries (TableRAG NeurIPS).
- SQL/pandas-as-interface for aggregation/filter/compare (TableRAG-EMNLP + TAG): execute deterministic code over recovered table, LLM only narrates computed number — kills numeric HALLU without violating no-override rule.
- Iterative heterogeneous loop: query decomposition -> text retrieval (prose) -> SQL execution (tables) -> compositional answer (TableRAG-EMNLP); TAG's synthesis->execution->generation covers the >80% of queries pure-Text2SQL/pure-RAG miss.
- Table linearization: default Markdown tables (better accuracy, LLM-native), HTML fallback only for merged-cell/multi-level-header tables Markdown cannot express; CSV most token-efficient but lower accuracy.
- Contextual Retrieval: 50-100 token LLM-generated per-chunk context prepended before BOTH embedding and BM25; 67% failure reduction with reranking (150->20); prompt-caching ~$1.02/M doc-tokens.


---

All evidence verified against real code (`__init__.py:911` even has a comment documenting the exact purge bug: "`deleted_at IS NULL` filter makes `is_reindex=False`"). I now have grounded confirmation for every load-bearing claim. Writing the completion strategy.

---

# RAGBOT COMPLETION ROADMAP — THE Strategy

Principal-architect synthesis. Every row is grounded in verified `file:line` (I re-checked the load-bearing ones this session) and mapped to a mined solution with source. Stance is **EVOLVE-not-rewrite** (strangler-fig): the frame is already expert (Hexagonal, Port+Registry+DI, `rows_to_structured_markdown` state machine already hardened) — the problem is **unconnected wires**, not a wrong frame.

## VERIFIED THIS SESSION (evidence anchors)
- `rows_to_structured_markdown` callers = **ONLY** `excel_openpyxl_parser.py:82` + `google_sheets_parser.py:127`. **DOCX (`docx_parser.py:110-119`) and Kreuzberg parser do NOT call it** → L1-DOCX-BYPASS + L1-KREUZBERG-NO-STRUCT confirmed real.
- `stats_index_repository.py`: `SUM(|AVG(|GROUP BY|COUNT(DISTINCT)` = **0 hits** → no-SUM/AVG/GROUP-BY confirmed.
- `document_stats.py:502-506` `_roles_def` = exactly `(name, category, aliases, price)` → quantity/date NOT roles confirmed.
- `git log -S count_by_name_keyword` = **empty**; `git diff --stat HEAD` = `query_graph.py +33`, `stats_index_repository.py +60`, `query_range_parser.py +6`, `?? test_count_operation_dispatch.py` untracked → INFRA-UNCOMMITTED-PHASE1A confirmed, at risk of loss.
- `document_service/__init__.py:911` — a **code comment** already documents the purge bug: "`deleted_at IS NULL` filter makes `is_reindex=False` → the store [skips purge]" → DQ-REINGEST-PURGE-BUG confirmed self-documented.
- `tabular_markdown.py` already has: multi-row header merge (`_is_header_continuation:102`, `_merge_header_fill:124`), pure-money gate (`_is_pure_money:64`), section-in-header split (`:220-232`), lookahead long-title (`_precedes_table:169`). But **forward-fill / skip-blank-with-gap-K = 0 hits** in it → the two PROVEN L1 fixes are NOT in the shared converter yet.
- `engine.py:69-79` superuser RLS escape confirmed.

---

## 1. PROBLEM → SOLUTION TABLE

Source keys: **AC**=Adaptive-Chunking (Ekimetrics/LREC), **RA**=RAG-Anything, **TLDW**=tldw_server, **TRN**=TableRAG-NeurIPS `2410.04739`, **TRE**=TableRAG-EMNLP `2506.10380`, **TAG**=`2408.14717`, **DOC**=Docling/TableFormer `2408.09869`, **TATR**=Table-Transformer, **ACR**=Anthropic Contextual Retrieval, **GR**=GraphRAG `2404.16130`, **HIPPO**=HippoRAG `2405.14831`.

### L1 — Structure recovery (the brittleness; fix FIRST)

| Problem | Solution (WHAT) | Source | WHY (solves) | TRADE-OFFS | EVOLVE note |
|---|---|---|---|---|---|
| **L1-DOCX-BYPASS** `docx_parser.py:110-119` | Route `table.rows` cell-matrix through `rows_to_structured_markdown(rows)` instead of hardcoding `rows[0]=header`. DOCX already gives clean cell text → just feed the matrix. | TLDW Borrow#1 (one block contract), our own converter | Inherits multi-row-header merge, section-in-header, pure-money gate the converter ALREADY has (`tabular_markdown.py:102,124,220`). Zero new logic. | DOCX cells never carry the openpyxl blank-run signal; converter's blank-row logic is a no-op but harmless. | Pure wire — 3-line diff, converter untouched. |
| **L1-KREUZBERG-NO-STRUCT** | Reconstruct a typed-block list from Kreuzberg flat markdown by **form only** (heading regex, `<Table>` fences, blank-line runs), then route table blocks through the converter. | AC `PyMuPDFParser` (`parsing.py:1054-1069` heading-regex-on-markdown) + TLDW `structure_aware.py:359-452` (extract-in-priority + gap-fill-as-paragraph, lossless) | Kreuzberg throws the separator away and re-judges by vocab → 2-stage drift → col_N. Recovering blocks from **markdown FORM** (no vocab) fixes it at the parser, upstream of the drift. | Kreuzberg output flatter than Azure/Docling → recovered `split_points` coarser than AC's rich path. Acceptable — still beats vocab re-judge. | Local rewrite of ONE adapter (sanctioned: "REWRITE cục bộ chỉ parser adapter"). Emit block list, converter unchanged. |
| **L1-XLSX-SHEET-ONE-BLOB** | Emit the sheet as the converter's structured markdown but tag table-region blocks so the downstream **dual-read / row-as-chunk** path fires even with `##` headings present. | TRN schema+cell index (`2410.04739`); TLDW `table_serialization.py:305` per-row entity_blocks | A 40k-token flattened sheet trips Lost-in-the-Middle (TRN: "100×200 table >40,000 tokens"). Row-as-chunk keeps each row self-describing + retrievable. | Fast-path currently gated `headings==0` (`analyze.py:454`) — must relax the gate, not just the parser (see L2-DUAL-READ). | Metadata tag on blocks, no schema change; relaxing gate is config. |
| **L1-COL-N-OOV-HEADER** (PROVEN-not-shipped) | Ship the proven header-merge converter fix; **blocked by re-sync purge bug** (DQ-REINGEST-PURGE-BUG) — fix purge first, then clean re-ingest measures runtime lift. | our converter (`tabular_markdown.py:102-134`) + DQ fix | Offline-proven multi-bot; runtime lift unmeasurable until stale chunks purge. | None on the fix; gated on infra. | Already in converter; the blocker is DQ, not L1. |
| **L1-MERGED-CELL-ROWSPAN** | **Forward-fill** sparse/empty category cells from the row above (rowspan recovery); add pure-shape gate so col0 category-stub isn't stolen as entity name. | DOC/TATR ("handles cell spans and hierarchy … partial or no borderlines, empty cells") | Merged-cell group-empty rows silently drop items; col0 stub becomes wrong entity name. Forward-fill is the SOTA rowspan recovery, domain-neutral. | Forward-fill can over-propagate on a genuinely-empty column — gate on "prev row had value AND this row's other cells are populated". | Add to converter (`tabular_markdown.py`); **PROVEN this session** per KEY CONTEXT. |
| **L1-NAME-WITH-MONEY-DROP** | Pure-money gate already exists (`_is_pure_money:64` strips money skeleton, any residual LETTER ⇒ name). Confirm it's wired on the DATA-row name-pick path in `document_stats.py:288`. | our converter (already shipped) | "Gói 6 triệu" (name) must not parse as price 6M. Shape-only residue-letter test. | None — already domain-neutral. | Already present in `tabular_markdown`; verify `document_stats.py` uses same gate (avoid drift). |
| **L1-TRANSPOSED-KV-PIVOT** (low) | Orientation detect: if col0 values are label-shaped and row0 across is value-shaped → transpose before converter. DEFER behind ADR. | DOC (geometry-only orientation) | Transposed/pivot tables produce garbage entities. | Needs ADR (hard-to-reverse heuristic); low frequency. | New optional pre-pass; gate per-bot. |
| **L1-SECTION-IN-HEADER / LONGTITLE** (low) | Already fixed: `_precedes_table` lookahead (`:169`) + section-in-header split (`:220-232`) remove the hard `len<=8` cap. | our converter (shipped) | Long section titles + section-in-header rows no longer lose the boundary. | None. | Present — add regression test to messy golden set. |
| **L1-EMPIRICAL-FORMATS-BREAK** (12/27 BUG) | The 15-case messy-format golden test (see §6 verify-gate) becomes the acceptance oracle; each L1 fix must flip a FAIL→PASS case. | AC `check_chunk_gaps` lossless invariant (`postprocessing.py:66`) + TLDW test template | Turns "12/27 break" from a claim into a measurable gate. | Building the golden corpus is upfront work. | Pure test asset, no prod code. |

### L2 — Chunking / embedding

| Problem | Solution | Source | WHY | TRADE-OFFS | EVOLVE note |
|---|---|---|---|---|---|
| **L2-ALIAS-FLOOD-EMBED** (chunk[142]=5311 chars aliases) | Move aliases → **metadata** (BM25-searchable payload) out of embedded content; embed name+price+category only. | TRN cell-index (distinct col-value pairs, freq-truncated to budget B) | Aliases drown product signal in the vector; as metadata they stay findable via BM25 without diluting cosine. | Alias-only queries rely on BM25 half of hybrid — acceptable (they're lexical anyway). | `document_stats.py:195-203` render change; metadata already carried. |
| **L2-ROW-MIXING** (780→751 binding) | **Atomic row/table block never cut**; one row = one chunk in dual-read, no char-budget packing across rows. | AC atomic-block sentinel + never-cut (`replicate.py:116`); TLDW `keep_intact` (`structure_aware.py:596`) | Char-budget packing (`strategies.py:142`) binds adjacent row's price/stock. Atomic block kills the cross-row leak. | Many tiny chunks ⇒ more embeddings/cost; bound with min-merge of TINY adjacent text only. | Chunker respects block boundary flag; AC split-then-merge `small_only` mode. |
| **L2-LINEARIZE-NO-COL-LABEL** (`col_4:214`) | Render synthetic row-linearized chunk **with column labels** (`quantity: 214 | price: 26,000,000`) using recovered roles. | TRN schema index (col name + dtype + example values) | Unlabeled `col_4:214` ⇒ LLM guesses quantity vs stock ⇒ Group-B HALLU. Labels remove the guess. | Requires roles resolved (ties to B-ROLE). | `document_stats.py` render; needs role expansion (§5). |
| **L2-TABLE-ROW-NO-BREADCRUMB** | Prepend `# Doc > ## Section` breadcrumb to table-row chunks (U5 currently SKIPS them). | TLDW `_build_contextual_header` global-header-stack (`structure_aware.py:711`); ACR per-chunk context | Section-scoped retrieval (e.g. "warranty") misses table rows with no section context. | Breadcrumb adds tokens per row-chunk; cheap. | `ingest_stages_enrich.py:190` remove `should_skip_row_enrich` for breadcrumb-only path. |
| **L2-DUAL-READ-CSV-DISQUAL** | Relax fast-path gate: allow row-as-chunk when block-type=TABLE even if `headings>0`; add `original_content` dual-store. | AC candidate-scoring (don't disqualify a-priori); RA `insert_content_list` block bypass | Gate `headings==0` (`analyze.py:454`) disqualifies any sheet with a `##` section → the exact XLSX case. | Relaxing gate risks non-table md hitting row-path — key on block TYPE not heading count. | `ingest_stages.py:749` whitelist → block-type predicate. |

### Analytical engine

| Problem | Solution | Source | WHY | TRADE-OFFS | EVOLVE note |
|---|---|---|---|---|---|
| **ANALYTIC-COUNT-DISPATCH / INFRA-UNCOMMITTED-PHASE1A** | **COMMIT the working-tree count-path NOW** (+33/+60/+6 + untracked test) on a branch, then A/B live-verify the original "1.020.000" repro. | — (hygiene) | Uncommitted = at risk of loss; unverified = can't claim shipped (rule #0). | Must re-run the live repro, not just unit-green. | Commit + verify gate; no design change. |
| **ANALYTIC-COUNT-DEAD-METHOD** | Wire `count_by_price_range` into the operation dispatch (currently only its own test calls it). | TAG execution step | Real `COUNT(*)` exists but never dispatched = dead. | None. | 1 dispatch branch. |
| **ANALYTIC-SILENT-UNDERCOUNT** (cap=100 < 257 real) | **SQL COUNT(*) for count-intent** (unbounded); for enumerate, return "N of M (capped)" honesty string. | TAG/TRE (SQL as interface — compute exact, don't eyeball) | LIMIT-cap 100 silently undercounts a 257-set ⇒ fabrication. COUNT(*) is exact; capped-honesty avoids silent lie. | Cap-honesty text is app-generated **metadata about retrieval**, NOT an answer override — sacred-rule#10 safe (it's a source fact, LLM narrates). | `stats_index_repository` add COUNT(*); constant `DEFAULT_STATS_INDEX_LIMIT` stays for enumerate. |
| **ANALYTIC-NO-SUM-AVG** (0 hits) | Add `SUM()`/`AVG()` SQL over the numeric stats index; parser signal `operation=sum/avg`; LLM narrates the computed scalar. | TRE/TAG (deterministic SQL kills numeric HALLU) | No sum/avg path anywhere. SQL compute + LLM-narrate = fabricate-class eliminated, sacred-rule#10 compliant. | Needs numeric index generic (ties to F7 revert). | Additive SQL + parser signal; **re-land F7 attribute-generic** (was reverted `9416f4d`). |
| **ANALYTIC-NO-GROUPBY-SERIES** ("5 series" returns 117 rows) | `GROUP BY` + `COUNT(DISTINCT)` over a recurring-token/series key. | TRE compositional; TAG synthesis→execution | "bao nhiêu loại Landspider" = group-by, not row count. Currently 0 path. | Series-key extraction is heuristic; gate per-bot glossary. | Additive SQL; series-key from column_roles (§5). |
| **ANALYTIC-SUMMARY-ORPHAN** (write-only) | Wire a read-site: on global/thematic-summary intent, read `summary_json`; or DROP the write if no read planned. | GR community-summary (global-search precedent) | `summary_json` computed at ingest, 0 read sites ⇒ pure waste OR a latent global-summary feature. | Deciding read-vs-drop needs a product call. | Either wire `matches_summary_pattern` (0 callers today) into a global-intent branch, or delete. |
| **ANALYTIC-B-ROLE-QTY-DATE** (`_roles_def` 4 only) | Add `quantity`/`date`/`stock` roles → labeled render (fixes L2-LINEARIZE). | TRN schema (dtype+role per column) | Same physical row answers 26 vs 214 because col unlabeled. Roles = labels. | ADR-0006 says "NAME = only universal role, CẤM per-domain" → must add as **owner-glossary opt-in**, not hardcoded default. | Extend via `custom_vocabulary.column_roles` 3-tier (SEM-*), not core frozenset. |
| **ANALYTIC-TWO-CLASSIFIERS** | Add a **local-vs-global router**: intent=`aggregation`/`comparative` → force stats/SQL path (currently intent is "a hint not required"). | TLDW `query_features` intent→routing (`:874`); TAG query-classify | Cosmetic intent has zero effect on whether stats fires; the two classifiers never cross-validate. | Rule-based router must stay LLM-free (T2 cost). | `retrieve.py:193-201` promote intent from hint to router key, LLM-free. |
| **ANALYTIC-NO-FUNCTION-CALLING** (low) | DEFER. Keep SQL-over-stats-index as the compute path; revisit function-calling only for ad-hoc tail. | TAG (SQL covers >80%) | SQL path covers the aggregation set; function-calling is marginal tail. | `supports_tools=false` all 3 LLMs. | No action; documented defer. |
| **ANALYTIC-EN-MEASURE-UNIT** (low) | Seed EN `measure_unit_re` (currently `''`) in language pack. | multilingual-no-vocab skill | Empty regex ⇒ EN "how many days" mis-routes to catalog count. | Locale seed via alembic (no psql hotfix). | `i18n.py:353` DB seed. |

### Multi-doc

| Problem | Solution | Source | WHY | TRADE-OFFS | EVOLVE note |
|---|---|---|---|---|---|
| **MULTIDOC-NO-CROSS-DOC-JOIN** | **Normalized shape-key** `(record_bot_id, workspace_id, lower(name)\|spec)` + **query-time reconcile** across sheets/docs sharing the key. | RA KG entity-merge across docs (`processor.py:1391` belongs_to); HIPPO entity resolution; TLDW `parent_retrieval` group_by_source | Entities stay flat per-chunk; no join. Shape-key is the entity-resolution enabler HIPPO/GR require. | Normalization heuristic (name vs spec vs code) can false-merge — gate on exact-key first, fuzzy behind flag. | Add reconcile at query-time over existing stats index; no KG rewrite. Optional graph index later (GR/HIPPO) via ADR. |
| **MULTIDOC-B-FRAG** (Davanti 26 vs 98) | Dedup by shape-key **across docs**, prefer non-NULL-price fragment; merge fragments. | RA entity-merge; TRE cross-source compositional | Per-doc dedup (`ingest_stages_final.py:139`) picks wrong fragment (26 not 98). Cross-doc merge picks the priced one. | Merge policy (which price wins) needs a rule — "non-null, latest doc". | Extend `_dedup_stats_entities` key to cross-doc shape-key. |
| **MULTIDOC-INCONSISTENT-KEYS** | Canonical shape-key + alembic unique index. | HIPPO entity resolution | Inconsistent keys block reconcile. | Schema migration (backward-compat null→default). | alembic additive index. |

### Robustness / infra / semantic (grouped)

| Problem | Solution | Source | WHY | EVOLVE note |
|---|---|---|---|---|
| **DQ-REINGEST-PURGE-BUG** (`__init__.py:911`) | Fix `is_reindex` detection: dedup SELECT must NOT filter `deleted_at IS NULL` (matches `uq_doc_tool` which ignores it) → purge fires on re-sync. | canonical-ingest-flow skill (safe-replace) | Self-documented bug; blocks ALL runtime L1 verification. **HIGHEST leverage.** | Fix the SELECT predicate; one-line + test. |
| **DQ-STALE-BLOCKS-RETEST** | Resolved by purge fix; then clean 3-bot re-ingest. | — | Unblocks col_N/quantity/lookup lift measurement. | Sequenced after purge. |
| **DQ-NOISY-COLUMN-NAMES** (date1/date2/STT) | col_N robust-header + date-role token (shape: `dd/mm/yyyy` pattern) → labeled. | DOC geometry; multilingual-no-vocab | date1/date2 ambiguity (SX vs "ngày về") resolved by role. | Ties to B-ROLE glossary. |
| **ROB-SILENT-COL-N-FALLBACK** | Keep `col_N` as fallback BUT **fail-loud**: surface unassigned columns in ingest result DTO. | AC `_clean_headers` (col_1..N is expected, not silent) | Silent semantic loss → owner never knows. | Ties to ROB-HEADERLESS-WARN. |
| **ROB-HEADERLESS-WARN-LOG-ONLY** | Surface `ingest_data_quality` advisory into ingest **result DTO / document metadata** (not just `logger.warning`). | RA CallbackManager lifecycle events; canonical-ingest soft-failure sentinel | Owner can't see why bot can't answer. Fail-loud-not-silent. | `ingest_stages_final.py:504` → return in DTO. |
| **ROB-NO-MESSY-FORMAT-TESTS** | Build the 15-case messy golden suite (blank rows, merged cells, empty cols, headerless, multi-row header) on REAL breaking docs. | AC lossless-invariant; TLDW `test_thai_tables_spans` template | Only synthetic taxonomy exists; real formats untested. | Pure test asset. |
| **ROB-OOM-REJECT-NO-SPLIT** (low) | Map-reduce sub-document split above size guard instead of hard-reject. | AC oversize-split (`postprocessing.py`) | 224KB→2643 chunks OOM. | Additive split path. |
| **ROB-CODETOK-SPACE-SPLIT** (low) | Capture ≥2-token spec ("155 80 13") as one code query. | — | Spec tokenization wrong. | `query_range_parser.py:448` regex. |
| **ROB-LIST-500-NO-SHRINK** | Prompt-shrink-retry / graceful degrade on provider context-length 500. | RA robust degrade; graceful-degradation pattern | Provider 500 propagates unhandled. | `router.py:652` context guard + retry. |
| **ROB-LEGAL-CLAUSE-MISS** | Add ACR contextual prefix (50-100 tok) so low-rerank relevant clause survives. | ACR (67% failure reduction) | Cliff-cut + rerank-min drops relevant clause → refuse. | ACR prepend at ingest; prompt-cached. |
| **ROB-CB-4XX-TRIPS-BREAKER** | Exclude all client 4xx (400/401/403/404/422) from `record_failure`, not just 429. | graceful-degradation (client-bug fail-loud, don't trip shared breaker) | One misconfigured bot OPENs a healthy shared provider. | `dynamic_litellm_router.py:150` widen exclusion set. |
| **SEM-COLUMN-ROLES-LIMITED / SEM-ROLES-VI-FROZENSET / SEM-CUSTOM-VOCAB-READ** | 3-tier role cascade: **structural NAME-infer** (universal) → **DB-seeded `column_role_tokens[locale]`** → **per-bot `custom_vocabulary.column_roles`** (authoritative). Read frozensets from `language_packs`, not hardcoded vi. | multilingual-no-vocab skill; TRN schema-role | EN "Item"/"Price" + vi synonyms silently drop at column level; roles frozen to vi + 4 types. | Ships quantity/date/stock as glossary opt-in (respects ADR-0006). |
| **INFRA-RLS-SUPERUSER-DSN** (`engine.py:69`) | **Ops**: switch live DSN to `ragbot_app` (non-superuser); RLS then enforces. Code already warns. | multi-tenant RLS sacred | RLS inert → tenant isolation not enforced at DB. | Ops `.env` change, not code. |
| **INFRA-RQ1-TSQUERY-SIMPLE** | tsquery regconfig from locale (`language_packs`), not hardcoded `'simple'`. | multilingual-no-vocab | Blocks non-VN stemming. | `pg_bm25_retrieval.py:101` param from locale. |
| **INFRA-OBS2-QWEN3-TOKENS** (low) | Fall back to `tokens_yielded` proxy when provider usage payload has no completion_total. | RA observability | qwen3 cost undercounted. | `router.py:916`. |
| **INFRA-SSRF-WEBHOOK** | Private-range / DNS-rebind guard before webhook POST. | security | SSRF risk. | `webhook_dispatcher.py:163` ipaddress guard. |
| **INFRA-PII-VS-SLOT** (low) | Shared unredacted-original reconciliation for slot extractor. | PII-at-boundary pattern | Slot reads raw; redactor masks query. | `generate.py:250`. |
| **INFRA-F7-ATTR-GENERIC-REVERTED** | **Re-land** attribute-generic numeric stats index (was `9416f4d` revert) — prerequisite for SUM/AVG on non-price columns. | TRN schema (every numeric column queryable) | aggregate_summary price-only. | Re-land additively with the messy-golden gate this time. |
| **INFRA-S2A-GOD-NODE** (low) | DEFER (Phase-2 architectural). Do NOT remove `condense_question` (LIVE). | — | 1852-line node, 2 decomposers. | Documented defer. |

---

## 2. L1 STRUCTURE-RECOVERY HARDENING PLAN (domain-neutral, no guessing)

**Locus:** `src/ragbot/shared/tabular_markdown.py` (the shared converter) — so **every** parser that routes through it inherits the fix. Then wire the two parsers that bypass it.

**Order (each domain-neutral, shape-only):**

1. **Skip-blank-rows + gap-threshold-K trim** (PROVEN). In `rows_to_structured_markdown`, drop fully-empty rows but **count consecutive blanks**; a run ≥ K (config `DEFAULT_TABLE_BLANK_GAP_K`) is a real table separator (`close_table()`), a run < K is noise to skip. *Solves blank-row breakage (6/15 formats).* Source: AC `check_chunk_gaps` invariant guarantees no span is silently lost.
2. **Forward-fill sparse/merged columns** (PROVEN). When a row's leading category cell is empty AND the row above had a value AND the row's other cells are populated → fill from above (rowspan recovery). Gate on the pure-shape condition so a genuinely-empty column doesn't over-propagate. *Solves merged-cell category loss + col0-stub-stolen-as-name.* Source: DOC/TATR "handles cell spans … empty cells".
3. **Trim-empty-cols**. Drop columns empty across ALL data rows before header binding (kills `date1|date2|hình ảnh1` residual columns). Shape-only. *Solves DQ-NOISY-COLUMN-NAMES + col_N residue.*
4. **Robust header detect** (already largely present — `_looks_header:90`, `_is_header_continuation:102`, `_merge_header_fill:124`). Add: date-shaped-cell → `date` role token (pattern `dd/mm/yyyy`, no vocab). Keep `col_N` ONLY as last-resort fallback.
5. **Fail-loud + owner-glossary**. Any column that lands `col_N` → record in an `unassigned_columns` list returned in the ingest result DTO (not just `logger.warning` at `ingest_stages_final.py:504`). Owner sees it and can supply `custom_vocabulary.column_roles`.

**Wiring (the two bypasses):**
- `docx_parser.py:110-119` → replace hand-rolled `rows[0]=header` with `rows_to_structured_markdown([[c.text for c in row.cells] for row in table.rows])`.
- Kreuzberg parser → reconstruct block list from markdown form (AC `PyMuPDFParser` heading-regex-on-markdown + TLDW extract-in-priority + gap-fill), route `<Table>` blocks through the converter.

**No-guess gate:** each of steps 1-5 must flip ≥1 FAIL→PASS in the 15-case messy golden test (§6) before it's claimed shipped.

---

## 3. L2 ADAPCHUNK COMPLETION

AdapChunk assumes clean Mistral-OCR markdown; L1 above now feeds it clean blocks. Remaining L2 wires:

1. **col_N labeled linearize** — synthetic row chunk renders `role: value` pairs using resolved roles (`quantity: 214 | price: 26,000,000`), never bare `col_4:214`. (Ties to §5 roles.) Source: TRN schema index.
2. **Atomic table/row block never cut** — chunker respects a block-boundary flag; one row = one chunk in dual-read; only TINY adjacent text merges (AC `small_only` mode). Kills L2-ROW-MIXING. Source: AC atomic sentinel + `check_chunk_gaps` assertion after every op (adopt the hard `assert` — block-integrity-quality-gate skill).
3. **Context-bind (breadcrumb)** — `# Doc > ## Section` prepended to every row chunk (AC `get_title_info` heading re-attach / TLDW `_build_contextual_header`); remove `should_skip_row_enrich` breadcrumb skip at `ingest_stages_enrich.py:190`. Optionally the ACR 50-100-token contextual blurb, prompt-cached, per-bot opt-in.
4. **original_content dual-read** — store the raw row markdown alongside the linearized/enriched form (RA `insert_content_list` dual-representation) so retrieval can serve either; relax the `headings==0` fast-path gate (`analyze.py:454`) to a **block-type=TABLE** predicate.
5. **Hard invariant** — `assert check_chunk_gaps(chunks, source)` after every chunker + merge (AC `postprocessing.py:66`, `split_documents.py:113`). Observe-only Block-Integrity + Size-Compliance metrics as structlog ingest events (no new infra — `feedback_no_premature_observability`).

---

## 4. CROSS-DOC LINKAGE DESIGN

**Normalized shape-key** (4-key-scoped, domain-neutral):
```
shape_key = (record_bot_id, workspace_id, lower(normalize(name | spec_or_code)))
```
- **Ingest**: compute `shape_key` per stats entity; alembic additive unique index (backward-compat: null workspace → default slug).
- **Query-time reconcile**: on a product lookup, `GROUP BY shape_key` across ALL docs of the bot; merge fragments preferring **non-NULL** attribute values (fixes B-FRAG: Davanti 98 not 26). Source: RA entity-merge-across-docs (`processor.py:1391`), HIPPO entity resolution, TLDW `group_by_source`.
- **Match ladder**: exact `shape_key` first (safe); fuzzy/spec-normalized behind a per-bot flag (false-merge risk gated).
- **EVOLVE**: this is a reconcile over the **existing stats index** — no KG rewrite. A full entity graph (GR communities / HIPPO PPR) for corpus-level multi-hop is a **future ADR**, additive alongside pgvector, not a replacement.

**Sacred:** reconcile is scoped by `record_bot_id` + `workspace_id` (never cross-tenant); merge is a **retrieval fact** the LLM narrates, not an app-override.

---

## 5. ANALYTICAL ENGINE (count / group-by / sum done right)

**Principle (TAG/TRE):** aggregation = **deterministic SQL over the recovered structured index**, LLM only narrates the computed number → kills fabricate/misinterpret HALLU classes while staying sacred-rule#10 compliant (no app-override; the number is a *source fact*).

1. **Commit + verify Phase-1a count** (INFRA-UNCOMMITTED) — commit the +33/+60/+6 + untracked test; re-run the live "1.020.000" repro (A/B). *No claim of "shipped" until the live repro passes* (rule #0).
2. **COUNT(*) exact** — count-intent → unbounded `COUNT(*)`, not LIMIT-capped list length. Wire the dead `count_by_price_range`. For enumerate, return `"N of M (capped)"` honesty metadata.
3. **SUM / AVG** — add SQL aggregates over the **re-landed attribute-generic numeric index** (INFRA-F7 re-land); parser signal `operation=sum|avg`; LLM narrates scalar.
4. **GROUP BY / COUNT(DISTINCT)** — series/recurring-token key from `column_roles`; "bao nhiêu loại X" → grouped count (5), not raw rows (117).
5. **Roles** — extend `_roles_def` (`document_stats.py:502`) with `quantity/date/stock` **via owner-glossary opt-in** (3-tier cascade §SEM), NOT hardcoded default (respects ADR-0006 "NAME = only universal role").
6. **local-vs-global router** — promote intent from hint to router key (`retrieve.py:193`): `aggregation`/`comparative` intent → force stats/SQL path. LLM-free (TLDW `query_features`).
7. **summary_json** — wire a global-summary read-site (GR precedent) OR delete the orphan write. Product call required.

---

## 6. PHASED ROADMAP (ordered by leverage — fix brittleness FIRST)

Every phase: **TDD** (failing test first) → **15-case messy golden test** (§ROB-NO-MESSY-FORMAT-TESTS) → **A/B on 3 demo bots** (rag-loadtest, Coverage + Faithfulness + HALLU) before "shipped".

### Phase 0 — UNBLOCK (highest leverage, ~1 day)
- **P0.1** Fix `DQ-REINGEST-PURGE-BUG` (`__init__.py:911` — drop `deleted_at IS NULL` from dedup SELECT so `is_reindex=True`). *Blocks everything downstream.*
- **P0.2** Commit `INFRA-UNCOMMITTED-PHASE1A` count-path (at-risk-of-loss).
- **Gate:** clean 3-bot re-ingest produces **zero duplicate chunks** (verify chunk count per doc_id); count-path live repro `1.020.000` passes A/B.

### Phase 1 — L1 BRITTLENESS (fix the 6/15 breaking formats)
- Build the **15-case messy golden test** (real breaking docs) — the acceptance oracle.
- Ship converter: skip-blank+gap-K, forward-fill, trim-empty-cols, date-role, fail-loud DTO (§2).
- Wire `docx_parser` + Kreuzberg through the converter (§2 wiring).
- **Gate:** ≥12/15 messy cases PASS (from 3/15); col_N residual = 0 on live xe re-ingest; Coverage lift measured, HALLU=0 held.

### Phase 2 — L2 ADAPCHUNK COMPLETION
- Atomic row block, labeled linearize, breadcrumb, original_content dual-read, `check_chunk_gaps` hard assert (§3).
- Relax fast-path gate to block-type predicate.
- **Gate:** row-mixing binding bug (Davanti-adjacent) gone; block-integrity metric ≥ threshold; A/B Coverage lift on table-question set.

### Phase 3 — ANALYTICAL ENGINE
- Re-land F7 numeric index; SUM/AVG/GROUP-BY/COUNT(DISTINCT); COUNT(*) exact + capped-honesty; local-vs-global router; roles via glossary (§5).
- **Gate:** B-SERIES "5 loại", B-TRUNC "257 not 100", SUM/AVG cases PASS live; HALLU=0 on numeric traps.

### Phase 4 — CROSS-DOC LINKAGE
- Shape-key + alembic unique index + query-time reconcile (§4).
- **Gate:** B-FRAG (Davanti 98 not 26) PASS; no cross-tenant leak (RLS test).

### Phase 5 — ROBUSTNESS + SECURITY hardening
- CB-4xx exclusion, ROB-LIST-500 shrink-retry, ROB-LEGAL-CLAUSE ACR prefix, SSRF guard, tsquery locale, EN measure-unit seed, obs qwen3 tokens.
- **Ops parallel:** switch DSN to `ragbot_app` (RLS enforcement — INFRA-RLS).
- **Gate:** legal-clause factoid recovered; 500 degrades gracefully; RLS enforced (superuser fallback WARN gone).

### Phase 6 — DEFER (ADR-gated)
- Transposed/pivot orientation-detect; function-calling tail; god-node split; full entity-graph (GR/HIPPO) for corpus multi-hop.

---

## 7. SACRED COMPLIANCE (per this strategy)

- **Shape-based / domain-neutral** — every L1 heuristic (skip-blank, forward-fill, trim-cols, header-detect, date-role) is FORM-only, no vocab, no brand/industry literal. Roles beyond NAME are owner-glossary opt-in (ADR-0006 honored).
- **Multi-tenant RLS** — shape-key + reconcile scoped `(record_bot_id, workspace_id)`; INFRA-RLS ops fix enforces at DB. 4-key identity preserved.
- **No app-override (rule #10)** — SQL aggregates and cross-doc reconcile produce **source facts**; the LLM narrates them. Capped-honesty and numeric-fidelity are **observability/metadata**, never answer-replacement (TLDW `check_numeric_fidelity` used as SIGNAL only, not override).
- **Fail-loud-not-silent** — col_N and unassigned columns surface in ingest DTO; client 4xx fail-loud without tripping shared breaker; transport errors degrade silent (graceful-degradation).
- **No-guess / measure-before-claim (rule #0)** — every phase gated on the 15-case golden test + live A/B (Coverage + Faithfulness + HALLU); nothing claimed "shipped" without runtime numbers. Count-path explicitly must re-run the live repro, not just unit-green.
- **EVOLVE-not-rewrite** — all changes are wires/converters/additive SQL/additive index over the existing frame; the only sanctioned local rewrite is the Kreuzberg parser adapter (emit block list). No frame replacement; pgvector, Port+Registry, 4-key, 9 sacred all preserved.

**Key files touched:** `shared/tabular_markdown.py` · `infrastructure/parser/{docx_parser,kreuzberg_markdown_parser}.py` · `shared/document_stats.py` · `application/services/document_service/{__init__,ingest_core,ingest_stages,ingest_stages_enrich,ingest_stages_final}.py` · `infrastructure/repositories/stats_index_repository.py` · `orchestration/{query_graph,retrieve,generate}.py` · `shared/query_range_parser.py` · `infrastructure/llm/dynamic_litellm_router.py` · `infrastructure/db/engine.py` (ops) · new `tests/unit/test_messy_format_golden.py`.

**Highest-leverage single action:** Phase 0.1 (purge-bug fix at `document_service/__init__.py:911`) — it is self-documented, one-line, and unblocks runtime verification of every L1/analytical fix that is currently "proven offline but not runtime-verified."