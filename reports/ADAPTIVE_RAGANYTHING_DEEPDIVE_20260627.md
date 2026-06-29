# ADAPTIVE-CHUNKING + RAG-ANYTHING — DEEP-DIVE (2026-06-27)

Two external references read end-to-end: **adaptive-chunking** (Ekimetrics, LREC-2026 reproduction code, under `_external_refs/adaptive-chunking/`) and **RAG-Anything** (HKUDS, multimodal ingest on top of LightRAG, under `_external_refs/RAG-Anything/`). All file:line below were grep/sed-verified against the actual source on disk (e.g. `metrics.py:264` `compute_block_integrity`, `utils.py:34` `format_table_body`, `modalprocessors.py:228` `'#'*text_level`).

---

## 0. TL;DR — top lessons for our chunking + parsing

1. **A ground-truth-free chunk-quality gate exists and is portable.** `compute_block_integrity` (`adaptive-chunking/.../metrics.py:264`) scores *fraction of gold blocks NOT cut by a predicted split* — deterministic, no LLM judge, no per-language model. This is the single highest-value steal: a **chunking regression canary** that fits our `measure-before-claim` / Coverage discipline (CLAUDE.md rule#0).
2. **Boundaries come from STRUCTURE, not domain vocabulary.** Both repos decide split points from headings/tables/paragraph-size/page-breaks + a generic separator ladder, never from keywords. This is exactly our domain-neutral mandate, proven in working reference code (`parsing.py:429-442`, `replicate.py:56-71`, `splitters.py:28`).
3. **Lossless-coverage invariant via reconstruction assertion.** After chunking, assert that concatenated chunks reproduce `full_text` (`adaptive-chunking/.../postprocessing.py:66,128`; paper splitters `:291,311,321`). A chunker that silently drops a digit = a fabricated/missing number downstream — directly protects HALLU=0.
4. **One canonical typed-block IR for every format.** RAG-Anything's `content_list` (`processor.py:457-533`, `utils.py:89`) and adaptive-chunking's parser JSON contract (`parsing.py:12-23`) both prove the *exact* "every format → one structured output → chunking" pattern our charter calls for (Kreuzberg flat-text → emit block list).
5. **Heading level is a NUMBER, not a regex match.** `text_level` → `'#'*level` (`modalprocessors.py:228-230`) — language-neutral header reconstruction. Fixes VN/ZH heading loss that keyword-based detection causes.
6. **Tables stay as data, rendered to markdown grids with `| --- |` separators** (`utils.py:34-58`) — this is the **antidote pattern to our `col_N` header bug**: structure is derived from row/column shape, captions kept attached, not from header words.
7. **Universal size regularization** (re-split >max, merge <min after ANY method) is a cheap consistent win (`replicate.py:272-318`) — method-agnostic post-step we can apply uniformly.
8. **Counter-lesson (do NOT copy):** RAG-Anything dispatches format by **file extension only** with a "try as PDF" fallback (`processor.py:457-533`, `parser.py:1415-1421`) and converts office→PDF→OCR (lossy for math). Our mandated `mime→ext→byte-sniff` + native per-format parsers are the more correct contract; keep them.

---

## 1. Adaptive-chunking: the algorithm + Block-Integrity metric (ground-truth-free regression gate)

**The thesis** (`adaptive-chunking/README.md:27-49`, `poster.pdf`): no single chunking method is universally best. So at *indexing time*, run several splitters, score each output with five intrinsic (no-ground-truth) metrics, and pick the per-document winner — **zero query-time overhead**. Selection engine: `find_best_method` (`paper/analysis.py:294-327`) takes a NaN-skipping weighted average across metrics; `output_best_chunks` (`paper/analysis.py:167`) realizes per-document Adaptive Chunking by writing the argmax method's chunks. Claimed lift: intrinsic mean 91.1 vs best fixed 90.7; RAG Retrieval-Completeness 67.7 vs 58-59; answered 65/99 vs 49/99 (`README.md:33-49`).

**Block-Integrity — the metric to port.** `compute_block_integrity` (`metrics.py:264`, verified):

```python
def compute_block_integrity(chunks, doc_split_points, full_text, tolerance_chars=5) -> float | None:
    # predicted splits = chunk start offsets[1:] located via find_chunks_start_and_end
    predicted_split_points = sorted({s for s in starts[1:] if s is not None})
    block_bounds = [0] + sorted(doc_split_points) + [doc_len]
    intact = 0
    for left, right in zip(block_bounds, block_bounds[1:]):
        block_broken = any(
            (left < p < right) and (p - left) > tolerance_chars and (right - p) > tolerance_chars
            for p in predicted_split_points)
        if not block_broken: intact += 1
    return intact / total
```

A gold block (bounded by `doc_split_points`) is "broken" ONLY if a predicted split lands strictly inside it AND is > `tolerance_chars` (5) from both edges (`metrics.py:296-304`). The chunk-location machinery is `find_chunks_start_and_end` (`postprocessing.py:100`) + the coverage check `check_chunk_gaps` (`postprocessing.py:66`). The metric suite siblings: `size_compliance` (`metrics.py:16`), `compute_intrachunk_cohesion` (`metrics.py:53`, embedder-based), `compute_contextual_coherence` (`metrics.py:150`, sliding 3000-tok window), reference-completeness via coref (`metrics.py:673` — **English-only, langdetect-gated**, NOT for our VN corpus). Orchestration: `compute_metrics.py:91-184`.

**How to use it as a chunk-quality canary in OUR pipeline.** Block-Integrity, size-compliance, and `check_chunk_gaps` are the three **language-neutral, embedder-free / deterministic** members — directly usable:

- **Ingest invariant (cheap, always-on):** after AdapChunk produces chunks, run our equivalent of `repair_gaps_between_chunks` + `assert check_chunk_gaps == True` (`postprocessing.py:128,66`). Raise a "this is a bug" error if chunks don't reconstruct source — this is the silent-failure guard. Pin it in a unit test.
- **Regression gate per (doc, method):** for any AdapChunk change (B1–B4 boundary work), compute Block-Integrity + size-compliance on a fixed corpus *before* and *after*. A drop = the change shredded structural blocks. This is a deterministic Coverage-style score with **no LLM judge** — satisfies rule#0 (SỰ THẬT not GIẢ THUYẾT).
- We need `doc_split_points` (gold block offsets). Our structured-markdown parser already knows heading/table boundaries — emit them as char offsets alongside chunks (see §5), then Block-Integrity is computable on our own data with no manual gold labelling.

**Two-track evaluation discipline** (`poster.pdf` "Two-track evaluation"): intrinsic metric is a *hypothesis*; the extrinsic RAG eval (`paper/rag_eval.py`, `RetrievalCompletenessMetric` `:37-143`, 0/0.5/1.0 recall proxy) is the *evidence*. Maps onto our rule: any chunking change must show both an intrinsic lift AND a downstream Coverage/Faithfulness lift before shipping. (Their extrinsic side uses an LLM judge; our `rag-loadtest` deliberately uses deterministic agent-scoring — adopt the *two-track principle*, not their judge.)

---

## 2. Adaptive splitters — how boundaries are chosen WITHOUT domain vocab

The decoupling: **the parser decides semantic boundaries, the splitter only honours generic separators + token budgets.** Parsers emit char-offset `split_points` in one JSON contract (`parsing.py:12-23` BaseParser docstring: `{document_name, pages, full_text, split_points:[int], titles:[{title,start,end,level}]}`). Every parser (Azure/Excel/Docling/PyMuPDF) emits the same contract → splitting/metrics are format-agnostic.

**Structural, vocabulary-free split rules** (`parsing.py:429-442`, Azure; mirrored Docling `:870-880`): *no split after TITLE/SECTION_HEADING; no split between two consecutive short (<100 tok) TEXT blocks; no split before FOOTNOTE*. Only generic structural ROLE tags (TITLE/TABLE/FOOTNOTE/PAGE_BREAK) — zero brand/industry literals.

**Core splitter** `RecursiveSplitter` (`splitters.py:7`):
- `_recursive_split` (`:128`) tries separators in order `['\n\n','\n',' ','']`; the empty-string fallback uses **binary search over char index** (`:142-181`) to find the largest prefix whose token count ≤ `max_length` — token-bounded hard splits in O(log n) measure calls, tokenizer injected via `length_function` (`:25`). No naive char truncation.
- Two merge strategies (config, not hardcoded): `_merge_splits` "**to_chunk_size**" (`:207`, greedily fill to budget, build overlap by backtracking parts) vs `_merge_small_splits` "**small_only**" (`:280`, **preserve parser boundaries, only fuse blocks < `min_chunk_tokens`**). `small_only` is the late-binding / no-shred mode RAG wants. Forward/backward merge order with order-preserving reversal (`:213,269`).

**Experimental baselines** (`paper/splitters.py`) worth knowing:
- `SEPARATORS` ladder (`replicate.py:56-71`): markdown H1–H6 → enumerated list items → bullet glyphs → blank lines → newline → sentence punctuation → comma → whitespace → char. Structural/markdown-based ⇒ language-agnostic for Latin-script + markdown.
- `LongContextSemanticSplitter` **adaptive threshold** (`paper/splitters.py:365-381`): instead of a magic cosine cutoff, sort sentence-pair dissimilarities descending and pick the **largest threshold whose resulting max chunk stays ≤ `max_chunk_tokens`** — a data-driven boundary respecting a structural budget (= our zero-hardcode discipline). Quantile fallback `np.quantile(...,0.90)` (`:363-364`).
- `SemanticChunkerWrapper._map_chunks` (`paper/splitters.py:36-46`): remaps LangChain's *mutated* semantic chunks back onto source via a whitespace-relaxed regex (`:30-34`) then forces monotonic contiguous boundaries (`:52-57`) — **converts a lossy splitter into a lossless span-based one.** Reconstruction asserts at `:291,311,321`.
- `LLMRegexSplitter` (`paper/splitters.py:545-577`): LLM emits a `re.split` pattern, validated via `re.compile` with an auto-repair pass (escape unescaped hyphens in char classes), else fall back to `[text]` — **validate-then-degrade, never trust raw LLM output.**

---

## 3. RAG-Anything multimodal parse — tables/images/equations/headers → structured markdown — the antidote to our `col_N` header bug

**The canonical IR.** Every parser (MineruParser / DoclingParser / PaddleOCRParser / custom-registered) emits ONE `content_list` = ordered `List[Dict]` of typed blocks: `{"type": "text"|"image"|"table"|"equation", text/img_path/table_body/latex, text_level, page_idx, captions...}` (`parser.py:962-1076`; schema by usage `examples/insert_content_list_example.py:104-173`; contract doc `processor.py:2100-2109`). Downstream consumes only this — format never leaks past the parser. Registry = Port+Strategy: `register_parser`/`get_parser` (`parser.py:2393-2522`) with built-in-override guard (`:2438-2443`); format buckets are class sets `OFFICE_FORMATS`/`IMAGE_FORMATS`/`TEXT_FORMATS` (`parser.py:76-78`).

**Tables — the antidote pattern (verified `utils.py:25-58`):**

```python
def get_table_body(item):                       # alias unification across parser variants
    if item.get("table_body") not in (None, ""): return item.get("table_body")
    if item.get("table_data") not in (None, ""): return item.get("table_data")
    return item.get("text", "")

def format_table_body(table_body):              # list-of-rows → real markdown grid
    if isinstance(table_body, str): return table_body            # passthrough
    if isinstance(table_body, list) and all(isinstance(r,(list,tuple)) for r in table_body):
        rendered = ["| " + " | ".join(str(c) for c in row) + " |" for row in table_body]
        column_count = max(len(row) for row in table_body)
        separator = "| " + " | ".join(["---"]*column_count) + " |"
        rendered.insert(1, separator)           # header separator after row 0
        return "\n".join(rendered)
```

Why this is the antidote to **our `col_N` bug**: structure is derived from the **data shape** (row/column count, padded to `max(len(row))`), captions/footnotes kept as attached arrays — the LLM sees aligned rows/columns, never a Python repr, never an invented placeholder header. Our `src/ragbot/shared/tabular_markdown.py::rows_to_structured_markdown` (`:106`) already emits `| --- |` separators and has a `_looks_header` heuristic (`:90`) — but the lesson is: **render structurally and unify alias fields at the parser boundary** rather than synthesizing `col_N` headers when a header row is absent. RAG-Anything simply pads and emits the grid without a fabricated header; that is the safer default for a header-less table.

**Headers (verified `modalprocessors.py:225-230`):** heading level is a numeric `text_level` attribute on the block; reconstruction is `f"{'#'*text_level} {text}"` only when `text_level > 0`. **Never inferred from heading words** ⇒ language-neutral, fixes VN/ZH heading loss that regex/keyword detection causes.

**Equations.** `omml_extractor.py` is a **zero-dependency OMML→LaTeX recursive-descent transformer**: opens DOCX as zip, finds every `m:oMath` in document order, dispatches per-tag handlers (`_h_fraction`, `_h_nary`, `_h_matrix`, `_h_radical`...) with an explicit **recall-over-correctness contract** — `_convert_children` returns `""` for a missing child instead of raising (verified `:411-424`), unknown n-ary operators fall back to the raw Unicode char rather than rewriting (`:336-541,717-751`). Office math (normally rasterized away in DOCX→PDF) becomes searchable LaTeX; a malformed equation never crashes the document. Field-priority resolution in `utils.py:61-86` (`text`→`latex`→`equation` alias; description NOT concatenated into the equation body).

**Cross-cutting hardening worth stealing:**
- Field-alias normalization at the parser boundary (`parser.py:1024-1042`, e.g. `img_caption↔image_caption`) absorbs MinerU 1.x↔2.0 schema drift — the claude-mem "normalize-at-the-hook" pattern; directly addresses our repeated column/field naming-drift bugs (V2 lessons).
- Parse-result caching keyed on `{abs path, mtime, parser, parse_method, kwargs}`, **re-validated on read against both mtime AND config** (`processor.py:48-96,239-316`) — busts correctly when parser/lang/method changes; saves the most expensive ingest step (T2).
- Path-traversal hardening on parser-emitted image paths: `resolve()` + `is_relative_to(base)` + symlink-block + safe-dir allowlist (`parser.py:1060-1069`, `utils.py:179-181`, `query.py:632-671`) — prevents document-driven file-read injection in multi-tenant media handling.
- Resilience (`resilience.py:24-56,233-397`): the circuit breaker opens/retries **only on transient network/API errors**; `TypeError`/`ValueError` fail loud — exactly our graceful-degradation sacred rule (transport→degrade silent, client bug→fail loud).
- Content-based `doc_id` hash (`processor.py:200-237`) ⇒ idempotent re-ingest regardless of filename — parallels our `X-Idempotency-Key`/UPSERT.

---

## 4. Multi-language in RAG-Anything (prompts_zh) — language variance handled config-side

Prompts are **data, not code**, swapped atomically: `PromptRegistry` (`prompt.py:13-65`) with lazy-loaded language packs (`prompt_manager.py:84-145`) and **per-key English fallback** so a partial translation never breaks the fixed JSON schema. `prompts_zh.py` is the Chinese pack; the JSON answer/description schema *inside* each localized prompt is identical across languages ⇒ downstream parsing stays language-independent. OCR `lang` kwarg is threaded to parsers (`parser.py:1388`); tiktoken offline cache covers non-OpenAI tokenization (`docs/offline_setup.md`); bge-m3 / Qwen multilingual backends are documented (`docs/vllm_integration.md`).

The structural layer is genuinely multilingual: table-from-shape and heading-from-`text_level` work identically for VN/ZH/EN. **Caveat to carry into our adoption:** in adaptive-chunking the *sentence-segmentation + coreference* layers are English-pinned (Stanza/spaCy default `'en'` `paper/splitters.py:140,165-169`; maverick-coref + `en_core_web_sm` `metrics.py:924`, `replicate.py:339`) and the pipeline langdetect-skips non-English (`split_documents.py:64-76`, `extract_mentions.py:32-44`). So **reference-completeness is NOT usable for our VN corpus** — keep only the structural + embedder-based metrics (Block-Integrity / size / cohesion) and feed them a VN-capable embedder via the SentenceTransformer-compatible `encode` adapter (`jina_embedder.py:104`).

Maps onto our `language_packs`: adopt **atomic-swap + per-key fallback** so a missing VN key never breaks the assembled prompt schema.

---

## 5. Domain-neutral verdict + concrete adoption (EVOLVE-not-rewrite, mapped to our files)

**Verdict: both repos are strongly domain-neutral and structural-over-vocabulary** — they are clean reference implementations of principles our CLAUDE.md already mandates, so adoption is *wiring/hardening*, not rewrite. Caveats: (a) magic numbers exist in both (adaptive-chunking min/max-tok 100/1100/1200, `tolerance_chars=5`, window 3000) — fine for research, would violate our zero-hardcode rule, so any port lifts these to `shared/constants.py` / `pipeline_config`; (b) RAG-Anything's extension-only dispatch + office→PDF→OCR is *lossier* than our mandated byte-sniff + native per-format parsers — **do not copy.**

Concrete, mapped to our files (all paths absolute):

- **`/var/www/html/ragbot/src/ragbot/shared/tabular_markdown.py`** — `rows_to_structured_markdown` (`:106`) already emits `| --- |` grids. EVOLVE: (1) when no header row is detected (`_looks_header` `:90` returns false), pad rows to `max(len(row))` and emit the grid **without synthesizing `col_N`** — adopt `format_table_body`'s header-absent behaviour (`RAG-Anything/utils.py:34-58`); (2) add an alias-unification reader like `get_table_body` (`utils.py:25`) at the parser→normalizer boundary so a parser emitting `table_data` vs `table_body` doesn't drop the table. This is the direct fix for the `col_N` header bug.
- **`/var/www/html/ragbot/src/ragbot/shared/document_stats.py`** — add a deterministic **chunk-quality stat suite** mirroring `compute_block_integrity` + `size_compliance` + `check_chunk_gaps` (`adaptive-chunking/.../metrics.py:264,16`, `postprocessing.py:66,128`), with all thresholds (`tolerance_chars`, min/max tokens) sourced from `shared/constants.py` / `pipeline_config`, NOT inline. Expose as a stat the load-test/ingest path can assert against (Coverage-style canary).
- **AdapChunk parser adapters (Kreuzberg → block list, per EVOLVE charter)** — emit the **canonical typed-block IR** once per format: ordered `[{type, text/table_body/img_path/latex, text_level, page_idx, char_start, char_end}]`, matching both `RAG-Anything content_list` (`processor.py:457-533`, `utils.py:89` `separate_content`) and adaptive-chunking's `split_points`+`titles` contract (`parsing.py:12-23`). Carrying `char_start/char_end` is what makes Block-Integrity + lossless-coverage assertions computable on our own data, and makes chunk→source citation offsets reliable.
- **AdapChunk splitter (L1–L7)** — adopt the **`small_only` merge mode** (`adaptive-chunking/.../splitters.py:280`) as the boundary-preserving default (keep parser blocks, fuse only < `min_chunk_tokens`), plus the **binary-search token-bounded crop** (`splitters.py:142-181`) for oversized blocks using our injected tokenizer, and a **method-agnostic size-regularization post-step** (re-split >max, merge <min — `replicate.py:272-318`).
- **Ingest invariant** — after chunking, run the lossless-coverage assertion (`check_chunk_gaps` / `repair_gaps_between_chunks`, `postprocessing.py:66,128`); pin in a unit test. Guards a silent HALLU-class failure (dropped digit).
- **DOCX path** — integrate an **OMML→LaTeX pass** (`RAG-Anything/omml_extractor.py`) so office equations become searchable LaTeX instead of being lost in any DOCX→PDF step; keep its recall-over-correctness graceful fallback.
- **`language_packs`** — adopt PromptRegistry-style **atomic-swap + per-key English fallback** (`prompt.py:13`, `prompt_manager.py:84`) so partial VN translations never break the assembled-prompt schema. (Append-only governance via our `SysPromptAssembler` ADR still applies — no new app-inject mechanism.)
- **Parser boundary hardening** — field-alias normalization (`parser.py:1024-1042`), config-sensitive parse cache (`processor.py:48-96`), and image-path `is_relative_to(base)` + symlink-block (`parser.py:1060-1069`) for multi-tenant media safety.

**Two explicit divergences to preserve (counter-lessons):** keep our `mime→file-ext→byte-sniff` verification (do NOT adopt extension-only dispatch, `processor.py:457-533`); keep our native per-format docx/xlsx/sheet parsers (do NOT route office→PDF→OCR, `parser.py:194-562` — lossy for inline math per `docs/multimodal_rag_failure_modes.md`). And keep our deterministic agent-scoring load-test — adopt RAG-Anything's defensive JSON-parse tiers (`reproduce/llm_answer_evaluator.py:159-284`) only to **harden our JSON-returning LLM steps** (CRAG grader / decomposer), not as a scoring method.
