# Deep Code Read — `src/ragbot/interfaces/workers/` (+ non-http interfaces remainder)

**Reader scope**: every file under `src/ragbot/interfaces/workers/` plus `interfaces/__init__.py`, `interfaces/workers/__init__.py`. `interfaces/http/` belongs to the http reader; `interfaces/http/embedded_workers.py` was read here too because it is the deployment vehicle for these workers. Key dependencies read to verify claims (evidence only, not re-audited in full): `infrastructure/events/redis_streams_bus.py`, `infrastructure/delivery/*`, `application/dto/chat_payload.py`, `infrastructure/repositories/request_log_repository.py`, `infrastructure/repositories/job_repository.py`, `application/services/document_service/ingest_core.py` + `ingest_stages.py` (row-preserve path), `shared/workspace_id_validator.py`, `config/logging.py`, `application/services/model_resolver/_cache_mixin.py`, `application/services/google_link_service.py`.

Every claim below is labeled **FACT** (verified code path, `file:line`) or **HYPOTHESIS** (inference, would need runtime/DB evidence).

---

## 1. File-by-file: what each file actually does

### 1.1 `interfaces/__init__.py`, `workers/__init__.py`
One-line docstrings each (`interfaces/__init__.py:1`, `workers/__init__.py:1`). No code.

### 1.2 `workers/chat_worker/` package (god-file split, 6 modules)

`__init__.py` (39 lines) — re-exports `handle_chat_received`, `main`, `_CHAT_CONFIG_KEYS`, config helpers, so legacy import path `from ...workers.chat_worker import X` keeps working.

**`pipeline.py` (798 lines)** — the production chat consumer.
- `main()` (pipeline.py:717-794): builds DI `Container`, `bus.ensure_streams()`, resolves per-process concurrency from `system_config.chat_worker_concurrency` (fallback `DEFAULT_CHAT_WORKER_CONCURRENCY = 4`, constants `_08_sentry_otel.py:41`), subscribes to `chat.received.v1` with `durable_name="chat-worker"`, `queue_group="chat"`. `_handler` (pipeline.py:757-774) acquires the semaphore, bumps/decs `chat_worker_queue_depth` gauge, and deliberately re-raises handler exceptions so the bus skips XACK (Z2-P0-2 comment, pipeline.py:758-762).
- `handle_chat_received()` (pipeline.py:97-112): resolves tenant UUID from payload, binds structlog/contextvars, `try/finally clear_request_context()`.
- `_handle_chat_received_body()` (pipeline.py:115-714) — the full flow:
  1. `job_id` parse-first (fail → log + return = XACK) (131-139).
  2. Pydantic `ChatReceivedPayload` validation; invalid → job `failed` + return (144-159).
  3. Tenant required, else job failed `RECORD_TENANT_ID_REQUIRED` (168-181).
  4. `resolve_workspace_id` — malformed slug fails the job loudly, missing slug falls back to `str(record_tenant_id)` (187-206); slug fed to RLS GUC binder via `bind_request_context(workspace_id=...)` (209).
  5. 4-key bot lookup via `BotRegistryService.lookup(record_tenant_id, workspace_slug, bot_id, channel_type)` (212-215); miss → job failed `BOT_NOT_FOUND`.
  6. Token-ledger contextvars: `record_bot_id_ctx` / `channel_type_ctx` / `mode_ctx("query")` (237-239).
  7. PII redaction at worker boundary, per-bot opt-in `plan_limits.pii_redaction_enabled` (272-278).
  8. 3-way `asyncio.gather` persist: user message (fresh `uuid4()`), request_log (`request_id = job_id`), job→`running`; any failure re-raised so the message NACKs (290-330).
  9. Batched `system_config` snapshot: `get_many(_CHAT_CONFIG_KEYS)` — one round-trip for ~180 keys (337-341); `StepTracker` honoring `batch_step_logging_enabled` (345-349).
  10. Two quota gates: legacy tenant `token_budget.ensure_affordable` (378-396) and per-bot `tokens_used vs compute_effective_max_tokens` with Redis L1 key `ragbot:bot:tokens_used:{bot_cfg.id}` (401-472); refusal text walks the 7-tier `oos_template_resolver`, webhook `send_quota_exhausted`, job + request_log finalized.
  11. History: `conv_repo.get_by_id` + MT-1 cross-transport merge with `chat_histories` via `HistoryReconciler.load_chat_histories_turns` → `merge_history_sources` (488-529); failure degrades to empty history.
  12. `pipeline_config = _build_pipeline_config(_cfg, bot_cfg)` (549) — see 1.2.4.
  13. Semantic-cache threshold drift warning below `SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED` (553-563).
  14. `get_graph(**build_graph_di_kwargs(container))` — canonical LangGraph assembly (571); optional KG service + per-request session factory (567-577).
  15. OOS template resolver + `SysPromptAssembler` (governed append, ADR-W1-S10) with fail-soft fallbacks to owner columns (584-609).
  16. `asyncio.wait_for(graph.ainvoke(...), timeout=pipeline_timeout_s)` — timeout → `failure="PIPELINE_TIMEOUT"` (637-644).
  17. Any exception → `failure=str(exc)` + `error_notify_hook.on_ai_error` fire-and-forget (655-672).
  18. `tracker.flush()` then `_persist_and_callback(...)` (679-714).

**`config.py` (306 lines)** — `_parse_intent_list` (JSON-or-CSV admin values, warns on malformed JSON that looks like a list, config.py:29-50), `_cfg_int/_cfg_float/_cfg_bool/_cfg_get` coercion helpers mirroring `SystemConfigService` semantics (53-99), and `_CHAT_CONFIG_KEYS` — the ~180-key batched tuple (104-306) kept in parity with the test_chat endpoint via `tests/unit/test_pipeline_cfg_keys_parity.py` (per comments at 186-202).

**`payload.py` (120 lines)** — `_maybe_redact_chat_query` (per-bot opt-in redaction, audit event `pii_redacted` with counts only, graceful degradation, payload.py:25-92) and `_resolve_record_tenant_id` (UUID claim wins; legacy INT translated by importing the **private HTTP-middleware helper** `_resolve_upstream_int_tenant` from `interfaces.http.middlewares.tenant_context` — payload.py:110-118).

**`pipeline_config.py` (501 lines)** — `_build_pipeline_config(_cfg, bot_cfg)`: assembles the ~140-key `pipeline_config` dict. Per-bot override via `resolve_bot_limit` for ~28 keys (top_k, rerank_top_n, cliff params, grounding, cascade/hyde/CR flags, semantic-cache knobs...), `resolve_semantic_cache_threshold` special case where per-bot wins outright (90-96); the remaining ~110 keys are system_config-only pass-throughs (mostly `None` → downstream `_pcfg` defaults).

**`callbacks.py` (322 lines)** — `_persist_and_callback`: re-uses `conv_for_history` (75-77); persists the assistant `Message` + `ChatAnswered` outbox event in one UoW (101-140); resolves `callback_url` payload > bot (144-148); failure branch: finalize failed + failure callback via `create_delivery` (150-198); success branch: `request_chunk_refs` from graded chunks + STEP-5 stats-entity attribution (202-229), `finalize_request_log` success (230-242), chat-completed hook registry two-stage fire (247-271), job success + success callback with tokens/cost/citations/duration (273-311), Prometheus counters + p99 outlier record (313-322).

### 1.3 `workers/document_worker.py` (801 lines) — the production ingest consumer
- Subscribes `document.uploaded.v1`, `durable_name="document-worker"`, `queue_group="documents"` (781-786). `_handler` re-raises (775-779).
- `handle_document_uploaded` binds context, `mode_ctx("ingest")`, try/finally clear (138-162).
- `_handle_document_uploaded_inner`:
  1. Payload parse — `payload["record_tenant_id"]`, `["record_bot_id"]`, `["document_id"]`, `["job_id"]`, `["trace_id"]`, `["source_url"]`, `["tool_name"]` are **bracket lookups outside the try block** (225-232).
  2. Workspace slug resolve with fallback (243-251), RLS GUC bind (254).
  3. Job → `running` (260).
  4. Phase-D ingest observability: synthetic `request_logs` row (`connect_id="ingest"`, BIGINT `message_id` = masked sha256(job_id) low-8-bytes) + `StepTracker(kind="ingest")` with PII mask; failures degrade to `None` tracker (270-328).
  5. Content acquisition: reuse `documents.raw_content` **only** for non-refetchable `local://` sources (350-374); otherwise re-fetch: Google viewer URL → export URL rewrite (`to_export_url`) with mime/name override (405-417), VLM image parser branch gated by `system_config.vlm_provider` + `supports_vision` (427-430, 165-219), fetch bytes with `follow_redirects=True` (443-448), `detect_parser_robust` (mime → ext → byte-sniff) (449-458), parse and **join chunk contents with `"\n\n"` into flat `full_text`** (460-466); OCR fallback for whatever the registry can't parse, which is the only path producing typed `parsed_blocks` (490-501).
  6. `DocumentService` constructed inline with cfg service, source validator (best-effort DI), NarrateService (registry `build_narrate`, default OFF per alembic 0230), `LLMChunkContextProvider`/`ChunkContextEnricher` (CR), stats repo, corpus version service (513-610).
  7. `doc_service.ingest(record_bot_id=..., content=full_text, ..., blocks=parsed_blocks, step_tracker=...)` — **no `raw_bytes`** (613-626).
  8. Success: `DocumentIngested` outbox event (`corpus_version=CorpusVersion(1)` hardcoded, 629), job success (`"corpus_version": 1`, 655-662), BE-to-BE idempotency `mark_done` (668-683), metrics.
  9. Failure: `error_notify_hook`, job failed, `DocumentFailed` outbox, metrics; DLC-2 transient classification `_TRANSIENT_INGEST_ERRORS` (106-114) → terminal failures mark the idempotency row `failed`, transient failures re-raise so the bus skips XACK (732-761).

### 1.4 `workers/document_recovery_worker.py` (460 lines) — stuck-document sweeper
- Loop every `DEFAULT_RECOVERY_INTERVAL_S=300` (constants `_20:225`), scans BYPASSRLS (`container.system_session_factory()`, 387) for docs `state='DRAFT'` older than 900s OR `state='active'` with 0 chunks (SQL 174-197), anti-dup LEFT JOIN on outbox with time-bounded cooldown `DEFAULT_RECOVERY_REPLAY_COOLDOWN_S=3600`.
- Per stuck row: builds a canonical `document.uploaded.v1` payload (**fresh `job_id` uuid4**, 227) and INSERTs an outbox row + HMAC audit row inside `session_with_tenant` (240-301). Prometheus `document_recovery_replayed_total`.
- Narrow exception policy on both loops (transient classes only, programmer bugs bubble) — matches CLAUDE.md policy (338-341, 410-413).
- Runs embedded (FastAPI lifespan) or standalone `__main__`.

### 1.5 `workers/outbox_publisher.py` (252 lines) — outbox → Redis Streams
- `run_outbox_loop`: per-row `FOR UPDATE SKIP LOCKED` transaction (`repo.poll_one_for_update()`), publish (`publish_raw` with `Msg-Id` header = outbox UUID), `mark_processed_in_session`, commit — publish + mark atomic under the row lock (97-146). Publish failure → rollback + fresh-tx retry-count bump or DLQ at `max_retries=5` (181-206). Legacy batch fallback kept only for test fakes (209-237). Top-level `except Exception` log+sleep+continue is a policy-allowed entrypoint wrapper (78-80).

### 1.6 `workers/ai_config_listener.py` (213 lines) — config/cache invalidation consumer
- Subscribes 5 subjects: `ai.config.changed.v1`, `bot.config_updated.v1`, `bot.registry.changed.v1`, `system_config.changed` (`SUBJECT_SYSTEM_CONFIG_CHANGED`), token-revoked (33-39).
- `handle_config_changed`: system_config key → local Redis cache delete (66-75); token revoked → delete `ragbot:token_ver:{service}` (82-94, prefix mirror duplicated at 31); bot registry → `registry.invalidate(4-key)` with UUID coercion, else `invalidate_all()` (97-134); default → `model_resolver.invalidate(record_tenant_id=tenant_id, record_bot_id=bot_id)` + `llm.refresh_routing()` (136-151).

### 1.7 `interfaces/http/embedded_workers.py` (303 lines) — single-process supervisor
- FastAPI lifespan spawns 5 tasks (`app.py:437`): document consumer (same durable/queue-group as standalone → consumer-group sharing, not duplication), outbox publisher, recovery worker, cost-cap alerter (BYPASSRLS read sweep), semantic-cache purge GC (238-271). `_supervise` catches only transient classes; **no auto-restart by design** (208-236). `stop_embedded_workers` cancels + drains (274-294).

### 1.8 Pipeline connection map
```
BE partner → POST /api/ragbot/documents/create (202) → outbox(document.uploaded.v1)
   → outbox_publisher (FOR UPDATE SKIP LOCKED → XADD+Msg-Id)
   → Redis Stream ragbot:document.uploaded.v1
   → document_worker/_dispatch_one (fairness sems → dedup hint → handler → inbox mark → XACK)
   → fetch/parse/OCR → DocumentService.ingest (U1-U7) → DocumentIngested/DocumentFailed outbox (no consumer)
   ↑ document_recovery_worker re-emits for stuck DRAFT / active-0-chunk docs

BE partner → POST chat (async) → outbox(chat.received.v1) → publisher → stream
   → chat_worker.pipeline (validate → 4-key lookup → PII → persist×3 → quota → history
      → pipeline_config → LangGraph ainvoke → persist assistant + ChatAnswered outbox (no consumer)
      → finalize + hooks → callback POST (HMAC + SSRF-guard + retry))

admin config write → outbox(system_config.changed / bot.registry.changed / token.revoked)
   → ai_config_listener (NO deployment vehicle found — see W-05)
```

---

## 2. Findings

### W-01 · CRITICAL · Bus "retry" never re-runs the handler — XCLAIMed messages are claimed, counted, and dropped; the exactly-once retry story both workers rely on does not exist
**FACT.** Both workers deliberately re-raise on failure so the bus skips XACK, with the explicit expectation that "`recover_pending_messages` will XCLAIM and retry (up to 5 deliveries before dead-letter)" (`chat_worker/pipeline.py:758-762`, `document_worker.py:755-761`, `document_worker.py:776-779`). But:
- `_dispatch_one` swallows the handler exception and returns without XACK (`redis_streams_bus.py:465-468`) — message stays in this consumer's PEL. Correct so far.
- The consumer loop reads **only new messages**: the sole `xreadgroup` in the entire codebase uses `{key: ">"}` (`redis_streams_bus.py:508-513`; grep over `src/` shows no other `xreadgroup` and no `xautoclaim`).
- `recover_pending_messages` (`redis_streams_bus.py:571-618`) XPENDINGs, dead-letters entries with `times_delivered > 5`, XCLAIMs the rest **to the same consumer**, then `return len(claimed)` — the claimed message bodies are **never dispatched to the handler**. The caller ignores the return value (`redis_streams_bus.py:504-507`).
- XCLAIM resets idle and increments the delivery counter, so the loop is: fail once → claim-bump every ~60s → after ~5-6 minutes `times_delivered > DEFAULT_BUS_DLQ_MAX_DELIVERIES (=5,` constants `_07_llm_sampling_defaults.py:144)` → parked to `{stream}:dlq` — **with exactly one handler execution ever**.

**Failure scenarios (concrete):**
- Chat: transient DB blip during the persist gather (`pipeline.py:318-330` re-raises) → job stays `running` forever (status was set `running` in the same gather), user never gets an answer or a failure callback, message silently parks in DLQ. The Z2-P0-2 fix's stated goal ("job stays running forever" prevention) is not achieved.
- Ingest: 429/5xx from embedder → `_TRANSIENT_INGEST_ERRORS` re-raise (`document_worker.py:760-761`) → no retry ever happens; the doc is rescued only ~15 min later by the recovery sweeper (which re-emits a fresh event). Chat has **no** equivalent sweeper — chat is effectively at-most-once.

**Test-health corollary (FACT):** `tests/unit/test_workers_handler_reraise.py:1-16` docstring pins the wrong contract ("recover_pending_messages will XCLAIM and redeliver"); `tests/unit/test_redis_streams_recovery.py` only asserts that `xclaim` is called and a count returned — no test anywhere asserts a claimed message is re-processed.

**Expert fix direction:** in the recovery pass, dispatch claimed `(id, fields)` through `_dispatch_one` (XCLAIM returns full bodies), or switch to `XAUTOCLAIM` + dispatch loop; keep the DLQ branch as-is.

### W-02 · HIGH · Chat handler is not idempotent under redelivery — duplicate user messages + request_log PK collision
**FACT.** `internal_message_uuid = uuid4()` per attempt (`pipeline.py:290`) → a redelivered message INSERTs a second user-message row. `request_log_repo.create_request_log` is a plain INSERT with `request_id` PK = `job_id` (`request_log_repository.py:82-97`, no ON CONFLICT) → the second attempt raises IntegrityError inside the gather → `pipeline.py:330` re-raises → the retry can never succeed even once W-01 is fixed; the message will grind to DLQ while duplicate user messages accumulate (message insert commits before the request_log insert fails — three independent sessions, `pipeline.py:283-289`). The bus's transactional-inbox exists precisely for this (`redis_streams_bus.py:223-249` `inbox_tx` hook) but the chat handler uses the legacy 1-arg signature, whose documented contract is "handler must be idempotent" (`redis_streams_bus.py:193-207`) — the chat handler is not.
**Fix direction:** derive the message UUID deterministically (e.g. uuid5 of job_id), upsert request_log, or adopt the `inbox_tx` hook.

### W-03 · HIGH · Failed/timeout pipeline still persists an "(empty)" assistant message and emits a `ChatAnswered` event before the failure branch
**FACT.** `_persist_and_callback` builds and persists the assistant message with `content=answer_text or "(empty)"` and adds the `ChatAnswered` outbox event **unconditionally** (`callbacks.py:101-140`); the `if failure:` branch comes after (`callbacks.py:150`). On `PIPELINE_TIMEOUT` or any pipeline exception:
- conversation history permanently gains an `"(empty)"` assistant turn → next turn's history merge (`pipeline.py:498-521`) feeds `"(empty)"` to the LLM as a real prior answer (multi-turn quality pollution, T1);
- a `ChatAnswered` domain event with `answer=""` is committed to the outbox even though the request failed (semantically it should be `ChatFailed`, which exists in `domain/events/chat_events.py:77-86` but is **never raised anywhere** — grep shows no constructor call).
Also `latency_ms=0` is hardcoded in the event though `_req_t0` is available (`callbacks.py:135`).

### W-04 · HIGH · Multi-format parity break: the canonical async ingest path can never hit the row-preserve (1-row→1-chunk) Excel/Sheets path — worker flattens parser output to text and never passes `raw_bytes`
**FACT (code path), HYPOTHESIS (quality delta magnitude).**
- `POST /documents/create` is 202-queue only (`documents.py:92-188`) → `document_worker` is the production parse path for URL ingest.
- The worker fetches bytes, parses via registry, then **joins chunk contents to flat text**: `full_text = "\n\n".join(c["content"] ...)` (`document_worker.py:464-466`) and calls `ingest(content=full_text, blocks=parsed_blocks)` with **no `raw_bytes`** (`document_worker.py:613-626`). On the registry path `parsed_blocks` is explicitly `None` (`document_worker.py:341-345`).
- Inside `DocumentService.ingest`, the row-preserve chunking (`parser_preserve`, strategy that suppresses whole-doc collapse and orphan-merge to keep 1-row→1-chunk semantics for `excel_openpyxl` / `google_sheets`) triggers **only** from `parser_row_chunks`, which is populated **only when `raw_bytes` is provided** (`ingest_core.py:314-337`, `ingest_stages.py:125-145`, `ingest_stages.py:763-767`). The only callers passing `raw_bytes` are `routes/sync.py:566` and the internal test harness `test_chat/document_routes.py:521`.
- Net: an XLSX/Google-Sheets document ingested through the canonical BE-to-BE API relies on smart_chunk's surface-form re-detection of the joined markdown, while the same file through sync/test paths gets the authoritative metadata-stamped row-atomic path. The G2 comment calls the stamp "the authoritative signal … independent of the parsed markdown's surface form" (`ingest_stages.py:136-140`) — the production path only has the non-authoritative form. This is exactly the cross-row price-conflate class of bug the orphan-merge guard exists for (`ingest_stages.py:790-798`).
**Fix direction (canonical-ingest-flow):** worker should pass `raw_bytes=_raw` + `file_name`/`mime_type` to `ingest()` and delete its own parse pre-pass (collapse to thin adapter), or at minimum thread the parser chunk dicts through instead of joining.

### W-05 · HIGH · `ai_config_listener` has no deployment vehicle — cross-replica config/token/bot-registry invalidation events are published but (in the repo's own topology) never consumed
**FACT (repo state), HYPOTHESIS (runtime).** The listener exists only as a standalone `__main__` (`ai_config_listener.py:160-213`). `start_embedded_workers` starts 5 tasks — document consumer, outbox publisher, recovery, cost-cap, cache purge — and **not** the listener (`embedded_workers.py:238-271`). Deploy units in-repo: `deploy/ragbot-chat-worker.service`, `ops/systemd/ragbot-document-worker@.service`, monthly-reset, token-reconcile — **no listener unit**; `docker-compose.yml` defines no worker services. Meanwhile the listener is the designated fix for "Bug 2 (P0) system_config cross-replica invalidation" and "Bug 5 (P1) token revocation invalidation" (`ai_config_listener.py:60-94`). Unless an operator runs it by hand, system_config writes and token revocations remain stale on peer replicas until TTL, and `bot.registry.changed` / `ai.config.changed` events are dead letters on the stream. CHƯA verify — cần `systemctl list-units | grep ragbot` trên host production để xác nhận listener có chạy không.

### W-06 · MEDIUM · Dead fan-out: `chat.answered.v1`, `document.ingested.v1`, `document.failed.v1` have zero consumers; `ChatFailed`/`ChatDeliveryFailed` events are never emitted at all
**FACT.** The only `bus.subscribe` sites in `src/` are: embedded doc consumer (`embedded_workers.py:74`), standalone doc worker (`document_worker.py:781`), chat worker (`pipeline.py:776`), ai_config_listener (`ai_config_listener.py:187`). The worker tail publishes `ChatAnswered` (`callbacks.py:119-139`) and `DocumentIngested`/`DocumentFailed` (`document_worker.py:631-653`, 709-725) through the outbox → published onto streams nobody reads (bounded by `DEFAULT_STREAM_MAXLEN`, so no unbounded growth, but pure T2 waste + misleading architecture). `ChatFailed` and `ChatDeliveryFailed` classes exist (`chat_events.py:77-99`) with no constructor call anywhere — dead code.

### W-07 · MEDIUM · Recovery replay mints a fresh `job_id` — the partner's original job can never complete, and `update_status` hides it as a silent 0-row UPDATE
**FACT.** `_build_replay_payload` sets `"job_id": str(_uuid.uuid4())` (`document_recovery_worker.py:227`). The replayed worker run calls `job_repo.update_status(job_id, ...)` for a job row that was never created; `update_status` executes `UPDATE ... WHERE id=:job_id` without a rowcount check (`job_repository.py:64-77`) → silent no-op. The partner polling the ORIGINAL upload's job_id sees `running`/`queued` forever even after the recovery replay successfully ingests the doc. Also the replay payload omits `idempotency_key` (`document_recovery_worker.py:219-237`) while `mark_done` is gated on it (`document_worker.py:668-683`) → a BE-to-BE idempotency row for the crashed original stays `"processing"` until TTL even after successful recovery.
**Secondary (FACT):** there is no replay-attempt counter — a permanently broken doc (e.g. dead source_url) is re-swept every cooldown hour forever (`document_recovery_worker.py:141-149` documents the cooldown as retry-with-backoff, but there is no terminal give-up).

### W-08 · MEDIUM · No per-conversation ordering — rapid consecutive messages in one conversation race
**FACT (mechanism), HYPOTHESIS (frequency).** Dispatch parallelism is bounded by workspace (10) / bot+channel (5) / global (5) semaphores (`redis_streams_bus.py:392-401`) and the worker's own semaphore (4) — none keyed by conversation. Two quick messages from the same user in one conversation are processed concurrently: message B's history load (`pipeline.py:488-521`) runs before message A's assistant persist commits → B answers without A's context; assistant messages can commit out of order. Multi-turn correctness (the same class as the QAQC spa-booking "bot quên info" report, memory 2026-05-28) depends on the user typing slower than the pipeline.

### W-09 · MEDIUM · Callback delivery config is platform-global: one shared HMAC secret + retry/SSL policy for all tenants/bots
**FACT.** `_callback_hmac_secret`, `callback_max_retries`, `callback_timeout_s`, `callback_verify_ssl` are read from global `system_config` only (`pipeline.py:633-636`) — no `resolve_bot_limit`/tenant tier, unlike ~28 other keys. All tenants' callbacks are signed with the same secret (default `""` = unsigned); per-tenant rotation or per-bot verify_ssl exceptions are impossible. The comment at `callbacks.py:143` even promises "request > bot > tenant > None" but the code implements only request > bot (`callbacks.py:144-148`) — comment/code drift marking the missing tenant tier.

### W-10 · MEDIUM · Google Sheets URL ingest silently drops every tab except one
**FACT (URL semantics).** `to_export_url` rewrites a Sheets viewer URL to `export?format=csv[&gid=N]` (`google_link_service.py:210-213`, reached from `document_worker.py:405-409`). CSV export returns exactly one worksheet (the `gid` tab, or the first tab when no gid). A partner ingesting a multi-tab workbook URL gets one tab's data with **no warning event** — the other tabs never become chunks; questions about them get refusals (silent coverage loss, the owner's #1 multi-doc/multi-format concern). `format=xlsx` + the excel parser would preserve all sheets.

### W-11 · MEDIUM · Malformed document payload crashes before the try/terminal-classification and burns the whole retry/DLQ budget with zero job-status trail
**FACT.** `payload["record_tenant_id"]` / `["record_bot_id"]` / `["document_id"]` / `["job_id"]` / `["trace_id"]` / `["source_url"]` / `["tool_name"]` bracket-lookups sit at `document_worker.py:225-232`, before `job_repo` is even resolved and outside the `try` at 331. A payload missing any of these (or with a non-UUID) raises KeyError/ValueError → propagates → no XACK → W-01 claim-loop → DLQ, with no `jobs` update and no `DocumentFailed` event. Contrast: the chat worker parses job_id first and fails the job gracefully (`pipeline.py:131-139`). The DLC-2 design explicitly classifies ValueError/TypeError/KeyError as terminal ("retrying just burns budget", `document_worker.py:99-105`) but that classification never sees these errors.

### W-12 · MEDIUM · Zero-hardcode violations across the worker layer (numbers that belong in `shared/constants.py`)
**FACT.** CLAUDE.md: "KHÔNG MỘT CON SỐ NÀO inline trong code ngoài shared/constants.py" (whitelist 0/1/100/indices).
- `pipeline.py:632` — `_cfg_int(_cfg, "pipeline_timeout_s", 60)`: no `DEFAULT_PIPELINE_TIMEOUT_S` constant exists (grep over constants pkg = 0 hits).
- `pipeline_config.py` inline defaults: `6` (73), `500` (74, 75, 123), `2` (76), `50` (97), `8000` (125), `5` (135 bm25 flags, 386 short-query), `0.3` (140, 297), `12` (274), `0.88` (435), `0.7` (436), `3600` (387).
- `outbox_publisher.py:46-48` — `poll_interval_s=0.2`, `batch_size=100`, `max_retries=5` in the signature.
- `redis_streams_bus.py:576-577` — `min_idle_ms=30_000`, `count=10`; `:504` — `% 12` recovery cadence.
- Redis key literal `ragbot:bot:tokens_used:` duplicated in 5 places, 3 as inline f-strings including `pipeline.py:412` (drift risk vs `token_usage_redis_hook.py:18` / `quota_threshold_notify_hook.py:21`).
Failure scenario: operator tunes `chat_max_history`-style knobs assuming constants SSoT; these fallbacks drift silently between the worker and the test_chat builder (exactly the Bug #7b/7c class already documented at `pipeline_config.py:264-268`).

### W-13 · LOW-MEDIUM · `ai_config_listener` default invalidation path passes unconverted tenant/bot identifiers → prefix-mismatch no-op invalidation
**FACT (code shape), HYPOTHESIS (impact).** For `ai.config.changed.v1`/`bot.config_updated.v1`, `tenant_id` may be a legacy INT (`payload.get("tenant_id")`, `ai_config_listener.py:54-58`) and `bot_id` the external slug; both are passed raw to `resolver.invalidate(record_tenant_id=..., record_bot_id=...)` (136-139), which builds a string prefix from them (`_cache_mixin.py:96-102`). Cache entries are keyed by UUIDs, so an INT tenant or slug bot builds a prefix that matches nothing → stale model bindings survive until TTL. The registry branch does the UUID coercion correctly (106-124); this branch doesn't. Additionally `invalidate()`'s docstring claims "in-process + Redis prefix" but the body only filters `self._mem` — Redis relies on TTL (`_cache_mixin.py:90-103`), so cross-replica invalidation of the resolver is TTL-bound even when the listener runs.

### W-14 · LOW · Graceful shutdown cancels in-flight handlers mid-pipeline; with W-01, that work is lost, not redelivered
**FACT.** `chat_worker.main`: on SIGTERM, `sub.unsubscribe()` cancels the consumer loop task (`pipeline.py:790-792`; `_StreamSubscription.unsubscribe`, `redis_streams_bus.py:77-82`), which cancels the in-flight `_dispatch_one` children awaited by the loop's gather (`redis_streams_bus.py:519-526`). A chat mid-LLM-call is cancelled; message unACKed; per W-01 it is never re-run → job stuck `running`. There is no drain-then-stop (stop reading, await in-flight, then cancel). Same for the embedded document consumer (`embedded_workers.py:86-97`).

### W-15 · LOW · `clear_request_context()` does not reset `tenant_id_ctx` (or `mode_ctx`/`record_bot_id_ctx`/`channel_type_ctx`) — the docstring promise is partial
**FACT (code), impact contained by task-per-message.** `config/logging.py:178-185` resets only `tenant_id_int_ctx` + `workspace_id_ctx` + structlog vars; `tenant_id_ctx` (which `session_with_tenant` falls back to for the RLS GUC, `db/engine.py:155-174`) and the token-ledger vars set at `pipeline.py:237-239` / `document_worker.py:157-158` are not reset. Because each bus message runs in a fresh `asyncio.create_task` (contextvars copy-on-task, `redis_streams_bus.py:523`), cross-message leakage does not occur today — but any future refactor to sequential in-task dispatch (the `concurrency=1` mode processes messages as tasks too, so currently safe) would inherit a stale tenant GUC fallback. Defence-in-depth gap + docstring drift.

### W-16 · LOW · Misc observability/consistency nits
- `chat_worker_queue_depth` gauge counts in-flight handlers, not queue depth (`pipeline.py:763-773`) — misleading name for backpressure dashboards (comment in `main()` even says "queue depth").
- Job status vocabulary `delivered` / `delivery_failed` (`callbacks.py:183-187`, 307-311) is written after `success`, so a successful-but-callback-failed job reads `delivery_failed` — partner polling semantics must know this is post-success; not documented in the DTO layer.
- `document_worker` success `result={"corpus_version": 1}` + `CorpusVersion(1)` hardcoded (`document_worker.py:629`, 660) even though a `corpus_version_service` is injected right above (609) — the event lies once corpus versions move past 1.
- `_channel_type = payload.get("channel_type") or "unknown"` (`pipeline.py:123`) can label metrics/callbacks "unknown" though the validated value exists later — cosmetic.

### W-17 · Multi-axis assessment summary
- **Multi-bot (per-bot config honored?)** — Substantially yes on the chat path: ~28 keys resolve through `resolve_bot_limit` chains (`pipeline_config.py:68-71, 104-132, 136-141, 301-383, 395-401, 459-467, 487-492`), OOS template via 7-tier resolver, sysprompt assembler per-bot opt-outs, PII/quota/cascade/HyDE per-bot flags. Gaps: callback HMAC/retry (W-09), `multi_query_*`, `generation_temperature`, `pipeline_timeout_s`, grounding-by-intent dicts are global-only — a tenant cannot tune them per bot.
- **Multi-tenant** — Strong: 4-key lookup (`pipeline.py:212-215`), tenant-scoped repo calls throughout, RLS GUC binding for workers (`pipeline.py:209`, `document_worker.py:254`), BYPASSRLS used only for documented cross-tenant forensic sweeps (`document_recovery_worker.py:383-387`, `embedded_workers.py:153-156, 185-187`), recovery writes inside `session_with_tenant` (`document_recovery_worker.py:258-300`). Weak spots: shared callback HMAC secret (W-09); ingest-fairness keys parsed from raw payload without tenant verification (spoofable only by internal publishers — low risk).
- **Multi-format** — The worker's fallback path does honor the layered mime→ext→byte-sniff order (`document_worker.py:427-458`) and Google export rewrite; but the flat-text join (W-04) + single-tab CSV export (W-10) + `parsed_blocks` only on the OCR path (`document_worker.py:341-345, 490-501`) mean structure fidelity differs by transport: local bytes (sync route) > OCR URL > registry URL. That is a parallel-parse-path violation of the "MỌI format đi CÙNG 1 luồng canonical" mandate.
- **Multi-locale** — `DEFAULT_LANGUAGE = "vi"` (`constants/_02:230`) is the fallback for bots without `language` (`pipeline.py:588`); the only language-specific preprocessing knob is `vietnamese_preprocessing_enabled` default-True (`pipeline_config.py:130-133`). A bot in another language rides a Vietnamese-default pipeline unless config says otherwise — config-gated, so acceptable, but the platform default is locale-biased rather than neutral.
- **Multi-doc** — Nothing in the worker layer blocks cross-document retrieval (that lives in orchestration); the worker-level multi-doc risk is coverage loss from W-10 (missing tabs = missing docs).

### W-18 · Broad-except & policy compliance sweep (scope files)
**FACT.** `except Exception` counts: pipeline.py 19, document_worker.py 12, ai_config_listener.py 6, callbacks.py 4, outbox_publisher.py 3. All carry `# noqa: BLE001` with reasons and sit on entrypoint/best-effort/metrics paths — technically compliant with the 3-case policy. Marginal ones worth tightening: `pipeline.py:383` (quota gate — swallows even programmer bugs into a `QUOTA_EXCEEDED` outcome), `pipeline.py:471` (whole quota-gate resolve), `document_worker.py:369` (raw_content lookup). The recovery worker and embedded_workers are exemplary (narrow transient tuples, programmer bugs bubble). No domain literals, no brand strings, no version-refs in code identifiers found in scope (event subjects `*.v1` are wire-schema versioning, the accepted analog of header versioning).

### W-19 · What is genuinely good here (evidence, for balance)
- Outbox publisher exactly-once (per-row lock tx + XADD-verify `BusError` contract) is correct and well-reasoned (`outbox_publisher.py:97-146`, `redis_streams_bus.py:301-333`).
- Transactional inbox + dedup-hint design is textbook (mark-after-process, hint never authorizes XACK) (`redis_streams_bus.py:39-66, 375-455`).
- NOGROUP auto-recovery closes the FLUSHDB spin-loop (`redis_streams_bus.py:470-544`).
- DLQ is a replayable parking-lot stream, XADD-then-XACK, never log-and-drop (`redis_streams_bus.py:620-670`).
- PII redaction at both worker boundaries with identical audit shape (`payload.py:25-92`, mirrored in document_service).
- Callback delivery has deliver-time SSRF re-resolution, HMAC timestamped signatures, pooled client, exponential backoff (`callback_delivery.py:83-151`).
- The DLC-1/DLC-2 idempotency lifecycle classification in document_worker is coherent (single source of truth tuple used by both the re-raise gate and the idempotency marking, `document_worker.py:99-119`).

---

## 3. Ranked recommendation order (T1/T2/T3)

| # | Finding | Tier | Effort |
|---|---|---|---|
| 1 | W-01 dispatch claimed messages (bus) | T1 (lost answers) | S — dispatch bodies returned by XCLAIM |
| 2 | W-03 move assistant-persist/outbox behind the failure branch; emit ChatFailed | T1 | S |
| 3 | W-02 idempotent chat persists (uuid5 + upsert or inbox_tx) | T1 | M |
| 4 | W-04 worker passes raw_bytes; delete parallel parse pre-pass | T1 (table corpora) | M |
| 5 | W-05 ship listener unit or embed it | T2 | S |
| 6 | W-07/W-11 recovery job_id reuse + rowcount check; parse payload inside try | T2 | S |
| 7 | W-08 per-conversation serialization (fairness key or Redis lock) | T1 multi-turn | M |
| 8 | W-09/W-10/W-12/W-13 config tiering, xlsx export, constants lift | T2/T3 | S each |
