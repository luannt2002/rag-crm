# Ragbot

> **Multi-tenant Retrieval-Augmented Generation (RAG) chatbot platform.** Vietnamese-first, multilingual. Adaptive pipeline (7 ingest stages + ~21 query nodes) that turns a tenant's document corpus into a refusal-safe Q&A bot. The product is the **API** (BE-to-BE integration, ~90% of real traffic); the demo FE pages are a test harness only. Platform owns ingestion, retrieval, faithfulness checking, refusal, observability — bot owner only ships documents + a `system_prompt`.
>
> **Domain-neutral by construction (ADR-0006/0007/0008)**: tabular structure is understood by **value-SHAPE**, not a hardcoded word list — the column carrying a product's descriptive name is chosen by cell shape (`shared/table_shape.py`), so any brand / language / column layout works with zero engine code. Structured answers pass deterministic HALLU guards: **numeric-fidelity** (every number in an answer must exist in served context/DB) and **brand-scope** (`shared/brand_scope.py` — a "we don't carry brand X" reply is caught when the index actually stocks X). Meaning travels WITH the data (owner `custom_vocabulary` / per-file manifest), never baked into the engine.

**Stack (verified against live DB + code 2026-06-19; alembic head `rls_system_role_grants_20260619`)**:
- **Runtime**: Python 3.12+ · FastAPI + uvicorn (**single process `python -m ragbot.main`**) · LangGraph orchestration. The one process also runs **5 embedded asyncio workers** (ingest consumer, outbox publisher, document recovery, cost-cap alerter, **semantic-cache GC**) — toggle `APP_EMBED_WORKERS_ENABLED`.
- **DB**: PostgreSQL 16 · pgvector HNSW · pg_trgm GIN · tsvector BM25 · RRF hybrid · **alembic baseline `squash_base_20260618`** (squashes the pre-2026-06-18 incremental history) → `phase4_costwin_20260619` → `rls_app_role_grants_20260619` → `rls_system_role_grants_20260619`.
- **Tenant isolation (RLS — request/system role split, Phase 1+2 provisioned)**: 20 tables `FORCE ROW LEVEL SECURITY` + 21 `tenant_isolation` policies on the `app.tenant_id` GUC. Two runtime roles provisioned: **`ragbot_app`** (NOBYPASSRLS — request path) + **`ragbot_system`** (BYPASSRLS — 4 cross-tenant workers). DB-level enforcement is **gated (Phase 3 = DSN flip)**; the app still connects as superuser `ragbot` today, so live isolation is the mandatory `record_bot_id` app-filter (§6). Proven by isolation probe (tenant A sees its rows, other tenant → 0, no-ctx → 0 fail-closed).
- **Cache**: Redis L1 (60s TTL) · pgvector semantic L2 @0.97 · OpenAI auto prompt-cache · hourly GC of expired L2 rows.
- **Events**: Redis Streams · transactional outbox (exactly-once) · transactional inbox dedup.
- **Operative model catalog (per seed + per-bot bindings, verified 2026-06-19)**:
  - **`gpt-4.1-mini`** — primary LLM: answer, grading, grounding judge, query-transforms (rewrite/multi-query/decompose/condense/intent), narrate, slot-extract. Query-transforms run at **temperature 0** (deterministic — §5).
  - **`gpt-4.1-nano`** — cheap small-task LLM (bound; cascade router can route simple intents here).
  - **`jina-embeddings-v3`** (1024-dim) — embedding · **`jina-reranker-v3`** — cross-encoder reranker · filter strategy **`cliff`** (gap-cut + absolute floor).
  - *(Migrated from ZeroEntropy zembed-1/zerank-2 1280-dim → Jina 1024-dim; semantic_cache + embedding column are 1024.)*
- **Patterns**: Hexagonal/DDD · Strategy + Port + Adapter + Registry + Null Object + DI · Domain-neutral · Zero-hardcode · Narrow exception
- **Webhook**: HMAC versioned secrets · **Auth**: JWT + 7-tier RBAC · **Obs**: Prometheus + OTEL + structlog JSON

**Design constraint**: the bot must **never fabricate**. Every node — chunking, embedding, multi-query, hybrid retrieval, reranking, MMR dedup, CRAG grading, generation, grounding check, reflection — earns its keep by lifting accuracy OR guaranteeing refusal when context is weak. No "look-better" nodes. The application **never injects template text into the LLM prompt nor overrides the LLM answer**; the bot owner's `system_prompt` is the single source of truth.

---

## 👥 Read the playbook for your role — 3 roles, 3 config-ownership modes

This README is the shared **architecture** reference. The day-to-day *workflow* is split by role,
each owning one part of configuration so no value is ever half-owned or hand-edited into drift:

| Your role | Playbook | Your mode — what you own |
|---|---|---|
| **BE developer** | [README_DEV.md](README_DEV.md) | **CONTRACT** — code + pipeline logic + the config *contract* (key names + types). Reads values from the DB, **fails loud on a missing key** — never an inline default. |
| **Database / data team** | [README_DATABASE.md](README_DATABASE.md) | **VALUES** — every config *value* the platform runs on: `system_config` seed, alembic seed migrations, bot content. Keeps the seed complete; **no psql hot-fix**. |
| **DevOps** | [README_DEVOPS.md](README_DEVOPS.md) | **GATE** — CI, the config-completeness gate, Docker build, env/secrets, deploy, RLS flip. The gate proves the seed covers the contract *before* build. |

**Why the split:** config *values* belong to the data team (seed/DB), not to backend code. The
backend declares only the *contract* and fails loud if a key is missing; DevOps' init-test gate
guarantees a shipped image can't have a missing key. This removes silent code-defaults — prod
never runs on a value nobody chose, and every DB behaves identically on a fresh clone.

Runtime resolve chain (high → low): `bots.threshold_overrides → bots.<column> → bots.plan_limits → system_config → (schema default, being removed)`. Prod truth is in the DB, not in constants.

---

## What changed & why (latest phase — 2026-06-19)

> **Happy-case input control (2026-06-22)** — thay vì cố parse mọi format bẩn (vô hạn), platform định nghĩa **1 happy-case template chuẩn** (structured-markdown: `## section` + `| table |`) + **checker code-only** chấm điểm data đầu vào + **normalizer** kéo source về template + **per-bot summary-doc** (deterministic, fix câu "liệt kê/tóm tắt" qua giới hạn topK). Verified end-to-end: upload 7-step (L1→L7) + query 8-step (Q1→Q8), answer **11/11 × 3 lượt stable, 0 HALLU**. Spec: `docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md` · tools: `scripts/check_happy_case.py`, `verify_happy_case_pipeline.py`, `verify_query_flow.py`, `verify_answer_quality.py`.

Engine swaps vs a textbook RAG (deliberate, policy-driven): Qdrant → **pgvector** (1 DB, RLS-capable); Mistral OCR (cloud) → **Kreuzberg** (self-hosted, data-sovereignty); LLM chunk-selector → **rule-based scorer** (deterministic, 0 LLM-cost/doc); sbert/BGE → **Jina v3** (embed+rerank, 1 vendor).

**Latest phase (2026-06-19)** — each measured, with the reason:

| Đổi gì | Trước → Sau | Vì sao (evidence) |
|---|---|---|
| **RLS enforcement** | policies present but inert (superuser) → **request/system role split provisioned (Phase 1+2, committed `edc2d6d`)** | `ragbot_app` NOBYPASSRLS request role + `ragbot_system` BYPASSRLS worker role. 4 cross-tenant workers (outbox/recovery-scan/cache-purge/cost-cap) rerouted to a 2nd no-RLS-hook engine so they aren't fail-closed. Probe PROVEN; 5926 unit pass. Phase 3 (DSN flip) gated. |
| **Embedded workers** | 4 → **5** (`168d00a`) | added **semantic-cache GC** (hourly DELETE expired `semantic_cache` > grace) to stop HNSW bloat. |
| **Cost flags ON** | OFF → **ON** (`a782097`, alembic `phase4_costwin_20260619`) | A/B: `pipeline_multi_query_speculative` **−21% cost / −779ms** + `adaptive_context` **−18% / −619ms**; per-bot opt-out kept. |
| **query_graph god-file** | 3945 → **2828 dòng** | tách ~22 node closure + 9 router decider + helpers (behavior-preserving, 5926 pass). |
| **Schema baseline** | pre-0618 incremental history → **`squash_base_20260618`** | one squashed baseline (replaces archived `_archive_pre_squash_20260618/`); repaired stamp-without-DDL drift. |
| **Stack** | ZeroEntropy zembed-1/zerank-2 (1280) → **Jina v3 (1024)** | single vendor embed+rerank; `semantic_cache` dim 1280→1024 migrated. |

**Giữ NGUYÊN khung-luồng**: 2 LangGraph state machine (ingest U0–U7 + query ~21 node), 4-key identity, HALLU=0 sacred, app không inject/override answer, Strategy+DI. Answer model **gpt-4.1-mini**. Query-transforms temperature **0**. Grounding sync XOR async (không chạy đôi).

> **Clean-rebuild validation 2026-06-19** (wipe DB+cache → seed 3 bots → upload 9 docs → load-test): bots **VERIFIED healthy** — correct grounded answers (spa prices, thông-tư hiệu lực 01/01/2021, xe Landspider/Rovelo) + **HALLU=0** (traps refuse). Full automated coverage scoring was **blocked by OpenAI gpt-4.1-mini TPM rate-limit** under cumulative load (honest: empty/500 answers were infra artifacts, not bot errors). Open gaps logged below + in `STATE_SNAPSHOT.md`.

---

## Table of contents
1. [What Ragbot does](#1-what-ragbot-does)
2. [The adaptive pipeline](#2-the-adaptive-pipeline)
3. [AdapChunk — adaptive chunking](#3-adapchunk--adaptive-chunking)
4. [Identity contract — 4 keys](#4-identity-contract--4-keys)
5. [Determinism + anti-hallucination](#5-determinism--anti-hallucination)
6. [Tenant isolation — RLS request/system split](#6-tenant-isolation--rls-requestsystem-split)
7. [Document ingest — 2-action async pattern](#7-document-ingest--2-action-async-pattern)
8. [Background workers + sacred contracts](#8-background-workers--sacred-contracts)
9. [Default config + reference docs](#9-default-config--reference-docs)

---

## 1. What Ragbot does

Each tenant maps a `workspace + bot slug + channel` (web / Zalo / Messenger / API) to an isolated knowledge base. Two tenants can run bots with the same `bot_id` slug without leaking data — isolation at every layer (schema, repository, middleware, audit log, RLS-ready).

A request flows through **two LangGraph state machines** that communicate **only** via the vector store (`document_chunks`) + event bus (Redis Streams) — no direct calls — so each scales / deploys / fails independently:

```
Ingest graph (async, worker-driven)        Query graph (per request, actual nodes)
──────────────────────────────────        ───────────────────────────────────────
U0   IDENTITY_VALIDATE                     guard_input              (input guardrail)
U0.5 BOT_RESOLVE_4KEY                      check_cache + understand (parallel)
U1   VALIDATE (size + dedup)               condense / router / query_complexity
U2   PARSE (Kreuzberg OCR/xlsx/sheets/md)  rewrite | decompose | adaptive_decompose | speculative
U3   CLEAN (NFC + injection strip)         retrieve / graph_retrieve (dense + BM25 + RRF, MQ fanout)
U4   CHUNK (AdapChunk — §3)                rrf_round_robin → rerank ◀── jina-reranker-v3 (+ cliff)
U5   ENRICH ◀── gpt-4.1-mini               mmr_dedup → neighbor_expand (opt)
U6   VN_SEGMENT (underthesea)              grade  (CRAG 3-state) → rewrite_retry (loop)
U7   EMBED + STORE ◀── jina-embeddings-v3  generate ◀── gpt-4.1-mini → critique_parse (opt)
     (finalize: DRAFT→active/failed)       guard_output (shingle/PII + grounding judge, sync XOR async)
                                           reflect → persist (cache + audit + outbox)
```

Ingest graph: `src/ragbot/application/services/document_service/`. Query graph: `src/ragbot/orchestration/query_graph.py` (+ extracted `orchestration/nodes/*.py`). Setup → first chat: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

> The query column lists the **actual** LangGraph nodes (in `orchestration/nodes/`). Names like `filter_min_score`, `prompt_build`, `citations_extract`, `grounding_check` are observability sub-steps (`request_steps`) inside these nodes.

---

## 2. The adaptive pipeline

An **Adaptive Query Router** gates expensive nodes per intent: a greeting/simple lookup runs a short path; a multi-hop question runs decompose → multi-query fanout → CRAG retry → reflect. Opt-in per-bot (default OFF / Null Object): `graph_retrieve`, `neighbor_expand`, `critique_parse` (Self-RAG), HyDE, speculative streaming. Two measured cost-wins are now ON by default: **multi-query speculative** (fan the MQ paraphrase parallel with understand) + **adaptive context** (prune weak chunks after rerank when top score clears the floor).

### 2.1 Ingest pipeline — U0 / U0.5 + U1 → U7

| Stage | Name | Output | Code path | Notes |
|---|---|---|---|---|
| U0 | IDENTITY_VALIDATE | `request.state.record_tenant_id` UUID | `interfaces/http/...document_schema` | Pydantic + workspace validator; 422 if missing |
| U0.5 | BOT_RESOLVE_4KEY | `BotConfig` + `record_bot_id` | `application/services/bot_registry_service.py` | Redis key `ragbot:bot:{rt}:{ws}:{bot}:{ch}`; miss+no-row → 404 |
| U1 | VALIDATE | accepted job (HTTP 202) | `document_service/ingest_core.py::ingest()` | size guard `MAX_DOCUMENT_CONTENT_CHARS=500_000` + content_hash + source_url dedup |
| U2 | PARSE | structured text | `infrastructure/parser/registry.py` | Kreuzberg OCR / openpyxl / google-sheets / markdown; **reuse `raw_content` from DB** (never refetch) |
| U3 | CLEAN (`_stage_u3_clean`) | normalized text | `document_service/ingest_stages*.py` | NFC + hyphenation + prompt-injection strip |
| U4 | CHUNK (`_stage_u4_chunk`) | List[Chunk] | `shared/chunking/` `smart_chunk()` | AdapChunk adaptive selection — §3; small-to-big parent/child |
| U5 | ENRICH (`_stage_u5_enrich`) | chunks + context prefix | `application/services/contextual_chunk_enrichment.py` | parent-child + Anthropic Contextual Retrieval; `gpt-4.1-mini` |
| U6 | VN_SEGMENT (`_stage_u6_vn_segment`) | tokenized text | `shared/vi_tokenizer.py` | underthesea compound segmentation; null fallback for non-VI |
| U7 | EMBED + STORE (`_stage_u7_embed_store`) | rows in `documents` + `document_chunks` | `infrastructure/embedding/` + `vector/pgvector_store.py` | **jina-embeddings-v3 1024-dim**; children embedded, **parents expand-only (not embedded)**; HNSW + tsvector; `_stage_finalize` flips `DRAFT→active/failed` |

**Default knobs**: `chunk_size=1024` / `parent_chunk_size=1024` / `child_chunk_size=256` / `chunk_overlap=128` · `enrichment_model=gpt-4.1-mini` · embed batched (rate-limit aware) + embedder circuit-breaker + bounded concurrency. *(Note: very large single-table sheets fan out to thousands of child chunks across many embed batches — see open gaps.)*

### 2.2 Query pipeline — actual node flow

```
guard_input → check_cache + understand (parallel)
   ├─(cache hit)──────────────────────────────────────────► persist
   └─► condense_question / router / query_complexity
        → rewrite | decompose | adaptive_decompose | speculative_retrieve
        → retrieve / graph_retrieve ──(no chunks)──► generate (refuse → oos_template)
        → rrf_round_robin → rerank → mmr_dedup → neighbor_expand(opt)
        → grade ──(fail & retries left)──► rewrite_retry → retrieve
        → generate → critique_parse(opt) → guard_output
             ├─(blocked)──► persist
             └─► reflect ──(answer empty & iters left)──► generate
                         └─────────────────────────────► persist → END
```

Per-node behavior (in `orchestration/nodes/`):
- **check_cache** — L1 Redis exact-hash + L2 pgvector semantic @0.97; key versioned by `system_prompt`+`oos_template`+corpus_version so edits bust the cache.
- **understand / router / query_complexity** — heuristic L1 + LLM intent classifier + cascade router; cheap intents skip rewrite/decompose/rerank/reflect (factoid ~70% of traffic).
- **retrieve** — hybrid dense+BM25 fused by RRF; multi-query fanout + `rrf_round_robin` merge across variants; per-intent `retrieve_top_k`.
- **rerank** — jina-reranker-v3 → **`cliff` filter** (gap-cut + absolute floor; keeps full set for aggregation/comparison/multi_hop) → retrieval safety-net re-unions top BM25 chunks the cross-encoder under-ranked.
- **grade** — CRAG batch grader (relevant/irrelevant/ambiguous); compound-intent leniency; smart-skip on high top-score; timeout → reranker order (grounding still enforces HALLU=0).
- **generate** — answers from `<documents>` context only; per-intent token + context-char caps; citations validated against retrieved chunk_ids; refuse short-circuit emits `bots.oos_answer_template` when zero chunks (never an injected string).
- **guard_output** — output guardrail (shingle leak / PII) + grounding judge (`gpt-4.1-mini`, SUPPORTED/NOT vs context; sync OR isolated background lane, never both).
- **reflect** — Self-RAG completeness re-check; bounded by `max_total_graph_iterations`.

### 2.3 Per-turn budget (measured 2026-06-19, cold/no-cache)
- Tier-1 greeting/chitchat (skip retrieve): 1 LLM call, ~700 tok, sub-second.
- Tier-2 factoid/FAQ: 2–3 calls, ~3K tok, **real RAG turn ~5–7s p50**.
- Tier-3 multi-hop/aggregation: 5–8 calls, ~10K tok, **~9–11s p95**.

---

## 3. AdapChunk — adaptive chunking

U4 profiles document structure and picks the best chunking strategy instead of one fixed splitter.

### 3.1 What is LIVE (`shared/chunking/` package)
- **6 strategies** dispatched by `smart_chunk()`: `table_csv` (row-as-chunk + header), `recursive` (heading+block, tables preserved), `hdt` (Hierarchical Document Tree, `structural_path` carried), `semantic` (sentence-similarity drop), `proposition` (atomic facts — legal), `hybrid` (HDT macro + proposition micro).
- **Rule-based document profile** (`analyze_document`) — deterministic counts, no LLM → reproducible.
- **Rule-based selector** (`select_strategy`) + **Layer-5 cross-check** (`apply_cross_check`, default ON) — overrides illogical picks, logs every override.
- **Small-to-big parent/child** — children embedded + retrieved, parents expand-only at answer time.
- **VN-legal heading promotion** (`Chương/Mục/Điều` → markdown) + **structural-anchor retrieval** (breadcrumb `[Chương N > Điều K. <title>]`) — `vn_structural` LIKE-filter matches the chunker's breadcrumb format (R1 fix: was matching 0 chunks).

### 3.2 PARTIAL / flagged (being wired)
- **Block pipeline** (`adapchunk_block_pipeline_enabled`) — flag ON but parser doesn't yet emit a block list → no-ops to text-flatten.
- **Atomic-block protection** (TABLE/FORMULA/IMAGE never cut) + **narrate-then-embed** — implemented, default OFF / not bootstrap-wired.
→ Until then U4 runs flat-text adaptive chunking (the 6 strategies, production-active).

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
- **Query-transform + classification forced to temperature 0** (`DEFAULT_DETERMINISTIC_LLM_PURPOSES`: decompose/rewrite/multi_query/condense/routing/intent/grade/grounding) — makes retrieval reproducible.
- **Layered HALLU defense (each can refuse independently)**: empty-context refuse short-circuit (emits `oos_answer_template`, never an injected string) · CRAG grade gate · cliff floor · per-intent context cap · citation validation vs retrieved chunk_ids · grounding judge (`gpt-4.1-mini`) · Self-RAG critique (opt-in).
- **No app-side numeric verification** — the pipeline checks *grounding* (claim supported by context) but does **not** recompute arithmetic (sacred: no app override). Anti-HALLU 4-types tracked: fabricate / misinterpret / extrapolate / conflate.
- **Grading method**: claim-level Coverage (per-fact LLM judge) + Faithfulness via `scripts/loadtest_graded.py` (DB-verified gold facts + semantic judge). **Latest (2026-06-19 clean rebuild): HALLU=0 verified on tested traps; coverage correct on tested factoid/list/structural flows; full-fleet automated score blocked by OpenAI TPM rate-limit under load (honest).** Tooling note: use serial + bypass-header + VN-number-normalization (eval_gate's concurrent burst trips the 60/window rate-limit → false negatives).

---

## 6. Tenant isolation — RLS request/system split

RLS needs three layers ALL live (`infrastructure/db/session.py`):
1. **Policies** — 20 tables `FORCE ROW LEVEL SECURITY` + 21 `tenant_isolation` policies on `current_setting('app.tenant_id')::uuid`. ✅ present.
2. **NOBYPASSRLS login role** — **`ragbot_app`** provisioned (LOGIN + DML grants, NOBYPASSRLS). ✅ Phase 1.
3. **Per-transaction `SET LOCAL app.tenant_id`** — D3 `after_begin` hook (`create_rls_session_factory`) on every request session + explicit `session_with_tenant`. ✅ wired.

**Request/system split (the reason "you can't just flip the DSN")**: 4 background workers run **cross-tenant** with no single tenant ctx (outbox drain, recovery forensic scan, cache-purge GC, cost-cap aggregate) — under the NOBYPASSRLS request role they'd be fail-closed to 0 rows. So they run on a **second engine** (`create_engine_system` → **`ragbot_system`** BYPASSRLS role, no RLS hook); the request path uses `ragbot_app`. Probe-proven: tenant A → its rows, other tenant → 0, no-ctx → 0; `ragbot_system` → all tenants.

**Status**: provisioned + code-wired + 5926 unit pass, but **inert today** — the app still connects as superuser `ragbot` (`DATABASE_URL_APP` unchanged), so DB-RLS is bypassed and live isolation = mandatory `record_bot_id` app-filter (already solid; RLS is defense-in-depth). **Phase 3 (gated)**: set `DATABASE_URL_APP`→`ragbot_app` + `DATABASE_URL_SYSTEM`→`ragbot_system` + `NULLIF('')` policy hardening + load-test gate. Plan: [`plans/260619-rls-enforcement/plan.md`](plans/260619-rls-enforcement/plan.md).

**Sacred contracts (violating any blocks merge):** HALLU=0 · Domain-neutral (0 brand literal in `src/`) · Zero-hardcode (defaults in `shared/constants/`) · Strategy+Port+Adapter+Registry+Null+DI · App-mindset (`system_prompt` SSoT, never inject/override) · Narrow exception · 4-key identity · No version-ref · **DB content via alembic/admin-audit only (no psql hot-fix)**.

---

## 7. Document ingest — 2-action async pattern

Industry pattern (OpenAI/Anthropic Files, AWS Textract): 100% async, no sync ingest >30s.

**Action 1** — `POST /bots/{bot_id}/{channel_type}/documents` (sync <1s): validate+dedup → fetch content (Google Docs/Sheets export, HTML→text) → INSERT `documents (state='DRAFT', raw_content)` → INSERT outbox `document.uploaded.v1` → commit → **HTTP 202** `{document_id, state:"DRAFT"}`.

**Action 2** — worker (async): outbox publisher → Redis Stream `ragbot:documents:ingest` → consumer (`run_embedded_document_consumer`, binds tenant ctx from payload) → read `raw_content` **from DB** (never refetch source_url — Google Sheets `/edit?gid=` returns an HTML login page) → `ingest()` U1–U7 → progress updates → atomic flip: `chunks_null>0 → failed`, else `active`.

State machine: `DRAFT → enriching → embedding → active` (or `failed`). UI polls `GET …/documents`; chat is guarded until docs are `active`. Stuck DRAFTs are swept by the recovery worker. Detail: [`docs/FLOW_INGEST_DETAIL.md`](docs/FLOW_INGEST_DETAIL.md) · [`docs/UPLOAD_FLOW_SUPPORT_REVIEW.md`](docs/UPLOAD_FLOW_SUPPORT_REVIEW.md).

---

## 8. Background workers + sacred contracts

**5 embedded asyncio workers** (`interfaces/http/embedded_workers.py`, single process, `APP_EMBED_WORKERS_ENABLED`):

| Worker | Job | RLS engine |
|---|---|---|
| `run_embedded_document_consumer` | XREADGROUP `document.uploaded.v1` → ingest pipeline | app (binds tenant ctx per event) |
| `run_embedded_outbox_publisher` | drain outbox → Redis Streams (FOR UPDATE SKIP LOCKED, exactly-once) | **system** (cross-tenant) |
| `run_embedded_recovery_worker` | sweep stuck DRAFT docs → re-emit (cooldown 3600s) | **system** (cross-tenant scan) |
| `run_embedded_cost_cap_alerter` | per-tenant monthly token-cap signal (D11) | **system** (cross-tenant aggregate) |
| `run_embedded_cache_purge` | hourly DELETE expired `semantic_cache` > grace (C1 GC) | **system** (cross-tenant) |

Horizontal-scale mode (separate processes) still available via `python -m ragbot.interfaces.workers.{document_worker,outbox_publisher}`.

**Known gaps (tracked, honest — `STATE_SNAPSHOT.md` 2026-06-19):**
- 🟡 **RLS DB-enforcement gated** (Phase 3 DSN flip pending — §6); app-filter isolation solid meanwhile.
- 🟡 **Oversized-doc ingest** — a single 224KB sheet → 2643 child chunks / 27 embed batches → slow + can OOM the process under concurrent load; needs batch-timeout + load-isolation + surface-loud (currently silent DRAFT).
- 🟡 **`guardrail_rules` not seeded** by the squash/seed path (migration 010f's 12 platform rules) → input-guardrail flow empty until re-seeded.
- 🟡 **OpenAI TPM** — org token/min limit throttles heavy back-to-back load-tests; needs higher tier / throttle / fallback LLM.
- 🟡 **Stale `system_config` embedding defaults** (`embedding_dimension=1536`/`text-embedding-3-small`) vs operative per-bot binding (jina-v3/1024) — benign (binding overrides) but should be cleaned in the seed.

---

## 9. Default config + reference docs

Config **values** are owned by the [DATABASE team](README_DATABASE.md) (seed → `system_config` /
`bots.plan_limits`, Redis-cached); the backend owns only the **contract** ([README_DEV.md](README_DEV.md))
and DevOps owns the **completeness gate** ([README_DEVOPS.md](README_DEVOPS.md)). **Resolve chain (high→low)**: `bots.threshold_overrides` → `bots.<column>` → `bots.plan_limits` → `system_config` → `PLAN_LIMIT_SCHEMA.default` → `constants.DEFAULT_*` (last-resort, being removed in favour of fail-loud). Changing a default = sync 4 places via alembic: `constants` + `bot_limits.py` + `init_system_config.py` + an alembic UPSERT (never psql).

| Domain | Key | Value (operative) |
|---|---|---|
| Cache | `cache_similarity_threshold` | `0.97` |
| Ingest | `MAX_DOCUMENT_CONTENT_CHARS` | `500_000` |
| Chunk | `chunk_size` / `parent` / `child` / `overlap` | `1024 / 1024 / 256 / 128` |
| Embed | provider / model / dim | **`jina` / `jina-embeddings-v3` / `1024`** |
| Rerank | provider / model | **`jina` / `jina-reranker-v3`** |
| Rerank | `rag_rerank_top_n` | `5` |
| Rerank | filter strategy | `cliff` |
| Generate | `generation_temperature` | `0.0` |
| Cost flags | `pipeline_multi_query_speculative_enabled` / `adaptive_context_enabled` | `true` (Phase 4 wins) |
| Loops | `max_total_graph_iterations` (caps CRAG+reflect) | small int |

**Reference docs:**
- **Role playbooks** — [`README_DEV.md`](README_DEV.md) (backend contract) · [`README_DATABASE.md`](README_DATABASE.md) (config values / seed) · [`README_DEVOPS.md`](README_DEVOPS.md) (CI gate / deploy / RLS)
- [`STATE_SNAPSHOT.md`](STATE_SNAPSHOT.md) — current state (READ FIRST in a new session) · [`STATE_SNAPSHOT_HISTORY.md`](STATE_SNAPSHOT_HISTORY.md) — append-only history
- [`CLAUDE.md`](CLAUDE.md) — sacred rules for Claude Code agents
- [`RAGBOT_STEP_PIPELINE.md`](RAGBOT_STEP_PIPELINE.md) — canonical pipeline reference · [`docs/master/`](docs/master/) — architecture sub-pages (A–P)
- [`plans/260619-rls-enforcement/plan.md`](plans/260619-rls-enforcement/plan.md) — RLS role-split + Phase 3 runbook
- [`plans/260619-clean-rebuild-5criteria/plan.md`](plans/260619-clean-rebuild-5criteria/plan.md) — clean-rebuild + 5-criteria load-test
- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) · [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) · [`docs/dev/IDENTITY_RULE_DETAIL.md`](docs/dev/IDENTITY_RULE_DETAIL.md) · [`docs/dev/TROUBLESHOOTING.md`](docs/dev/TROUBLESHOOTING.md)

> Verified against live DB + code 2026-06-19 (alembic head `rls_system_role_grants_20260619`; baseline `squash_base_20260618`). **If a fact here disagrees with code, the code wins.** Git history reset 2026-06-14 (fresh phase); `STATE_SNAPSHOT.md` is the always-updated source of truth.

---

## License
Internal use. See repository owner.
