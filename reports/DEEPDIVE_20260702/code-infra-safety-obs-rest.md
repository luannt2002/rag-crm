# Deep-read report — infrastructure/guardrails + safety + observability + events + all remaining infrastructure subdirs

**Reader scope**: `src/ragbot/infrastructure/` — `guardrails/`, `safety/`, `observability/`, `events/`, plus every subdir NOT covered by sibling readers (`pii/`, `notify/`, `delivery/`, `chat_hooks/`, `cag/`, `chunking_strategy/`, `chunk_quality/`, `conversation_state/`, `convo_summary/`, `doc_profile/`, `embedding_text/`, `entity_extractor/`, `graph/`, `hyde/`, `idempotency/`, `narrate/`, `proximity_cache/`, `query_router/`, `rate_limiter/`, `resilience/`, `retrieval/`, `security/`, `self_rag_router/`, `sentence_similarity/`, `tenant_model_tier/`, `text_normalizer/`, `tokenizer/`, `token_ledger/`, `tools/`).
**Method**: every file read line-by-line; every wiring claim verified by grep against `bootstrap.py`, `document_worker.py`, `chat_worker/`, `orchestration/`. Labels: **FACT** (evidence `file:line`) vs **HYPOTHESIS** (needs runtime verification).
**Date**: 2026-07-02. Read-only pass; no source files modified.

---

## Part 1 — File-by-file: what it does + pipeline connection

### 1.1 guardrails/ (1,639 lines — LIVE)

| File | Purpose | Pipeline connection |
|---|---|---|
| `local_guardrail.py` (973) | `LocalGuardrail(GuardrailPort)` orchestrator: input rules (length, too_short, prompt-injection, PII regex, SQLi), output rules (system-prompt-leak shingles, secret scanner, regex grounding_check, LLM grounding judge). Rules come from DB `guardrail_rules` via `GuardrailRuleLoader` when wired, else from `_default_patterns` SSoT. Persists hits to `guardrail_events` via repo; raises `GuardrailBlocked` only on `severity="block"`. | Built in `bootstrap.py:359-366` (`providers.Factory(build_guardrail, provider="local", ...)`); consumed by `orchestration/nodes/guard_input` + `guard_output`. |
| `_default_patterns.py` (266) | SSoT for 12 platform-default regex rules (prompt_injection, 5 classic-injection, 3 VN PII, SSN, SQLi, secret_leak) + lazy compile cache + flag-csv parser. Alembic seed 010f inserts the same rows. | Fallback path of `LocalGuardrail` when no loader; alembic parity. |
| `math_lockdown.py` (238) | Pure functions `extract_numeric_claims` / `find_ungrounded_numbers` — VND money, percent, VN duration units, doc-ref `NN/YYYY` normalization. No I/O. | **NOT an answer-override** (compliant with sacred rule #10): only production caller is `orchestration/nodes/persist.py:149-152` which uses `extract_numeric_claims` to decide *cache-skip* for numeric answers. Comment in `guard_output.py:67` explicitly disclaims override. |
| `null_guardrail.py` (101) | Null Object — everything passes; never raises. | Registered `"null"` in registry; **unreachable in production** (provider hardcoded `"local"`, see F-13). |
| `registry.py` (56) | `build_guardrail(provider)` → `{"local","null"}`; unknown → Null. | `bootstrap.py:359`. |

**Grounding judge — warn-only verdict (special focus)**: FACT — the LLM grounding judge returns `GuardrailHit(severity="warn", action="hitl")` (`local_guardrail.py:541-552`); the regex `grounding_check` likewise (`local_guardrail.py:409-414`). `check_output` raises `GuardrailBlocked` only for `severity=="block"` hits (`local_guardrail.py:933`), so a FAILED grounding verdict never blocks — the answer ships to the user with a flag. No comment inside `local_guardrail.py` claims it blocks. However:
- `guard_output.py:100-104` comment says the fuzzy judge "FALSE-BLOCKS a correct answer" on the stats route — misleading wording (a warn flag can only trigger a reflect-retry regenerate, `orchestration/nodes/reflect.py:154-157`, never a user-facing block).
- The ONLY grounding-related block is the inverted case: judge *unavailable* → fail-closed refuse (`guard_output.py:217-231, 355-380`, default `DEFAULT_GROUNDING_FAILURE_MODE = "fail_closed"`, `constants/_14:305-306`). See F-2 for the resulting inconsistency.
- `action="hitl"` has **no consumer** anywhere — no HITL queue exists (grep: only `conversation_state/jsonb_conversation_state.py:266,287` also *emits* it). "hitl" is an aspirational label.

### 1.2 safety/ (1,209 lines — LIVE code, but 2 of 3 features unwired)

| File | Purpose | Pipeline connection |
|---|---|---|
| `sanitizer.py` (158) | `CleanBaseTier0Sanitizer` — 4-stage ingest scrub: HTML-tag strip, NFC, zero-width remove, prompt-injection blacklist → `SanitizeReport`. | **NOT WIRED** — see F-4. |
| `null_sanitizer.py` (44) | Passthrough + zero-count report. | Same registry; also unwired. |
| `domain_allowlist_validator.py` (190) | `DomainAllowlistValidator.is_allowed(url, patterns)` — per-bot source-URL allowlist (regex `re:` / URL-prefix / bare-host w/ subdomain match); fail-closed on all-invalid-regex config (PoisonedRAG defence). | **NOT WIRED** — see F-5. |
| `null_source_validator.py` (35) | Always-allow Null. | Same. |
| `pii_detector.py` (376) | `RecapPiiDetector` facade: two-gate (system `recap_pii_enabled` + per-bot `pii_redaction_enabled`) detect+mask with telemetry; internal `_RecognizerRegistryStrategy` fallback uses the full VN recognizer set **only when `pii_redactor is None`** (`pii_detector.py:185-188`). | Called from `document_service/ingest_helpers.py:329` (`_maybe_redact_ingest_content`). See F-3 for why it never masks. |
| `vn_recognizers.py` (245) | 15 VN recognizer specs (DSN, JWT, API keys, CCCD±spaces, card, plate, phone×3, email, CMND, bank acct, VN address) — all regex bodies from `shared.constants` (zero-hardcode compliant); `(start, -length)` overlap resolution. | Consumed only by `pii_detector._scan_with_recognizers`. |
| `registry.py` (149) | `build_source_validator` + `build_sanitizer` factories, graceful null fallback. | **Zero callers in src/** (only tests). |

### 1.3 observability/ (2,050 lines — LIVE)

| File | Purpose | Pipeline connection |
|---|---|---|
| `metrics.py` (525) | ~45 Prometheus collectors on a private `REGISTRY` (request/step durations, tokens, cost, guardrail, grounding fail+degraded, rate-limit, cache-stampede, cliff-drop, notify, warmup, failover…) + `MetricsRegistry` facade + `setup_metrics_app()`. | Imported by guardrails, invocation logger, notify dispatcher, p99 guard, chat_worker; `/metrics` route renders. |
| `invocation_logger.py` (359) | `InvocationLogger.invoke_model()` ctx-manager → single atomic INSERT into `model_invocations` with hashes, tokens, cost, status; ON CONFLICT defensive; prompt stored as sha256 hash only (privacy OK); tenant-scoped `fetch_by_message_id` (guessable BIGINT `message_id` + required `record_tenant_id` — good). | LLM/embed/rerank audit chain (INVARIANT #2); admin trace route. |
| `pipeline_audit_logger.py` (227) | Per-bot per-day JSONL appender (`reports/pipeline_audit_<bot>_<date>.jsonl`), weakref lock pool, size-cap `_partN` rotation, env toggle, default OFF. | DI singleton `bootstrap.py:93`; DocumentService leader-trace. |
| `warmup.py` (386) | Boot-time fail-soft probes: embedder/LLM/reranker/tokenizer `health_check()`, per-provider histogram. | FastAPI lifespan `asyncio.create_task`. |
| `sla_metrics.py` (225) | Pure classifiers p95/error-rate/cache-hit/CB-open → `SLAStatus`; `thresholds_from_config`. | **Zero consumers in src/ or scripts/** (only `tests/unit/test_sla_metrics.py` + hand-written `scripts/sla_alerting_rules.yaml`). Built-not-wired (F-16). |
| `p99_outlier.py` (100) | `record_chat_latency` → `chat_p99_outlier_total{intent,bucket}` + warn log. | `chat_worker/callbacks.py:27`. |
| `tracing.py` (137) | OTel init (opt-in env), no-op fallback, Sentry init. | `invocation_logger`, app boot. **Propagator import path broken** — F-11. |
| `prometheus_metrics_adapter.py` (49) | `MetricsPort` impl (step duration + rate-limit bypass). | DI. |
| `null_audit_logger.py` (21) | No-op AuditLoggerPort. | DI default. |

### 1.4 events/ (681 lines — LIVE)

`redis_streams_bus.py` — the platform event bus. XADD publish with `BusError` on failure (durability contract with the outbox publisher); `subscribe()` spawns an XREADGROUP loop per (subject, group) with 3-level fairness semaphores (workspace 10 → bot+channel 5 → global) and bounded overflow registry; transactional inbox (`event_inbox` table) with process-then-mark-then-XACK exactly-once (hook-aware handlers get `inbox_tx` to commit mark atomically with side-effects — genuinely well-designed); NOGROUP self-heal; DLQ parking-lot `{stream}:dlq` XADD-then-XACK. Subscribers: `embedded_workers.py:74`, `ai_config_listener.py:187`, `document_worker.py:781`, `chat_worker/pipeline.py:776`. **One serious defect in the recovery path — F-1.**

### 1.5 pii/ (283 lines — LIVE registry, one dead adapter)

- `vn_regex_pii_redactor.py` — VN-focused masking, 11 patterns from constants, longest-span-wins overlap logic, offsets against original text. Correct.
- `regex_pii_redactor.py` — older `PIIRedactorPort` adapter. **Dead code** (grep: imported only by its own `__init__.py`; zero callers/tests). Also internally buggy: spans computed against the *already-redacted* string (`regex_pii_redactor.py:34` iterates `out` not `text`), and dict order lets 10-digit `PHONE_VN` mangle a 12-digit CCCD prefix — the exact collision `vn_regex` fixed. Harmless only because dead.
- `presidio_pii_redactor.py` — declared stub, raises → registry falls back to Null. Fine.
- `registry.py` — `build_pii_redactor` `{null, vn_regex, presidio}`, fail-soft.

**PII placement (special focus)**: the hook *placement* is correct per claude-mem ("PII redaction TẠI HOOK LAYER"): chat boundary `chat_worker/payload.py:36-92` masks before message persist/request-log/LLM; ingest boundary `ingest_helpers.py:244-339` masks before chunk/embed. **But the provider is frozen to `"null"`** — F-3.

### 1.6 notify/ + delivery/ + chat_hooks/ (LIVE)

- `webhook_notifier.py` — quota-exhausted webhook, Redis SETNX throttle (fail = send, correct direction), env-config URL, NullNotifier. OK.
- `webhook_dispatcher.py` — error-alert dispatcher: resolver-driven config, sha-dedup window, per-minute Redis rate bucket, bounded semaphore, 4xx-no-retry/5xx-retry backoff envelope, 3 metrics. **Redis exception classes wrong** — F-9.
- `delivery/callback_delivery.py` — HMAC-signed POST of chat answers to caller `callback_url`, deliver-time SSRF re-resolution guard (DNS-rebinding closure — good), pooled client, exponential backoff. `noop_delivery.py` = poll mode. `create_delivery` factory dispatches on presence of callback_url.
- `chat_hooks/token_usage_db_hook.py` — `UPDATE bots SET tokens_used = tokens_used + :delta` (atomic). Comment claims "FOR UPDATE locks the bot row" but no `FOR UPDATE` exists in the SQL (`token_usage_db_hook.py:31` vs 37-43); the `SET LOCAL transaction_isolation='SERIALIZABLE'` (line 32-34) will raise in Postgres if the enclosing transaction already ran a query — F-14 (HYPOTHESIS on runtime, FACT on comment/code drift).
- `chat_hooks/token_usage_redis_hook.py` — post-commit INCR + `TTL_S = 60` inline class constant (not in shared/constants — minor zero-hardcode drift).
- `chat_hooks/quota_threshold_notify_hook.py` — threshold-cross detection + one-shot notify; respects `bypass_token_check`.

### 1.7 Remaining live dirs

- `conversation_state/` — `JsonbConversationState`: `conversations.action_state` JSONB load/save with TTL guard, UUID-validated `SET LOCAL app.tenant_id`, `_sanitize` whitelist + slot cap. Drift detection (service-lock, price-lock) — see F-7/F-8 for happy-case gaps. Wired: `bootstrap.py:641`.
- `doc_profile/` — `RuleBasedDocumentProfileAnalyzer` (AdapChunk L3): heading/table/formula/image/code/TOC/language 10-feature profile; script-range language detect (multilingual-positive); constants-driven. Minor: only ATX `#…####` headings counted (no setext/H5/H6); TOC literal markers are vi+en only, but the structural dotted-leader fallback compensates.
- `embedding_text/` — 4 strategies (prefix_plus_raw / raw_only / field_selective flood-cell strip / null). Shape-only, domain-neutral, registry-driven. Clean.
- `entity_extractor/` — `vi_underthesea` (NER+POS, lazy backend, language-gated) + `en_simple` (cap-run/acronym/numeric heuristics) + null. See F-12 (single global provider vs per-bot language).
- `graph/` — `KnowledgeGraphService` (LLM triple extraction → `knowledge_edges`, ILIKE seed + N-hop traversal) + `graph_retriever.graph_retrieve` node adapter. **Broken both directions** — F-6 (the biggest multi-doc finding).
- `narrate/` — LLM linearisation of TABLE/FORMULA/IMAGE blocks pre-embed, per-locale prompt packs, degrade-to-original (HALLU-safe). Default OFF by seeded system_config (alembic 0230); `narrate_lang` never threaded by `document_worker.py:557-566` → would use the `"vi"` pack for all tenants if re-enabled (multi-locale gap, currently moot).
- `rate_limiter/` — Redis sliding-window ZSET limiter (fail-open + metric; middleware may fail-closed) + in-memory twin + registry. Known small race: prune/count pipeline and ZADD are two round-trips, so N concurrent checks can over-admit by ~N; bounded, acceptable (info).
- `resilience/` — `_ResourceBreakerAdapter` base + redis/db/llm(null) breakers + `FailoverOrchestrator` cache (per-provider LLM suffix). Clean.
- `retrieval/` — `PgBM25Retrieval` (`websearch_to_tsquery('simple')` + `ts_rank_cd`, bot-scoped via `documents.record_bot_id` JOIN, soft-delete excluded, opt-in CR-combined tsvector) + null + registry. `'simple'` config = no stemming/no unaccent for any language — a known engine-wide lexical limitation, not a per-file bug.
- `security/` — `JwtVerifier` (RS256/HS256, leeway, issuer/audience), `hmac_signer`, `EnvSecretsAdapter` (AES-GCM KEK from env). Clean.
- `token_ledger/` — `AsyncDBTokenLedger` (bounded queue 10k, batch 200, 1s flush, drop+count on full — correct aux-sink degradation), `emit_aux_usage` snapshots 4-key identity from ContextVars. Clean.
- `idempotency/` — **empty package** (single docstring line, no code). Orphan placeholder.

### 1.8 Dead-marked dirs (acknowledged by in-repo notices, verified 0 live code lines)

`cag/` (Cache-Augmented Generation), `chunk_quality/`, `convo_summary/`, `hyde/` (superseded by `application/services/hyde_generator.py`), `proximity_cache/`, `query_router/`, `self_rag_router/`, `sentence_similarity/`, `tenant_model_tier/`, `text_normalizer/`, `tokenizer/`, `tools/` (MCP client) — all fully commented out with a standard "DEAD-CODE NOTICE 2026-06-03" header. `chunking_strategy/` is the odd one: carries a "DISABLED — UNUSED" header but its code is **live/importable** (imports, classes intact — `chunking_strategy/registry.py:22-66`), unlike the others. ~3,900 lines of commented corpse + ~400 lines of importable-but-unreachable selector. This is deliberate policy (reversible escape-hatch), reported here for completeness — the risk is only navigational noise + the inconsistent disable style.

---

## Part 2 — Findings (evidence-first)

### F-1 (HIGH, events, multi-doc reliability) — XCLAIM recovery never re-dispatches: failed/orphaned messages get zero retries, then DLQ
**FACT.** `redis_streams_bus.py:571-618`: `recover_pending_messages` XPENDINGs idle entries, XCLAIMs them to the current consumer, **returns `len(claimed)` and discards the claimed payloads** — no handler dispatch. The consumer loop reads only new messages (`xreadgroup(..., {key: ">"})`, line 508-513); nobody ever reads the PEL (`"0"`). Consumer names include a per-process uuid (`redis_streams_bus.py:358`), so every restart orphans the old PEL. Each ~60s recovery pass (line 504-507) re-claims the same entry, incrementing `times_delivered`, until it crosses `DEFAULT_BUS_DLQ_MAX_DELIVERIES = 5` (`constants/_07:144`) → dead-lettered.
**Failure scenario**: document-ingest handler hits a transient embed-API 429 → handler raises → no XACK → message idles → claimed 5× *without one retry* → parked in `ragbot:document.uploaded:dlq` ~5 minutes later; document stuck DRAFT until the separate recovery worker or an admin replays. The comments at `document_worker.py:756,777` and `chat_worker/pipeline.py:761` ("recover_pending_messages will XCLAIM and retry until the handler succeeds") describe behavior that does not exist. The at-least-once contract is effectively at-most-once-then-DLQ.
**Fix direction**: dispatch the `claimed` entries through `_dispatch_one`, or switch to `XAUTOCLAIM` + a periodic PEL re-read (`xreadgroup` id `"0"`).

### F-2 (HIGH, guardrails/T1) — Grounding gate is inverted: judge-says-ungrounded ships, judge-unavailable refuses
**FACT.** When the grounding judge RUNS and the unsupported ratio exceeds threshold, the hit is `severity="warn", action="hitl"` (`local_guardrail.py:541-552`) → answer ships to user, flag recorded, no HITL consumer exists (grep: zero readers of `action="hitl"`); the only effect is an optional reflect-retry (`reflect.py:154-157`). When the judge CANNOT run (LLM unwired), default `fail_closed` **replaces the answer with the OOS template** (`guard_output.py:355-380`; `DEFAULT_GROUNDING_FAILURE_MODE="fail_closed"` `constants/_14:306`). So the HALLU net blocks precisely the case where nothing was measured, and passes the case where the judge measured "ungrounded".
**Failure scenario**: bot answers with fabricated content, judge returns 4/5 NOT_SUPPORTED (ratio 0.8 > 0.3) → user still receives the fabricated answer; only `grounding_fail_total` ticks. HALLU=0 relies entirely on sysprompt + upstream retrieval, not on this gate.
**Note**: this may be intentional (avoid false-block), but then the fail-closed branch's justification "honouring HALLU=0 sacred" (`guard_output.py:225`) overstates what the net does. Owner decision needed: either escalate confirmed-ungrounded to the same refuse path (per-bot opt-in), or rename/document the judge as observability-only.

### F-3 (CRITICAL, safety/PII, multi-tenant) — PII redaction is inert end-to-end: provider frozen to "null", system_config knob dead, facade fallback unreachable
**FACT chain**:
1. `bootstrap.py:447-450` — `pii = providers.Singleton(build_pii_redactor, provider=DEFAULT_PII_REDACTOR_PROVIDER)` passes the compile-time constant `"null"` (`constants/_13:100`). The adjacent comment (line 441-446) claims "Provider resolved PER-CALL from system_config.pii_redactor_provider" — false; contrast `crag_grader_factory` at `bootstrap.py:435-441` which does use `providers.Callable(get_boot_config(...))`.
2. `pii_redactor_provider` IS whitelisted in `bootstrap_config.py:61` but **no call site reads it** (grep: only the whitelist row + comments).
3. Chat boundary: `chat_worker/payload.py:65` calls `pii_redactor.redact(text)` on the Null singleton → `(text, [])` → passthrough, even for a bot with `plan_limits.pii_redaction_enabled=true`.
4. Ingest boundary: `ingest_helpers.py:329` builds `RecapPiiDetector(pii_redactor=pii_redactor)`; the facade's full-VN-recognizer fallback activates only when the arg is `None` (`pii_detector.py:185-188`) — but callers always pass the Null instance, so with both gates open the decision is `no_entities_detected` and nothing masks.
**Failure scenario**: operator sets `system_config.pii_redactor_provider='vn_regex'`, `recap_pii_enabled=true`, bot opts in → user pastes CCCD + phone → raw PII persisted into `messages`, `document_chunks`, embeddings, request logs. All three layers of the two-knob opt-in work; the strategy under them is permanently a no-op. One-line fix in bootstrap (Callable + get_boot_config).

### F-4 (HIGH, safety, built-not-wired) — CleanBase Tier-0 sanitizer never runs; flag defaults ON but `_sanitizer` is never assigned
**FACT.** `ingest_stages.py:310` reads `_sanitizer = getattr(self, "_sanitizer", None)`; grep across src/ shows **no assignment** of `_sanitizer` anywhere (no constructor param, no bootstrap provider; `build_sanitizer` has zero callers outside `safety/registry.py` + one unit test). `DEFAULT_CLEANBASE_TIER0_ENABLED = True` (`constants/_20:40`), so every ingest logs `cleanbase_tier0_skipped reason=no_sanitizer_wired` at DEBUG (`ingest_stages.py:330-338`) — invisible at default log level. The docstring "Default-ON Tier-0" (`sanitizer.py:83`) and the T1-Safety corpus-poisoning defence (HTML strip, zero-width/Trojan-Source removal, injection-token defang) are fiction in production. Only the legacy `_clean_document_text` prompt-injection sweep still runs.
**Failure scenario**: attacker uploads a DOCX containing zero-width-joiner-obfuscated "ignore previous instructions" — Tier-0 (the layer designed to catch it) never executes.
**Side note (multi-format)**: if someone wires it, `HTML_TAG_REGEX = r"</?[A-Za-z!][^<>]*>"` (`constants/_19:78`) strips `<br>`/`<b>`/`<table>` markup that some parsers legitimately emit inside markdown, and can eat text between comparison operators (`x < 5 và y > 3` — verified: the regex consumes `< 5 và y >`). Wire-up should be paired with a table-content test.

### F-5 (HIGH, safety, built-not-wired) — Source-URL allowlist (PoisonedRAG defence) unreachable: container has no `source_validator` provider
**FACT.** `document_worker.py:522-528` guards with `if hasattr(container, "source_validator")`; `bootstrap.py` defines **no** such provider (grep exit 1) → `_src_validator = None` always → `DocumentService._maybe_validate_source_allowlist` short-circuits. A bot owner who populates `plan_limits.allowed_source_domains` + flips `source_allowlist_enabled` gets **no filtering**, silently. `DomainAllowlistValidator` itself (190 lines, careful fail-closed regex handling) has zero production callers.
**Failure scenario**: tenant configures allowlist `["docs.example.com"]`; attacker submits `https://evil.test/poison.pdf` via the ingest API → ingested normally.

### F-6 (CRITICAL, graph/T1, multi-doc) — GraphRAG is broken in BOTH directions by keyword-name mismatches, swallowed by broad-except; mocks hide it
**FACT (query side)**: `graph_retriever.py:59-64` calls `kg_service.query_graph(query=..., bot_id=record_bot_id, session=..., max_hops=...)` but the method signature is `query_graph(self, query, record_bot_id, session, *, max_hops, max_entities, channel_type)` (`knowledge_graph.py:179-188`) → `TypeError: unexpected keyword argument 'bot_id'` on every call → caught by `except Exception` (`graph_retriever.py:107-109`) → `graph_retrieve_failed` warning + `graph_context: []`. With `graph_rag_mode` enabled, graph retrieval returns nothing, always.
**FACT (ingest side)**: `ingest_core.py:801` calls `kg_service.store_triples(bot_id=bot_uuid, ...)` but the signature is `store_triples(self, record_bot_id, triples, session, *, source_chunk_id, channel_type)` (`knowledge_graph.py:128-136`) → TypeError → swallowed by the enclosing `except Exception` (`ingest_core.py:819-825`, `graph_rag_extraction_failed`). Triples are **extracted by LLM (cost paid) then never stored** — the loop aborts on the first chunk that yields triples.
**Failure scenario**: operator flips `graph_rag_default_mode='adaptive'` for multi-hop/aggregation queries (the exact multi-doc-join capability the owner cares about) → ingest burns extraction tokens, `knowledge_edges` stays empty, query node logs a warning per request and contributes zero chunks. Feature appears "on" in config and in step metadata.
**Test-health**: `tests/unit/test_node_graph_retrieve.py:55-56` mocks `kg.query_graph = AsyncMock(...)` — AsyncMock accepts any kwargs, so the suite cannot catch the mismatch; `store_triples` has no test at all (grep). This is the naming-convention bug class (`bot_id` vs `record_bot_id`) the project has hit twice before (per memory), now at the call-site/kwarg level where the pin tests don't look.

### F-7 (MEDIUM, conversation_state, happy-case-only) — Drift detection's service-token heuristic only parses comma-CSV rows; canonical markdown pipe tables never match
**FACT.** `jsonb_conversation_state.py:299-315` `_candidate_service_tokens` splits chunk lines on `","` and requires `parts[0].isdigit()` (pattern: `"4,Chăm sóc da,800.000"`). The platform's canonical ingest emits **markdown pipe tables** (`| 4 | Chăm sóc da | 800.000 |`) — `line.split(",")` yields one part, `parts[0].isdigit()` is False → zero candidate tokens → the `conversation_state_service_drift` rule can never fire for corpora ingested through the structured-markdown funnel. Works only for the one legacy layout (raw CSV chunks).
**Failure scenario**: booking bot with pipe-table price list; LLM drifts to a different service mid-flow → no drift hit, no HITL flag.

### F-8 (MEDIUM, conversation_state + math_lockdown, multi-locale) — Price/number logic is VND-only and Vietnamese-only in structure-deciding paths
**FACT.** `jsonb_conversation_state.py:61-63` `_PRICE_RE` suffixes `k|đ|VND|đồng`; price band clamps to `DEFAULT_PRICE_MIN_VND..DEFAULT_PRICE_MAX_VND` (`:337`). `math_lockdown.py:35-76` recognizes only VND units + Vietnamese duration words (`phút|giờ|tiếng|buổi|…`). Consequences: (a) a non-VND bot (USD/EUR corpus) gets **no** price-drift protection and **no** numeric-cache-skip (the `persist.py:152` cache-skip won't detect `$29.99` → numeric answers of non-VND bots DO get cosine-cached, re-importing the stale-number HALLU risk the skip was built to prevent); (b) violates the multilingual/currency-neutral mindset (currency should be config, not baked). This is the owner's #1 concern shape: works for the Vietnamese spa/legal demo corpora, silently degrades for any other locale.
**HYPOTHESIS (impact)**: unmeasured how many non-VND bots exist today; flagged as structural, not as a live incident.

### F-9 (MEDIUM, notify) — webhook_dispatcher catches builtin `ConnectionError/TimeoutError` but redis-py raises its own hierarchy → "never raises" contract broken, fail-open promise broken
**FACT.** `webhook_dispatcher.py:331` and `:354` catch `(OSError, ConnectionError, TimeoutError)`. Verified in this venv (redis 7.4.0): `redis.exceptions.ConnectionError.__mro__ = (ConnectionError→RedisError→Exception)` — no builtin OSError/ConnectionError ancestry. A Redis outage during `_is_duplicate`/`_is_rate_limited` therefore escapes, `dispatch()` raises despite its "never raises" docstring (`:120-125`), and since callers fire-and-forget via `asyncio.create_task`, the alert is dropped with only an unhandled-task traceback — the opposite of the documented "fail open (allow the alert through)". Compare `webhook_notifier.py:76` which correctly uses broad-except for the same pattern, and `redis_streams_bus.py` which imports `redis.exceptions.RedisError` properly.
**Failure scenario**: Redis down (the very incident you want alerts for) → error-alert webhooks stop sending.

### F-10 (MEDIUM, observability) — `InvocationLogger` finally-block INSERT is unprotected: a DB blip fails the successful LLM call and leaks the tracing span
**FACT.** `invocation_logger.py:244-246`: the audit INSERT+commit inside `finally:` has no try/except (unlike the Prometheus emit right below, `:249-268`). If the pool is exhausted / DB restarts at that moment, the exception propagates out of the `invoke_model` context manager, discarding an already-successful LLM response — contradicting the module's own "observability MUST never break the LLM call" (`:158`) and the graceful-degradation rule (aux sink must not kill the money path). Additionally `_span_cm.__exit__` (`:276`) is only reached after the INSERT, so the span leaks on that path.
**Failure scenario**: transient `TooManyConnections` during a traffic spike → user-facing 5xx on chat turns whose LLM answer had already arrived; the same spike that exhausts the pool amplifies into answer loss.

### F-11 (LOW-MEDIUM, observability) — OTel W3C propagation import path is wrong → cross-service trace stitching permanently off, with a misleading log
**FACT.** `tracing.py:191` `from opentelemetry.trace.propagation import TraceContextTextMapPropagator` — verified ImportError in this venv (class lives in `opentelemetry.trace.propagation.tracecontext`). The except branch logs `feature_disabled_dep_missing missing_pkg="opentelemetry.propagators"` (`:193-203`) — the package IS installed; the import path is a typo. Even with `OTEL_ENABLED=true`, `set_global_textmap` never runs. Local spans still work.

### F-12 (MEDIUM, entity_extractor, multi-bot/multi-locale) — one global `entity_extractor_provider` + hard language gates = entity expansion silently off for every bot whose language ≠ the platform-wide pick
**FACT.** Provider is a single boot-cached system_config key (`bootstrap_config.py:60` whitelist; container Singleton). `ViUnderthesseaExtractor.extract` returns `[]` for `language not in VI_DOMAIN_LANGUAGES` (`vi_underthesea_extractor.py:197-198`); `EnSimpleExtractor` returns `[]` for `language != "en"` (`en_simple_extractor.py:126-127`). There is no composite/per-language router adapter. On a multi-tenant platform with mixed vi/en bots, whichever provider is chosen disables entity-grounded multi-query expansion for the other cohort — by design "safe", but invisible (no metric distinguishes "gate-skipped" from "no entities"). Per-bot config is honored for *language*, not for *strategy*.

### F-13 (LOW, guardrails, zero-hardcode) — guardrail provider hardcoded `"local"` in bootstrap; `"null"` tier unreachable; `guard_output` depends on a private method of the concrete class
**FACT.** `bootstrap.py:361` `provider="local",  # TODO Phase 4: lift to system_config.guardrail_provider` — the registry + Null tier ("tenants on a free tier", `null_guardrail.py:5-9`) are unreachable via config. Related latent contract break: `guard_output.py:461,508` call `guardrail._persist(...)` — not part of `GuardrailPort` (grep `ports/guardrail_port.py`: no `_persist`); NullGuardrail has no such attribute → if the TODO is ever completed and a bot resolves `"null"` while grounding is enabled with the parallel path, the node raises AttributeError. Fix now (add persist to the Port or guard with getattr) to make the TODO safe to complete.

### F-14 (LOW, chat_hooks) — token_usage_db_hook comment/code drift + fragile `SET LOCAL transaction_isolation`
**FACT (drift)**: `token_usage_db_hook.py:3-4,30-31` claim "FOR UPDATE row lock" — no FOR UPDATE in the SQL (`:37-43`; the atomic UPDATE self-locks, so the comment is wrong, not the behavior). **HYPOTHESIS (runtime)**: `SET LOCAL transaction_isolation = 'SERIALIZABLE'` (`:32-34`) errors in Postgres when the enclosing transaction has already executed a query ("SET TRANSACTION ISOLATION LEVEL must be called before any query"); this hook runs inside the caller's transaction at stage="db" — whether a prior statement exists depends on the hook runner's ordering. Needs one integration run to verify; if it raises, token accounting for that turn fails.

### F-15 (LOW, pii) — `regex_pii_redactor.py` is dead code with internal bugs; delete or de-export
**FACT.** Zero callers (grep across src/tests); exported only via `pii/__init__.py:3`. Internal bugs (spans against mutated string `:34`; CCCD/phone overlap) documented in §1.5. Registry docstring (`pii/registry.py:4-6`) says "bootstrap currently binds RegexPIIRedactor directly" — stale: bootstrap binds via `build_pii_redactor`.

### F-16 (LOW, observability) — `sla_metrics.py` has zero production consumers
**FACT.** grep: no imports of `sla_metrics`/`classify_latency`/`SLAStatus` outside the module + its unit test. The promised consumers ("/health/sla route or a background reporter", `sla_metrics.py:15-18`) don't exist; `scripts/sla_alerting_rules.yaml` is hand-maintained (drift risk with `shared.constants` values — e.g. `DEFAULT_SLA_P95_WARN_SECONDS = 10.0`, `constants/_08:112` — nothing enforces the YAML matches). Built-not-wired.

### F-17 (INFO, observability) — p99 bucket label lies when threshold reconfigured
`p99_outlier.py:36-47`: bucket labels `"20-30"/"30-60"/"60+"` are string constants keyed to the default 20s threshold (`constants/_08:129`); `record_chat_latency` accepts `threshold_s` as a parameter, so a 10s threshold puts a 12s request in the `"20-30"` bucket. Cosmetic-but-misleading Grafana axis.

### F-18 (INFO, events/knowledge_graph, locale) — KG extraction prompt is Vietnamese-only; VN stopword list is unaccented
`knowledge_graph.py:36-47` `_ENTITY_EXTRACTION_PROMPT` is hardcoded Vietnamese (multi-locale gap if GraphRAG is ever fixed per F-6); `_extract_query_keywords` stopwords (`:358-364`) list unaccented forms ("cua","khong") that never match real diacritic Vietnamese queries → stopword filtering is a no-op for vi and the seed ILIKE gets noisy `%của%` patterns.

### F-19 (INFO, hygiene) — misc zero-hardcode / dead-param drift
- `webhook_dispatcher.py:52-68` retry/backoff/bucket constants + `message[:200]` (`:316`) module-inline instead of `shared/constants.py`.
- `token_usage_redis_hook.py:19` `TTL_S = 60` inline.
- `webhook_notifier.py:33-35` `_POST_TIMEOUT_S = 10.0` inline.
- `knowledge_graph.py:187` `channel_type` kwonly param of `query_graph` is never used in the SQL (dead param); `store_triples`/`extract_entities` default `channel_type="web"` — a 4-key value defaulted in infra (ingest_core doesn't pass it → all edges land under "web").
- `delivery/callback_delivery.py:108` `"Ragbot-Webhook/1.0"` UA string (protocol-surface version, acceptable; noted for the no-version-ref sweep).
- `idempotency/` — empty package, delete or implement.

---

## Part 3 — Axis summaries

**Happy-case-only (owner's #1 concern)** — the pattern in this scope is less "one table layout" and more "one locale + one wiring path":
- CSV-comma-only drift heuristic (F-7) vs the platform's own pipe-table canon.
- VND/Vietnamese-only numeric logic in drift-lock, math_lockdown cache-skip (F-8), KG prompt + stopwords (F-18), narrate default-`vi` prompt pack (§1.7).
- Safety chain assumes DI wiring that doesn't exist and degrades to no-op with DEBUG-level or misleading logs (F-3, F-4, F-5) — "works" only in the sense that nothing crashes.

**Multi-doc**: GraphRAG — the one subsystem in scope purpose-built for cross-document joins/multi-hop — is dead on both write and read paths (F-6). Bus recovery gap (F-1) additionally weakens multi-document ingest reliability under transient failures.

**Multi-bot**: per-bot knobs exist and are threaded (grounding thresholds/intents, leak shingle params, action_config drift severity, pii opt-in, allowlist), but three of them terminate in unwired strategies (F-3/F-4/F-5) and one in a global-single-provider (F-12). Guardrail provider itself is not per-anything (F-13).

**Multi-format**: this scope is mostly format-agnostic; the two format-sensitive spots are the Tier-0 HTML strip vs markdown-embedded HTML tables (F-4 note) and doc_profile's ATX-only heading counting (§1.7, minor).

**Multi-tenant**: generally good — guardrail events, invocation fetch (`invocation_logger.py:279-333`), conversation-state writes (UUID-validated GUC), BM25 bot-scoping all carry proper scoping. No cross-tenant leak found in scope. `pipeline_audit_logger` writes per-bot files by raw `bot_id` string into a shared `reports/` dir — operator-only surface, acceptable.

**Test-health**: AsyncMock-based node tests cannot catch keyword-name mismatches (F-6); regression pins exist for the *state-key* form of the bug (`test_p21_regression.py:55-64`) but not the *call-site kwarg* form; sanitizer/source-validator/sla_metrics have unit tests that pass while the features are unreachable in production — tests validate the strategy, not the wiring.

**CLAUDE.md compliance highlights**: sacred rule #10 is honored in this scope (math_lockdown not used to override; narrate/sanitizer outputs storage-only; guard_output substitutes only via the pre-existing refuse contract — though F-2 questions its direction). Broad-except usage is mostly within policy (background wrappers, aux sinks) with `noqa: BLE001` annotations; `jsonb_conversation_state.py:115,174` are test-pinned contracts. Zero-hardcode drift is minor (F-19). No brand/tenant literals found in scope. No URL `/vN/` version-refs (OTLP `/v1/traces` is external protocol).

---

## Part 4 — Priority table

| # | Sev | Finding | Evidence anchor |
|---|-----|---------|-----------------|
| F-3 | CRITICAL | PII redaction inert: provider frozen "null", config knob dead, facade fallback unreachable | `bootstrap.py:447-450` |
| F-6 | CRITICAL | GraphRAG broken both directions (`bot_id=` kwarg TypeError ×2, swallowed) | `graph_retriever.py:61`, `ingest_core.py:801` |
| F-1 | HIGH | Bus recovery XCLAIMs but never re-dispatches → zero retries, straight to DLQ | `redis_streams_bus.py:608-615` |
| F-4 | HIGH | CleanBase Tier-0 sanitizer never wired; flag default ON is fiction | `ingest_stages.py:310` |
| F-5 | HIGH | Source-URL allowlist (PoisonedRAG defence) unreachable — no container provider | `document_worker.py:523` |
| F-2 | HIGH | Grounding gate inverted: measured-ungrounded ships; unmeasured refuses | `local_guardrail.py:541-552`, `guard_output.py:355-380` |
| F-9 | MED | notify dispatcher catches wrong Redis exception classes → alerts die when Redis dies | `webhook_dispatcher.py:331,354` |
| F-10 | MED | InvocationLogger finally-INSERT unprotected → DB blip kills successful LLM turn | `invocation_logger.py:244-246` |
| F-7 | MED | Drift detection parses only comma-CSV rows, not canonical pipe tables | `jsonb_conversation_state.py:309-314` |
| F-8 | MED | VND/vi-only numeric logic (drift lock, cache-skip) — non-VND bots unprotected | `math_lockdown.py:35-76`, `jsonb_conversation_state.py:61` |
| F-12 | MED | Single global entity-extractor provider + language gates = silent no-op per cohort | `vi_underthesea_extractor.py:197` |
| F-11 | LOW+ | OTel W3C propagator import path wrong (verified ImportError) | `tracing.py:191` |
| F-13 | LOW | Guardrail provider hardcoded "local"; `guard_output` uses private `_persist` off-Port | `bootstrap.py:361`, `guard_output.py:461` |
| F-14 | LOW | token DB hook: FOR UPDATE comment false; SET LOCAL isolation fragile | `token_usage_db_hook.py:31-43` |
| F-15 | LOW | `regex_pii_redactor.py` dead + internally buggy | `pii/regex_pii_redactor.py:34` |
| F-16 | LOW | `sla_metrics.py` zero consumers; YAML drift unenforced | `observability/sla_metrics.py` |
| F-17..19 | INFO | p99 bucket labels, KG vi-prompt/stopwords, inline constants, empty idempotency pkg | see body |

**Cross-cutting theme**: this layer's engine code is largely well-built (ports/registries/null-objects, careful degradation, real exactly-once design in the bus) — the systemic failure mode is the **last mile of DI wiring**: five separately-shipped safety/quality features (PII, sanitizer, source allowlist, GraphRAG ingest, GraphRAG query) all pass their unit tests and all do nothing in production, each behind a silent or DEBUG-level degradation path. A one-page "wiring audit" (does each registry have a bootstrap provider reading its documented system_config key, and does one integration test exercise the real class un-mocked?) would have caught all five.
