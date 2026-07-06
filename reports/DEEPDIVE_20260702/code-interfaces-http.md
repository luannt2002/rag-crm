# Deep-Read Report — `src/ragbot/interfaces/http/`

Scope: every route file, middleware, and schema under
`src/ragbot/interfaces/http/`. Read line-by-line (18,283 LOC across 66 files).

Legend: **FACT** = verified against `file:line` / empirical repro / test file.
**HYPOTHESIS** = plausible from code but not runtime-confirmed.

---

## 0. Executive summary

The HTTP layer is broad and generally disciplined (4-key identity threaded
almost everywhere, RBAC numeric levels, forensic audit rows on mutations,
narrow-except policy, per-tenant/per-4-key/per-source rate-limit *modules*).

But three multi-tenant control middlewares — **per-tenant CORS whitelist**,
**per-4-key bot rate limit**, **per-source-tag ingest rate limit** — are wired
into the stack so that they execute **before** `TenantContextMiddleware` binds
`request.state.record_tenant_id`. All three read that attribute, find `None`,
and silently **bypass / fall back to the global list**. The per-tenant CORS
integration test masks this by inverting the middleware order relative to
production. This is the single biggest happy-case gap in the scope.

Secondary: the "one canonical ingest funnel" mandate is not met — the canonical
`POST /documents/create` is **URL-only** (no direct binary upload), so
multi-format local-bytes ingest for external BE consumers has no canonical
endpoint (only the internal test-harness `/test/.../documents/upload`). The
`X-Schema-Version` negotiation is single-version scaffolding with no handler
branch. A `list_documents` sync query lacks workspace scoping. Numerous
inline numeric fallbacks in `_build_pipeline_config` / `sync.py` /
`bot_admin_routes` tension the zero-hardcode rule.

---

## 1. File-by-file — what it does + pipeline wiring

### Composition / bootstrap
- **`__init__.py`** — re-exports `app`, `create_app`.
- **`router.py`** — composes every route module under `BASE = api_base_path`.
  Mounts health/health_models/honeypot at root; chat/documents/sync/crm/admin
  under `BASE`; test_chat under `{BASE}/test`; pages at root.
  `documents_stream_upload` is **deliberately NOT mounted** (line 56-64 comment:
  orphan stream with no consumer → data-loss). No parallel/duplicate upload
  endpoint is active. **FACT** (`router.py:8-35,55-64`).
- **`app.py`** — `create_app()` builds the FastAPI app + full middleware stack;
  `lifespan()` boots the DI container, fail-loud key preflight, RLS-role check,
  reranker preflight (registry-based, DI-clean), resolver/registry/token/guardrail
  cache warmup, embedded workers, warmup probe. `/favicon.ico` + `/metrics`
  (Bearer-guarded when `RAGBOT_METRICS_AUTH_TOKEN` set, else open) declared here.

### Middlewares
- **`tenant_context.py`** — THE auth layer. Verifies service JWT (HS256) then
  user JWT (RS256), resolves `record_tenant_id` (UUID claim → upstream-INT
  fallback via `tenants.config->>'upstream_tenant_id'`), enforces Layer-1
  (per-tenant, fail-closed), Layer-1.5 (per-service), Layer-2 (per-connect_id)
  rate limits, binds `request.state.{record_tenant_id,user_id,bot_id,role}`.
  Also exposes `enforce_tenant_match` route guard.
- **`schema_version.py`** — reads `X-Schema-Version`, validates against
  `SUPPORTED_SCHEMA_VERSIONS=(1,)`, binds `request.state.schema_version`.
- **`rate_limit.py`** — `SlidingRateLimitMiddleware` (per-token per-endpoint).
- **`bot_rate_limit.py`** — `BotRateLimitMiddleware` (per-4-key fairness).
- **`source_rate_limit.py`** — `SourceRateLimitMiddleware` (per `(tenant,source_tag)` on ingest).
- **`ip_rate_limit.py`** — `IpRateLimitMiddleware` (pre-auth per-IP, fail-closed).
- **`anti_abuse.py`** — UA denylist + auth-fail ban + scanner + 4xx-ratio flag.
- **`cors_per_tenant.py`** — `CORSPerTenantMiddleware` reads `tenants.allowed_origins`.
- **`body_size.py`** — per-path Content-Length cap + chunked-transfer reject.
- **`security_headers.py`** — OWASP baseline response headers.
- **`loadtest_bypass.py`** — env-token + loopback-gated benchmark bypass.
- **`logging_mw.py`** / **`trace_context.py`** — request log counter + trace-id.
- **`rbac.py`** — `require_permission` / `require_permission_dep` (metadata-driven,
  single-flight cache of `module_permissions`).

### Routes
- **`chat.py`** — production `POST /chat` (202+worker), `POST /feedback`.
  4-key resolve via `BotRegistryService.lookup`, tenant from JWT.
- **`chat_stream.py`** — production SSE `POST /chat/stream` (in-request pipeline).
- **`chat_async.py`** — `/test/chat-async` (XADD→worker→poll). Fallback tenant.
- **`documents.py`** — canonical `POST /documents/create` (URL-only), delete,
  rechunk, rechunk-by-id. Idempotency via `X-Idempotency-Key`. Quota gate.
- **`documents_stream_upload.py`** — DISABLED binary streaming (not mounted).
- **`sync.py`** — upstream-BE `POST /sync/bot`, `/sync/documents`, GET/DELETE docs.
- **`jobs.py`** — `GET /jobs/{id}` job status.
- **`feedback.py`** — `POST /feedback/thumbs` (message_feedback analytics).
- **`crm.py`** — operator analytics read-layer over request_logs/steps.
- **`health.py`** / **`health_models.py`** — liveness + AI-provider smoke probes.
- **`honeypot.py`** — passive scanner traps (404 + flag IP).
- **`admin_ai.py`** — provider/model/binding CRUD + keys + cache + effective-config.
- **`admin_analytics.py`** — per-bot pass-rate/cost/latency/drift/feedback,
  cross-tenant rollup (L100), workspace-aggregate.
- **`admin_audit.py`** — audit message/overview/query-detail/verify (tenant-scoped).
- **`admin_bots.py`** — admin bot CRUD + purge + effective-prompt + cache.
- **`admin_gdpr.py`** — right-to-erasure (message/conversation) + PII scrub + audit.
- **`admin_metrics.py`** — overview/by-model/top-questions/steps/timeseries.
- **`admin_notify.py`** — notify-channel config CRUD + test dispatch.
- **`admin_policy.py`** — model capability + tenant policy upsert.
- **`admin_rate_limits.py`** — partner-facing `GET /admin/rate-limits/inspect`.
- **`admin_refuse_suggestions.py`** — refuse-loop read + FAQ candidate clustering.
- **`admin_tenant_policy.py`** — 3-column tenant policy GET/PATCH + cache bust.
- **`admin_tenants.py`** — super-admin tenant CRUD (create/list/get/patch/delete).
- **`admin_webhooks.py`** — HMAC secret rotation + version listing.
- **`embedded_workers.py`** — in-process document consumer / outbox / recovery /
  cost-cap alerter / cache-purge tasks.
- **`test_chat/*`** — demo + BE test-harness: chat/stream/history/clear, bot CRUD,
  documents CRUD/upload, admin config/keys/redis/models, tokens, monitoring,
  insights (audit/question-gen/quality-dashboard), pipeline-config SSoT, pages.
- **schemas** — chat/document/common/admin_ai/admin_tenants/admin_tenant_policy.

---

## 2. TOP FINDINGS

### F1 (HIGH · multi-tenant) — Per-tenant CORS + per-4-key bot RL + per-source RL run BEFORE tenant is bound → silently disabled

**FACT.** In `app.py`, `TenantContextMiddleware` is added at **line 497**, but
`BotRateLimitMiddleware` (**536**), `SourceRateLimitMiddleware` (**549**), and
`CORSPerTenantMiddleware` (**559**) are added *after* it. Starlette wraps in
reverse insertion order, so later-added middleware wrap **outside** and run
**before** TenantContext on the request path. TenantContext is the *only* place
`request.state.record_tenant_id` is set (`tenant_context.py:377`,`:420`).

Empirical reproduction (Starlette `TestClient`, mirroring the add order) — both
CORS and BotRL observed `tenant=None`; TenantContext ran last:
```
CORS  → CORS sees tenant=None
BotRL → BotRL sees tenant=None
TenantContext
```

Consequences (each verified against the middleware code):
- **BotRateLimit** — `_resolve_bot_identity` strategy-2 requires
  `request.state.record_tenant_id`; it is `None` → returns `None` → `dispatch`
  bypasses (`bot_rate_limit.py:122-123,172-174`). Strategy-1
  (`request.state.bot_identity`) is **never set anywhere** (grep: only *read*
  in `bot_rate_limit.py:104`). ⇒ the per-4-key fairness limiter is dead for
  **every** request.
- **SourceRateLimit** — `_resolve_tenant` returns `None` when the state attr is
  absent → bypass (`source_rate_limit.py:97-99,166-168`). ⇒ per-source ingest
  fairness dead.
- **CORSPerTenant** — `_resolve_allowed` sees `record_tenant_id is None` and
  returns the **global env list**, never `tenants.allowed_origins`
  (`cors_per_tenant.py:231-236`). Because `allowed` is computed *before*
  `call_next` (`:169`), even the response-header path uses global origins. ⇒
  the entire per-tenant CORS whitelist is inert in production; every tenant
  gets the global env origins.

**Failure scenario:** Tenant A sets `allowed_origins=['https://a.example.com']`;
a browser request from `https://evil.example` still receives the global env
ACAO (or is admitted if global list is permissive), and Tenant B's "support"
bot can flood ingest without ever tripping the 4-key/source caps — the
multi-tenant fairness + CORS isolation both silently absent.

**Test masks it:** `tests/integration/test_cors_per_tenant_enforce.py:145-152`
adds the tenant-context *stub* **last** (so it runs first/outer) while claiming
"exactly the prod order" — the relative order is **inverted** vs `app.py`, so
the test binds `record_tenant_id` before CORS reads it and passes green.

**Fix direction:** move `TenantContextMiddleware.add_middleware` to be the
**last** of the auth-dependent trio (added after CORS/BotRL/SourceRL so it
wraps outermost among them and runs first), OR convert these three to plain
ASGI middleware that resolve tenant themselves. Add a regression test that
asserts the *production* `create_app()` order, not a hand-wired stub.

---

### F2 (HIGH · multi-format) — Canonical `POST /documents/create` is URL-only; no canonical binary-ingest funnel for external BE

**FACT.** `IngestDocumentRequest` requires `source_url: HttpUrl` and has **no**
bytes/file field (`schemas/document_schema.py:54`). The route builds
`IngestDocumentCommand(source_url=..., mime_type=...)` with no raw bytes
(`documents.py:171-182`) — the worker must re-fetch from the URL. The only live
binary/multipart ingest path is the **internal test harness**
`POST /api/ragbot/test/bots/{bot_id}/{channel_type}/documents/upload`
(`test_chat/document_routes.py:481-536`), which CLAUDE.md classifies as
dev/QA-only (not for external consumers). The dedicated streaming binary route
(`documents_stream_upload.py`) is **disabled** (`router.py:56-64`).

**Failure scenario:** A partner BE holding a local PDF/DOCX/XLSX (no hostable
URL) has no canonical `/documents/create` path to POST bytes to — it must
either host the file behind a URL first or fall back to `/sync/documents`
(inline *text* only) or the internal test route. This contradicts the "MỌI
format đi CÙNG 1 luồng canonical" first-class-ingest mandate for the BE-to-BE
surface. **HYPOTHESIS** on impact severity (no external consumer trace to
measure), **FACT** on the endpoint shapes.

Related: there are effectively **three** ingest funnels with divergent input
contracts — `/documents/create` (URL), `/sync/documents` (inline text + optional
mime), `/test/.../documents/upload` (multipart bytes) — vs the "ONE canonical
funnel" rule.

---

### F3 (MEDIUM · multi-tenant/workspace) — `GET /sync/documents` list query is not workspace-scoped

**FACT.** `sync.list_documents` filters `b.bot_id`, `b.channel_type`,
`b.record_tenant_id`, `b.is_deleted` but **omits `workspace_id`**
(`sync.py:645-666`). It also takes no `workspace_id` param. If a tenant has the
same `(bot_id, channel_type)` slug in two workspaces (explicitly allowed by the
4-key model), the JOIN returns documents from **both** bots.

**Failure scenario:** Tenant T has bot `support:web` in workspace `ws-a` and a
different `support:web` bot in `ws-b`. `GET /sync/documents?bot_id=support&
channel_type=web&tenant_id=NN` returns the union of both workspaces' documents —
cross-workspace read within the tenant. The sibling `POST/DELETE /sync/documents`
correctly use `find_by_4key(...workspace_slug...)` (`sync.py:449,708`), so the
list path is the odd one out. Contrast with the demo `list_documents`
(`test_chat/document_routes.py:64-73`) which *does* accept + apply an optional
`workspace_id` clause.

---

### F4 (MEDIUM · multi-format/dead-scaffold) — `X-Schema-Version` negotiation is single-version, no handler branches

**FACT.** `SUPPORTED_SCHEMA_VERSIONS=(1,)`,
`SUPPORTED_INGEST_SCHEMA_VERSIONS=frozenset({1})`
(`shared/constants/_09_...py:131,158`). `SchemaVersionMiddleware` binds
`request.state.schema_version` but **no route reads it** (grep). The
`IngestDocumentRequest.schema_version` body field is validated
(`schemas/document_schema.py:64-80`) but the `documents.py` handler never
references `req.schema_version`. So the whole header/body version-negotiation is
scaffolding with a single value and zero branch logic — built-but-not-wired.
Not a bug today; will silently accept `schema_version=1` forever. Flagged so the
V2 wire-up is deliberate, not assumed present.

---

### F5 (MEDIUM · multi-tenant) — Shared fallback tenant `UUID(int=1)` on demo/async chat when JWT carries no tenant

**FACT.** `chat_async._tenant_uuid` returns `_PLATFORM_TENANT_FALLBACK_UUID =
uuid.UUID(int=1)` when the JWT has no `record_tenant_id`
(`chat_async.py:74,91-99`). `test_chat.test_chat` uses the same fallback
(`chat_routes.py:118-120`, `_shared.py:47`). `find_by_3key_unique` then scopes
to tenant `int=1` (`_shared.py:135-152` → `find_by_3key_unique`).

**Failure scenario:** Multiple demo/harness callers that omit a tenant claim all
resolve to the single shared tenant `00000000-…-0001`, so their bots + chat
histories co-mingle under one tenant bucket. These routes live under
`/api/ragbot/test/*` (internal harness per CLAUDE.md), so the blast radius is
demo-only — but if the network/gateway block on `/test/*` is ever
mis-configured, this is a cross-caller mixing point. **FACT** on the fallback,
**HYPOTHESIS** on external exposure.

---

### F6 (LOW · CLAUDE.md tension) — `/health/models` config-drift "fix" advises a banned psql UPDATE to `bot_model_bindings`

**FACT.** `_detect_config_drift` returns a remediation string
`"UPDATE bot_model_bindings SET purpose='rerank' WHERE purpose='reranker'"`
in the JSON response (`health_models.py:408-411`). CLAUDE.md sacred-rule 7
explicitly BANS psql hot-fixes to `bot_model_bindings.*`; the platform is
surfacing an anti-pattern remediation to operators. The string is not executed
(diagnostic text only), so impact is advisory. Suggest replacing with "run
alembic migration X" guidance.

---

### F7 (LOW · zero-hardcode) — Inline numeric fallbacks across pipeline-config / sync / bot-create

**FACT.** `_build_pipeline_config` (`test_chat/_pipeline_config.py:371-829`)
carries dozens of inline numeric defaults for `system_config` misses (e.g. `500`
grade/reflect preview, `6` condense, `8000` prompt_max_tokens/whole_doc,
`50`/`1024`/`256`/`512` chunk sizes, `0.88` mmr, `0.7` lambda, `0.3` grounding,
`12` shingle, `50` recursion). `sync.sync_bot` (`sync.py:292-294`) and
`bot_admin_routes.create_bot` (`bot_admin_routes.py:266-268,279-281`) inline
`0.3`/`450`/`0.4` temperature/max_tokens/top_p fallbacks. CLAUDE.md zero-hardcode
rule forbids any inline number outside `shared/constants.py`. These are
"last-resort fallback if the DB row is missing" values (`system_config` is the
SSoT), so the runtime behaviour is config-driven — but the literals themselves
tension the rule and drift risk between endpoints. Medium-effort sweep to lift
into constants. **FACT** on the literals.

---

### F8 (LOW · provider-coupling) — Startup key preflight hardcodes Jina env-var names

**FACT.** `_check_required_provider_keys` requires `OPENAI_API_KEY` and one of
`PROVIDER_API_KEYS_JSON` / `RERANKER_JINA_API_KEY_PRIMARY` /
`EMBEDDING_JINA_API_KEY_PRIMARY` (`app.py:142-160`). The reranker preflight was
correctly refactored to be registry/Strategy-based (no `startswith` per
provider, `app.py:57-121`), but this key-check still bakes in `OPENAI` +
`JINA`-specific env names in the domain-neutral engine. After a provider swap
(e.g. ZeroEntropy per the memory notes) the check validates the wrong keys.
Low impact (UAT/staging only), but it is a brand-literal + provider-coupled
gate. **FACT**.

---

### F9 (INFO · degraded-keying) — `SlidingRateLimitMiddleware` also runs before tenant bind → keys on token-hash not `tenant:user`

**FACT.** Added at `app.py:518` (after TenantContext at 497), so it also runs
before `record_tenant_id`/`user_id` are set. `_caller_key` therefore skips the
`tok:{tenant}:{user_id}` branch and falls to the bearer sha256 branch
(`rate_limit.py:62-72`). The limiter still functions (per raw token), so this is
degraded keying rather than a bypass — but it means the per-token limiter never
uses the documented `tenant:user` composite key in production. Lower severity
than F1 because it still enforces a cap.

---

### F10 (INFO · dead code) — `_safe_uuid` in `rate_limit.py`, `documents_stream_upload.py` orphan

**FACT.** `rate_limit.py:244-253` defines `_safe_uuid` used only by tests
(docstring says so); no production caller. `documents_stream_upload.py` is a
fully-written 390-line streaming ingest route intentionally kept but unmounted
(`router.py:56-64`) — orphan-by-design. Both are documented; noting for the
inventory, not action.

---

## 3. Cross-axis assessment

- **multi-doc:** No cross-document join logic lives in the HTTP layer (it is a
  thin adapter over use-cases/services). `crm`/`admin_analytics` aggregate over
  `request_logs`, not corpus. No finding here beyond F3's workspace-scope gap.
- **multi-bot:** Per-bot config *is* honored — `_build_pipeline_config` threads
  `resolve_bot_limit(bot_cfg, ...)` for nearly every knob (`_pipeline_config.py`),
  OOS template + assembled sysprompt via per-bot resolvers, `action_config` /
  `rerank_intent_whitelist` per-bot. No hardcoded per-bot branch found in
  orchestration/application (consistent with the "no per-bot logic in core"
  rule). The dead F1 bot-RL is a fairness gap, not a config-honor gap.
- **multi-format:** F2 (URL-only canonical) + F4 (single schema version) are the
  format-parity gaps at the HTTP boundary. `/sync/documents` routes tabular
  sources by `mime_type` (`sync.py:557-568`) and the demo upload passes bytes to
  the parser registry — parity exists on *those* paths, not on `/documents/create`.
- **multi-tenant / RLS:** Tenant lifting + `enforce_tenant_match` +
  RLS-role boot check (`app.py:170-250`) + tenant-scoped audit/analytics are
  solid. The break is F1 (rate-limit/CORS run pre-tenant) and F3/F5 (workspace
  + fallback-tenant). Admin routes correctly gate super-admin (100) vs admin
  (60) vs tenant-admin (80) via `require_min_level` / `require_permission_dep`;
  `require_binding_ownership` adds cross-tenant enumeration defence on binding
  mutate (`_resource_ownership.py`). No RBAC-level inversion found.

## 4. Positives worth preserving
- Fail-loud boot preflights (keys, RLS role) + fail-soft `/health*`.
- `enforce_tenant_match` + `require_binding_ownership` 404-collapse anti-enumeration.
- Idempotency on the canonical create (`documents.py:117-161`) with body-hash drift log.
- SSRF check on `chat_async` callback_url before enqueue (`chat_async.py:241-247`).
- `ChatRequest` `extra="forbid"` blocks the historical `system_prompt` body-inject
  (`chat_schema.py:41`) — sacred-rule 10 honored at the schema boundary.
- Narrow-except discipline with `# noqa: BLE001 — <reason>` at genuine top-level
  entrypoints only.

---

## 5. Verification notes (rule#0)
- F1 middleware ordering: verified by (a) `app.py:497,536,549,559` add order,
  (b) an empirical Starlette `TestClient` repro showing CORS/BotRL observe
  `tenant=None`, (c) the masking test at
  `test_cors_per_tenant_enforce.py:145-152`, (d) grep proving
  `request.state.bot_identity` is never assigned.
- All other findings cite exact `file:line`; impact-severity items where no
  runtime trace exists are labelled HYPOTHESIS.
