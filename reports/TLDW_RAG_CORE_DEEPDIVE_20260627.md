# TLDW_SERVER RAG DEEP-DIVE — code-level + input-data best practices (2026-06-27)

> Synthesis of 10 deep agent reads over tldw_server's full RAG pipeline (chunking core/strategies/templates, embeddings pipeline/infra/vector-store, retrieval, rerank/grade/faithfulness, generation/config, upload/ingestion, multi-source ingestion). All claims carry tldw `file:line`. Our-side claims carry ragbot `file:line`.

---

## 0. TL;DR — how expert is tldw's RAG, and the 5 biggest things we should steal

tldw_server is genuinely expert-grade on the dimensions we care about most: it is **structurally domain-neutral** (structure is detected from markup/grammar, never from a vocabulary word-list), **multi-format by one canonical contract**, and **config/registry-driven everywhere** (Port+Strategy+Registry+NullObject for chunking strategies, OCR backends, rerankers, embedding providers, vector-store client, and source adapters). The two real weaknesses are: (a) **English-only prompts and NLP heuristics** (granularity router regex, grader keyword votes, capital-letter sentence splitting, Western number parsing) — these are exactly the trap our domain-neutral rule forbids and must NOT be copied verbatim; and (b) **inline numeric defaults** (max_size=400/overlap=200, chunk_size=1000, provider hardcodes like `huggingface` fallback / `openai` situate default) that violate our zero-hardcode rule.

**The 5 biggest things to steal (ROI-ordered):**

1. **Structural header/table detection instead of a vocab token-set** — `structure_aware.py` detects a table header by a markdown separator line `^[\s\|:\-]+$` after a `|`-row (structure_aware.py:494) and header level = `len(match.group(1))` = count of leading `#` (structure_aware.py:384). This is the direct antidote to our `_HEADER_EXACT_TOKENS` bug (document_stats.py:191) — we already have `_is_separator_line()` (document_stats.py:303) but don't use it as a header signal.
2. **Content-hash idempotency + diff over EXTRACTED text** — `diff_snapshots()` (diffing.py:55) emits created/changed/unchanged/deleted by `sha256(extracted_text)` equality (local_directory.py:220); re-ingest becomes a deterministic no-op when nothing changed, re-chunk/re-embed only on change. Solves our "re-ingest cost" and "status=success ≠ answered correctly" lessons.
3. **3-signal type detection with byte-sniff as authority + HARD-FAIL on mismatch** — `Upload_Sink.validate_file` does puremagic→python-magic→mimetypes and refuses if detected MIME isn't in the per-type allow-list (Upload_Sink.py:673-690), with longest-suffix-first ext candidates so `.tar.gz` beats `.gz` (Upload_Sink.py:271).
4. **Per-collection embedding-model auto-resolution at query time** — `_resolve_scoped_query_embedding_override` (database_retrievers.py:1451) resolves the query embedding model from the stored chunks' metadata, preventing the classic "query embedded with model A vs corpus embedded with model B" mismatch we've hit repeatedly.
5. **Sentinel-calibrated answer gating** — `TwoTierReranker` injects a synthetic known-irrelevant doc through the SAME reranker and gates on `top_prob < threshold OR (top_prob − sentinel_prob) < margin` (advanced_reranking.py:1567-1688), a self-calibrating refuse signal that beats a fixed cosine cutoff (our cliff/threshold-drift pain).

---

## 1. Full RAG architecture map

| Subsystem | Key files | Flow (short) | Config mechanism |
|---|---|---|---|
| **Chunking core** | `Chunking/chunker.py` (orchestrator), `base.py` (ABC + ChunkMetadata), `async_chunker.py` | sanitize → size-guard → normalize method → resolve method → option-alias → clamp overlap → cache check → strategy acquire → execute → cache store | Lazy zero-arg factory registry `_register_strategy_factories` (chunker.py:377); `[Chunking]` section of config.txt + `DEFAULT_CHUNK_OPTIONS` (base.py:467, __init__.py:56); LRU cache keyed on SHA-256 of text+params+LLM-signature (chunker.py:2121) |
| **Chunking strategies** | `Chunking/strategies/*.py` (13 strategies), `strategies/__init__.py` (STRATEGY_REGISTRY) | each subclasses `BaseChunkingStrategy`, returns `ChunkResult` w/ exact start_char/end_char | `STRATEGY_REGISTRY` name→class + `get_strategy(name)` (__init__.py:18,29); engine/threshold/aggressiveness via options (propositions.py:71) |
| **Chunking templates** | `Chunking/templates.py`, `template_initialization.py`, `block_to_chunks.py`, `enhanced_chunking_integration.py` | classify doc→template → preprocess/chunk/postprocess stages → normalize to {text,metadata} | Template = DATA (`ChunkingTemplate` dataclass, templates.py:65); string→callable op registry (templates.py:97); `TemplateClassifier.score` reads match rules from template's own JSON (templates.py:762); DB-seeded (template_initialization.py:328) |
| **Embeddings pipeline** | `Embeddings/async_embeddings.py` (orchestrator), `request_batching.py`, `multi_tier_cache.py`, `model_warmup.py`, `sharding.py`, `simplified_config.py`, `Embeddings_Create.py` | resolve service (per-loop singleton) → cache read L1→L2→L3 → batch coalesce → provider call → write-through | dataclass config layered YAML→config.txt→env (simplified_config.py:243); per-`{NAME}_API_KEY` auto-load (simplified_config.py:36) |
| **Vector-store + worker** | `Embeddings/ChromaDB_Library.py`, `services/jobs_worker.py`, `services/redis_worker.py`, `redis_pipeline.py`, `error_recovery.py`, `dlq_crypto.py` | enqueue (SETNX idempotency) → consume (xreadgroup) → chunk/embed/store stages (artifact handoff) → ack/DLQ | Injectable client (`client=`/`client_factory`/config-string backend, ChromaDB_Library.py:237); stream/group/retry/TTL all env (redis_pipeline.py:77-97) |
| **Retrieval** | `rag_service/database_retrievers.py`, `granularity_router.py`, `parent_retrieval.py`, `advanced_retrieval.py`, `embedding_cache.py`, `connection_pool.py` | (opt) granularity route → per-source retrieve → hybrid FTS+vector gather → RRF fuse → (opt) multi-vector rerank → parent expansion | `context.config[...]` dicts (database_retrievers.py:3540); `BaseRetriever` ABC + `MultiDatabaseRetriever` registry (database_retrievers.py:447,3048); fusion string-switch rrf/weighted/max |
| **Rerank + grade + faithfulness** | `rag_service/advanced_reranking.py`, `document_grader.py`, `knowledge_strips.py`, `faithfulness.py`, `evidence_accumulator.py`, `evidence_chains.py`, `citations.py` | grade (CRAG) → rerank (8 strategies) → two-tier sentinel gate → (opt) evidence accumulate → faithfulness claims → citations | `RerankingStrategy` enum + `create_reranker` factory (advanced_reranking.py:33,1435); calib weights env `RAG_RERANK_CALIB_*` (advanced_reranking.py:1632) |
| **Generation + config** | `rag_service/generation.py`, `config.py`, `advanced_config.py`, `prompt_loader.py`, `guardrails.py`, `hyde.py`, `clarification_gate.py`, `chunk_metadata.py` | load config (3-layer) → (opt) clarify/HyDE → generate via one chat seam → post-gen guardrails (observe-only) | `GenerationStrategy` Protocol + `create_generator` (generation.py:38,529); provider = config STRING through `perform_chat_api_call_async` (generation.py:390); env-mapping table (config.py:238); declarative ConfigValidation (advanced_config.py:443) |
| **Ingestion (upload/URL)** | `Ingestion_Media_Processing/Upload_Sink.py` (SSoT validator+config), `input_sourcing.py`, `download_utils.py`, `OCR/` (Port/Strategy/Registry), `chunking_options.py` | detect (3-signal) → validate (hard-fail mismatch) → SSRF/size guards → parse→canonical markdown → chunk handoff | `DEFAULT_MEDIA_TYPE_CONFIG` + `EXT_TO_MEDIA_TYPE_KEY` tables (Upload_Sink.py:150,248); OCR `_BACKENDS` dict + config priority (registry.py:39,59) |
| **Ingestion (multi-source)** | `Ingestion_Sources/{models,local_directory,git_repository,archive_snapshot,diffing,service,jobs}.py`, `sinks/*`, `services/ingestion_sources_worker.py` | enqueue → lease → claim (single-writer lock) → build snapshot → content-hash → diff → per-change sink upsert → commit | Source/Sink enums (models.py:5); `build_<x>_snapshot_with_failures` uniform adapter; allowed-roots/retention/poll all env (config.py:271) |

---

## 2. Chunking deep-dive — strategies + TEMPLATE mechanism (the antidote to `_HEADER_EXACT_TOKENS`)

### 2.1 How it picks a chunking strategy per doc WITHOUT hardcoded vocab

Two independent mechanisms, both data-driven:

- **Method dispatch** is a lazy zero-arg factory registry: `Chunker._register_strategy_factories` builds `dict[method_string → lambda]` once (chunker.py:377-414), heavy/optional strategies `__import__`-ed inside the lambda so optional deps load only on demand. Adding a method = add a file under `strategies/` + one registry line; the orchestrator never changes. Method vocabulary is an Enum `ChunkingMethod` (base.py:18) normalized through one chokepoint `_normalize_method_argument` (chunker.py:2006).
- **Doc-type → template** is a SCORED classifier reading match rules from each template's OWN config: `TemplateClassifier.score` (templates.py:762-794) scores `media_type` membership (weight 0.5) + filename/title/url regex hits (weight 0.5 averaged over 3), clamped by per-template `min_score`. Highest score wins. This is DATA, not an `if/elif` on doc type — adding a new format's strategy = inserting a JSON template row (DB-seeded at startup, template_initialization.py:328). Regex inputs are ReDoS-guarded (`regex_safety.check_pattern`, templates.py:782).

The template engine itself (`TemplateProcessor.process_template`, templates.py:124-168) threads `{text, chunks, metadata}` through preprocess/chunk/postprocess stages, each dispatching via a string→callable op registry (`register_operation`, templates.py:113). The `Chunker` is constructor-injected (templates.py:79). Param precedence is explicit: top-level → nested config → call options → constant default (templates.py:222-252).

### 2.2 How `structure_aware` detects headers/tables STRUCTURALLY (no vocab)

`_parse_document_structure()` (structure_aware.py:345) runs an ordered NON-vocab regex cascade compiled in `__init__` (structure_aware.py:175). The crucial facts:

- **Headers**: `markdown_header` regex `^(#{1,6})\s+(.+)$` (structure_aware.py:176); **header LEVEL = `len(match.group(1))` = number of `#`** (structure_aware.py:384). No keyword list — the *syntax* `##` means heading in Vietnamese, legal, spa, or phone corpora identically.
- **Tables**: detected by lines containing `|` followed by a separator line matching `^[\s\|:\-]+$` (`_extract_tables`, structure_aware.py:459,494). The separator line — not a label vocabulary — is the authority for "this is a table with a header row."
- **Code fences** `(```|~~~)` extracted FIRST (they can contain other markup) (structure_aware.py:179); bullet/numbered lists by `^[\s]*[-*+]\s+` / `^[\s]*\d+\.\s+` (structure_aware.py:181-182); leftover spans → PARAGRAPH (structure_aware.py:431).
- **Breadcrumbs**: `_build_contextual_header()` (structure_aware.py:696-722) builds `A > B > C` by maintaining a level-stack over the GLOBAL header index — pure structural nesting, zero vocab.

Other strategies reinforce the "structure from grammar" rule: JSON via grammar scanners `_scan_top_level_array_spans`/`_scan_top_level_object_pairs` (json_xml.py:322,391); code via `ast.parse` (code_ast.py:79); XML via defusedxml ElementTree with XXE pre-screen (json_xml.py:758,58); semantic via TF-IDF cosine on the document's OWN sentences (semantic.py:174,231) — no embedding model, no vocab. The ONLY language literals are syntactic and ISO-keyed: sentence delimiters incl. zh/ja/ko/ar (sentences.py:54), NLTK punkt map (semantic.py:281), ebook chapter navigation words PLUS owner-supplied custom patterns (ebook_chapters.py:49).

### 2.3 Why this is the antidote to our bug — concrete contrast

Our `_is_header_row()` (document_stats.py:275-300) **requires a vocab match**: `if normalised in _HEADER_EXACT_TOKENS or normalised in declared_labels: has_label_match = True` and returns `has_label_match`. `_HEADER_EXACT_TOKENS` (document_stats.py:191-194) is a curated frozenset (name/category/price/aliases tokens). A fully-custom domain (phone `Model/RAM/Pin`, legal `Điều/Khoản`) whose header cells match no built-in token becomes `col_N` unless the owner pre-declares labels — violating domain-neutral + multi-lang.

tldw never needs price knowledge or a label vocab because it keys off the **structural separator line**. We *already have the building block*: `_is_separator_line()` (document_stats.py:303-314) handles both pipe `| --- |` and comma `---,---` forms. The fix (Section 6) is to add a structural-first path: **if a row is immediately followed by a separator line, treat it as the header regardless of vocab**, and combine the existing structural "price-cell ⇒ data row" signal (`parse_money_vn(col) is not None`, document_stats.py:294) so header detection works with ZERO vocab matches — `_HEADER_EXACT_TOKENS` demoted to a tie-breaker only.

---

## 3. Input-data CONTROL best practices (the user's core question)

The complete list of how tldw codes the input-data control flow, each backed by file:line:

1. **ONE shared validator + config core; every input path routes through it.** Upload, URL, and archive-members all go through `Upload_Sink.process_and_validate_file` → `validate_file` (Upload_Sink.py:1179,512). Format taxonomy is two data tables: `DEFAULT_MEDIA_TYPE_CONFIG` (per-type allowed_extensions/mimetypes/max_size_mb) and `EXT_TO_MEDIA_TYPE_KEY` (Upload_Sink.py:150,248). New format / tightened cap = one table edit, instantly applied everywhere — no per-endpoint drift.

2. **3-signal type detection, byte-sniff is authority, HARD-FAIL on mismatch.** Claimed ext → `EXT_TO_MEDIA_TYPE_KEY` → byte MIME via puremagic→python-magic→mimetypes (Upload_Sink.py:639-671); refuse if detected MIME not in the per-type allow-list with explicit "Do NOT fall back to extension-derived MIME" (Upload_Sink.py:673-690). URL files routinely carry `application/octet-stream` or spoofed ext — sniffing is the only safe routing.

3. **Longest-suffix-first extension candidates.** `_extension_candidates` yields `.tar.gz` before `.gz` (Upload_Sink.py:271-280) so compound and multi-dot names route/size correctly.

4. **Parser-as-adapter emitting ONE canonical structured-markdown + metadata, with cascading fallback.** EPUB picks extractor by method with fallback to `read_epub` then emits `result['content']` markdown (Book_Processing_Lib.py:585,723-748,755). Chunking is format-agnostic: add a format = add an extractor returning the same contract; degraded parse never hard-fails.

5. **Structural structure-detection (no vocab).** As Section 2: headers by `#`-count (structure_aware.py:384), tables by separator-line `^[\s\|:\-]+$` (structure_aware.py:494), JSON/code/XML by grammar (json_xml.py:322; code_ast.py:79; json_xml.py:758).

6. **Template-per-doctype as DATA + scored classifier.** `ChunkingTemplate` (templates.py:65) + `TemplateClassifier.score` reading each template's own match rules (templates.py:762). DB-seeded, dual-schema-tolerant loader (`stages` OR `{preprocessing,chunking,postprocessing}`, templates.py:648-682).

7. **Multilingual WITHOUT vocab.** Language behavior is data keyed by ISO code (LanguageConfig dataclasses: delimiters/direction/tokenizer-name/requires_spacing, multilingual.py:28-187); detection by Unicode script ranges + generic stopword frequency (multilingual.py:49-73); per-language tokenizers (jieba/fugashi/konlpy/pythainlp/pysbd) with character/space fallback (words.py:32-40, sentences.py:54-67). The only embedded literals are script ranges, sentence punctuation, and the CJK/Thai no-space set (words.py:114).

8. **Offset-preserving sanitization + grapheme-safe boundaries.** Only apply NFC/replacement when string length is unchanged, else retain original + log (chunker.py:1410-1466) so start_char/end_char stay valid for citations. `_expand_end_to_grapheme_boundary` in the base class (base.py:154-215) prevents splitting combining-mark/ZWJ/emoji clusters — every strategy inherits correct VN-diacritic/emoji boundaries for free.

9. **Idempotency / diffing over extracted text.** Producer SETNX+TTL guard before enqueue (redis_pipeline.py:166-179); stage-to-stage artifact handoff makes each stage idempotent — reuse existing artifact unless force_regenerate (jobs_worker.py:447-457,520-540,627-634); `diff_snapshots()` pure created/changed/unchanged/deleted by `sha256(extracted_text)` (diffing.py:55; local_directory.py:220). Extraction failures subtracted from "deleted" so a transient parse error never archives a still-present doc (worker.py:386).

10. **Chunk-quality gating (online + offline).** Online Prometheus `ChunkingMetrics` labeled by method (utils/metrics.py:49); offline `evaluate_propositions` precision/recall/F1 + claim_density + dedup_rate via TF-IDF cosine greedy match with Jaccard fallback (utils/proposition_eval.py:85,39,64). Chunking changes get a NUMBER before shipping.

11. **Metadata as a typed canonical hint, NOT a behavior switch.** `block_to_chunks._build_citation_span` carries page/paragraph/line/slide/row/col/sheet/bbox_quad/timestamps with camelCase/snake_case multi-key fallbacks (block_to_chunks.py:186-240); `RAGChunkMetadata`/`CitationSpan` Pydantic schema with `extra='ignore'` (chunk_metadata.py:28-79) — one citation contract across all formats.

12. **Security at the boundary.** Two-layer blocked-extension guard (saver pre-write input_sourcing.py:144-204 + validator re-check Upload_Sink.py:538), with one explicit carve-out (`.js` only for `code`). SSRF/egress validation BEFORE any URL fetch (`_validate_egress_or_raise`, download_utils.py:270 → evaluate_url_policy http_client.py:947). Streaming dual size enforcement (header Content-Length + running byte total) with partial-write unlink (download_utils.py:200,220; input_sourcing.py:390-404). Path-traversal containment (download_utils.py:67). Archive zip-slip/encrypted/symlink-member rejection (archive_snapshot.py:80,161). Allow-list/deny-list chunking-option keys raising on unsupported (chunking_options.py:10-38,90).

13. **Single-writer sync lock + last-good pointer.** Conditional UPDATE on `active_job_id` only if NULL/self (service.py:1132); `last_successful_snapshot_id` advances only on success (service.py:1168) so a failed run never poisons the diff baseline. Per-item partial-failure isolation with explicit degraded statuses + event log (worker.py:427,500).

---

## 4. Embedding / rerank / retrieval best practices (what makes it production-grade)

**Embeddings:**
- **Request coalescing per (provider, model, config-fingerprint)** with size-OR-timeout flush and per-request asyncio Futures (request_batching.py:313-362) — the single biggest throughput win at scale; **adaptive batching** tunes size/timeout from rolling throughput (request_batching.py:752-801).
- **3-tier cache** L1 memory LRU → L2 disk → L3 Redis, with **access-count-gated promotion** (promote to L1 only after N accesses to avoid one-hit-wonder pollution) and executor-offloaded tier I/O to keep the loop non-blocking (multi_tier_cache.py:770-834). Deterministic **content-addressed key** `sha256(text)` namespaced by `provider:model[:base_url]` (async_embeddings.py:570-573) — cross-process, model-scoped, language-agnostic.
- **Local-model LRU registry bounded by BOTH count and memory-GB with in-use refcount** so an actively-encoding model is never evicted (Embeddings_Create.py:1024-1080,1040).
- **Opt-in, config-listed warmup** with a trivial test string + periodic re-warm (model_warmup.py:112-152).
- **Provider fallback chain** (async_embeddings.py:669-723) and graceful degradation (Redis-down → L3 disabled, multi_tier_cache.py:588).
- **Atomic disk writes** (temp+fsync+os.replace) + restrictive `_SafeUnpickler` whitelist (multi_tier_cache.py:343-417,64).
- **Sharding** via consistent-hash ring with virtual nodes (150) + scatter-gather merge (sharding.py:29-108).

**Vector-store + worker:** injectable client with same-contract in-memory Null double (ChromaDB_Library.py:237,1807-1977); process-local ref-counted client cache (ChromaDB_Library.py:203-219); **dimension-drift detection on write** — compare incoming dim vs collection metadata, recreate + record dim+source_model_id (ChromaDB_Library.py:1276-1309); DLQ encryption-at-rest scrypt+AES-GCM (dlq_crypto.py:45-82); per-provider CircuitBreaker + bounded semaphore + retry (connection_pool.py:59-126); soft-delete compactor (vector_compactor.py).

**Retrieval:** hybrid FTS+vector via `asyncio.gather` then **weighted RRF (alpha, k=60)** — scale-invariant fusion (database_retrievers.py:1899-1966); **rule-based granularity router** (no LLM, regex+length+wh-word → top_k/parent/multi-vector flags, granularity_router.py:60-279); **per-collection embedding-model auto-resolution** (database_retrievers.py:1451) prevents query/corpus model mismatch; **defence-in-depth tenant re-check** after vector search (database_retrievers.py:1724-1733); ColBERT-style non-invasive span max-sim reranker (advanced_retrieval.py:100); late-chunking on retrieved docs with composite `parent*0.1+chunk*0.9` (database_retrievers.py:842-969); parent expansion with diminishing-returns diversity scoring (parent_retrieval.py:433-465); progressive FTS hardening (phrase-quote hyphens, bounded OR fallback, stop-word filter) closing the classic 0-chunk bug (database_retrievers.py:152-272).

**Rerank + grade + faithfulness:** Strategy+factory `create_reranker` over 8 strategies (advanced_reranking.py:33,1435); **sentinel-calibrated answer gating** (advanced_reranking.py:1567-1688); two-tier cost control (cheap CE prefilter → LLM scores shortlist only, per-call timeout + total budget + max-docs, advanced_reranking.py:1340-1352,1400); **claim-level faithfulness** (extract atomic claims → verify each → supported/total, per-claim breakdown, faithfulness.py:156-242); content-hash dedup across multi-round evidence (evidence_accumulator.py:73,447); lenient LLM-JSON parse → heuristic vote → score fallback (document_grader.py:245-360). **Caveat: prompts + keyword/sentence heuristics are English-only** (document_grader.py:63,302; faithfulness.py:87,97; knowledge_strips.py:85; evidence_chains.py:66) — adopt the architecture, drive prompts/heuristics from our language_packs.

---

## 5. OUR GAPS — ragbot vs tldw on input-data control

| # | Gap (where ragbot falls short) | our_file | tldw_pattern (file:line) | Priority |
|---|---|---|---|---|
| 1 | Table header detection REQUIRES vocab match; custom-domain/non-VN headers → `col_N` | `src/ragbot/shared/document_stats.py:191,275-300` | structural separator-line header detection `^[\s\|:\-]+$` after `\|`-row (structure_aware.py:494); header level = `#`-count (structure_aware.py:384) | **P0** |
| 2 | No content-hash idempotency / diff on re-ingest; full re-chunk+re-embed every time | ingest flow (`DocumentService`, `interfaces/http/routes/documents.py`) | `diff_snapshots()` by `sha256(extracted_text)` (diffing.py:55; local_directory.py:220); artifact-handoff reuse (jobs_worker.py:447-457) | **P0** |
| 3 | Byte-sniff is a hint, not a hard authority; no per-parser allow-list reject on MIME/ext mismatch | `src/ragbot/shared/mime_sniff.py`, `infrastructure/ocr/kreuzberg_parser.py:255`, each parser open-codes `data[:4]==b'%PDF'` | `validate_file` hard-fail if detected MIME ∉ allow-list (Upload_Sink.py:673-690); SSoT tables `DEFAULT_MEDIA_TYPE_CONFIG`+`EXT_TO_MEDIA_TYPE_KEY` (Upload_Sink.py:150,248) | **P0** |
| 4 | No SSRF/egress guard on any URL-sourced ingest | ingest URL path (`documents.py`) | `_validate_egress_or_raise` before fetch (download_utils.py:270; http_client.py:947) | **P1** |
| 5 | No streaming dual size enforcement (header + running bytes) + partial-write unlink | `documents.py`, `documents_stream_upload.py` | header cap + running-total cap + unlink on overflow (download_utils.py:200,220; input_sourcing.py:390-404) | **P1** |
| 6 | Query-embedding model not auto-resolved from corpus chunks' metadata → model mismatch risk | `orchestration` retrieve node, `reranker_resolver`/embedder resolve chain | `_resolve_scoped_query_embedding_override` (database_retrievers.py:1451) | **P1** |
| 7 | No dimension-drift detection on pgvector write after provider migration | pgvector store write path | compare dim vs collection metadata + record dim/model (ChromaDB_Library.py:1276-1309) | **P1** |
| 8 | Header-token sets scattered in code, not a config-driven SSoT format/detection table | `document_stats.py` token frozensets | format taxonomy as DATA tables + per-bot detection mode flag (Upload_Sink.py:150,248; templates.py:762) | **P1** |
| 9 | No grapheme-safe chunk-end expansion → VN diacritics/emoji can split mid-cluster | AdapChunk chunker base | `_expand_end_to_grapheme_boundary` in base class (base.py:154-215) | **P2** |
| 10 | No offline chunk-quality eval gating changes pre-ship (no-guess-must-measure) | eval harness | `evaluate_propositions` precision/recall/F1+dedup (proposition_eval.py:85) | **P2** |
| 11 | No template-per-doctype as data + scored classifier (chunking strategy not config) | AdapChunk strategy selection | `ChunkingTemplate` + `TemplateClassifier.score` (templates.py:65,762) | **P2** |
| 12 | No per-item partial-failure isolation + degraded-status event log on batch ingest | ingest worker | degraded_ingestion_error / degraded_sink_error + events (worker.py:427,500) | **P2** |

---

## 6. Adoption plan — EVOLVE not rewrite (ROI-ordered, mapped to our files)

**Strangler-fig stance: keep our Hexagonal/Port+Registry frame + 4-key + sacred rules; WIRE/HARDEN, don't rebuild. Lift ALL tldw inline constants into `shared/constants.py`/`system_config`. NEVER copy tldw's English-only prompts/heuristics — those go through `language_packs`/`custom_vocabulary`.**

**P0-1 — Structural-first header detection (kills the `_HEADER_EXACT_TOKENS` bug).** In `document_stats.py:_is_header_row` (line 275), add a structural path BEFORE the vocab check: (a) caller passes the next line; if `_is_separator_line(next_line)` (we already have it at document_stats.py:303) → treat current row as header regardless of vocab; (b) keep the existing "any cell parses as money ⇒ data row, return False" guard (document_stats.py:294); (c) demote `_HEADER_EXACT_TOKENS` to a tie-breaker only when no separator context is available. Add a per-bot `header_detection_mode` flag (`structural` | `token_assisted`) in `pipeline_config` so fully-custom-domain bots run structural-only. Mirrors structure_aware.py:494. **TDD: failing test with phone `Model|RAM|Pin` + separator line → currently `col_N`, must become named header.** Low blast radius, highest T1 ROI.

**P0-2 — Content-hash idempotency + diff on re-ingest.** Add `ingest_source_items` table (alembic) keyed `(record_bot_id, source_url/path)` storing `content_hash = sha256(canonical_markdown)` + stage state, mirroring service.py:989 `upsert_source_item`. In the ingest flow add a `diff_snapshots`-style helper (created/changed/unchanged/deleted, diffing.py:55) so re-ingest skips unchanged docs (no re-chunk/re-embed), re-processes changed, soft-deletes disappeared. Hash over **canonical markdown** not raw bytes (local_directory.py:220). Add producer-side SETNX+TTL guard `ragbot:ingest:{record_bot_id}:{doc_hash}` (redis_pipeline.py:166-179). Directly cuts re-ingest cost; deterministic. Honors no-psql-hotfix (schema via alembic).

**P0-3 — Byte-sniff as hard authority + SSoT format table.** Build one config-driven module backing `detect_parser`: ext→category + allowed-mime + size-cap, consolidating the scattered `data[:4]==b'%PDF'` checks. Enforce the Upload_Sink rule: detected MIME ∉ this parser's allow-list ⇒ reject at the boundary before `detect_parser` (Upload_Sink.py:673-690). Add longest-suffix-first ext candidates for compound/VN-dotted names (Upload_Sink.py:271). Constants → `shared/constants.py` + `system_config`.

**P1-4 — SSRF/egress guard + streaming dual size enforcement.** Add a config-driven private-IP/egress deny check before any URL fetch in the ingest flow (download_utils.py:270 pattern). Add header Content-Length + running-byte cap with partial-write unlink to `documents.py`/`documents_stream_upload.py` (download_utils.py:200,220; input_sourcing.py:390-404). Multi-tenant SSRF/DoS exposure — security P1.

**P1-5 — Query-embedding model auto-resolution + dimension-drift detection.** Wire the per-collection model resolution into the retrieve node + embedder resolve chain (database_retrievers.py:1451) so query embedding matches corpus. Add dim+model to our pgvector table/collection metadata and detect mismatch on upsert (ChromaDB_Library.py:1276-1309) — hardens our known threshold/embedding-column drift after provider migration.

**P2-6 — Grapheme-safe chunk-end + config-driven language table.** Port `_expand_end_to_grapheme_boundary` (base.py:154) into our chunker base (free VN-diacritic/emoji correctness across all parser adapters). Make language behavior a `LanguageConfig`-style table (delimiters/requires_spacing/tokenizer-name) stored in `system_config`/`language_packs`, not branches (multilingual.py:28-187).

**P2-7 — Offline chunk-quality eval + per-item failure isolation + template-as-data.** Add an `evaluate_propositions`-style precision/recall/F1+dedup metric to the eval harness (proposition_eval.py:85) so chunking changes get a number pre-ship (no-guess mandate). Add per-document degraded-status + event log to the ingest worker (worker.py:427,500) so one corrupt PDF never fails the batch (raises Coverage, untouched HALLU). Optionally introduce `ChunkingTemplate`-as-data + scored classifier for per-bot/per-doctype chunking (templates.py:65,762), keeping constants in `system_config`.

**Explicitly DO-NOT-COPY:** tldw's inline `max_size=400/overlap=200` (base.py:382), `chunk_size=1000/overlap=200` (redis_worker.py:127), `huggingface` fallback / `openai` situate hardcodes (jobs_worker.py:579; ChromaDB_Library.py:610), and ALL English-only prompts/regex/keyword heuristics (granularity_router.py:22-47; document_grader.py:302; knowledge_strips.py:85; faithfulness.py:87; guardrails.py:57-67). Adopt their *architecture*; source vocab/prompts/numbers from our config layer.
