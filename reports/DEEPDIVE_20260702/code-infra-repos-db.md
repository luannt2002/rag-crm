# Deep-dive: `src/ragbot/infrastructure/repositories/` + `src/ragbot/infrastructure/db/`

Date: 2026-07-02 · Scope: 24 source files (~5,600 LOC) — every file read line-by-line.
Evidence rule: every claim carries `file:line`; runtime claims verified against the live dev DB (`ragbot_v2_dev`) via psql where marked **[DB-verified]**. Labels: **FACT** (code/DB evidence) vs **HYPOTHESIS** (mechanism proven, runtime impact not yet measured).

---

## Part 1 — File-by-file: what it does + pipeline connection

### `db/` package

| File | Purpose | Pipeline connection |
|---|---|---|
| `db/__init__.py` (13 ln) | Re-exports engine/session factory + `Base`/`mapper_registry`. | Import surface for bootstrap + alembic. |
| `db/engine.py` (219 ln) | Three engine builders: `create_engine` (admin DSN, alembic/ops), `create_engine_app` (runtime DSN `DATABASE_URL_APP`, refuses to build unless the superuser escape env `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` is set — engine.py:60-92), `create_engine_system` (BYPASSRLS worker DSN, falls back to admin — engine.py:95-123). `session_with_tenant()` (engine.py:134-194) opens a session, validates the tenant UUID (`_assert_uuid_str`, engine.py:33-41), issues `SET LOCAL app.tenant_id`, optionally `SET LOCAL app.workspace_id`, and `SET LOCAL statement_timeout = DEFAULT_STATEMENT_TIMEOUT_MS` (30 s — constants `_04_jwt_auth.py:177`). | Every explicitly-tenant-bound DB access (feedback, stats bulk insert, ingest phases, pgvector store) goes through `session_with_tenant`. |
| `db/session.py` (246 ln) | RLS layer-3 binder: `after_begin` hook that reads `tenant_id_ctx` / `workspace_id_ctx` and issues `SET LOCAL app.tenant_id` / `app.workspace_id` on every transaction (session.py:149-171). `create_rls_session_factory` wraps `create_session_factory` + attaches the hook (session.py:221-235). Declares `RUNTIME_DB_ROLE="ragbot_app"`, `SYSTEM_DB_ROLE="ragbot_system"`, GUC names. | Bootstrap wires **all** repos through this factory (`bootstrap.py:185-186`), so any repo session inside a bound HTTP request / worker turn carries the tenant GUC. |
| `db/uow.py` (148 ln) | `SqlAlchemyUnitOfWork` — refuses to open without a bound `tenant_id_ctx` (uow.py:44-49), issues `SET LOCAL app.tenant_id` (uow.py:54), buffers outbox rows and flushes them in `commit()` (uow.py:71-78). `add_outbox` / `add_outbox_raw` build `OutboxModel` rows with `WORKSPACE_SYSTEM_SLUG` fallback (uow.py:93, 123). | Transactional boundary for bot-management writes + cross-replica cache-bust events (`bot_management_service.py:365`). |
| `db/models.py` (806 ln) | Core ORM: tenants, workspaces, bots (with `plan_limits`, `threshold_overrides`, `action_config`, `metadata_extraction_config`, token-quota columns), conversations, messages, documents, ingest idempotency keys, jobs, outbox, quotas, ai_providers, ai_models, bot_model_bindings, prompt_templates, audit_log (hash chain col), bot_token_usage_log. Imports monitoring/guardrail/invocation models for metadata registration (models.py:765-778). | Single ORM source of truth for alembic autogen + every repo. |
| `db/models_monitoring.py` (354 ln) | `request_logs` (17-field per-request row, opt-in plaintext columns models_monitoring.py:115-116), `request_steps`, `request_chunk_refs` (relational split of dropped JSONB — models_monitoring.py:207-245), `model_capabilities`, `tenant_model_policy`. Also registers a **Table shim for `document_chunks`** (models_monitoring.py:56-69) so `RequestChunkRefModel`'s FK resolves — document_chunks itself is raw-SQL-managed by pgvector_store. | Observability spine: every chat turn writes 1 request_log + N request_steps + N chunk refs. |
| `db/models_guardrail.py` (123 ln) | `guardrail_events` (one row per rule hit, hash-only privacy) + `guardrail_rules` (DB-driven moderation rules, `record_tenant_id IS NULL` = platform default). | Guardrail input/output nodes persist hits; rule loader reads rules. |
| `db/models_invocation.py` (129 ln) | `prompt_versions` (append-only versioned prompts) + `model_invocations` (one row per LLM/embed/rerank call with `feature_name` cost rollup key). | Cost audit + invariant #2 full-chain audit. |
| `db/message_feedback_model.py` (92 ln) | `message_feedback` — one thumbs verdict per assistant message, 2-value SQL Enum from constants. | Feedback route → `MessageFeedbackRepository`. |
| `db/refuse_suggestion_model.py` (73 ln) | **Scaffold, explicitly not wired** (docstring refuse_suggestion_model.py:1-20): `refuse_suggestions` aggregate table; admin endpoint queries `request_logs` directly instead. | None today (alembic metadata only). |

### `repositories/` package

| File | Purpose | Tenant scoping style |
|---|---|---|
| `_base.py` (37 ln) | `TenantScopedRepository`: holds session factory, `_ensure_tenant` raises `TenantIsolationViolation` on None. | Enforces *presence* of the arg only — actual filtering is per-method. |
| `bot_repository.py` (326 ln) | Bots CRUD; `_row_to_config` maps ORM → `BotConfig` DTO with drift-tolerant Pydantic validation (bot_repository.py:42-117). `find_by_4key` (167-189) is the canonical external resolve; `find_by_3key_unique` refuses ambiguous matches (191-221). `update_bot` filters fields through a frozen allowlist (270-275). | Explicit `record_tenant_id` WHERE on 4-key/3-key; `None` = platform-admin bypass on `list_active`/`get_by_id`/`update_bot`/`soft_delete`. |
| `conversation_repository.py` (302 ln) | Conversation aggregate get-or-create/save; single LEFT OUTER JOIN fetch capped at `MAX_HISTORY_LIMIT_REQUEST` (=20) kills the old N+1 (223-272). | Explicit tenant WHERE everywhere; save() cross-checks `conversation.record_tenant_id != tid` (157-160). |
| `message_repository.py` (163 ln) | GDPR-grade direct message CRUD: create (inherits workspace slug from parent conversation, 55-64), get_content, soft_delete_content, soft_delete_conversation (content nulled + `deleted_at`). | Explicit tenant WHERE on every method. |
| `document_repository.py` (384 ln) | Documents CRUD + keyset `list_by_bot`; `get_by_source_url` raises on ambiguity (165-197); `find_chunks_by_document_ids` / `find_chunks_by_ids` (247-381) are the **stats-index → grounded-context bridge** returning vector-shape dicts with `score: 1.0` sentinel. | Documents: explicit tenant WHERE. Chunk fetchers: `record_bot_id` only (documented as sufficient — internal unique key). |
| `stats_index_repository.py` (662 ln) | The B-AGG structured-stats layer over `document_service_index`: `bulk_insert` (delete-before-reingest contract), `query_by_price_range`, `top_by_price` (superlative), `count_by_price_range`, `count_by_name_keyword`, `list_all_entities`, `query_by_name_keyword` (unaccent + synonym OR-expansion + digit-separator notation folding + reverse/token fallback). Every read JOINs live parent docs (`_DOC_LIVE_JOIN`, 57-58) so deleted catalogs can't resurface. | `bulk_insert` via `session_with_tenant`; **all reads + `delete_by_document` via plain sessions filtered by `record_bot_id` only** (see findings F5, F7). |
| `request_log_repository.py` (648 ln) | request_logs lifecycle (`create_request_log` → `finalize_request_log` incl. `monitoring_log` durable mirror INSERT, 173-192), chunk-ref extraction (195-240), step writes (per-step + `add_steps_batch` one-round-trip, 334-391), feedback attach, and 5 analytics queries (overview / by-model / top-questions / step-breakdown). | Explicit tenant on every method (`_ensure`); finalize re-checks row ownership (132-135). |
| `ai_config_repository.py` (882 ln) | Providers/models CRUD (batch `get_models_by_ids`/`get_providers_by_ids` N+1 fixes, 257-292), bindings CRUD (global-NULL-tenant fallback semantics, 335-414; TOCTOU-safe UPDATE...RETURNING, 496-517), prompt templates, `ai_keys` raw-SQL CRUD (647-754), audit writes with optional per-bot PII redaction (757-842). | Bindings/prompts/audit: explicit tenant. Providers/models/keys: platform-global (no tenant — correct, shared stack). |
| `outbox_repository.py` (213 ln) | Outbox poller: legacy `poll_unprocessed` (test-fake fallback only — outbox_publisher.py:219-222), exactly-once `poll_one_for_update` (FOR UPDATE SKIP LOCKED, caller owns tx, 60-92), per-tx mutators. | Runs on the **system** (BYPASSRLS) session factory (`bootstrap.py:485`) — correct for a cross-tenant publisher. |
| `quota_repository.py` (87 ln) | Tenant token/cost quota: `get` (auto-creates row), `increment_usage`, `check_within_budget`. | Explicit tenant WHERE. |
| `job_repository.py` (113 ln) | Async job rows: create/update_status/get. `update_status` allows `record_tenant_id=None` for fail-before-lookup paths (61-63). | Explicit tenant WHERE (except the documented None path). |
| `guardrail_repository.py` (107 ln) | Insert guardrail events (legacy/prefixed key tolerance, 40-44; inherits workspace from parent request_log, 51-59), list by message. | Rows carry tenant; `list_by_message` filters by `message_id` **only** (see F14). |
| `audit_repository.py` (209 ln) | Read-only audit analytics: `get_audit_overview` (request stats + doc/chunk stats + chunking-strategy distribution), `get_query_detail` (keyset pagination on `started_at`). | Explicit tenant on request_logs AND raw-SQL doc/chunk queries (P17 fix, 68-92). |
| `audit_chain_writer.py` (120 ln) | `insert_audit_row` — tamper-evident hash chain: locks per-tenant tail row `FOR UPDATE`, computes `sha256(prev ‖ fields)`, pre-stamps `created_at` (38-117). | All audit_log writes route through it (`tenant_policy_repository.py:203`, `ai_config_repository.py:807`). |
| `tenant_repository.py` (399 ln) | Tenant policy read/patch + super-admin CRUD. Slug lives in `config['slug']` JSONB with SELECT-before-INSERT duplicate check (193-202); soft-delete gated on zero active bots (360-392). | Session-injected (caller owns tx); all queries keyed on tenant UUID PK. |
| `tenant_policy_repository.py` (307 ln) | `tenant_model_policy` + `model_capabilities` upsert/read with ratio-sum invariant + capability cross-validation (138-160); audits via hash chain. | Explicit tenant WHERE. |
| `workspace_repository.py` (101 ln) | Workspace entity lookup/list/`ensure` (get-or-create with IntegrityError race recovery, 85-93). | Explicit tenant WHERE. |
| `message_feedback_repository.py` (135 ln) | Thumbs verdict insert + per-bot aggregate (FILTER-count one-round-trip, 110-127). | `session_with_tenant` on both paths — the only repo besides stats bulk_insert that explicitly binds the GUC per call. |
| `token_ledger_analytics_repository.py` (96 ln) | `usage_timeseries` — `date_trunc` GROUP BY over `token_ledger` with whitelisted bucket/breakdown interpolation (23-29, 79-92); `all_tenants` flag drops tenant filter (route-gated RBAC 100). | Explicit tenant WHERE (caller-supplied); table itself has **no RLS** [DB-verified]. |
| `history_reconcile.py` (268 ln) | MT-1 fix: merges the two history stores (`chat_histories` HTTP/SSE + `messages` worker) at read time — global time sort, adjacent dedup, most-recent-N (77-111). Best-effort: any `SQLAlchemyError` → empty history (164-167). | Called per turn by chat_stream (`chat_stream.py:162`), test_chat, chat_worker. Scoped by `record_bot_id + connect_id` only. |
| `language_pack_repository.py` (74 ln) | Read-only `language_packs` (code, prompt_key) lookup. Platform-wide, tenant-agnostic by design. | None (intentional). |
| `__init__.py` (33 ln) | Re-exports 9 of the ~17 repo classes (guardrail/message/feedback/workspace/stats/history/token-ledger/audit-chain not exported — imported by full path in bootstrap). | — |

**Bootstrap wiring (context):** all repos get the RLS-hooked app factory (`bootstrap.py:185-186, 463-522`); only `SqlAlchemyOutboxRepository` gets the system factory (`bootstrap.py:485`).

---

## Part 2 — Findings

### F1 · CRITICAL — All 5 `ai_keys` methods query a schema that does not exist (`ragbot.ai_keys`)

**FACT [DB-verified].** `ai_config_repository.py:664, 689, 709, 731, 747` hardcode `ragbot.ai_keys` in raw SQL. Live DB: `SELECT count(*) FROM pg_namespace WHERE nspname='ragbot'` → **0**; `to_regclass('ragbot.ai_keys')` → **NULL**; the table is `public.ai_keys` (squashed_baseline.sql:39). `models.py:53` shows the leftover: `RAGBOT_SCHEMA = "public"` "kept for backward compat" — the raw-SQL strings were never migrated when the dedicated schema was collapsed into `public`.
**Wired, not dead**: `ai_config_service.py:411-508` → `admin_ai.py:209-214` expose these as admin key-rotation/health endpoints.
**Failure scenario**: admin calls `POST /admin/ai/keys` (insert_key) → asyncpg `UndefinedTableError: relation "ragbot.ai_keys" does not exist` → 500. Consistent with live evidence: `public.ai_keys` has **0 rows** — no key has ever been persisted through this path. The whole DB-backed key-pool feature (V16 `DBBackedApiKeyPoolFactory`) is built-but-broken at the repo layer.
**Fix**: drop the `ragbot.` prefix (or interpolate a schema constant pinned to `RAGBOT_SCHEMA`).

### F2 · CRITICAL (deployment posture) — RLS is currently dead: runtime runs as superuser via escape hatch

**FACT [DB-verified].** `.env:110` sets `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` and defines **no** `DATABASE_URL_APP`/`DATABASE_URL_SYSTEM` (.env has only `DATABASE_URL`/`DATABASE_URL_SYNC`). `create_engine_app` therefore falls back to the admin DSN (engine.py:67-81), which is the `postgres` role — `pg_roles`: `postgres | rolsuper=t | rolbypassrls=t`. A `ragbot_app` role exists (`rolbypassrls=f`) but is unused. Consequence: the 21+ `tenant_isolation` policies (squashed_baseline.sql:1463-1511 + 20260626 migration) enforce **nothing** today; tenant isolation rests 100 % on the explicit `record_tenant_id`/`record_bot_id` WHERE clauses catalogued below. All the careful layer-3 plumbing (session.py hook, uow guard, session_with_tenant) is live but inert.
This is the single most important lens for the whole scope: every repo below that "relies on RLS" is actually relying on nothing.

### F3 · HIGH — Price-range "any" filter matches rows where **neither** price is in range (cross-column OR/AND bug)

**FACT (logic).** `stats_index_repository.py:239-248` (`query_by_price_range`) and 381-391 (`count_by_price_range`): for `price_column="any"` (the **default**, line 197) with both bounds set, the WHERE is
`(pp >= min OR ps >= min) AND (pp <= max OR ps <= max)` — evaluated across *different* columns.
**Failure scenario**: entity has `price_primary=1,200,000`, `price_secondary=90,000`; user asks "dịch vụ nào giá từ 100k đến 200k?" → `(1.2M ≥ 100k)` true, `(90k ≤ 200k)` true → row returned although neither price is inside [100k, 200k]. The bot then confidently lists an out-of-range service — a T1 correctness failure on the exact query class the stats index exists to make deterministic. Correct semantics: `(pp BETWEEN min AND max) OR (ps BETWEEN min AND max)`.

### F4 · HIGH — `count_by_name_keyword` and `query_by_name_keyword` use different match sets → count ≠ list

**FACT.** The list path has three matchers: forward unaccent-ILIKE + notation fold (`stats_index_repository.py:562-589`) + reverse/token fallback when forward is empty (615-645). The count path (`406-464`) has only forward unaccent-ILIKE + attributes (documented as intentional for the fold, 419-423 — but the **reverse fallback omission is not covered by that argument**).
**Failure scenario**: corpus stores granular entity "Nách"; question "có bao nhiêu gói triệt lông nách?" → forward match 0 → `count_by_name_keyword` returns **0** ("we have no such service") while the list route for the same phrase returns rows via reverse fallback. Same divergence for digit-notation variants ("205/55R16" vs "205 55 16"): fold exists only in list. Count answers and list answers about the same catalog contradict each other — a coherence bug in the B-AGG feature shipped specifically for count honesty (commit 949a3a4).

### F5 · HIGH — `stats_index_repository` class docstring claims tenant filters + RLS GUC that the read paths don't have

**FACT.** Docstring (stats_index_repository.py:4-8): "record_tenant_id + record_bot_id filters on every query, and the session is opened via session_with_tenant which sets the Postgres RLS app.current_tenant_id parameter". Reality: (a) only `bulk_insert` uses `session_with_tenant` (106); all six read methods and `delete_by_document` open plain `self._sf()` sessions (263, 330, 401, 461, 491, 604, 173); (b) no read filters `record_tenant_id` — `record_bot_id` only; (c) the GUC name in the docstring (`app.current_tenant_id`) doesn't exist — policies read `app.tenant_id` (session.py:82, squashed_baseline.sql:1477).
Per the CLAUDE.md identity rule, bot-scoped reads on `record_bot_id` alone are *acceptable*, and the RLS after_begin hook does bind the GUC inside bound requests — but the docstring materially misdescribes the isolation contract and would mislead an auditor into believing double-scoping exists. With F2 (RLS dead), `record_bot_id` is the **only** fence on this table today.

### F6 · HIGH — Per-bot config columns exist but have **no governed write path** (multi-bot axis)

**FACT.** `bot_repository.update_bot` allowlist (`bot_repository.py:270-275`) = {bot_name, record_model_id, record_embedding_model_id, system_prompt, setting_options, custom_vocabulary, max_history, max_documents, prompt_max_tokens, rerank_top_n, plan_limits, callback_url}. **Missing**: `language`, `oos_answer_template`, `rerank_intent_whitelist`, `threshold_overrides`, `action_config`, `metadata_extraction_config`, `bypass_*` flags — all of which are read-honored per-bot in the pipeline (`_row_to_config` maps them, 89-116; consumers: `chat_stream.py:267` oos_answer_template, `_action_conversation.py:40` action_config, threshold resolve chain).
Grep across `interfaces/http/routes/*.py`: **no route writes any of these fields** (only reads). Unknown keys are silently filtered out — `update_bot` returns the unchanged row, so a caller passing `language="en"` gets HTTP 200 with nothing updated (silent no-op, 282-284).
**Failure scenario**: bot owner wants to change the refusal template or enable slot-filling → no API exists → the only paths are psql UPDATE (banned by CLAUDE.md sacred rule 7) or a per-change alembic migration. This is the structural root of the recurring "action_config drift / configured-but-empty" incidents (memory: action slot-machine 2026-06-04). Multi-bot config is honored on read but not ownable on write.

### F7 · MEDIUM — `delete_by_document` (stats) is tenant-unscoped and will silently no-op fail-closed under live RLS

**FACT (code) + HYPOTHESIS (runtime, blocked by F2 today).** `stats_index_repository.py:173-181` deletes by `record_document_id` only, on a plain session, with a docstring asserting "RLS is not enforced here". Under the future `ragbot_app` role: callers are `ingest_stages_final.py:548` and `delete_document.py:92`, which run inside worker/request contexts that *do* bind tenant ctx (document_worker.py:149) — then the hook scopes the DELETE correctly. But any path where ctx is not bound (ops scripts, recovery worker replays) makes the DELETE match 0 rows **silently** (policy `current_setting(..., true)` → NULL): stale entities survive re-ingest and the subsequent `bulk_insert` (which *does* bind tenant) doubles every entity → price/count answers double-count. The wrapper `_insert_stats_index` swallows all exceptions (`document_service/__init__.py:313-328`), so nothing would surface.

### F8 · MEDIUM — `chat_histories` has no tenant column and no RLS; raw chat content fenced only by bot UUID

**FACT [DB-verified].** Table shape (squashed_baseline.sql:243-251): `record_bot_id, channel_type, connect_id, role, content` — **no `record_tenant_id`**, and `pg_class.relrowsecurity = f`. Also `token_ledger` (`f`) and `monitoring_log` (`f`). `history_reconcile.py:211-231` reads it by `(record_bot_id, connect_id)`. Because `record_bot_id` is an unguessable internal UUID, this is consistent with the "internal queries use record_bot_id ONLY" rule — but it means the RLS-hardening program has a permanent hole: even after the `ragbot_app` cutover, a bug that leaks one bot UUID (logs, citations payload) exposes cross-tenant chat transcripts with no second fence. `messages` (the other history store) is fully tenant-columned + RLS-forced; the two stores have asymmetric protection for identical content.

### F9 · MEDIUM — `finalize_request_log` writes three attributes that are neither ORM columns nor DB columns — silently dropped

**FACT [DB-verified].** `request_log_repository.py:144,147,165` set `row.agent_id`, `row.binding_variant`, `row.payload_sha256` on a `RequestLogModel` instance. The model (models_monitoring.py:75-162) defines none of them; live `information_schema.columns` for `request_logs` returns no such columns; no alembic migration mentions them. Setting undefined attributes on a declarative instance is a no-op for persistence — the values evaporate without error.
Currently **no caller passes them** (grep: 7 finalize call sites, none pass these kwargs) so it is a dead-parameter trap rather than active data loss — but the first caller to pass `binding_variant=` for A/B analytics will believe it's persisted. Either add the columns or remove the params.

### F10 · MEDIUM — Get-or-create races: `quota_repository.get` and `conversation_repository.get_or_create` have no IntegrityError recovery

**FACT (code).** `quota_repository.py:26-36`: SELECT → None → INSERT + commit with no `except IntegrityError` (PK = `record_tenant_id`). `conversation_repository.py:94-118`: same pattern against `uq_conv_bot_connect`. Contrast with `workspace_repository.ensure` (workspace_repository.py:85-93), which handles the race correctly.
**Failure scenario**: two first-turn chat requests for a brand-new tenant (or the same new `(bot, connect_id)` pair — e.g. a user double-sending) hit `get_or_create` concurrently → both SELECT None → both INSERT → loser raises `IntegrityError` → 500/turn failure on the *first* interaction a user ever has with the bot. Low frequency, worst-possible UX location. **HYPOTHESIS** on frequency (not load-tested), FACT on mechanism.

### F11 · MEDIUM — `bulk_insert` builds one monolithic INSERT: >~5,400 entities exceeds asyncpg's bind-param limit and the whole stats index is silently lost

**FACT (mechanism) + HYPOTHESIS (corpus sizes).** `stats_index_repository.py:116-148` emits one `INSERT ... VALUES (...), (...), ...` with 6 bound params per entity + 4 shared. PostgreSQL's extended protocol caps bind params at 32,767 → hard ceiling ≈ 5,460 entities. The caller passes the full parsed entity list unbatched (`document_service/__init__.py:315-321`) and **swallows any exception** with a warning (:322-328).
**Failure scenario**: a tenant uploads an XLSX price catalog with 6,000 rows → parser emits 6,000 entities → asyncpg raises → warning `stats_index_insert_failed` → ingest reports success, but every count/list/superlative/price query for that bot silently degrades to top-k vector retrieval (the exact failure class the index exists to prevent). Also note the same session carries `SET LOCAL statement_timeout='30000'` (engine.py:189) — a large multi-VALUES insert with a correlated chunk-lookup subquery per row (stats_index_repository.py:126-128) can hit the 30 s query-path timeout, same silent outcome. Fix: chunk into constant-size batches inside the repo.

### F12 · MEDIUM — `tenant_policy_repository.get_policy` with `record_bot_id=None`/`record_model_id=None` returns an arbitrary row

**FACT.** `tenant_policy_repository.py:236-245`: the bot/model filters are *skipped* when None (not translated to `IS NULL`), and there is no ORDER BY; `session.scalar()` returns whichever enabled row the planner yields first. A tenant with one tenant-wide policy + one bot-specific policy asking for "the tenant policy" can get the bot-specific one nondeterministically. (The `upsert` lookup at 163-169 is safe — SQLAlchemy renders `== None` as `IS NULL`.) Happy-case assumption: one policy per tenant.

### F13 · MEDIUM — Workspace dimension systematically collapsed to `"system"` on 6 write paths (multi-tenant/forensic axis)

**FACT.** Rows that belong to real workspaces are hardcoded to `WORKSPACE_SYSTEM_SLUG`:
- `job_repository.py:39` — every job (the signature doesn't even accept a workspace);
- `tenant_policy_repository.py:176, 206` — policies + their audit rows;
- `ai_config_repository.py:605` — prompt templates (a per-bot resource with a real bot workspace);
- `ai_config_repository.py:810` — every AI-config audit row;
- `quota_repository.py:32` — quota rows;
- `uow.py:93,123` — outbox fallback (defensible for tenant-level events).
Two consequences: (a) workspace-scoped forensic queries can't attribute these rows; (b) **latent RLS-cutover conflict**: the workspace-aware policies (squashed_baseline.sql:1483 for jobs etc.) add `WITH CHECK (workspace_id = current_setting('app.workspace_id'))` whenever the workspace GUC is bound. Workers *do* bind the workspace GUC (`document_worker.py:254`, `chat_worker/pipeline.py:209` → session.py:166-170). Under the `ragbot_app` role, a worker turn bound to workspace `acme-ws` inserting a `jobs` row with `workspace_id='system'` fails the WITH CHECK → insert rejected. HYPOTHESIS today (RLS dead per F2), deterministic once cutover happens. `create_binding` and `guardrail_repository.insert` do it right (inherit the parent row's slug — ai_config_repository.py:446-454, guardrail_repository.py:51-59); the six paths above should follow the same pattern.

### F14 · MEDIUM — `guardrail_repository.list_by_message` filters by upstream `message_id` only — cross-tenant read under app-level scoping

**FACT.** `guardrail_repository.py:77-88`: `WHERE message_id = :int` with no tenant filter. `message_id` is an upstream BIGINT with no global-uniqueness contract across tenants (models_guardrail.py:45 — "ID của khách"). With RLS dead (F2), any caller of this method (or the route above it, if tenant check is missing there too) can read another tenant's guardrail hits by guessing small integer ids. Even post-RLS it silently mixes rows from colliding upstream ids within a tenant's view. Add `record_tenant_id` to the filter.

### F15 · MEDIUM — Keyset cursors on bare timestamps skip rows on ties

**FACT (mechanism).** Two pagers use strictly-less-than on a timestamp with no tiebreaker:
- `document_repository.list_by_bot` (document_repository.py:242-243): `created_at < cursor`. Documents bulk-created in one transaction share `now()` (Postgres `now()` is txn-stable) → identical `created_at` → after page 1, `created_at < cursor` drops **all remaining same-timestamp rows**.
- `audit_repository.get_query_detail` (audit_repository.py:171-172): `started_at < cursor` — request_logs at the same millisecond straddling a page boundary are skipped.
**Failure scenario**: batch-ingest 100 docs in one job txn → `GET /documents?limit=50` page 2 returns empty → 50 documents invisible to the owner's console. Fix: composite keyset `(created_at, id)`.
Contrast: `tenant_repository.list_tenants` uses LIMIT/OFFSET (tenant_repository.py:266-270) — contradicts the project's keyset standard but is admin-only/low-cardinality (**info**).

### F16 · LOW-MEDIUM — `attach_feedback_by_message` updates *all* rows for `(tenant, message_id)`, docstring says "newest"

**FACT.** `request_log_repository.py:393-425`: plain UPDATE, no ordering/limit. A retried upstream message (same BIGINT id across two turns) gets feedback attached to every row, silently inflating `evaluated` counts in `get_overview` accuracy metrics (497-507).

### F17 · LOW-MEDIUM — Audit hash chain: unprotected genesis race per tenant

**FACT (mechanism).** `audit_chain_writer.py:62-78`: the tail lock is `SELECT ... FOR UPDATE LIMIT 1` — when a tenant has **zero** audit rows there is no row to lock, so two concurrent first-writes both read `prev_hash=""` and insert two genesis rows → the verifier sees a forked chain for that tenant forever (the immutability trigger prevents repair). One-time-per-tenant window; low probability, permanent artifact.

### F18 · LOW — Happy-case / locale / hardcode details

1. **`"vi"` literal fallback** — `bot_repository.py:89` `language=getattr(row, "language", "vi")` bypasses `DEFAULT_LANGUAGE` (constants `_02_...py:230`). The getattr default can't actually fire on a mapped column, but it's a zero-hardcode violation and a copy-paste trap. **FACT**.
2. **`"cache_hit"` sentinel string** — `audit_repository.py:57,194` compare `model_name == "cache_hit"` inline; no constant exists (grep of constants package: none). Magic string coupling analytics to whatever writes that sentinel. **FACT**.
3. **Model-layer magic defaults** — `models.py:71` & `:465` `10_000_000` tokens, `:476` `1000` docs/day, `:550-551` `8192`/`4096` context/output defaults: inline numbers that per CLAUDE.md belong in `shared/constants.py` (bot columns nearby correctly use `COLUMN_DEFAULTS`/`DEFAULT_*`). **FACT**.
4. **Price API is integer-only** — `stats_index_repository.py:195-196, 355-356` type `price_min/price_max: int | None`; columns are NUMERIC. Fine for VND-style integer prices; a decimal-currency corpus (USD 19.99) cannot express exact bounds (19.99 → int truncation upstream or rejection). Single-currency/scale happy-case per the `metadata-optional-hint` contract. **FACT** (signature), **HYPOTHESIS** (whether any current tenant needs decimals).
5. **`list_all_entities` silent truncation** — cap `DEFAULT_STATS_INDEX_QUERY_LIMIT=1000` (constants `_21_streaming_upload.py:31`), `ORDER BY created_at ASC LIMIT` (stats_index_repository.py:481-489). A catalog with >1000 entities silently loses the tail in "list everything" answers, and the caller has no signal (unlike count, which was cap-honesty-fixed). **FACT** (mechanism).

### F19 · LOW — Dead code / dead imports / drift

- `db/engine.py:198-208` `_on_engine_connect` + `attach_engine_hooks`: **no callers anywhere** (grep) — dead code.
- `audit_repository.py:9`: `Numeric, cast, literal_column` imported, never used. `ai_config_repository.py:9`: `insert` unused. `refuse_suggestion_model.py:42`: `TenantId` unused. **FACT** (grep-verified).
- `refuse_suggestion_model.py` — documented scaffold (V2 aggregator never shipped); harmless but counts as built-not-wired.
- `outbox_repository.poll_unprocessed` — legacy, retained deliberately for test fakes (outbox_publisher.py:219-222 documents this). Not dead, but the repo docstring's "production publisher loop now uses the per-row entry point" is accurate — OK.
- `request_log_repository.add_step` (276-332) still exists alongside `add_steps_batch`; per-step path does 1 parent-SELECT + 1 INSERT + 1 commit per pipeline stage (≈27/turn) if any caller still uses it — the batch API was added precisely to fix this; residual single-step callers are an N+1-shaped cost.
- `_base.py` docstring says the base class "enforce tenant_id presence at runtime" — it only provides the helper; several subclasses expose `record_tenant_id: None` bypasses (`bot_repository.list_active/get_by_id/update_bot/soft_delete`, `job_repository.update_status`, `ai_config_repository.list_bindings`). Each bypass is individually documented, but the aggregate means "extends TenantScopedRepository" is not evidence of scoping — audits must read per-method (this report does).

### F20 · INFO — Things verified healthy (for the synthesis agent)

- **RLS layer-3 plumbing is correctly built**: hook targets the sync session class of the async_sessionmaker (session.py:173-193), validates UUID/slug shapes before interpolation (99-134), and bootstrap routes every repo through it (bootstrap.py:185-186). UoW fail-closed guard (uow.py:44-49) and `session_with_tenant` fail-closed guard (engine.py:160-166) are real. The gap is deployment (F2), not design.
- `document_service_index` RLS `missing_ok` gap was found and fixed by `20260626_rls_missing_ok_setting.py` (policy re-asserted workspace-aware; live `relforcerowsecurity=t`).
- `outbox_repository.poll_one_for_update` exactly-once design is sound (lock lifetime == caller tx; rollback-on-exception, 60-92).
- `ai_config_repository.update_binding`/`delete_binding` TOCTOU-safe (tenant filter inside UPDATE WHERE, 499-517). *Caveat*: the field filter accepts any mapped column incl. `record_tenant_id`/`record_bot_id` (490-492) — a route bug could re-home a binding cross-tenant; today's admin routes don't pass those keys (**HYPOTHESIS** on exploitability, defense-in-depth gap only).
- `audit_repository.get_audit_overview` doc/chunk SQL is tenant-scoped post-P17 (68-92) — verified all three raw statements carry `:tid`.
- `message_feedback_repository` is the model citizen: `session_with_tenant` on read AND write + explicit tenant WHERE + table RLS-forced [DB-verified].
- `history_reconcile.merge_history_sources` is pure + well-tested shape (sort/dedup/limit semantics, 77-111); `SQLAlchemyError`-only catch honors the narrow-except policy.
- Broad-except sweep: **zero** `except Exception` in the entire scope (all catches are narrow: IntegrityError/SQLAlchemyError/ValueError/TypeError/InvalidOperation) — clean per policy.
- No provider `if/elif` ladders, no version-refs, no tenant/brand literals in any file in scope (the one "Innocom" mention is a historical incident comment in ai_config_repository.py:47-50 naming the platform's own infra, not a customer).

---

## Part 3 — Special-focus answers

### Tenant scoping census (which fence protects each repo, given F2 = RLS currently dead)

| Repo / path | Explicit tenant WHERE | session_with_tenant / GUC | Effective fence today |
|---|---|---|---|
| bot / conversation / message / document / quota / job / request_log / audit(read) / tenant_policy / workspace / prompt+binding (ai_config) | ✅ | hook-only (when ctx bound) | app-level WHERE |
| stats_index **reads** + doc-repo chunk fetchers + history_reconcile | ❌ tenant (record_bot_id only) | hook-only | bot-UUID unguessability |
| stats_index `delete_by_document` | ❌ | ❌ | document-UUID unguessability (trusted path) |
| message_feedback | ✅ | ✅ explicit | both |
| stats_index `bulk_insert` | ✅ (columns) | ✅ explicit | both |
| guardrail `list_by_message` | ❌ | hook-only | **none** (F14) |
| language_pack / providers / models / ai_keys | n/a (platform-global) | — | intentional |
| token_ledger analytics | ✅ caller-supplied (+RBAC-gated `all_tenants`) | table has no RLS | app-level WHERE |
| outbox repo | none (system worker) | system factory (BYPASSRLS) | by design |

### stats_index query paths ×5
- **keyword (list)**: forward unaccent+synonyms+fold+attributes → reverse fallback — richest matcher (512-659).
- **keyword (count)**: forward-only → diverges from list (F4).
- **range**: cross-column OR/AND bug on default `"any"` (F3).
- **superlative**: `top_by_price` correct (NULL-excluded, COALESCE ranking, 281-349).
- **list-all**: silent 1000-row truncation (F18.5).
All five JOIN live parent docs (ING-7 guard) — verified present in every SQL string.

### Keyset vs offset
Keyset: audit `get_query_detail`, document `list_by_bot` — both tie-unsafe (F15). Offset: `tenant_repository.list_tenants` (admin-only). `conversation._fetch_by_keys_with_messages` LIMIT-pushdown JOIN is the good pattern.

### N+1 risks
Fixed: conversation JOIN fetch; `get_models_by_ids`/`get_providers_by_ids`; `add_steps_batch`. Residual: per-event parent-workspace SELECT in `guardrail_repository.insert` (:52-58) and per-step parent SELECT in legacy `add_step` (:304) — one extra round-trip per write, acceptable; `finalize_request_log`'s correlated `(SELECT bot_id FROM bots ...)` in the monitoring INSERT (:181) is one subquery per finalize — fine.

---

## Part 4 — Ranked fix list (repo-scope only)

1. **F1** ai_keys schema prefix — 5-line fix, unbricks admin key management. `[T2]`
2. **F3** price-range "any" semantics — direct wrong-answer path. `[T1]`
3. **F4** count/list matcher parity (share one WHERE builder). `[T1]`
4. **F6** extend update_bot allowlist (or dedicated admin endpoints) for language/oos_answer_template/action_config/threshold_overrides/metadata_extraction_config/rerank_intent_whitelist. `[T1-adjacent, owner self-service]`
5. **F2** ops: provision DATABASE_URL_APP→ragbot_app, remove escape env — with **F13** workspace-slug writes fixed first, else worker inserts start failing WITH CHECK. `[T2/security]`
6. **F11** batch bulk_insert (constant batch size) + surface partial failure. `[T1 durability]`
7. **F10** IntegrityError-recovery in quota.get / conversation.get_or_create (copy workspace_repository.ensure pattern). `[T2]`
8. **F15** composite keyset cursors. **F14** tenant filter in list_by_message. **F9** delete dead finalize params. **F16/F17/F18/F19** cleanups.
