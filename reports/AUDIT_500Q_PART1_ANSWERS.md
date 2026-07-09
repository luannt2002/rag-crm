# AUDIT 500Q — PART 1 ANSWERS (code-truth) · 2026-07-09

> Workflow wf_9c821e24-5d9 (21 agents, 0 err). Synthesis TRUTH-MAP + raw section answers from journal.

> Rule #0: file:line / SQL live. Sections from journal are authoritative (synthesis input was truncated).


## SYNTHESIS (25-item TRUTH-MAP + verified)

I have enough evidence. Compiling the final document. Note on scope: my input contained answer+verdict blocks for only 4 of the 10 requested sections (B, T, UP, and a truncated P — CL/CH/EN/EM/ST/W were not supplied). Per rule #0 I do not fabricate the missing sections; I independently spot-verified the cross-section status-items that the 25-item TRUTH-MAP names, using live repo tools, and flag the rest as UNVERIFIABLE.

---

# AUDIT 500Q PART-1 — ANSWERS (code-truth)

> **Scope & provenance (read first).** This document assembles the per-section auditor answers + adversarial verdicts I was given. My input included complete answer+verify blocks for **SECTION B, T, UP** and a **truncated SECTION P** (answer cut off at P28, no P verify-block). Answer-blocks for **CL, CH, EN, EM, ST, W were NOT in my input.** Per project rule #0 (CẤM ĐOÁN — no claim without evidence) I do **not** reconstruct those sections from memory. For the 25-item TRUTH-MAP — which names status-items spanning all sections — I independently re-verified with live repo tools every item I could reach (`grep`, `pytest --collect-only`, source read); items whose source section was not supplied and could not be reached are marked **UNVERIFIABLE** with whatever partial evidence I found. cwd=`/var/www/html/ragbot`, branch `fix-260623-ingest-expert`.

---

## 1. TRUTH-MAP — 25 claimed-status items

| # | Claim | Verdict | Evidence (file:line / SQL / cmd) |
|---|---|---|---|
| 1 | Block-native pipeline is a **no-op** on the happy path | **CONFIRMED** | Flag `adapchunk_block_pipeline_enabled` code-default `=True` (`_12:185`, DB absent→True) but `_analyze_blocks` guarded `if … and parsed_blocks` (`ingest_stages.py:612`); only the OCR fallback produces `Block`s (`document_worker.py:500`), the 6 registry parsers return `list[dict]` ⇒ `blocks=None` ⇒ every structured-format doc falls to legacy text-flatten. |
| 2 | **RLS is inert/dead at runtime** (superuser bypass) | **CONFIRMED** | Live probe as `postgres`: `SET app.tenant_id='000…0'; SELECT count(*) FROM bots` → **6** (bogus tenant sees all). `current_user=postgres, is_superuser=on`. Fallback path `engine.py:67-81` (DATABASE_URL_APP unset + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` .env → admin DSN). |
| 3 | **HyDE never wired / dead** | **CONFIRMED** (spot-verified) | `infrastructure/hyde/null_hyde.py:11` header comment verbatim: *"Reason: HyDE infra never wired."* Default `NullHyDEGenerator` returns query verbatim (`hyde_port.py:13,17`). |
| 4 | Embedding = **zembed-1 truncated to 1280-dim** | **CONFIRMED** (code) | `DEFAULT_ZEROENTROPY_EMBEDDING_MODEL="zembed-1"` (`_02_per_intent_rerank_skip_gate_.py:65`), `DEFAULT_ZEROENTROPY_EMBEDDING_DIM=1280` (`:68`); zembed-1 is 2560-dim, matryoshka-truncated to 1280 because pgvector HNSW caps <2000 (`:66-67`). *Live `embedding_provider` value not re-queried this pass (see §4).* |
| 5 | **83 feature flags** | **UNVERIFIABLE** | Source section not in my input. Rough live count: 94 `bool`/`Final[bool]` constants under `shared/constants/`, +9 `bool` settings in `config/settings.py`. Exact "83" definition (which set) unknown. |
| 6 | **7381 tests** | **CONFIRMED** (spot-verified) | `python -m pytest --collect-only -q` → *"7381 tests collected, 12 errors in 21.30s"* (12 collection errors, e.g. `test_streaming_upload.py`). Raw `def test_` grep = 6892 across 833 files; delta = parametrized cases. |
| 7 | **token_ledger embed-leak** | **UNVERIFIABLE** (polarity unknown) | Source section (EM/EN) not supplied. Mechanism evidence: `token_ledger/aux_usage.py:1` emits rerank/embed usage rows fire-and-forget; **only** `jina_embedder.py:329` wires it; `embedding/registry.py:98` comment: *"LiteLLM/ZeroEntropy ignore `ledger`"* → the **live ZE embedder does not ledger embed tokens** ⇒ embed usage unattributed for the shipped provider. |
| 8 | **check_token_cap is dead** (defined, never called) | **CONFIRMED** (spot-verified) | `async def check_token_cap` at `application/services/tenant_token_meter.py:276`; grep of `check_token_cap(` across `src/` = **0 call sites** (only the def + a docstring at `:57`). |
| 9 | **/ready does not exist** → deploy.sh readiness loop broken | **CONFIRMED** | No route defines `/ready`; `deploy.sh` step-5 polls it. `/health` (`routes/health.py:107`) and `/health/models` (`health_models.py:1`) are the real probes. |
| 10 | **/documents/upload-stream is DEAD** (router not mounted) | **CONFIRMED** | `router.py:64` line commented `# router.include_router(documents_stream_upload…)` under a `# DISABLED — data-loss` block; `documents_stream_upload.py:1-7` docstring "DISABLED — not mounted". Handler exists (~:221) but never included. |
| 11 | Canonical content-hash dedup does a **silent skip** | ⚠️ **REFUTED** | `ingest_core.py:446` logs `ingest_duplicate_content_hash` then `:451 raise DocumentDuplicateError` — it **RAISES**; worker `document_worker.py:690 except Exception` → job `status='failed'` + `DocumentFailed(INGEST_FAILED)`. Duplicate ⇒ **hard job failure**, opposite of a silent skip. (sha256/normalized-content/scoping sub-claims are correct.) |
| 12 | `tests/unit/test_content_hash_dedup.py` referenced but **does not exist** | **CONFIRMED** | `ls` → No such file. Referenced only in comments `ingest_core.py:427`, `test_ingest_embed_failure_no_orphan.py:20`. Dedup is comment-claimed-tested but untested. |
| 13 | **4-state max-ingest-chars drift** (500K vs 2M) | **CONFIRMED** | Constant `MAX_DOCUMENT_CONTENT_CHARS=500_000` (`_03:78`); service default 500K (`ingest_core.py:377`); HTTP test_chat inline `2_000_000` (`test_chat/document_routes.py:238/240`); **live DB `system_config.max_ingest_content_chars=2000000`**. Inline 2M = zero-hardcode drift. |
| 14 | **docling = dead residue** (wired, not installed) | **CONFIRMED** | `infrastructure/ocr/docling_parser.py` (167 LOC) + `ocr_factory.py:77-81` + `settings.py:273 Literal["docling","mistral"]="docling"`, but `docling` import = ModuleNotFoundError; live `parser_engine='kreuzberg'` → never constructed; selecting it would ImportError at `DoclingParser.__init__`. |
| 15 | **VLM image parser dormant** (provider=null) | **CONFIRMED** | `DEFAULT_VLM_PROVIDER="null"` (`_00:167`); seed absent, live DB absent ⇒ `document_worker.py:189 if provider=="null": return None`. `vlm_caption_prompt` seeded but unused. Never fires. |
| 16 | **markdown_normalize OFF/dormant** | **CONFIRMED** | `DEFAULT_MARKDOWN_NORMALIZE_ENABLED=False` (`_11:65`); not in `init_system_config.py`; live DB absent ⇒ OFF. Only normalizes joined-text path, not row-chunks (`document_service/__init__.py:851-862`). |
| 17 | **Happy-case checker not wired into ingest/CI** (silent) | **CONFIRMED** | `scripts/check_happy_case.py` is an offline CLI (`main:293`), `sys.exit(2)` only on bad args; no runtime reject/warn hook; low-quality doc still ingests. |
| 18 | **ragbot_system role MISSING** in live DB | **CONFIRMED** | SQL: `ragbot_system` count=0; `ragbot_app` exists (`super=f,bypassrls=f,login=t`). `DATABASE_URL_SYSTEM` unset → falls back to admin (`engine.py:110-113`). |
| 19 | **tenant_model_policy = 0 rows** (dormant switch) | **CONFIRMED** | SQL: 0 rows. Columns `record_tenant_id, record_bot_id, private_doc_ratio, web_search_ratio, general_knowledge_ratio, default_for_task, record_fallback_model_id, enabled`; route `admin_tenant_policy.py`. |
| 20 | **bots.bypass_token_check = 6/6 TRUE** | **CONFIRMED** | SQL: `(count, FILTER bypass_token_check, bypass_token_limit, bypass_rate_limit)=(6,6,6,0)`. |
| 21 | **Config-completeness gate advisory, not in CI**; 71 keys fall through to DEFAULT | **CONFIRMED** | `scripts/check_config_completeness.py` — grep of `.github/workflows/*` = 0 hits; only DB-free stub `test_config_completeness_baseline.py` (`_BASELINE_MAX=71`). Baseline `scripts/config_constant_fallback_baseline.txt`. |
| 22 | **Embed workers embedded in API single process (`--workers 1`)** | **CONFIRMED** | `embed_workers_enabled` default `True` (`settings.py:124`); `app.py:433 if settings.app.embed_workers_enabled: start_embedded_workers`; live systemd `ragbot-py.service … uvicorn --workers 1`. |
| 23 | **Soft-delete orphans chunks** (CASCADE never fires) | **CONFIRMED** | 11 FKs→`bots(id)` are `ON DELETE CASCADE`, but repo does soft delete `is_deleted=True` (`bot_repository.py:302`) ⇒ CASCADE never triggers ⇒ chunks/docs/cache/conversations orphan under soft-deleted bot. |
| 24 | `infrastructure/parser/chunk_strategy.py` referenced in STEP_PIPELINE.md but **MISSING** | **CONFIRMED** | `RAGBOT_STEP_PIPELINE.md:60` (U4) cites the path; file does not exist; real chunking in `shared/chunking/`. |
| 25 | **doc-state 'processing' never written to DB** | **CONFIRMED** | Live states = `active×24, failed×2` only; `'processing'` is a UI-derived label (`document_routes.py:104-115`); grep for a `state='processing'` writer = 0 hits. |

**Roll-up:** 21 CONFIRMED · 1 REFUTED (item 11) · 2 UNVERIFIABLE (items 5, 7) · item 4 CONFIRMED at code-level (live provider value pending re-query, §4).

---

## 2. Per-section answer blocks

Auditor file:line answers preserved. The only verifier-REFUTED claim in the supplied data is UP04 (flagged ⚠️ inline).

### SECTION B — Boot / Entry / Process (B01–B15)

- **B01.** Init = `lifespan()` `app.py:253`. Order: `get_settings()` (:259) → `setup_logging` (:260) → `_check_required_provider_keys` (:270, **NO-OP in dev**, gated `APP_ENVS_STRICT` :137) → `Container()` (:272) → `_check_runtime_db_role` (:279) → tracing/sentry (:283) → `bus.ensure_streams()` (:288) → `llm.refresh_routing()` (:299) → `asyncio.gather(_bootstrap_resolver, _bootstrap_bot_registry, _bootstrap_tokens, _bootstrap_guardrail_rules)` (:363) → CORS check (:375) → reranker preflight (:411) → `run_warmup` (:422) → `start_embedded_workers` (:433) → `startup_complete` (:444). `create_app()` (middleware+routers) runs at **import** :687, before lifespan.
- **B02.** `embed_workers_enabled: bool = True` code-default `settings.py:124` (env `APP_EMBED_WORKERS_ENABLED`), NOT a system_config key; live=True. OFF → standalone `python -m ragbot.interfaces.workers.document_worker` (`main()` :764). Wired `app.py:433`.
- **B03.** PROD = bare-metal **systemd `ragbot-py.service`** `uvicorn … --workers 1`, `Restart=always`, enabled+active. Single process = API + embedded consumer/outbox/recovery/cost-cap/cache-purge (`embedded_workers.py:242`). `ragbot-chat-worker.service`=disabled; `ragbot-document-worker@.service`=not installed; timers/cron=none. **DRIFT:** `start.sh:12`/`deploy.sh:113` use `--workers 2` (would double-consume the embedded stream).
- **B04.** `docker-compose.yml`=DEV (hardcoded creds, port 8000); `docker-compose.uat.yml`=UAT overlay (port 3004). **Neither is live prod** — bare-metal systemd; `docker ps` shows no ragbot container.
- **B05.** 131 route decorators under `routes/`. Test/debug harness = 45 (37 `@router` in `test_chat/*.py` + 7 `@pages_router` in `pages.py` + 1 `admin_documents_debug.py:96`). Kept per headless-BE rule. `documents_stream_upload` de-registered `router.py:64`.
- **B06.** `SchemaVersionMiddleware` `schema_version.py:52`; header `X-Schema-Version`; served = **(1,)** only (`_09:130-132`); missing→default 1; unknown→HTTP 400 `SCHEMA_VERSION_UNSUPPORTED`.
- **B07.** Runtime middleware order (Starlette reverse-of-add): IpRateLimit → AntiAbuse → BodySizeLimit → CORSPerTenant → SourceRateLimit → BotRateLimit → SlidingRateLimit → SecurityHeaders → GZip → TraceContext → TenantContext → SchemaVersion → Logging → route (`create_app` :495-636).
- **B08.** **Redis down → API CANNOT answer chat**: `IpRateLimitMiddleware` (default True `settings.py:116`) **fail-CLOSED** → 503 before route (`ip_rate_limit.py:287-299`; `DEFAULT_RL_FAIL_MODE="closed"` `_03:131`). SourceRateLimit degrades open; registry/token/semantic-cache have DB fallback but the fail-closed gate short-circuits first.
- **B09.** `build_graph` `query_graph.py:981`; compiled graph = **process-wide singleton, lazily built on FIRST request** via `get_graph()` :3046 (memoized :3050). Callers = request handlers only (`chat_stream.py:235`, `test_chat/chat_routes.py:390/900`, `chat_worker/pipeline.py:571`). NOT in lifespan/warmup. DB config change ⇒ **restart** required.
- **B10.** Constants module-level `Final`, bound once at import; `get_settings()` `@lru_cache` (`settings.py:15`). Prod `APP_DEBUG=false` ⇒ no uvicorn reload ⇒ constant edit needs `systemctl restart ragbot-py`.
- **B11.** `tests/conftest.py` diverges from prod: fake `DATABASE_URL_APP`, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (:24), no-op dotenv (:76), autouse disables IP-RL + anti-abuse (:352-355), busts `get_settings.cache_clear()` per test. Not prod parity.
- **B12.** `/health` `health.py:107` **always 200**, truth in body `status`; checks PG `SELECT 1` + Redis `PING` in parallel; provider probes in `/health/models`. **`/ready` does NOT exist** → deploy.sh readiness loop broken.
- **B13.** `setup.sh`=dev-laptop provisioning; `deploy.sh` = check PG/Redis → `alembic upgrade head` → `pytest tests/unit` → stop old PID → `nohup uvicorn --workers 2` → poll `/ready` (broken). **No direct psql UPDATE/INSERT** in deploy/start/setup (grep=0) — sacred no-psql-hotfix OK.
- **B14.** Config-completeness gate = `check_config_completeness.py`, **ADVISORY / NOT in CI** (workflow grep=0). **71 contract keys currently fall through to code DEFAULT unseeded** (baseline `_BASELINE_MAX=71`).
- **B15.** Missing system_config key = **default-swallow, NOT fail-loud** (`system_config_service.py:72,92`). Fail-loud only for ENV secrets: `APP_API_TOKEN` (`app.py:343`), provider keys (`app.py:162`, strict envs only). Config-completeness gate is the intended drift guard but unenforced.
- **Cross-cutting (live):** `APP_ENV=development` → strict preflight/CORS = NO-OP. `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` → runtime role `postgres` (`rolsuper=t, rolbypassrls=t`) → **RLS INERT**. `DATABASE_URL_APP` absent. `system_config`=264 rows; `reranker_enabled=True, reranker_provider=zeroentropy`.

### SECTION T — Identity / Multi-tenant / RLS (T01–T30)

- **T01.** 4-key resolve `BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` `bot_registry_service.py:117` → Redis-first then `find_by_4key` `bot_repository.py:167`. All 4 required.
- **T02.** `record_tenant_id` lifted from JWT in `tenant_context.py:132` (service) / `:404` (user); bound to `request.state` (:377/:420). Chat/document body carries no tenant; only super-admin routes body-carry it (`admin_tenants.py:108`), defended by `enforce_tenant_match` (:481).
- **T03.** Cache key `ragbot:bot:{tenant}:{ws}:{bot}:{channel}` (`:82-88`), TTL `DEFAULT_BOT_CONFIG_TTL_S=3600` (`_04:101`). DB-edit bust via `invalidate()` (:253); `_read_cache` evicts cross-tenant-poisoned rows (:198-207).
- **T04.** `uq_bots_record_tenant_workspace_bot_channel = UNIQUE(record_tenant_id, workspace_id, bot_id, channel_type)` — verified live. Migration `20260504_0062_workspace_4key_identity.py`.
- **T05.** No base-repo auto-inject; `_ensure_tenant()` (`_base.py:20`) not called in bot_repo, called manually elsewhere. Real scope = per-query explicit filter (`pgvector_store.py:258`). RLS is dead backstop (T08).
- **T06.** FORCE-RLS tables lacking `record_tenant_id` column: `document_chunks`, `knowledge_edges` — scope via EXISTS-join to parent.
- **T07.** **24 FORCE-RLS = 24 RLS-enabled = 24 policies** (live). List verbatim: audit_log, bot_model_bindings, bots, conversations, document_chunks, document_service_index, documents, guardrail_events, ingest_idempotency_keys, jobs, knowledge_edges, message_feedback, messages, model_invocations, outbox, prompt_templates, prompt_versions, quotas, refuse_suggestions, request_logs, request_steps, semantic_cache, tenant_model_policy, workspaces.
- **T08.** `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` + `DATABASE_URL_APP` UNSET → runtime engine falls back to admin superuser DSN (`engine.py:60-92`) → **every RLS policy silently bypassed**. `current_user=postgres, is_superuser=on`.
- **T09.** `create_rls_session_factory` (`session.py:221`) SET-LOCALs `app.tenant_id` only when `tenant_id_ctx` bound (:160-163). No-op under superuser.
- **T10.** `create_engine_system` (`engine.py:95`) intended BYPASSRLS role; consumers = `bot_repo_system` (`bootstrap.py:481`) + `OutboxRepository` (:499). `DATABASE_URL_SYSTEM` UNSET → admin fallback → split inert.
- **T11.** Integration RLS-leak suite = **4 skipped** (needs `--run-integration`+PG/Redis). **Live superuser probe: `SET app.tenant_id='000…0'; SELECT count(*) FROM bots` → 6 = bypass PROVEN.** Live isolation genuinely **CHƯA VERIFY**.
- **T12.** Cross-bot isolation enforced by explicit `WHERE record_bot_id` (`pgvector_store.py:258`), NOT RLS. Live DB = **1 tenant owns all 6 bots** → cross-bot leak not exercised → **CHƯA VERIFY live**.
- **T13.** tenantA-JWT→tenantB-bot: body-tenant routes → 403 `enforce_tenant_match` (:489); chat → `find_by_4key` foreign bot → None/not-found; Redis evicts poisoned row. RLS layer inert.
- **T14.** `bots.bypass_token_check`=**6/6 TRUE**, `bypass_token_limit`=6/6, `bypass_rate_limit`=0/6. No dedicated audit trail for the flag.
- **T15.** `tenants`: 58 total, **0 `bypass_rate_limit=true`**.
- **T16.** Blocker = `DATABASE_URL_APP` UNSET. `ragbot_app` role EXISTS (non-super, non-bypassrls); **`ragbot_system` role MISSING (0)**. Runbook `docs/dev/NON_SUPERUSER_DSN_RUNBOOK.md`, ADR-0001, grant migration `20260619_rls_app_role_grants.py`.
- **T17.** `NULLIF('')` hardening **NOT applied** (0 policies). Policies read `current_setting('app.tenant_id', true)::uuid`; unset→NULL→fail-closed; literal `''` would raise on cast (pre-validated `session.py:_assert_uuid_str`).
- **T18.** `resolve_workspace_id` `workspace_id_validator.py:75`; missing/empty→`WorkspaceId(str(record_tenant_id))`; forensic rows `WORKSPACE_SYSTEM_SLUG="system"`.
- **T19.** `channel_type` = plain VARCHAR, no enum, no CHECK. Schema `chat_schema.py:50` `pattern=CHANNEL_TYPE_PATTERN`; well-formed-unknown (e.g. `telegram`) passes then misses 4-key resolve (not-found, not 422). All live bots=`web`.
- **T20.** Chat history scoped by `record_bot_id`+`connect_id`+`channel` (column named `channel`, not `channel_type`).
- **T21.** `audit_log`=65 rows; actions {create, delete, document_bulk_delete, document_bulk_ingest, purge, update}. `system_prompt` edit = PATCH `/admin/bots/{uuid}` → `update_bot` → `_write_audit(action="update")` (`bot_management_service.py:199`).
- **T22.** `rbac.py:17`: super_admin=100, tenant=80, admin/service=60, operator=40, user=20, viewer=10, guest=0. Two guards: `require_permission_dep` + `require_min_level`/`_require_super_admin`. Coverage scan: no admin endpoint missing a guard.
- **T23.** `api_keys`=3 rows ALIVE (8 refs); `ai_keys`=0 dormant (5 refs). Runtime provider keys from env `PROVIDER_API_KEYS_JSON` via `DBBackedApiKeyPoolFactory` (`bootstrap.py:222`). `ai_providers`=4, `ai_models`=7.
- **T24.** Layered limiter in `TenantContextMiddleware`: L1 per-tenant fail-CLOSED (`:233`), L1.5 per-service-token (120/60), L2 per-user connect_id (5/3s). Plus BotRateLimit / SourceRateLimit / IpRateLimit / AntiAbuse middlewares.
- **T25.** `is_loadtest_bypass` `loadtest_bypass.py:44`: env token + `secrets.compare_digest` header + loopback peer. Scope = anti-abuse counter + IP-RL only; auth/RBAC/honeypot still apply.
- **T26.** Constraint permits same `(bot_id,channel_type)` across tenants; resolve always tenant-scoped. Test `test_4key_required.py`. 1-tenant live → not runtime-exercised → **CHƯA VERIFY**.
- **T27.** 11 FKs→`bots(id)` `ON DELETE CASCADE`; **BUT repo soft-deletes `is_deleted=True` (`bot_repository.py:302`) ⇒ CASCADE never fires ⇒ chunks/docs/cache/conversations ORPHAN** under soft-deleted bot.
- **T28.** `DELETE /admin/tenants/{id}` = SOFT, 409 if tenant still has bots (`admin_tenants.py:9`), super-admin-only, emits audit + invalidates `TenantConfigCache`. Hard GDPR path `admin_gdpr.py`.
- **T29.** Hidden switch = `tenant_model_policy` — **0 rows = dormant**.
- **T30.** Flip RLS today, first breakages: (1) `ragbot_system` MISSING → outbox publisher + `bootstrap_cache` warm fail if system DSN flipped; (2) `bootstrap_cache` warm `list_active(record_tenant_id=None)` cross-tenant read → 0 rows → empty bot cache on NOBYPASSRLS app role; (3) any unbound-tenant app session → fail-closed 0 rows; child EXISTS-join tables (`document_chunks`,`knowledge_edges`) vanish without matching parent GUC.

### SECTION UP — Upload / Ingest entry (UP01–UP20)

- **UP01.** Canonical = `POST /api/ragbot/documents/create` (`documents.py:91`, mounted `router.py:55`). "1 API" claim **VIOLATED** — live ingest paths: `/documents/rechunk` (:228), `/rechunk-by-id` (:267), `POST /sync/documents` (`sync.py:428`→SYNC `doc_svc.ingest()` :559, no outbox), test_chat `/documents` (`document_routes.py:171`, own INSERT+outbox), test_chat `/documents/upload` (:481, SYNC). Dead: `/documents/upload-stream` (`documents_stream_upload.py:216`, not mounted). = 3 live ingest + 2 rechunk + 1 dead.
- **UP02.** Canonical Action-1 = `IngestDocumentUseCase.execute` (`ingest_document.py:54`): idem_key (58) → slugify tool_name (65) → existing check (75) → duplicate fast-path (78-87) → `Document.new_draft` content_hash="pending" (93, **NOT fetched**) → save DRAFT (113) → `jobs.create` (118) → `add_outbox(DocumentUploaded)` (144) → commit (145) → `idem.register` (147). Canonical create does NOT fetch raw_content in Action-1 (worker fetches); test_chat add_document is a SEPARATE Action-1 that DOES fetch+store.
- **UP03.** `MAX_DOCUMENT_CONTENT_CHARS=500_000` (`_03:78`); service `ingest_core.py:377` default 500K→worker `state=failed`. HTTP test_chat inline default **2_000_000** (`document_routes.py:238-249`) = zero-hardcode drift. **4-STATE DRIFT:** code=500K / HTTP-inline=2M / **live DB system_config=2000000** / comment "worker checks". Referenced test `test_content_hash_dedup.py` **does NOT exist**.
- **UP04.** ⚠️ **REFUTED by verifier.** Auditor said canonical/service content-hash dedup does a **silent skip** (logs, no 409). **Correction:** `ingest_core.py:451` **RAISES `DocumentDuplicateError`** after logging `ingest_duplicate_content_hash` (:446); worker `document_worker.py:690 except Exception` → `job status='failed'` (:709) + `DocumentFailed(INGEST_FAILED)`. A hash-duplicate becomes a **hard job failure**, not a silent skip. (sha256 hexdigest of normalized content, scoping `(record_bot_id, content_hash, deleted_at IS NULL)` = correct.)
- **UP05.** source_url dedup `ingest_core.py:391-416` routes to re-index. Canonical idem key includes source_url (`idempotency_key.py:40`). Sheets diff-gid preserved (`google_link_service.py:211-213`) → different gid = different content_hash = NOT collapsed; byte-identical source_url string → deduped.
- **UP06.** `to_export_url` unified (`google_link_service.py:195`), used by Action-1 fetch (:149) AND worker (`document_worker.py:405`). DOUBLE-FETCH in test_chat path; canonical `/documents/create` does NOT double-fetch (Action-1 no fetch).
- **UP07.** `documents.raw_content`=TEXT, `content_chars`=int. Worker reuses raw_content ONLY for `local://` (`document_worker.py:350-368`). Live max content_chars=**266,669 → 187 chunks**; max chunks any doc=207. "**224KB→2643 chunks**" figure **NOT reproduced**.
- **UP08.** 202 body (canonical) = `IngestDocumentResponse{job_id, tool_name, status="queued", trace_id}` — **no progress field**. Poll: `GET /jobs/{job_id}` (no progress%); test_chat `GET /…/documents` rich progress (`document_routes.py:158-166`).
- **UP09.** Event subject `document.uploaded.v1` (`_09:19`). Payload fields listed; persisted to `outbox` (bytea). `FOR UPDATE SKIP LOCKED` `outbox_repository.py:64`, driven by `outbox_publisher.py:116` per-row exactly-once. NOT the ingest worker (worker consumes Redis stream).
- **UP10.** Redis Stream key `ragbot:document.uploaded.v1` (`redis_streams_bus.py:109`). Group `documents:document-worker` (`document_worker.py:781`). Consumer `group:{uuid4[:8]}`. XREADGROUP :344. Tenant fields inside JSON payload, not separate stream fields.
- **UP11.** Worker does NOT blanket-reuse raw_content: `document_worker.py:350 if document_id and not _is_refetchable_url(source_url)` → reuse only for `local://`; http(s)/Google **always RE-FETCHED** + re-parsed (:394,443-448). Grep-claim "no refetch source_url" is FALSE for URL sources — refetch by design.
- **UP12.** Live states = `active×24, failed×2` (no `processing` persisted). Flips: DRAFT (Action-1) → job `running` (:260, not doc.state) → store UPSERT `active` (`ingest_core.py:504`, bypasses domain SM) → store error `failed` → finalize re-decides active|failed by leaf-embed coverage (`ingest_stages_final.py:188-215`). `'processing'` = UI-only label, never written (writer grep=0).
- **UP13.** No per-bot binding ⇒ graceful fallback: `_embedding_spec` catches resolver failure → system_config → settings default (`document_service/__init__.py:381-402`). LLM stages best-effort try/except default OFF. Only genuine death: platform embedder null/unreachable → `EmbeddingError` → finalize `failed` (embedded≤0).
- **UP14.** 0-byte: test_chat upload→400 "Empty file" (:506); add_document empty→400 "Tài liệu trống" (:226); upload-stream(dead)→422. Weird MIME: fallback chain → octet-stream rescued by `detect_parser_robust` byte-sniff (:456). VN-filename: verbatim, unicode-safe, no normalization.
- **UP15.** **No explicit `state='active'` guard in retrieval.** `pgvector_store.py:270 doc_deleted_at IS NULL` + record_bot_id; `pg_bm25_retrieval.py:117`. "readiness gate serves only active" (`ingest_stages_final.py:197`) is ASPIRATIONAL — not in retrieval SQL. DRAFT invisible only via 0-chunks; failed×2 both 0 chunks + deleted. Safe by 0-chunk accident, not a state filter.
- **UP16.** Recovery `document_recovery_worker.py:363`. Conditions: DRAFT+aged OR active+0-chunks+aged, AND no fresh outbox (anti-dup LEFT JOIN). `STUCK_THRESHOLD_S=900`, `INTERVAL_S=300`, `BATCH_SIZE=100`, `REPLAY_COOLDOWN_S=3600`. Replays canonical event + audit.
- **UP17.** Re-upload = **INCREMENTAL diff, NOT full delete+replace** (`ingest_stages_store.py:710-730`, single tx): DELETE stale (index≥new count) + changed (hash mismatch); unchanged preserved. Diff via `shared/diff_reingest.py`. (Full DELETE exists only for rechunk/delete use-cases `pgvector_store.py:132,179`.)
- **UP18.** **No `/documents/check` route** (grep=0). Checker logic exists as `shared/ingestion_validator.py` but NOT an endpoint.
- **UP19.** Two idempotency layers: (a) HTTP header `X-Idempotency-Key` (max 128, TTL 24h) → table `ingest_idempotency_keys`, body fingerprint sha256, replay returns original job_id; (b) use-case dedup key `ingest:{tenant}:{ws|system}:{bot}:{source_url}:{corpus_version=0}` (`idempotency_key.py:40-64`).
- **UP20.** Per-stage timing (request_steps, **n=1** ingest job): validate=0, parse=0, clean=117ms, chunk=199ms, enrich=0, **vn_segment=9009ms (~97%)**, embed_store=0ms. Total ≈9.33s. CAVEAT: only 1 ingest request_log (26 docs predate Phase-D tracker); `embed_store=0ms` implausible → embed not wrapped by tracked step; U6 VN-segment is the real hotspot.

### SECTION P — Parse / Multi-format (P01–P28, truncated)

> P answer-block was truncated at P28 in my input, and no P verify-block was supplied. Reproduced through the last complete item.

- **P01.** Registry `infrastructure/parser/registry.py:45-61` `_REGISTRY` (8 keys); `detect_parser()` (:97-120) iterates insertion order, first `.supports(mime,ext)` wins: null → kreuzberg_markdown(pdf/pptx/html) → excel_openpyxl(xlsx) → google_sheets(csv/gsheet) → pdf(legacy) → docx → markdown(md/txt) → vlm_image.
- **P02.** Parser+test+REAL live ingest: PDF (4 active), CSV (6), TXT/MD (8 text/plain), HTML (6). Code+test but **0 live ingest**: DOCX, XLSX, PPTX, images(dormant).
- **P03.** MarkdownParser = transform not passthrough (`markdown_parser.py:42-71`): strips YAML front-matter, splits on H1/H2, heading re-prepended per section. Verified: `# Section One` retained in chunk-0.
- **P04.** Canonical IR convergence = `shared/tabular_markdown.py:214 rows_to_structured_markdown()` + `:358 split_markdown_to_row_chunks()`. Convergence real only for TABULAR (CSV/XLSX/Sheets/DOCX-tables); prose (PDF/PPTX/HTML flat blob, MD/TXT section-split) stays parser-native.
- **P05.** Kreuzberg = **in-process Python lib**, NOT a service (compose has no OCR container). OCR path `infrastructure/ocr/kreuzberg_parser.py` (Tesseract `vie+eng`); test `test_kreuzberg_parser.py`. Installed.
- **P06.** Docling = **DEAD residue**: present + wired (`ocr_factory.py:77-81`, `settings.py:273 …="docling"`) but NOT installed; `parser_engine='kreuzberg'` live → never constructed; selecting it → ImportError.
- **P07.** OCR live `bootstrap.py:322`; call-site `document_worker.py:494`, reached ONLY on OCR-fallback branch (`:490 if not registry_routed and not full_text.strip()`) — images/scanned only. `vlm_image_parser` never auto-fires.
- **P08.** XLSX (`excel_openpyxl_parser.py:79-95`) walks ALL sheets (`data_only=True, read_only=True`) → ONE document, sheet nested under `# <name>` only when `sheet_count>1`, then row-as-chunk split.
- **P09.** CSV NO delimiter sniffing — default comma (`google_sheets_parser.py:83`), BOM stripped, utf-8→latin-1 fallback. "700,000→700000" via `number_format.py:136-137`. Verified: `parse_money_vn('700,000')=700000`, `'1tr499'=1499000`, `'6 triệu'=6000000`.
- **P10.** Row-1-header flatten REPLACED by state machine (`tabular_markdown.py:214-349`): header by shape `_looks_header` (:93), empty cell→`col{i+1}`, multi-row header merged (:314-335), empty-column/merged-cell forward-fill (:144-211). 16 tabular tests pass.
- **P11.** HTML→kreuzberg_markdown (table→md internal). **NO explicit nav/footer strip** in either path. Fallback `simple_text_parser.py:317-382` converts `<table>`→pipe, `<h1-6>`→`#`, also no nav/footer removal.
- **P12.** Sheets `edit?gid=N` guard `google_link_service.py:195-219`: rewrites viewer→`/export?format=csv&gid={N}` (gid PRESERVED), Docs→`format=docx`. Called worker `:405` before fetch. Prevents HTML-login-page OCR retry-storm.
- **P13.** No dedicated PDF text-vs-scan byte-sniff — kreuzberg_markdown always `extract_bytes_sync(OutputFormat.MARKDOWN)` (kreuzberg OCRs scanned internally). Legacy `pdf_parser.py:82-99` skips empty pages → pure-scan yields 0 chunks. Live: 1 pdf failed at `enriching`, not parse.
- **P14.** Happy-case checker `scripts/check_happy_case.py`: heading-density ≥3, column-role coverage, value-density ≥0.5, **price coverage ≥0.9=PASS / >0<0.9=WARN / 0=FAIL** (:196-202). Imports role-token SSoT from `document_stats.py`. Manual CLI, not in ingest/CI.
- **P15.** `normalize_to_markdown` runtime only in `document_service/__init__.py:854-862`, gated `markdown_normalize_enabled`. **4-state:** code default `False` (`_11:65`) / not seeded / **live DB ABSENT** → effectively **OFF/dormant**; only normalizes joined-text path.
- **P16.** Low happy score → **silent**: offline CLI, `sys.exit(2)` only on bad args; NO runtime reject/warn hook — low-quality doc still ingests.
- **P17.** Col-token SSoT `shared/document_stats.py:175-225` (`_NAME/_CATEGORY/_PRICE/_ALIASES_COL_TOKENS`, `_HEADER_*`, `_AGGREGATE_TOKENS`→`_REJECT_NAME_TOKENS`). **DOMAIN-NEUTRAL FLAG:** hardcoded VN+EN vocab in engine code (`"dich vu","san pham","goi","combo","gia","don gia"` :176-194) + `CUSTOMER_CONTEXT_COLUMN_NAMES` VN literals (`_13:24-26`) = per-language coupling in shared engine code.
- **P18.** Post-U2 form = ONE structured-markdown string (`##`+pipe) for tabular, ONE flat blob for pdf/html/docx. Verified `rows_to_structured_markdown` output. Live U2 step: `parser_provider='passthrough'`, mime text/csv, n_chars_in==out==266669.
- **P19.** `parsed_blocks` = **dead-wire on happy path**. `BlockType` Literal HEADING/TEXT/TABLE/FORMULA/IMAGE/CODE/LIST (`shared/types.py:105`). **Producer: ONLY OCR fallback** (`document_worker.py:500`); 6 registry parsers return `list[dict]` ⇒ `registry_routed=True` ⇒ `blocks=None`. **Consumer** `ingest_stages.py:597-615` gated `adapchunk_block_pipeline_enabled` (default True `_12:185`, DB absent⇒True) BUT `_analyze_blocks` only `if … and parsed_blocks` (:612) → empty ⇒ legacy text-flatten. Net: Block-native pipeline is a **no-op** for every structured-format doc despite flag True.
- **P20.** Embedded-image VLM = **dormant**. `DEFAULT_VLM_PROVIDER="null"` (`_00:167`), seed absent, live DB absent ⇒ `document_worker.py:189 return None`. `vlm_caption_prompt` seeded but unused. Never fires.
- **P21.** `.txt`→MarkdownParser (degrades to single section). CSV-detect misfire `mime_sniff.py:154-161` only when declared mime ∈ {"",octet-stream} + `first_line.count(",")>=3`; `text/plain` trusted → misfire limited to no-ext/octet-stream comma-rich.
- **P22.** Non-UTF8 handled AT PARSE (no separate decode stage): markdown `errors="replace"` (:101), CSV utf-8→latin-1 (:41-44), simple_text `errors="ignore"`. No BOM/encoding column recorded.
- **P23.** STEP_PIPELINE.md deltas: (a) U2 line 58 omits docx/kreuzberg_markdown/google_sheets/vlm_image; (b) U4 line 60 cites `infrastructure/parser/chunk_strategy.py` which **does not exist** (real chunking in `shared/chunking/`); (c) doc omits dual parse path.
- **P24.** No `error_message` column on `documents` (cols: `current_step`, `metadata_json`). Failure detail in `jobs.error`. Live 2 failed docs both at `current_step='enriching'` = enrich-stage, not parse. Parse errors raise `ValueError`, caught→warn+OCR-fallback.
- **P25.** Wide/merged linearize `tabular_markdown.py:_normalize_rows:144-211` + multi-row header merge (:314-335). Stress `test_tabular_robustness_golden.py` + `test_parser_all_formats_structured.py` + `test_document_parser_strategy.py` → **16 passed**.
- **P26.** MD code block kept as plain TEXT inside its section, NOT typed CODE (registry markdown splits only on `#`). Verified: ` ```python … ` retained verbatim. (CODE BlockType exists only in OCR adapters.)
- **P27.** YAML frontmatter **DROPPED** (`markdown_parser.py:33-36,42`). Verified: `title:/lang:` absent from output chunks.
- **P28.** *(truncated in input)* Golden datasets `tests/eval/datasets/30Q_golden_{legalbot,medispa,thongtu}.json` (3 bots × 30Q) + re-ingest scripts `scripts/retest_{spa,xe}_golden*.py`. A "9-file/3-bot re-ingest" run was **NOT executed** (needs live ingest worker) → **CHƯA VERIFY**.

### SECTIONS CL, CH, EN, EM, ST, W — NOT SUPPLIED

Answer+verify blocks for **CL (Clean), CH (Chunk/Chat), EN (Enrich), EM (Embed), ST (Stats/Store/Tests), W (Workers/misc)** were **not present in my input** (payload truncated after P28). I did not reconstruct them. The cross-section status-items those sections own that I could still verify with live repo tools appear in the §1 TRUTH-MAP (HyDE dead #3, embedding zembed-1280 #4, 7381 tests #6, token_ledger #7, check_token_cap dead #8). Everything else in those sections is **pending re-supply** (see §4).

---

## 3. Top surprises — 8 EXISTS≠WORKS gaps

1. **RLS is present but INERT at runtime** — 24 FORCE-RLS tables + 24 policies exist, yet the app connects as `postgres` (superuser, `rolbypassrls=t`) because `DATABASE_URL_APP` is unset and `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`. Live probe: bogus tenant sees all 6 bots. `engine.py:67-81`, `.env`. Cross-tenant isolation is enforced only by explicit per-query `WHERE record_bot_id`, not by RLS.
2. **Block-native ingest pipeline is a no-op** — flag `adapchunk_block_pipeline_enabled=True`, but the 6 registry parsers emit `list[dict]` (not `Block`), so `parsed_blocks` is only populated on the OCR fallback. Every structured-format doc silently falls to legacy text-flatten. `ingest_stages.py:612`, `document_worker.py:500`.
3. **HyDE infrastructure is fully built and never wired** — `null_hyde.py:11` literally states *"HyDE infra never wired."* Default `NullHyDEGenerator` passes the query through unchanged.
4. **`check_token_cap` is dead code** — defined `tenant_token_meter.py:276`, **zero call sites** in `src/`. The token-cap enforcement path it implies does not run.
5. **`/ready` endpoint does not exist → deploy readiness loop is broken**, compounded by a `--workers 2` drift (`start.sh:12`/`deploy.sh:113`) against the live `--workers 1` unit — 2 workers would double-consume the embedded Redis stream. `deploy.sh` step-5, `B03`.
6. **Content-hash "dedup" is a hard failure, and its test doesn't exist** — a duplicate hash `raise DocumentDuplicateError` → job `status='failed'` (not a graceful skip), and `tests/unit/test_content_hash_dedup.py` (cited at `ingest_core.py:427`) is missing. `ingest_core.py:451`, `document_worker.py:690-709`.
7. **docling is dead residue that would crash if selected** — wired in `ocr_factory.py:77-81` and defaulted in `settings.py:273`, but the package is not installed; `parser_engine='kreuzberg'` is what saves it. Selecting `docling` → ImportError at construction.
8. **RLS cutover never happened** — `ragbot_system` role is MISSING (0) and `ragbot_app` is provisioned but unused; the NOBYPASSRLS runtime DSN is unset. Flipping it today would empty the bot cache at boot (`bootstrap_cache` cross-tenant `list_active(None)`), break the outbox publisher, and fail-close unbound sessions. `T16`, `T30`, `bootstrap.py:481/499`.

Runners-up (also EXISTS≠WORKS): config-completeness gate unenforced with 71 keys unseeded (`B14`); VLM image parser dormant `provider="null"` (`P20`); `markdown_normalize` OFF/dormant (`P15`); `tenant_model_policy` 0 rows (`T29`); soft-delete orphaning chunks because CASCADE never fires (`T27`); live ZE embedder does not emit embed usage to the token ledger (`registry.py:98`).

---

## 4. CHƯA VERIFY — open items + how to close them

| Item | Why unverified | Command to close |
|---|---|---|
| **Live cross-tenant / cross-bot RLS isolation** (T11/T12/T26) | Live DB has 1 tenant owning all 6 bots; integration suite = 4 skipped | Provision `ragbot_app` NOBYPASSRLS DSN into `DATABASE_URL_APP`, seed a 2nd tenant, run `pytest tests/integration/test_rls_leak_2tenant.py --run-integration` |
| **9-file / 3-bot golden re-ingest** (P28) | Needs a live ingest worker; not run in-session | `python scripts/retest_spa_golden*.py` / `retest_xe_golden*.py` against a running document worker; diff answers vs `30Q_golden_*.json` |
| **Per-stage ingest timing** (UP20) | Only n=1 ingest request_log; 26 docs predate Phase-D tracker; `embed_store=0ms` implausible | Ingest one fresh doc with StepTracker ON, then `SELECT step,duration_ms FROM request_steps WHERE connect_id='ingest' ORDER BY started_at` |
| **Sections CL, CH, EN, EM, ST, W** | Answer+verify blocks not in my input (payload truncated after P28) | Re-supply those section answer/verdict blocks, or re-run their audits |
| **"83 flags"** (TRUTH-MAP #5) | Source section not supplied; exact flag-set definition unknown | Supply the source section; my rough live count = 94 `Final[bool]` constants + 9 settings bools |
| **`token_ledger` embed-leak** (TRUTH-MAP #7) | Claim polarity unknown (source section EM/EN not supplied) | Supply the claim text; mechanism evidence already gathered: `aux_usage.py:1`, `jina_embedder.py:329`, `registry.py:98` ("ZeroEntropy ignore ledger") |
| **Live `embedding_provider` value** (TRUTH-MAP #4) | `psql` via socket failed auth this pass (`fe_sendauth: no password supplied`) | With the same DSN the B/T verifiers used: `SELECT value FROM system_config WHERE key='embedding_provider'` (code default constant is `zembed-1`/1280-dim; prior session state = ZeroEntropy) |
| **`documents_stream_upload` truly unreachable** (UP01) | Router-include is commented; not proven no other module includes it | `grep -rn "documents_stream_upload" src/ | grep include_router` → expect only the commented `router.py:64` |

---

*Evidence-only. All CONFIRMED/REFUTED verdicts trace to a live `grep`/`pytest --collect-only`/SQL result or a `file:line`. UNVERIFIABLE items are those whose source section was not in my input and could not be reached with the tools available this pass; none are guessed.*



# ===== RAW SECTION ANSWERS (all 10) =====


I have all the evidence I need. Here is the complete answer block for SECTION B.

```markdown
## SECTION B — Boot / Entry / Process (B01–B15)

**B01.** Init order = `lifespan()` at `src/ragbot/interfaces/http/app.py:253`. First 10 steps: (1) `get_settings()` app.py:259; (2) `setup_logging` :260; (3) `_check_required_provider_keys(settings)` :270 — **NO-OP in dev** (APP_ENV=development, gated by `APP_ENVS_STRICT` app.py:137); (4) `Container()` + `app.state.container/settings` :272-274; (5) `await _check_runtime_db_role(container.db_engine())` :279 (RLS role guard); (6) `init_tracing` + `init_sentry` :283-284; (7) `bus=container.bus(); await bus.ensure_streams()` :288-289; (8) `llm.refresh_routing()` :299-300; (9) `asyncio.gather(_bootstrap_resolver, _bootstrap_bot_registry, _bootstrap_tokens, _bootstrap_guardrail_rules)` :363-368; (10) CORS-mismatch check :375 → reranker preflight :411 → `run_warmup` task :422 → `start_embedded_workers` :433-437 → `startup_complete` :444. NOTE: `create_app()` (middleware stack + routers) runs at **import** app.py:687, BEFORE lifespan.

**B02.** `embed_workers_enabled: bool = True` — code default `src/ragbot/config/settings.py:124` (env `APP_EMBED_WORKERS_ENABLED`, prefix at :131). NOT a `system_config` key (env-driven, no seed/DB row). `.env` does not override → **live = True**. Comment settings.py:117-123. OFF → ingest runs via standalone `python -m ragbot.interfaces.workers.document_worker` (`main()` `document_worker.py:764`, `__main__` :800) + `outbox_publisher.py:240/251`. Wired in app.py:433 `if settings.app.embed_workers_enabled:`.

**B03.** PROD = bare-metal **systemd `ragbot-py.service`** (`/etc/systemd/system/ragbot-py.service`, enabled+running): `ExecStart=... uvicorn ragbot.interfaces.http.app:app --host 0.0.0.0 --port 3004 --workers 1 --log-level info`, `Restart=always`. Single process = API + embedded consumer/outbox/recovery/cost-cap/cache-purge (embedded_workers.py:242). `ragbot-chat-worker.service` = **disabled**. `ops/systemd/ragbot-document-worker@.service` template = not installed. `deploy/*.timer` (monthly-reset, token-reconcile) present on disk but **NOT installed** (`systemctl list-timers` shows none). `crontab -l` = empty. DRIFT: `start.sh`/`deploy.sh` run `--workers 2` but live unit is `--workers 1` (with `--workers 2` embedded workers would double-consume the stream).

**B04.** `docker-compose.yml` = DEV (hardcoded creds `ragbot/ragbot`, api built from `docker/Dockerfile.api` on port **8000**, `--profile ml` for infinity/tei). `docker-compose.uat.yml` = UAT overlay (env-sourced creds, port **3004**, Redis AOF+`appendfsync everysec`, api healthcheck → `/health`, db-backup sidecar, no host-exposed pg/redis ports). **NEITHER is the live prod** — prod runs bare-metal via systemd (B03); no docker container is running for ragbot.

**B05.** 131 route decorators under `src/ragbot/interfaces/http/routes/` (`grep -rE '@router\.(get|post|put|patch|delete)\(' | wc -l = 131`). test/debug harness = 45 decorators across `routes/test_chat/*` (7 files: chat/admin/bot_admin/bot_insights/document/monitoring/token + pages.py) + `admin_documents_debug.py`. Mounted: test_chat at `{BASE}/test` (router.py:101), `chat_async` at `{BASE}/test` :105, `pages_router` at root :106. NOT deleted per headless-BE rule (CLAUDE.md §1) — internal QA harness. Orphaned `documents_stream_upload` is **de-registered** router.py:64.

**B06.** `SchemaVersionMiddleware` `src/ragbot/interfaces/http/middlewares/schema_version.py:52`, added app.py:496. Reads header `X-Schema-Version` (`SCHEMA_VERSION_HEADER` `constants/_09_message_feedback_thumbs_verd.py:130`). Versions served = **only (1,)** (`SUPPORTED_SCHEMA_VERSIONS=(1,)` :131, `DEFAULT_SCHEMA_VERSION=1` :132). Missing header → default 1; unparseable/unknown → HTTP 400 `SCHEMA_VERSION_UNSUPPORTED` (schema_version.py:73-93). Not a `system_config` key.

**B07.** Middleware chain — `create_app()` app.py:495-636. Runtime order (Starlette = reverse of add order, outermost runs first): **IpRateLimit** (:629, if enabled) → **AntiAbuse** (:599, if enabled) → **BodySizeLimit** (:571) → **CORSPerTenant** (:559) → **SourceRateLimit** (:549) → **BotRateLimit** (:536) → **SlidingRateLimit** (:518) → **SecurityHeaders** (:505) → **GZip** (:499) → **TraceContext** (:498) → **TenantContext** (:497) → **SchemaVersion** (:496) → **Logging** (:495) → route. (In-code docstring at :490-494 lists a shorter target flow; actual stack is the above.)

**B08.** Redis consumers: rate-limit middlewares (IP, per-token sliding, bot, source), `semantic_cache` L2, BotRegistry cache, JWT-token cache, Streams bus. **Redis down → API CANNOT answer chat**: `IpRateLimitMiddleware` (enabled default True, settings.py:116) is **fail-CLOSED** → 503 before route (`ip_rate_limit.py:206-208`; `DEFAULT_RL_FAIL_MODE="closed"` `constants/_03_language_packs_db_driven_pro.py:131`; sliding limiter rate_limit.py:183-190 also fail-closed unless mode="open"). SourceRateLimit degrades open (app.py:544). Registry/token/semantic-cache have DB fallback but the fail-closed rate-limit gate short-circuits first.

**B09.** `build_graph` `orchestration/query_graph.py:981`; compiled-graph **process-wide singleton, lazily built on FIRST request** via `get_graph()` :3046 (`_GRAPH_SINGLETON` :3042, memoized, kwargs ignored after first build :3050). Callers = request handlers only: `chat_stream.py:235`, `test_chat/chat_routes.py:390/900`, `chat_worker/pipeline.py:571` (via `build_graph_di_kwargs` graph_assembly.py:92). NOT built in lifespan (warmup fires a probe but no `get_graph` in warmup.py). DB config re-read requires **restart**: DI singletons closed over at build-time are process-wide; per-request data lives on `GraphState` (:3037 comment).

**B10.** Constants are module-level `Final` in `shared/constants/_NN_*.py`, bound once at import → stay in RAM for process life. `get_settings()` is `@lru_cache` (settings.py:15) — value captured at first call. Graph singleton (B09) + DI Container Singletons compound this. Hot-reload only if `settings.app.debug` (uvicorn `reload=settings.app.debug` main.py:32); prod `APP_DEBUG=false` → no reload. So editing a constant needs `systemctl restart ragbot-py`.

**B11.** Root `tests/conftest.py`. Diverges from runtime: sets `os.environ.setdefault("DATABASE_URL_APP", "postgresql+asyncpg://test:test@localhost.../ragbot_test")` :20-23 and `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` :24; no-ops `dotenv.load_dotenv/find_dotenv` :76-77; autouse `_disable_security_middlewares_unless_overridden` sets `APP_IP_RATE_LIMIT_ENABLED=false` + `APP_ANTI_ABUSE_ENABLED=false` :352-355; busts `get_settings.cache_clear()` per-test :356/358; resets embedding-cache + jina CB singletons :247-258. So tests run superuser-allowed, security-mw off, fake DSN — not prod parity.

**B12.** `/health` `routes/health.py:107` — **always HTTP 200**, truth in body `status` field (health.py:5-8). Checks Postgres `SELECT 1` (:88) + Redis `PING` (:99) in parallel (:130) + pool stats; does NOT probe AI providers. Provider probes live in separate **`/health/models`** (`health_models.py:1`, embedder/reranker/LLM via registry). NOT static-200-only, but never 500s. NOTE: `/ready` (curled by deploy.sh :5-step) **does not exist** — no route defines it → deploy.sh readiness loop is broken drift.

**B13.** `setup.sh` = dev-laptop provisioning (Linux Mint: Chrome, IDE, Docker, Node, Go — NOT prod). `deploy.sh`: (0) check Redis+Postgres up (read-only `SELECT` via psycopg2), (1) `alembic upgrade head` :line "Running migrations", (2) `pytest tests/unit`, (3) stop old PID, (4) `nohup uvicorn --workers 2 --access-log`, (5) poll `/ready` (broken, see B12). **No direct `psql UPDATE/INSERT`** in deploy.sh/start.sh/setup.sh (grep `psql|UPDATE|INSERT` = 0 hits) — sacred no-psql-hotfix OK; only alembic mutates DB.

**B14.** Config-completeness gate = `scripts/check_config_completeness.py` (needs seeded DB, runs after `alembic upgrade head`). **ADVISORY / NOT wired into any CI** — grep of `.github/workflows/*` for `check_config_completeness`/`config_completeness` = 0 hits; the 5 workflows run only `test_eval_gate_scoring.py` (eval-gate.yml:45), `test_rls_cross_tenant.py` (cross-tenant-rls.yml:109), per-bot-golden, eval-ragas, audit-agent-diff. Only a DB-free regression stub `tests/unit/test_config_completeness_baseline.py` exists (baseline ceiling `_BASELINE_MAX=71` :39). Docstring claims "deploy pipeline runs it" (check_config_completeness.py:5-10, README_DEVOPS.md) but no runner enforces it. Result: **71 contract keys currently fall through to code DEFAULT unseeded** (baseline file `scripts/config_constant_fallback_baseline.txt`).

**B15.** Missing `system_config` key = **key-or-DEFAULT swallow, NOT fail-loud**: `SystemConfigService.get(key, default=None)` `application/services/system_config_service.py:72` → `if row is None: return default` :92 (get_int/get_bool/get_float wrap it :96-120). Fail-loud is only for specific ENV secrets, not DB keys: `APP_API_TOKEN` raises `RuntimeError` in `_bootstrap_tokens` app.py:343-347; provider keys raise in `_check_required_provider_keys` app.py:162-167 (strict envs only, skipped in dev). The config-completeness gate (B14) is the intended drift guard but is unenforced.

---
### Cross-cutting live-DB evidence (relevant to B, verified this session)
- `APP_ENV=development` (.env) → strict-env preflight/CORS-wildcard reject are NO-OPs in this deploy.
- `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (.env) → superuser boot-guard bypassed (`app.py:191` escape). Live runtime role = `postgres` (`rolsuper=t, rolbypassrls=t`) → **RLS policies INERT** (cross-tenant isolation not enforced). `DATABASE_URL_APP` absent from .env (0 hits); app connects via `DATABASE_URL` superuser DSN — the staged `ragbot_app` NOBYPASSRLS cutover has not happened.
- `system_config` live: 264 rows; `reranker_enabled=True`, `reranker_provider=zeroentropy`; `schema_version` + `cors_allowed_origins` = `<MISSING>` (schema_version is a code constant not a DB key, so expected; cors falls back to env `APP_CORS_ALLOWED_ORIGINS="[]"`).
```

---


I have comprehensive evidence. Compiling the final answer block.

---

## SECTION T — Identity / Multi-tenant / RLS (T01–T30)

**T01.** 4-key resolve = `BotRegistryService.lookup(record_tenant_id: UUID, workspace_id, bot_id, channel_type)` → `application/services/bot_registry_service.py:117`; Redis-first then DB via `SqlAlchemyBotRepository.find_by_4key(record_tenant_id, workspace_id, bot_id, channel_type)` → `infrastructure/repositories/bot_repository.py:167`. Both require all 4 keys.

**T02.** `record_tenant_id` lifted from JWT claim in `TenantContextMiddleware.dispatch` — service-JWT path `interfaces/http/middlewares/tenant_context.py:132` (`payload.get("record_tenant_id")`), user-JWT path `:404`; bound to `request.state.record_tenant_id` (`:377`, `:420`) + `bind_request_context()`→`tenant_id_ctx` (`:383`,`:425`). Chat/document body does NOT carry tenant (`chat_schema.py:5-6` doc; body = bot_id/channel_type/workspace_id/connect_id). Only super-admin-only routes body-carry it: `schemas/admin_tenants.py:108`, `schemas/admin_tenant_policy_schema.py:21`, defended by `enforce_tenant_match` (`tenant_context.py:481`).

**T03.** Key `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` → `bot_registry_service.py:82-88` (`_key`). TTL `DEFAULT_BOT_CONFIG_TTL_S=3600` → `shared/constants/_04_jwt_auth.py:101` (used `:109/:238/:295`). DB-edit bust: `BotManagementService.update_bot` → `BotRegistryService.invalidate()` re-loads/DELs the key (`bot_registry_service.py:253`); `_read_cache` also evicts cross-tenant-poisoned rows (`:198-207`).

**T04.** `uq_bots_record_tenant_workspace_bot_channel` = `UNIQUE(record_tenant_id, workspace_id, bot_id, channel_type)` — verified live (`pg_get_constraintdef`). Migration `alembic/versions/20260504_0062_workspace_4key_identity.py`.

**T05.** NO base-repo auto-inject. `_base.py:TenantScopedRepository` only exposes `_ensure_tenant()` guard (`repositories/_base.py:20`), NOT called in `bot_repository.py`; it IS called manually in `conversation_repository.py:93`, `workspace_repository.py:38`, `tenant_analytics_service.py:223` etc. Per-query explicit filter is the real scope — e.g. retrieval `pgvector_store.py:258` `record_bot_id = :record_bot_id` (`_build_bot_filter :218`). RLS is the generic backstop but DEAD (superuser, see T08).

**T06.** FORCE-RLS tables lacking a `record_tenant_id` column: `document_chunks`, `knowledge_edges` (SQL run). Both scope via EXISTS-join to parent in the policy — `document_chunks`→`documents.record_tenant_id`, `knowledge_edges`→`bots.record_tenant_id` (quals dumped). Every other tenant table carries `record_tenant_id`.

**T07.** 24 FORCE-RLS tables = 24 RLS-enabled = 24 policies (SQL run): `audit_log, bot_model_bindings, bots, conversations, document_chunks, document_service_index, documents, guardrail_events, ingest_idempotency_keys, jobs, knowledge_edges, message_feedback, messages, model_invocations, outbox, prompt_templates, prompt_versions, quotas, refuse_suggestions, request_logs, request_steps, semantic_cache, tenant_model_policy, workspaces`.

**T08.** `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (.env; const `_17_pipeline_audit.py:12-13`). `SELECT current_user` → `postgres`, `is_superuser=on`. `DATABASE_URL_APP` UNSET + escape=1 → runtime engine falls back to admin superuser DSN (`engine.py:60-92`, `settings.py:222-236`) → **every RLS policy silently bypassed**.

**T09.** `create_rls_session_factory(engine)` (`db/session.py:221`) = `create_session_factory` + `attach_rls_session_hook` (`after_begin` listener `_after_begin` `:149`). Wired at `bootstrap.py:185-187` for the app `session_factory`. Per-transaction `SET LOCAL app.tenant_id='<uuid>'` fires ONLY when `tenant_id_ctx` is bound (`:160-163`) — i.e. authed requests via middleware; public/ops sessions = no-op. Explicit belt `session_with_tenant` (`engine.py:134`) also SET-LOCALs for pgvector paths. All no-ops under superuser.

**T10.** `create_engine_system` (`engine.py:95`) = intended `ragbot_system` BYPASSRLS role. Consumers of `system_session_factory`: only `bot_repo_system` (`bootstrap.py:481` → `BotRegistryService(system_repo=...)` warm `:620`) + `OutboxRepository` (`:499`). The docstring's "4 system jobs" (recovery/cache-GC/cost-cap) do NOT route through it (regular factory). `DATABASE_URL_SYSTEM` UNSET → falls back to admin (`engine.py:110-113`) → split inert.

**T11.** Probes = `tests/integration/test_rls_leak_2tenant.py` (+ `test_rls_cross_tenant.py`, `test_rls_tenant_isolation.py`, `test_rls_tenant_scope_enforced.py`). RAN `pytest test_rls_leak_2tenant.py` → **4 skipped** (needs `--run-integration` + Postgres/Redis; `skipif not DATABASE_URL_APP`). Live isolation = CHƯA VERIFY here. My own live probe (as postgres, `SET app.tenant_id='0000…'` then `SELECT count(*) FROM bots`) → **6 rows visible = superuser bypass PROVEN, RLS dead at runtime**.

**T12.** Cross-bot same-tenant isolation is enforced by explicit `WHERE record_bot_id` in retrieval (`pgvector_store.py:258`, unit `tests/unit/infrastructure/vector/test_pgvector_store_tenant_filter.py`), NOT by RLS. Live DB has only **1 tenant owning all 6 bots** (SQL) → cross-bot leak not exercised at runtime → CHƯA VERIFY live.

**T13.** tenantA-JWT→tenantB-bot: for body-tenant routes `enforce_tenant_match` → HTTP 403 `record_tenant_id mismatch` (`tenant_context.py:489-496`); for chat, `find_by_4key` uses `request.state.record_tenant_id` so a foreign bot returns `None` → not-found; Redis `_read_cache` evicts poisoned cross-tenant row (`bot_registry_service.py:198-207`). RLS layer would also block but is inert (superuser).

**T14.** `bots.bypass_token_check` = **6/6 TRUE** (SQL: total=6, bypass_token_check_true=6); also `bypass_token_limit`=6/6, `bypass_rate_limit`=0/6. No dedicated audit trail for the flag (only generic bot `update` audit).

**T15.** `tenants.bypass_rate_limit` BOOLEAN (+ `rate_limit_per_min` INT). Live: 58 tenants, **0 with bypass_rate_limit=true** (SQL). Prod/dev value = default false everywhere.

**T16.** Blocker = `DATABASE_URL_APP` UNSET → provision the non-superuser `ragbot_app` role + credential + point runtime DSN at it. Live: `ragbot_app` role EXISTS (`super=false,bypassrls=false,login=true`) but **`ragbot_system` role MISSING** (SQL: 0). Runbook `docs/dev/NON_SUPERUSER_DSN_RUNBOOK.md` + `docs/adr/0001-rls-enforce-app-role.md`; grant migration `alembic/versions/20260619_rls_app_role_grants.py`.

**T17.** NULLIF('') hardening **NOT applied** — 0 policies use `NULLIF` (SQL). Policies read `current_setting('app.tenant_id', true)::uuid` directly. Sample (bots): `tenant_isolation :: ((record_tenant_id = (current_setting('app.tenant_id',true))::uuid) AND ((COALESCE(current_setting('app.workspace_id',true),'')='') OR ((workspace_id)::text = current_setting('app.workspace_id',true))))`. Unset→NULL::uuid→fail-closed; a literal `''` set would raise on cast (code pre-validates UUID shape at `session.py:_assert_uuid_str`).

**T18.** `resolve_workspace_id` → `shared/workspace_id_validator.py:75`; missing/empty → `WorkspaceId(str(record_tenant_id))` UUID slug (`:106`). Tenant/forensic rows use `WORKSPACE_SYSTEM_SLUG="system"`.

**T19.** `channel_type` = plain VARCHAR (SQL: `character varying`), NO enum, NO DB CHECK constraint (SQL: 0). Schema field `chat_schema.py:50` is `str` with `pattern=CHANNEL_TYPE_PATTERN` + `max_length` → pattern-violation → 422; but an unknown-yet-well-formed channel (e.g. `telegram`) passes and simply misses the 4-key resolve (bot-not-found), NOT 422. All live bots = `web`.

**T20.** Chat history scoped by `record_bot_id` + `connect_id` + `channel` (conversations columns: `record_bot_id, connect_id, channel, workspace_id, record_tenant_id` — SQL). Column named `channel` (not `channel_type`).

**T21.** `audit_log` = 65 rows; distinct actions `{create, delete, document_bulk_delete, document_bulk_ingest, purge, update}` (SQL). Admin edit of `system_prompt` = PATCH `/admin/bots/{uuid}` (`admin_bots.py:79`, guard `require_permission_dep("bot","update")`) → `BotManagementService.update_bot` → `_write_audit(action="update")` (`bot_management_service.py:199-203`) → yes, writes an audit row.

**T22.** Levels in `shared/rbac.py:17`: super_admin=100, tenant=80, admin/service=60, operator=40, user=20, viewer=10, guest=0. Guarded two ways: `require_permission_dep` (perm-based, `admin_ai.py`,`admin_bots.py`) + `require_min_level`/`_require_super_admin` (`admin_analytics.py:59`, `admin_audit.py:25`). Coverage scan: every `admin_*.py` file has guards ≥ endpoints (e.g. admin_bots 8 ep/16 guard-refs, admin_rate_limits 1/4) → **no admin endpoint missing a guard**.

**T23.** Both tables exist. `api_keys` = 3 rows = ALIVE (8 code refs). `ai_keys` = 0 rows = empty/dormant (5 refs). Runtime provider keys actually come from env `PROVIDER_API_KEYS_JSON` via `DBBackedApiKeyPoolFactory` (`bootstrap.py:222`). `ai_providers`=4, `ai_models`=7.

**T24.** Layered limiter in `TenantContextMiddleware`: L1 per-tenant `TenantRateLimiter.check` (`tenant_context.py:233`, `application/services/tenant_rate_limiter.py`) keyed `record_tenant_id`, limit = tenant.`rate_limit_per_min` → sysconfig `tenant_rate_limit_per_min`, fail-CLOSED 503 (owner/super_admin skip); L1.5 per-service-token `rl_val`/`rl_win` (default 120/60); L2 per-user `connect_id` (default 5/3s). Plus middleware `BotRateLimitMiddleware` (per-4-key, key `ragbot:botrl:{tenant}:{ws}:{bot}:{channel}:{bucket}` `bot_rate_limit.py:140`), `SourceRateLimitMiddleware`, `IpRateLimitMiddleware`, `AntiAbuseMiddleware` (app.py:518-559).

**T25.** `is_loadtest_bypass` → `middlewares/loadtest_bypass.py:44`: 3 gates — env `RAGBOT_LOADTEST_BYPASS_TOKEN` non-empty + header via `secrets.compare_digest` + peer ∈ `{127.0.0.1, ::1}` loopback (`:57-60`). SET in .env. Scope = anti-abuse 4xx counter + IP RL only; auth/RBAC/honeypot still apply (`:14-15`).

**T26.** Constraint permits same `(bot_id,channel_type)` across tenants (tenant in the unique key, T04); resolve always tenant-scoped. Test `tests/unit/test_4key_required.py`. Live DB has only 1 tenant with bots → not runtime-exercised → CHƯA VERIFY live.

**T27.** All 11 FKs → `bots(id)` are `ON DELETE CASCADE` (SQL: conversations, bot_model_bindings, tenant_model_policy, knowledge_edges, message_feedback, document_chunks, documents, messages, request_logs, semantic_cache, document_service_index); `document_chunks`+`document_service_index`→`documents` also CASCADE. **BUT** the repo does SOFT delete `is_deleted=True` (`bot_repository.py:302 soft_delete`), so CASCADE never fires → chunks/docs/cache/conversations **ORPHAN under a soft-deleted bot**. Hard `DELETE FROM bots` would cascade cleanly.

**T28.** `DELETE /admin/tenants/{id}` = SOFT delete, **409 if tenant still has bots** (`admin_tenants.py:9`), super-admin-only (`_require_super_admin :73`), emits audit + invalidates `TenantConfigCache` (`:21-27`). Repo `infrastructure/repositories/tenant_repository.py`. Hard GDPR erase path = `interfaces/http/routes/admin_gdpr.py`.

**T29.** Hidden per-tenant/per-bot switch = table `tenant_model_policy` (columns `record_tenant_id, record_bot_id, private_doc_ratio, web_search_ratio, general_knowledge_ratio, default_for_task, record_fallback_model_id, enabled`). Live: **0 rows = dormant/dead**. Admin route `admin_tenant_policy.py`.

**T30.** Flip RLS today (point `DATABASE_URL_APP`→`ragbot_app` NOBYPASSRLS), first breakages by evidence: (1) `ragbot_system` role MISSING in DB (SQL: 0) — if `DATABASE_URL_SYSTEM` also flipped, the outbox publisher + `BotRegistryService.bootstrap_cache` warm (both on `system_session_factory`, `bootstrap.py:481/499`) fail to connect. (2) `bootstrap_cache` warm calls `list_active(record_tenant_id=None)` — cross-tenant read; on the NOBYPASSRLS app role with no bound tenant at boot → **0 rows → empty bot cache** unless routed to the (missing) system role. (3) Any app session opened WITHOUT a bound `tenant_id_ctx` (ops scripts, background jobs, or a route that skips `bind_request_context`) → fail-closed 0 rows; plus `document_chunks`/`knowledge_edges` EXISTS-join policies require the parent tenant GUC to match or the whole child set vanishes.

---


## SECTION UP — (not captured)


Evidence gathering complete. Here is the full SECTION P answer block.

---

**SECTION P — Parse / Multi-format (P01–P35)** · cwd=`/var/www/html/ragbot` · live DB queried, deps checked (kreuzberg/pypdfium2/openpyxl/python-docx INSTALLED, docling MISSING), `parser_engine='kreuzberg'` live.

**P01.** Registry = `src/ragbot/infrastructure/parser/registry.py:45-61` `_REGISTRY` dict (8 keys). It is NOT a static MIME→class map; `detect_parser()` (`:97-120`) iterates the dict in insertion order and returns the first parser whose `.supports(mime,ext)` is True. Order: null → kreuzberg_markdown(pdf/pptx/html) → excel_openpyxl(xlsx) → google_sheets(csv/gsheet) → pdf(legacy) → docx → markdown(md/txt/plain) → vlm_image(images).

**P02.** Formats with parser+test+REAL live ingest (live `documents` n=26): **PDF** kreuzberg_markdown→pdf_parser, `test_parser_pdf.py`, 4 active; **CSV** google_sheets_parser, `test_sheets_row_as_chunk.py`, 6 active; **TXT/MD** markdown_parser, `test_markdown_parser_plain_text_support.py`, 8 `text/plain` active; **HTML** kreuzberg_markdown, 6 active. Code+test but **0 live ingest**: **DOCX** (`docx_parser.py`,`test_docx_parser.py`), **XLSX** (`excel_openpyxl_parser.py`,`test_excel_row_as_chunk_parity.py`), **PPTX** (kreuzberg_markdown, no dedicated test), **images** (`vlm_image_parser.py`, dormant).

**P03.** MarkdownParser = transform, NOT byte-passthrough: `markdown_parser.py:42-71` strips YAML front-matter (`_strip_front_matter`) then splits on H1/H2 (`_split_sections`); heading BYTES kept — heading line re-prepended as first line of each section chunk (`:69`). Verified run: `# Section One` retained verbatim in chunk-0 content.

**P04.** Canonical IR convergence fn = `src/ragbot/shared/tabular_markdown.py:214` `rows_to_structured_markdown()` (CSV/XLSX/Sheets/DOCX-tables → section-bound `##`+`| pipe |`) + `:358` `split_markdown_to_row_chunks()`. Text formats converge via `markdown_normalizer.py:43 normalize_to_markdown` (default OFF). NOT through the IR: PDF/PPTX/HTML (kreuzberg_markdown returns ONE flat markdown blob, `kreuzberg_markdown_parser.py:161-171`), plain MD/TXT (markdown_parser section split). So convergence is real only for TABULAR sources; prose formats stay parser-native markdown.

**P05.** Kreuzberg is an **in-process Python lib** (`import kreuzberg`), NOT a service — `docker-compose.yml` services = postgres/redis/api/infinity/tei-reranker only (no OCR/kreuzberg container). OCR real path = `infrastructure/ocr/kreuzberg_parser.py` (Tesseract `vie+eng`, `DEFAULT_KREUZBERG_OCR_LANGUAGE` `_18:136`); test `tests/unit/test_kreuzberg_parser.py`. kreuzberg INSTALLED (verified import).

**P06.** Docling = **DEAD residue**: `infrastructure/ocr/docling_parser.py` (167 LOC) still present + wired in `ocr_factory.py:77-81` + `DOCLING_PARSER_ENGINE_KEY` (`_13:16`) + `settings.py:273 provider Literal["docling","mistral"]="docling"`, but `docling` NOT installed (verified ModuleNotFoundError) and `parser_engine='kreuzberg'` live → never constructed. Selecting `docling` would raise ImportError at `DoclingParser.__init__`.

**P07.** OCR LIVE: `bootstrap.py:322 ocr = providers.Singleton(build_ocr_parser)` (`ocr_factory.py:47`); runtime call-site `document_worker.py:494 ocr = container.ocr(); parsed = await ocr.parse(source_url,...)` — but ONLY reached on the OCR-fallback branch (`:490 if not registry_routed and not full_text.strip()`), i.e. images/scanned only. `vlm_image_parser` never auto-fires (detect_parser no-arg probe raises TypeError, `registry.py:56-60`).

**P08.** XLSX = `excel_openpyxl_parser.py:79-95` walks **ALL** sheets (`wb.sheetnames`), `data_only=True, read_only=True`. Multi-sheet → **ONE** document; each sheet nested under `# <sheet_name>` heading only when `sheet_count>1` (`:90-93`). Then row-as-chunk split (`:115 split_markdown_to_row_chunks`).

**P09.** CSV delimiter: NO sniffing — `google_sheets_parser.py:83 csv.reader(io.StringIO(text))` uses default comma delimiter (BOM stripped `:39`, utf-8→latin-1 fallback `:36-44`). "700,000→700000" fix = `number_format.py:136-137` (exactly-3 trailing digits ⇒ thousands). **VERIFIED run**: `parse_money_vn('700,000')=700000`, `'1tr499'=1499000`, `'6 triệu'=6000000`. Test `tests/unit/test_query_range_parser.py` + `test_document_stats_parser.py`.

**P10.** Row-1-as-header flatten REPLACED by state machine `tabular_markdown.py:214-349`: header row detected by shape `_looks_header` (`:93`), empty header cell → `col{i+1}` placeholder (`open_header:229`), multi-row/split header merged (`:314-335 _is_header_continuation`+`_merge_header_fill`). Stage-2b empty-column / merged-cell forward-fill = `_normalize_rows:144-211`. Verified via 16-passing tabular tests.

**P11.** HTML → kreuzberg_markdown (`_KREUZBERG_MIMES` incl `text/html`, `kreuzberg_markdown_parser.py:46-56`); table→md handled by kreuzberg internally. NO explicit nav/footer strip in either path. Fallback `simple_text_parser.py:317-382 _parse_html_to_structured` (stdlib HTMLParser) converts `<table>`→md pipe (`:357-362`), `<h1-6>`→`#`, but also has NO nav/footer removal (renders all `handle_data` text).

**P12.** Sheets `edit?gid=N` guard = `google_link_service.py:195-219 to_export_url()`: rewrites viewer URL → `/export?format=csv&gid={N}` (gid PRESERVED `:211-213`) for Sheets, `format=docx` for Docs — prevents HTML-login-page OCR retry-storm. Called in worker `document_worker.py:405` BEFORE fetch. Private/login detected in `_check_access:259-285` + `_PRIVATE_INDICATORS:42`.

**P13.** No dedicated PDF text-vs-scan byte-sniff branch in the registry path — kreuzberg_markdown always runs `extract_bytes_sync` with `OutputFormat.MARKDOWN` (`kreuzberg_markdown_parser.py:129-141`); kreuzberg internally OCRs scanned pages. Legacy `pdf_parser.py:82-99` extracts text-layer per page, silently skips empty pages (`:98 if not page_text: continue`) → a pure-scan PDF yields 0 chunks there. Parse error surfaced as `ValueError` (`:137-141 / :120`). Live: 1 pdf in `state='failed'` but it failed at `current_step='enriching'` (content_chars=77803), i.e. parsed fine.

**P14.** Happy-case checker = `scripts/check_happy_case.py`; criteria are per-dimension Cards: heading-density ≥3 headings (`:131`), column-role coverage (`:162`), value-density ≥0.5 (`:182`), **price coverage ≥0.9 = PASS**, >0 but <0.9 = WARN, 0 = FAIL (`:196-202`). Imports the role-token SSoT from `document_stats.py` so spec/checker/parser can't drift (`:45`). It is a manual CLI script, not wired into ingest or CI.

**P15.** `normalize_to_markdown` (`markdown_normalizer.py:43`) called at RUNTIME only inside `document_service/__init__.py:854-862`, gated by `markdown_normalize_enabled`. 4-state: code default `DEFAULT_MARKDOWN_NORMALIZE_ENABLED=False` (`constants/_11:65`) / seed = not in `init_system_config.py` / **live DB = ABSENT** (queried) / comment "flip only after re-ingest validation" → effectively **OFF/dormant**. Also only normalizes the joined-text path, not parser row-chunks (`:851-853`).

**P16.** Low happy score → **silent**: `check_happy_case.py` is an offline operator CLI (`main:293`, prints verdict, `sys.exit(2)` only on bad args). NO runtime rejection/warn hook in the ingest pipeline — a low-quality doc still ingests. No blocker gate.

**P17.** Col-token SSoT file = `src/ragbot/shared/document_stats.py:175-225` (`_NAME_COL_TOKENS`, `_CATEGORY_COL_TOKENS`, `_PRICE_COL_TOKENS`, `_ALIASES_COL_TOKENS`, `_HEADER_EXTRA_TOKENS` union `_HEADER_EXACT_TOKENS`, `_AGGREGATE_TOKENS`→`_REJECT_NAME_TOKENS`). Out-of-scope doc: checker flags dimension, "customer fixes the SOURCE — we do NOT grow the parser per format" (`:169-170`). **DOMAIN-NEUTRAL FLAG**: these frozensets are hardcoded VN+EN vocabulary in engine code (`"dich vu","san pham","goi","combo","gia","don gia"` `:176-194`) + `CUSTOMER_CONTEXT_COLUMN_NAMES` VN literals `"Mô tả","Mo ta"` (`constants/_13:24-26`) — a per-language coupling in shared engine code.

**P18.** Post-U2 form = ONE structured-markdown string (`##`+pipe tables) for tabular, ONE flat markdown blob for pdf/html/docx. **VERIFIED real output** (ran `rows_to_structured_markdown`): `## Dịch vụ Spa cao cấp\n| Tên dịch vụ | Thời lượng | Giá |\n| --- | --- | --- |\n| Massage body | 60 phút | 700,000 |` … then `split_markdown_to_row_chunks` → one chunk per data-row, each carrying `## section`+header+separator+1 row. Live U2 step (`request_steps`, 1 ingest_parse row): `parser_provider='passthrough'`, mime `text/csv`, n_chars_in==out==266669.

**P19.** `parsed_blocks` = **dead-wire on the happy path**. Block type = `domain/entities/document.py:42 Block`; `BlockType` Literal `HEADING/TEXT/TABLE/FORMULA/IMAGE/CODE/LIST` (`shared/types.py:105`). **Producer**: ONLY the OCR fallback (`document_worker.py:500 parsed_blocks=list(parsed.blocks)`); the 6 registry parsers return `list[dict]` NOT `list[Block]`, so `registry_routed=True` ⇒ `blocks=None`. **Consumer**: `ingest_stages.py:597-615` — block pipeline gated `adapchunk_block_pipeline_enabled` (code default `=True` `_12:185`, DB ABSENT ⇒ runtime True) BUT `_analyze_blocks` only used `if ... and parsed_blocks` (`:612`); empty ⇒ falls to legacy `analyze_document(content)` text-flatten. Net: for every structured-format doc the Block-native pipeline is a **no-op** despite the flag being True.

**P20.** Embedded-image VLM = **dormant**. `vlm_image_parser.py`; built only via `document_worker.py:188 provider=_cfg.get("vlm_provider", DEFAULT_VLM_PROVIDER)`. 4-state: code default `DEFAULT_VLM_PROVIDER="null"` (`_00:167`) / seed absent / **live DB ABSENT** ⇒ `"null"` ⇒ `:189 if provider=="null": return None`. `vlm_caption_prompt` IS seeded live but unused while provider null. Never fires.

**P21.** `.txt` → MarkdownParser (`markdown_parser.py:20-30`, supports `text/plain`+`.txt`, degrades to single section when no `#`). CSV-detect misfire: `mime_sniff.py:154-161` routes ambiguous-declared UTF-8 text with `first_line.count(",")>=3` → `text/csv` → google_sheets parser. Only triggers when declared mime ∈ `{"","octet-stream"}` (`:44-48,120`); a `.txt` declared `text/plain` is trusted, so misfire is limited to no-ext/octet-stream comma-rich uploads.

**P22.** Non-UTF8 decode handled AT PARSE (no separate U2 decode stage): markdown `markdown_parser.py:101 decode("utf-8",errors="replace")`; CSV/Sheets `google_sheets_parser.py:41-44` utf-8→latin-1 fallback; simple_text `:106,109 errors="ignore"`. No BOM/encoding column recorded.

**P23.** `RAGBOT_STEP_PIPELINE.md` vs code deltas: (a) line 58 U2 lists parsers "pdf / excel / sheets / markdown / null" — **MISSING** docx, kreuzberg_markdown, google_sheets(csv), vlm_image (registry has 8). (b) line 60 U4 references `infrastructure/parser/chunk_strategy.py` which **does not exist** (verified `MISSING`) — real chunking lives in `shared/chunking/`. (c) Doc omits the dual parse path (worker parses via registry, then U2 sees `passthrough`).

**P24.** No `error_message` column on `documents` (schema cols verified: `current_step`, `metadata_json`, no error col). Failure detail lives in `jobs.error`. Live: 2 `state='failed'` docs (1 pdf, 1 csv) both failed at `current_step='enriching'` (content_chars 77803/266598) — **enrich-stage failures, NOT parse**. Parse errors raise `ValueError` at parser (`kreuzberg_markdown_parser.py:137`, `docx_parser.py:86-89`) and are caught to warn+OCR-fallback in worker (`document_worker.py:481-488`).

**P25.** Wide/merged-cell linearize = `tabular_markdown.py:_normalize_rows:144-211` (blank-head/tail trim, gap-K skip `DEFAULT_TABLE_GAP_ROWS=2`, leading-sparse forward-fill for merged/rowspan labels) + multi-row header merge `:314-335`. **Stress test RUNS**: `tests/unit/test_tabular_robustness_golden.py` + `test_parser_all_formats_structured.py` + `test_document_parser_strategy.py` → **16 passed** (ran).

**P26.** MD code block = kept as plain TEXT inside its H1/H2 section, NOT typed as CODE (registry MarkdownParser only splits on `#`/`##`, `markdown_parser.py:37-39`). **VERIFIED run**: ` ```python\nx = 700000 ` retained verbatim in section chunk. (CODE BlockType exists only in OCR adapters `kreuzberg_parser.py:63` / simple_text, not the registry markdown path.)

**P27.** YAML frontmatter = **DROPPED**: `markdown_parser.py:33-36 _FRONT_MATTER_RE` + `:42 _strip_front_matter` removes `---\n...\n---\n`. **VERIFIED run**: `title:/lang:` frontmatter absent from both output chunks.

**P28.** Golden datasets exist: `tests/eval/datasets/30Q_golden_{legalbot,medispa,thongtu}.json` (3 bots × 30Q) + `golden_set/` corpus + re-ingest scripts `scripts/retest_{spa,xe}_golden*.py`. A "9-file/3-bot re-ingest" run was **NOT executed here** (needs live ingest worker) → **CHƯA VERIFY** for actual re-ingest; datasets+scripts present on disk.

**P29.** Parser emits `request_steps`: yes — U2 wrapped in `ingest_core.py:314 async with _phase_d_step(step_tracker,"ingest_parse")`, sets `parser_provider/mime_type/n_chars_in/out` (`:337-343`). **Live proof**: 1 `ingest_parse` row (`status=success`, provider=`passthrough`, csv). Full ingest emits 7 steps live (ingest_parse/clean/chunk/enrich/vn_segment/embed_store/validate — queried). Note: worker-path shows `passthrough` because the worker parses via registry itself (`document_worker.py:456-467`) and passes `content=full_text, raw_bytes=None` (`:613-624`), so DocumentService U2 does NOT re-parse.

**P30.** Dead/dormant parser LOC (current config: kreuzberg installed, vlm=null, engine=kreuzberg): `docling_parser.py` 167 (dead — uninstalled) + `vlm_image_parser.py` 119 (dormant — provider null) + `pdf_parser.py` 122 (shadowed by kreuzberg_markdown for PDF; fires only if kreuzberg absent) + `simple_text_parser.py` 549 (OCR fallback reached only if kreuzberg import fails) ≈ **957 of ~2332 parser+ocr LOC ≈ 41% dormant/shadowed** in this deploy. Live: registry (kreuzberg_markdown, excel_openpyxl, google_sheets, docx, markdown, null) + ocr/kreuzberg_parser + ocr_factory.

**P31.** Multi-file / ZIP archive ingest = **KHÔNG TỒN TẠI**. No `.zip`/`extractall`/archive expansion in parser or ingest (grep clean). The only ZIP handling is OOXML single-file manifest peek (`mime_sniff.py:72-96`, `registry.py:135-141`) to distinguish xlsx/docx/pptx — one Office file, not a multi-doc archive.

**P32.** EN vs VI parse branch = **KHÔNG TỒN TẠI** in registry parsers (grep for language/lang in `parser/*.py` empty — parsers are language-agnostic). VN-specific logic is downstream, not parse: OCR `simple_text_parser.py:30-33 _VN_SECTION_RE` (Phần/Chương/Mục/Điều), stats `document_stats.py:175-225` VN col-tokens, `number_format.py` VN money suffixes. Parser output identical regardless of doc language.

**P33.** `text_processing.py` (`document_service/text_processing.py`, 201 LOC) vs `infrastructure/text_normalizer/` — different layers, minimal overlap: former = ingest-time hyphenation-fix/injection-strip/block-type→chunk-type map (imports `shared/text_normalization.normalize_vn`, `PROMPT_INJECTION_PATTERNS` `_10:69`); latter = swappable Port+registry strategy (`bartpho_accent_normalizer`/`null_normalizer`, default provider `"null"` `_13:80`). No duplicated regex; `text_processing` is the live inline path, `text_normalizer` registry is a separate opt-in DI strategy.

**P34.** FORMULA: no value-vs-formula distinction. FORMULA/IMAGE `BlockType` (`types.py:105`) produced ONLY by OCR kreuzberg adapter mapping (`kreuzberg_parser.py:57-64 _BLOCK_TYPE_MATCHERS` "formula"/"equation"→FORMULA, atomic `:68`) — its `.text` content is stored verbatim, no LaTeX/rendered split. Registry parsers never emit FORMULA. Consumer = `narrate_service.py` (`("TABLE","FORMULA","IMAGE")` `:99`) which is behind `narrate_provider` (DB ABSENT ⇒ dormant). So formula blocks are dead-in-deploy (OCR path only + narrate off).

**P35.** Keep-3 / cut-formats usage decision = **KHÔNG TỒN TẠI** (no governance doc/ADR; grep hit was rerank `min_keep=3`, unrelated). Live usage evidence (`documents` n=26): `text/plain`×8, `text/csv`×6, `text/html`×6, `application/pdf`×4; **pptx/docx/xlsx/images = 0 live ingest**. So the 4 truly-exercised formats are txt/md, csv, html, pdf — docx/xlsx/pptx/image parsers ship + test-covered but unused in this corpus.

---


All evidence gathered. Here is the CL section answer block.

```markdown
## SECTION CL — Clean / Normalize (evidence: cwd=/var/www/html/ragbot, real code + runtime SQL/py)

CL01. `_stage_u3_clean` — ingest_stages.py:274 (`_StageChunkMixin`). Transform ORDER, two-stage chain + LLM tail:
  (a) **CleanBase Tier-0** `sanitizer.sanitize()` (infrastructure/safety/sanitizer.py:98-155), gated `cleanbase_tier0_enabled` AND `getattr(self,"_sanitizer")` wired (ingest_stages.py:310-312): HTML-tag strip→space → Unicode **NFC** → zero-width/BOM remove → prompt-injection→`[REDACTED]`.
  (b) **`_clean_document_text`** (text_processing.py:78-105), gated `ingestion_cleaning_enabled` (ingest_stages.py:296-297,341-342): `normalize_vn`(NFC) → `_fix_hyphenation` → `_strip_prompt_injection` → collapse `\n{3,}`→`\n\n` → strip repeated header/footer lines (Counter, count≥3 & len<100, only when >10 lines) → collapse `[ \t]+`→' ' → `.strip()`.
  (c) LLM metadata-extract `_extract_metadata_llm` (ingest_stages.py:380-381) if `metadata_extraction_enabled`. NOTE order-drift: `metadata_extraction_enabled` code default=False (ingest_stages.py:377) / seed "false" (init_system_config.py:234) / **live DB=True** — runs in prod.

CL02. Patterns = `PROMPT_INJECTION_PATTERNS` constants/_10_rbac.py:69-80 (6 EN + 3 VN + 1 chatML `<|im_start|>`). Applied TWICE: Tier-0 sanitizer.py:143 + `_strip_prompt_injection` text_processing.py:68-75, both →`[REDACTED]`. Test: tests/unit/test_p26_l1_prompt_injection_filter.py (EN/VN/case-insensitive/im_start). **"ignore previous" nuance**: regex requires trailing noun `(instruction|prompt|direction|rule)s?` (line 70) → `"ignore previous instructions"`→REDACTED, but bare `"ignore previous"` / `"ignore the water bottle"` does **NOT** match → goes into chunk verbatim (proven by `test_legitimate_content_preserved` line 31-36 + own run).

CL03. `_fix_hyphenation` text_processing.py:35-52, regex `([A-Za-zÀ-ỹ])-\n([A-Za-zÀ-ỹ])` (only ALPHABETIC incl VN around hyphen+newline). RAN: `ABC-\nDEF`→`ABCDEF` (letter-code across line break = **BROKEN/glued**); `700-\n000`→**unchanged** (digits not in class = SAFE); `thông-\ntin`→`thông tin` (VN keeps space, ctx-scan line 44-49); `infor-\nmation`→`information`. Risk limited to alphabetic hyphenated codes spanning a physical line break; numeric/alnum codes untouched.

CL04. Clean does NOT strip pipes, but `re.sub(r"[ \t]+"," ",text)` text_processing.py:104 collapses intra-cell padding — RAN: `| x     | y |`→`| x | y |`. Structure/pipes preserved (harmless for md parse); alignment whitespace lost. Secondary risk: repeated-line strip (line 99) could drop an identical row/separator only if it recurs ≥3× AND len<100 in a >10-line doc.

CL05. `ZERO_WIDTH_CHAR_REGEX` constants/_19_ekimetrics_selector.py:92-94 (U+200B-200F, U+202A-202E, U+2060, U+2066-2069, U+FEFF; U+00AD soft-hyphen intentionally KEPT). Stripped ONLY in Tier-0 sanitizer.py:138 (gate `cleanbase_tier0_enabled`, code-default True). `_clean_document_text` does **NOT** strip zero-width. RAN both stages on `a\x00b\x07c\u200bd\ufeffe`: Tier-0→`a\x00b\x07cde` (zw_removed=2) — **ASCII C0 control chars U+0000 NUL / U+0007 BEL SURVIVE both Tier-0 and legacy clean**. FLAG: no C0-control scrub anywhere in the clean path.

CL06. `content_hash = hashlib.sha256(content.encode()).hexdigest()` ingest_core.py:420 — computed **BEFORE** `_stage_u3_clean` (called ingest_core.py:579). ⇒ hash is on RAW pre-clean parsed content. Dedup gate ingest_core.py:428-453 (`WHERE record_bot_id AND content_hash AND deleted_at IS NULL`→`DocumentDuplicateError`), skipped on re-index. Consequence: whitespace-only-different docs → different raw hashes → **NOT deduped** (both ingest). Clean/normalize never feeds the doc-dedup hash. (Chunk-level hash ingest_core.py:599 is separate, on *enriched* text.)

CL07. BOTH surfaces, ingest is default-DEAD. **Ingest**: `_maybe_redact_pii` ingest_helpers.py:260-342, called ingest_core.py:348; composite gate `recap_pii_enabled` (system, DEFAULT_RECAP_PII_ENABLED=False constants/_19:108, not seeded, absent in live DB) **AND** per-bot `plan_limits.pii_redaction_enabled` (default False); redactor provider `DEFAULT_PII_REDACTOR_PROVIDER="null"` (_13_adapchunk_ocr_parser.py:100 → NullPiiRedactor passthrough, bootstrap.py:452). Live = NO-OP. **Observability**: step_tracker.py:154-172 `redact_mapping`/`redact_text` on `request_steps` metadata+error (surface=request_steps). Not tied to the LLM answer text (sacred #10 — no output-answer rewrite).

CL08. NOT silent-raw. Body exception in U3 propagates: `_phase_d_step` captures it in `_body_propagate` and re-raises (ingest_phases.py:227-245,266-267 — only tracker/observability errors swallowed). Bubbles to `handle_document_uploaded` outer except → `job_repo.update_status(status="failed", error=str(exc))` document_worker.py:706 + `DocumentFailed(error_code="INGEST_FAILED", error_message=str(exc))` outbox (lines 711-723). Chunk/embed never run ⇒ 0 chunks persisted. **CAVEAT/FLAG**: documents row was INSERTed `state='active'` at U2 (ingest_core.py:504) BEFORE U3; the clean-crash path marks the JOB failed but does NOT flip `documents.state` to 'failed' (that UPDATE lives only in the store stage, ingest_stages_store.py:503,539) → doc row can linger `state='active'` with 0 chunks (state inconsistency).

CL09. `segment_vi_compounds` shared/vi_tokenizer.py:117 → `_init_tokenizer` line 70. **ImportError** branch line 101-103: `_tokenize_fn=None` + `logger.info("vi_tokenizer_loaded", backend="fallback_lowercase")` (INFO, **not warn**); then segment returns text unchanged with `logger.debug("vi_compound_segment_no_backend")` line 170-172. WARN only for: non-import warmup fail (AttributeError/OSError/ValueError/RuntimeError, line 104-113 `vi_tokenizer_warmup_failed`), oversize skip (line 162 `vi_compound_segment_skipped_oversize`), per-chunk raise (line 184-190 `vi_compound_segment_failed`). So import-fail = **silent null fallback** (content_segmented=original), ingest continues; U6 wrapper `_stage_u6_vn_segment` ingest_stages.py:954, per-chunk seg errors already caught via `return_exceptions=True` (ingest_stages_enrich.py:364-373).

CL10. The DI slot `infrastructure/text_normalizer/` is **DEAD** — `bartpho_accent_normalizer.py:1-57` fully commented under DEAD-CODE NOTICE 2026-06-03 ("text_normalizer never wired in production path"), `registry.py:45-80` all commented, zero bootstrap caller (grep `build_text_normalizer` = 0 hits). No LIVE regex there. BUT the runtime clean/normalize path IS VN-hardcoded: `_VIETNAMESE_DIACRITICS_RE = re.compile(r'[À-ỹ]')` text_processing.py:32 (+ `[A-Za-zÀ-ỹ]` hyphenation line 52); `PROMPT_INJECTION_PATTERNS` VN phrases constants/_10_rbac.py:76-78 (`bỏ qua…hướng dẫn`, `quên đi…`, `tiết lộ…`); shared/markdown_normalizer.py:7 VN legal markers `Chương/Mục/Điều`; shared/vi_tokenizer.py:221 `_VI_ABBREVIATIONS_SEED` (VN word list). Domain-neutral FLAG: language-coupled VN literals sit in the live clean path, gated only by `VI_DOMAIN_LANGUAGES` for the segmenter — hyphenation + injection regex run VN patterns unconditionally.

### 4-state flag table (clean/normalize)
| flag | code default | seed | live DB | comment |
|---|---|---|---|---|
| cleanbase_tier0_enabled | True — `DEFAULT_CLEANBASE_TIER0_ENABLED` constants/_20_cag_mode_cache_augmented_gen.py:40 | NONE (not in init_system_config.py) | absent → code True | ingest_stages.py:282 "Opt-out via system_config.cleanbase_tier0_enabled" |
| ingestion_cleaning_enabled | True (literal, ingest_stages.py:297) | "true" init_system_config.py:231 | **True** | seed desc says "NFKC" but code uses **NFC** (`normalize_vn`) — comment DRIFT |
| recap_pii_enabled | False — `DEFAULT_RECAP_PII_ENABLED` constants/_19:108 | NONE | absent → False | system kill-switch, composite with per-bot |
| pii_redactor_provider | "null" — `DEFAULT_PII_REDACTOR_PROVIDER` constants/_13:100 | NONE | absent → "null" | NullPiiRedactor passthrough (ingest PII dead) |
| metadata_extraction_enabled | False (ingest_stages.py:377) | "false" init:234 | **True** | live overrides ON — DRIFT (LLM metadata runs in prod) |
```

---


I have all the evidence needed. Here is the complete CH section audit.

---

**SECTION CH — Chunking / AdapChunk (CH01–CH40)** — evidence from live code + `ragbot_v2_dev` DB (26 docs / 906 chunks, queried this session).

CH01. `smart_chunk()` = `src/ragbot/shared/chunking/__init__.py:412`; sig `smart_chunk(text, chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP, strategy=None, with_metadata=False, *, table_strategy=DEFAULT_TABLE_STRATEGY) -> list` → `list[str]` (or `list[dict]` when `with_metadata=True`). LIVE: called `ingest_stages.py:770`.

CH02. Strategy→fn→file: recursive→`_chunk_recursive_with_tables` strategies.py:64 · hdt→`_chunk_hdt` strategies.py:277 · semantic(lexical)→`_chunk_semantic` strategies.py:416 · proposition→`_chunk_proposition` strategies.py:631 · hybrid→`_chunk_hybrid` strategies.py:702 · table_csv→`_chunk_table_csv_with_context` csv_chunker.py:251 · table_dual_index→`_chunk_table_dual_index` csv_chunker.py:357 (+ async `_chunk_semantic_embed` strategies.py:487). Tested: `tests/unit/test_chunking_strategies.py` (49 test fns), plus per-strategy `test_chunk_table_csv.py`(9)/`test_chunk_table_dual_index.py`(5)/`test_embedding_semantic_chunk.py`(36).

CH03. `analyze_document(text: str)` analyze.py:215 — input is **flat text (str)**, confirmed (`lines = text.split("\n")` L221). Block-aware variant `analyze_document_blocks(blocks)` analyze.py:318 exists but only runs when `ctx.blocks` non-empty (dormant — see CH27).

CH04. `select_strategy` analyze.py:407. Rule table: (1) `is_csv AND total_headings==0 AND vn_markers==0` → `table_strategy` conf 1.0 (L454); (2) `total_headings+vn_markers ≥ 3` → HDT conf 1.0 (L462); (3) ekimetrics path if flag+text (L473, OFF); else weighted scorer `DEFAULT_STRATEGY_WEIGHTS` (_12…:189) over hdt/semantic/recursive/hybrid/proposition (L489-532); `best=max(scores)`, if `<DEFAULT_STRATEGY_MIN_CONFIDENCE`(0.45) → recursive (L538).

CH05. L5 cross-check = `apply_cross_check` analyze.py:576, 5 priority rules (low-conf→hybrid / hdt-few-headings→semantic / semantic-short→proposition / proposition-long-structured→hdt / mixed-warn-only). Original-vs-final logged event `adapchunk_l5_strategy_overridden` with `original_strategy`+`override_strategy` at `__init__.py:471-480`; ingest records final in `request_steps.metadata_json.strategy_used` (ingest_stages.py:851).

CH06. **MEASURED** `SELECT metadata_json->>'chunking_strategy', count(DISTINCT record_document_id), count(*)`: `recursive`=9 docs/689 chunks, `hdt`=6 docs/217 chunks. **DEAD in live corpus** (0 docs): semantic, proposition, hybrid, table_csv, table_dual_index, parser_preserve, whole_document, parent_child. (request_steps has only 1 `ingest_chunk` row = recursive.)

CH07. `_chunk_table_csv` csv_chunker.py:22 — one chunk per data row, header prepended (`f"{header}\n{row}"` L48). **No row-count cap**; only per-chunk char cap `DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS=1500` (oversized row kept whole + warn L49-55). No "2643-row limit" exists in code — CHƯA VERIFY that specific number (not a live constant).

CH08. Recursive separators = `["\n\n","\n",". "," ",""]` strategies.py:131. Heading respect: `_split_h1_sections` strategies.py:42 hard-breaks at `# ` before recursive split (H1 sections never share a chunk). `_prefix_section_headings` __init__.py:375 re-attaches nearest `##` to severed chunks.

CH09. HDT tree `_chunk_hdt` strategies.py:277; oversize split when `len(chunk) > chunk_size*2` **chars** (L338, not tokens). `structural_path` format `[H1 > H2 > H3]` → stored `{full, parts}`. **3 real DB examples** (metadata_json→structural_path→full): "Tóm tắt & danh sách đầy đủ — bot spa > Có những dịch vụ / sản phẩm gì? (tóm tắt)"; "…> Liệt kê chi tiết… > BẢNG GIÁ DỊCH VỤ CHĂM SÓC DA…"; "…> Dịch vụ gội đầu".

CH10. CONFIRMED misleading name: `_chunk_semantic` strategies.py:416 uses `_sentence_similarity`→`lexical_similarity` (SequenceMatcher+Jaccard, strategies.py:365-376) — **lexical, NOT embedding**. Embedding version = separate `_chunk_semantic_embed` strategies.py:487 (async, flag `embedding_semantic_chunk_enabled`, dormant).

CH11. `_chunk_proposition` strategies.py:631 — rule-based: split sentences then COORDINATING connectors only (`;/—/,và/,hoặc/,nhưng/,and/,or/,but`) L661-666; subordinating (nếu/vì/khi) deliberately kept attached; clauses > `DEFAULT_CHUNK_MIN_CLAUSE_LEN=20` chars grouped to chunk_size.

CH12. `_chunk_hybrid` strategies.py:702 = HDT macro (chunk_size*2) + proposition micro when `word_count > proposition_threshold`. Constant `DEFAULT_PROPOSITION_THRESHOLD_WORDS=300` (_07…:97).

CH13. **RUNTIME = `shared/chunking/`** (called ingest_stages.py:770). `infrastructure/chunking_strategy/` (registry.py, llm_resolver.py, rule_resolver.py) = **DEAD/orphan** — 0 src callers; imported only by `tests/unit/test_llm_chunking_strategy_resolver.py` + `test_chunking_strategy_constants_pin.py`.

CH14. No real overlap/dup: `csv_chunker.py` IS the `table_csv` family (`_chunk_table_csv`, `_chunk_table_csv_with_context`, `_chunk_table_dual_index`). The strategy string `"table_csv"` dispatches to `_chunk_table_csv_with_context` (__init__.py:503). One implementation, no shadow module.

CH15. `tenant_style.py` — **domain-neutral, NO per-tenant hardcode**: `apply_tenant_style(text, *, heading_uppercase_promote=False, table_separator="")` tenant_style.py:76; both knobs default OFF = byte-identity no-op; owner config via `plan_limits.chunking_config.style_profile`. LIVE caller ingest_stages.py:467.

CH16. parent=`DEFAULT_PARENT_CHUNK_SIZE=1024` (_10…:268), child=`DEFAULT_CHILD_CHUNK_SIZE=256` (:269), child_overlap=`DEFAULT_CHILD_CHUNK_OVERLAP=50` (:270) — **the "128" is `DEFAULT_CHUNK_OVERLAP` (flat path), NOT child overlap**. Resolve chain ingest_stages.py:496-503 (`_cfg.get_int` → constant fallback). **4-state DRIFT on `parent_child_enabled`**: code-default=`False` (ingest_stages.py:483), seed=`"true"` (scripts/init_system_config.py:84), **live DB=`False`**, comment "Bật/tắt parent-child chunking". → parent-child is OFF in production.

CH17. **MEASURED**: `parent_chunk_id IS NULL` for **all 906** chunks; `embedding IS NULL` for **0** (all embedded). No parent/child rows exist (parent_child_enabled=False) → the "parent embedding NULL, child NOT NULL" design is **dormant**, not exercised.

CH18. Orphan child = **0** (no `parent_chunk_id` set anywhere; all chunks are flat top-level). N/A in live corpus.

CH19. `expand_parent_chunks` query_graph_helpers.py:46, wired query_graph.py:2707 + retrieve.py:232 — **LIVE but no-op** (reads `chunk.get("parent_chunk_id")` L54, always None in this corpus). Flat-corpus neighbor expansion via `neighbor_expand.py` groups by shared parent_chunk_id (also null). Context-cap: applied downstream in generate; expand is dormant here.

CH20. Overlap unit = **chars, not tokens**. `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap=…)` strategies.py:128 uses default `length_function=len` (no tokenizer passed). `DEFAULT_CHUNK_OVERLAP=128` chars (_00…:60). Not measured against any token model.

CH21. Not "<20 tok" — orphan merge threshold is **100 chars** (`DEFAULT_CHUNK_ORPHAN_THRESHOLD=100`, _00…:64) via `merge_orphan_chunks` __init__.py:597 (caller ingest_stages.py:800, skipped for row-atomic strategies). Separate proposition min-clause `DEFAULT_CHUNK_MIN_CLAUSE_LEN=20` chars (skip tiny fragments). `chunk_quality`/`quality_score` computed at ingest_stages_enrich.py:578, persisted `metadata_json.quality_score`.

CH22. **MEASURED** metadata `structural_path.full` = one leaf section per chunk (single `[H1>H2>H3]` path); multi-section chunks appear only where HDT flushes one section = intended. Sample doc `24fc5081` shows H2 "Mục lục…" re-prefixed across idx 0-5. No cross-section blob observed.

CH23. `coverage.py::check_chunk_gaps` __init__ via ingest_stages.py:889-890 (OBSERVE-only, never raises). Gate is `coverage_ratio ≥ 1.0 - tol`, `DEFAULT_COVERAGE_TOL=0.02` → **≥0.98** char-coverage (NOT 0.8). `DEFAULT_MIN_LOCATABLE_CHARS=2`. On gap → warns `chunk_char_coverage_gap`. Numeric superset gate `find_dropped_numbers` ingest_stages.py:869.

CH24. **MEASURED**: 1 doc with index anomaly — `24fc5081` (legal-00-summary, 12 chunks) has **duplicate** chunk_index 0-5 (each appears twice, identical content) → a re-ingest/version dup, not a gap. Other 25 docs: continuous.

CH25. Re-chunk on size change: `rechunk_document.py` use-case + `rechunk_by_document_id` (tests test_rechunk_by_document_id.py, test_rechunk_validate_before_wipe.py — validate-before-wipe). No automatic re-chunk on constant change; old docs keep old chunking until manual re-ingest (drift real).

CH26. `blocks.py` types = text/table/formula/image/code (`_ATOMIC_BLOCK_TYPES` blocks.py:146). `smart_chunk_atomic` __init__.py:653 callers (grep): **tests only** (`test_smart_chunk_atomic.py`); `test_block_feed_s1_plumbing.py:51` asserts it is NOT wired ("S1 is plumbing only"). → **NOT LIVE**; production always uses text-based `smart_chunk`.

CH27. `adapchunk_block_pipeline_enabled` 4-state: code-default=**True** (`DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED` _12…:185), seed=**absent** (not in init_system_config.py), live DB=**no row → resolves True** (ingest_stages.py:560-567), comment "Default ON… degrades gracefully". But it feeds an **empty block stream** (CH28) so it's effectively a no-op branch.

CH28. ON + no blocks: ingest_stages.py:597 `parsed_blocks = list(ctx.blocks or [])`; `ctx.blocks` populated ONLY on OCR-fallback path (document_worker.py:500 `parsed.blocks`), else None. When empty: L612 `if _analyze_blocks and parsed_blocks:` is False → falls to `analyze_document(content)` (L615) — same text profile as legacy path. Then still calls text `smart_chunk` at L770 (never `smart_chunk_atomic`). → block pipeline ON = **byte-identical strategy selection to OFF** in practice.

CH29. MD table split: `_split_into_blocks` blocks.py:67 keeps table lines atomic; oversized (>chunk_size*3) split by row-groups **with header re-prepended** (strategies.py:142-166). **MEASURED** chunk_type dist: `table`=631, `text`=275 chunks; e.g. `xe-1`=207, `lốp`=187 chunks are table-row-heavy. No mid-row cut observed.

CH30. **MEASURED — no FORMULA/IMAGE/CODE blocks in real corpus**: `metadata_json.block_type` = TABLE(619)/TEXT(113) only; 0 chunks match `$$` formula, 0 match markdown-image. `DEFAULT_ATOMIC_BLOCK_TYPES={TABLE,FORMULA,IMAGE,CODE}` (_00…:138) but formula/image/code paths are dormant (no such source docs).

CH31. Cost to fully wire block pipeline: parser adapters must emit typed `Block` stream on the **normal** (non-OCR) registry path (today only OCR fallback sets `parsed.blocks`), + flip `smart_chunk_atomic` into `_chunk_text_blocks_to_chunks` at ingest. Currently `smart_chunk_atomic` unused; `attach_context_buffer` (context_buffer.py:110) + `analyze_document_blocks` ready but starved of input. Estimate = wire parser→Block emit + swap chunk call-site (ingest_stages.py:770); no new infra.

CH32. `infrastructure/doc_profile/` (rule_based_doc_profile.py, null, registry) → `DocumentProfile` entity via `build_doc_profile_analyzer` (ingest_stages.py:704). Gated `adapchunk_layer3_doc_profile_enabled` code-default=**False** (_18…:73), live DB=no row→False → **dormant/telemetry-only**; comment L681 "NOT yet wired into select_strategy". Parallel to `analyze_document` dict; not consumed by selector.

CH33. Narrate: `NarrateService` (narrate_service.py:84) wired sync.py:492 + document_worker.py:570 → `narrate_dispatch.dispatch` before embed. Gate `narrate_then_embed_enabled` code-default=**False** (_20…:81), **live DB=False** → **OFF at runtime**. VN text is a **locale-keyed pack** `_26_narrate_prompt_locale_pack.py` (`_default` = "SAME language, do NOT translate" language-agnostic L40-56; `vi` key L60 holds Vietnamese) — config pack, not engine hardcode (sacred#10-safe).

CH34. Enabling block pipeline (with real Block emit) → 3 regression risks: (1) atomic TABLE emitted as standalone Chunk with `context_before/after` may **change chunk boundaries** vs current row-as-chunk table_csv (retrieval recall shift); (2) `smart_chunk_atomic` defaults strategy to `"HYBRID"` (__init__.py:740) when None → different splits than current recursive/hdt; (3) `_chunk_text_blocks_to_chunks` re-joins with `"\n\n"` (L838) losing exact source offsets → coverage-gate false gaps. All CHƯA VERIFY by load test (pipeline dormant).

CH35. VN legal regex `_VN_HEADING_DETECT_RE` vn_structural.py:107 (Chương|Phần|Mục|Điều + numeral). Mispromote guard: `promote_vn_hierarchical_headings` fires only when `≥ DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES=3` line-start markers (vn_structural.py:300) — a casual "Điều 1" FAQ mention is NOT promoted. Markers now locale-resolved via `DEFAULT_STRUCTURAL_MARKERS_BY_LANG` (non-VN bot → empty/EN set, no VN leak).

CH36. Breadcrumb match: chunker writes `[Chương N > Mục M > Điều K. <title>]`; `build_vn_structural_like_clauses` vn_structural.py:262 emits 4 boundary-safe LIKE patterns (`%{prefix} {num}.%`, `]%`, `,%`, ` >%`) so "Điều 56" ≠ "Điều 561". Regression pin `tests/unit/test_vn_structural_anchor.py` (9 tests, incl `test_build_like_clauses_match_real_chunker_breadcrumb_header`).

CH37. Structural LIKE retrieve end-to-end: `detect_vn_structural_anchor` → `build_vn_structural_like_clauses` **LIVE** at retrieve.py:1161-1164 + query_graph.py:1799-1802. Single-anchor "Điều 56" → pre-filter; multi-anchor query → None (no pre-filter). Test coverage `test_vn_structural_anchor.py`.

CH38. **CHƯA VERIFY** "chunk-289": no chunk numbered 289 in live DB (max chunks/doc = `xe-1`=207). Legal corpus = `thongtu-09-2020` (92 chunks, hdt strategy, all embedded, state active) + `legal-00-summary.md` (12 chunks). No "chunk-289 fail" artifact in DB; likely a historical eval-question ID, not a stored chunk.

CH39. EN docs structural_path fairness: markdown `#/##/###` HDT path (`_chunk_hdt`) is language-neutral → EN docs with markdown headings DO get `structural_path`. But VN legal PROMOTION (Chương/Mục/Điều) is VN-only; EN legal "Chapter/Article" markers resolve via `resolve_struct_markers('en')` only if caller passes lang (module-level `_STRUCT_MARKERS` uses default `vi` — vn_structural.py:76). **MEASURED**: no EN structural_path in live corpus (all VN). Non-VN prose without `#` headings → recursive, no path (fairness gap for EN plain-text legal).

CH40. Test-count by layer (grep `def test_`): **most-trusted** = `test_chunking_strategies.py`(49), `test_adapchunk_l5_crosscheck.py`(18), `test_table_taxonomy.py`(17), `test_chunk_coverage_gate.py`(27), `test_embedding_semantic_chunk.py`(36). **thinnest/dormant** = `test_smart_chunk_atomic.py`(7, code NOT wired), `test_adapchunk_b2_block_pipeline.py`(5, dormant path), `test_block_feed_s1_plumbing.py`(3, asserts non-wiring), `test_document_service_block_pipeline_wired.py`(2). → The heavily-tested layer (legacy `smart_chunk` strategies + L5 + coverage) is the LIVE one; the Block/atomic pipeline is thinly tested AND dormant.

---
**Cross-cutting flags (4-state summary)** — code-default / seed / live-DB / comment:
- `adapchunk_layer5_cross_check_enabled`: True (_12…:149) / absent / **True** (DB) / "active by default"
- `adapchunk_block_pipeline_enabled`: True (_12…:185) / absent / no-row→True / "Default ON"
- `formula_image_atomic_protect_enabled`: False (_00…:126) / absent / no-row→False / OFF
- `adapchunk_layer3_doc_profile_enabled`: False (_18…:73) / absent / no-row→False / telemetry-only
- `ekimetrics_5metric_selector_enabled`: False (ingest_stages.py:576) / absent / no-row→False
- `parent_child_enabled`: **False** (ingest_stages.py:483) / **"true"** (init_system_config.py:84 — DRIFT) / **False** (DB) / "Bật/tắt parent-child"
- `narrate_then_embed_enabled`: False (_20…:81) / absent / **False** (DB)
- `table_csv_emit_header_footer_chunks_enabled`: (constant) / absent / **True** (DB)

**Domain-neutral flags**: no brand/tenant literal found in chunking engine. VN legal vocabulary is locale-resolved (`DEFAULT_STRUCTURAL_MARKERS_BY_LANG`, vn_structural.py:76) and narrate prompts are locale-packed (`_26_narrate_prompt_locale_pack.py`) — the only VN literals are the `vi`-keyed config entries, not hardcoded engine branches.

---


I have comprehensive evidence. Here is the answer block for SECTION EN.

## SECTION EN — Enrich / VN-segment (EN01–EN12)

**EN01.** 3 flags, 4-state each — all three OFF live, seed drifted TRUE:
- `contextual_retrieval_enabled`: **code**=`False` (`shared/constants/_11_table_csv_chunking_strategy.py:111`); **seed**=`"true"` (`scripts/init_system_config.py:102`) — **DRIFT**; **live DB**=`false` (updated 2026-05-05 21:41); **comment** (`_11:107-110`)="Default OFF: per-chunk nano nhồi full-doc = O(n²) storm; Jina late_chunking cấp ngữ cảnh 0 LLM; xem alembic 0228".
- `enrichment_enabled`: **code**=`False` (`config/settings.py:322` `EnrichmentSettings.enabled`); **seed**=`"true"` (`init_system_config.py:90`) — **DRIFT**; **live DB**=`false` (2026-05-05); **comment** (`settings.py:318-321`)="redundant with Jina late_chunking, part of O(n²) storm".
- `narrate_then_embed_enabled`: **code**=`False` (`shared/constants/_20_cag_mode_cache_augmented_gen.py:81`); **seed**=absent from `init_system_config.py`; **live DB**=`false` (2026-06-16 17:32); **comment** (`_20:72-81`)="Default OFF — value now matches docstring (was True, a contradiction)".
- (companion) `cr_enhanced_enabled` code=`False` (`_11:145`)/live=`false`; `vi_compound_segmentation_ingest_enabled` code=`True` (`_12_multi_stage_retrieval_fallba.py:48`)/seed=`true`/live=`true`; `enrich_row_gate_enabled` code=`True` (`_11:178`)/not-in-DB→default.

**EN02.** Partial no-op, NOT skip. When all 3 OFF, `_stage_u5_enrich` still fully executes (`ingest_stages_enrich.py:120`): `cr_active=False`→`enriched_chunks=list(chunks)` no-op (`:509-531`), but `score_chunk_quality` runs on every chunk (`:578-594`, observability-only), Phase-D row recorded (`:559`), persist decision (`:610-616`). Cleaning is a SEPARATE prior stage `_stage_u3_clean` (`ingest_core.py:579`) that always runs — enrich OFF does not touch it.

**EN03.** CR prompt = fixed system+user template `contextual_chunk_enrichment.py:45-59`; one `litellm.acompletion` per chunk `:177`, `max_tokens=max_context_tokens` (=100, `DEFAULT_CR_CONTEXT_MAX_TOKENS`), full-doc as cache-controlled system block. Model = `contextual_retrieval_model` live DB = **`"openai/claude"`** (malformed wire name — would 400/fail→return original chunk `:234-241`). Cost/doc when ON = n_chunks×1 LLM call, ~90% input discount after warm-up (`_11:156-163`), but **runtime cost=0 today** (flag OFF, 0 rows `content LIKE '<chunk_context>%'` in DB).

**EN04.** Reason = O(n²) token-storm + Jina late_chunking supersedes (comments `ingest_stages_enrich.py:201-210,232-242,445-452`; cited storm numbers: "1 doc=1.54M tok/~8min/saturates 200k TPM"). **Alembic 0228/0230/0231 referenced in comments DO NOT EXIST** — history squashed at `alembic/versions/20260618_squash_baseline.py` (33 files total, none touch these keys). **A/B recall-lift numbers = CHƯA VERIFY** (no eval report found; only storm/cache % in comments). **FLAG:** the "Jina late_chunking supersedes" justification is **STALE** — Jina bindings dropped (`alembic/versions/20260626_drop_jina_bindings.py`), live `embedding_provider="zeroentropy"`, and `late_chunking` is a **Jina-only** feature (`infrastructure/embedding/jina_embedder.py:308,389`; not in zeroentropy path) → `late_chunking_enabled=true` (live) is a **no-op** for the current embedder; the 3 disabled paths have no active replacement.

**EN05.** Per-bot enable: **only `cr_enhanced_enabled`** resolves per-bot via `resolve_bot_limit(bot,"cr_enhanced_enabled",...)` (`ingest_stages_enrich.py:274-281`, `plan_limits`). `contextual_retrieval_enabled` + `enrichment_enabled` are **system_config GLOBAL only** — no per-bot resolve (`:162,456` read `self._cfg.get` unconditionally, no bot fallback). **Re-ingest REQUIRED** — enrichment happens at ingest U5; existing chunks keep old `content`. Steps: set `plan_limits.cr_enhanced_enabled=true` on the bot (+ wire `chunk_context_enricher`, live in worker `document_worker.py:596-606`) → re-ingest the doc.

**EN06.** 
- `embedding_text` = **INGEST** concern (U7), not enrich; caller `_stage_u7_embed_store` → `_resolve_embedding_text_strategy_name` (`ingest_stages_store.py:215`) + `build_embedding_text_strategy` (`:236`); default `"prefix_plus_raw"` (`_02_per_intent_rerank_skip_gate_.py:104`), live DB=`"auto"`. Decides dense-embed bytes (raw_only for structural, prefix_plus_raw prose, field_selective row-preserve).
- `convo_summary` = query-side conversation-memory concept but **DEAD** — every class/def in `infrastructure/convo_summary/*.py` is commented out ("never wired in bootstrap or graph", `__init__.py:11`), **zero live callers** (grep clean). Port exists (`application/ports/convo_summary_port.py`) but unwired.

**EN07.** U6 writes a **COPY column**, does not overwrite `content`. `_stage_u6_vn_segment` (`ingest_stages.py:954`) produces `_segmented_chunks` (`:980-1014`) → `ctx.segmented_chunks` → persisted to **`content_segmented`** column (`ingest_stages_store.py:810-812,915,1024`), only when `_segmented != _txt` else `None` (`ingest_stages.py:1011-1013`). `content` keeps enriched/persisted text. **LIVE:** 906/906 chunks have `content_segmented` non-null, all with `_` compound joins (DB query); sample `"Tóm_tắt & danh_sách đầy_đủ..."` — U6 confirmed running.

**EN08.** Lang gate = `_vi_seg_lang_eligible = _effective_language in VI_DOMAIN_LANGUAGES` (`ingest_stages_enrich.py:302-303`); `VI_DOMAIN_LANGUAGES=("vi",)` only (`_02_per_intent_rerank_skip_gate_.py:232`). Non-`vi` bot → U6 skipped entirely (`ingest_stages.py:995` elif guard + `skipped=True` metadata `:1029`); `segment_vi_compounds` also self-gates (`vi_tokenizer.py:158-159`). Bilingual accuracy = **CHƯA VERIFY** — binary gate: a `vi` bot runs underthesea over EN text too (risk corrupting EN tokens); a non-`vi` bot leaves VN compounds unsegmented. No measured accuracy number.

**EN09.** Fail-soft: `segment_vi_compounds` returns **original text** on every failure (underthesea unavailable/raises/oversize `vi_tokenizer.py:159,163,172-...`); ingest wrapper also catches (`ingest_stages_enrich.py:371-372` `return_exceptions=True`→original). Effect on BM25: `content_segmented=None`→trigger `update_chunk_search_vector` falls back `to_tsvector('simple', COALESCE(content_segmented, content, ''))` (DB trigger verified) → raw `content` indexed without compound-join symmetry (3 loose tokens vs 1). **Real case = CHƯA VERIFY** (no captured failing example; fail-path is graceful, no error rows found).

**EN10.** narrate OFF confirmed: `narrate_then_embed_enabled` live=`false` AND `NarrateService(enabled=_narrate_enabled=False)` (`document_worker.py:552-571`) → `_narrate_chunks_for_embed` identity passthrough (`ingest_stages_store.py:277-283`). Tables embedded as **raw chunker output** (csv/table_csv key:value rows or `field_selective` flood-cell-stripped embed `ingest_stages_store.py:227-233`), no LLM linearization. **FLAG:** worker comment `document_worker.py:542-544` ("in-code DEFAULT stays True... seeded row 0230 = OFF") is **STALE** — constant is now `False` (`_20:81`) and alembic 0230 doesn't exist. Price-table-miss / number-embed-quality measure = **CHƯA VERIFY** (needs load test; no A/B).

**EN11.** Two distinct paths:
- `entity_extractor` (Port) = **QUERY-side, RULE-based**, default OFF. Wired `bootstrap.py:400-404` `build_entity_extractor(provider=entity_extractor_provider)`; default provider=`"null"` (`_11:316`), `entity_grounding_enabled` default=`False` (`_11:317`), no live DB override → **NullExtractor at runtime** (`extract`→`[]`). Impls are rule-based: `vi_underthesea_extractor.py` (underthesea NER/POS BIO tags) + `en_simple_extractor.py` — **NO LLM**. Callers: `query_graph.py:2159`, `multi_query_expansion.py:517` (both query-time). **NOT used at ingest.**
- Ingest entity path = `_extract_graph_entities` (`ingest_core.py:712,781` `kg_service.extract_entities`, **LLM-based** `_MiniLLM:747`) — but **DEAD**: gated `graph_rag_default_mode != "disabled"` (`:731`), live DB=`"disabled"`, and `knowledge_edges` table = **0 rows** (DB). Also latent bug `:794` (`bot_id=` kwarg comment flags TypeError→triples discarded). No DSI-backfill entity path via entity_extractor found.

**EN12.** Order confirmed `ingest_core.py:579-582,708`: U3 clean → U4 chunk → **U5 enrich → U6 vn_segment** → (incremental hash diff) → **U7 embed_store** → finalize. Split of outputs at U7: the **enriched prefix goes into `content` and IS dense-embedded** (via `embed_text_strategy`: prefix_plus_raw for prose, raw_only for structural, `ingest_stages_store.py:224-257`); the **U6 segmented text is generate-and-store-for-BM25 only** → `content_segmented` → `search_vector` (`update_chunk_search_vector` trigger `COALESCE(content_segmented,content)`), **NOT dense-embedded**. So: segmented = lexical/BM25 channel; enriched = dense channel — two separate representations, not "segmented+embedded".

---
**Cross-cutting flags:** (1) seed `init_system_config.py:90,102` = `true` for enrichment/CR while code-default + live DB = `false` — long-standing drift (live keys set false 2026-05-05, seed never updated). (2) `contextual_retrieval_model` live = `"openai/claude"` malformed. (3) The Jina-late_chunking rationale that justifies disabling all 3 enrich paths is void post-Jina-drop (embedder=zeroentropy). No brand/tenant literal found in the enrich engine code itself (bot-specific breadcrumb like "bot spa" lives in DB chunk data, not code).

---


All 25 questions verified with runtime evidence. Here is the answer block.

---

## SECTION EM — Embedding (EM01–EM25) — forensic answers

**EM01.** Operative = **zeroentropy / zembed-1 / 1280-dim** — SQL `bot_model_bindings⋈ai_models`: 6 bindings `purpose='embedding'`→zeroentropy/zembed-1/dim1280 (+1 STALE `openai/text-embedding-3-small`/1024 but `enabled=f`). `system_config`: `embedding_provider="zeroentropy"`, `embedding_model="zembed-1"`, `embedding_dimension=1280`. DI resolves at `bootstrap.py:308-318` (`build_embedder(provider=get_boot_config("embedding_provider"))`). README **STALE/WRONG**: `README.md:16-17,60,98,125,251` claim `jina-embeddings-v3 1024-dim` + "migrated ZE→Jina 1024, semantic_cache 1280→1024" — contradicts live DB (still ZE 1280).

**EM02.** Swap NULLed ALL vectors + forced full re-ingest — `alembic/versions/20260626_embed_swap_to_zeroentropy_1280.py:45,55-56` (`UPDATE…SET embedding=NULL`) + docstring:20. Live SQL `SELECT vector_dims(embedding),count(*)` → **906 rows all 1280, 0 NULL** → re-embed complete this DB.

**EM03.** `document_chunks.embedding = vector(1280)` (`\d`), latest migration `_NEW_DIM=1280` (`embed_swap_to_zeroentropy_1280.py:34,46`). NULL-non-parent count = **0** — SQL: all 906 have `parent_chunk_id IS NULL` (no child chunks exist) and 0 NULL embeddings.

**EM04.** Two distinct dirs/ports: `infrastructure/embedding/` = vector-gen adapters (`EmbeddingPort`: ZE/Jina/LiteLLM/BKAI), text→float[]. `infrastructure/embedding_text/` = `EmbeddingTextStrategyPort` (`embedding_text_port.py:33`) pure-fn choosing WHICH text (raw_only / prefix_plus_raw / field_selective / null) to feed embedder; `system_config.embedding_text_strategy="auto"`; wired `ingest_phases.py:121`, `ingest_stages_enrich.py:85`.

**EM05.** Two batch levels + concurrency cap. Outer doc-batch `DEFAULT_EMBED_DOC_BATCH_SIZE=100` (`constants/_04:77`), sleep 0.5s (`_04:81`), per-batch timeout 300s (`_04:167`) — `document_service/__init__.py:511-524`. Inner HTTP batch `DEFAULT_EMBEDDING_MAX_BATCH=64` (`_04:185`) — `zeroentropy_embedder.py:212`. Concurrent limit = `Semaphore(DEFAULT_EMBEDDER_MAX_CONCURRENT=4)` (`_05:21`) — `zeroentropy_embedder.py:100`.

**EM06.** CB `fail_max=10, reset=60s` (`constants/_05:11-12`; `zeroentropy_embedder.py:101-107`). **TRIPPED IN PROD** — `journalctl 30d` = **488** `embedder_circuit_open` events, latest `2026-07-07T11:45:47Z` model=zembed-1 logger=`zeroentropy_embedder` lineno 283. NOT dormant.

**EM07.** `tpm_per_key`/`tpm_safety_fraction` exist ONLY in `JinaEmbedder.__init__` (`jina_embedder.py:129`); `registry.py:109-114` injects them only when present in ctor sig → ZE/LiteLLM/BKAI never receive the TPM limiter. **DEAD for operative ZE provider.**

**EM08.** Retry `DEFAULT_RETRY_MAX_ATTEMPTS=3` (`_04:180`), retryable {429,500,502,503,504} (`zeroentropy_embedder.py:61,266-281`). Coverage floor `DEFAULT_INGEST_MIN_LEAF_EMBED_COVERAGE=0.8` (`_20:239`); `ingest_stages_final.py:215` doc→active if `leaf_coverage>=0.8` else failed; line 309 partial-but-≥floor → **SERVE degraded** (up to 20% chunks NULL-embedded yet doc active → those chunks retrievable by BM25 only).

**EM09.** Query embed uses per-bot binding (`query_graph.py:1603-1613` resolve_runtime `purpose="embedding"`). Mismatch check `_check_embed_model_consistency` (`query_graph.py:754`) is **NAME-only, detection-only, never raises** (docstring:755); NO dim check — a real dim mismatch only surfaces as pgvector cast error at `<=>`. Same-model NOT enforced.

**EM10.** ZE embedder emits **NO token_ledger** — grep `ledger` in `zeroentropy_embedder.py` = 0 hits; DI passes `ledger=token_ledger` (`bootstrap.py:317`) but `registry.py` sig-filter drops it (ZE `__init__` has no `ledger` param). Only `jina_embedder.py:329` emits (`emit_aux_usage`). SQL `token_ledger WHERE purpose~embed OR provider~zeroentropy` = **0 rows** (providers logged: innocom/openai/jina). **Embed cost INVISIBLE for operative provider.**

**EM11.** Passage prefix applied at `ingest_stages_store.py:322-327` (per-bot `plan_limits.embedding_passage_prefix` > `system_config`, resolver `__init__.py:564-607`); query prefix `query_graph.py:1587-1589`. LIVE both empty (`system_config.embedding_passage_prefix=""`, `embedding_query_prefix=""`). ZE encodes asymmetry via `input_type` document/query (`zeroentropy_embedder.py:180-185,229`), not text prefixes → **no prefix on wire currently**.

**EM12.** ZE sends **NO truncate** — payload = `{model,input,input_type,dimensions}` only (`zeroentropy_embedder.py:225-231`), no client-side truncation, no lost-tail log; oversized chunk → hard provider error. (Jina/BKAI DO send `"truncate":True` — `jina_embedder.py:305`, `bkai_vn_embedder.py:239`.) Provider-returned-fewer-vectors guard at `ingest_stages_store.py:517`.

**EM13.** Operator = **`<=>`** (cosine distance), score `1-(col <=> emb)` — `pgvector_store.py:333,337,531,534`; `semantic_cache.py:477,485`. HNSW `vector_cosine_ops` (`ix_chunks_embedding_hnsw`). NO client-side L2 normalize (grep normalize in `infrastructure/embedding/` = 0) — cosine op is scale-invariant. Not `<#>`.

**EM14.** baseurl `DEFAULT_ZEROENTROPY_API_URL="https://api.zeroentropy.dev"` (`_02:64`) → `/v1/models/embed` (`zeroentropy_embedder.py:81`); timeout `system_config.embedding_timeout_s=90` = `DEFAULT_EMBEDDING_TIMEOUT_S=90` (`_04:158`). Provider down: retry3→CB open→`ExternalServiceError`. Ingest = fail-loud, doc→failed + recovery re-queues (`document_service/__init__.py:540`). Query = `embed_degraded` → lexical-only. **No "queue" hold.**

**EM15.** `semantic_cache.query_embedding = vector(1280)`, HNSW cosine `ix_semantic_cache_qe_hnsw`. Matches `document_chunks` 1280. Migration TRUNCATEd cache on 1024→1280 (`embed_swap_to_zeroentropy_1280.py:57`). `information_schema` confirms only 2 vector cols, both 1280.

**EM16.** **No real-network embed integration test** — all mock: `test_multi_query_embed_batch_zeroentropy.py`, `test_litellm_embedder_cb.py`, `test_bkai_vn_embedder.py`. grep `api.zeroentropy.dev|ZEROENTROPY_API_KEY` in `tests/` = 0 for embedder (only `test_zeroentropy_reranker.py`). No live ZE embed hit.

**EM17.** Pricing in `ai_models.input_price_per_1k_usd` — zembed-1 = **$0.0001/1k**, dim 1280 (SQL). BUT per EM10 no ledger emit → actual embed cost never computed/recorded. No missing-price fallback needed (row exists); cost accounting for embed is effectively **absent**.

**EM18.** No measured zembed-1 VN benchmark for this deployment — docs cite generic external MTEB/vendor claims (`docs/dev/EMBEDDING_RERANKER_ANALYSIS.md`, `docs/dev/RAG_STACK_VIETNAM_RECOMMENDATION.md`) but no load-test-measured VN recall run. **CHƯA VERIFY.**

**EM19.** Doc-level content dedup at `ingest_core.py:420-448` — `SHA256(content)`, lookup `(record_bot_id, content_hash)`; duplicate → log `ingest_duplicate_content_hash`, skip (no re-embed). Test `test_content_hash_dedup.py`. Chunk-level embed-text fingerprint `__init__.py:774-782`. → 2 same-content docs = 2nd deduped, **not re-embedded**.

**EM20.** `proximity_cache` = **DEAD** — `infrastructure/proximity_cache/__init__.py:11` "proximity_cache infra never wired in bootstrap or graph"; all exports commented out; `lsh_proximity_cache.py` fully commented. No caller.

**EM21.** No cron/systemd timer. Only manual one-off `scripts/db/backfill_parent_embeddings.py` (R1 fix, null-parent backfill, run by hand). **KHÔNG TỒN TẠI** scheduled embed backfill.

**EM22.** Swap checklist = migration `embed_swap_to_zeroentropy_1280.py`: (1) binding `UPDATE bot_model_bindings→zembed-1` (67-72); (2) dim `system_config.embedding_dimension=1280` + `ai_models.embedding_dimension` (62-64); (3) `semantic_cache` TRUNCATE+widen (57); (4) re-embed = NULL chunk vectors + require re-ingest (55-56, docstring:20); (5) HNSW drop/recreate `m=32 ef_construction=200 cosine` (`_resize` 39-50). **MMR 0.98** done SEPARATELY: `DEFAULT_MMR_SIMILARITY_THRESHOLD=0.98` (`_14:235`, comment 231 "0.88→0.98 002-D 2026-07-04, 0.88 calibrated for pre-zembed-1") — recalibrated for zembed-1 tighter cosine, NOT in the migration.

**EM23.** No cross-lingual EN-query/VI-corpus test (grep `cross.lingual|english.*query` in `tests/` = 0) and no measured hit-rate. **CHƯA VERIFY** (test KHÔNG TỒN TẠI).

**EM24.** Query 429 → ZE retry 3× backoff (429 retryable) → exhaust/CB → `EmbeddingError` caught at `query_graph.py:1649-1664`: `state["embed_degraded"]=True`, log `embed_query_error_degraded`, `return []` → retrieve falls back to **lexical-only (BM25)**. User sees a degraded answer, **NOT** a 429/error. No user-facing 429 surface.

**EM25.** Last embed-model change = alembic **`embed_swap_ze1280_260626`** (2026-06-26, `embed_swap_to_zeroentropy_1280.py`); down-rev `chat_swap_innocom_260626`; current head `innocom_timeout_90s_260708`. Owner mechanism = tracked migration (sacred-rule 7, NOT psql). Measure-after: SQL 906/906 chunks re-embedded 1280-dim, 0 NULL (re-ingest done). Post-swap operational reality = CB tripping in prod (EM06, 488 events to 2026-07-07) + no cost tracking (EM10) + no VN benchmark measure (EM18 CHƯA VERIFY).

---


All evidence gathered. Here is the SECTION ST answer block.

---

**ST — Store / pgvector / Index (live DB `ragbot_v2_dev`, connected as `postgres` superuser; stats never reset so idx_scan counts are lifetime)**

**ST01.** `document_chunks` = 16 cols (live `\d`): `id`uuid PK, `record_document_id`(NN FK→documents CASCADE), `chunk_index`int, `content`text, `content_segmented`text, `content_hash`char(64), `embedding`vector(1280), `metadata_json`jsonb`'{}'`, `parent_chunk_id`(self FK SET NULL), `chunk_chars`int, `search_vector`tsvector, `created_at`, `record_bot_id`(NN FK→bots CASCADE), `chunk_type`varchar(32) def`'text'` CHECK(text/table/table_row/code), `chunk_context`varchar(1024), `doc_deleted_at`. NO ORM model — raw-SQL/DDL managed (models_monitoring.py:52 note; grep found only `RequestChunkRefModel`). Far beyond minimal `id/content/embedding` — denormalized `record_bot_id`+`doc_deleted_at` added for HNSW pushdown.

**ST02.** HNSW built `m='32', ef_construction='200'` cosine — squashed_baseline.sql:1160; recreated identically @1280 in `20260626_embed_swap_to_zeroentropy_1280.py:47-50`. Runtime `ef_search` tuned per-session: `pgvector_store.py:322` & `:404` `SET hnsw.ef_search = {ef_val}`, clamped `max(1,min(ef,1000))` (:313/:395). `DEFAULT_EF_SEARCH=64`, `MAX_EF_SEARCH=1000` — `_00_app_env_taxonomy.py:223,225`.

**ST03.** Config lang = `'simple'` everywhere: trigger fn `update_chunk_search_vector` (`to_tsvector('simple',…)`, squashed_baseline.sql:27) + functional GIN `idx_chunks_search_vector_combined` (`'simple'::regconfig`). `'simple'`=no stem/no stopword (correct for VN, avoids EN stemmer). VN BM25 handled by pre-segmenting compounds into `content_segmented` via `segment_vi_compounds`, symmetric at index & query (pgvector_store.py:409) — NOT a VN dictionary.

**ST04.** Trigger `trg_chunk_search_vector BEFORE INSERT OR UPDATE OF content, content_segmented` → `search_vector = to_tsvector('simple', COALESCE(content_segmented, content, ''))`. Sync gap: UPDATE of `chunk_context` alone does NOT refire → `chunk_context` only reflected by the separate functional index `idx_chunks_search_vector_combined`, not by `search_vector`.

**ST05.** LIVE insert = `_bulk_insert_chunks` `ingest_helpers.py:146` (called ingest_stages_store.py:828/938/1045). Multi-row `VALUES` batched (`max_rows=(POSTGRES_MAX_BIND_PARAMS-1)//binds_per_row`); sequential batches share ONE session/tx → mid-doc batch failure rolls back the whole INSERT set (comment :251-252). NOT per-chunk. `pgvector_store.upsert_chunks:111` is the Port method but has NO live ingest caller (bypassed).

**ST06.** `document_service_index` cols: id, record_tenant_id, workspace_id, record_bot_id, record_document_id, record_chunk_id(FK→chunks SET NULL), entity_name(NN), entity_category, price_primary numeric, price_secondary numeric, attributes_json jsonb, created_at, entity_synonyms text; idx: pkey, idx_dsi_attrs(gin), idx_dsi_bot_price1/2, idx_dsi_doc, ix_dsi_entity_synonyms_trgm. Live count = **598** (NOT 1335): c6e1fc56=242, ff8c86c5=187, 5f2e12a8=107, 76f20f28=62. Sync = FK CASCADE on doc/bot delete, SET NULL on chunk delete. "1335 entities" claim FALSE.

**ST07.** chunk `metadata_json` key freq (906 rows): `chunking_strategy/total_chunks/chunk_index/enriched_prefix/quality_score/document_title/chunking_confidence`=906; `raw_chunk`=892; `original_content`=849; `block_types`=849; `enriched_prefix_persisted`=833; `block_type`/`narrated_text`=732; `structural_path`=213; `is_full_document`/`chunk_strategy`/`original_char_count`=4 (near-dead; `chunk_strategy` is a stray dup of `chunking_strategy`). DEAD REF: code claims chunk-level filters use `ix_chunks_metadata_gin` (pgvector_store.py:244,253) but NO such index exists → chunk `metadata_json @>` seq-scans.

**ST08.** `original_content` COLUMN = **KHÔNG TỒN TẠI** on document_chunks. Exists only as `metadata_json->>'original_content'` (849/906 chunks); document raw is `documents.raw_content` (written 26/26 docs, 1807kB).

**ST09.** `document_chunks` = 27 MB total / 2192 kB heap (indexes dominate; HNSW alone 11 MB). n_live=906, n_dead=197 (~22% bloat), autovacuum_count=82, last_autovacuum 2026-07-06 08:54. Autovacuum active/healthy.

**ST10.** `knowledge_edges` = **0 rows**. Writer `KnowledgeGraphService` (graph/knowledge_graph.py) invoked ingest_core.py:743 but gated — returns early when `graph_rag_default_mode=="disabled"` (:731); live `system_config.graph_rag_default_mode="disabled"`. DORMANT / dead table.

**ST11.** idx_scan=0 on document_chunks: `ix_chunks_embedding_hnsw`(HNSW 11MB), `ix_chunks_content_hash`, `idx_chunks_search_vector_combined`, `ix_chunks_chunk_context_trgm`. HNSW dormant — `EXPLAIN ANALYZE` with literal AND correlated vector both → **Seq Scan** (906-row corpus too small for planner to pick HNSW; 8.7 ms). DB-wide 0-scan: `ix_semantic_cache_qe_hnsw`, all `token_ledger` idx, `model_invocations`, `monitoring_log`, etc.

**ST12.** 12 idx on document_chunks; 165 public indexes DB-wide (relkind='i'). squashed_baseline.sql = 102 `CREATE INDEX`; post-squash only 2 migrations touch idx (20260624 `ix_dsi_entity_synonyms_trgm`, 20260626 HNSW-1280 recreate). Every live index traces to baseline SQL + those 2 — no orphan stamp-without-DDL drift. Live alembic head = `innocom_timeout_90s_260708`. "22 indexes" figure not reproduced.

**ST13.** NO partitioning anywhere (pg_class relkind='p' = 0). `request_trace` table **KHÔNG TỒN TẠI**. `request_logs`+`request_steps` are plain tables (relkind='r'); `request_steps`=17947 rows, unpartitioned. No monthly partitioning.

**ST14.** All builders share pool params (engine.py:44/60/95): `pool_size/max_overflow/pool_recycle/pool_timeout` from settings. Live env: pool_size=50, max_overflow=50, timeout=5s, recycle=1800s → 100-conn cap/engine. Runtime = **2 engines** (bootstrap.py:179 `db_engine=create_engine_app`+RLS wrapper; :194 `db_engine_system=create_engine_system` BYPASSRLS). BOTH fall back to admin superuser DSN today: `DATABASE_URL_APP`/`DATABASE_URL_SYSTEM` UNSET + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` → RLS effectively bypassed at runtime (superuser ignores FORCE RLS). 3rd builder `create_engine` = alembic/ops only.

**ST15.** `scripts/backup_db.sh` exists (pg_dump `-Fc`, self-documented cron 02:00 + 7-day retain, fail-loud). BUT `crontab -l`=none for root, no systemd backup timer, `/var/backups/ragbot` empty (dir made May 7, 0 dumps). **Never run on this host; no restore drill.** Dormant.

**ST16.** `documents.access_groups` COLUMN = **KHÔNG TỒN TẠI**. retrieve.py:1851 reads `chunk.get("access_groups")` behind `permission_filtering_enabled` (live=`false`); even if ON, retrieval SQL never selects it → key always None → `default_public=true` path → dead filter.

**ST17.** Hand-ran dense(cosine `ROW_NUMBER`)+sparse(`ts_rank_cd`/`websearch_to_tsquery('simple')`)+RRF `FULL OUTER JOIN` on bot c6e1fc56 (probe=existing chunk emb). Top-3 rrf_score: `019f3c5b-4d1c…` 0.01734 ("Tài liệu: xe-3…"), `019f36a1-295d…` 0.01734 ("xe-1 … LANDSPIDE"), `019f3c5b-4d1e…` 0.01707 ("xe-3 phần 2/187"). Mirrors `pgvector_store.hybrid_search:526-566` (dense/sparse/fused CTE, `vec_w`+`bm25_w`, `rrf_k`). Fusion runs & returns rows. (Corpus carries brand literals e.g. "LANDSPIDE", tool_names xe-1/3 — data, not engine code.)

**ST18.** NO `WHERE state='active'` in retrieval. Exclusion = chunk-denormalized `doc_deleted_at IS NULL` (`_doc_filter_sql` pgvector_store.py:270) + structural (chunks written only at successful store stage post-embed). Live: 906/906 chunks under state='active' docs, 0 soft-deleted. DRAFT/failed docs never hold chunks. No document-state guard column — relies on soft-delete flag + write-on-success.

**ST19.** `stats_*_daily` rollup TABLES = **KHÔNG TỒN TẠI** (no `*_daily`/`*_rollup`/`stats_*` tables). No rollup worker. Analytics (`crm_analytics_service.py:74` "daily timeline", `tenant_analytics_service.py`) compute rollups on-the-fly via GROUP BY over `token_ledger` (18779 rows)/`request_logs` at query time — not materialized.

**ST20.** Rebuild from git = alembic squash `20260618_squash_baseline.py` (`op.execute(squashed_baseline.sql)` → DROP/CREATE public, one-shot schema) then seed (`init_system_config.py`~159 keys, `seed_ai_config.py`, `seed_dev.py`, `seed_3test_bots.py`) + re-ingest (1280-swap nulled chunk vectors). Runbook `scripts/db/REBUILD_DEV_DB_RUNBOOK.md` (last commit 2026-06-17) is STALE — describes pre-squash `Base.metadata.create_all` bypass, predates squash baseline (2026-06-18); last documented clean rebuild "Tested 2026-05-06". No post-squash rebuild drill recorded → CHƯA VERIFY that documented path still matches (squash supersedes it).

---


All evidence gathered. Here is the full W-section answer block.

## SECTION W — Workers / Async / State (W01–W25)

**W01.** 5 embedded workers spawned by `start_embedded_workers` (`interfaces/http/embedded_workers.py:242-275`), each wrapped in `_supervise` (:211): (1) **document_consumer** → `run_embedded_document_consumer` (:60) → `handle_document_uploaded` (`workers/document_worker.py:138`), engine = `container.bus()` (Redis) + `container.session_factory()` (RLS request engine, doc_worker.py:352); (2) **outbox_publisher** → `run_embedded_outbox_publisher` (:101) → `run_outbox_loop` (`workers/outbox_publisher.py:42`), engine = `container.outbox_repo()`; (3) **recovery_worker** → `run_embedded_recovery_worker` (:122) → `run_recovery_loop` (`workers/document_recovery_worker.py:363`), engine = `container.system_session_factory()` BYPASSRLS (:387); (4) **cost_cap_alerter** → `run_embedded_cost_cap_alerter` (:141) → `evaluate_tenants` (`services/cost_cap_alerter.py:82`), engine = `system_session_factory` (:155); (5) **cache_purge** → `run_embedded_cache_purge` (:173), engine = `system_session_factory` (:189). NOT embedded (separate/dormant): `scripts/chat_async_worker.py`, `workers/chat_worker/pipeline.py`, `workers/ai_config_listener.py`. Live prod unit = `ragbot-py.service` (active, "single process: API + embedded ingest/outbox/recovery").

**W02.** `embed_workers_enabled: bool = True` (`config/settings.py:124`); not set in `.env` → default True. Gate at `app.py:433`. OFF → ingest runs by standalone `python -m ragbot.interfaces.workers.document_worker` + `outbox_publisher` (main() entrypoints intact, doc_worker.py:764/outbox_publisher.py:240). BUT cost_cap_alerter + cache_purge have NO standalone `-m` entrypoint (embedded-only) — OFF loses them (recovery has standalone main at recovery_worker.py:432).

**W03.** XREADGROUP loop `redis_streams_bus.py:498-550`; XACK fires ONLY after inbox-mark commit (`_dispatch_one` :455) — process-then-mark. Poison: `recover_pending_messages` (:572, called every ~60s from loop :505) routes `times_delivered > DEFAULT_BUS_DLQ_MAX_DELIVERIES (=5, _07:144)` to `_dead_letter` (:644) → XADD to `{stream}:dlq` parking-lot THEN XACK (XADD-before-XACK, never log-drop). NOT infinite — capped at 5 deliveries. Caveat: DLQ check only runs inside recover pass (idle≥30s), not on the hot XREADGROUP path.

**W04.** `run_outbox_loop` batch=100, poll_interval=0.2s (`outbox_publisher.py:46-48`), per-row `FOR UPDATE SKIP LOCKED` (`_drain_batch_per_row` :97). Published rows are **UPDATE-d not DELETE-d**: `mark_processed_in_session` sets `status='processed', processed_at=now` (`outbox_repository.py:145-164`). **No GC** — `purge_stale_data.py` only deletes semantic_cache (line 169), never outbox. Live DB: `select status,count(*) from outbox` → **257 rows, all `processed`**, span 2026-06-18→2026-07-07. Bloat confirmed (monotonic growth, no reaper).

**W05.** `_scan_stuck_documents` (`document_recovery_worker.py:128`): DRAFT older than `DEFAULT_RECOVERY_STUCK_THRESHOLD_S=900s` (`_20:225`) OR `state='active' AND COALESCE(chunks_processed,0)=0 AND NOT EXISTS chunks AND updated_at<threshold`. Cooldown = `DEFAULT_RECOVERY_REPLAY_COOLDOWN_S=3600s` (`_20:249`) via time-bounded LEFT JOIN anti-dup (:178-183). Re-emit builds fresh `document.uploaded.v1` with NEW `event_id`+`job_id` (`_build_replay_payload` :210) → new outbox row (new Msg-Id). Dup-idempotency: only the 3600s cooldown join suppresses re-replay; downstream inbox dedup keys on outbox-row-UUID so each replay is a distinct msg (can double-ingest if a prior replay is still in-flight past cooldown). Interval `DEFAULT_RECOVERY_INTERVAL_S=300s` (`_20:220`).

**W06.** `cost_cap_alerter.evaluate_tenants` (`services/cost_cap_alerter.py:82`). Source table = `request_logs.total_tokens` (NOT token_ledger) aggregated vs `tenants.quota_monthly_tokens`; window `DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS=30`, warn `0.8`, exceed `1.0` (`_16:206-210`). Dest = structlog events `cost_cap_warning`(warn)/`cost_cap_exceeded`(error) only — **no table, no email/webhook sink**. `check_token_cap` (`tenant_token_meter.py:276`) is a SEPARATE write-time enforcer, not called by the alerter. Alerter LIVE (embedded, interval `DEFAULT_COST_CAP_ALERT_INTERVAL_S=3600` `_16:214`); `check_token_cap` — CHƯA VERIFY its call-sites (out of W-scope), not dead per grep in tenant_token_meter.

**W07.** `run_embedded_cache_purge` (`embedded_workers.py:173`): `DELETE FROM semantic_cache WHERE expires_at < now() - (grace||' hours')::interval`, grace `DEFAULT_CACHE_PURGE_GRACE_HOURS=24`, interval `DEFAULT_CACHE_PURGE_INTERVAL_S=3600` (`_16:221-222`), via BYPASSRLS `system_session_factory`. LIVE (embedded). Cron duplicate `scripts/purge_stale_data.py` exists but timer NOT installed on host.

**W08.** Standalone/horizontal entrypoints importable: `document_worker.main` (:764), `outbox_publisher.main` (:240), `document_recovery_worker.main` (:432) — all with `if __name__=="__main__"`. Logic-drift risk: embedded reuses the SAME `run_outbox_loop`/`run_recovery_loop`/`handle_document_uploaded` (no fork). BUT cost_cap + cache_purge exist ONLY as embedded coroutines (no `-m` module) → horizontal-scale mode silently drops those two.

**W09.** Switch = `settings.app.embed_workers_enabled` (`app.py:433`). Double-consume: embedded + standalone document_worker share `queue_group="documents", durable_name="document-worker"` → same Redis consumer group `documents:document-worker` (doc_worker.py:781-786, embedded_workers.py:78-80) → messages load-balanced not doubled (safe). Outbox: `FOR UPDATE SKIP LOCKED` prevents double-publish across replicas. Real risk = running BOTH modes duplicates cost_cap/cache_purge sweeps (harmless, idempotent DELETE) — no message double-consume.

**W10.** `handle_document_uploaded` binds ctx from payload (`document_worker.py:149-158`): `record_tenant_id`, `record_bot_id`. Hard-required at `_handle_document_uploaded_inner:225` `TenantId(UUID(payload["record_tenant_id"]))` — missing → KeyError → caught by broad-except (:690), `_is_transient_ingest_error` False (KeyError not in `_TRANSIENT_INGEST_ERRORS` :106) → job marked `failed` (:706), NOT re-raised → **fail-closed (terminal fail), not skip**. workspace_id missing → `resolve_workspace_id` fallback to `str(tenant_id)` (:243-251).

**W11.** Concurrency caps in bus `_dispatch_one` (`redis_streams_bus.py:392-401`): workspace sem `DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE=10` (outer) ⊃ bot+channel sem `DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL=5` ⊃ global handler `DEFAULT_BUS_HANDLER_CONCURRENCY=5` (`_07:111,128,129`). 10 docs same bot → 5 concurrent (bc cap), rest queue on semaphore; sem registry bounded by `DEFAULT_BUS_TENANT_SEM_MAX=256` with `_overflow` fallback (:158). XREADGROUP `count=REDIS_XREAD_COUNT=10` per batch (`_07:100`).

**W12.** SIGTERM: uvicorn owns it (`start.sh:--timeout-graceful-shutdown 30`); lifespan `finally` → `stop_embedded_workers` cancels tasks (`app.py:450-455`, embedded_workers.py:278) → `CancelledError` → each worker `finally` unsubscribes/commits. In-flight ingesting doc: handler task gets CancelledError mid-`doc_service.ingest()` → partial → row left DRAFT or active-0-chunk → **resume = recovery_worker re-sweep** (900s threshold). Standalone: `signal.SIGINT/SIGTERM → stop.set()` (`doc_worker.py:788-796`). No explicit in-flight drain barrier — relies on recovery sweep.

**W13.** SQL run: `documents.chunks_processed` vs live `COUNT(document_chunks)` for all 15 active docs → **exact parity every row** (8/8, 187/187, 207/207, 80/80, 92/92, 33/33, 12/12, 1/1...). Writer of `chunks_processed`: terminal flip `ingest_stages_final.py:334` (state=active, progress_percent=100, chunks_processed=:cp), also `ingest_core.py:808`, seeded 0 at `ingest_stages.py:911`.

**W14.** Per-stage progress: `documents.progress_percent` + `current_step` columns updated at `ingest_stages_final.py:329-334` (flip to 100 on completion); `StepTracker` emits per-U1–U7 `request_steps` rows (`document_worker.py:309-318`). User %-real: progress_percent is 0→100 binary-ish (set 100 on terminal); mid-ingest granularity via request_steps, not a continuous % — CHƯA VERIFY a live monotonic sub-100 progress feed.

**W15.** structlog JSON (`setup_logging(json=True)` doc_worker.py:767). Context bound per-message: `trace_id`, `record_tenant_id`, `bot_id`, `workspace_id` via `bind_request_context` (:149-153,254) + `document_id` on events. Sample event names emitted: `document.uploaded.consumed` (:223), `worker_reused_raw_content` (:364), `worker_parser_registry_routed` (:468), `document_ingest_failed` (:691), `embedded_document_consumer_started` (embedded_workers.py:81). CHƯA VERIFY — no live journalctl captured (no runtime log tail run); format/fields cited from code.

**W16.** Delivery: `create_delivery` factory (`infrastructure/delivery/__init__.py:7`) → only **CallbackDelivery** (HTTP callback+HMAC) or **NoopDelivery**. **Zalo delivery = KHÔNG TỒN TẠI** (no zalo adapter in `infrastructure/delivery/`; only callback_delivery.py + noop_delivery.py). Callers = `chat_worker/callbacks.py:164,283` (`delivery.deliver`) + chat_hooks `fire_db_stage`/`fire_post_stage` (:264-269). BUT the interfaces `chat_worker` (which owns these callers) has NO deployed runner (see W23) → delivery path is DORMANT in the live single-process deployment.

**W17.** `_xadd_or_raise` sets `maxlen=DEFAULT_STREAM_MAXLEN=100_000, approximate=True` (`redis_streams_bus.py:321`, const `_10:31`); DLQ XADD also trimmed (:670). **GAP**: `chat_async.py:266` XADDs `CHAT_REQUEST_STREAM` (chat.requested) with **NO maxlen** → that stream is unbounded (relies on Redis `allkeys-lru` eviction, compose maxmemory 2gb). Report: document/config streams trimmed at 100k; the async-chat stream is untrimmed.

**W18.** Repo-wide `grep datetime.now()|datetime.utcnow()` (no-tz) in `src/ragbot/` → **0 hits**. Workers use tz-aware: `datetime.now(tz=UTC)` (doc_worker.py:713), `datetime.now(tz=timezone.utc)` (recovery_worker.py:223, outbox_repository.py:105,163). No naive-datetime bug.

**W19.** Two workers/one doc: dedup is at TWO layers, not table-lock on documents. (a) Redis consumer group `documents:document-worker` → each `document.uploaded.v1` delivered to exactly one consumer. (b) `event_inbox` PK `(subscriber_id, msg_id)` ON CONFLICT DO NOTHING (`redis_streams_bus.py:51,244`) + Redis dedup hint (:413-430); live `event_inbox` has 124 rows (confirmed). Outbox side uses `poll_one_for_update` `with_for_update(skip_locked=True)` (`outbox_repository.py:82`). No live 2-worker concurrency test run — CHƯA VERIFY empirically; mechanism verified in code.

**W20.** Heartbeat/worker-liveness table = **KHÔNG TỒN TẠI**. SQL `pg_tables ilike %heartbeat%/%worker%` → none (only outbox, token_ledger, model_capabilities). Liveness inferred only via Redis PEL idle (`min_idle_ms=30_000`) + `times_delivered` in `recover_pending_messages` (:577). No DB heartbeat, no Prometheus liveness gauge for workers.

**W21.** No global ingest wall-clock timeout / U7 60-min killer. `grep wait_for|asyncio.timeout` in `document_service/` → only per-stage timeouts: `DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S`, `DEFAULT_NARRATE_TIMEOUT_S`, `DEFAULT_HTTP_TIMEOUT_S` (ingest_core.py:72-87), `DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S`. **No `asyncio.wait_for` wrapping the whole `ingest()`** → a wedged ingest relies on per-stage HTTP timeouts + recovery-worker 900s stuck-sweep, not a total-job killer. "U7 60min killer" = KHÔNG TỒN TẠI.

**W22.** `crontab -l` → not permitted (no user crontab). systemd: only OS timers active (dnf/logrotate/tmpfiles). Ragbot timers `deploy/ragbot-token-reconcile.timer` (`OnCalendar=*:0/5`, every 5min → `reconcile_token_usage_redis_vs_db.py`) + `ragbot-monthly-reset.timer` (`*-*-01 00:01` → `reset_bot_token_quota_monthly.py`) are DEFINED in `deploy/` but **NOT installed/active** (`systemctl list-timers` shows none; only `ragbot-py.service` active). So scheduled token reconcile + monthly reset are DORMANT on this host.

**W23.** Live chat worker = `scripts/chat_async_worker.py` (consumes `chat.requested`, systemd `ragbot-chat-worker.service:30` runs `-m scripts.chat_async_worker`). The interfaces **`workers/chat_worker/pipeline.py` (consumes `chat.received.v1`) has NO deployed runner** — not embedded (embedded_workers only subscribes `document.uploaded.v1`), not in any systemd/docker unit; only `python -m` manual + importable name preserved (`__init__.py`). Yet `POST /api/ragbot/chat` (`routes/chat.py:49`) publishes `ChatReceived`/`chat.received.v1` (`use_cases/answer_question.py:118`) → **canonical chat path emits events with no wired consumer in the single-process deployment** (finding). pipeline_config parity: production worker keys `chat_worker/config.py:104 _CHAT_CONFIG_KEYS` vs HTTP `test_chat/_pipeline_config.py:125` which is manually "44 keys synced from chat_worker._CHAT_CONFIG_KEYS" with comment "Drift between this list and chat_worker._CHAT_CONFIG_KEYS is a bug" → **manual-sync parity, drift-prone** (two hand-maintained key lists, not one SSoT).

**W24.** Redis persistence OFF (`docker-compose.yml`: `--save "" --appendonly no`). FLUSHDB now → LOSE: all un-drained stream entries + consumer groups + PEL, `ragbot:outbox:dedup:*` hint keys, semantic cache, rate-limit counters, and **all in-flight `chat.requested` jobs (Redis-only, no PG backing → unrecoverable)**. RECOVER: (a) consumer groups auto-recreate on `NOGROUP` (`redis_streams_bus.py:538 _ensure_group_exists`); (b) `event_inbox` (124 rows) + `outbox` (PG) survive → any outbox row still `status='pending'` republishes; already-`processed` rows do NOT (247... all 257 currently processed → nothing to republish); (c) docs stuck mid-ingest → recovery worker 900s sweep re-emits. Document pipeline = recoverable; async-chat jobs = lost.

**W25.** Real ingest sequence (fn-per-arrow): `POST /api/ragbot/documents/create` (`routes/documents.py`) → Action-1 stores `documents.raw_content` + `uow.add_outbox(DocumentUploaded)` → **outbox_publisher** `run_outbox_loop→_drain_batch_per_row→poll_one_for_update (FOR UPDATE SKIP LOCKED)` (outbox_publisher.py:97) → `_publish_one→bus.publish_raw→_xadd_or_raise` XADD `ragbot:document.uploaded.v1` (redis_streams_bus.py:278/301) → `mark_processed_in_session` + commit → **document_consumer** `subscribe→_loop→xreadgroup→_dispatch_one` (redis_streams_bus.py:509/375) → `handle_document_uploaded→_handle_document_uploaded_inner` (doc_worker.py:138/222): `job_repo.update_status(running)` → reuse raw_content (local://) OR fetch+`detect_parser_robust`+`parser.parse` OR `container.ocr().parse` → `DocumentService.ingest()` (U1–U7: parse→clean→chunk→embed→store, StepTracker→request_steps) → `chunks_processed` flip `ingest_stages_final.py:334` → `uow.add_outbox(DocumentIngested)` + `job_repo.update_status(success)` (:613-662) → inbox-mark commit → `xack` (redis_streams_bus.py:455). Failure → `DocumentFailed` outbox + transient re-raise → `recover_pending_messages` XCLAIM (≤5) else `_dead_letter`→`{stream}:dlq`; permanent DRAFT → recovery_worker re-emit.

---
**Domain-neutral scan (W-scope):** no brand/tenant literals found in worker engine code (payloads supply all identifiers; audit actor uses generic `"system"` sentinel, recovery_worker.py:103). Clean.

---
