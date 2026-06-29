# GOLD STANDARD — Input-Data / RAG Ingestion & Retrieval

> Distilled from 10 reference projects (tldw_server, RAG-Anything, adaptive-chunking/Ekimetrics-LREC2026, open-notebook, llama-cookbook/NotebookLlama, and the structure-aware chunking strategy lib).
> **Self-contained audit checklist.** Each principle = 1-2 lines + the ref `file:line` that PROVES it (evidence the auditor does NOT need to open). Use this to grade ragbot's code: ✅ = present & correct, ❌ = missing/wrong-layer.
>
> **THE ONE LAW (rule #0 of input-data):** Structure is decided by **FORM** (markup depth, byte signature, element type, char/token offset, rank position) — **NEVER by VOCABULARY** (no brand/domain/keyword/heading-word/single-language lists in any structure-deciding code path). Every section below restates this law in its own terms.

---

## A. Canonical ingest / upload flow — ONE funnel, every format

- **A1. Single canonical entrypoint for every source** (multipart upload, URL download, dir scan, archive member, git). All converge to one normalized record. — `Ingestion_Sources/local_directory.py:217-229` ({relative_path, content_hash=sha256(text), source_format, raw_metadata, text}); `open-notebook/graphs/source.py:54-78` (one `extract_content` call, `output_format="markdown"`, both URL+doc).
- **A2. Force ONE structured-markdown output for all formats** so every downstream stage is format-agnostic. — `RAG-Anything/parser.py:1846-1907` (uniform `content_list` block schema); `open-notebook/graphs/source.py:54-78`.
- **A3. New format = new parser adapter only; orchestrator/differ/worker/sink UNCHANGED** (Open-Closed). — `RAG-Anything/parser.py:2389-2444` (register_parser); `Ingestion_Sources` differ knows nothing about sinks.
- **A4. Content-hash idempotency, decoupled from byte identity** — re-ingest of identical logical content is a no-op; safe retries across machines. — `diffing.py:55` (diff → created/changed/unchanged/deleted); `RAG-Anything/processor.py:200-237` (content-based doc_id = mdhash of structural signature).
- **A5. Per-item failure isolation, not batch abort** — one corrupt file must not kill the other 99; preserve prior content_hash so next sync re-attempts. — `services/ingestion_sources_worker.py:427-459` (degraded_sink_error, item still upserted, event recorded).
- **A6. Stream chunks to disk with per-chunk flush()** — long ingest is crash-resilient, memory stays flat. — `NotebookLlama Step-1 cell 25` (write + flush inside per-chunk loop).
- **A7. Soft-failure sentinel detection** — third-party extractor returning `title='Error'`/`'Failed to extract'` must be detected & raised, never saved+embedded as a body. — `open-notebook/graphs/source.py:80-105`.
- **A8. Permanent-vs-transient error split drives retry** — blocklist retry (retry all EXCEPT ValueError/ConfigError); re-raise permanent so job is "failed/retryable" not silently "completed". — `commands/source_commands.py:49-59,141-155`.

## B. Type detection — mime → ext → byte-sniff (layered, with provenance)

- **B1. Three-stage precedence: extension-candidate (longest suffix first, multi-dot `.tar.gz`-aware) → magic byte-sniff → mimetypes fallback.** — `Upload_Sink.py:271` (_extension_candidates), `:283` (_resolve_media_type_key), `:639-671` (puremagic→python-magic→mimetypes).
- **B2. Track WHICH detector fired** (`mime_detection_source`) so mis-routing is debuggable, not silent. — `Upload_Sink.py:631-671`.
- **B3. Two-tier MIME policy: hard-fail when detected MIME is known-bad; lenient ext-acceptance ONLY for text-shaped categories (code/document/json)** where octet-stream is common. — `Upload_Sink.py:673-690` (hard-fail), `:692-727` (drop MIME-only issue when ext matches).
- **B4. URL downloads (often `application/octet-stream`, no ext) rescued by content-type→media-key map + byte-sniff.** — `download_utils.py:100`; `RAG-Anything/parser.py:120-132`.
- **B5. Routing tables are pure ext→key / MIME→key — NO vocabulary.** — `Upload_Sink.py:248` (EXT_TO_MEDIA_TYPE_KEY), `:150` (DEFAULT_MEDIA_TYPE_CONFIG).
- **B6. Extension-only typing is a KNOWN GAP** (NotebookLlama `cell 8` `.pdf`-only) — an expert pipeline MUST add byte-sniff; flag any code that trusts ext alone.

## C. Parser-as-adapter — Port + Strategy + Registry

- **C1. BaseParser ABC / Protocol; N backends emit the IDENTICAL output schema.** — `adaptive-chunking/parsing.py:12-40` (BaseParser ABC, 4 backends → one JSON contract); `chunking base.py:65-104` (Protocol + ABC, uniform `chunk()`/`chunk_with_metadata()`).
- **C2. Registry with validation: `register_parser` enforces subclass, blocks built-in override, normalizes name; `get_parser` = built-ins then custom.** — `RAG-Anything/parser.py:2389-2444, 2493-2511`. Parser choice = config string (`config.parser`, env `PARSER`).
- **C3. Field-alias normalization in ONE place (getters), not scattered `.get()`** — upstream parsers drift field names (`img_caption↔image_caption`, `table_body↔table_data`); centralize to prevent silent data loss. — `RAG-Anything/parser.py:1024-1042` (_FIELD_ALIASES); `utils.py:25-31, 61-86`.
- **C4. Path-traversal hardening on parser-EMITTED media paths** — parser output is untrusted (subprocess/3rd-party); verify `is_relative_to(base)`, reject symlinks/oversize. — `RAG-Anything/parser.py:1060-1069`; `utils.py:179-214`.
- **C5. Delegate text chunking to the engine; the adapter only OWNS the hard part (multimodal→text render).** Avoid two competing chunkers. — `RAG-Anything/utils.py:224-256`.
- **C6. Provider/parser/reranker selection = config string + factory, never `if provider==` in business logic** (the one DI gap to flag: tldw embeddings uses an if/elif ladder vs a registry dict — `Embeddings_Create.py:1831-2054`).

## D. STRUCTURAL structure detection — header/table/section by FORM, NEVER vocab

- **D1. Heading level = COUNT of leading `#` (`^(#{1,6})\s+(.+)$`, `len(group(1))`)** — a depth integer, language-independent. — `structure_aware.py:176, :384`; `RAG-Anything modalprocessors.py:228-229` (`'#'*text_level`); `adaptive parsing.py:1059-1067`.
- **D2. Section ancestry = level-stack pop/push over header depths** (breadcrumb `H1>H2>H3`), works for any language. — `structure_aware.py:711-722, :696-752`. Title span ends at next title with level ≤ own. — `adaptive parsing.py:463-471`.
- **D3. Element type decided by structural regex SHAPE:** code=fenced ```` ``` ````/`~~~` (DOTALL); table = `|`-lines + `^[\s\|:\-]+$` separator; list = `^\s*[-*+]\s+` or `^\s*\d+\.\s+`; quote = `^>\s+`; else paragraph. — `structure_aware.py:175-184, :459-533`.
- **D4. Two-pass span-claiming: extract high-priority structures first (code→table→header→list), record (start,end), skip later matches inside a claimed range, sweep gaps as paragraphs.** Precedence = extraction ORDER + range-masking, NOT keyword priority lists. Output sorted by source offset (chunk order == doc order). — `structure_aware.py:345-457, :567-569`.
- **D5. Block boundaries are emitted by the PARSER from element TYPE + token-size** (split between two TEXT blocks only if both <100 tok; never inside a table). — `adaptive parsing.py:436-442, 871-880`.
- **D6. Structured-data structure read from the format's own grammar:** JSON = parsed-tree traversal (with non-parsing depth guard vs recursion-bomb); XML = tree (XXE-guarded, reject DOCTYPE/ENTITY); Python = `ast.parse` blocks (byte-accurate line spans + fallback). — `json_xml.py:113-140, :54-62`; `code_ast.py:76-119, :80-82`.
- **D7. Table column count derived structurally** (split on `|`, count cells; generate `Col{i+1}` when header absent) — never assumed/named. — `structure_aware.py:64, :77, :108, :535-565`; `RAG-Anything utils.py:34-58`.
- **D8. Setext/ALL-CAPS heading detection keys on typography/case, not words.** — `agentic_chunker.py:233-242`; `open-notebook chunking.py:266-319` (HTML/MD scored by MARKUP TOKENS `<!DOCTYPE`,`<div`,`^#{1,6}`,code fences — never domain terms).
- **D9. Citation/locator schema is format-structural & uniform:** page/paragraph/line/slide/row/col/sheet/timestamp_ms/bbox_quad — one numeric schema spans PDF/DOCX/PPTX/XLSX/audio, zero per-format branching. — `ChromaDB_Library.py:1153-1174`; `RAG/chunk_metadata.py:28-44`.

## E. Chunking — strategies + template-per-doctype + multilingual-no-vocab

- **E1. Strategy + Protocol + ABC, uniform contract returning EXACT `start_char`/`end_char` spans** (lossless, citation-safe slice from original). — `base.py:65-104, 217-299`.
- **E2. Template = DATA (JSON, DB-seeded), operations = REGISTRY.** Doc-type (academic/legal/chat/code) is a JSON row; engine knows only generic op names in a dict. Add doc-type = add JSON; add transform = `register_operation()`. NO `if doctype=='legal'` anywhere — legal is just JSON `method='paragraphs'`. — `templates.py:97-122, 124-168`; `template_initialization.py:198-207`.
- **E3. Stage ops are generic verbs** (normalize_whitespace, extract_sections, merge_small, filter_empty) parameterized by config; SAME ops serve every doc-type, only JSON params differ. — `templates.py:344-532`.
- **E4. Dual-schema / key-alias template loader** (rich `stages` form + flat `{preprocessing,chunking,postprocessing}`; `{type,params}` and `{operation,config}`) — schema-evolution tolerance without versioned classes. — `templates.py:637-695, 180-186`.
- **E5. 3-tier template provenance with last-resort in-code minimal set, narrow exception tuples; idempotent updater diffs JSON before write, only touches `is_builtin` rows** (never clobbers user templates). — `template_initialization.py:92-211, 299-320`.
- **E6. Two-stage chunking: structure-aware primary (header) → token-bounded secondary that re-splits ONLY oversized blocks.** Keeps small sections intact AND guarantees every chunk fits the embedder window. — `open-notebook chunking.py:398-415, 470-472`.
- **E7. Token-based (not char-based) sizing with explicit headroom below the embedder ceiling; clamp+warn on misconfig.** — `open-notebook chunking.py:33-118` (CHUNK_SIZE=400, ~20% under 512-tok BERT). Binary-search char crop for token-exact last-resort split. — `splitters.py:142-181`.
- **E8. Drop sub-MIN_CHUNK_SIZE fragments but NEVER nuke a doc to zero chunks** (guard `len(chunks)>1` / `if kept`). — `open-notebook chunking.py:483-491`.
- **E9. Chunk at WORD boundaries, never raw char offsets** (cheapest correct floor). — `NotebookLlama cell 18`.
- **E10. Recursive split-then-merge:** ordered separator hierarchy `['\n\n','\n',' ','']` (paragraph→line→word→char), regex-capable, two merge modes, overlap with intelligent re-split of oversized overlap parts. — `splitters.py:7-392, :237-257`; `splitters.py:28` (is_separator_regex).
- **E11. Semantic boundary = statistical signal (TF-IDF adjacent-sentence cosine DROP), gated by min/max size, fallback to single chunk on all-stopword text** — domain/language-neutral, no topic dictionary. — `semantic.py:205-255, 175-188`.
- **E12. Table serialization in multiple styles (markdown / entity `Row N: header: value` / narrative / compact), option-selectable** so rows are individually embeddable/self-describing. — `structure_aware.py:71-156`.
- **E13. Tables sub-split size-aware at PARSE time, header re-attached to every fragment** — never a half-table chunk. — `adaptive parsing.py:926-953, 70-101, 527-565`.
- **E14. Multimodal block → self-contained retrievable text chunk + windowed context enrichment** (N neighbor blocks, token-budget-truncated) so a table/image chunk is interpretable in isolation. — `RAG-Anything modalprocessors.py:139-210, 471-540`.
- **E15. Grapheme-boundary-safe end expansion (unicodedata Mn/Me/Cf/VS/ZWJ/skin-tone)** — chunk ends never split a combining mark/emoji/CJK cluster. — `base.py:154-215`.
- **E16. Config/options-driven with safe-default constants; optional NLP libs (jieba/fugashi/konlpy/pythainlp/pysbd/nltk/sklearn) probed & degraded, never crash.** — `base.py:126-152`; `multilingual.py:230-297`.

## F. Chunk-quality / block-integrity gating — measurable, label-free

- **F1. Block-Integrity = fraction of parser-emitted blocks (split_points) NOT cut by a chunk boundary, from char offsets + tolerance — ZERO human labels.** Regression-gate ANY chunking change on ANY corpus/language. — `metrics.py:264-307`.
- **F2. Lossless-coverage INVARIANT: `assert check_chunk_gaps(...)` after EVERY strategy run; repair gaps first; overlap-tolerant relocation.** Fail loud — no silent source-text dropping. — `split_documents.py:127-134`; `postprocessing.py:66-98, 128-151, 100-126`.
- **F3. Intrinsic cohesion/coherence metrics (ICC vs chunk's own embedding, DCC vs sliding window), overlap-safe, normalized [0,1]** — "did the chunk keep coherent content together" without retrieval/QA labels. — `metrics.py:53-148, 150-262`.
- **F4. Per-document adaptive strategy selection = weighted argmax over metric pivot; NaN-skipping; weights = pure config dict** (a failing metric degrades gracefully, doesn't crash selection). — `analysis.py:225, 294-327`.
- **F5. Chunking metrics layer = Prometheus-or-NoOp (Null-Object), per-method labels: duration, chunk-size histogram, chunks/request, input-bytes, cache hit/miss, errors-by-type.** Metrics never a hard dep. — `Chunking/utils/metrics.py:14-46, 49-138, 240-255`.
- **F6. Truncation/data-loss is OBSERVABLE** — report exactly where a size budget cut. — `NotebookLlama cell 10` (`Reached {max_chars} at page {n}`).

## G. Embedding pipeline — batch / cache / warmup / shard

- **G1. Cache key = FULL identity tuple `provider:model:sha256(text)[:base_url]`; write-back key recomputed from the ACTUAL serving provider/model after fallback.** Prevents fallback-vector cache-poisoning (different dim/distribution under primary key). — `async_embeddings.py:569-573, 608-617`.
- **G2. Attention-mask-aware mean pooling, `clamp(min=1e-9)` denominator; per-model-family pooling selected STRUCTURALLY from model-name capability** (Qwen3→last-token + instruct template), not per-deployment hardcode. — `Embeddings_Create.py:1336-1408`.
- **G3. Mean-pool-with-double-normalization for long docs** (normalize each chunk → mean → re-normalize) so one doc = one unit-length comparable vector. — `open-notebook embedding.py:55-108`.
- **G4. Adaptive batching keyed by `(provider, model, config-fingerprint=SHA256)`; throughput-feedback resize; idle queues self-terminate.** Only identical-config requests batch together (mixing configs is incorrect). — `request_batching.py:688-728, 752-801, 281-298`.
- **G5. Per-model load lock + in-use refcount + memory-budget admission (evict→recheck→refuse), never evict mid-encode.** OOM-safe for GB-scale local models. — `async_embeddings.py:343-456`; `Embeddings_Create.py:1862-1918`.
- **G6. Backoff retries ONLY 429/5xx/network; re-raise 4xx/auth/ValueError immediately** (narrow retryable surface — blanket retry hides bugs & burns quota). — `Embeddings_Create.py:979-1021`.
- **G7. Idempotent embedding: DELETE-then-INSERT keyed on source, preserve `order` index for sequence reconstruction.** Safe to retry; no stale chunks after re-ingest. — `embedding_commands.py:413-465`.
- **G8. BFloat16→float32 fallback on unsupported-scalar errors; truncation/padding to `max_length`; tokenization delegated to the model's own tokenizer.** Language-agnostic by construction (UTF-8 only for hashing). — `Embeddings_Create.py:1410-1449, 1383-1389`.

## H. Vector store + reliability

- **H1. Self-describing collection: store `embedding_dimension` + `source_model_id` in metadata; on mismatch DELETE+RECREATE (sampled-vector fallback for legacy).** Dimension drift after model swap is silent corruption. — `ChromaDB_Library.py:1276-1309, 1288-1290`.
- **H2. L1 in-mem LRU (byte-bounded) → L2 disk → L3 Redis, access-count promotion.** — `multi_tier_cache.py:770-793`.
- **H3. L2 disk write = NamedTemporaryFile → flush → `os.fsync` → `os.replace` (atomic), evict before commit, temp cleanup on every failure branch.** Crash-consistency: partial file never observable. Index written same way. — `multi_tier_cache.py:350-433, 496-508`.
- **H4. Restrictive `_SafeUnpickler.find_class` allowlist on ALL cache deserialization (disk + Redis)** — closes pickle-RCE at the untrusted-deserialization boundary. — `multi_tier_cache.py:64-94, 305, 621`.
- **H5. Dead-letter queue: classify error → per-reason recovery; non-retryable (INVALID_INPUT/MODEL_NOT_FOUND) excluded; rate-limit capped exp-backoff `60*2^n` max 300s.** Transport-degrade vs client-fail-loud. — `error_recovery.py:185-205, 214-250, 376-398`.
- **H6. Sharding = consistent hash (150 virtual nodes, md5 over ring), scatter-gather merge by distance.** Routing is mathematical, never content/domain-keyed. — `sharding.py:78-101`.

## I. Retrieval / rerank / grade / faithfulness

- **I1. Hybrid FTS+vector with RRF fusion `1/(k+rank)`, k=60, alpha-weighted, dedupe on doc id.** Rank-based = score-scale-invariant → merges incomparable bm25 & cosine correctly. — `database_retrievers.py:1908-1966`.
- **I2. Cross-backend FTS polarity handled: SQLite bm25 (lower-better, ASC) vs Postgres ts_rank (higher-better, DESC) normalized before fusion.** Silent rank-inversion bug class. — `database_retrievers.py:1001-1075, 810`.
- **I3. Reranker = Strategy + factory dispatch on Enum; add provider = class + one elif, callers untouched.** Even generative-reranker special-case isolated in factory by model-name. — `advanced_reranking.py:205-236, 1435-1473`.
- **I4. Two-tier reranker injects a synthetic "irrelevant" sentinel into CE+LLM passes → logistic calibration → sentinel score = answer-generation GATE** (if real docs barely beat the decoy, REFUSE not fabricate). Strongest anti-hallucination idea; calib weights env-tunable. — `advanced_reranking.py:1566-1650`.
- **I5. Query-granularity routing (BROAD/SPECIFIC/FACTOID) with ZERO LLM calls (regex + length + wh-word) → deterministic param map; confidence normalized & inspectable.** Microsecond latency. — `granularity_router.py:104-252`. ⚠ defaults are ENGLISH regex (`granularity_router.py:22-47`) — patterns ARE constructor-injectable, MUST supply per-locale packs.
- **I6. Late-interaction (ColBERT-style) span max-sim rerank at QUERY time on the shortlist — no re-indexing.** Drop-in quality boost; span params config. — `advanced_retrieval.py:55-76, 100-214`.
- **I7. Parent/hierarchical expansion (5 strategies) with distance-decay `1/(1+dist*0.2)`, siblings 0.7×, diversity penalty capping `max_expansion_factor`** — added context never outranks the true hit; one doc can't flood the window. — `parent_retrieval.py:311-348, 433-465`.
- **I8. Document grading with tiered fallback: LLM-JSON → heuristic parse → retrieval-score floor; batched concurrent, bounded by batch_size, `gather(return_exceptions=True)`.** Grader can only improve recall, never destroy it (LLM outage ≠ dropped shortlist). — `document_grader.py:245-360, 362-452`.
- **I9. Claim-level faithfulness: extract atomic claims → verify each vs context → score=supported/total; per-claim breakdown; safe degenerate cases (empty answer→1.0, empty context→0.0, no claims→1.0).** Hallucinations debuggable, no divide-by-zero. — `faithfulness.py:137-242, 208-225`.
- **I10. Dual citations: academic (MLA/APA/...) for humans + chunk-level (chunk_id, source_doc_id, location, confidence=retrieval-score, usage_context) for verification.** Verifiable provenance map, not free-text. — `citations.py:466-603, 664-671`.
- **I11. FTS query built by character-class STRUCTURE** (multi-token/hyphen/paren/unicode-dash → quoted-phrase vs prefix-match), never by recognizing words. ⚠ `to_tsquery('english', …)` hardcoded — MUST thread `chunk_language`. — `database_retrievers.py:2004-2020, 1033, 1037`.

## J. Metadata as optional HINT, not a GATE

- **J1. Method/strategy chosen by config or STRUCTURAL cue, never content vocabulary** — dispatcher resolves from explicit `method` option (default from config); only nudges code→code_ast when language hint starts with `'py'`. — `chunker.py:1957-1976`.
- **J2. Doc-type selection by NAME + structural classifier (media_type match + filename/title/url regex, weighted, min_score gate)** — caller-supplied regex over METADATA, never scanning body for keywords. — `templates.py:759-794`; `chunking_options.py:325-372`.
- **J3. Chunk-method DEFAULTS chosen by media_type STRUCTURE** (ebook→ebook_chapters, audio/video→sentences, document→larger size/overlap) — structural class, not reading the text. — `chunking_options.py:114-143`.
- **J4. Language is a PASS-THROUGH metadata field (caller/config supplied), never a hardcoded default; first-class on the chunk; explicit precedence when overridden.** — `chunking_options.py:149-163`; `chunk_metadata.py:66`; `templates.py:236-245`.
- **J5. Detection precedence is policy: extension primary; markup-heuristic override ONLY when ext=PLAIN AND confidence ≥ threshold (0.8).** One tunable governs all overrides — metadata refines, doesn't dictate. — `open-notebook chunking.py:322-362`.
- **J6. Sink/delete behavior gated by declared POLICY (canonical vs mirror) + binding presence — a structural state-machine, not content inspection** (won't destructively trash unless source declared authoritative; optimistic-concurrency via expected_version). — `media_sink.py:31-58`; `ingestion_sources_worker.py:193, 204-213`.

## K. Multi-bot / multi-language / multi-format / config-driven invariants

- **K1. ZERO brand/customer/domain literals in any structure-deciding path** (grep `<bot-slug>|<industry-noun>|...` = 0 across chunking/parsing/embedding/retrieval cores). Built-in template NAMES (academic_paper/legal_document/...) are doc-TYPE archetypes living as overridable JSON seeds, not brands. — verified across `structure_aware.py / multilingual.py / base.py / templates.py / parsing.py / raganything/*`.
- **K2. Language auto-detected by Unicode SCRIPT RANGES** (Hiragana/Katakana→ja, CJK→zh, Thai, Devanagari→hi, Cyrillic→ru, Hangul→ko, Arabic) + `auto`/`detect` override; structural detection NEVER depends on it. — `chunker.py:2406-2428`; `multilingual.py:44-116`; `templates.py:430-448`.
- **K3. Per-language behavior is DATA in LanguageConfig** (sentence_delimiters incl. CJK 。！？/Hindi danda ।/Arabic ؟/Spanish ¡¿/French «», `requires_spacing` flag, RTL marks, chunk-size multiplier). Add a language = add config, not code. — `multilingual.py:132-185, 423-445`.
- **K4. Runtime prompt-language swap of ALL templates with English fallback; `register_prompt_language(code, prompts)` adds a language without code change** (ships lazy-loaded Chinese pack). — `RAG-Anything prompt_manager.py:64-81, 115-131`.
- **K5. ALL knobs config/env-sourced with named-constant defaults** (preserve_tables, contextual_header_mode, similarity_threshold, batch sizes/TTLs, rate limits, calib weights, RRF-k, span params). No magic numbers in business logic. — `base.py:126-152`; `multi_tier_cache.py:740-759`; `rate_limiter.py:163-233`; `RAG-Anything config.py:18-115`.
- **K6. Multi-format parity: PDF/DOCX/XLSX/CSV/PPTX/HTML/TXT/MD/XML/EPUB/JSON all admitted via one ext→key registry and converge to one `{text, source_format, raw_metadata}` record** through swappable parser delegates. — `Upload_Sink.py:248, 150`; `local_directory.py:127-164`. Non-prose blocks (list `items`, table `rows`) still synthesize chunkable text. — `block_to_chunks.py:165-183`.
- **K7. Security is first-class on the highest-risk path (archives):** denylist on claimed AND on-disk name, depth-limited recursion, zip-bomb (count+declared+running uncompressed), path-traversal/symlink/encrypted-member rejection, Py3.12 tar `filter='data'`; each member re-validated through the same `validate_file`. — `Upload_Sink.py:537-561, 745-1066`.
- **K8. Streaming size enforcement DURING write (per-media-type config cap, abort mid-stream)** — a 10GB upload rejected after 1 over-limit chunk, not after buffering. Same logic shared by upload + URL-download. — `input_sourcing.py:358-395`; `download_utils.py:152-237`.
- **K9. Config-as-contract for chunking knobs: allow-list of supported keys + explicit rejection of unknown** (raises "Unsupported … Supported: …" instead of silently dropping). — `chunking_options.py:10-38, 90`.
- **K10. Idempotent re-ranking: capture immutable base score, clamp, re-sort** so repeated stage application can't compound multipliers. — `enhanced_chunking_integration.py:383-406`.

---

## AUDIT VERDICT TABLE (fill per ragbot file)

| § | Principle | ragbot evidence (file:line) | ✅/❌ | Note |
|---|---|---|---|---|
| A1 | One canonical ingest funnel | `interfaces/http/routes/documents.py` | | `POST /api/ragbot/documents/create` |
| B1-B4 | mime→ext→byte-sniff + provenance | | | |
| C1-C2 | Parser Port+Registry | | | |
| D1-D9 | Structure by FORM not vocab | | | grep brand/keyword=0 |
| E1-E16 | Strategy + template-per-doctype | | | |
| F1-F2 | Block-integrity + lossless assert | | | |
| G1,G7 | Cache identity + idempotent embed | | | |
| H1 | Self-describing collection / dim-drift | | | |
| I4,I9 | Sentinel-gate + claim faithfulness | | | HALLU=0 sacred |
| J1-J6 | Metadata = hint not gate | | | |
| K1-K2 | Zero-literal + script-range lang | | | |

**RED FLAGS to grep for (any hit = ❌):** `if doctype==`, `if provider==`, brand/domain literals in chunking/retrieval, `to_tsquery('english'`, English-only regex as the ONLY classifier, char-offset (not token) sizing, missing `assert check_chunk_gaps`, cache key without provider, fallback vector written under primary key, ext-only type trust, no per-item failure isolation.
