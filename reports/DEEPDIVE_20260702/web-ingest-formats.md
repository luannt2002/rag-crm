# DEEPDIVE — 2025–2026 SOTA: Multi-format Document Ingest Pipelines (web research)

**Slug**: `web-ingest-formats` · **Date**: 2026-07-02 · **Mode**: READ-ONLY web research + code cross-reference
**Evidence discipline (rule #0)**: every web claim carries a URL; every code claim carries `file:line`. Claims are labelled **FACT** (fetched page / code read directly) vs **SNIPPET** (search-result summary — directionally reliable, exact number not independently re-derived) vs **HYPOTHESIS** (inference, not yet measured on ragbot).

---

## 0. Executive summary

Ragbot's ingest architecture — single canonical API → layered `mime → ext → byte-sniff` detection → parser Port+Registry → structured-markdown IR → Redis-Streams worker with job status + DLQ → doc-level sha256 dedup + chunk-level hash-diff re-embed — **already matches the dominant 2025–2026 industry pattern on ~6 of 8 axes examined**. The strongest confirmations: (a) "markdown as the LLM-ready intermediate representation" is now industry consensus (MarkItDown, Docling, LlamaParse, Kreuzberg all converge on it); (b) LlamaIndex's docstore-dedup upsert pattern is functionally what `ingest_core.py` already implements; (c) the 202-Accepted + `GET /jobs/{id}` async job pattern is textbook Azure async-request-reply.

The four real gaps versus SOTA, in T1→T2 order:

1. **No confidence/quality escalation for scanned documents** — the 2026 SOTA is a *router*: native text layer → fast path; scanned → cheap OCR → escalate low-confidence pages to a VLM. Ragbot has all three engines in-tree (kreuzberg+Tesseract, Docling opt-in, `VlmImageParser`) but no router between them; `VlmImageParser` fires only for image MIMEs.
2. **HTML/web ingest has no boilerplate removal** — kreuzberg parses `text/html` structurally, but SOTA (trafilatura F1 0.883–0.958) strips nav/footer/tracking; boilerplate stripping cut LLM tokens ~97.9% in one 2026 benchmark without quality loss.
3. **No completion webhook for partner BEs** — job status is poll-only; ingest webhooks exist only for failure + quota events. The 2026 async-API reference pattern is poll + signed webhook with retry/idempotency. (Ragbot already has the 21-control-point webhook design written but deferred.)
4. **Google Workspace ingest is one-shot pull** — export-URL normalization (Sheets→CSV, Docs→DOCX) matches industry practice, but there is no Drive `changes` API / revision-based incremental re-sync, which is how LlamaIndex/Ragie/Databricks-class connectors keep corpora live.

One SOTA-choice validation worth recording: independent 2025 benchmarks put **Docling first on complex-table fidelity (97.9% cell accuracy)** and **Kreuzberg first on speed/footprint (71 MB install vs Docling 1 GB+; ~35 files/s class)** — ragbot's "kreuzberg default, docling opt-in" split (`ocr_factory.py`) is therefore defensible, but the *per-document quality-tier routing* that LlamaParse v2 productized (fast → agentic tiers at 1–45 credits/page) is the missing config layer.

---

## 1. Ragbot current-state ingest map (code FACTS, baseline for all comparisons)

| Capability | Evidence | Status |
|---|---|---|
| Single canonical ingest API + idempotency replay | `src/ragbot/interfaces/http/routes/documents.py:98-163` (idempotency service, replay event `ingest_idempotency_replay`, quota charged AFTER replay check) | ✅ live |
| Parser Strategy registry (add format = 1 file + 1 row) | `src/ragbot/infrastructure/parser/registry.py:45-61` (`_REGISTRY`: kreuzberg_markdown, excel_openpyxl, google_sheets, pdf, docx, markdown, vlm_image, null) | ✅ live |
| Layered type detection mime→ext→byte-sniff | `registry.py:97-120` (`detect_parser` supports-probe), `registry.py:123-150` (`_sniff_mime`: `%PDF-` magic, OOXML zip manifest peek, kreuzberg long-tail detector), `registry.py:153-179` (`detect_parser_robust` — declared pair trusted first, sniff only on miss) + `src/ragbot/shared/mime_sniff.py:1-60` (`AMBIGUOUS_DECLARED_MIMES` frozen: `""`, `application/octet-stream`, `binary/octet-stream`) | ✅ live |
| URL ingest with redirect + octet-stream rescue | `src/ragbot/interfaces/workers/document_worker.py:436-452` (fetch source_url, `raise_for_status`, `detect_parser_robust` on body bytes) | ✅ live |
| OCR engine selection + observable degradation | `src/ragbot/infrastructure/ocr/ocr_factory.py:1-30` (kreuzberg default → SimpleTextParser fallback with `ocr_parser_fallback` WARN; docling explicit opt-in; unknown engine = ValueError fail-loud) | ✅ live |
| Structured Block stream (headings, atomic blocks, page numbers) | `src/ragbot/infrastructure/ocr/kreuzberg_parser.py:224-326` (`_extract_blocks`: heading context threading, `is_atomic`, per-block page_no) | ✅ live |
| Scanned-PDF OCR | `kreuzberg_parser.py:4` — "scanned image-only PDFs (Tesseract OCR)" | ✅ live (Tesseract only) |
| VLM parsing | `registry.py:56-60` — `vlm_image` constructed ONLY with injected llm+spec; "the worker selects it explicitly for image MIMEs when VLM is enabled" (`document_worker.py:165-200`) | ⚠️ images only |
| Doc-level exact dedup (per-bot sha256) | `src/ragbot/application/services/document_service/ingest_core.py:421-449` (sha256 of raw content; duplicate → `ingest_duplicate_content_hash`, insert blocked; pinned by `tests/unit/test_content_hash_dedup.py` per comment at `ingest_core.py:424-428`) | ✅ live |
| UPSERT by (record_bot_id, source_url) | `ingest_core.py:387-415` (existing-doc lookup) + `ingest_core.py:502-507` (`INSERT … ON CONFLICT … source_url = EXCLUDED.source_url`) | ✅ live |
| Chunk-level incremental re-embed (hash diff) | `ingest_core.py:628-660` — re-index loads `{chunk_index: content_hash}`, unchanged chunks skipped, only changed chunks re-embedded | ✅ live |
| Async job: 202 + status resource + worker + DLQ + recovery | `documents.py` (enqueue), `src/ragbot/interfaces/http/routes/jobs.py:16-17` (`GET /jobs/{job_id}`), `document_worker.py:260` (status→running), `:655-707` (success/failed transitions), `:117` (`_is_transient_ingest_error` retry/DLQ split), `src/ragbot/interfaces/workers/document_recovery_worker.py` | ✅ live |
| Ingest webhooks | `document_worker.py:692` (failure → webhook channel); `src/ragbot/infrastructure/notify/webhook_notifier.py:54-128` (quota-exhausted webhook + Redis SETNX throttle) | ⚠️ failure/quota only, no completion event |
| Google Workspace link normalization | `src/ragbot/application/services/google_link_service.py:212` (Sheets → `/export?format=csv`), `:218` (Docs → `/export?format=docx`), `:84-90` (host allowlist) | ✅ live (pull-once) |
| Sheets/CSV structured markdown (multi-subtable, header binding) | `src/ragbot/infrastructure/parser/google_sheets_parser.py:57-80` (section-bound `## title` + `| table |`, explicit rejection of row-1-as-global-header flatting) | ✅ live |
| Parallel upload endpoint | `src/ragbot/interfaces/http/routes/documents_stream_upload.py:1-8` — router explicitly **DISABLED / not mounted** ("Redis stream it XADDs to has no consumer… would silently drop"), retained per keep-test-code rule | ✅ contained (no data-loss path today) |

**FACT**: all rows above verified by direct file reads on 2026-07-02, branch `fix-260623-ingest-expert`.

---

## 2. PDF extraction quality — kreuzberg vs docling vs unstructured vs marker vs LlamaParse

### 2.1 What the 2025–2026 benchmarks say

**Procycons benchmark (fetched directly — FACT of the page's content)** — 5 corporate sustainability reports (Bayer 52pp/32 tables, DHL, Pfizer, Takeda, UPS), Docling vs Unstructured vs LlamaParse:
- **Docling: 97.9% cell accuracy on complex tables**, "100% accuracy for core content" text fidelity; 6.28 s for 1 page, 65.12 s for 50 pages.
- **Unstructured: 100% simple tables but only 75% complex structures**; slow (51.06 s for 1 page, 141 s for 50).
- **LlamaParse: ~6 s regardless of size** (cloud parallel), strong simple tables, struggles on complex layouts/multi-column.
- Verdict: Docling best overall for table-heavy corpora.
- URL: https://procycons.com/en/blogs/pdf-data-extraction-benchmark/

**opendataloader-bench, 200 PDFs (SNIPPET)** — ranking: hybrid-AI 0.909 > pdfmux 0.905 > **Docling 0.877 > marker 0.861** > opendataloader 0.852 > MinerU 0.831; LlamaParse/Unstructured unpublished on this bench.
- URL: https://pdfmux.com/blog/pdfmux-vs-llamaparse-vs-docling-vs-unstructured-2026/

**Kreuzberg's own cross-framework benchmark suite (SNIPPET)** — compares Kreuzberg / Docling / MarkItDown / Unstructured on speed, memory, success-rate, install size across 6 languages: **Kreuzberg ≈ 71 MB install vs Docling 1 GB+**; throughput spread "35 files/second to 60+ minutes per file" across frameworks; Kreuzberg positioned as the speed/footprint leader, not the table-structure leader.
- URLs: https://benchmarks.kreuzberg.dev/ · https://goldziher.github.io/python-text-extraction-libs-benchmarks/ · https://github.com/Goldziher/python-text-extraction-libs-benchmarks

**Table-extraction shootouts (SNIPPET)** — Docling ≥94% on most numeric/text tables with near-perfect row/column structure; marker good-but-below-docling on structure fidelity.
- URLs: https://codecut.ai/docling-vs-marker-vs-llamaparse/ · https://boringbot.substack.com/p/pdf-table-extraction-showdown-docling · https://llms.reducto.ai/document-parser-comparison

**LlamaParse v2 productized quality tiers (SNIPPET, vendor)** — Fast (1 credit/page) / Cost-Effective (3) / Agentic (10, ≈1.2¢/page, 84.9% on ParseBench) / Agentic-Plus (45, 1.25¢/page). The tier system — *pay more per page only for documents that need it* — is the key product pattern, independent of vendor.
- URLs: https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper · https://www.llamaindex.ai/blog/parsebench · https://developers.llamaindex.ai/llamaparse/general/pricing/

**Unstructured OSS status (SNIPPET)** — library still released regularly (0.18.24, 2026-01-05) but the company's push is the paid platform; no maintenance-mode announcement found.
- URLs: https://github.com/Unstructured-IO/unstructured · https://pypi.org/project/unstructured/

### 2.2 Mapping to ragbot

- **FACT**: ragbot default = kreuzberg (`ocr_factory.py:1-30`, `DEFAULT_PARSER_ENGINE="kreuzberg"`), Docling = opt-in engine (`ocr/docling_parser.py`, 167 lines, constructed directly on `parser_engine="docling"`).
- **Verdict (evidence-backed)**: kreuzberg-as-default is *validated* for a multi-tenant, CPU-bound, many-small-docs platform (install size, speed, fail-soft). Docling is *measurably better* on complex tables (97.9% procycons; 0.877 opendataloader) — and ragbot corpora are table-heavy (price sheets, spreadsheets).
- **Gap vs SOTA**: no per-bot/per-document **parser quality tier**. LlamaParse v2 proves the market wants tiered parsing; ragbot's config chain (`bots.plan_limits` > `system_config`) is exactly the right place to hang `parser_engine` per bot — today engine selection is process-global env (`RAGBOT_PARSER_ENGINE`, `ocr_factory.py:1-12`), so one tenant cannot opt into Docling-quality parsing without changing every tenant's engine. **HYPOTHESIS** (needs measurement): routing only table-dense PDFs to Docling would lift table-answer coverage at bounded latency cost; must be verified with ragbot's own eval harness before claiming any %.

---

## 3. Office formats — DOCX / XLSX / PPTX / CSV

### 3.1 SOTA landscape

- **Markdown-as-IR is consensus**: Microsoft **MarkItDown** (139k+ GitHub stars, converts Office/PDF/audio → markdown, "100 pages in 12 s, no GPU") is the mainstream lightweight path; Docling the high-fidelity path. URLs: https://github.com/microsoft/markitdown · https://realpython.com/python-markitdown/
- **DOCX**: mammoth (semantic docx→HTML; its own docs now say *markdown support deprecated — generate HTML then convert*) vs python-docx (element-level access, you map styles yourself). URLs: https://pypi.org/project/mammoth/ · https://github.com/microsoft/markitdown
- **XLSX**: guidance across sources is uniform — *don't blind-chunk spreadsheets*; preserve headers, sheet names, and sub-table boundaries. SpreadsheetLLM (Microsoft) formalizes spreadsheet encoding for LLMs; newer OSS (ks-excel-parser) preserves formulas/dependency graphs/merged regions with token-counted, citation-ready chunks. URLs: https://arxiv.org/html/2407.09025v1 · https://github.com/knowledgestack/excel-parser · https://www.useparagon.com/learn/what-to-know-about-ingesting-google-drive-data-for-rag/ ("for structured data like Google Sheets and Excel you may not want to chunk at all to keep column headers intact")
- **PPTX**: handled by the same converters (MarkItDown, kreuzberg, Docling); no dedicated 2025 benchmark of note surfaced — slide→markdown with heading preservation is the accepted contract.

### 3.2 Mapping to ragbot

- **FACT**: dedicated light parsers exist per format — `docx_parser.py` (145 L), `excel_openpyxl_parser.py` (125 L, row-as-chunk), `google_sheets_parser.py` (115 L); PPTX/HTML/PDF via `kreuzberg_markdown_parser.py:50-61`; the registry docstring codifies the split: kreuzberg for pdf/pptx/html, "lighter python-docx / openpyxl / csv parsers" for Office (`kreuzberg_markdown_parser.py:45`).
- **FACT**: `google_sheets_parser.py:57-80` implements exactly the SOTA spreadsheet guidance — section-bound structured markdown, multi-sub-table detection, explicit rejection of "row-1-as-global-header" flattening; `shared/tabular_markdown.py:214` centralizes row classification.
- **Assessment**: ragbot's Office handling is **at or above** the OSS mainstream (MarkItDown-class) and aligned with SpreadsheetLLM-direction research. No urgent gap. **Optional**: formula/merged-region preservation (ks-excel-parser-style) is a future differentiator, not a 2026 must.

---

## 4. OCR — vision-LLM parsing vs traditional OCR, and the routing pattern

### 4.1 SOTA landscape (moving fast, 2025→2026)

- **Two families**: traditional engines (Tesseract, PaddleOCR classic, Surya) — CPU-friendly, confidence scores; and OCR-specialist VLMs (DeepSeek-OCR 3B MoE with aggressive vision-token compression, olmOCR-2-7B from Qwen2.5-VL, Mistral OCR API, Granite-Docling-258M) — GPU-hungry, layout-native. URLs: https://modal.com/blog/8-top-open-source-ocr-models-compared · https://unstract.com/blog/best-opensource-ocr-tools/ · https://getomni.ai/blog/benchmarking-open-source-models-for-ocr
- **Leaderboard state (SNIPPET, largely vendor-self-reported)**: PaddleOCR-VL-1.6 claims OmniDocBench v1.6 composite 96.33; MinerU2.5-Pro 95.69; MinerU VLM engine 86.2 on v1.5; OCR Arena ELO: Mistral OCR v3 #11 (1523) > DeepSeek OCR #21 (1390). URLs: https://www.codesota.com/ocr · https://www.codesota.com/browse/computer-vision/document-parsing/omnidocbench · https://www.ocrarena.ai/compare/mistral-ocr-v3/deepseek-ocr · https://www.spheron.network/blog/best-open-source-ocr-vlm-self-host-gpu-cloud-2026/
- **Granite-Docling-258M (FACT of announcement)**: IBM's ultra-compact doc-conversion VLM plugging into Docling's pipeline as a `vlm_pipeline` option — the notable 2025-26 development for *self-hosted* structure-preserving OCR at tiny parameter count. URLs: https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion · https://huggingface.co/ibm-granite/granite-docling-258M · https://docling-project.github.io/docling/usage/vision_models/
- **The pattern that matters — confidence-based hybrid routing (SNIPPET, multiple independent sources)**: (1) check for an embedded text layer first (PyMuPDF/pdfplumber/pdf-inspector page classification) — born-digital pages skip OCR entirely; (2) scanned pages → fast CPU OCR; (3) **escalate pages with confidence 0.70–0.90 to a mid-tier VLM**, accept ≥0.90; hybrid routing reaches <400 ms/page. URLs: https://slavadubrov.github.io/blog/2026/03/04/ocr-guide/ · https://llms.reducto.ai/best-llm-ready-document-parsers-2025 · https://www.ahnafnafee.dev/blog/local-llm-pdf-ocr · https://arxiv.org/pdf/2605.18818 (microservice OCR+LLM production architecture)
- **Counterpoint** worth keeping: VLM "OCR" can *guess* plausibly instead of reading (visual-grounding failures documented) — a reason to keep HALLU=0-sensitive platforms on OCR-with-confidence + escalation rather than VLM-everything. URL: https://arxiv.org/pdf/2605.27750 · https://arxiv.org/pdf/2601.03714

### 4.2 Mapping to ragbot

- **FACT**: scanned PDFs go to kreuzberg+Tesseract (`kreuzberg_parser.py:4`); Docling is a config-selected alternative (`ocr_factory.py`); `VlmImageParser` exists but only fires for image MIMEs, explicitly selected by the worker (`registry.py:56-60`, `document_worker.py:165-200`).
- **Gap (the biggest T1 ingest gap found)**: **no router between the three engines**. A low-quality scan today produces low-quality Tesseract text silently — there is no per-page confidence signal captured, no escalation to Docling/Granite-Docling/VLM, and no `parse_quality` metadata on the document for later triage. This is precisely where 2026 SOTA moved.
- **Design note (fits existing architecture — HYPOTHESIS until built+measured)**: an `OcrRouterParser` strategy in the existing registry (Port+Strategy, config-driven thresholds from `system_config`) that (a) detects text-layer presence, (b) runs Tesseract with confidence capture, (c) escalates below-threshold pages to the configured VLM engine, satisfies zero-hardcode + DI rules with no orchestrator edits. HALLU=0 argues for *escalate-to-structure-preserving-OCR* (Granite-Docling) before *escalate-to-generative-VLM*.

---

## 5. HTML / web ingest

### 5.1 SOTA landscape

- **Benchmarks (SNIPPET)**: Trafilatura (Python) best mean F1 across an 8-dataset combined benchmark (**0.883**), up to **0.958 F1** on ScrapingHub; rs-trafilatura + MinerU-HTML fallback best WCXB held-out F1 (0.910). Jina ReaderLM-v2 (1.5B transformer) and commercial APIs (Firecrawl, Jina Reader) add JS rendering/anti-bot but are not systematically benchmarked by open suites. URLs: https://murroughfoley.com/web-content-extraction-benchmark/ · https://www.contextractor.com/trafilatura-vs-jina-readerlm/ · https://arxiv.org/pdf/2605.21097 (WCXB) · https://www.firecrawl.dev/blog/best-web-extraction-tools · https://blog.apify.com/jina-ai-vs-firecrawl/
- **Token economics (SNIPPET)**: stripping boilerplate HTML before LLM processing **cut tokens 97.9% without hurting extraction quality** (serp.fast guide). URL: https://serp.fast/guides/web-extraction-benchmarks

### 5.2 Mapping to ragbot

- **FACT**: `text/html` routes to `KreuzbergMarkdownParser` (`kreuzberg_markdown_parser.py:50-61`); grep for `trafilatura|readability|bs4` in `src/ragbot` = 0 hits (only false-positive word matches in comments) — **no boilerplate-removal stage exists**.
- **FACT**: worker already guards against one HTML failure mode — a Google-viewer HTML page parsed to empty text used to loop to DLQ (`document_worker.py:404` comment).
- **Gap**: ingesting a real-world web page today embeds nav/footer/cookie-banner text into chunks → retrieval noise + token waste. **Recommendation**: a `html_readability` parser strategy (trafilatura → markdown) registered ahead of kreuzberg for `text/html`; kreuzberg stays the fallback. One file + one registry row per the parser-adapter pattern. **HYPOTHESIS**: chunk-noise reduction must be measured with ragbot's eval harness before claiming a retrieval lift.

---

## 6. Google Workspace ingestion

### 6.1 SOTA landscape

- Export-URL normalization (Docs→DOCX/HTML, Sheets→CSV/XLSX) is the universal baseline; managed connectors differentiate on **incremental sync**: Google Drive **`changes` API with page tokens** for delta detection, per-file **revision history** as the change signal; LlamaIndex's "Live RAG over Google Drive" demo implements exactly load → detect-changed-docs-by-hash → re-upsert-only-changed. URLs: https://developers.google.com/workspace/drive/api/guides/change-overview · https://developers.llamaindex.ai/python/examples/ingestion/ingestion_gdrive/ · https://www.ragie.ai/blog/powering-your-rag-integrating-google-drive-for-seamless-knowledge-ingestion · https://docs.databricks.com/aws/en/ingestion/google-drive · https://www.useparagon.com/learn/what-to-know-about-ingesting-google-drive-data-for-rag/

### 6.2 Mapping to ragbot

- **FACT**: `google_link_service.py:212,218` normalizes shared links to export URLs (Sheets→CSV, Docs→DOCX) with a google.com host allowlist (`:84-90`); the DOCX export then flows into the normal registry (`DocxParser`) — i.e., **Google Docs ARE supported** via the canonical flow, not just Sheets. The Sheets parser consumes CSV-export bytes (`google_sheets_parser.py:4`).
- **Assessment**: matches the *baseline* industry pattern (public/shared-link export, one flow). Gaps vs managed-connector SOTA: (a) no OAuth Drive API access (private files), (b) no `changes`/revision-based incremental re-sync — re-ingest is caller-triggered. Given ragbot is a headless B2B platform where the *partner BE owns the source*, pull-on-demand + UPSERT-by-source_url (`ingest_core.py:387-415, 502-507`) is a defensible contract; a scheduled re-fetch of `source_url`-bearing docs (delta detected by the existing doc-level sha256) would close most of the freshness gap **without** any Google-specific API dependency. **HYPOTHESIS**: cheap to build since dedup/upsert already exists; needs a plan + quota policy.

---

## 7. Incremental re-ingest + dedup

### 7.1 SOTA landscape

- **LlamaIndex IngestionPipeline docstore-dedup (the reference OSS pattern)**: attach a docstore → duplicates found via `doc_id` + content hash; *hash changed → re-process + upsert; hash unchanged → skip*. URL: https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/
- **Research (SNIPPET)**: "Byte-Exact Deduplication in RAG: A Three-Regime Empirical Analysis" — byte-exact dedup captures the bulk of dedup benefit across public benchmarks (near-dup/MinHash adds marginal gains at real complexity cost). URL: https://arxiv.org/pdf/2605.09611
- Cache-per-(node,transformation)-hash for pipeline-step memoization is the second LlamaIndex idea worth knowing (skip re-chunk/re-embed when transformation inputs unchanged).

### 7.2 Mapping to ragbot

- **FACT — ragbot already implements the full reference pattern, arguably better-scoped**:
  - doc-level: per-bot sha256 exact dedup blocks duplicate inserts (`ingest_core.py:421-449`), test-pinned (`tests/unit/test_content_hash_dedup.py` per `ingest_core.py:424-428`);
  - identity-level: UPSERT by `(record_bot_id, source_url)` (`ingest_core.py:387-415, 502-507`);
  - chunk-level: on re-index, `{chunk_index: content_hash}` diff → only changed chunks re-embedded (`ingest_core.py:628-660`) — this is the docstore-upsert pattern at finer grain (embedding cost saved per chunk, not per doc);
  - API-level: `X-Idempotency-Key` replay (`documents.py:116-148`).
- **Assessment**: **at-SOTA**. Two refinements, both LOW: (a) chunk-diff keys on `chunk_index` — an inserted paragraph shifts all later indices and forces re-embed of the tail (content-hash-set matching would be positional-shift-proof; the arxiv 2605.09611 result suggests marginal value); (b) dedup is exact-only — per rule the platform should NOT do cross-bot dedup (tenant isolation), so near-dup is correctly out of scope.

---

## 8. Format-detection robustness (mime / ext / byte-sniff)

### 8.1 SOTA landscape

- Consensus: *"Filenames and Content-Type headers lie; magic numbers don't"* — validate content by signature, never trust user-declared MIME alone; libmagic (python-magic) carries a 4,000+ entry signature DB including hard cases (TTF-vs-OTF, MP3-vs-AAC frame bits). URLs: https://pypi.org/project/python-magic/ · https://codecut.ai/python-magic-file-type-detection/ · https://transloadit.com/devtips/secure-api-file-uploads-with-magic-numbers/
- **Polyglot files** (valid as two formats simultaneously, e.g. image+ZIP) defeat naive signature checks; hardened pipelines validate full structure or re-encode. URL: https://arxiv.org/pdf/2407.01529

### 8.2 Mapping to ragbot

- **FACT**: ragbot implements the layered scheme correctly and in the right ORDER (declared pair trusted first; sniff only rescues ambiguous/missing declarations — `registry.py:153-179`; sniff itself = `%PDF-` magic → OOXML `[Content_Types].xml` zip-manifest peek → kreuzberg detector long tail — `registry.py:123-150`; `AMBIGUOUS_DECLARED_MIMES` frozen at `mime_sniff.py:44-49`). The OOXML manifest peek is *stronger* than naive `PK\x03\x04` matching (distinguishes xlsx/docx/pptx properly) — many DIY pipelines get this wrong.
- **Gap (LOW-MED, security-flavored)**: no full-structure validation / no polyglot hardening; sniff trusts first-match. For a multi-tenant platform accepting partner-BE bytes, a malicious tenant's polyglot upload is parsed by whatever parser wins. Mitigation is cheap here because all parsers already run *full structural parses* (openpyxl/python-docx/pypdfium2 reject malformed containers loudly) — the residual risk is mostly HTML-in-something confusion. **HYPOTHESIS**: adding python-magic as a second opinion (log-only divergence event first, enforce later) would quantify how often declared/sniffed/libmagic disagree in production before any behavior change.

---

## 9. Async ingest job architecture (queues, status, webhooks)

### 9.1 SOTA landscape

- **Async request-reply**: 202 Accepted + status resource (`GET /jobs/{id}` with PENDING→PROCESSING→DONE/ERROR) is the canonical pattern (Azure Architecture Center). URLs: https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply · https://zuplo.com/learning-center/asynchronous-operations-in-rest-apis-managing-long-running-tasks · https://dev.to/damikaanupama/designing-asynchronous-apis-with-a-pending-processing-and-done-workflow-4gpd
- **Do-not-work-in-the-request**: verify signature → dedupe on event ID → persist to durable queue → return 202 immediately → process from queue.
- **Webhook reliability 2026 reference**: registry of callback URLs per job, signed payloads, delivery with retries + idempotency keys, DLQ for undeliverable. URL: https://www.digitalapplied.com/blog/webhook-reliability-idempotency-retries-engineering-reference-2026
- Managed ingest products (LlamaCloud, Unstructured Platform) expose exactly: async parse job → poll or webhook → typed result artifact. URL: https://developers.api.llamaindex.ai/

### 9.2 Mapping to ragbot

- **FACT**: ragbot implements the queue side fully — enqueue at API, Redis Streams consumer, `running/success/failed` transitions (`document_worker.py:260, 655-707`), transient-vs-permanent error split for retry/DLQ (`:117`, `:404`), a recovery worker, per-job StepTracker observability U1–U7 (`:262-328`), Prometheus counter (`:685, :727`), and poll status at `jobs.py:16-17`.
- **Gap (MED, T2/partner-UX)**: **no success/completion webhook** — webhooks exist only for ingest *failure* (`document_worker.py:692`) and quota exhaustion (`webhook_notifier.py:54`). Partner BEs must poll. The full multi-tenant webhook design (tenant_webhooks + webhook_deliveries + doc state machine, 21 control points) was already designed 2026-05-12 and consciously deferred (~20 h) — this research confirms that design matches the 2026 reference pattern (signed, retried, idempotent deliveries) and nothing in SOTA has moved past it.
- **FACT (compliance)**: the forbidden parallel upload path is neutralized — `documents_stream_upload.py:1-8` documents itself DISABLED/not-mounted because its stream had no consumer (the exact data-loss CLAUDE.md warns about). Canonical-single-funnel holds.

---

## 10. Prioritized recommendations (mapped to ragbot's single-canonical-ingest design)

All go through the existing funnel — none adds a parallel path. Every item = new registry strategy or config key; zero orchestrator edits (parser-adapter-pattern).

| # | Rec | Tier | Effort | Evidence anchor |
|---|---|---|---|---|
| R1 | **OCR confidence router**: text-layer check → Tesseract w/ confidence capture → escalate low-confidence pages to Docling/Granite-Docling (structure-preserving before generative); store `parse_quality` in doc metadata | T1 | M (1 strategy + config thresholds) | Gap §4.2; SOTA https://slavadubrov.github.io/blog/2026/03/04/ocr-guide/ |
| R2 | **Per-bot parser tier**: lift `parser_engine` from process-global env (`ocr_factory.py:1-12`) into the bot-limit resolve chain (`shared/bot_limits.py` pattern) so a paid tenant can opt into Docling-quality parsing — the LlamaParse-v2 tier pattern, self-hosted | T1/T2 | S-M | §2.2; https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper |
| R3 | **`html_readability` strategy** (trafilatura→markdown) registered ahead of kreuzberg for `text/html`; kreuzberg fallback | T1 | S (1 file + 1 registry row) | §5.2; trafilatura F1 0.883–0.958 |
| R4 | **Completion webhook**: ship the already-designed tenant_webhooks Phase 2 (success event + signed retried delivery) — poll-only is below 2026 partner-API bar | T2 | M (design exists) | §9.2; https://www.digitalapplied.com/blog/webhook-reliability-idempotency-retries-engineering-reference-2026 |
| R5 | **Scheduled re-fetch of source_url docs** (freshness loop): reuse existing sha256 dedup to no-op unchanged fetches; covers the Google-Drive-changes gap generically, no Google API dependency | T2 | S-M | §6.2; https://developers.llamaindex.ai/python/examples/ingestion/ingestion_gdrive/ |
| R6 | **libmagic second-opinion (log-only)**: emit divergence event when declared vs sniffed vs libmagic disagree; measure before enforcing; revisit polyglot hardening with data | T2/sec | S | §8.2; https://arxiv.org/pdf/2407.01529 |
| R7 | **Do NOT**: adopt Unstructured (75% complex-table, slow, platform-first vendor); rewrite to VLM-everything (visual-grounding hallucination risk vs HALLU=0 — https://arxiv.org/pdf/2605.27750); add near-dup/MinHash dedup (marginal per https://arxiv.org/pdf/2605.09611); build Google-specific OAuth connector before R5 measures demand | — | — | §§2,4,7 |

**Verification obligations (rule #0)**: R1–R3 carry NO lift numbers here — any claimed % must come from ragbot's own eval harness (Coverage + Faithfulness per load-test gate) after implementation. Benchmarks cited describe *other* corpora.

---

## 11. Full source list

**PDF benchmarks**: https://procycons.com/en/blogs/pdf-data-extraction-benchmark/ · https://pdfmux.com/blog/pdfmux-vs-llamaparse-vs-docling-vs-unstructured-2026/ · https://codecut.ai/docling-vs-marker-vs-llamaparse/ · https://boringbot.substack.com/p/pdf-table-extraction-showdown-docling · https://llms.reducto.ai/document-parser-comparison · https://github.com/applied-artificial-intelligence/pdf-parser-benchmark · https://arxiv.org/pdf/2604.12047 · https://arxiv.org/pdf/2509.04469
**Kreuzberg**: https://benchmarks.kreuzberg.dev/ · https://goldziher.github.io/python-text-extraction-libs-benchmarks/ · https://github.com/Goldziher/python-text-extraction-libs-benchmarks · https://dev.to/nhirschfeld/announcing-kreuzberg-v20-a-lightweight-modern-python-text-extraction-library-4ca4
**OCR / VLM**: https://www.codesota.com/ocr · https://www.codesota.com/browse/computer-vision/document-parsing/omnidocbench · https://www.ocrarena.ai/compare/mistral-ocr-v3/deepseek-ocr · https://modal.com/blog/8-top-open-source-ocr-models-compared · https://getomni.ai/blog/benchmarking-open-source-models-for-ocr · https://unstract.com/blog/best-opensource-ocr-tools/ · https://www.spheron.network/blog/best-open-source-ocr-vlm-self-host-gpu-cloud-2026/ · https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion · https://huggingface.co/ibm-granite/granite-docling-258M · https://docling-project.github.io/docling/usage/vision_models/ · https://slavadubrov.github.io/blog/2026/03/04/ocr-guide/ · https://www.ahnafnafee.dev/blog/local-llm-pdf-ocr · https://arxiv.org/pdf/2605.18818 · https://arxiv.org/pdf/2605.27750 · https://arxiv.org/pdf/2601.03714
**Office**: https://github.com/microsoft/markitdown · https://realpython.com/python-markitdown/ · https://pypi.org/project/mammoth/ · https://arxiv.org/html/2407.09025v1 · https://github.com/knowledgestack/excel-parser · https://pypi.org/project/openpyxl/
**HTML/web**: https://murroughfoley.com/web-content-extraction-benchmark/ · https://www.contextractor.com/trafilatura-vs-jina-readerlm/ · https://arxiv.org/pdf/2605.21097 · https://www.firecrawl.dev/blog/best-web-extraction-tools · https://blog.apify.com/jina-ai-vs-firecrawl/ · https://serp.fast/guides/web-extraction-benchmarks
**Google Workspace**: https://developers.google.com/workspace/drive/api/guides/change-overview · https://developers.llamaindex.ai/python/examples/ingestion/ingestion_gdrive/ · https://www.ragie.ai/blog/powering-your-rag-integrating-google-drive-for-seamless-knowledge-ingestion · https://docs.databricks.com/aws/en/ingestion/google-drive · https://www.useparagon.com/learn/what-to-know-about-ingesting-google-drive-data-for-rag/
**Dedup / incremental**: https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/ · https://arxiv.org/pdf/2605.09611
**Type detection / security**: https://pypi.org/project/python-magic/ · https://codecut.ai/python-magic-file-type-detection/ · https://transloadit.com/devtips/secure-api-file-uploads-with-magic-numbers/ · https://arxiv.org/pdf/2407.01529
**Async API / webhooks**: https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply · https://zuplo.com/learning-center/asynchronous-operations-in-rest-apis-managing-long-running-tasks · https://dev.to/damikaanupama/designing-asynchronous-apis-with-a-pending-processing-and-done-workflow-4gpd · https://www.digitalapplied.com/blog/webhook-reliability-idempotency-retries-engineering-reference-2026 · https://developers.api.llamaindex.ai/
**LlamaParse / Unstructured product state**: https://www.llamaindex.ai/blog/introducing-llamaparse-v2-simpler-better-cheaper · https://www.llamaindex.ai/blog/parsebench · https://developers.llamaindex.ai/llamaparse/general/pricing/ · https://github.com/Unstructured-IO/unstructured · https://pypi.org/project/unstructured/
