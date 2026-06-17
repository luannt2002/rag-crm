# RAGBOT 32+ Step Adaptive Pipeline Reference

> **Canonical**: this file lists 7 ingest (U1-U7) + 25-32 query (Q1-Q32 adaptive) steps as a reference table.
> **Adaptive**: Query Router L1 heuristic + L3 LLM decomposer gates expensive nodes (CRAG retry, HyDE, multi-query fanout) per intent — minimum 25 step (greeting / simple lookup), maximum 32 step (decomposed complex multi-hop).
> **Post Wave A+B+F ship 2026-05-19**: 4 new RAG-Anything mindset wired into pipeline: (1) Anthropic Contextual Retrieval column + BM25 boost (alembic 010l + 010n) gated `plan_limits.cr_enhanced_enabled`, (2) Cascade Routing tier model selection (Haiku/mini/Sonnet) gated `plan_limits.cascade_routing_enabled` — Wave E pilot WE-1 measured cost delta noise -0.42% (not paper -30-50%), latency -16.7%; cascade_t_low=0.3 unreachable on medispa corpus, (3) Self-RAG critique parser after generate gated `plan_limits.self_rag_critique_enabled`, (4) HyDE production wire gated `plan_limits.hyde_enabled`. Reranker threshold WA-5 code constant bumped 0.15→0.30 but system_config row KEEP 0.15 per WE-4 verdict (96.7% pass parity all 4 threshold; simulated 0.30 = 64% refuse risk).
> **Post Phase A-D ship 2026-05-12**: pipeline optimization + GA hardening. Worker scale 4× (multi-instance + Haiku concurrency 20).
> **Detail per step**: [`docs/master/04-D-pipeline-orchestration.md`](docs/master/04-D-pipeline-orchestration.md) (logic) + [`docs/master/11-K-pipeline-code-mapping.md`](docs/master/11-K-pipeline-code-mapping.md) (code paths).
> **Sacred contracts**: [`CLAUDE.md`](CLAUDE.md).
> **Current numbers**: [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md).

---

## TL;DR

Ragbot platform có **2 pipeline song song**:
- **Upload (U1–U7)** + 2 ingress (U0/U0.5): ingest documents per bot → chunk → enrich (Haiku contextual) → embed (ZE zembed-1 1280-dim) → store. 4 worker instance parallel via systemd template + Redis Streams fan-out.
- **Query (Q1–Q32 adaptive)** + 2 ingress (Q0/Q0.5): chat retrieve + grade + generate + persist. Adaptive router gates expensive nodes per intent.

Mỗi pipeline bắt đầu với **identity validate + bot resolve 4-key** trước khi vào logic chính.

**4-key bot identity** (V10):
- Body 3-key external: `(workspace_id, bot_id, channel_type)`
- JWT bearer claim: `record_tenant_id` UUID
- Internal resolve: `(record_tenant_id, workspace_id, bot_id, channel_type)` → `record_bot_id` UUID
- DB unique: `uq_bots_record_tenant_workspace_bot_channel`

---

## Identity contract (V10)

```
EXTERNAL (HTTP body):
  workspace_id  : VARCHAR(64)  REQUIRED   regex ^[a-zA-Z0-9-]+$
  bot_id        : VARCHAR(64)  REQUIRED   slug
  channel_type  : VARCHAR(32)  REQUIRED   allowed list

EXTERNAL (JWT bearer):
  record_tenant_id : UUID      REQUIRED

INTERNAL (DB row):
  record_bot_id : UUID         PK của bots.id
  bots row có: (record_tenant_id, workspace_id, bot_id, channel_type) UNIQUE
```

**Workspace pass-through**: tenant truyền sao platform lưu vậy. Missing/null `workspace_id` → fallback `str(record_tenant_id)`. Slug mới chưa từng thấy → trả empty (KHÔNG 404). Slug invalid format → 422 `WorkspaceIdInvalid`. Cross-workspace isolation tự nhiên qua 4-key DB unique. Platform KHÔNG quản lý workspace lifecycle.

---

## UPLOAD pipeline (U0/U0.5 + U1 → U7)

Flow: HTTP body 3-key + JWT bearer → identity validate (U0) → bot resolve 4-key (U0.5) → size+dedup guard (U1) → mime parse (U2) → text clean (U3) → strategy chunk (U4) → contextual enrich (U5) → optional VN segment (U6) → embed + store (U7). Chunks luôn chain qua `documents.record_bot_id`; workspace ngầm qua bot row.

| Step | Name | Input | Output | Code path | Notes |
|---|---|---|---|---|---|
| U0 | IDENTITY_VALIDATE | HTTP body 3-key + JWT | request.state.record_tenant_id (UUID) | `interfaces/http/schemas/document_schema.py` | Pydantic + WorkspaceIdValidator; 422 if missing |
| U0.5 | BOT_RESOLVE_4KEY | (rt, ws, bot, ch) | BotConfig (record_bot_id UUID) | `application/services/bot_registry_service.py` | Redis cache `ragbot:bot:{rt}:{ws}:{bot}:{ch}`; miss + DB no row → policy decides (lazy-create or 404) |
| U1 | VALIDATE | bytes / source_url / content | accepted job | `application/services/document_service.py :: ingest()` | size guard 500k chars + content_hash + source_url dedup |
| U2 | PARSE | mime + content | structured text | `infrastructure/parser/registry.py` | pdf / excel / sheets / markdown / null |
| U3 | CLEAN | raw text | normalized text | `shared/text_normalization.py` | NFC + hyphenation + injection strip (ZWS/control) |
| U4 | CHUNK | normalized text | List[Chunk] | `infrastructure/parser/chunk_strategy.py` | Strategy registry: table_csv / recursive / hdt / semantic / hybrid / proposition |
| U5 | ENRICH | chunks + doc | chunks w/ context-prefix + metadata | `application/services/contextual_chunk_enrichment.py` | Whole-doc bypass + parent-child + Contextual Retrieval (Anthropic 2024-09) |
| U6 | VN_SEGMENT | chunk text (vi) | tokenized text | `infrastructure/tokenizer/vi.py` | underthesea compound segmentation; null fallback for EN |
| U7 | EMBED + STORE | chunks | rows in `documents` + `document_chunks` | `infrastructure/embedding/litellm_embedder.py` + `infrastructure/vector/pgvector_store.py` | EmbedderPort default binding (ZeroEntropy zembed-1 1280-dim matryoshka); HNSW + tsvector indexes; chunks chain via `record_bot_id` |

**Default knobs (highlights — full table in [`docs/master/15-O-anti-hallu-tuning.md`](docs/master/15-O-anti-hallu-tuning.md))**:
- U1 `ingest_max_doc_size_chars=500_000` · U4 `chunk_size=1024` / `overlap=128` / `whole_doc_threshold=5000`
- U5 `contextual_retrieval_enabled=true` · `enrichment_model=gpt-4.1-mini` · `temperature=0.0` · prompt-cache on
- U7 embedder = `zembed-1` (ZeroEntropy, 1280-dim matryoshka; request `dimensions:1280`)

---

## 🆕 2026-05-09 wave — new pluggable nodes (admin wires DI later)

The 24-step pipeline shape is unchanged. Coder added 5 swappable Ports that
admin can opt-in by flipping the per-bot config flag:

| Port | Where it slots | Default | Effect when active |
|---|---|---|---|
| `SelfRagRouterPort` | Pre-Q6 | `null` | Skip retrieve when intent in `{greeting, chitchat, vu_vo}` (-30-50% tier-1 latency) |
| `ProximityCachePort` | Pre-Q6 + Q17 | `null` | LSH-bucket lookup of similar prior queries (target 25-40% hit ratio) |
| `ConvoSummaryPort` | Q3 condense path | `null` | Compress N-turn history to ≤200 token summary |
| `TenantModelTierPort` | Q14 binding resolve | `null` | Filter `bot_model_bindings` to allowed tiers `{cheap, mid, premium}` per tenant |
| `RagasMetricPort` | Off-pipeline (eval CLI) | stub | Faithfulness / answer_relevancy / context_precision / context_recall scoring |

All 5 follow Strategy + Registry + Null Object pattern. None touch chat hot
path (Quality Gate #10). Admin merges G1 → wires DI in `bootstrap.py` → flips
per-bot flag.

### TASK-10 graph singleton

`build_graph()` is now invoked once via `get_graph()` (async-locked
singleton). Per-request params flow through `GraphState`:
`step_tracker`, `bot_system_prompt`, `kg_service`, `session_factory`. The
compiled graph is multi-tenant safe because data lives in state, not
closure. `_reset_graph_singleton_for_test()` clears the cache for tests.
Effect: -100-200ms cold start, no per-request graph rebuild cost.

### TASK-15 Auditor markdown-aware regex

The multi-agent review framework's deterministic Auditor pre-strips
markdown code-fence/backtick/blockquote/indent-4 blocks before running the
sacred-keyword regex (`_v[0-9]`, `_legacy`, `if provider ==`). Eliminates
false-positive REJECTED on plans/docs that DISCUSS forbidden patterns
inside code-fence quotes.

---

## QUERY pipeline (Q0/Q0.5 + Q1 → Q17)

Flow: HTTP body + JWT → identity validate (Q0) → bot resolve (Q0.5) → input guard (Q1) → 2-tier cache (Q2, parallel Q3) → understand+condense (Q3) → multi-query/HyDE (Q4) → decompose multi_hop (Q5) → hybrid retrieve fanout (Q6) → graph retrieve synthesis (Q7) → min-score filter (Q8) → MMR pre-rerank (Q9) → rerank (Q10) → MMR post-rerank (Q11) → CRAG grade (Q12) → rewrite_retry loop (Q13) → generate (Q14) → output guard (Q15) → reflect grounded (Q16) → persist + outbox (Q17).

| Step | Name | Purpose | Code path | Skip if |
|---|---|---|---|---|
| Q0 | IDENTITY_VALIDATE | Pydantic body + JWT lift | `interfaces/http/schemas/chat_schema.py` | — |
| Q0.5 | BOT_RESOLVE_4KEY | Lookup BotConfig from 4-key | `application/services/bot_registry_service.py` | None → caller short-circuit empty answer |
| Q1 | GUARD_INPUT | length + NFC + injection + PII detect | `infrastructure/guardrails/local_guardrail.py` | per-bot toggle |
| Q2 | CHECK_CACHE | L1 Redis exact + L2 pgvector semantic @0.97 | `infrastructure/cache/redis_cache.py` + `pg_semantic_cache` | hit → jump to Q17 |
| Q3 | UNDERSTAND_QUERY | intent classifier + condense + cost-aware route | `application/services/multi_query_expansion.py` | — |
| Q4 | REWRITE | HyDE + 3-N paraphrases | `application/services/multi_query_expansion.py` | intent ∈ chitchat OR <5 tokens |
| Q5 | DECOMPOSE | structured sub-queries | `orchestration/query_graph.py :: _router_route()` | intent ≠ multi_hop OR <8 tokens |
| Q6 | RETRIEVE | hybrid dense+BM25+RRF per variant | `infrastructure/vector/pgvector_store.py :: hybrid_search()` | filter `record_bot_id` only |
| Q7 | GRAPH_RETRIEVE | knowledge_edges traversal (synthesis intents) | `infrastructure/graph/graph_retriever.py` | non-synthesis intent; fail-soft empty |
| Q8 | FILTER_MIN_SCORE | drop chunks below per-intent threshold | `orchestration/query_graph.py` | chitchat bypass=0.0 |
| Q9 | MMR_DEDUP_PRE | drop near-duplicates pre-rerank | `orchestration/query_graph.py :: _mmr_dedup()` | <2 chunks |
| Q10 | RERANK | cross-encoder default binding (ZeroEntropy `zerank-2`; per-bot Cohere/ViRanker/null swappable) | `infrastructure/reranker/registry.py` + `ApiKeyPoolFactory` | intent ∈ chitchat/OOS |
| Q11 | MMR_DEDUP_POST | second MMR pass after rerank | `orchestration/query_graph.py :: _mmr_dedup()` | <2 chunks |
| Q12 | GRADE | CRAG 3-state per-chunk LLM grader | `orchestration/query_graph.py :: grade()` | per-bot toggle |
| Q13 | REWRITE_RETRY | reformulate + loop back to Q6 | `orchestration/query_graph.py` | retries ≥ max (default 1) |
| Q14 | GENERATE | LLM answer with bot's `system_prompt` SSoT, per-purpose binding, LITM, prompt-cache; `gpt-4.1-mini` (platform answer-model policy; ALL per-bot answer bindings realigned to mini — alembic 0184, 2026-06-08) | `orchestration/query_graph.py :: generate()` + `infrastructure/llm/dynamic_litellm_router.py :: complete()` | — |
| Q15 | GUARD_OUTPUT | shingle leak + PII redactor; refusal text from `oos_answer_template`; leak-shingle skipped when answer ≈ `oos_answer_template` (Jaccard ≥ `DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD=0.90`) — refusal text shares vocab with sysprompt | `infrastructure/guardrails/local_guardrail.py` | — |
| Q16 | REFLECT | re-generate if grounding < threshold | `orchestration/query_graph.py :: reflect()` | intent ∈ DEFAULT_SKIP_REFLECT_INTENTS (factoid/greeting/feedback/chitchat/vu_vo/OOS) |
| Q17 | PERSIST | INSERT semantic_cache + conversations + messages + request_logs + request_steps + outbox event | `orchestration/query_graph.py :: persist()` + `infrastructure/events/redis_streams_bus.py` | — |

**Default knobs (highlights — full matrix in [`docs/master/15-O-anti-hallu-tuning.md`](docs/master/15-O-anti-hallu-tuning.md))**:
- Q2 `pipeline_cache_similarity_threshold=0.97` · `semantic_cache_ttl=3600s`
- Q6 `rag_top_k=20` · `rrf_k=60` · BM25 substring fallback on
- Q8 `rerank_filter_strategy="cliff"` (default) with `force_min_keep=True` + `cliff_absolute_floor=0.05` · **`rerank_cliff_min_keep=3` (default raised 1→3, alembic 0181, 2026-06-05)** · `threshold` strategy (opt-in per-bot) uses `reranker_min_score_active=0.30` for zerank-2 (Cohere=0.30, Voyage=0.35)
- Q10 `rag_rerank_top_n=7` (per-intent override `rerank_top_n_by_intent` factoid=7/aggregation=20) · per-bot `reranker_provider`
- Q14 `generation_temperature=0.0` · `generate_max_tokens=250` · `generate_context_chars_cap=2900` (Chroma 2025 cliff guard) · `citation_marker_required=true`
- Q16 `grounding_check_enabled=true` · `threshold=0.3` · `pipeline_max_reflect_retries=2`
- 2026-05-09 new ports defaults (admin opt-in): `DEFAULT_SELF_RAG_SKIP_INTENTS={greeting, chitchat, vu_vo}` (R5.A1) · `DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD=0.92` (R5.B3)

**9-layer anti-hallu map** (1) temperature=0 → Q3/Q4/Q5/Q12/Q14/U5; (2) grounding+citation → Q14+Q15+Q16; (3) chunk quality `reranker_min_score` → Q8; (4) self-correct retries → Q16; (5) retrieve top_k+rerank_top_n → Q6+Q10; (6) generate caps → Q14; (7) chunking → U4; (8) cache threshold → Q2; (9) per-bot sysprompt + oos_template → Q14.

### Retrieval-funnel forensic (step-level, 2026-06-05)

Step-by-step trace (`request_steps` funnel) of an exact-fact legal miss (thong-tu
"hiệu lực/thay thế" — Điều 56) isolated WHERE chunks are lost. Read top→down; a
chunk must survive every gate:

| Step | Gate | Failure mode found | Fix (layer) |
|---|---|---|---|
| Embed (U5) | CR-context label feeds the vector | narration prefix paraphrased away the identifiers → exact-fact chunk dense ~0.08 | **CR-prompt copies verbatim** doc-numbers/dates/% (`chunk_context_enricher.py`); re-ingest → Điều-56 vector rank 2/80, BM25 rank 1/80 ✅ |
| **Q6 stats-index route** (TRUE ROOT) | `parse_range_query` gates the price/stats short-circuit | **false-positive: "Thông tư 09/2020" ascii-folds "tư"→"tu" = the range token "từ", and "09" parses as price≥9 → query false-routes to the stats/price path, which returns uniform score=1.0 chunks that EXCLUDE the answer clause and bypass hybrid retrieval** | **`query_range_parser` rejects (a) number followed by `/digits` (date/doc-ref), (b) unit-less bare number < 1000 VND** → legal queries no longer route to stats; price queries (`dưới 800 nghìn`, `trên 1 triệu`) preserved. DEFAULT fix, domain-neutral, every bot. ✅ RESOLVED — thong-tu now answers "01/01/2021 + 18/2018" |
| Q10 rerank | semantic cross-encoder reorder | reranker under-ranks an exact-clause chunk that BM25 ranks #1 (79.7% gap) | reranker quality (open); mitigated by safety-net below |
| Q10 retrieval safety-net | post-rerank keep-set | a strong-retrieval chunk silently dropped by a bad reranker score | **`rerank_retrieval_safety_n=2` (default-on)** unions top-2 retrieval-ordered chunks back in (`query_graph` rerank node) |
| Q8 cliff `filter_min_score` | `min_keep` + `gap_ratio` cut | `min_keep=1` collapsed 7→1 on one reranker mis-score | **default `rerank_cliff_min_keep` 1→3** (alembic 0181); legal +5 (0180) |
| Q12 grade / Q14 generate | LLM relevance + answer | only the wrong chunk → refuse / (rarely) fabricate | **owner sysprompt anti-over-refuse + fact-extract** (alembic 0178, behavioral, leak-safe) |

Principle applied (product): the platform **DEFAULT** is tuned to "smart/happy"
— no single mis-score collapses retrieval, CR keeps identifiers, the range
parser never mistakes a document number for a price, the rewrite strips
preamble. Per-bot `plan_limits` overrides only **ADD** expert headroom (legal
min_keep=5, HyDE on); they never rescue a broken default. A newly-created bot
inherits all the smart defaults with zero tuning.

---

## Skill set required per-step

See [`docs/master/04-D-pipeline-orchestration.md`](docs/master/04-D-pipeline-orchestration.md) for the full skill matrix per step (Pydantic / parser strategy / hybrid retrieval / CRAG / etc).

---

## Sacred contracts

HALLU=0, app-không-inject-text, app-không-override-answer, 4-key identity, domain-neutral, zero-hardcode, Strategy+DI, no-version-ref, provider-agnostic ApiKeyPool, notify-after-retry-exhaust — see [`CLAUDE.md`](CLAUDE.md) for authoritative wording.

---

## Smartness gates (T1)

- HALLU_FABRICATE = 0 sacred per round
- PASS rate ≥ 85% on baseline load test
- top_score avg ≥ 0.5 (corpus-quality dependent)
- 9 question categories handled (factoid / comparison / multi_hop / aggregation / OOS / greeting / feedback / chitchat / vu_vo)
- Anti-Fake-Premise / Promo / Incident clauses present in active sysprompt

---

## Cost+Perf gates (T2)

- p95 latency ≤ 14s (post-Phase 4 target); p99 ≤ 25s
- Cost/turn ≤ $0.001 target
- Provider prompt-cache hit ≥ 30%
- ApiKeyPool failover events ≤ 5/hour; LLM provider failover ≤ 1/hour
- Bypass-counter visible to admin dashboard

---

## Numbers verified

Always-fresh load-test PASS / HALLU / latency / cost numbers live in [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md). This file no longer pins a baseline (post-V12 re-measure cycle).

---

## Audit checklist — top 10 actionable

Full extended checklist (architecture / smartness / perf / multi-tenancy / observability / domain-neutral / tests) in [`docs/master/08-H-enforcement-acceptance.md`](docs/master/08-H-enforcement-acceptance.md). Top 10 to gate per release:

1. Alembic 0062 unique constraint `uq_bots_record_tenant_workspace_bot_channel` enforced.
2. `BotRegistryService.lookup` always called with 4 keys (no optional / default).
3. Pydantic ingress rejects malformed `workspace_id` (422 not 500).
4. JWT bearer carries `record_tenant_id`; body NEVER carries it on chat/document routes.
5. Per-bot `system_prompt` SSoT — zero application text injection in Q14 prompt builder.
6. No `if provider ==` branch in `src/ragbot/orchestration/` or `application/services/` (Strategy+DI gate).
7. No magic numbers / model literals in hot-path (zero-hardcode pre-commit grep clean).
8. HALLU_FABRICATE = 0 in latest load test round (per `STATE_SNAPSHOT.md`).
9. CircuitBreaker + ApiKeyPool failover wired for ZeroEntropy + LiteLLM + reranker registry.
10. `request_steps` rows persisted with `workspace_id` for every Q-step instrumented.
11. PostgreSQL Row-Level Security live: alembic 0069 enables `tenant_isolation` policy + `FORCE ROW LEVEL SECURITY` on 20 tables (18 direct `record_tenant_id`, 2 JOIN-via-FK). App must connect under a non-superuser role for RLS to actually filter — see `T1.S1b` in `plans/260508-master-replan-tiered/plan_v2.md`.

---

## Compare with other RAG projects

See research notes in [`docs/academic-papers/INDEX.md`](docs/academic-papers/INDEX.md) for benchmark comparisons.

---

## File này dùng cho

- **Onboarding** — single doc covers ingest + query at a glance
- **Audit reference** — top-10 checklist + sacred contracts pointer
- **Architecture review** — table maps step → code path
- **Plan reference** — cite Q-step / U-step in plan files when wiring features
- **Skill briefing** — Skill required per-step pointer for new engineers

---

## Early-exit map — 11 paths có thể làm bot im lặng / sai

Khi diagnose bot không trả lời (Tokens=0/Cost=$0), trace theo 11 nhánh sau (file:line ở `src/ragbot/orchestration/query_graph.py`):

| # | Path | Line | Trigger | Effect |
|---|---|---|---|---|
| E1 | Input guardrail block | 3623 | rule blocked stage=input | persist, skip all |
| E2 | Cache hit | 3629-3632 | `cache_status="hit"` + answer | persist, skip all |
| E3 | **Refuse short-circuit** | 2978-3008 | `graded=[]` + intent NOT chitchat + `refuse_short_circuit_enabled=True` | LLM skip → empty/oos_template — **MAIN silent-bot trigger** |
| E4 | Retrieve 0 chunks | 3717-3720 | `retrieved=[]` | jump to generate → E3 |
| E5 | **Threshold cuts all** | 2536 | mọi score < `reranker_min_score_active` (chỉ áp khi `rerank_filter_strategy="threshold"`) | `reranked=[]` → grade `[]` → E3 |
| E6 | Grade all-irrelevant | 2865-2876 | LLM grade tất cả "no" | `graded=[]` → E3 |
| E7 | CRAG retry exhausted | 3684-3690 | retries hết, vẫn inadequate | generate với `[]` → E3 |
| E8 | Iteration cap | 3696/3699 | total_iters > max | persist |
| E9 | Output guardrail block | 3471-3472 | output filter blocked | replace với oos_template |
| E10 | **Cliff fail-safe** | 418-486 | `force_min_keep=True` (default) | preserve top-1 — **NEVER returns []** (safety net) |
| E11 | Reflect skip | 3697 | intent in DEFAULT_SKIP_REFLECT | bypass reflect (không critical) |

E3 là gate cuối cùng quyết định bot im lặng vs trả lời. Mọi nhánh E4/E5/E6/E7 hội tụ về E3 khi không có chunks. Hai cách tránh E3:

1. **Strategy cliff** (default từ 2026-05-07): `force_min_keep=True` đảm bảo E10 trả ≥1 chunk → E3 không fire
2. **Lower threshold** trên `system_config.reranker_min_score_active` → E5 ít cắt hơn

## Default config flow — sync 4 source khi đổi threshold

Đổi default reranker/cliff/grounding phải sync **4 nơi** (chi tiết: `RAGBOT_MASTER.md` section 14):

1. `constants.py` `DEFAULT_*`
2. `bot_limits.py` `PLAN_LIMIT_SCHEMA[key]["default"]` (reference constant — KHÔNG hardcode literal)
3. `init_system_config.py` seed
4. Alembic UPSERT migration cho deployment cũ

Plus `chat_worker.py` + `test_chat.py` phải đọc qua `resolve_bot_limit(bot_cfg, key, system_default=…)`.

**Sai phạm 2026-05-07** (đã closed via 8 commit + alembic 0067 + DB UPSERT):

- Lower `DEFAULT_RERANKER_MIN_SCORE_ACTIVE` constant nhưng `system_config.reranker_min_score_active` còn giá trị cũ → fallback không kích hoạt → bot tiếp tục im lặng cho tới khi alembic UPSERT live row.
- 6 keys (reranker_min_score_active, rerank_filter_strategy, rerank_cliff_*x3, grounding_check_threshold) bypass `resolve_bot_limit` ở chat_worker → per-bot override hoàn toàn vô tác dụng.

---

**Parent doc**: [`RAGBOT_MASTER.md`](RAGBOT_MASTER.md) section 5
**Sibling**: [`README.md`](README.md) quick start + troubleshooting
**Coder-branch inventory** (latest `coder-260509-*` wave + per-task PR/merge status): [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md)
