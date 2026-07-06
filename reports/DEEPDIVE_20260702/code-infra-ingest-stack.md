# DEEPDIVE — Infra Ingest Stack (parser / ocr / cache / vector / retrieval_fallback / metadata_filter)

- Date: 2026-07-02
- Scope: `src/ragbot/infrastructure/{parser,ocr,cache,vector,retrieval_fallback,metadata_filter}` (37 files, 5,452 LOC) + load-bearing shared helpers (`shared/tabular_markdown.py`, `shared/mime_sniff.py`) and wiring call-sites read for evidence.
- Method: every file read line-by-line; claims runtime-verified where possible (venv probes against installed libs, live dev-DB schema queries). Each claim labeled **FACT** (evidence attached) or **HYPOTHESIS** (needs runtime verification).

---

## PART 1 — File-by-file: what it does + pipeline connection

### 1.1 `infrastructure/parser/` — the registry (structured-markdown) ingest path

| File | Purpose | Pipeline connection |
|---|---|---|
| `registry.py` (187) | `_REGISTRY` of 8 parser strategies; `build_parser` (fail-soft → NullParser), `detect_parser` (first `supports()` match wins), `_sniff_mime` (PDF magic → OOXML manifest → kreuzberg detector), `detect_parser_robust` (declared mime/ext first, byte-sniff on miss) | `DocumentService._route_through_parser` (`document_service/__init__.py:241,820`) and `document_worker.py:456` both call `detect_parser_robust` — this is the canonical U2 parse dispatch |
| `kreuzberg_markdown_parser.py` (174) | PDF/PPTX/HTML/XHTML → ONE structured-markdown block via `kreuzberg.extract_bytes_sync` + `ExtractionConfig(output_format=MARKDOWN)`; size cap `DEFAULT_PDF_MAX_BYTES`; `asyncio.to_thread` offload; fail-loud ValueError wraps | Registered before `pdf` → wins for PDF; the current default PDF/PPTX/HTML path. **Runtime-verified WORKING** (HTML → `# Title` + pipe table, see §2.1) |
| `docx_parser.py` (145) | python-docx; walks body in document order (paragraphs + tables interleaved), headings 1-3 → `#`, tables → `rows_to_structured_markdown`; wraps corrupt-file errors into ValueError; cap `DEFAULT_DOCX_MAX_BYTES` | Registry key `docx`; emits ONE markdown doc |
| `excel_openpyxl_parser.py` (125) | openpyxl read-only; each sheet → `rows_to_structured_markdown`, multi-tab → `# <sheet>` headings; then `split_markdown_to_row_chunks` → row-as-chunk | Registry key `excel_openpyxl` (only `.xlsx`) |
| `google_sheets_parser.py` (115) | CSV bytes (Sheets export or plain CSV) → decode (utf-8 → latin-1 fallback) → `csv.reader` → `rows_to_structured_markdown` → row-as-chunk | Registry key `google_sheets`; claims mimes `application/vnd.google-apps.spreadsheet`, `text/csv`, ext `.csv` |
| `markdown_parser.py` (130) | Strips YAML front matter, splits on H1/H2 into section chunks; **also owns `text/plain` + `.txt`** (documented degrade) | Registry key `markdown` |
| `pdf_parser.py` (122) | Legacy pypdfium2 per-page extraction (`## Page N` headers), module-level semaphore `DEFAULT_PDF_PARSE_CONCURRENCY`, careful native-handle release | Registry key `pdf` — now shadowed by `kreuzberg_markdown` for PDFs (only reachable if kreuzberg uninstalled) |
| `vlm_image_parser.py` (119) | Image → base64 data-URL → injected vision LLM caption (LLMPort + LLMSpec, fail-loud on non-vision spec) | NOT auto-detected (`detect_parser`'s no-arg probe raises TypeError → skipped); worker builds it explicitly for image MIMEs when VLM enabled (`document_worker.py:426-431`) |
| `null_parser.py` (34) | Null Object; `supports()` False, `parse()` raises NotImplementedError | Registry fail-soft target |

### 1.2 `infrastructure/ocr/` — the OCR/blocks fallback path

| File | Purpose | Pipeline connection |
|---|---|---|
| `ocr_factory.py` (98) | Reads `RAGBOT_PARSER_ENGINE` env (default `DEFAULT_PARSER_ENGINE="kreuzberg"`); kreuzberg → fallback SimpleTextParser **only on ImportError**; docling/simple explicit; unknown token fail-loud | `bootstrap.py:322` `ocr = providers.Singleton(build_ocr_parser)` |
| `kreuzberg_parser.py` (326) | OCRPort adapter: bytes/URL → `kreuzberg.extract_bytes` → elements → typed `Block` list (HEADING/TABLE/FORMULA/IMAGE/CODE/LIST/TEXT), heading context threading, atomic flags | Reached at `document_worker.py:494-495`: `ocr = container.ocr(); parsed = await ocr.parse(source_url, ...)` when the registry produced no text and source is refetchable. **Runtime-verified BROKEN — returns 0 blocks always** (§2.1, F-1) |
| `simple_text_parser.py` (549) | Heuristic block builder: pipe/tab/space table detection, heading detection (MD `#`, **hardcoded VN markers** Phần/Chương/Mục/Điều/Khoản/Tiết, ALL-CAPS, numbered), DOCX via python-docx, PDF via pypdfium2, HTML via stdlib HTMLParser | Fallback engine when kreuzberg lib missing; also explicit `RAGBOT_PARSER_ENGINE=simple` |
| `docling_parser.py` (167) | Docling OCRPort adapter (tempfile + DocumentConverter, label → BlockType) | Opt-in only; **docling not installed** (runtime-verified ModuleNotFoundError) → currently unselectable |
| `__init__.py` | exports SimpleTextParser | — |

### 1.3 `infrastructure/cache/`

| File | Purpose | Pipeline connection |
|---|---|---|
| `semantic_cache.py` (600) | `PgSemanticCache`: 2-tier (exact SHA256 hash → pgvector cosine ≥ threshold); single-flight stampede protection (Redis SETNX cross-process + in-process asyncio.Lock via WeakValueDictionary); `store()` INSERT with TTL + chunks snapshot (first 8) | Read: `nodes/check_cache.py:96` `find_similar_with_text(...)`. Write: `nodes/persist.py:95` fire-and-forget with config `cache_ttl`. Invalidation: `DELETE FROM semantic_cache WHERE record_bot_id=...` on re-ingest (`document_service/__init__.py:971,1015,1076`); expired-row purge in `embedded_workers.py:193` |
| `redis_cache.py` (127) | `RedisCache` (CachePort) + two client factories: short-op client (sub-second timeouts) vs Streams client (30s socket_timeout > XREADGROUP block) | DI container; narrow-exception wrapped in `CacheError` |
| `embed_cache.py` (112) | Query-embedding Redis cache, model-scoped key `ragbot:embed:{model}:{sha16}` — deliberately cross-bot (same text+model = same vector); silent degrade | Retrieval/embedding pipeline via DI |
| `understand_query_cache.py` (109) | Memoises understand_query LLM output; key `ragbot:uq:v{prompt_version}:{record_bot_id}:{sha16(query[:300])}` — bot-scoped (record_bot_id is the unique internal PK, per CLAUDE.md identity rule); silent degrade | understand_query node |

### 1.4 `infrastructure/vector/`

| File | Purpose | Pipeline connection |
|---|---|---|
| `pgvector_store.py` (621) | `search` (HNSW cosine), `hybrid_search` (dense + tsvector BM25 approx fused by weighted RRF in one CTE; VN NFC-normalize + compound segmentation + filler-strip + diacritic-removal on the sparse arm; symbol-phrase OR-branch; VN structural LIKE pre-filter with graceful no-match retry), `upsert_chunks`/`delete_by_document`/`delete_by_tool_name`/`count`; all reads/writes through `session_with_tenant` (`SET LOCAL app.tenant_id`, engine.py:174); embedding-column whitelist gate | Retrieve node's primary retrieval backend (via `build_vector_store` DI). NOTE: **`upsert_chunks` has zero callers** — real ingest writes SQL in `ingest_helpers.py:238` (§2.5) |
| `null_vector_store.py` (127) | Null Object mirroring the full method surface (searches → [], upserts → 0) | Registry fail-soft |
| `registry.py` (100) | `pgvector`/`postgres`/`null`; kwargs filtered to ctor signature; init failure → NullVectorStore | `bootstrap.py:262` |

### 1.5 `infrastructure/retrieval_fallback/` — Stream S8 multi-stage chain (default OFF)

| File | Purpose | Pipeline connection |
|---|---|---|
| `registry.py` (101) | 4 stages + null; fail-soft | `nodes/retrieve.py:1536-1562` builds each configured stage per turn when `retrieval_multistage_enabled` and (0 chunks or top score < `DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD`=0.35) |
| `hybrid_stage1.py` (105) | Wraps `vector_store.hybrid_search`; signature-probes backend; **threads `record_tenant_id`** through | Stage 1 |
| `bm25_only_stage2.py` (122) | tsvector-only SQL over `document_chunks JOIN documents` (bot-scoped) via **plain** `session_factory()` | Stage 2 — the “embedder dead” fallback |
| `keyword_stage3.py` (149) | Regex anchor (default `DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN`, VN structural; per-bot overridable) → ILIKE lookup; static score 0.25 < early-exit 0.35 by design | Stage 3 |
| `parent_expand_stage4.py` (135) | Appends parents of chunks carrying `parent_chunk_id`; anchors parent score at prior max | Stage 4 — **provably a no-op today** (§2.4) |
| `null_stage.py` (54) | Pass-through | disable-one-stage knob |

### 1.6 `infrastructure/metadata_filter/`

| File | Purpose | Pipeline connection |
|---|---|---|
| `registry.py` (97) | `null` (default) / `article_aware` / `generic_llm` | `bootstrap.py:417-420` resolves per-call from `system_config.metadata_filter_provider` |
| `article_aware_filter.py` (185) | Operator-supplied regex patterns (`system_config.article_ref_patterns`) → `{<name>_no: value}` containment dict; flag whitelist (IGNORECASE only); malformed entries skipped | retrieve node → `metadata_filter` arg → `PgVectorStore._doc_filter_sql` JSONB containment |
| `generic_llm_extractor.py` (222) | LLM (raw litellm module injected) extracts entities/topics/keywords → pydantic-validated → `{"entities":[...≤3 lowercased]}` filter; bounded timeout; every failure → `{}` | Layer 3, default OFF (`DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED=False`); soft-imported at `nodes/retrieve.py:142-148` |
| `llm_metadata_cache.py` (90) | Redis memo keyed `ragbot:metadata_extract:v1:{locale}:{sha32}` | wraps the above |
| `null_filter.py` (34) | `extract() → {}` | default |

---

## PART 2 — Findings (evidence-first, ranked)

### F-1 · CRITICAL — Kreuzberg OCR adapter calls an async API synchronously → OCR fallback returns 0 blocks for EVERY document

**FACT (runtime-verified).** `kreuzberg_parser.py:238` fetches `kreuzberg.extract_bytes` and `kreuzberg_parser.py:258` calls it synchronously inside `run_in_executor`:

```python
result = extract_bytes(data, mime_type_arg)     # kreuzberg_parser.py:258
```

Installed lib: **kreuzberg 4.9.7**, where `inspect.iscoroutinefunction(kreuzberg.extract_bytes) == True` (the sync variant is `extract_bytes_sync` — the one `KreuzbergMarkdownParser` correctly uses). The call therefore returns an un-awaited coroutine; `getattr(result, "elements", None)` → None (`:270-274`) → `blocks=[]`, `page_count=0`.

Live probe in the project venv:

```
RuntimeWarning: coroutine 'extract_bytes' was never awaited
kreuzberg_parse_done  block_count=0 atomic_count=0 page_count=0 language=vie+eng
blocks: 0 page_count: 0
```

**Failure scenario (concrete):** any ingest that misses the parser registry — image uploads with VLM disabled, legacy `.doc`/`.xls`/`.ppt`, unknown formats — reaches `document_worker.py:494-495` (`ocr = container.ocr(); parsed = await ocr.parse(...)`), gets 0 blocks, `full_text=""`, and dies at `RuntimeError("empty document text after parse")` (worker ~line 510) → retry/DLQ. The degrade contract in `ocr_factory.py:56-73` only catches **ImportError at construction**, so this runtime emptiness never falls back to `SimpleTextParser` — the "graceful degradation" documented in the module docstring cannot trigger for this failure mode.

**Compounding defects in the same function (FACT):**
- The modern (positional) call passes **no `ExtractionConfig`** — `ocr_language` (`vie+eng`) is only passed in the legacy `TypeError` fallback (`kreuzberg_parser.py:262-268`). On kreuzberg ≥4 the configured Vietnamese OCR language would never reach Tesseract even after the async bug is fixed; the telemetry `language=self._ocr_language` (`:196`) reports a value the engine never received.
- **Test-health / mock-drift:** `tests/unit/test_kreuzberg_parser.py:60` installs a fake module with a **sync** `def fake_extract_bytes(data, **kwargs)`; the modern positional call `extract_bytes(data, mime)` even TypeErrors against that fake (2nd positional arg), driving the adapter into the legacy branch — so the suite green-lights exactly the code path that is dead against the real library.

**Expert fix (right layer):** use `kreuzberg.extract_bytes_sync(data, mime, ExtractionConfig(...))` (same as the working markdown parser), pass OCR/tesseract language via config, and add one **un-mocked** smoke test against the installed lib (or a contract test asserting `not iscoroutinefunction(extract_bytes)` before sync use).

### F-2 · HIGH — Declared first-class formats `.doc` / `.xls` / `.ppt` (and `.tsv`, `.json`) have NO parser; combined with F-1 they cannot be ingested at all

**FACT (runtime probe).**

```
.doc  (application/msword)          -> None
.xls  (application/vnd.ms-excel)    -> None
.ppt  (application/vnd.ms-powerpoint)-> None
.tsv -> None    .json -> None
```

`registry.py:45-61` registers only OOXML/PDF/HTML/MD/CSV/image strategies; `mime_sniff.py` has no OLE2 (`D0 CF 11 E0`) branch. CLAUDE.md declares “**PDF · DOCX/DOC · XLSX/XLS · CSV · Google Sheets/Docs · PPTX · HTML · TXT · MD**” first-class. A legacy Office upload today: registry miss → OCR fallback (F-1, returns 0 blocks) → `RuntimeError("empty document text after parse")`. Even with F-1 fixed, kreuzberg’s support for OLE2 legacy formats needs verification — nothing in-tree covers it. Byte-sniffed octet-stream PDF is handled correctly (probe: `octet-pdf -> KreuzbergMarkdownParser`).

### F-3 · HIGH — Fallback stages 2–4 open plain sessions: tenant GUC never set (RLS) and soft-deleted documents are retrievable

**FACT.** `nodes/retrieve.py:1575` deliberately threads `record_tenant_id` into every stage (“mega-sprint-G1: thread tenant for RLS-enforced runtime DSN”). Stage 1 forwards it into `PgVectorStore.hybrid_search` → `session_with_tenant` (`hybrid_stage1.py:94-95`). But:

- `bm25_only_stage2.py:86`, `keyword_stage3.py:112`, `parent_expand_stage4.py:81` all use `async with session_factory() as session:` — the `record_tenant_id` kwarg is swallowed by `**kwargs` and never used; no `SET LOCAL app.tenant_id` (`engine.py:174`) is executed.
  - Under an RLS-enforced runtime role (the configuration the comment describes), these stages silently return **0 rows every time** — 3 of the 4 fallback stages are dead exactly in the deployments RLS was built for.
  - Under a policy-bypassing role, they run without the tenant defence-in-depth layer (isolation still holds via `record_bot_id`, but single-layer).
- **Soft-delete leak:** stage 2 (`bm25_only_stage2.py:78-80`) and stage 3 (`keyword_stage3.py:102-105`) filter only `d.record_bot_id = :rbid` — no `doc_deleted_at IS NULL` / `documents.deleted_at IS NULL` guard, unlike the main path (`pgvector_store.py:270`). **Failure scenario:** owner soft-deletes a document; multistage is ON; a query where stage 1 scores below 0.35 falls to stage 2 → the deleted document’s chunks return and are answered from. Deleted content resurrection.
- **Asymmetric tokenization:** stage 2 feeds the raw query to `websearch_to_tsquery('simple', :query)` (`bm25_only_stage2.py:75,80`) while ingest indexes `content_segmented` (VN compounds joined by `_`); the main hybrid path mirrors that with `segment_vi_compounds(query_text)` (`pgvector_store.py:404`). The “embedder-dead” BM25 fallback therefore under-matches VN compound terms — exactly the scenario it exists for.

### F-4 · HIGH — `parent_chunk_id` is never SELECTed by any retrieval SQL → three shipped features are permanent no-ops

**FACT.** Ingest writes `parent_chunk_id` as a **column** (`ingest_stages_store.py:919`, `ingest_helpers.py:190`; DB-verified: `document_chunks.parent_chunk_id uuid NULL`). But no retrieval SQL returns it: `pgvector_store.search` (`:331`), `hybrid_search` CTEs (`:523,:532,:553`), `bm25_only_stage2.py:72`, `keyword_stage3.py:100` — none select the column, and the result-dict builders (`pgvector_store.py:340-350`, `:581-592`) never set the key. Repo-wide grep: nothing lifts `parent_chunk_id` onto retrieved chunk dicts. Consequences:

1. `parent_child_enabled` inline swap — `nodes/retrieve.py:1770-1772` `c.get("parent_chunk_id")` is always None → `child_ids_with_parent == []` → never expands.
2. `ParentExpandStage4Retriever` — `parent_expand_stage4.py:62-66` finds no parent ids → always returns the prior result (“parent_expand_stage4_no_parent_links”). The 4th configured default stage (`DEFAULT_RETRIEVAL_STAGES`, constants `_12:16-21`) is inert.
3. `shared/auto_merge_retrieval.py:102` groups by the same missing key → merge never fires.

Built-but-not-wired at the SELECT layer; a one-column addition to the retrieval SELECTs + dict builders would light up all three.

### F-5 · HIGH — `PgVectorStore.upsert_chunks` is a dead write API that would violate NOT NULL and produce invisible chunks if ever used

**FACT.** No call-site exists anywhere (`grep upsert_chunks` → only `vector_store_port.py:59` and the two implementations); the real ingest writes its own SQL in `ingest_helpers.py:238` including `record_bot_id`, `content_segmented`, `chunk_chars`, `chunk_type`, `chunk_context`. The port implementation `pgvector_store.py:140-150` inserts only `(record_document_id, chunk_index, content, content_hash, <embedding>, metadata_json)`:

- DB-verified: `document_chunks.record_bot_id` is **NOT NULL** and `chunk_type` **NOT NULL** → the INSERT raises NotNullViolation the moment anyone calls the port method.
- Even with defaults, missing `record_bot_id` makes the rows invisible to every search (all reads filter `record_bot_id = :record_bot_id` on the chunk row, `pgvector_store.py:258`), and missing `content_segmented` blinds the BM25 arm.

The canonical Port write contract is broken while the application layer bypasses the port with raw SQL — inverse of the Strategy+DI rule (T3) and a landmine for any future caller (e.g. a second vector backend implementing the port faithfully will never be fed by ingest).

### F-6 · HIGH — Multi-bot embedding dimensionality is locked to one global 1280-dim column; the per-bot `embedding_column` plumbing is config theater

**FACT.** `ALLOWED_EMBEDDING_COLUMNS = {"embedding"}` (constants `_02:85-89`); DB-verified `document_chunks.embedding = vector(1280)` and `semantic_cache.query_embedding = vector(1280)`; the cache mapper hard-returns the single column (`semantic_cache.py:58-65`). Every `embedding_column=` parameter across pgvector_store / semantic_cache / stages / state accepts exactly one legal value.

**Failure scenario:** a tenant binds a bot to any embedder whose output dim ≠ 1280 (e.g. OpenAI text-embedding-3-small at native 1536) — ingest `CAST(:emb AS vector)` into `vector(1280)` fails with a dimension error; semantic-cache `store()` likewise. Per-bot embedding bindings only work for models coerced to exactly 1280 (matryoshka `dimensions:1280`). Nothing validates this at binding time; the failure surfaces as ingest-time DB errors. Also: `PgVectorStore.__init__` takes `dimension: int = DEFAULT_RERANKER_EMBEDDING_DIM` (=1024, a **reranker** constant that matches neither the column nor any embedder) and stores it in `self._dimension` which is **never read** (`pgvector_store.py:106-109`) — dead parameter with a misleading name.

### F-7 · HIGH — CSV/Sheets happy-case-only decode and error typing: UTF-16 CSV crashes ingest unhandled; private-Sheet HTML ingests as garbage

**FACT (code-level):**
- `google_sheets_parser.py:36-44` `_decode_csv`: utf-8 → latin-1(replace). A UTF-16 CSV (Excel “Unicode Text” export is UTF-16LE) decodes via latin-1 into NUL-riddled text; `csv.reader` then raises `csv.Error("line contains NUL")` (`:83`). `csv.Error` is neither ValueError nor NotImplementedError, so the ingest guard `ingest_core.py:326 except (ValueError, NotImplementedError)` does **not** catch it → unhandled exception up the ingest call stack instead of the documented degrade. Vietnamese cp1258/legacy-encoded CSVs silently ingest as mojibake (latin-1 replace) — wrong content with no warning.
- No HTML guard: the worker rewrites Sheets viewer URLs to `export?format=csv` and stamps `mime="text/csv"` (`document_worker.py:405-410`). A **private** sheet returns an HTTP-200 HTML login page; `GoogleSheetsParser` happily converts HTML markup lines into “sections”/“notes” (`:79-104`) → garbage corpus rows ingested silently (HYPOTHESIS for the exact Google response shape; FACT that the parser has no `<html`/`<!doctype` rejection while `mime_sniff` — not consulted here because the declared mime is non-ambiguous — knows how to detect HTML).

### F-8 · MEDIUM — Excel parser: no size cap, no corrupt-file wrap (parity gap with every sibling parser)

**FACT.** `excel_openpyxl_parser.py:73-75` calls `load_workbook(BytesIO(content))` with (a) **no max-bytes guard** — PDF caps at `DEFAULT_PDF_MAX_BYTES` (`pdf_parser.py:70`), DOCX at `DEFAULT_DOCX_MAX_BYTES` (`docx_parser.py:76`), MD at `DEFAULT_MARKDOWN_MAX_BYTES` (`markdown_parser.py:95`) — a 300MB xlsx OOMs the worker; and (b) **no BadZipFile/InvalidFileException → ValueError wrap** — DocxParser does this at `docx_parser.py:84-89`. A corrupt `.xlsx` raises `zipfile.BadZipFile`, again missing the `ingest_core.py:326` catch → unhandled ingest failure instead of the “degrade to raw” path.

### F-9 · MEDIUM — Tabular structure state machine keys on Vietnamese money vocabulary; docstring claims “no vocabulary”

**FACT.** `shared/tabular_markdown.py:43-46` `_MONEY_UNIT_RE = (triệu|trieu|nghìn|nghin|ngàn|ngan|vnd|tr|đ|k|m)` + `parse_money_vn` drive `_is_pure_money` (`:60-72`), which decides HEADER vs DATA vs SECTION (`:93-102`, `:265-286`) and gates the merged-cell forward-fill: `_normalize_rows` only fills from rows where `_has_money(r)` (`:203`). This is the exact “single-language word list in a structure-deciding path” the multilingual-no-vocab principle bans, and the module docstring (`:9-10` “DOMAIN-NEUTRAL … no service/brand vocabulary”) overstates neutrality.

Mitigation observed: bare grouped numbers also parse as money (`parse_money_vn` step 3, `number_format.py:192-196`), so most numeric sheets in any locale still classify. **Failure scenarios that remain:** (a) a text-only catalog sheet (no numeric cells) never triggers forward-fill → merged/rowspan group labels stay empty on continuation rows → rows bind to the wrong group; (b) prices written as `USD 1,499` (letter residue) are treated as NAME cells → a priced row can pass `_looks_header` and be promoted to a table header, mislabeling all following rows.

### F-10 · MEDIUM — Data cells beyond the (trimmed) header width are silently dropped

**FACT.** `tabular_markdown.py:230-231` trims trailing **empty** header cells, then the DATA emitter `:339-341` truncates every row to `len(header)`:

```python
vals = [(cells[k] if k < len(cells) else "") for k in range(len(header))]
```

**Failure scenario:** sheet header `["Tên","Giá",""]` (last column unnamed — common for a notes column) → header trimmed to 2 → every data row’s 3rd cell (“ghi chú khuyến mãi…”) is discarded from the markdown, the chunk, the embedding, and BM25. Silent per-cell data loss with zero log. A safer emit: pad the header (`colN`) to the max observed row width instead of truncating rows.

### F-11 · MEDIUM — `check_cache` skips the exact-hash cache tier whenever the embedder is down, defeating a designed degrade

**FACT.** `nodes/check_cache.py:89-95` returns early (`reason="no_embedding"`) before calling `find_similar_with_text` when `query_embedding` is empty. The cache itself supports hash-only lookups (`semantic_cache.py:410-459`; slow path guards `if not query_embedding: return None` at `:462-463`), and `persist.py:84-91` deliberately stores numeric answers with NULL embeddings precisely to serve exact-hash hits. **Failure scenario:** embedding provider outage → every request pays full pipeline cost even for byte-identical repeat queries whose answers sit in cache — the aux-dependency graceful-degradation rule inverted (embedder outage disables a tier that doesn’t need the embedder).

### F-12 · MEDIUM — Operator-extensible `article_ref_patterns` names silently mis-route to the document-level filter and can zero out retrieval

**FACT.** `ArticleAwareFilter` emits `<name>_no` for whatever pattern names the operator configures (`article_aware_filter.py:156-181`). `PgVectorStore._doc_filter_sql` routes only the frozen set `CHUNK_LEVEL_METADATA_FILTER_KEYS = {article_no, clause_no, section_no, appendix_no, chapter_no}` (constants `_17:105`) to the chunk-level `metadata_json @>` predicate; **any other key** is ANDed into the `documents.metadata_json @>` subquery (`pgvector_store.py:251-256, 271-278`). Ingest’s `extract_structured_refs` writes these refs to **chunk** metadata (per `article_aware_filter.py:24-27`). **Failure scenario:** operator adds `{"name":"point","regex":"Điểm\\s+(\\d+)"}` → query filter `{"point_no":"3"}` → routed to documents-level containment → no document carries it → both hybrid subqueries return 0 rows → retrieval collapses for every query matching that pattern (until the metadata-relax fallback, if enabled, rescues). Config-extensible input vs code-frozen routing.

### F-13 · MEDIUM — Kreuzberg-markdown PDF path loses page numbers (citation granularity regression vs legacy parser)

**FACT.** Legacy `pdf_parser.py:101-109` emitted per-page chunks with `metadata.page_number`. The now-default `kreuzberg_markdown_parser.py:161-171` returns ONE block whose metadata has no page info (deliberate for heading-hierarchy chunking, per `:158-160` comment). Downstream citations for PDFs on the default path cannot point at pages anymore. T2/UX trade-off that was never surfaced as a decision — worth an explicit ADR note or page-marker injection in the markdown.

### F-14 · MEDIUM — SimpleTextParser: hardcoded VN heading vocabulary despite existing locale packs + first-row column-drop bug in tab-table conversion

**FACT.**
- `simple_text_parser.py:30-33` + `:424-431` hardcode `Phần|Chương|Mục|Điều|Khoản|Tiết` although `DEFAULT_STRUCTURAL_MARKERS_BY_LANG` locale packs exist precisely to fix this class of hardcode (constants `_24:24-38` — “used to be hardcoded inline… silently left non-Vietnamese bots with no structural vocabulary”). The OCR fallback engine never got the retrofit.
- `simple_text_parser.py:490`: `cells = [c.strip() for c in line.split("\t") if c.strip() or rows]` — for the FIRST line only (when `rows` is empty), empty cells are dropped; subsequent lines keep them. **Failure scenario:** TSV whose header row has an empty first cell (`"\tName\tPrice"`) → header cells shift left by one relative to data rows → every value binds to the wrong column label.

### F-15 · LOW/MEDIUM — GenericLLMMetadataExtractor bypasses the LLM Port stack; `"vi"` locale literals as code defaults

**FACT.** `generic_llm_extractor.py:105-117` takes a raw `litellm_module` + model string and calls `acompletion` directly (`:146-153`); `nodes/retrieve.py:142-148` imports `litellm` at module level for it. This sidesteps the platform LLM Port (API-key pool, circuit breaker, per-tenant usage accounting in `request_logs`) — inconsistent with the Strategy+DI mandate that put every other LLM call behind the port. `extract(..., locale: str = "vi")` (`:122`) and `LLMMetadataCache.get/set(..., locale="vi")` (`llm_metadata_cache.py:47,74`) bake the platform-default language into signatures rather than reading the bot’s language. Feature is default-OFF (`DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED=False`, constants `_12:41`), so exposure is currently zero.

### F-16 · LOW — Zero-hardcode & hygiene nits (each FACT)

| Where | Issue |
|---|---|
| `semantic_cache.py:540` | `ttl_s: int = 3600` magic default; `DEFAULT_SEMANTIC_CACHE_TTL = 3600` exists (constants `_04:13`) but is not imported here (caller passes config, so impact is latent) |
| `semantic_cache.py:412` | Comment claims NULL-tenant rows are “soft-expired by `_cleanup_null_rows`” — **no such function exists anywhere** in the repo (stale contract comment; actual purge is `embedded_workers.py:193` on `expires_at` only) |
| `redis_cache.py:25` | `max_connections: int = 50` magic (streams client uses `DEFAULT_REDIS_STREAMS_MAX_CONNECTIONS`; the short-op client does not have a constant) |
| `pgvector_store.py:363-364` | `bm25_use_cover_density: bool = True`, `bm25_normalization_flags: int = 5` magic signature defaults — `DEFAULT_BM25_NORMALIZATION_FLAGS` exists and is used by stage 2 but not here |
| `pgvector_store.py:73-77` | `tokenize_vi` imported, never used — dead import |
| `pgvector_store.py:106-109` | `dimension` ctor param dead (see F-6), name borrowed from a reranker constant |
| `semantic_cache.py:163-189` | `find_similar` (embedding-only port method) has zero callers — dead port surface kept for contract compliance |
| `kreuzberg_markdown_parser.py:89` | `_resolve_mime` falls back to `"application/pdf"` for any unrecognized ext — a mislabeled non-PDF body gets a PDF mime hint (fail-soft: wrapped ValueError → registry-failed → raw-text path) |
| `vlm_image_parser.py:41` | `(b"RIFF", "image/webp")` claims ANY RIFF container (WAV/AVI too) as webp — should check bytes 8–12 == `WEBP`; failure is a silent empty caption |
| `docling_parser.py:114-115` | HYPOTHESIS: docling-core ≥2 `iterate_items()` yields `(item, level)` tuples and table items carry no `.text` → all blocks skipped. Unverifiable here (docling not installed — runtime FACT), engine opt-in only |

### F-17 · Test-health summary

- `test_kreuzberg_parser.py` mocks `kreuzberg` with a **sync** `extract_bytes` (`:60-67`) — green suite, broken production adapter (F-1). The `ocr_language` pin test (`:219-236`) only exercises the legacy-kwarg branch that real kreuzberg 4.9.7 never takes.
- Parser-quality coverage is otherwise decent (`test_parser_all_formats_structured.py`, `test_excel_row_as_chunk_parity.py`, `test_pgvector_store_tenant_scoping.py`, `test_retrieval_fallback*.py` exist), but nothing pins: stage-2/3 soft-delete filtering, stage-2 tenant GUC, `parent_chunk_id` presence in retrieval dicts, or a real-library kreuzberg contract.

---

## PART 3 — Axis scorecard

| Axis | Verdict | Basis |
|---|---|---|
| **Multi-format** | **Weakest axis.** OOXML+PDF+HTML+CSV+MD/TXT genuinely share one canonical structured-markdown funnel with byte-sniff rescue (verified working). But: legacy .doc/.xls/.ppt = unsupported (F-2); the entire OCR safety net beneath the registry is broken (F-1); scanned-image PDFs survive only because they route to `extract_bytes_sync` in the markdown parser; images depend on opt-in VLM; CSV decode is utf-8-or-garbage (F-7) |
| **Multi-bot** | Per-bot config honored for: stages (`retrieval_stage_{n}` via `_pcfg`), keyword pattern, thresholds, metadata-filter provider, cache threshold/TTL, reranker gates. NOT honored: embedding dimension (F-6, global 1280), tabular money vocabulary (F-9, global VN), simple-parser heading vocab (F-14), OCR language (F-1, never reaches engine) |
| **Multi-tenant** | PgVectorStore + semantic cache are properly tenant-scoped (`session_with_tenant`, strict `record_tenant_id` equality in cache SQL, NULL-tenant write refusal). Fallback stages 2–4 are the hole (F-3): no GUC, no soft-delete gate. Embed cache cross-bot by design (safe: key = hash of text+model, no readable payload leak) |
| **Multi-doc** | Row-as-chunk (excel/sheets) is consistent and section-bound; cross-document joins rely on stats-index/aggregation paths outside this scope. Parent/child and auto-merge — the in-scope multi-chunk context machinery — are inert (F-4) |
| **HAPPY-CASE-ONLY hot spots** | F-1 (OCR assumes old sync API), F-7 (CSV assumes utf-8 + public sheet), F-8 (xlsx assumes well-formed + small), F-9/F-10 (tables assume VN money + header ≥ data width), F-14 (TSV assumes filled first header cell), F-3 (fallback SQL assumes RLS-off role and live documents) |

## PART 4 — What is genuinely good here (evidence)

- `detect_parser_robust` order (declared → registry → byte-sniff → registry) is correct and runtime-verified for the octet-stream PDF case; OOXML zip-manifest peek disambiguates xlsx/docx/pptx (`mime_sniff.py:72-96`).
- `KreuzbergMarkdownParser` (the registry path) is correct against the installed lib: `extract_bytes_sync` + `OutputFormat.MARKDOWN`, verified emitting headings + pipe tables.
- `rows_to_structured_markdown` multi-row header merge, section-in-header split, gap-collapse and forward-fill are careful, shape-based, and shared across excel/sheets/docx — real structured-markdown parity across tabular formats.
- Semantic cache: strict two-key tenant/bot scoping in SQL, NULL-tenant write refusal, two-layer stampede control with TTL-bounded recursion, asyncpg-safe CAST, whitelisted identifiers before f-string SQL, corpus/bot-version keying, invalidation on re-ingest + background purge.
- `pgvector_store.hybrid_search`: whitelisted embedding column, bounded ef_search, NFC + segmentation symmetry with ingest, GIN-indexable predicates by default, structural-prefilter graceful no-match retry that keeps bind params consistent (regression comment `:571-578` shows the lesson was learned).
- Null Object + fail-soft registries are uniform across all five strategy families.

## PART 5 — Recommended fix order (T1-first)

1. **[T1] F-1**: switch OCR adapter to `extract_bytes_sync` + config (ocr_language, markdown), add un-mocked contract test. Unblocks every registry-miss format.
2. **[T1] F-3**: route stages 2–4 through `session_with_tenant` + add `doc_deleted_at IS NULL`; segment query in stage 2.
3. **[T1] F-4**: add `parent_chunk_id` to retrieval SELECTs + dict builders (one column, three features light up).
4. **[T1] F-2**: decide legacy-format story — either kreuzberg-route .doc/.xls/.ppt (after F-1) with sniff support for OLE2, or reject loudly at API with a clear error instead of DLQ-after-timeout.
5. **[T1] F-7/F-8**: charset detection (BOM/UTF-16) + `csv.Error`/`BadZipFile` → ValueError wraps + xlsx size cap; HTML-login guard on sheet ingest.
6. **[T2] F-11** (hash-tier during embedder outage), **F-10** (pad header instead of truncating rows), **F-12** (derive chunk-level key set from configured pattern names).
7. **[T3] F-5/F-6**: either make ingest call the port’s upsert (fixing its column list) or delete the method from the port; validate binding dim == column dim at bind time; remove dead `dimension` param; hygiene nits F-16.
