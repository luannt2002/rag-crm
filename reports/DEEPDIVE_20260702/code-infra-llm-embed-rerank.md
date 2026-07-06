# Deep-dive: `infrastructure/llm/` + `infrastructure/embedding/` + `infrastructure/reranker/`

- **Date**: 2026-07-02 · **Reader**: deep-code-reader subagent (read-only)
- **Scope**: every file in the 3 directories (27 `.py`, 6,594 lines), plus load-bearing neighbours read for wiring verification: `shared/llm_usage.py`, `shared/constants/*`, `application/services/retry_policy.py`, `application/services/reranker_resolver.py`, `application/dto/ai_specs.py`, `application/dto/model_runtime.py`, `application/ports/llm_port.py`, `orchestration/nodes/rerank.py`, `orchestration/query_graph.py` (stream section), `orchestration/graph_assembly.py`, `bootstrap.py`, `shared/bootstrap_config.py`, `application/services/model_resolver/*` (dimension resolution).
- **Method**: full line-by-line read of every in-scope file; every cross-file claim grep-verified. Claims labeled **FACT** (code evidence `file:line`) vs **HYPOTHESIS** (inference, runtime not measured). Per rule #0 no runtime numbers are asserted — this is a static baseline, not a VERIFIED runtime audit.

---

## PART 1 — File-by-file: what it does + pipeline connection

### 1.1 `llm/dynamic_litellm_router.py` (1,293 lines) — the LLM gateway

**Purpose**: the single production `LLMPort` implementation (DI: `bootstrap.py:569-575`, Singleton). Two call modes dispatched by duck-typing in `complete()` (line 1092-1109: first positional arg having `.litellm_name` = runtime-config mode):

| Mode | Entry | Used by | Model source | Cost source |
|---|---|---|---|---|
| **runtime-cfg** (query path) | `complete_runtime` (565), `complete_runtime_stream` (871) | `query_graph.py:662/739/1141/1969`, `retrieve.py:1242`, `guard_output.py:184`, `adaptive_decompose.py:92` | `ModelRuntimeConfig` (per-bot binding via ModelResolver) | `cfg.pricing` from `ai_models` row → `compute_cost_usd` (278) |
| **legacy LLMPort** (spec) | `_complete_via_llmport` (1111), `stream` (1255) | `llm_chunk_context_provider.py:106` (ingest CR enrichment), `hyde_generator.py:116` | `LLMSpec.to_litellm_kwargs()` | `resp._hidden_params.response_cost` (litellm's own cost map, 1202) |

**Key mechanics** (all FACT):
- **Circuit breakers**: one `CircuitBreaker` per provider code, lazily built (410-426), `fail_max=5` / `cooldown=30s` (`_08_sentry_otel.py:16-17`). Pre-flight `can_execute()` (736, 911, 1152); 429 is deliberately **excluded** from breaker accounting on all three LLM paths (769, 955, 1181) — rate-limit = flow-control, not outage (comment 142-152).
- **Retry**: `retry_with_backoff` over `_RETRYABLE_LLM_EXCEPTIONS` (129-140: OSError/Conn/Timeout + litellm RateLimit/ServiceUnavailable/APIConnection/InternalServer). Auth/BadRequest propagate first-attempt.
- **Semaphores**: per-provider (403-408, default 16 `_10_rbac.py:52`), plus an isolated `"{code}::background"` lane (725-729, default 4) selected solely by the `background=True` flag so post-response grounding can't starve foreground generation.
- **Failover (fallback model)**: `complete_runtime` only (605-641). Triggers = `CircuitBreakerOpen | LLMError | ServiceUnavailable | APIConnection` (101-106). One hop max; `_build_fallback_cfg` (659-674) swaps provider/wire-name and clears fallback fields.
- **Anthropic prompt cache**: `_apply_anthropic_cache_control` (217-258) wraps the FIRST system message in `cache_control: ephemeral`, keyed on `ANTHROPIC_PROVIDER_CODES = ("anthropic","claude")` substring (205-214, `_10_rbac.py:246`). Applied on both runtime paths (690-698, 918-922); intentionally NOT transferred to the failover hop (631-639).
- **Cost metering + tokenizer fallback**: runtime paths extract usage via `shared/llm_usage.extract_usage_from_response`, then **fill zero counts** with a local tiktoken cl100k estimate — `estimate_tokens_fallback` at 794-797 (sync) and 1045-1048 (stream, from the accumulated `answer_parts`). Never overwrites a non-zero provider count (`llm_usage.py:61-62`). Stream usage requires `stream_options={"include_usage": True}` for OpenAI/Azure only (314-328, 927-931).
- **Token ledger**: every path emits a `TokenLedgerEntry` snapshot (identity from contextvars, unit prices frozen from `cfg.pricing`) inside `try/except` that never breaks the LLM path (834-860, 1212-1234).
- **Tenant token meter**: `_meter_tokens` (447-472) post-call increment keyed by `tenant_id_ctx` contextvar; streaming emits ONE increment post-drain (1050-1053).
- **TPM limiter**: process-wide `_TPM_LIMITER` (80-82, limit = 200k × 0.9) — acquired **only** in `_complete_via_llmport` (1147-1150), i.e. only on the legacy/spec path (ingest enrichment + HyDE).
- **DB routing table**: `refresh_routing` (528-559) builds `_model_list` from `ai_providers`+`ai_models` and writes it to Redis `ragbot:models`.

**Pipeline connection**: this is the choke point for every generative call in both graphs (query answer/stream, decompose, multi-query, grounding, guard, ingest enrichment, HyDE).

### 1.2 `llm/registry.py` (54) — Strategy registry for LLMPort
`{"dynamic_litellm": DynamicLiteLLMRouter, "speculative": SpeculativeRouter}` + fail-loud `build_llm` (raises KeyError — correct per silent-fallback ban). **FACT**: production never calls `build_llm` — `bootstrap.py:569` constructs `DynamicLiteLLMRouter` directly and `query_graph.py:1026` constructs `SpeculativeRouter` directly; the only `build_llm` caller is `tests/unit/test_speculative_router.py:382-388`. Registry is decorative on this port.

### 1.3 `llm/speculative_router.py` (623) — draft-vs-main race (Wave K1/K2)
Wraps two `LLMPort`s; only `complete_runtime_stream` races (docstring 87-94). Race = `asyncio.wait(FIRST_COMPLETED)` over `_race_first_token` wrappers (231-239); winner streams, loser cancelled + drained in background with a cost-placeholder event (`speculative_loser_cost_usd`, cost_usd always 0.0 — 597-604). Phase-3 verifier path buffers draft tokens, cross-checks vs main first chunk, yields `SPECULATIVE_REDO_SENTINEL` then main on rejection (495-509). Default OFF (`DEFAULT_SPECULATIVE_STREAMING_ENABLED=False`, `_20_cag...py:167`), per-bot opt-in `plan_limits.speculative_streaming_enabled` + `draft_model` (`bot_limits.py:365-369`). Constructed per-turn in `query_graph.py:1019-1031` with `draft_llm=llm` (same instance; draft swap per call).
**FACT**: class defines `complete`, `stream`, `health_check`, `refresh_routing`, `close`, `complete_runtime_stream` — but **no `complete_runtime`** despite the docstring (line 90) listing it as delegated. Latent-only today (no direct `.complete_runtime(` caller outside infra; runtime calls go through `complete()` dispatch).

### 1.4 `llm/tpm_rate_limiter.py` (95) — sliding-window TPM pacing
Per-model-key trailing-60s token window; waiters queue under a per-key asyncio.Lock (65-85); oversized single request admitted on empty window (never deadlocks). `estimate_request_tokens` = `chars//4 + max_output` (88-95) — an **English-calibrated** heuristic (see finding F12).

### 1.5 `llm/anthropic_haiku_batch.py` (260) — Batch-API skeleton
Protocol + DTOs + `NullAnthropicHaikuBatchClient` + `estimate_batch_cost_usd`, self-described "thin client skeleton" for narrate-then-embed 50%-discount batching. **FACT**: zero references outside the module and its own `__all__` — not in `bootstrap.py`, not in any `narrate*` service (grep `AnthropicHaikuBatch|narrate_use_batch_api|NullAnthropicHaikuBatchClient` → 0 hits outside file). Built-but-not-wired (see F14).

### 1.6 `llm/llm_chunk_context_provider.py` (154) — Contextual-Retrieval enrichment
Implements ChunkContextProviderPort: resolves the `intent="contextualization"` LLMSpec per doc (89-93), fans out per-chunk prompts with a bounded semaphore, degrades per-chunk failures to `""` (113-126). Warm-then-fan-out: first chunk alone then `gather` (139-144) claiming provider prompt-cache reuse. Output goes to `document_chunks.chunk_context` only (Quality Gate #10 honored). Uses the **legacy** `complete(messages, spec=...)` path → gets TPM pacing + ledger, but **no Anthropic cache_control and no tokenizer cost fallback** (see F6/F17).

### 1.7 `embedding/registry.py` (125) — embedder Strategy registry
`{litellm, jina, jina_ai, zeroentropy, bkai_vn}`; default `litellm`; `bkai_vn` flag-gated by `system_config.bkai_vn_embedder_enabled` (47-63). Ctor-kwargs filtered by signature; Jina TPM knobs read via `get_boot_config` (109-121). **FACT**: unknown provider silently falls back to the default with **no log** (`_REGISTRY.get(key, _REGISTRY[DEFAULT])`, line 93) — the reranker registry in the same codebase warns on the identical condition (`reranker/registry.py:72-78`). **FACT**: `build_embedder` never passes a `dimensions` kwarg (kwargs assembled at 100-121: only key_pool_factory/model/ledger/tpm_*), so every adapter's dimension is its constructor constant.

### 1.8 `embedding/litellm_embedder.py` (231) — default cloud embedder
LiteLLM `aembedding` with CB (`embedder:litellm`, one breaker for ALL models routed through the adapter) + retry + 64-item sub-batching (`DEFAULT_EMBEDDING_MAX_BATCH=64`, `_04_jwt_auth.py:185`). Jina-prefixed models get key-pool active-key + 403/429 `mark_cooldown` failover (99-112, 161-164, 201-212). Matryoshka: passes `dimensions` **only when** `"text-embedding-3" in model` (157-159).

### 1.9 `embedding/jina_embedder.py` (439) — direct-HTTP jina-embeddings-v3
Query/passage task heads (213-217, 385-386); **late chunking** for passages: greedy char-windows under `window_tokens × 2 chars/token` (109, 262-282), per-window degrade to non-late on the Jina 422 "could not be tokenized for late_chunking" (399-414). Per-KEY TPM limiter buckets (`_limiter_for`, 195-211) + per-key concurrency from `PROVIDER_KEY_CONCURRENCY_JSON` env (76-102). Emits embedding usage to the token ledger via `emit_aux_usage` (326-337). Sorts response rows by `index` before stripping vectors (338-341). Wire `dimensions` = ctor constant `DEFAULT_JINA_EMBEDDING_DIM=1024` (125, 135, 299) — `spec.dimension` never consulted.

### 1.10 `embedding/zeroentropy_embedder.py` (315) — direct-HTTP zembed-1
Same shape as Jina minus late-chunking/TPM/ledger. `dimensions` = ctor constant `DEFAULT_ZEROENTROPY_EMBEDDING_DIM=1280` (77, 83, 229 — the matryoshka cap; model native 2560). Health check verifies returned vector length == configured dim (162-174) — good drift guard. Canonical `external_call_failed` event on non-200 (241-249). **FACT**: pool entry resolved but never cooled on 429 (`_entry` unused, 203); no usage ledger emit.

### 1.11 `embedding/bkai_vn_embedder.py` (340) — self-hosted TEI (PhoBERT 768-dim)
HF TEI `POST {base}/embed`; URL from env `BKAI_VN_EMBEDDING_URL` (required, fail-loud at call, 211-215); tolerant response-shape parsing (list / `embeddings` / `data[].embedding`, 258-267). Flag-gated at registry. Minor: `_get_client` has **no lock** (154-157) unlike ZE/Jina — concurrent first calls can build N clients (leak, benign); the `model` computed at 220-221 is never sent to TEI (single-model server; telemetry-only).

### 1.12–1.16 `embedding/openai_embedder.py`, `null_embedder.py`, `multi_vector_registry.py`, `null_multi_vector.py`, `sentence_split_multi_vector.py`
**All five are fully commented-out corpses** behind `DEAD-CODE NOTICE — 2026-06-03` headers (verified: every code line is `#`-prefixed). Consequences: (a) the embedding stack has **no Null Object** anymore — the registry's "fail-soft" now degrades to a *real* LiteLLM embedder rather than a Null strategy; (b) the entire multi-vector/late-interaction scaffold is parked.

### 1.17 `reranker/registry.py` (102) — reranker Strategy registry
`{jina, jina_ai, litellm, null, viranker_local, voyage, zeroentropy}`; unknown → warn + `NullReranker` (72-78); ctor failure → error + `NullReranker` (88-94); kwargs signature-filtered (84-87). Fail-soft is deliberate and observable — compliant.

### 1.18 `reranker/null_reranker.py` (77) — Null Object
Returns `chunks[:top_n]` preserving retrieval scores (60-67). Correct.

### 1.19 `reranker/jina_reranker.py` (397) — Jina rerank v3
64-doc cap (217), pool round-robin + 403/429 cooldown + failover metric (162-185), per-key concurrency semaphore held only for the HTTP attempt (238-247), CB, retry on transport only (4xx propagate), token-ledger `emit_aux_usage` (306-314), **D5a deterministic tie-break sort** (336-342). Endpoint hardcoded module-level (`_JINA_RERANK_ENDPOINT`, 59) instead of constants.

### 1.20 `reranker/litellm_reranker.py` (165) — litellm `arerank`
Retry-only (no CB), full-fallback `except Exception → chunks[:top_n]` (136-145). Has the D5a sort (110-116). Carries the index-misalignment bug (see F2).

### 1.21 `reranker/viranker_local_reranker.py` (68) — deliberate fail-loud stub
Ctor raises `NotImplementedError` with install guide; registry catches → NullReranker + error log. Working as designed.

### 1.22 `reranker/voyage_reranker.py` (383) — Voyage rerank-2
Same skeleton as Jina (pool/cooldown/CB/retry), 1000-doc cap, optional `dimensions` knob. **No concurrency semaphore, no ledger emit, no D5a tie-break sort** (output loop 299-326 returns API order).

### 1.23 `reranker/zeroentropy_reranker.py` (459) — ZE zerank (production reranker per state snapshot)
Most hardened adapter: per-attempt key re-resolution so a 429 rotates keys mid-retry (270-299), 429/rate-503 short-cooldown vs 403 long-cooldown (183-212), transient 5xx re-raised as retryable ConnectError (306-309), bulkhead semaphore (127), CB fail_max=10, timeout 5s. `latency="slow"` default is measured/justified in constants (`_01_...py:73-82`) but the module docstring (8-9) still claims "default to fast" — stale. **No ledger param in ctor** (78-88) and **no D5a tie-break sort** (379-406).

### 1.24 `reranker/_modality_boost.py` (253) — M17 modality-aware boost
Pure helpers `apply_modality_boost`/`boost_chunks`, per-bot gate `plan_limits.modality_rerank_enabled`, default-OFF, English-seed boost map. **FACT**: zero production callers — only `tests/unit/test_modality_boost.py` imports it; `orchestration/nodes/rerank.py` does not (import list 23-43). Orphan feature.

### Pipeline-connection summary
- **Query graph**: `generate` → `llm.complete(cfg,…)` / `complete_runtime_stream` (SSE via sink, `query_graph.py:1088-1101`); `rerank` node → `reranker_resolver.resolve_for_bot()` per turn (`nodes/rerank.py:86-90`) with fallback to the DI singleton; `_embed_query` → embedder singleton.
- **Ingest**: DocumentService embed stages → embedder singleton (`bootstrap.py:307-315`); CR enrichment → `LLMChunkContextProvider` → legacy LLM path.
- **Resolve chain (reranker)**: `bot_model_bindings(purpose='rerank')` → `system_config.reranker_{enabled,model,provider}` + `ai_models`/`ai_providers` join → NullReranker, Redis-cached 60s incl. negative cache (`reranker_resolver.py:56-284`). The `_lookup_platform_default` fallback mandated by the 2026-05-14 lesson **is present and loud on drift** (267-274). ✅

---

## PART 2 — Special-focus analyses

### 2.1 DynamicLiteLLMRouter — flow deltas across the 4 paths (FACT table)

| Capability | `complete_runtime` (sync) | `complete_runtime_stream` | `_complete_via_llmport` | `stream` (legacy) |
|---|---|---|---|---|
| Circuit breaker | ✅ | ✅ (soft, post-hoc) | ✅ | ❌ none |
| Retry | ✅ | ❌ (documented — can't replay) | ✅ | ❌ |
| Semaphore | ✅ (+background lane) | ❌ | ❌ | ❌ |
| TPM limiter | ❌ | ❌ | ✅ (1147) | ❌ |
| Fallback-model failover | ✅ (605) | ❌ | ❌ | ❌ |
| Anthropic cache_control | ✅ | ✅ | ❌ (1121-1131) | ❌ |
| tiktoken zero-fill | ✅ (794) | ✅ (1045) | ❌ | ❌ |
| Token ledger | ✅ | ❌ (usage_sink instead) | ✅ | ❌ |
| Tenant meter | ✅ | ✅ | ❌ | ❌ |
| DB pricing (`cfg.pricing`) | ✅ | ✅ | ❌ (litellm cost map only) | ❌ |

The legacy `stream()` (1255-1281) is the weakest surface (no CB/retry/usage at all); its callers are limited (none found in orchestration — the query stream path uses `complete_runtime_stream`).

### 2.2 Fallback & draft cost/pricing (F5)
`ModelRuntimeConfig` has `fallback_model_row_id / fallback_wire_model_id / fallback_provider` but **no fallback pricing** (`dto/model_runtime.py:83-85`); `_build_fallback_cfg` (`dynamic_litellm_router.py:664-674`) keeps `cfg.pricing` (and `cfg.params`) from the PRIMARY. Every failover-hop call is therefore costed at primary-model rates. Same class of drift in `SpeculativeRouter._build_draft_cfg` (`speculative_router.py:529-545`): only `litellm_name` swaps — draft runs on the primary's `api_key`/`api_base`/`pricing`, so (a) a cross-provider draft model breaks (HYPOTHESIS: provider 404/400 — wire name unknown at that api_base), (b) draft cost is billed at main-model rates in ledger/meter.

### 2.3 Cost metering — tokenizer fallback coverage (F6)
The new tiktoken fallback (`shared/llm_usage.py:46-71`, lru-cached cl100k encoder, optional-dep-safe) is wired ONLY on the runtime paths (794-797, 1045-1048). The legacy path — which the ledger comment itself declares "the choke point for non-streaming calls (ingest CR enrichment + narrate + grade/grounding)" (1206-1211) — reads raw `usage` (1199-1201) and litellm `response_cost` (1202) with **no zero-fill and no DB pricing**. Consequence (HYPOTHESIS, mechanism FACT): when the same usage-omitting gateway that motivated OBS-F6 serves enrichment, ingest ledger rows record 0 tokens / cost NULL.

### 2.4 Registry/DI purity
- No `if provider ==` ladders in orchestration/business logic for LLM/embed/rerank (grep clean in scope). Provider branch points live inside adapters (`LiteLLMEmbedder` Jina-prefix dispatch, 161-164 — documented as the dispatch surface) and registries. ✅
- `build_llm` never used in production (1.2) — the LLM "registry" exists to satisfy the pattern but bootstrap and query_graph construct classes directly. T3-level impurity, not a behavior bug.
- The embedder registry's *silent* unknown-provider fallback (1.7) contradicts both the reranker registry's warn-behavior and the silent-fallback ban lesson.

### 2.5 Embedding dimension / matryoshka-1280 (F3)
Per-bot dimension IS resolved into `EmbeddingSpec.dimension` (binding `extra_params.dimension` → model metadata → column default; `model_resolver/_binding_mixin.py:275-281`) and IS consumed by ingest bookkeeping (`document_service/__init__.py:460`, `ingest_stages_store.py:1089`). But at the wire:
- Jina adapter sends ctor-constant 1024 (`jina_embedder.py:299`),
- ZE adapter sends ctor-constant 1280 (`zeroentropy_embedder.py:229`),
- LiteLLM adapter honors `spec.dimension` only for `"text-embedding-3"` models (`litellm_embedder.py:157-159`),
- registry never forwards a dimensions kwarg (`embedding/registry.py:100-121`).

So the 1280 matryoshka works because **constant == the single global `document_chunks.embedding` column** (`DEFAULT_EMBEDDING_COLUMN`, `ingest_stages_store.py:828/939/1045`). A per-bot binding declaring any other dimension is silently ignored → vectors come back at the constant dim (FACT); DB insert/query dim-mismatch or silent wrong-dim search follows (HYPOTHESIS by dim arithmetic; ZE health check would catch a *global* flip but not a per-bot one).

### 2.6 Reranker resolve chain (F1 + positives)
Chain and fail-soft behavior are correct and the `system_config` platform-default fallback is present with loud drift logging (1.24). Two structural gaps:
1. **Adapter churn**: `resolve_for_bot` → `_build_from_config` → `build_reranker` on EVERY call — including Redis cache hits (`reranker_resolver.py:129`). No instance cache keyed on config.
2. `api_key_encrypted` decrypt "not implemented" → NullReranker (299-304) — a bot configured with an encrypted key silently loses rerank (warned, but rerank-off is the exact silent-quality-drop the silent-fallback ban targets).

---

## PART 3 — Findings (ranked)

### F1 — HIGH · T2/resilience · Per-turn reranker adapter construction defeats CB, semaphore, client reuse; leaks HTTP clients
**FACT chain**: `nodes/rerank.py:86-90` calls `reranker_resolver.resolve_for_bot()` on every rerank invocation (resolver is wired in production: `bootstrap.py:709`, `graph_assembly.py:92-122` auto-passes it into `build_graph`; `query_graph.py:2538`). `reranker_resolver.py:129` (cache-hit) and `:163` (cache-miss) both end in `_build_from_config` → `build_reranker(...)` (314) → a **fresh adapter instance**. Each JinaReranker/ZeroEntropyReranker instance owns a fresh `httpx.AsyncClient` (lazy, `jina_reranker.py:141-149`, `zeroentropy_reranker.py:137-145`), a fresh `CircuitBreaker`, and a fresh `Semaphore`; `close()` is never called by the rerank node or resolver (no reference kept).
**Consequences**: (a) TLS+DNS handshake per turn — the exact cost the DI-singleton comment says it avoids (`bootstrap.py` reranker comment "Singleton … amortises the TLS handshake"), (b) breaker state never accumulates across turns → provider outage can never fast-fail on the per-bot path (each turn re-pays full timeout×retry), (c) the per-key concurrency gate is per-request → no cross-request pacing → the 429-burst protection it documents is void, (d) unclosed AsyncClients leak sockets until GC (HYPOTHESIS on leak visibility; construction churn is FACT).
**Failure scenario**: ZE outage; 50 concurrent turns each construct a fresh ZE reranker, each burns `5s timeout × 3 attempts` before degrading to RRF — CB OPEN never happens; p95 inflates platform-wide.

### F2 — HIGH · bug · LiteLLMReranker index misalignment on empty-content chunks
**FACT**: `litellm_reranker.py:69-73` builds `passages` then **filters out empty strings**; the response mapping at 95-98 indexes `chunks[idx]` on the ORIGINAL list. Any chunk with empty/missing `content`/`text` shifts every later index by one.
**Failure scenario**: `chunks=[A(content=""), B, C]` → documents sent `[B,C]`; API returns `index=0` (B) → code attaches B's score to **A** and returns A among top-N → wrong chunk enters the prompt (T1 risk). Mitigation today: LiteLLMReranker is not the active provider (registry default "jina", production ZE); Jina/ZE/Voyage don't filter, so they're aligned.

### F3 — HIGH · multi-bot · Per-bot `EmbeddingSpec.dimension` silently ignored by Jina/ZE adapters (matryoshka pinned by constant)
See §2.5. **FACT**: `jina_embedder.py:299`, `zeroentropy_embedder.py:229`, `embedding/registry.py:100-121`, vs `model_resolver/_binding_mixin.py:275-281` resolving per-bot dimension into the spec. The platform is single-embedding-space by constant; multi-bot/per-binding dimension is configuration theater. Any second tenant/bot needing a different dim (or an operator changing `extra_params.dimension`) gets constant-dim vectors with no warning.

### F4 — HIGH (opt-in path) · bug · `SPECULATIVE_REDO_SENTINEL` leaks verbatim into the user answer; redo protocol unimplemented
**FACT**: sentinel yielded at `speculative_router.py:423/500`; grep shows **no consumer** anywhere (`SPECULATIVE_REDO` only in the module + its tests); `_sse_helper.redo_event` (88-104) has **zero callers**; the stream consumer `query_graph.py:1088-1101` appends every delta into `buffer` (→ `answer_text`, → cache/log) and pushes it to the SSE sink unfiltered.
**Failure scenario**: bot owner enables `speculative_streaming_enabled` + `speculative_hallu_verify_enabled` (the documented HALLU-safe combo); verifier rejects a draft → the client receives literal `__SPECULATIVE_REDO__` inside the answer text and it is persisted in the recorded answer. The HALLU-safe mode ships user-visible garbage; Phase-3 wiring is half-done.

### F5 — MEDIUM · cost-metering · Fallback-hop and draft-model calls costed at PRIMARY pricing
See §2.2. **FACT**: `dynamic_litellm_router.py:659-674` (`replace` keeps `pricing`), `dto/model_runtime.py:83-85` (no fallback pricing field), `speculative_router.py:529-545` (only `litellm_name` swapped). Ledger/meter rows for failover and draft calls carry the wrong unit prices (model name is correct, prices aren't). Cross-provider draft additionally runs against the wrong `api_base`/`api_key` (latent breakage).

### F6 — MEDIUM · cost-metering · Legacy LLMPort path has neither tokenizer zero-fill nor DB pricing
See §2.3. **FACT**: `dynamic_litellm_router.py:1199-1202` vs 794-797/1045-1048. Ingest CR enrichment + HyDE (the highest-volume batch LLM consumers) log 0 tokens/$0 whenever the gateway omits `usage` — the precise blind spot OBS-F6 was built to close, closed only on the query path.

### F7 — MEDIUM · resilience asymmetry · Streaming answer path has no fallback-model failover (and no retry/semaphore/TPM)
**FACT**: failover logic exists only in `complete_runtime` (605-641); `complete_runtime_stream` (871-1090) raises pre-flight `LLMError` when the breaker is OPEN and has no `_failover_eligible` consult. Retry-absence is documented (892-895); failover-absence is not.
**Failure scenario**: primary provider CB OPEN; a bot with a configured `record_fallback_model_id` still fails its streamed turns 503 while its non-streamed calls (decompose/grounding) transparently fail over — the *most* user-visible path is the only one without the safety net.

### F8 — MEDIUM · dead machinery · DB routing table + Redis `ragbot:models` is write-only
**FACT**: `refresh_routing` writes `self._model_list` + Redis key (528-559); the only reader of `_model_list` is `health_check` (519-526); grep for `ragbot:models` shows no other reader in src/scripts. Routing decisions never consult it — every call passes explicit `model/api_key/api_base` from cfg/spec. The class docstring ("Model list cached in Redis at `ragbot:models` so all workers share it", 361-368) describes machinery that routes nothing. `_maybe_refresh` on the legacy path (1120) refreshes a table nobody reads → periodic DB+Redis work with no function beyond health.

### F9 — MEDIUM · dead code + misleading docstring · `_preflight_token_cap`
**FACT**: defined at `dynamic_litellm_router.py:474-505`; grep shows zero callers. Docstring says "raises `LLMError` when blocked" — the body never raises anything (it logs a debug line at most, 501-505; internal NOTE at 495-500 admits the cap isn't visible here). The `enforce_preflight_cap` ctor flag (377, 392) is therefore inert: tenant token caps are never pre-flight enforced regardless of configuration.

### F10 — MEDIUM · silent degrade · `build_embedder` unknown-provider fallback is silent (and Null Object removed)
**FACT**: `embedding/registry.py:93` falls back to LiteLLM with no log; contrast `reranker/registry.py:72-78`. With `null_embedder.py` dead (commented out), a typo'd/retired `system_config.embedding_provider` silently produces vectors from a DIFFERENT model/space at the default dim.
**Failure scenario**: operator sets `embedding_provider="voyage"` (not registered) → ingest silently embeds with `text-embedding-3-small`; queries against a ZE-1280 corpus either dim-mismatch at insert or (if dims coincide) silently search the wrong space — retrieval scores collapse with no error anywhere.

### F11 — MEDIUM · CB policy inconsistency · Embedder/reranker breakers count 429 as outage; LLM router doesn't
**FACT**: LLM router excludes rate-limit from breaker accounting with a long rationale (`dynamic_litellm_router.py:142-152, 769, 955, 1181`). Every embedder/reranker adapter wraps `retry_with_backoff` in `with self._cb:` whose `__exit__` records failure on ANY exception (`retry_policy.py:193-202`) — including `RateLimitError`/HTTP-429 after retries (`litellm_embedder.py:181-191`, `jina_embedder.py:344-357`, `zeroentropy_embedder.py:263-281`).
**Failure scenario**: ingest re-embed burst on the LiteLLM embedder (which has NO TPM limiter, unlike Jina) → N consecutive 429s → `embedder:litellm` CB OPEN → subsequent batches fail `ExternalServiceError` — the exact Nygard anti-pattern the router comment names, one directory over.

### F12 — MEDIUM · multi-locale happy-case · LLM TPM estimator is English-calibrated
**FACT**: `tpm_rate_limiter.py:88-95` uses `chars//4`; `jina_embedder.py:104-109` explicitly documents Vietnamese at ~2.5 chars/token and picks 2 for safety. The LLM-side limiter (used for ingest enrichment pacing) under-estimates Vietnamese token counts by roughly half (HYPOTHESIS on exact ratio; the divergent in-repo calibrations are FACT) → the 0.9 safety fraction is consumed by estimation error on VN corpora and the 429-storm the limiter exists to prevent stays reachable.

### F13 — MEDIUM · T1 reproducibility · D5a deterministic tie-break missing from ZE + Voyage rerankers
**FACT**: tie-break sort present in Jina (`jina_reranker.py:331-342`) and LiteLLM (`litellm_reranker.py:107-116`) with the explicit "answer flip" rationale; absent in ZeroEntropy (output loop 379-406 returns API arrival order) and Voyage (299-326). The production reranker (ZE zerank per state snapshot) is one of the two without the fix — tied rounded scores can still reorder across identical calls → nondeterministic context → answer flips the D5a commit was written to kill.

### F14 — MEDIUM · built-not-wired · Anthropic Haiku Batch narration client is an orphan
**FACT**: §1.5 — Protocol + Null + cost math, no live adapter, no DI registration, no caller, and the gating flag `narrate_use_batch_api` appears nowhere else in src. The 50%-discount narrate path is documentation only.

### F15 — MEDIUM · built-not-wired · Modality boost never called from the pipeline
**FACT**: §1.24 — `boost_chunks`/`apply_modality_boost` imported only by unit tests; `nodes/rerank.py` doesn't import the module. The per-bot flag `plan_limits.modality_rerank_enabled` is a no-op knob (bot owners can set it; nothing reads it in the answer path).

### F16 — LOW-MED · cost-metering gaps · Ledger coverage is one-provider-deep
**FACT**: embedding usage emitted only by JinaEmbedder (`jina_embedder.py:326-337`); ZE/LiteLLM/BKAI embedders emit nothing (no `emit_aux_usage`, and LiteLLM/ZE ctors don't accept the `ledger` the registry forwards — `embedding/registry.py:103-104` filters it out by signature). Rerank usage emitted only by JinaReranker (306-314); ZE (`zeroentropy_reranker.py:78-88` — no ledger param) and Voyage emit nothing. With ZE as the active embed+rerank stack, both spends are invisible to the token-log-center.

### F17 — LOW-MED · key-pool failover half-wired on direct-HTTP embedders
**FACT**: JinaEmbedder and ZeroEntropyEmbedder resolve a pool entry but never call `mark_cooldown` (`jina_embedder.py:378` and `zeroentropy_embedder.py:203` — `_entry` discarded; no cooldown call anywhere in either file), and the key is resolved ONCE per `embed_batch` (all windows/sub-batches reuse it). Contrast `LiteLLMEmbedder._mark_cooldown` (99-112) and every reranker. A 429/403 on the active embed key retries the SAME key then fails the batch — multi-key rotation exists in the pool but the embed path can't trigger it.

### F18 — LOW · prompt-cache gap on enrichment path
**FACT**: `_complete_via_llmport` never applies `_apply_anthropic_cache_control` (1121-1139) and the CR-enrichment prompt is a single `user` message (`llm_chunk_context_provider.py:107`; the wrapper only decorates `system` messages anyway, 242-247). The warm-then-fan-out comment (128-138) claiming "cache-read ≈ 10% tokens" holds only for OpenAI auto-cache (HYPOTHESIS for actual provider mix); with any Anthropic contextualization binding the doc prefix is re-billed cold N times.

### F19 — LOW · dead modules · 5 commented-out embedding files; LLM registry decorative
**FACT**: §1.12-1.16 (openai/null embedder + entire multi-vector stack) and §1.2 (`build_llm` test-only). Kept-intact-by-policy, but the multi-vector port + `DEFAULT_MULTI_VECTOR_PROVIDER` constants still suggest a capability that cannot be enabled by config.

### F20 — LOW · CLAUDE.md hygiene bundle
- **Tenant literal**: "innocom" (the operator's company) named in 5 tracked comments — `dynamic_litellm_router.py:789, 971, 1040`, `shared/llm_usage.py:54`, `test_chat/chat_routes.py:508`. Comments, not credentials, but the tenant-identifier rule covers tracked files generally.
- **Hardcoded endpoint**: `_JINA_RERANK_ENDPOINT` module literal (`jina_reranker.py:59`) while every other provider's URL lives in `shared/constants` (e.g. `DEFAULT_VOYAGE_RERANK_ENDPOINT`).
- **Stale docs**: ZE reranker docstring says default latency "fast" (`zeroentropy_reranker.py:8-9`) vs constant `"slow"` (justified at `_01_...py:73-82`); SpeculativeRouter docstring lists a `complete_runtime` delegate that doesn't exist (89-94); router class docstring describes Redis-shared routing that routes nothing (F8).
- **Silent overflow**: rerank adapters cap candidates at 64 (`jina_reranker.py:217`, `zeroentropy_reranker.py:244`) and chunks beyond the cap are dropped from the output entirely (not appended after top-N) — a merged multi-query pool >64 silently loses its tail before scoring (happy-case: retrieval ≤64).
- Broad-except usage in scope is predominantly policy-compliant (noqa'd cleanup/ledger/CB-account paths); the one debatable case is `litellm_reranker.py:136` swallowing even mapping-code `TypeError` into "fallback to original order".

### Positives worth recording (so they don't get "fixed")
- 429-vs-outage CB separation on the LLM router with per-provider isolation + background bulkhead lane (76-152, 716-734) is genuinely expert.
- Reranker resolve chain honors the mandated `system_config` fallback and fails LOUD on config drift (`reranker_resolver.py:261-274`).
- ZE reranker's per-attempt key rotation + rate-503 body sniffing (270-309) is the most production-hardened adapter in scope.
- Jina late-chunking per-window 422 degrade (399-414) is a correct quality-vs-correctness call.
- Streaming CB semantics (pre-flight check, no mid-stream re-check, cancel ≠ provider fault, 0-token stream = failure) are carefully reasoned (896-1023).
- `estimate_tokens_fallback` never overwrites real provider counts and degrades cleanly without tiktoken (`llm_usage.py:20-31, 61-65`).

---

## PART 4 — Multi-axis scorecard for the scope

| Axis | Verdict | Evidence anchor |
|---|---|---|
| **multi-doc** | N/A-mostly (this layer is doc-agnostic); the only multi-doc-relevant behavior is the silent >64-candidate drop in rerankers (F20) which penalizes wide multi-doc retrieval pools | jina_reranker.py:217 |
| **multi-bot** | Weakest axis: per-bot embed dimension ignored (F3); per-bot rerank binding honored but at the cost of per-turn adapter churn (F1); per-bot speculative/draft flags honored but half-wired (F4); modality per-bot flag is a no-op (F15) | §2.5, F1, F4, F15 |
| **multi-format** | Not this layer's job; no format assumptions found in scope (embedders take opaque text). Late-chunk window packing is char-based and table-row-aware only via the 422 fallback | jina_embedder.py:262-282, 399-414 |
| **multi-tenant** | Sound: tenant identity flows via contextvars into meter/ledger with safe-UUID parsing (118-125, 428-445); `record_tenant_id` accepted (if unused) on every embed call for propagation. No cross-tenant state in adapters (keys/pools are provider-scoped, deliberately) | dynamic_litellm_router.py:428-472 |
| **T1 smartness** | F2 (wrong chunk scored), F13 (nondeterministic ZE ordering) are the two T1-touching defects | F2, F13 |
| **T2 cost/perf** | F1 (per-turn TLS + dead CB), F5/F6/F16 (cost blind spots), F7 (no stream failover), F11/F12 (429 handling) | F1, F5-F7, F11-F12, F16 |
| **T3 design** | Registries mostly pure; LLM registry decorative; 5 dead modules; Null Object missing on embedding | F8-F10, F19 |
