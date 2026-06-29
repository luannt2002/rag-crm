# TLDW_SERVER — FULL RAG SURFACE DEEP-DIVE (2026-06-27)

> Scope: the RAG surface *beyond* the chunking/embedding/retrieval core — upload boundary, per-format parsers, embedding-provider abstraction, claims/faithfulness, evaluation, RAG-as-workflow adapters, vector/DB/FTS storage, and operational helper scripts. All `file:line` are inside `_external_refs/tldw_server/`. Our-side cross-checks verified against live `src/ragbot/` (embedding registry eager-import, `EmbeddingPort` Protocol has no `capabilities()`, `reranker_resolver._lookup_platform_default` 3-tier fallback, `documents.py` X-Idempotency-Key) — all confirmed.

---

## 0. TL;DR — the RAG surface beyond the core, 5 biggest lessons

tldw_server is a mature multi-format, multi-tenant RAG platform whose *peripheral* layers are where most of the transferable expertise lives. The core engine (`unified_rag_pipeline`, ~80 keyword flags) is actually the **anti-pattern** for us — the gold is in the layers around it.

**The 5 biggest lessons (ranked by impact on our T1/T2):**

1. **Two-phase decompose-then-verify is the right anti-hallucination architecture (T1, HALLU=0).** `Claims_Extraction` extracts atomic decontextualized claims from an answer, then verifies *each* against evidence with a **typed status enum** — `HALLUCINATION / NUMERICAL_ERROR / MISQUOTED / MISLEADING / CITATION_NOT_FOUND / CONTESTED` (`claims_engine.py:417 determine_verification_status`). This maps 1:1 onto our Anti-HALLU 4-loại-số and localizes *which sentence* is unsupported — something our whole-answer grounding float cannot do. **This is the single highest-value adoption** and directly answers the 2026-06-03 spa-07 lesson (number fabrication is a verification-tier problem, not a sysprompt-tier one).

2. **Verification thresholds and the judge itself must be measured, not trusted.** Their eval harness *clamps* the LLM judge with deterministic lexical-overlap floors/ceilings (`rag_evaluator.py:478-483`: faithfulness coverage≤0.05 → cap 0.55), and gates ranking behind a hard **grounding gate** before quality (`rag_answer_quality_execution.py:671`). This is "no-guess must-measure" made executable — but their tokenizer drops Vietnamese diacritics (`rag_answer_quality_execution.py:1146 re.findall(r'[a-z0-9]+')`), so we must fix the tokenizer before borrowing the clamps.

3. **The byte-sniff type-detection layer we're told to have (CLAUDE.md `mime→ext→byte-sniff`) is fully built in their `FileValidator`.** Three layers: ext-candidate parse (multi-dot aware) → allow/deny lists → `puremagic`→`python-magic`→`mimetypes` content sniff with a **"detected-MIME beats extension" hard-fail** (`Upload_Sink.py:639-690`). We have the canonical endpoint + idempotency but lack this unified sniff layer.

4. **Content-change must mechanically invalidate vectors in the same transaction.** Their storage layer couples any content rewrite to `content_hash=sha256 + chunking_status='pending' + vector_processing=0` *atomically*, then best-effort invalidates intra-doc vectors after commit (`synced_document_update_ops.py:64-69, 127-138`). This is the structural fix for our recurring "embedding NULL/stale" bug class — the DB row itself becomes the source of truth that vectors are stale.

5. **We are already ahead in three places — do NOT regress them.** Our `reranker_resolver` has a real 3-tier fallback (binding → `_lookup_platform_default`/system_config → NullReranker, verified `reranker_resolver.py:188/203`); their embedding registry has *no* resolution-fallback chain and *no dim enforcement* (`capabilities() → dimensions_default=None` for openai/google/hf). Our `X-Idempotency-Key` request dedupe (`documents.py:11,133`) is stronger than their media_id-reprocess idempotency. Keep ours; borrow only the lazy-import + capabilities surface + normalize-contract pieces.

---

## 1. Upload/ingest API boundary — how requests become documents (type detect, idempotency, routing)

**Architecture (the inverse and the parallel of ours).** tldw splits ingest into per-format endpoints (`process_documents.py`, `process_pdfs.py`, `process_ebooks.py`, `process_emails.py`, `process_code.py`, `process_videos.py`, `process_audios.py`) — *not* one canonical endpoint like our `POST /api/ragbot/documents/create`. **But** every endpoint funnels through a shared 4-stage toolkit, so the per-endpoint code differs only by an `allowed_extensions` set + a processor closure. That shared toolkit is exactly the Strategy/registry shape we want, just expressed as "pass different args" rather than "register an adapter."

**The 4 stages, with `file:line`:**

- **Stage 1 — input contract.** Endpoint resolves a Pydantic `Process*Form` via `Depends(get_process_*_form)`, normalizes the legacy `urls=['']` sentinel (`input_contracts.py:9 normalize_urls_field`), then `media_mod._validate_inputs(media_type, urls, files)` (`process_documents.py:129`). Guards run first: `guard_storage_quota` + `require_within_limit(STORAGE_MB/API_CALLS_DAY)` (`process_documents.py:71-75`).
- **Stage 2 — sourcing.** Everything goes through one temp dir: `TempDirManager(prefix='process_doc_')` (`input_sourcing.py:51`, auto-rmtree on exit). Uploads → `save_uploaded_files(...)` (`input_sourcing.py:83`); URLs → `download_url_async(...)` (`download_utils.py:239`). Both take the endpoint's `allowed_extensions`.
- **Stage 3 — type detection + validation (the part we lack).** `save_uploaded_files` does: `_extension_candidates` multi-dot parse (`Upload_Sink.py:271`, handles `.tar.gz`) → hard `blocked_extensions` denylist `.exe/.sh/.dll` (`input_sourcing.py:144`) → allow-list filter (`input_sourcing.py:206`) → infer media key for size cap via `EXT_TO_MEDIA_TYPE_KEY` (`Upload_Sink.py:248`) → **stream-write 1MB chunks enforcing `max_cfg_bytes` during write** (`input_sourcing.py:384-433`, defeats lying Content-Length) → `FileValidator.validate_file` content-sniff (`Upload_Sink.py:639 puremagic.from_file`) + optional YARA + size. The **hard-fail rule**: a detected MIME outside the allow-list is rejected and *not* overridden by the extension guess (`Upload_Sink.py:639-690`). For URLs, `download_url_async` adds `_validate_egress_or_raise` SSRF guard (`download_utils.py:270`), Content-Disposition filename + content-type→ext fallback (`download_utils.py:477`), and `_validate_target_path` traversal guard (`download_utils.py:67`).
- **Stage 4 — batch orchestration.** Each endpoint defines a local `_<type>_batch_processor(items: list[ProcessItem])` closure fed to the shared `run_batch_processor` (`pipeline.py:41`). `ProcessItem` (`pipeline.py:23` dataclass: input_ref/local_path/media_type/metadata) is the uniform intermediate; `run_batch_processor` tallies Success+Warning=processed, Error=errors (`pipeline.py:69-79`) → status code **200 all-ok / 207 partial / 400 none** (`process_documents.py:506`). The actual parse runs in `loop.run_in_executor(None, partial)` (sync libs off the event loop).

**Idempotency / persistence is decoupled.** The `process-*` endpoints are explicitly **no-DB** (`db_id=None`, "Processing only endpoint."). Persistence + embed live in `reprocess.py`: `clear_unvectorized_chunks + process_unvectorized_chunks` (`reprocess.py:286-300`), then `background_tasks.add_task(_generate_embeddings)` (`reprocess.py:352`), with a **conflict-retry-with-backoff** on the ready-state flip (`reprocess.py:96-112 _mark_embeddings_complete_with_retry`). **Important caveat:** their idempotency is *media_id-reprocess*, NOT request-key dedupe — our `X-Idempotency-Key` (`documents.py:133`) is the stronger contract. Keep ours.

**What to adopt (mapped to our files):**
- Port `FileValidator.validate_file`'s three-layer sniff + "detected-MIME-beats-extension" hard-fail into our `documents.py` flow's `detect_parser` step — this is the missing byte-sniff layer the `doc-format-control` skill requires.
- Add **207 Multi-Status** + structured per-item Error dicts (`pipeline.py:69`) for bulk ingest observability.
- Port the URL hardening (`_validate_egress_or_raise`, `_validate_target_path`, **streaming size cap during write**) before we accept `source_url` ingest.
- Adopt archive-safety (`validate_archive_contents` `Upload_Sink.py:745`: zip-bomb caps, nested-depth, symlink/encrypted-member rejection) if/when we accept `.zip` bundles.

**Do NOT copy:** inline chunk-size magic numbers `500/200/1000` repeated across endpoints (`process_documents.py:563`) — fails our zero-hardcode grep; hardcoded `language='en'` TODO (`media_embeddings.py:294`) — we already thread language per-bot; the ~80% boilerplate re-chunk block copy-pasted across 6 endpoints — keep that in the shared toolkit, not the route.

---

## 2. Document parsers per format — how each preserves structure/headers/tables

**Architecture (the confirming anti-pattern for our Port+registry).** tldw uses one library module per media family with a `process_<type>(...)` function. **Critically, parser selection is `if/elif` on a `parser` string** (`PDF_Processing_Lib.py:651-686`: `pymupdf4llm`/`pymupdf`/`docling`) and format selection is an extension `elif` ladder (`Plaintext_Files.py:176-303`) — **there is NO central parser registry or Port ABC.** Adding a format = editing the elif ladder. This *confirms our `detect_parser` registry + Port design is the better choice* — we should borrow only the per-format extraction *logic*, never their selection mechanism.

**The one thing they do brilliantly: a single uniform result-dict contract across ALL formats.** Every `process_*` returns the same key set: `status / input_ref / media_type / parser_used / content / metadata / chunks / analysis / keywords / error / warnings / analysis_details` (PDF `process_pdf:490`; EPUB `process_epub:585`; markup/text `_process_markup_or_plain_text:1187`; doc `process_document_content:333`; email `process_email_task:336`). This is exactly our "one canonical structured-markdown output" goal — and theirs adds **`parser_used` + `warnings[]` + a Success/Warning/Error tri-state** that ours lacks. Their tri-state lets the owner see "ingested but OCR failed" instead of our binary silent loss.

**Structure/header/table preservation, per format:**
- **PDF.** `normalize_pdf_text_for_storage` (`PDF_Processing_Lib.py:239-318`) is the standout: it reflows *only* soft-wrapped lines inside non-structural paragraph blocks and **never touches headings/lists/tables/code-fences/page-markers** (detected by `_is_structural_line:154`), with **CJK-aware no-space joining** (`_should_join_without_space:196`) and de-hyphenation (`:208`). Heading inference from font size (`font>20/16/14 → #/##/###`, `PDF:345-355`) is brittle vs our structured-markdown contract — borrow the reflow, skip the font heuristic. OCR tables become typed chunks (see below).
- **EPUB.** BeautifulSoup maps `h1-h6 → '#'*level`, `ul/ol → '-'/'1.'` (`Book_Processing_Lib.py:234-244, 380-394`); metadata from Dublin Core (`extract_epub_metadata_from_epub_obj:256`); extractor fallback chain (filtered→basic `read_epub`, `:730-748`).
- **HTML/XML/text.** `convert_document_to_text` (`Plaintext_Files.py:147`) branches on extension — HTML via `html2text` after BeautifulSoup strips script/style/noscript (`:240-251`); **XML via `defusedxml`** (`_xml_to_text_simple:135`, XXE/billion-laughs safe); DOCX via `docx2txt`; RTF via `pypandoc`.
- **Tables/figures as first-class chunks (the #1 RAG failure mode).** `_extract_analysis_extra_chunks_for_indexing` (`persistence.py:314-463`) turns OCR tables and VLM detections into typed retrieval chunks tagged `chunk_type=table|media` with page/bbox/score metadata, deduped by content+metadata key. This directly serves our table-taxonomy stress test.

**Cross-format governance (worth stealing wholesale):** `persistence.py` holds a declarative per-media-type `_METADATA_CONTRACTS` dict (`:734-868`) + `_METADATA_COMMON_TYPED_KEYS` (`:715-732`), validated by `_evaluate_metadata_contract_issues` (`:911`) under an **off/warn/error policy** resolved from form-data→env→config (`_resolve_metadata_contract_policy:871`). This guarantees every format produces minimally-valid metadata before it reaches the index — domain-neutral, no app-injected text, pure validation (sacred-rule-10 compliant).

**Adopt to us:** uniform result-dict + `parser_used` + `warnings[]` + tri-state as our parser-adapter emit contract; port `normalize_pdf_text_for_storage` into our PDF/HTML adapters (CJK rule reusable for Vietnamese); replicate the declarative metadata contract as a config-driven step in our normalizer; emit `chunk_type=table|media` typed chunks; mandate `defusedxml` in our XML/sheet adapters. **Keep our registry** — confirmed superior.

---

## 3. Embedding provider abstraction (Port+registry) — adding a provider without touching callers

**Architecture (textbook Port+Strategy+Registry — and lazier than ours in one good way).** The Port is `EmbeddingsProvider(ABC)` (`providers/base.py:118-148`) with two abstract methods: `capabilities()->dict` and `embed(request, *, timeout)->dict`, both OpenAI-shaped. The registry `EmbeddingsProviderRegistry` (`embeddings_adapter_registry.py:18`) holds `DEFAULT_ADAPTERS: dict[str,str]` (`:21`) mapping provider name → **dotted import-path STRING** (e.g. `"...openai_embeddings_adapter.OpenAIEmbeddingsAdapter"`), resolved **lazily via `importlib.import_module` on first use** (`_resolve_adapter_class:42-50`) and cached (`:52-69`).

**Adding a provider = add one adapter file + one row in `DEFAULT_ADAPTERS`** (or `register_adapter` at runtime). Zero changes to any caller. The chat side mirrors it: `Summarization_General_Lib.py:140` consumes purely via `get_registry().get_adapter(provider)` then `adapter.chat/stream`.

**Two patterns we should steal:**
1. **Lazy importlib string-registry.** Heavy/optional deps (`mlx_lm`, `sentence-transformers`) are never imported unless that provider is selected. **Our `registry.py` hard-imports every adapter at module load** — verified: it imports `BkaiVnEmbedder`, `JinaEmbedder`, `LiteLLMEmbedder`, `ZeroEntropyEmbedder` eagerly at top. So one broken/optional adapter import (e.g. self-hosted `bkai_vn` needing torch) breaks embedding for *all* providers, including litellm/zeroentropy users. Converting `_REGISTRY` to dotted-path strings resolved inside `build_embedder()` hardens our existing "unknown provider falls back to default" safety.
2. **`capabilities()` on the Port.** Returns `dimensions_default / max_batch_size / default_timeout_seconds` (`base.py:127-135`); registry aggregates via `get_all_capabilities` (`:71-81`). **Our `EmbeddingPort` has no capability surface** — verified: it has only `health_check/embed_batch/embed_one/close`. Dim/batch knowledge is scattered. Adding `capabilities()` feeds a `/health/models` introspection and our threshold-drift recalibration discipline.

**Per-adapter `_normalize` discipline (adopt as a Port contract test).** Each adapter coerces provider-specific JSON (Google `{embedding:{values}}`, HF `list[list[float]]`, OpenAI `{data:[...]}`) into the canonical envelope via private `_normalize` (`openai_embeddings_adapter.py:37-48`; `google:32-49`; `hf:39-67`; `mlx:661-666`). A Port-level test "every embedder returns `list[list[float]]` of spec dim regardless of upstream shape" guards against exactly the Jina/ZE response-shape drift (matryoshka 2560 vs 1280) we've hit.

**Other transferable pieces:** `MLXSessionRegistry` (`mlx_provider.py:131`) shows the same Port hosting a heavyweight in-process model with bounded-concurrency semaphore + warmup + **atomic swap-keeps-previous-on-failure** + metrics — a reference for our future self-hosted `bkai_vn` lifecycle; `embedding_queue.py` confirms our Redis-Streams ingest→embed decoupling is industry-standard (priority remap, is_test_mode short-circuit, fails-soft via `logger.debug`).

**Where WE are stronger (do NOT regress):** their registry has **no resolution-fallback chain** (per-bot binding → system_config → NullObject) like our `reranker_resolver.py:188/203` `_lookup_platform_default`, and **no dim enforcement** (`capabilities()→dimensions_default=None`, dim passed through opportunistically `openai_embeddings_adapter.py:83-89` but never validated). We lift dim from `EmbeddingSpec` at runtime and pin per-column dims — keep that.

**Caveat for adoption:** their per-provider behavior flags are env-var reads (`LLM_EMBEDDINGS_NATIVE_HTTP_*`, `openai_embeddings_adapter.py:23`). For us these must route through `system_config` (zero-hardcode rule), but the two-step rollout idea (set provider key, then flip flag) is already what our `_FLAG_GATED_PROVIDERS` does for `bkai_vn` (verified `registry.py`).

---

## 4. Claims/faithfulness/anti-hallucination engine — what we could adopt for grounding

**This is the highest-value bucket for our T1 + HALLU=0 sacred rule.** The engine (`Claims_Extraction/`) is a 2-phase decompose-then-verify pipeline on Protocol-based DI.

**Phase 1 — extract atomic claims.** Two Protocols: `ClaimExtractor.extract()` (`claims_engine.py:173`) and `ClaimVerifier.verify()` (`:185`). Extraction is Strategy+registry: `_extract_claims_by_mode` (`:1478`) builds `strategy_map={"heuristic","ner","aps","llm"}` dispatched via `run_async_claims_strategy` (`extractor_registry.py:170`) with graceful fallback to heuristic. `LLMBasedClaimExtractor` (`:721`) prompts the LLM for **atomic, decontextualized, standalone propositions** as strict JSON. Each claim is aligned back to source via a **3-tier matcher** (exact → normalized casefold → fuzzy token sliding-window) producing exact char offsets, and it is **Unicode/multi-script aware** — `_NON_SPACED_SCRIPT_RE` covers CJK/Hangul/Thai/Lao/Khmer/Myanmar (`alignment.py:15-17, 311`), directly relevant to Vietnamese.

**Phase 2 — verify each claim, emit a typed status.** `HybridClaimVerifier.verify` (`:988`): re-rank candidate docs with numeric/date overlap bonus (`_score_doc:1019`) → local NLI model (`roberta-large-mnli`, `:1041-1096`) → LLM fact-check judge (`:1098`) → deterministic decision tree `determine_verification_status` (`:417`) emitting **`VERIFIED / REFUTED / HALLUCINATION / NUMERICAL_ERROR / MISQUOTED / MISLEADING / CITATION_NOT_FOUND / CONTESTED / UNVERIFIED`**. `classify_claim_type` (`:289`) gates type-specific checks: STATISTIC → numeric-precision tolerance (`guardrails.check_numeric_precision:1278`), QUOTE → literal substring match (`:1289`). `doc_only_mode` escalates no-evidence to HALLUCINATION (`:496`).

**The FVA falsification layer (opt-in high-assurance).** Beyond confirming evidence, `should_trigger_falsification` (`falsification.py:83`) flags low-confidence/high-risk claims → `AntiContextRetriever` generates negation queries (`anti_context_retriever.py:73 NEGATION_TEMPLATES`) → `ClaimAdjudicator` weighs support-vs-contradict → may flip to CONTESTED/REFUTED. Budget-gated to ≤30% of budget (cost-controlled, T2).

**Why this maps onto our problems exactly:**
- Our HALLU traps are overwhelmingly numeric (prices, %, điều/article numbers). A dedicated **numeric-precision verifier** (`claims_engine.py:1276`) is the *correct-tier* fix — the antithesis of the sysprompt rules we wrongly shipped for spa-07 (2026-06-03 lesson).
- Their `VerificationStatus` enum *is* our Anti-HALLU 4-loại-số (fabricate→HALLUCINATION/CITATION_NOT_FOUND, misinterpret→MISLEADING, NUMERICAL_ERROR, MISQUOTED).
- `coverage=(verified+refuted)/total` + `precision=verified/total` (`verification_report.py:196-199`) is our mandated **Coverage metric** made first-class and per-claim.
- All thresholds are config-driven with clamped/validated defaults (`runtime_config.py:112-259`) — recalibratable post model-migration without redeploy.

**CRITICAL sacred-rule guardrail for adoption:** this must be a **verification/observability layer, NOT answer injection.** Keep claim spans/citations OUT of the answer text — surface them via an audit/report endpoint and load-test scoring only — to honor sacred rule #10 (app must not override the LLM answer). Adopt as a *post-generation grounding node + scoring harness*, never as a prepend/rewrite.

**English-keyword caveat:** regex claim-typing and anti-context negation templates (`anti_context_retriever.py:73` `'evidence against {claim}'`) are English-biased and degrade for Vietnamese; extraction/alignment/NLI/LLM-judge stay language-agnostic. Localize templates to language_packs before enabling FVA for a non-EN-locale bot.

---

## 5. RAG evaluation system — metrics/recipes/judge

**Two parallel engines.** (1) Classic metric engine `RAGEvaluator` (`rag_evaluator.py:91`): config-driven async LLM-as-judge computing relevance/faithfulness/answer_similarity/context_precision/context_recall/claim_faithfulness, each its own `_evaluate_*` coroutine run via `asyncio.gather(return_exceptions=True)` (`:184-311`), each a 1-5 rubric normalized /5.0, wrapped in `llm_circuit_breaker.call_with_breaker`. (2) Recipe engine: `recipe_runs_jobs_worker.py` → `rag_answer_quality_execution.py` which *generates* answers per candidate model then *scores* them. `EvaluationRunner` (`eval_runner.py:141`) is OpenAI-Evals-compatible with handlers (`_eval_rag/_eval_summarization/_eval_exact_match/_eval_nli_factcheck`), double-semaphore batching + per-sample `asyncio.wait_for` timeout (`_process_batch:1292`).

**The four practices to steal (all serve "no-guess must-measure"):**
1. **Judge anti-hallucination clamps.** `rag_evaluator.py:455-483`: faithfulness coverage≤0.05 → cap 0.55; relevance lexical_overlap≤0.10 → cap 0.35, ≥0.60 → floor 0.75. A deterministic floor/ceiling stops the LLM judge giving a high faithfulness score when the answer shares almost no tokens with context. **We currently trust the judge fully.** ⚠ Their `_tokenize` is `re.findall(r'[a-z0-9]+')` (`rag_answer_quality_execution.py:1146`) — drops Vietnamese diacritics. **Fix the tokenizer first.**
2. **Claim-level faithfulness** as a first-class metric (`rag_evaluator.py:313-360`): extract atomic claims → verify each → `supported/(supported+refuted+nei)`. Catches partial hallucination a holistic 1-5 score misses.
3. **Retrieval-ranking metrics** MRR + nDCG@K (`eval_runner.py:1001-1028`) computed from labeled `relevant_ids`. Our load tests only measure answer PASS/HALLU, never retrieval ranking — so we can't *numerically prove* a retrieval fix worked. This is the missing proof for Coverage-rate.
4. **fixed_context vs live_end_to_end split with FROZEN retrieval flags** (`rag_answer_quality_execution.py:60-79 _LIVE_RETRIEVAL_FROZEN_FLAGS`, `:435-487` hashed preset). Isolates generation-quality from retrieval-variance so a regression is attributable to the right layer — directly addresses our "fix sai tầng" problem.

**Plus:** a hard **grounding GATE** before ranking (`:671 grounding_gate_passed`) with auto `_derive_failure_labels` (`:1160`: hallucinated/missed_answer/bad_abstention/format_failure) — encodes T1>everything as a gate, not a weighted term; per-metric thresholds that **FAIL-LOUD on missing metrics** (`eval_runner.py:2184-2213`) — prevents silent-green from a misconfigured spec (same class as our "status=success ≠ answered" lesson); config-grid sweep + leaderboard + `ConfidenceSummary(sample_count/spread/winner_margin)` (`:491-538`) to replace ad-hoc threshold tuning during model migrations.

Recipes are an open Port/registry (`recipes/registry.py:36-66`): add an eval type = add one `RecipeDefinition` class — consistent with our Strategy+DI mindset.

---

## 6. RAG-as-workflow (adapters) — composition pattern

**Architecture.** Every workflow step is `async def run_X_adapter(config: dict, context: dict) -> dict` registered by a decorator `@registry.register(name, category, parallelizable, config_model)` on a singleton `AdapterRegistry` (`adapters/_registry.py:29-193`). Each adapter declares a `config_model` subclassing `BaseAdapterConfig` (`_base.py:36`, `extra="allow"` for forward-compat). The shared `AdapterContext` (`_base.py:13`) carries `user_id/tenant_id/run_id/prev/last/secrets` plus capability-injected callables (`is_cancelled`, `add_artifact`, `append_event`, `heartbeat`).

**The patterns worth adopting at PIPELINE-STEP granularity (our Port/registry is currently per-component):**
- **Decorator-registry-with-config_model.** `get_parallelizable()` derives the map-step set from the `parallelizable` *flag* (`_registry.py:135-143`, replacing an old hardcoded constant), and `config_model.model_json_schema()` auto-generates an introspectable catalog (`get_catalog:164`). A thin step-registry over our LangGraph nodes would let an admin `/pipeline/catalog` endpoint exist and make parallelizability metadata-driven.
- **Per-node `config_model` + passthrough-key allowlist.** `rag_search` (`adapters/rag/search.py:98-121`) whitelists ~30 passthrough keys before `**kwargs` to the engine — explicit allowlist, not blind forward. Cleaner config contract than free-form dicts, validates per-bot `pipeline_config` without touching the orchestrator.
- **Composable control steps.** retry/cache/checkpoint are themselves adapters that resolve the wrapped node from the registry (`adapters/control/state.py:177-235`: retry resolves `get_adapter(step_type)`, re-invokes with `min(backoff_base**attempt, backoff_max)`). DRYs the retry/CB logic we duplicate across infra adapters.
- **Per-node `is_test_mode()` simulation branch** returning deterministic output with `simulated:True` (`adapters/rag/query.py:226`, `rerank.py:106`). Lets us assert orchestration wiring/branching in CI without LLM/embed/DB calls — closes our "baseline static vs verified runtime" gap cheaply.
- **Ingest interruptibility:** inject `is_cancelled`/`heartbeat`/`add_artifact` on context, check in long loops + emit per-file artifact with sha256 (`adapters/media/ingest.py:341-352, 449-481`).

**WARNING — do NOT copy:** the monolithic `unified_rag_pipeline` with ~80 keyword flags (`unified_pipeline.py:1160-1280`) is the opposite of our Strategy-per-file mandate — its internal expansion/rerank/citation are NOT swappable adapters, only the outer step is. Keep our per-strategy DI; borrow only the outer step-registry + per-step config_model + test-mode + composable-control-step ideas. Also avoid their English-literal LLM prompts in query_rewrite/HyDE (`query.py:134, 365`) — for our multi-lingual corpora these must come from language_packs.

---

## 7. Vector/DB/FTS storage + ops (migration, backfill) best practices

**The storage layer is the cleanest part of tldw and maps directly onto recurring ragbot bug classes.**

**Optimistic concurrency on every content mutation.** `UPDATE ... WHERE id=? AND version=?`, then `rowcount==0 → ConflictError`; version increments per change (`media_lifecycle_ops.py:49-53`, `synced_document_update_ops.py:60-84`, `document_version_rollback_ops.py:111-126`). **Our document/chunk UPSERT has no version guard** — concurrent re-ingest of the same `source_url` can silently clobber. Adopt `WHERE record_document_id=? AND version=?` to pair with our `X-Idempotency-Key`.

**Content-change side-effect contract (the fix for our "embedding NULL/stale" class).** Any content rewrite sets `content_hash=sha256 + chunking_status='pending' + vector_processing=0` in the **same transaction**, then fires `invalidate_intra_doc_vectors()` best-effort after commit (`synced_document_update_ops.py:64-69, 127-138`). The DB row itself becomes the source of truth that vectors are stale — a stale vector cannot survive a content change.

**Forward-only versioning + rollback-as-new-version.** Rollback re-applies an old `DocumentVersion` as a brand-new version, refuses rollback to current latest (`document_version_rollback_ops.py:92-126`). Immutable audit trail — directly supports our no-psql-hotfix / reproducible-DB-state sacred rule applied to *content*.

**Idempotent multi-key dedupe.** `add_media_with_keywords` (`media_repository.py:48`): `content_hash=sha256` → normalize URL → candidate set → dedupe by URL → fallback dedupe by `content_hash` → **identical-content = metadata-only update** (`:108-119, 282-432`). Plus in-process `_media_insert_lock` + recheck-after-lock to close the new-insert race (`:578-589`). Eliminates duplicate chunks/embeddings and wasted embedding spend on re-ingest.

**Backend-polymorphic FTS with field weighting.** `_update_fts_media` (`fts_ops.py:26-109`) emits SQLite `media_fts` INSERT-OR-REPLACE *or* Postgres `setweight(to_tsvector('english', title),'A') || setweight(...content,'C')` — **title weighted 'A' above body 'C'**, a cheap lexical-relevance win our BM25 path lacks. ⚠ It hardcodes `'english'` (`:87,95,179`) — for us, parameterize the regconfig **per-corpus** for Vietnamese. Synonym expansion is config-flagged, corpus-scoped, bounded, done at **index-write time** (not query-time, keeps query latency flat) (`fts_ops.py:29-54, 73-98`).

**Score normalization at the store boundary.** L2 `1/(1+d)` (`kanban_vector_search.py:433`) + piecewise cosine normalizer (`persona_exemplar_embeddings.py:158-178`) — scores returned to callers are always `[0,1]` regardless of backend metric, so threshold logic is metric-agnostic. **Directly mitigates our threshold-drift-after-provider-migration recurring bug.** Vector subsystem degrades to FTS-only behind an availability flag, handling even pyo3 `PanicException` (`kanban_vector_search.py:32-49`).

**Ops helpers (CLI, argparse, exit codes):**
- **Backfill** (`backfill_chunk_metadata.py`): delegates the transform to a shared testable `normalize_chunk_metadata`, writes only rows where `changed=True` (`:99-117`), `--dry-run` short-circuits. ⚠ These helpers `UPDATE` directly — for us, route writes through alembic/admin-audited path (no-psql-hotfix).
- **Dimension-safe migration** (`chroma_to_pgvector_migrate.py`): infers `embedding_dim` via **override > metadata > sampled-vector-len** (`:112-121`) + **embedder identity drift warnings** (`_maybe_warn_embedder_metadata:415-458`) — prevents writing into a wrong-dim column (our V2 1536→1280 bug class) and mixing incompatible vector spaces.
- **Zero-downtime HNSW reindex** (`pgvector_migrate_hnsw.py:45-91`): create new table → build index empty → batched server-side cursor copy `ON CONFLICT DO NOTHING` → **atomic RENAME swap inside BEGIN/COMMIT**, gated `--dry-run`/`--swap-now` (default: print swap SQL for manual low-traffic window). For re-indexing `document_chunks.embedding` after a Jina→ZE swap without locking live retrieval.
- **HyDE/doc2query backfill** (`hyde_backfill.py:197-216`): stores generated questions as sibling vectors `kind='hyde_q'` with `parent_chunk_id` + deterministic hash IDs — multi-representation indexing to lift recall on question-style queries.
- **Eval-corpus integrity** (`build_rag_bench_corpus.py:201-250`): path-traversal guard, manifest dedupe, **reject ambiguous (0 or >1) title→media_id mapping** so ground-truth labels can never be silently wrong.

---

## 8. Domain-neutral verdict + our adoption priorities (EVOLVE-not-rewrite)

**Domain-neutral verdict: PASS across the whole surface.** Zero brand/industry/customer literals in any of the ~60 files read. Type taxonomies are *data* (`EXT_TO_MEDIA_TYPE_KEY`, `DEFAULT_MEDIA_TYPE_CONFIG`, `MEDIA_TYPE_BY_EXT`); provider/method/strategy selection is config strings against registries/enums; synonyms are corpus-scoped registries, not per-customer. **Three real multi-lang gaps to fix before adopting any heuristic layer:** (a) FTS `to_tsvector('english', ...)` hardcoded (`fts_ops.py:87`); (b) eval tokenizer drops Vietnamese diacritics (`rag_answer_quality_execution.py:1146`); (c) claim-typing + anti-context negation templates are English-keyword based (`anti_context_retriever.py:73`). Their alignment/NLI/LLM-judge layers stay language-agnostic — the *heuristic clamps* are what break. Minor zero-hardcode violations exist (inline chunk-size `500/200/1000`, font-size thresholds, `top_k`/`hybrid_alpha` re-stated in adapter bodies despite Pydantic defaults) — our `shared/constants.py` SSoT is stricter; don't import the drift.

**Adoption priorities (EVOLVE, not rewrite — mapped to our files):**

**P0 — T1 / HALLU=0 (highest value):**
1. **Two-phase decompose-then-verify grounding node** + typed `VerificationStatus` enum + dedicated **numeric-precision verifier** (from `claims_engine.py:417, 1276`). Adopt as a *post-generation verification + load-test scoring layer*, OUT of the answer text (sacred-rule-10). Fix the tokenizer for Vietnamese first. This is the correct-tier fix for our number-fabrication traps.
2. **Three-layer byte-sniff** (`Upload_Sink.py:639-690` "detected-MIME-beats-extension") into our canonical `documents.py` → `detect_parser` step — fills the `mime→ext→byte-sniff` mandate the `doc-format-control` skill requires.

**P1 — close recurring bug classes:**
3. **Content-change → atomic vector invalidation** (`synced_document_update_ops.py:64-69`) — kills the "embedding NULL/stale" class.
4. **Dimension-safe migration + embedder-drift warnings + zero-downtime HNSW reindex** (`chroma_to_pgvector_migrate.py:112-121`, `pgvector_migrate_hnsw.py:45-91`) — kills the post-model-migration dim/threshold-drift class; route content writes via alembic/admin-audit (not direct UPDATE).
5. **Lazy importlib string-registry + `capabilities()` on `EmbeddingPort`** — our `registry.py` eager-imports all adapters (one broken optional adapter breaks all embedding); our `EmbeddingPort` has no capability surface. Both verified against live code.
6. **Score normalization to [0,1] at the store boundary** (`kanban_vector_search.py:433`) — kills the threshold-drift-after-provider-migration class.

**P2 — measurement & ops discipline (no-guess must-measure):**
7. **Judge anti-hallucination clamps + claim-level faithfulness + MRR/nDCG@K + grounding GATE + fail-loud-on-missing-metric** into our load-test harness (`rag_evaluator.py:478-483`, `eval_runner.py:1001-1028, 2184-2213`).
8. **fixed_context vs live_end_to_end frozen-retrieval split** (`rag_answer_quality_execution.py:60-79`) — stop fixing sai-tầng.
9. **207 Multi-Status + per-item Error dicts + SSRF/egress + streaming-size-cap** on our ingest boundary (`pipeline.py:69`, `download_utils.py:270`).
10. **Optimistic-concurrency version guard + idempotent multi-key dedupe + forward-only versioning** on our document/chunk UPSERT (`media_repository.py:48`, `document_version_rollback_ops.py:92-126`).

**KEEP ours (already ahead — do NOT regress):** the 3-tier `reranker_resolver` fallback (binding → `_lookup_platform_default`/system_config → NullReranker, verified `reranker_resolver.py:188/203`); `X-Idempotency-Key` request dedupe (stronger than their media_id-reprocess); per-column dim enforcement from `EmbeddingSpec`; `detect_parser` Port+registry (their `if/elif` parser selection confirms ours is superior); `shared/constants.py` SSoT. Borrow LOGIC, not their selection mechanisms or inline-default drift.

**Relevant our-side files for the work above:** `src/ragbot/infrastructure/embedding/registry.py` (lazy-import + capabilities), `src/ragbot/application/ports/embedding_port.py` (add `capabilities()`), `src/ragbot/interfaces/http/routes/documents.py` (byte-sniff + 207 + SSRF), `src/ragbot/application/services/reranker_resolver.py` (reference for the fallback pattern to mirror onto embedding resolution), plus the AdapChunk parser-adapter layer (uniform result-dict + `parser_used`/`warnings[]`/tri-state contract + table-typed chunks).
