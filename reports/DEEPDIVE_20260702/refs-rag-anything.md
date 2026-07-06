# DEEPDIVE — RAG-Anything (HKUDS) reference study

**Slug**: refs-rag-anything · **Date**: 2026-07-02 · **Scope**: `/var/www/html/ragbot/_external_refs/RAG-Anything` (23,866 Python LOC measured via `wc -l` over non-`__pycache__` `.py`; task said ~24k — FACT, matches).
**Mandate**: map how RAG-Anything handles multi-format ingest (PDF/office/images/tables/equations), its parser abstraction, multimodal knowledge graph, and retrieval composition; extract the patterns most applicable to ragbot's multi-format ingest weakness (owner: "ragbot only handles happy-case").
**Method**: full read of all 19 modules in `raganything/` (parser.py 2660 LOC, processor.py 2229, modalprocessors.py 1607, query.py 868, omml_extractor.py 758, raganything.py 644, enhanced_markdown.py 534, batch_parser.py 470, batch.py 428, prompt.py 406, resilience.py 397, utils.py 380, callbacks.py 377, prompts_zh.py 337, config.py 158, prompt_manager.py 156, asset_urls.py 117, base.py 12) + `docs/` + `tests/` listing + targeted greps on ragbot's ingest side for comparison. Every claim below carries `file:line`. FACT = read in code; HYPOTHESIS = inference, labelled.

Upstream identity (FACT): HKUDS project built **on top of LightRAG** (README.md:20 "Based on LightRAG"), technical report arXiv 2510.12323 (README.md:19,75). It is a **single-tenant library**, not a platform: `grep -rn "tenant" raganything/*.py` → 0 hits; the only isolation concept is LightRAG's `workspace` passed through (raganything.py:313,327,388).

---

## 1. Architecture map

```
file/URL ──► Parser (MinerU | Docling | PaddleOCR | BYOP registry)      parser.py
                 │  every parser emits the SAME output:
                 ▼
        content_list: [ {type: text|image|table|equation|<generic>, …, page_idx} ]
                 │                                                     processor.py
                 ├─ parse cache (md5 key: path+mtime+parser+method+kwargs)
                 ├─ content-based doc_id (md5 of content signature)
                 ▼
        separate_content() ──► text_content ─► LightRAG.ainsert (token chunking)
                 │                                                     utils.py:89
                 └► multimodal_items ─► 7-stage type-aware batch pipeline
                        Stage1 per-type LLM/VLM description (concurrent, semaphore)
                        Stage2 chunk template (raw structure + enhanced caption)
                        Stage3 store chunks (text_chunks + chunks_vdb)
                        Stage3.5 modal main entities → KG + entities_vdb + full_entities
                        Stage4 LightRAG extract_entities on modal chunks
                        Stage5 belongs_to edges (every extracted entity → modal entity, w=10.0)
                        Stage6 LightRAG merge_nodes_and_edges
                        Stage7 doc_status chunks_list update            processor.py:1025-1057
Query:  aquery (delegate LightRAG modes) │ aquery_with_multimodal (describe user
        media → enhanced query text) │ aquery_vlm_enhanced (retrieve prompt →
        swap img paths for base64 → VLM answers)                        query.py
```

Composition style (FACT): `RAGAnything(QueryMixin, ProcessorMixin, BatchMixin)` dataclass mixin god-object (raganything.py:51), config from env vars via a dataclass (config.py:13-115), processors initialized in `_initialize_processors()` (raganything.py:204-247, vision func falls back to LLM func at :220). This is mixin/env style, NOT ports+DI — architecture-wise ragbot's Hexagonal + registry + `system_config` chain is strictly ahead (T3); the value here is in the **ingest data contracts and robustness mechanics**, not the wiring.

---

## 2. Multi-format ingest — how each format flows

### 2.1 Parser abstraction

- **Base class contract** (FACT, parser.py:68-691): `Parser` defines format sets `OFFICE_FORMATS={.doc,.docx,.ppt,.pptx,.xls,.xlsx}`, `IMAGE_FORMATS={.png,.jpeg,.jpg,.bmp,.tiff,.tif,.gif,.webp}`, `TEXT_FORMATS={.txt,.md}` (parser.py:76-78) + abstract `parse_pdf / parse_image / parse_document / check_installation` (parser.py:608-691). Shared helpers: URL download with Content-Type→extension inference (parser.py:92-165), unique output dir via path-hash to prevent same-name collisions (parser.py:171-191, fixes their #51).
- **Three built-ins**:
  - `MineruParser` (parser.py:694-1452) — shells out to the `mineru` CLI via `subprocess.Popen` with threaded stdout/stderr pumps and a wall-clock timeout that kills the process (parser.py:803-899), then reads `*_content_list.json` + `.md` artifacts back from disk (parser.py:960-1076).
  - `DoclingParser` (parser.py:1455-2049) — in-process Python API, `DocumentConverter` cached per pipeline-option tuple under a lock so layout/OCR/TableFormer models load once (parser.py:1627-1701); handles PDF, office natively, HTML; exposes `table_mode: fast|accurate` (TableFormer) as a plain kwarg (parser.py:1649-1675).
  - `PaddleOCRParser` (parser.py:2052-2375) — per-language OCR instance cache (parser.py:2073-2097), renders PDF pages to images and OCRs them.
- **BYOP registry** (FACT, parser.py:2389-2522): module-level `_CUSTOM_PARSERS: Dict[str, type]`, `register_parser(name, cls)` validates `issubclass(cls, Parser)` and **refuses collisions with built-in names** (parser.py:2434-2443); `get_parser()` checks built-ins first then the custom registry (parser.py:2493-2522). Same shape as ragbot's `infrastructure/parser/registry.py` — confirmation of the pattern, plus two guards ragbot's registry lacks (type check + reserved-name guard; ragbot `registry.py:64-89` accepts any class already present in its dict and has no runtime registration API).

### 2.2 Format routing = extension-only (their weakness, ragbot's strength)

FACT: routing is `file_path.suffix.lower()` in both the parser (`parse_document`, parser.py:1400-1421) and the orchestrator (processor.py:456-539). There is **no MIME check and no byte-sniff anywhere**; URL downloads infer extension from URL path or `Content-Type` header only (parser.py:103-133). Unknown extension → *"attempting to parse as PDF"* (parser.py:1415-1421) — a mis-named `.bin` xlsx would be fed to a PDF OCR engine.
→ Ragbot's `detect_parser_robust` (mime → ext → magic-number `%PDF-` → OOXML `[Content_Types].xml` peek → kreuzberg sniff, `src/ragbot/infrastructure/parser/registry.py:123-179`) is **strictly stronger**. Nothing to import here; do not regress.

### 2.3 Everything-to-PDF normalization (MinerU path) — anti-pattern with a built-in confession

FACT: for MinerU, office docs are converted with headless LibreOffice (`libreoffice`→`soffice` fallback chain, 60 s timeout, size sanity check, parser.py:194-341) and txt/md are **rendered to PDF with ReportLab** (parser.py:343-562, incl. CJK font gymnastics :408-467) before OCR-style parsing. This is lossy by design: heading levels, spreadsheet cell topology, and inline math are destroyed then re-guessed by a layout model.
The repo itself ships the countermeasure that proves the cost: `omml_extractor.py` exists because "When a DOCX is converted to PDF … inline math is typically rasterized … so the structured math content is lost" (omml_extractor.py:5-9) — a 758-LOC pure-stdlib OMML→LaTeX recovery pass over `word/document.xml` that merges equations back into the content_list (omml_extractor.py:1-38).
→ Ragbot's per-format structured parsers (docx/xlsx/sheets/csv native, kreuzberg-markdown for pdf/pptx/html) are the better architecture. The **portable idea** is the OMML trick itself: DOCX equations are extractable losslessly from the zip with zero deps — relevant if ragbot ever needs equation support in DOCX (ragbot currently has none: `grep -rn "equation|formula" src/ragbot/infrastructure/parser/` → 0 hits; only a `formula_count` profile metric at `ingest_stages.py:721` and an atomic-protect flag at `ingest_stages.py:549`).

### 2.4 The `content_list` typed-block contract — the load-bearing interface

FACT: every parser, whatever the engine, converges to one list-of-dicts schema, documented at processor.py:2100-2110:

```python
{"type": "text",     "text": str, "text_level": int, "page_idx": int}
{"type": "image",    "img_path": abs, "image_caption": [..], "image_footnote": [..], "page_idx": int}
{"type": "table",    "table_body": md, "table_caption": [..], "table_footnote": [..], "page_idx": int}
{"type": "equation", "text": latex, "text_format": "latex", "page_idx": int}
{"type": <anything>, "content": any, "page_idx": int}   # generic — open taxonomy
```

Multimodal blocks are **atomic**: 1 block → exactly 1 chunk downstream (processor.py:1059-1105) — never split, matching ragbot's atomic-block invariant. Unknown types don't crash: `get_processor_for_type` routes them to `GenericModalProcessor` (utils.py:330-350, modalprocessors.py:1447+), i.e. the taxonomy is open-closed.

**Alias tolerance** (FACT) — the contract survives schema drift across parser versions:
- `img_caption ↔ image_caption`, `img_footnote ↔ image_footnote` bidirectional normalization for MinerU 1.x vs 2.0 rename (parser.py:1024-1041);
- `get_table_body`: `table_body` → `table_data` → `text` priority (utils.py:25-31); `format_table_body` renders list-of-lists as a markdown table instead of a Python repr (utils.py:34-58);
- `get_equation_text_and_format`: `text` → `latex` → `equation` priority with format inference (utils.py:61-86).
This is the same bug class as ragbot's `'rerank' vs 'reranker'` binding drift ([feedback_v2_bug_lessons]) — RAG-Anything solves it with tolerant readers at the single choke-point instead of chasing every producer.

Also FACT: image paths in content_list are made absolute with a **path-traversal guard** (`is_relative_to(resolved_base)`, offending fields blanked, parser.py:1060-1067), and an optional local-path→public-URL two-field mapping is attached per media field (`attach_public_media_urls`, asset_urls.py — env `RAGANYTHING_PUBLIC_ASSET_BASE_URL`/`STRIP_PREFIX`; explicitly wired into the MinerU path only, asset_urls.py:8-12).

### 2.5 Where the contract is weaker than ragbot

FACT: `separate_content()` flattens ALL text blocks into a single `"\n\n"`-joined string and hands it to LightRAG's token chunker (`utils.py:101-119` — reads only `item["text"]`, ignores `text_level`; insert via `lightrag.ainsert` with optional `split_by_character`, utils.py:224-254). Heading structure is discarded at the text/chunking boundary; page/heading provenance for *text* chunks is lost. Ragbot's structured-markdown contract + template-per-doctype chunking keeps that structure. Additional wart: `DoclingParser.read_from_block` **fabricates** `page_idx = cnt // 10` (≈"10 blocks per page", parser.py:1856,1862,1882,1889,1899,1906) — citation metadata is fiction on the Docling path.

---

## 3. Non-happy-path machinery (the part ragbot's owner asked about)

This is the densest cluster of adoptable patterns. RAG-Anything is visibly shaped by real-world dirty-file issues (numbered GitHub issues are cited in code: #24 CID fonts, #51 name collisions, #85 prompt language, #135 event-loop teardown, #151 BYOP, #172 stuck ingest).

1. **Per-block graceful degradation with sentinel blocks** (FACT): a failed image decode becomes `{"type":"text","text":"[Image processing failed: <caption>]"}` instead of killing the doc (parser.py:1884-1890); same for tables (parser.py:1901-1907). Every modal processor has a fallback entity so enrichment failure still yields an indexable chunk (`image_…/table_…` fallback entities, modalprocessors.py:954-964, 1150-1160, image-processing exceptions likewise 1017-1027). Batch stage-1 failures are filtered, the rest proceed (processor.py:1006-1019); whole-batch failure falls back to **individual** processing (processor.py:714-723).
2. **Fail-loud floor**: zero blocks extracted → `raise ValueError("Parsing failed: No content was extracted")` (processor.py:567-568). Degrade per block, never to an empty doc — exactly ragbot's "never nuke a doc to zero chunks" invariant, implemented at the right layer.
3. **Capability-level parser fallback** (FACT): if the configured parser raises `NotImplementedError` for images, orchestrator falls back to `MineruParser().parse_image` with a warning (processor.py:497-507). A degradation *chain across strategies*, not just Null-object. Ragbot's registry has fail-soft **construction** (ImportError→NullParser, registry.py:81-89) but no per-capability fallback chain after selection.
4. **Encoding ladder**: text read tries utf-8 → gbk → latin-1 → cp1252 before giving up (parser.py:368-386).
5. **Robust JSON parse ladder** for LLM enrichment output (FACT, modalprocessors.py:577-718): strip `<think>/<thinking>` reasoning tags (:609-619; also `_strip_thinking_tags` :553-575 to keep CoT out of the KG) → fenced-code-block JSON → balanced-brace scan → smart-quote/trailing-comma cleanup → progressive backslash escaping (LaTeX `\alpha` case, :672-685) → regex field extraction as last resort (:687-718). Ingest enrichment never crashes on malformed model JSON.
6. **Resilience module** (FACT, resilience.py, written for issue #172 "process_document_complete getting stuck"): `retry`/`async_retry` with exp backoff + 0-50 % jitter and a **narrow retryable tuple** (ConnectionError/Timeout + httpx/openai transient classes only — "Local programming errors … should not be retried", resilience.py:24-56); `CircuitBreaker` with single-flight half-open probe and the subtlety that **application bugs don't count as upstream failures but do release the half-open gate** (resilience.py:352-397). Consistent with ragbot's graceful-degradation doctrine (transport→degrade, client bug→fail loud).
7. **MinerU subprocess hardening**: real-time stdout/stderr pumping via reader threads + queues, error-line harvesting, kill-on-timeout with actionable message ("often means a model download is stuck…", parser.py:830-899), `MineruExecutionError` carrying return code + messages (parser.py:57-65).
8. **Failure-modes runbook** (FACT, docs/multimodal_rag_failure_modes.md, 84 lines): six named failure classes (OCR text corruption; table structure lost; image↔caption misalignment; retrieval biased toward text; slow-vs-stuck; local-path assets in remote UI) each with concrete checks. Best single idea for ragbot QA: the **probe question** — "Run a probe question that can only be answered from an image or table (not from surrounding text)" (docs/multimodal_rag_failure_modes.md:47-51) = a modality-coverage metric alongside Coverage rate.

---

## 4. Idempotency, caching, and doc lifecycle

- **Parse cache** (FACT, processor.py:48-96, 239-384): key = md5 of `{abs path, mtime, parser, parse_method, relevant kwargs (lang/device/pages/formula/table/backend/source)}`; entries carry `content_list`, `doc_id`, `mtime`, `parse_config`, `cache_version`; validation re-checks mtime AND config equality before reuse (:262-297). Config change or file touch = transparent reparse. Stored in a LightRAG KV namespace (`parse_cache`, raganything.py:386-392).
- **Content-based doc_id** (FACT, processor.py:200-237): md5 over a content signature assembled per type (text strips; `image:<path>`; `table:<body>`; `equation:<text>`) → same content = same `doc-…` id regardless of filename → natural dedupe/idempotent re-ingest. Ragbot's canonical-ingest skill mandates content-hash idempotency — this is a working reference incl. the per-type signature detail.
- **Doc status state machine** (FACT, base.py:1-12: READY/HANDLING/PENDING/PROCESSING/PROCESSED/FAILED): the key design point is **two independent completion axes** — LightRAG marks text PROCESSED early, so multimodal completion is a separate `multimodal_processed` flag, with a compatibility KV namespace when the doc_status schema can't hold extra fields (processor.py:159-198, 1533-1561 incl. schema-incompatible fallback) and explicit states like "text done but multimodal still needed" (processor.py:648-676). Failures record `error_msg` + a **stage label** (`stage = parse | text_insert | multimodal` tracked through the workflow, processor.py:1681,1731,1762, error path :1776-1797). `is_document_fully_processed` / `get_document_processing_status` expose it (processor.py:1574-1652). For ragbot's multi-stage ingest (parse→chunk→embed→enrich→store) the per-stage flags + stage-labeled error are directly transplantable to the `documents` state machine.
- **Batch**: `BatchParser` = ThreadPoolExecutor over files with per-file timeout, tqdm, dry-run mode, and a `BatchProcessingResult` (success/fail lists + per-file error map + success-rate, batch_parser.py:21-337); `BatchMixin` = asyncio semaphore version bound to full processing (batch.py:34-176). Observability = optional `CallbackManager` with typed hooks (`on_parse_start/complete/error`, `on_text_insert_*`, `on_multimodal_*`, `on_query_*`, `on_document_*`, callbacks.py:61-180) — same role as ragbot's `request_steps`/structlog events, ingest-side.

---

## 5. Multimodal knowledge graph

FACT (modalprocessors.py + processor.py stages 3.5-6):

- Each modal block gets an **entity** (`entity_name` auto-suffixed with type, e.g. "Revenue Table (table)", modalprocessors.py:1242-1244) whose description is the LLM-generated analysis; stored 4 ways: KG node (`upsert_node`), `entities_vdb`, `full_entities` per-doc roster (processor.py:1208-1360), and the chunk itself in `text_chunks` + `chunks_vdb` (modalprocessors.py:471-535).
- The chunk content is a **dual-representation template**: raw structure (markdown table body / LaTeX / image path+captions) PLUS the enhanced caption (prompt.py:328-353 `image_chunk`/`table_chunk`/`equation_chunk`/`generic_chunk`; applied at processor.py:1107-1189 with fallback to bare description on template error). So BM25 hits raw cell values while dense embedding hits the narrative — both roads lead to the same chunk.
- **`belongs_to` containment edges**: after LightRAG's standard entity extraction runs *on the modal chunk text*, every extracted entity gets an edge `entity —belongs_to→ modal_entity` with `weight: 10.0`, keywords `"belongs_to,part_of,contained_in"` (batch path processor.py:1391-1453 esp. :1431-1446; individual path modalprocessors.py:766-799). Effect: graph query for "Q3 revenue" reaches the table entity → whose `source_id` is the full-table chunk. That's the cross-modal retrieval composition in one edge type.
- **Context-aware enrichment** (modalprocessors.py:39-364): `ContextExtractor` gathers ±N pages or ±N blocks of *text* around the modal block (mode `page|chunk`, config'd window/`max_context_tokens`/`include_headers`/`include_captions`, config.py:80-107), token-truncates at sentence boundaries (:314-363), and feeds it into `*_prompt_with_context` variants (prompt.py:113-142, 183-213, 244-273) so a table titled "Table 3" gets described in terms of what the surrounding narrative says it shows. Ships as a documented feature (docs/context_aware_processing.md, 375 lines). This is the anti-hallucination lever for enrichment: description grounded in document context, not model imagination.
- Two-stage batch design: stage 1 (LLM descriptions) is concurrent under `max_parallel_insert` semaphore with progress ticks (processor.py:907-1006); heavy KG merge runs as LightRAG batch (stages 4-6) — matches ragbot's Async Rule 6 (bounded gather).

HYPOTHESIS (not measured here): the belongs_to/KG layer only pays off in graph-mode retrieval (LightRAG local/global/mix). For ragbot (pgvector + BM25 + rerank, no KG in the answer path), the transplantable core is the **dual-representation modal chunk + context-grounded enrichment**, not the graph itself; ragbot already has `infrastructure/graph/` and `entity_extractor/` dirs (ls evidence, src/ragbot/infrastructure/) — wiring belongs_to would be a Phase-2 experiment behind per-bot config, gated by retrieval eval, per T1-first ordering.

---

## 6. Retrieval composition (query side)

FACT (query.py):

1. `aquery` — thin delegate to LightRAG modes (`local/global/hybrid/naive/mix/bypass`), auto-upgrades to VLM-enhanced when a `vision_model_func` exists (query.py:102-149).
2. `aquery_with_multimodal` — user attaches media *to the query*; each item is described by its modal processor (image→VLM, table/equation→LLM, query.py:505-587), descriptions are concatenated into an enhanced query text + suffix (query.py:437-473), then normal retrieval runs. Cached under a normalized md5 key (basenames for paths, hashes for big table bodies, query.py:26-100).
3. `aquery_vlm_enhanced` — the standout composition (query.py:349-420): run retrieval with `only_need_prompt=True` to get the assembled context (:391-392), scan it for image paths, validate them (exists/not-symlink/extension/≤50 MB, utils.py:156-221) and replace with base64 attachments (:397-399), then send the multimodal message set to the VLM; **fallback to plain text query when no valid images found** (:401-407). The generator sees actual pixels of retrieved figures, not just their captions.

Ragbot compliance note (FACT about ragbot rules, not about this repo): pattern 3 injects platform prompt text (`QUERY_ENHANCEMENT_SUFFIX`, prompt.py:404-406) and constructs messages around the user query — under ragbot's sacred rule #10 this must be re-homed: prompts into `language_packs`/bot config with the ADR-W1-S10 append-only exception process, feature per-bot opt-in, and it composes *input* to the LLM rather than overriding the answer (allowed surface, but governance applies).

---

## 7. Config & i18n

- Config = env-var dataclass (`PARSER`, `PARSE_METHOD`, `ENABLE_{IMAGE,TABLE,EQUATION}_PROCESSING`, `MAX_CONCURRENT_FILES`, `SUPPORTED_FILE_EXTENSIONS`, context knobs; config.py:18-115) with deprecation shims for renamed keys (config.py:117-158). Per-modality **enable flags** map cleanly onto ragbot per-bot `plan_limits` toggles; env-global scope does not (multi-tenant needs per-bot).
- Prompts = `PromptRegistry` with **atomic snapshot swap** (prompt.py:13-65) + `prompt_manager.set_prompt_language`/`register_prompt_language` under an RLock, lazy `prompts_zh` load (prompt_manager.py:28-100). Same idea as ragbot's `language_packs` DB, but **process-global** — one language per process. FACT: unusable as-is for ragbot's per-bot locale; the enrichment-prompt *keys* (vision/table/equation × with/without context, ANALYSIS_SYSTEM set) are the reusable part, as language-pack rows.

---

## 8. Direct content-list injection

FACT: `insert_content_list()` (processor.py:2087-2229) accepts a pre-parsed typed block list and runs the *identical* downstream (doc_id → status → separate → text insert → multimodal stages). Parsing and indexing are decoupled at a documented public seam; external systems (or a parser farm on other hardware) can feed blocks directly. For ragbot's headless BE-to-BE platform this suggests one optional canonical API surface: `documents/create` accepting `content_blocks` alongside file/URL — **same funnel after the parser stage**, no parallel pipe (consistent with canonical-ingest-flow rule; would need X-Idempotency-Key + 4-key identity + tenant scoping).

---

## 9. Side-by-side: RAG-Anything vs ragbot ingest

| Axis | RAG-Anything | ragbot today | Verdict |
|---|---|---|---|
| Type detection | extension-only; unknown→try-as-PDF (parser.py:1400-1421) | mime→ext→byte-sniff (`registry.py:123-179`) | **ragbot ahead** — don't regress |
| Parser abstraction | base class + built-ins + BYOP registry w/ collision+type guards (parser.py:2389-2522) | Port+Registry+Null (`registry.py:45-120`) | parity; ragbot lacks registration guards |
| Parser output | typed block list, open taxonomy, alias-tolerant readers (processor.py:2100-2110; utils.py:25-86) | `list[{"content": str, "metadata": dict}]` (`document_parser_port.py:29-39`) + structured markdown | **RAG-A ahead**: modality is first-class in the contract |
| Text structure | flattened, heading-blind chunking (utils.py:101-119) | structured markdown, heading/table-aware template chunking | **ragbot ahead** |
| Embedded images/figures | VLM caption + entity + chunk (modalprocessors.py:832-1067) | dropped — kreuzberg parser has zero image handling (grep 0 hits); `vlm_image` = standalone image files only (`registry.py:56-60`) | **RAG-A ahead — this is the gap** |
| Tables | LLM analysis + dual-representation chunk + entity (modalprocessors.py:1069-1261) | table-aware markdown + row-as-chunk xlsx/sheets | partial parity; ragbot has no table *description* layer |
| Equations | dedicated processor + OMML recovery from DOCX (modalprocessors.py:1264+; omml_extractor.py) | none (grep 0 hits) | **RAG-A ahead** |
| Non-happy-path | sentinel blocks, fallback entities, JSON ladder, retry/CB, capability fallback chain, zero-block fail-loud | soft-failure sentinel skill exists; coverage unverified here | **RAG-A = reference implementation** |
| Idempotency | parse cache + content-based doc_id (processor.py:48-96,200-237) | content-hash idempotency mandated by skill; X-Idempotency-Key | parity in intent; RAG-A adds parse-config in key + mtime validation |
| Doc lifecycle | 6-state + per-stage flags + stage-labeled errors (processor.py:103-198,1681-1797) | document state machine (webhook design doc) | RAG-A's two-axis completion is the takeaway |
| Multi-tenant | none (0 "tenant" hits) | 4-key identity + RLS | **ragbot ahead**; all imports need tenant scoping |
| Retrieval composition | KG modes + multimodal query + VLM-enhanced query (query.py) | hybrid BM25+vector+rerank, ~21-node graph | different families; VLM-enhanced query is the novel piece |
| Hardcode/domain rules | env defaults, inline numbers (e.g. 50 MB img cap utils.py:156, weight 10.0), fabricated page_idx | zero-hardcode + system_config mandated | ragbot rules stricter; port constants into `shared/constants.py`/`system_config` |
| Tests | 21 test files, ~231 test funcs (grep -c), unit-level, no e2e eval | 2000+ unit + load-test harness | ragbot ahead on harness; RAG-A has good regression tests for alias/parsers |

---

## 10. Ranked adoption list for ragbot (tier-tagged)

**T1-Smartness (bot answers better):**
1. **Modal-block taxonomy in the parser contract** — extend `DocumentParserPort` output metadata with `block_type: text|table|image|equation|generic` (+`page_idx`, captions) instead of type-blind `{"content","metadata"}`. Prereq for everything below; open taxonomy + generic fallback per utils.py:330-350.
2. **Embedded-figure enrichment path** — images inside PDFs/DOCX → VLM caption grounded by ContextExtractor-style ±1-page text window → dual-representation chunk (path+captions+description). Closes ragbot's hard modality gap. Per-bot opt-in flag (cost), enrichment via existing ingest-enrich model binding.
3. **Table description layer** — keep row-as-chunk, ADD one table-level summary chunk per table (structure + LLM analysis, table_chunk template shape) so "what does this table show"-type queries hit.
4. **Modality probe questions in load tests** — per corpus with tables/figures, N questions answerable only from those blocks; report modality-coverage next to Coverage (docs/multimodal_rag_failure_modes.md:47-51).

**T2-Cost/Perf/Robustness:**
5. **Parse cache keyed on content-hash + parser-config** (mtime for files, sha for bytes) — skip reparse+re-embed on unchanged re-upload; config change invalidates (processor.py:48-96,262-297).
6. **Per-stage doc status flags + stage-labeled error_msg** on `documents` (parse/chunk/embed/enrich axes; two-axis completion per processor.py:648-676).
7. **Sentinel-block degradation + zero-block fail-loud** at parser adapters (parser.py:1884-1907 + processor.py:567-568) — codifies the existing soft-failure skill with a reference shape.
8. **JSON parse ladder + think-tag strip** for every LLM-enrichment call in ingest (modalprocessors.py:577-718) — shared helper, narrow-except compliant.
9. **Alias-tolerant field readers** at the block-contract choke point (utils.py:25-86) — immunizes against parser-lib upgrades (kreuzberg/docling renames).
10. **Retry/CB for enrichment calls** — ragbot already has CircuitBreaker infra; the narrow-retryable-tuple + single-flight half-open detail (resilience.py:29-56,319-350) is the part worth mirroring.

**T3-Design (defer until T1 moves):**
11. Registry registration guards (type check + reserved-name collision, parser.py:2434-2443).
12. `content_blocks` direct-injection variant of `documents/create` (processor.py:2087-2229) — same funnel, BE-to-BE.
13. VLM-enhanced query (retrieve→attach real images→VLM) — Phase-2 experiment, per-bot flag, prompts via language_packs (rule #10 governance).
14. OMML equation extraction for DOCX (omml_extractor.py) — only if equation corpora appear.

**Do NOT copy** (anti-patterns vs ragbot rules): extension-only detection (§2.2); everything-to-PDF normalization (§2.3); fabricated `page_idx = cnt // 10` (parser.py:1856+); heading-blind text flattening (utils.py:101-119); process-global prompt/config state (prompt_manager.py:36-40 `_current_language` global) — multi-tenant hostile; pervasive `except Exception` swallow-and-log (e.g. processor.py:313-314, 677-679 — violates ragbot broad-except policy); inline magic numbers (50 MB cap utils.py:156, weight 10.0 processor.py:1437, `cnt // 10`) — would go to `shared/constants.py`/`system_config`; md5 for ids (fine for cache keys, but ragbot convention review needed); mixin god-object vs ports+DI.

---

## 11. Verification status

All RAG-Anything claims: FACT from direct file reads at cited lines (READ-ONLY honored; only this report file was created). Ragbot-side claims: FACT where cited (`registry.py`, `document_parser_port.py`, grep outputs for image/equation absence); the assertion "ragbot only handles happy-case" is the **owner's statement** — this study did NOT runtime-test ragbot's ingest failure paths (CHƯA verify — cần chạy dirty-file corpus qua `POST /api/ragbot/documents/create`: mislabeled mime, corrupt xlsx, image-only PDF, 0-byte file, CJK txt — đo bằng doc status + chunk counts). Performance/quality lift numbers for any adoption item: not measured here — every T1 item above requires a before/after retrieval eval per no-guess rule #0 before claiming lift.
