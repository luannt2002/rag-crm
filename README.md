# Ragbot

> **Multi-tenant Retrieval-Augmented Generation (RAG) chatbot platform.** Vietnamese-first, multilingual. Adaptive pipeline (9 ingest steps + ~21 query nodes) that turns a tenant's document corpus into a refusal-safe Q&A bot. The product is the **API** (BE-to-BE integration, ~90% of real traffic); the demo FE pages are a test harness only. Platform owns ingestion, retrieval, faithfulness checking, refusal, observability — bot owner only ships documents + a `system_prompt`.

**Stack (verified against code at alembic head `0219`, 2026-06-16; model catalog locked at `0216`)**:
- **Runtime**: Python 3.12+ · FastAPI + uvicorn (**single process `ragbot-py`, 1 worker**) · LangGraph orchestration. The one process also runs 4 embedded asyncio workers (ingest consumer, outbox, recovery, cost-cap alerter).
- **DB**: PostgreSQL 16 · pgvector HNSW · pg_trgm GIN · tsvector BM25 · RRF hybrid · Row-Level Security (policies present; app connects as superuser so isolation is enforced by mandatory `record_bot_id` app-filter — §6) · **alembic head `0216`**
- **Cache**: Redis L1 (60s TTL) · pgvector semantic L2 @0.97 · OpenAI auto prompt-cache
- **Events**: Redis Streams · transactional outbox · dedup ledger
- **Model catalog (LOCKED 2026-06-14, alembic 0216 — only these 4 exist)**:
  - **`gpt-4.1-mini`** — primary LLM for ALL OpenAI tasks: answer, grading, grounding judge, query-transforms (rewrite/multi-query/decompose/condense/intent), narrate, slot-extract. Query-transforms run at **temperature 0** (deterministic — §5).
  - **`gpt-4.1-nano`** — available for cheap small tasks (not currently bound; 0212 routes everything to mini for the quality baseline).
  - **`ZeroEntropy zembed-1`** (1280-dim matryoshka) — embedding · **`ZeroEntropy zerank-2`** cross-encoder reranker · filter strategy **`cliff`** (gap-cut + absolute floor `0.05`).
  - Removed entirely (cannot be selected): claude-haiku, gpt-4.1 (full), gpt-5, gemma, qwen, and the anthropic/jina/lmstudio/infinity/tei providers — see alembic 0216.
- **Patterns**: Hexagonal/DDD · Strategy + Port + Adapter + Registry + Null Object + DI · Domain-neutral · Zero-hardcode · Narrow exception
- **Webhook**: HMAC versioned secrets · **Auth**: JWT + 7-tier RBAC · **Obs**: Prometheus + OTEL + structlog JSON

**Design constraint**: the bot must **never fabricate**. Every node — chunking, embedding, multi-query, hybrid retrieval, reranking, MMR dedup, CRAG grading, generation, grounding check, reflection — earns its keep by lifting accuracy OR guaranteeing refusal when context is weak. No "look-better" nodes. The application **never injects template text into the LLM prompt nor overrides the LLM answer**; the bot owner's `system_prompt` is the single source of truth.

---

## What changed & why (latest flow — 2026-06-14, alembic 0216)

Two kinds of change. **(A) Engine swaps vs a textbook RAG / the original AdapChunk spec** — deliberate, policy-driven (detail in §3.2):

| Đổi gì | Từ → Sang | Vì sao |
|---|---|---|
| Vector DB | Qdrant → **pgvector** | 1 database (PostgreSQL), RLS-capable, bớt ops |
| OCR | Mistral OCR (cloud) → **Kreuzberg** (self-hosted) | dữ liệu không rời công ty; cost |
| Chunk strategy selector | **LLM** → **rule-based scorer** | deterministic, 0 LLM-cost/doc, reproducible |
| Embedding | sbert/BGE-m3 → **ZeroEntropy zembed-1** | matryoshka 1280-dim, 1 vendor cho embed+rerank |

**(B) Latest phase (2026-06-14)** — each measured, with the reason:

| Đổi gì | Trước → Sau | Vì sao (evidence) |
|---|---|---|
| **Model catalog** | haiku + gpt-4.1-full + gpt-5 + gemma/qwen → **chỉ gpt-4.1-mini + nano + zembed-1 + zerank-2** | gpt-4.1-full là answer-model đốt 5× tiền giai đoạn 11-13/6; haiku/gemma/qwen orphan. Xóa hẳn (DB + constants + seed + alembic 0216) nên không thể chọn lại |
| **All LLM tasks → mini** | nano/gemma per-task → **gpt-4.1-mini** (alembic 0212) | "get-it-correct" phase: mini long-context tốt + auto-cache; nano để dành khi A/B chứng minh không regression |
| **Process topology** | 4 systemd service (api + worker + outbox + recovery) → **1 process `ragbot-py`** (4 embedded asyncio task) | bớt ops, 1 chỗ quản; embedded workers qua `APP_EMBED_WORKERS_ENABLED` |
| **Latency (burst)** | async grounding judge chung semaphore foreground | **lane semaphore riêng** (`{provider}::background` cap 4) | đo: backlog grounding bỏ đói foreground generate → post-burst factoid **26.8s→3.3s** |
| **UI workspace** | read-path 404 cho bot non-default workspace | **`find_by_3key_unique`** (unique-match khi không có workspace_id) | fix demo UI "3 bot trống" sau khi bot move sang workspace (alembic 0213) |
| **Cost profile (đo)** | — | chat **~$0.006/câu** · upload **~$0.013/ingest** · factoid 70% traffic chỉ ~4 LLM call (multi_query+rewrite đã intent-gate OFF) |

**Giữ NGUYÊN khung-luồng**: 2 LangGraph state machine (ingest U0-U7 + query ~21 node), 4-key identity, HALLU=0 sacred, app không inject/override answer, Strategy+DI. **Answer model gpt-4.1-mini.** Query-transforms temperature **0** (deterministic). Grounding sync XOR async (không chạy đôi).

---

## Table of contents
1. [What Ragbot does](#1-what-ragbot-does)
2. [The adaptive pipeline](#2-the-adaptive-pipeline)
3. [AdapChunk — adaptive chunking (built incrementally)](#3-adapchunk--adaptive-chunking-built-incrementally)
4. [Identity contract — 4 keys](#4-identity-contract--4-keys)
5. [Determinism + anti-hallucination](#5-determinism--anti-hallucination)
6. [Sacred contracts + known gaps](#6-sacred-contracts--known-gaps)
7. [Document ingest — 2-action async pattern](#7-document-ingest--2-action-async-pattern)
8. [Default config reference](#8-default-config-reference)
9. [Reference docs](#9-reference-docs)

---

## 1. What Ragbot does

Each tenant maps a `workspace + bot slug + channel` (web / Zalo / Messenger / API) to an isolated knowledge base. Two tenants can run bots with the same `bot_id` slug without leaking data — isolation is intended at every layer (schema, repository, middleware, audit log).

A request flows through **two LangGraph state machines** that communicate **only** via the vector store (`document_chunks`) + event bus (Redis Streams) — no direct calls — so each scales / deploys / fails independently:

```
Ingest graph (async, worker-driven)        Query graph (per request, actual nodes)
──────────────────────────────────        ───────────────────────────────────────
U0   IDENTITY_VALIDATE                     guard_input              (input guardrail)
U0.5 BOT_RESOLVE_4KEY                      cache_check + understand (parallel)
U1   VALIDATE (size + dedup)               condense / router / query_complexity
U2   PARSE (Kreuzberg OCR/xlsx/sheets/md)  rewrite_and_mq_parallel | decompose | adaptive_decompose
U3   CLEAN (NFC + injection strip)         retrieve                (dense + BM25 + RRF, multi-query fanout)
U4   CHUNK (AdapChunk — §3)                graph_retrieve          (optional, synthesis)
U5   ENRICH ◀── gpt-4.1-mini               rerank ◀── zerank-2     (+ cliff filter + retrieval safety-net)
U6   VN_SEGMENT (underthesea)              mmr_dedup → neighbor_expand (opt)
U7   EMBED + STORE ◀── zembed-1            grade                   (CRAG 3-state + compound leniency)
                                           rewrite_retry           (loop to retrieve if grade fails)
                                           generate ◀── gpt-4.1-mini
                                           critique_parse          (Self-RAG, opt-in)
                                           guard_output            (shingle/PII + grounding judge ◀── gpt-4.1-mini, sync XOR async lane)
                                           reflect → persist       (cache + audit + outbox)
```

Ingest graph: `src/ragbot/application/services/document_service.py`. Query graph: `src/ragbot/orchestration/query_graph.py`. Setup → first chat: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

> The query column lists the **actual** LangGraph nodes (verified in `build_graph()`), not a "Q0–Q17" abstraction. Names like `cache_check`, `filter_min_score`, `prompt_build`, `citations_extract`, `litm_order`, `grounding_check` are observability sub-steps (`request_steps`) inside these nodes.

---

## 2. The adaptive pipeline

An **Adaptive Query Router** gates expensive nodes per intent: a greeting/simple lookup runs a short path; a multi-hop question runs decompose → multi-query fanout → CRAG retry → reflect. Opt-in per-bot (default OFF / Null Object): `graph_retrieve`, `neighbor_expand`, `critique_parse` (Self-RAG), HyDE, speculative streaming.

### 2.1 Ingest pipeline — U0 / U0.5 + U1 → U7

| Step | Name | Output | Code path | Notes |
|---|---|---|---|---|
| U0 | IDENTITY_VALIDATE | `request.state.record_tenant_id` UUID | `interfaces/http/schemas/document_schema.py` | Pydantic + workspace validator; 422 if missing |
| U0.5 | BOT_RESOLVE_4KEY | `BotConfig` + `record_bot_id` | `application/services/bot_registry_service.py` | Redis key `ragbot:bot:{rt}:{ws}:{bot}:{ch}`; miss+no-row → 404 |
| U1 | VALIDATE | accepted job (HTTP 202) | `application/services/document_service.py::ingest()` | **size guard `MAX_DOCUMENT_CONTENT_CHARS=500_000`** + content_hash + source_url dedup |
| U2 | PARSE | structured text | `infrastructure/parser/registry.py` (singular `parser`) | Kreuzberg OCR / openpyxl / google-sheets / markdown; **SKIP refetch if `raw_content` already in DB** |
| U3 | CLEAN | normalized text | `shared/text_normalization.py` | NFC + hyphenation + prompt-injection strip |
| U4 | CHUNK | List[Chunk] | **`shared/chunking/` package `smart_chunk()`** | AdapChunk adaptive selection — §3 (split into vn_structural/analyze/blocks/csv_chunker/strategies, 2026-06-15) |
| U5 | ENRICH | chunks + context prefix | `application/services/contextual_chunk_enrichment.py` | whole-doc bypass + parent-child + Anthropic Contextual Retrieval; `gpt-4.1-mini` |
| U6 | VN_SEGMENT | tokenized text | **`shared/vi_tokenizer.py`** | underthesea compound segmentation; null fallback for non-VI |
| U7 | EMBED + STORE | rows in `documents` + `document_chunks` | `infrastructure/embedding/zeroentropy_embedder.py` + `vector/pgvector_store.py` | zembed-1 1280-dim; HNSW + tsvector; atomic state `DRAFT→active/failed` |

**Default knobs**: `chunk_size=1024` / `parent_chunk_size=1024` / `child_chunk_size=256` / `chunk_overlap=128` · `enrichment_model=gpt-4.1-mini` · embed batched with `embed_inter_batch_sleep_s=0.5` (ZE rate-limit) · embedder circuit-breaker + bounded concurrency.

### 2.2 Query pipeline — actual node flow

```
guard_input → cache_check_and_understand_parallel
   ├─(cache hit)──────────────────────────────────────────► persist
   └─► understand_query / condense_question / router / query_complexity
        → rewrite_and_mq_parallel | decompose | adaptive_decompose
        → retrieve ──(no chunks)──► generate (refuse short-circuit → oos_template)
        → graph_retrieve(opt) → rerank → mmr_dedup → neighbor_expand(opt)
        → grade ──(fail & retries left)──► rewrite_retry → retrieve
        → generate → critique_parse(opt) → guard_output
             ├─(blocked)──► persist
             └─► reflect ──(answer empty & iters left)──► generate
                         └─────────────────────────────► persist → END
```

Per-node behavior (verified in code):
- **cache** — L1 Redis exact-hash + L2 pgvector semantic @0.97; key versioned by `system_prompt`+`oos_template`+corpus_version so edits bust the cache.
- **understand/route** — heuristic L1 + LLM intent classifier; cheap intents skip rewrite/decompose/rerank/reflect.
- **retrieve** — hybrid dense+BM25 fused by RRF; multi-query fanout + RRF-merge across variants; per-intent `retrieve_top_k`.
- **rerank** — zerank-2 → **`cliff` filter** (gap-cut, absolute floor `0.05`, keeps full set for aggregation/comparison/multi_hop) → **retrieval safety-net** re-unions top BM25 chunks the cross-encoder under-ranked (legal exact-clause fix). Static threshold gate runs only when strategy≠cliff.
- **grade** — CRAG batch grader (relevant/irrelevant/ambiguous); compound-intent leniency promotes `irrelevant→ambiguous` so multi-fact chunks survive; smart-skip on high top-score; timeout → reranker order (grounding still enforces HALLU=0).
- **generate** — answers from `<documents>` context only; per-intent token + context-char caps; citations validated against retrieved chunk_ids; refuse short-circuit emits `bots.oos_answer_template` when zero chunks (never an injected string).
- **guard_output** — output guardrail (shingle leak / PII) + grounding judge (`gpt-4.1-mini`, ≤5 sentences SUPPORTED/NOT vs context; runs sync OR on an isolated background semaphore lane, never both).
- **reflect** — Self-RAG completeness re-check; bounded by `max_total_graph_iterations`.

### 2.3 Per-turn budget (estimate)
- Tier-1 greeting/chitchat (skip retrieve): 1 LLM call, ~700 tok, ~0.25s
- Tier-2 FAQ: 2-3 calls (understand+answer), ~3K tok, ~1.5s
- Tier-3 multi-hop: 5-8 calls (understand+decompose+grade+answer+reflect), ~10K tok, multi-fact turns measured ~9-17s

---

## 3. AdapChunk — adaptive chunking (built incrementally)

U4 is **AdapChunk**: instead of one fixed splitter for every document, it profiles document structure and picks the best chunking strategy. It is being **built in layers** — the strategy engine is live; the block-aware OCR front-end is partial.

### 3.1 What is LIVE (`shared/chunking/` package)
- **6 strategies**, dispatched by `smart_chunk()`:
  - `table_csv` — row-as-chunk with header prepended (price tables / CSV)
  - `recursive` — heading-then-block split, tables preserved (safe default)
  - `hdt` — Hierarchical Document Tree: split by heading, each chunk carries `structural_path`
  - `semantic` — cut at sentence-similarity drops
  - `proposition` — atomic self-contained facts (legal / contracts)
  - `hybrid` — HDT macro + proposition micro for large sections
- **Rule-based document profile** (`analyze_document`) — deterministic counts (headings/tables/formulas/avg block length/mixed-content). No LLM → reproducible ground truth.
- **Rule-based strategy selector** (`select_strategy`) — weighted scorer + CSV / VN-legal fast paths.
- **Layer-5 rule cross-check** (`apply_cross_check`, default ON) — overrides illogical picks (e.g. HDT with <5 headings → SEMANTIC), logs every override.
- **`structural_path` metadata** on HDT/HYBRID chunks; VN-legal heading promotion (`Chương/Mục/Điều` → markdown) before profiling.

### 3.2 Intentional substitutions vs the original AdapChunk spec (company policy: self-hosted, data-sovereignty, cost, determinism)
| Spec component | Ragbot uses | Why changed |
|---|---|---|
| Qdrant vector DB | **pgvector** (PostgreSQL-native) | one database, RLS-capable, no extra ops surface |
| Mistral OCR (external API) | **Kreuzberg** (Tesseract/EasyOCR, self-hosted) | data never leaves the company; cost |
| **LLM** strategy selector (Tầng 4) | **rule-based weighted scorer** | deterministic, zero per-doc LLM cost, reproducible cross-check ground truth |
| vietnamese-sbert / BGE-m3 | **ZeroEntropy zembed-1** (1280-dim) | matryoshka dims, single vendor for embed+rerank |

The AdapChunk **mindset** (structure-aware, adaptive strategy, atomic-block protection, narrate-then-embed) is preserved; only the engines differ.

### 3.3 What is PARTIAL / flagged (being wired)
- **Block pipeline** (`adapchunk_block_pipeline_enabled`) — flag ON but the parser does not yet emit a block list (Wave B1/B2), so it currently no-ops to the text-flatten path.
- **Atomic-block protection** (TABLE/FORMULA/IMAGE never cut) — implemented (`_smart_chunk_with_atomic_protect`), default **OFF**.
- **Narrate-then-embed** (tables/formulas → natural language; original in `metadata.original_content`) — implemented, but `_narrate_service` is not wired into bootstrap by default → passthrough.
- **`smart_chunk_atomic`** (Block→Chunk, Layer 6) — implemented, not yet routed from `ingest()`.

→ Roadmap: surface a structured block list from the OCR parser → wire `smart_chunk_atomic` + narration → flip atomic-protect ON. Until then U4 runs flat-text adaptive chunking (the 6 strategies), which is production-active.

---

## 4. Identity contract — 4 keys

Internal bot identity = **`(record_tenant_id, workspace_id, bot_id, channel_type)` → `record_bot_id` UUID**.

| Layer | Key | Required |
|---|---|---|
| HTTP body | `bot_id` (slug) | YES |
| HTTP body | `channel_type` (web/zalo/api…) | YES |
| HTTP body | `workspace_id` (slug `^[a-zA-Z0-9-]+$`) | OPTIONAL (null → `str(record_tenant_id)`) |
| JWT bearer | `record_tenant_id` (UUID) | YES (body never carries it) |

Resolve once at the boundary via `BotRegistryService.lookup(...)`; DB unique `uq_bots_record_tenant_workspace_bot_channel` enforces 4-key uniqueness; internal queries then use `record_bot_id` alone. Edge cases: [`docs/dev/IDENTITY_RULE_DETAIL.md`](docs/dev/IDENTITY_RULE_DETAIL.md).

---

## 5. Determinism + anti-hallucination

- **`generation_temperature = 0.0`** always.
- **Query-transform + classification forced to temperature 0** (`DEFAULT_DETERMINISTIC_LLM_PURPOSES`: decompose/rewrite/multi_query/condense/routing/intent/grade/grounding). Measured 2026-06-09: at the inherited ~0.3, the same multi-fact question intermittently refused vs answered because the reformulated sub-query shifted retrieval; forcing 0 makes the pipeline reproducible.
- **Layered HALLU defense (each can refuse independently)**: empty-context refuse short-circuit (emits `oos_answer_template`, never an injected string) · CRAG grade gate · cliff floor `0.05` · per-intent context cap · citation validation vs retrieved chunk_ids · grounding judge (`gpt-4.1-mini`) · Self-RAG critique (opt-in).
- **No numeric-verification step** — the pipeline checks *grounding* (is a claim supported by context) but does **not** recompute arithmetic. `gpt-4.1-mini` multi-number sums can be wrong even when the component numbers are correct; this is a known model limit, reported honestly, **not** patched by app-side compute (sacred: no app override).
- **Grading method**: claim-level Coverage (per-fact LLM judge) + Faithfulness; load tests via `scripts/loadtest_graded.py` (DB-verified gold facts + semantic judge + 3-run determinism). Latest fleet: 87/91 pass, HALLU=0. Per-bot tuning: [`docs/master/15-O-anti-hallu-tuning.md`](docs/master/15-O-anti-hallu-tuning.md).

---

## 6. Sacred contracts + known gaps

**Sacred (non-negotiable — violating any blocks merge):**
1. **HALLU=0** — load-test gate
2. **Domain-neutral** — 0 brand/customer literal in `src/`; domain data → `system_config` / `bots.custom_vocabulary`
3. **Zero-hardcode** — defaults in `shared/constants/`, thresholds via config
4. **Strategy + DI** — every swappable thing via Port + Adapter + Registry + Null Object + DI
5. **App-mindset** — bot owner's `system_prompt` is THE SSoT; app never injects/overrides answer
6. **Narrow exception** — `except Exception:` only at entrypoint / `finally` / background wrapper
7. **4-key identity** — never fewer
8. **No version-ref** — no `_v1/_v2/_legacy/_new/_old` in names
9. **DB content via alembic only** — no psql hot-fix to `system_prompt`/config/bindings

**Known gaps (tracked, honest — see [`reports/DEEPDIVE_ALL_20260609.md`](reports/DEEPDIVE_ALL_20260609.md)):**
- 🔴 **RLS not enforced end-to-end** — policies exist + `session_with_tenant` binds the tenant GUC for pgvector/semantic-cache, but `attach_rls_session_hook` is not wired, so repositories on plain sessions bypass it. Isolation currently depends on `DATABASE_URL_APP` = NOBYPASSRLS `ragbot_app` role. (Plan: wire hook + leak test.)
- 🟡 **LiteLLMReranker** degrades silently to unranked order on error, no circuit breaker (ZE/Jina/Voyage have one).
- 🟡 **Retrieval determinism on dense similar-chunk corpora** (e.g. an 80-article legal circular) — same simple question occasionally retrieves a different chunk set (rerank tie). Mitigated by temp-0 transforms + retrieval safety-net; not fully eliminated.
- 🟡 **AdapChunk block front-end** partial (§3.3).
- ✅ **Model catalog clean (2026-06-14)** — claude-haiku / gpt-4.1-full / gpt-5 / gemma / qwen removed from DB + constants + seed + alembic 0216. narrate + slot-extractor defaults repointed to `gpt-4.1-mini`. Only gpt-4.1-mini + gpt-4.1-nano + zembed-1 + zerank-2 remain.

---

## 7. Document ingest — 2-action async pattern

Industry pattern (OpenAI/Anthropic Files, AWS Textract): 100% async, no sync ingest >30s.

**Action 1** — `POST /bots/{bot_id}/{channel_type}/documents` (sync <1s): validate+dedup → fetch content (Google Docs/Sheets export, HTML→text) → INSERT `documents (state='DRAFT', raw_content)` → INSERT outbox `document.uploaded.v1` → commit → **HTTP 202** `{document_id, state:"DRAFT"}`.

**Action 2** — worker (async): outbox publisher → Redis Stream → 4 `document-worker` instances (XREADGROUP fan-out) → read `raw_content` **from DB (never refetch source_url** — Google Sheets `/edit?gid=` returns an HTML login page) → `ingest()` U1–U7 → progress updates → atomic state flip: `chunks_null>0 → failed`, else `active`.

State machine: `DRAFT → enriching(20%) → embedding(60%) → active(100%)` (or `failed`). UI polls `GET …/documents` every 3s; chat is guarded until docs are `active`. Detail: [`docs/dev/INGEST_FLOW_DEEP_DIVE.md`](docs/dev/INGEST_FLOW_DEEP_DIVE.md).

---

## 8. Default config reference

All defaults in `shared/constants/` (package, re-exported), overridable via `system_config` (platform) or `bots.plan_limits` (per-bot). **Resolve chain (high→low)**: `bots.threshold_overrides` → `bots.<column>` → `bots.plan_limits` → `system_config` → `PLAN_LIMIT_SCHEMA.default` → `constants.DEFAULT_*`. Changing a default = sync 4 places: `constants` + `bot_limits.py` + `init_system_config.py` + an alembic UPSERT.

| Domain | Key | Default |
|---|---|---|
| Cache | `cache_similarity_threshold` | `0.97` |
| Ingest | `MAX_DOCUMENT_CONTENT_CHARS` | `500_000` |
| Chunk | `chunk_size` / `parent` / `child` / `overlap` | `1024 / 1024 / 256 / 128` |
| Embed | `embedding_provider` / `model` / `dim` | `zeroentropy` / `zembed-1` / `1280` |
| Rerank | `reranker_provider` / `model` | `zeroentropy` / `zerank-2` |
| Rerank | `rerank_top_n` | `7` |
| Rerank | `rerank_filter_strategy` | `cliff` |
| Rerank | `rerank_cliff_absolute_floor` | `0.05` |
| Rerank | `reranker_min_score_active` (gate only when strategy≠cliff) | `0.30` |
| Generate | `generation_temperature` | `0.0` |
| Loops | `max_total_graph_iterations` (caps CRAG+reflect) | small int |

Full per-domain tables: [`docs/master/14-N-config-flow.md`](docs/master/14-N-config-flow.md). Worker scale: 2 uvicorn (RAM-constrained VM) + 4 `ragbot-document-worker@` + 1 `ragbot-outbox`.

---

## 9. Reference docs
- [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md) — current state (READ FIRST in a new session)
- [`CLAUDE.md`](CLAUDE.md) — sacred rules for Claude Code agents
- [`RAGBOT_STEP_PIPELINE.md`](RAGBOT_STEP_PIPELINE.md) — canonical pipeline reference
- [`docs/master/`](docs/master/) — architecture sub-pages (A–P); [`11-K`](docs/master/11-K-pipeline-code-mapping.md) step→file:line, [`04-D`](docs/master/04-D-pipeline-orchestration.md) orchestration
- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) · [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) · [`docs/dev/IDENTITY_RULE_DETAIL.md`](docs/dev/IDENTITY_RULE_DETAIL.md) · [`docs/dev/TROUBLESHOOTING.md`](docs/dev/TROUBLESHOOTING.md)
- [`docs/dev/CONFIG_REFERENCE.md`](docs/dev/CONFIG_REFERENCE.md) — **config sources of truth + how to change each (chống drift / dead-key bug)**; model-resolution chain, system_config vs bindings vs constants, alembic-not-psql sync rules
- [`reports/DEEPDIVE_ALL_20260609.md`](reports/DEEPDIVE_ALL_20260609.md) — latest code-vs-doc audit
- [`plans/260609-file-size-reduction/plan.md`](plans/260609-file-size-reduction/plan.md) — file-size refactor plan (industry std ≤1000 lines/file)

> Docs verified against alembic head `0219` (2026-06-16; 0217 monitoring_log, 0218 booking-precedence, 0219 token_budgets; catalog locked at 0216). **If a fact here disagrees with code, the code wins.** Git history was reset on 2026-06-14 (fresh phase); `STATE_SNAPSHOT.md` is the always-updated source of truth.

---

## License
Internal use. See repository owner.
