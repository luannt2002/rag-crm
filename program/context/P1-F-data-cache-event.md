# P1-F · DATA / CACHE / EVENT EXPERT — Context Absorption (Phase 1)

> READ-ONLY understanding pass. Every claim = `file:line` or `commit`. Extends pre-seed
> `P1-C-PRESEED-multitenancy.md` (§2 cache scoping, §5 FK cascade) — not re-derived.
> NO judgement labels yet (that is Phase 2). Alembic head = `0195`
> (`20260609_0195_purge_lmstudio_grounding_grading_openai.py`).

---

## (a) TABLE INVENTORY — 30 tables

ORM models live across 5 files (`models.py` + 4 imported siblings). `document_chunks` +
`semantic_cache` + `system_config` have **NO ORM model** — raw SQL / Table-ref only.

### Group 1 — Tenant / Bot / Identity (5)
| Table | File:line | Notes |
|---|---|---|
| `tenants` | `models.py:67` | PK UUID. soft-delete `deleted_at`. `rate_limit_per_min`, `monthly_token_cap`, `allowed_origins` JSONB |
| `bots` | `models.py:106` | 4-key UNIQUE `uq_bots_record_tenant_workspace_bot_channel` (`:108`). FK→tenants `ON DELETE RESTRICT` (`:130`). soft `is_deleted`+`deleted_at` (`:208/215`). Holds `system_prompt`, `oos_answer_template`, `action_config`, `threshold_overrides`, `record_embedding_model_id` |
| `bot_model_bindings` | `models.py:553` | FK→bots CASCADE (`:566`), FK→ai_models RESTRICT (`:570`). `purpose`/`rank`/`variant` UNIQUE (`:555`) |
| `prompt_templates` | `models.py:601` | per-bot jinja override, `active` flag |
| `bot_token_usage_log` | `models.py:688` | monthly roll-up, 4-key UNIQUE (`:690`) |

### Group 2 — Documents / Chunks (3)
| Table | File:line | Notes |
|---|---|---|
| `documents` | `models.py:275` | `state` VARCHAR default `'active'` (`:299`). soft `deleted_at`. `content_hash` UNIQUE per-bot. `current_step`/`progress_percent`/`chunks_processed` added alembic `0093`. UNIQUE `uq_doc_tool(record_tenant_id, record_bot_id, tool_name)` (`:278`) |
| `document_chunks` | NO ORM — raw SQL in `pgvector_store.py`; Table-ref `models_monitoring.py:56`. `embedding vector(1280)` (alembic `0085` `_NEW_DIM=1280:48`). FK→documents CASCADE (`0013`), FK→bots CASCADE (`0108`). `parent_chunk_id` self-FK for HDT |
| `request_chunk_refs` | `models_monitoring.py:214` | FK→document_chunks CASCADE (`:230`) |

### Group 3 — Conversations / Messages (3)
| Table | File:line | Notes |
|---|---|---|
| `conversations` | `models.py:220` | FK→bots CASCADE (`:232`). UNIQUE `(record_bot_id, connect_id)` |
| `messages` | `models.py:247` | FK→conversations CASCADE (`:256`). soft `deleted_at` |
| `message_feedback` | `message_feedback_model.py:50` | thumbs verdict |

### Group 4 — Cache (1 DB table + 3 Redis namespaces)
| Cache | Location | Key composition |
|---|---|---|
| `semantic_cache` (L2 pgvector) | NO ORM; `semantic_cache.py` raw SQL; alembic `0014`; dim-fix `0105` → `vector(1280)` | row scoped `(record_bot_id, record_tenant_id, bot_version, corpus_version, query_hash)`. **NO FK to bots** (alembic `0014` declares none) |
| L1 embedding cache (Redis) | `shared/embedding_cache.py:22` | `ragbot:emb:{model}:{dim}:{sha256(text)[:16]}` TTL 30d. Content-keyed |
| L1 understand_query (Redis) | `understand_query_cache.py:60` | `ragbot:uq:v{prompt_version}:{record_bot_id}:{sha256(query[:300])[:16]}` |
| L1 corpus_version (Redis) | `corpus_version_service.py:60` | `{prefix}{record_tenant_id}:{record_bot_id}` TTL 300s |
| L1 bot registry (Redis) | `BotRegistryService` | `ragbot:bot:{tid}:{ws}:{bot}:{channel}` |
| provider prompt-cache | Anthropic native (cache_control) | n/a — provider-side |

### Group 5 — Events / Outbox / Idempotency (3)
| Table | File:line | Notes |
|---|---|---|
| `outbox` | `models.py:388` | transactional outbox. `status`/`retry_count`/`redis_entry_id`. `FOR UPDATE SKIP LOCKED` per-row tx in `outbox_publisher.py` |
| `ingest_idempotency_keys` | `models.py:319` | UNIQUE `(record_tenant_id, workspace_id, idempotency_key)` (`:333`). `request_hash` SHA256 + `expires_at` (24h, nightly sweep) |
| `jobs` | `models.py:365` | generic job rows |

### Group 6 — Config / AI (4)
`ai_providers` `models.py:456` · `ai_models` `models.py:500` (FK→providers CASCADE `:509`, `embedding_dimension`) · `tenant_model_policy` `models_monitoring.py:289` · `model_capabilities` `models_monitoring.py:244` · `system_config` (NO ORM — Redis-cached KV) · `quotas` `models.py:424`.

### Group 7 — Audit / Observability (6)
`audit_log` `models.py:637` (tamper hash-chain `row_hash` alembic `010g`) · `request_logs` `models_monitoring.py:78` · `request_steps` `models_monitoring.py:161` · `guardrail_events` `models_guardrail.py:35` · `guardrail_rules` `models_guardrail.py:75` · `model_invocations` `models_invocation.py:73` · `prompt_versions` `models_invocation.py:44` · `refuse_suggestions` `refuse_suggestion_model.py:48`.

---

## (b) INVALIDATION MATRIX + HOLES

Two distinct invalidation mechanisms:
- **Embedded-version-key** (passive): `bot_version` = `hash(system_prompt + oos_answer_template)`
  (`query_graph.py:974`), `corpus_version` = `hash(bot_id, MAX(GREATEST(updated_at, deleted_at)))`
  (`corpus_version_service.py:69/224`). Computed at READ time → a change makes old rows
  *unreachable* (orphaned until TTL), never served stale. NO explicit purge needed.
- **Active purge**: `DELETE FROM semantic_cache WHERE record_bot_id` in document_service.

| Event | semantic_cache (L2) | corpus_version Redis | bot registry Redis | uq cache | embedding cache | chunks/docs | code file:line |
|---|---|---|---|---|---|---|---|
| **update system_prompt** | passive bust (bot_version flips) | — | invalidate (`:209`) | **NO bust** ← HOLE-1 | — | — | `bot_management_service.py:209` |
| **update oos_answer_template** | passive bust | — | invalidate | — | — | — | same |
| **change embedding model binding** | **NO bust** ← HOLE-2 | **NO bump** (corpus_version keyed on doc updated_at, not binding) | invalidate | — | — | no re-embed guard | pre-seed §5; `bot_management_service.py:170-215` |
| **re-ingest / replace doc (URL)** | purge (`DELETE … record_bot_id`) | TTL eventual (no explicit `invalidate()`) | — | — | content-keyed (safe) | soft-del old + new chunks | `document_service.py:3996` |
| **delete one document** | purge | corpus_version bumps via `GREATEST(deleted_at)` (`:224`) | — | — | — | hard-del chunks + soft-del doc | `document_service.py:4080-4092` |
| **delete all docs of bot** | purge | bumps | — | — | — | hard-del chunks + docs | `document_service.py:4026-4039` |
| **soft-delete bot** | **NO purge** ← HOLE-3 (pre-seed §2) | **NO purge** ← HOLE-4 | invalidate (`:256`) | **NO bust** ← HOLE-1 | — | **NOT cascaded** (soft-del = `is_deleted=true`, FK CASCADE never fires) ← HOLE-5 | `bot_management_service.py:256` |
| **soft-delete tenant** | **NO purge** ← HOLE-6 | **NO purge** | not invalidated per-bot ← HOLE-7 | — | — | **NOT cascaded** (`soft_delete_tenant`; bots FK = `ON DELETE RESTRICT` → hard delete would even be BLOCKED) ← HOLE-8 | `tenant_repository.py:360`, `admin_tenants.py:330` |

### HOLES — ranked

1. **HOLE-5 / HOLE-3 (worst)** — bot soft-delete (`is_deleted=true`, `deleted_at`) purges
   **nothing downstream**: semantic_cache rows orphan (no FK to bots — alembic `0014` declares
   none, so even hard delete would orphan), corpus_version Redis key lingers (TTL 300s),
   conversations/messages/documents/chunks stay in DB forever (FK CASCADE on `bots.id` only
   fires on **hard** row delete, which never happens). A re-created bot with the same 4-key
   could read a previous incarnation's cache rows (same `record_bot_id`? — no, UUID rotates;
   but cross-version corpus stale if UUID reused). Evidence: `bot_management_service.py:226-269`
   does registry-invalidate + outbox only.

2. **HOLE-8 / HOLE-6** — tenant soft-delete leaves all child data + cache live; no orchestrated
   cascade job. `bots → tenants` FK is `ON DELETE RESTRICT` (`models.py:130`) so a hard delete
   is structurally impossible while any bot exists. Plan `260608-multitenant-hardening/plan.md:72`
   flags "Deletion cascade → verify 0 orphan" as an open task (→ D4/lifecycle).

3. **HOLE-2** — changing a bot's embedding-model binding neither purges semantic_cache nor bumps
   corpus_version (it is keyed on `documents.updated_at`, not the binding). Existing 1280-dim
   chunks stay; new queries embed with the new model → cosine garbage. Only a detection-only
   Prometheus counter exists (`query_graph.py:702-727`, never raises — pre-seed §5). → D10.

4. **HOLE-1** — `understand_query_cache` (`ragbot:uq:...`) is bumped only by its embedded
   `prompt_version` integer (`understand_query_cache.py:64`), NOT by per-bot `system_prompt`
   change. Lower severity: it caches intent classification, not the answer.

5. **HOLE-4 / HOLE-7** — corpus_version + per-bot Redis keys are never actively deleted on
   bot/tenant delete; they self-expire after TTL 300s. Low blast radius (TTL bounded) but the
   `invalidate()` method (`corpus_version_service.py:160`) exists and is **never called** from
   the ingest/delete paths — only the passive TTL fallback runs.

**NOT a hole (verified correct):**
- system_prompt / oos change → passive bust works (`_compute_bot_cache_version`
  `query_graph.py:974`, hashed into key at every read `:1876`).
- single-doc delete → corpus_version bumps because `_fetch_marker` uses
  `MAX(GREATEST(updated_at, COALESCE(deleted_at, updated_at)))` (`corpus_version_service.py:224`)
  AND the row is purged directly — so stale-answer-from-deleted-doc is prevented two ways.
- embedding cache is content-hash keyed (`embedding_cache.py:22`) — immune to bot/corpus/prompt.
- **provider prompt-cache (tier 3)** needs NO app invalidation: `apply_anthropic_cache_control`
  (`shared/anthropic_cache.py:19`) stamps `cache_control: ephemeral` in the answer path
  (`dynamic_litellm_router.py:547` non-stream + `:697` stream), ingest CR enrichment
  (`contextual_chunk_enrichment.py:142`), structured-output (`structured_output_helper.py:444`).
  Key = exact prompt-prefix content at provider side → any system_prompt edit self-invalidates;
  no-op for non-Anthropic providers.
- semantic_cache rows carry `expires_at` honored at read (`semantic_cache.py:424`) + per-key
  stampede lock `SET NX EX DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S` (`semantic_cache.py:237`).

---

## (b2) OUTBOX + STREAMS — delivery-semantics verdict

**Verdict: NOT exactly-once.** Stream delivery = at-least-once; handler execution degrades to
**at-most-once** the moment the dedup key is set. Chain of evidence:

1. **Publisher** — per-row tx `FOR UPDATE SKIP LOCKED`, publish + `mark_processed_in_session`
   + commit in ONE tx (`outbox_publisher.py:104-146`). Crash between XADD and commit → row
   stays pending → republished on restart = duplicate stream entry (intentional, documented
   `outbox_publisher.py:5-17`). XADD failure reified as `BusError` → rollback, row pending
   (`redis_streams_bus.py:112-144`) — Redis blip cannot mark a row processed (`e467e1e`).
2. **Dedup ledger** — Redis `ragbot:outbox:dedup:{outbox-row-UUID}` via `SET NX EX 86400`
   (`redis_streams_bus.py:33` + `:196-208`; TTL `_07_llm_sampling_defaults.py:114`). Scope =
   **global by outbox UUID, NOT per-bot** — correct, the UUID is globally unique; tenant scoping
   is carried inside the payload (4-key, `ingest_document.py:109-123` per pre-seed §3).
3. **THE GAP — dedup-before-success**: `SET NX` fires BEFORE the handler runs
   (`redis_streams_bus.py:196-208` then `:215`). Handler raises → no XACK (`:217-220`) →
   message sits in PEL → `recover_pending_messages` XCLAIMs after 30s idle (`:323-377`) →
   redelivery hits the already-set dedup key → **skip + XACK = message silently dropped**
   (`:202-208`). One transient handler exception consumes the only delivery.
4. **Dead-letter asymmetry**: publisher side has real DLQ (`mark_dlq` after 5 retries,
   `outbox_publisher.py:189-191`); consumer side "DLQ" = log + XACK at `times_delivered>5`
   (`redis_streams_bus.py:345-359`) — no persistence, no replay queue.
5. **Compensating sweeper** (partial): `document_recovery_worker.py` re-emits canonical
   `document.uploaded.v1` for docs `state='DRAFT'` older than 900s (constants
   `_20_cag_mode_cache_augmented_gen.py:225/230`, cadence 300s; shipped `606f8f4` + cast fix
   `eccb330`). Covers ONLY the ingest subject — registry-changed / feedback subjects have no
   equivalent.
6. **No transactional inbox**: dedup mark (Redis) and handler side-effects (Postgres) are two
   non-atomic systems; the 24h dedup TTL (Q5) + dedup-before-success (point 3) are both
   consequences of not having an inbox table in the same DB tx as the handler.

---

## (c) GIT SCHEMA-DRIFT TIMELINE (selected, head 0195)

| Alembic | Event |
|---|---|
| `0013` | document_chunks + pgvector (FK→documents CASCADE) |
| `0014` | semantic_cache table created **without FK to bots** (root of orphan holes) |
| `0048` | documents content_hash UNIQUE per-bot (dedup) |
| `0050` | embedding dim `1024 → 1536` |
| `0062` | workspace_id 4-key identity stamped on 16 tables |
| `0069` | RLS enable tenant isolation (policy exists; hook not wired — pre-seed §1) |
| `0085` | ZeroEntropy zembed-1: column `1024 → 2560` **then corrected to 1280** in-file (`_NEW_DIM=1280:48`, HNSW 2000-dim limit). Per-bot embed dim now globally fixed 1280 |
| `0093` | documents progress cols: `current_step`/`progress_percent`/`chunks_processed` (DRAFT-stuck observability) |
| `0100` | bot token quota columns |
| `0105` | fix semantic_cache.query_embedding `1024 → 1280` (was raising DataError every INSERT) |
| `0107c` | documents.record_bot_id → bots CASCADE |
| `0108` | document_chunks.record_bot_id → bots CASCADE + RLS JOIN policy |
| `010g` | audit_log tamper hash-chain (`row_hash`) |
| `0141` | workspace-aware dual-GUC RLS |
| `0150` | action_config + slot-state columns |
| `0162` | metadata_extraction_config per-bot |
| `0187` | re-assert canonical RLS policies |
| `0195` | purge LMStudio → grounding=nano, grading=mini (head) |

Embedding-dim drift chain: `1024 (0013) → 1536 (0050) → 2560→1280 (0085) → cache catch-up 1280 (0105)`.

### Outbox / Streams code evolution (git, `--follow`)

| Commit | Event |
|---|---|
| `0af7bab` → `be04dc4` | streams hardened: XADD MAXLEN bound (unbounded growth fix) |
| `730d3d6` | publisher narrow-except RedisError/OSError/Timeout → retry+DLQ |
| `ba7beba` | **exactly-once refactor** (Agent N 2026-05-16): batch-mark → per-row `FOR UPDATE SKIP LOCKED` |
| `e467e1e` | XADD durability verify (`BusError` on falsy entry_id) + `redis_entry_id` forensic join key |
| `606f8f4` / `eccb330` | document recovery worker (DRAFT sweeper) + bytea→jsonb cast fix |
| `1fd50c8` | Bug #11 NOGROUP auto-recover (FLUSHDB / no-persistence restart self-heal) |
| `1942677` | consumer Semaphore concurrency (=5, `_07:111`) — still global, not per-tenant |

---

## (d) vs SOTA DATA/CACHE 2026 — HAS / LACKS (objective)

### HAS (matches industry practice)
1. Transactional outbox + `SKIP LOCKED` per-row publisher + DLQ + forensic `redis_entry_id` join key (`outbox_publisher.py:104-146`).
2. Consumer msg-id dedup ledger (`redis_streams_bus.py:196-208`) + XCLAIM crash recovery (`:323-377`) + NOGROUP self-heal (`:222-248`).
3. **Version-stamped cache keys** (bot_version + corpus_version embedded in key, `query_graph.py:974` + `corpus_version_service.py:69`) — purge-free passive invalidation, the 2026-recommended pattern over explicit purge fan-out.
4. 3-tier cache: L1 content-keyed embedding Redis (30d TTL, `embedding_cache.py:22`) · L2 pgvector semantic 0.97 cosine, 4-key scoped + RLS (pre-seed §2) · provider prompt-cache wired on answer path (`dynamic_litellm_router.py:547/697`).
5. Cache-stampede protection: per-key Redis `SET NX EX` mutex (`semantic_cache.py:211-280`).
6. API-level idempotency keys table w/ request-hash + 24h expiry (`models.py:319-333`).
7. Audit hash-chain (`010g`), FK CASCADE chains documents→chunks→refs, HNSW index.
8. Stuck-ingest compensating sweeper (`document_recovery_worker.py`, 300s cadence).

### LACKS (gap vs SOTA)
1. **Exactly-once**: dedup-before-success = at-most-once handler ((b2).3). SOTA = transactional **inbox** table committed atomically with handler side-effects, dedup mark only on success.
2. **Lifecycle GC**: no BotLifecycleService / tenant-offboarding saga — soft-delete purges nothing (HOLE-3/5/6/8); SOTA = orchestrated cascade job + storage TTL reaper.
3. **Embedding versioning**: no `embedding_model/version` column on chunks; binding swap = silent cosine garbage (HOLE-2). SOTA = version column + dual-write re-embed migration + read-side guard.
4. **Event-driven cache freshness**: `corpus_version.invalidate()` dead code → 300s TTL lag instead of post-ingest bump; SOTA wires invalidation into the same outbox event.
5. **Per-tenant fairness**: one global stream + one shared Semaphore(5) (`redis_streams_bus.py:60/170`); SOTA = keyed partitions / per-tenant streams + weighted fair scheduler (→ D8).
6. **Hit-rate levers unexploited**: cosine threshold fixed 0.97 (`_04_jwt_auth.py:144`) with no per-bot tuning surface in matrix, no negative-result caching, no warm-up; charter target cache hit ≥ 30% has no per-tenant measurement (counters exist but degrade silently, `semantic_cache.py:74-86`).
7. **Consumer-side DLQ**: dead-letter = log + ACK only ((b2).4); SOTA persists to a replayable parking-lot stream.
8. **semantic_cache orphan hygiene**: no FK to bots (`0014`) + no background sweep of expired/orphan rows — only read-time `expires_at` filter (`semantic_cache.py:424`).

---

## (d-bis) RELATED PLANS — status

| Plan | Relevance | Status |
|---|---|---|
| `plans/260608-multitenant-hardening/plan.md` | `:61` semantic_cache cross-bot check; `:72` deletion-cascade "verify 0 orphan"; `:92` lifecycle task #4 | OPEN — cascade not implemented (confirms HOLE-5/8) |
| `plans/260610-ga-hardening/` | GA blockers | active this branch |
| `docs/V2_MIGRATION_BUG_LESSONS.md` | semantic_cache dim hardcode (0105), binding purpose drift | lessons captured |
| `docs/PROJECT_FLOWS.md:741` | dev runs postgres superuser → RLS inert | known |

---

## (e) 10 OPEN QUESTIONS

1. **Bot/tenant lifecycle**: should soft-delete trigger an orchestrated purge (semantic_cache +
   corpus_version Redis + cascade conversations/chunks), or a `BotLifecycleService`? Currently
   nothing cleaks at read (UUID rotates) but storage grows unbounded. (HOLE-3/5/6/8 → D4)
2. **semantic_cache has no FK to bots** (`0014`) — add `ON DELETE CASCADE` so a future hard
   delete self-cleans, or keep raw-SQL table and rely on app purge? Trade-off vs pgvector index.
3. **Embedding-model change guard** (HOLE-2 → D10): on binding change with existing chunks,
   should we block / force re-embed / bump corpus_version? Today only a no-op Prometheus counter.
4. **Stuck-document blind spot**: the UPSERT path INSERTs `state='active'` synchronously
   (`document_service.py:1624-1629`) BEFORE async embed; worker crash before the final flip
   (`:3682`) leaves an `active` doc with zero/partial chunks. The recovery sweeper exists but
   scans ONLY `state='DRAFT'` (`document_recovery_worker.py` design block) — `active`-with-0-chunks
   rows are invisible to it. Unify on DRAFT-first state machine, or extend the sweeper predicate?
5. **Dedup-before-success** ((b2).3): a transient handler exception consumes the message's only
   delivery (redelivery → dedup skip → ACK). Move the dedup mark to post-success, or adopt a
   transactional inbox in Postgres? Plus: dedup TTL 86400s (`_07:114`) means a replay after 24h
   re-executes the handler — right window vs outbox row lifetime?
6. **Redis Streams are global per-subject** (`ragbot:{subject}`, `redis_streams_bus.py:60`), one
   `Semaphore(5)` shared across all tenants (`:170`) → noisy-neighbor on ingest (pre-seed §3 →
   D8). Per-tenant stream or fair-scheduler needed?
7. **corpus_version `invalidate()` is dead code** (`corpus_version_service.py:160`, 0 callsites in
   ingest/delete). Wire it post-ingest for sub-TTL freshness, or accept the 300s lag?
8. **understand_query_cache** not bumped on per-bot config change (HOLE-1). Add `record_bot_id`
   config-hash to the key, or accept (intent rarely flips on prompt change)?
9. **`build_response_cache_key`** (`cache_port.py:103`) is scoped-correct but **dead** (0
   callsites, pre-seed §2). Remove or adopt as the canonical key builder?
10. **Tenant FK `ON DELETE RESTRICT`** (`models.py:130`) makes hard tenant delete structurally
    impossible while bots exist — is RESTRICT intended (force explicit cascade job) or should it
    be CASCADE with a guarded admin flow?
