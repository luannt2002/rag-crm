# PASS-2 DEEP RE-ANALYSIS — Cross-cutting Systemic Classes + Multi-Tenant/Security

- **Date**: 2026-07-03 · **Branch**: `fix-260623-ingest-expert` · **HEAD**: `6796cd9` (revert ING-F1 pure-money fallback)
- **Note on tree drift**: pass-1 read at `949a3a4`; the tree has since advanced ~40 commits (deep-debug OBS-F2/F3, GEN-F6, ING-F1 + revert, XLSX row-shape parity, etc.). **All verdicts below are re-checked against the CURRENT tree**, not pass-1's snapshot. Where a commit changed a finding, it is flagged.
- **Method**: skeptical re-read of every cited `file:line` + 3 executable probes (langgraph key-drop, Starlette middleware-order, AST pin-test prototype) + live DB queries (`ragbot_v2_dev`). Label discipline (rule#0): **FACT** = code/probe/DB evidence attached; **HYPOTHESIS** = mechanism proven, runtime frequency not measured.
- Stance: **EVOLVE not REWRITE.** Every fix respects sacred #10 (no app-override), #7 (alembic-only content), domain-neutral, zero-hardcode, 4-key, HALLU=0.

---

## 0. Executive verdict (2nd pass)

The three systemic root-classes are **CONFIRMED as engineering phenomena**, not just finding-lists. S1 (LangGraph undeclared-key drop) is empirically reproduced against installed `langgraph==1.2.4` and the affected-key set is **larger than pass-1 reported** — an AST walk finds **22 undeclared-but-used keys**, of which pass-1 catalogued 11; I add **`rerank_score_mode`** (a live cross-node dead-drop on the rerank→grade hand-off) as a NEW instance, and I *downgrade* `action_state` to cosmetic (DB-backed, survives the drop). S2 (last-mile DI wiring gap) is confirmed for all probed features: PII/sanitizer/source-allowlist/GraphRAG×2/cascade/xml-wrap/parent-child are each provably inert in production. S3 (happy-case box) is confirmed with a documented, vocabulary-gated box boundary that **degrades to zero** (0 entities) rather than gracefully. On security: the middleware-order defect (F1) is reproduced with a Starlette probe — CORS + all three post-auth rate-limiters run with `tenant=None`; RLS is DB-verified dead (runtime role `postgres`, `rolsuper=t rolbypassrls=t`); `ai_keys` targets a non-existent schema (DB-verified); the ingest idempotency key omits `record_bot_id`. Two pass-1 claims are **REFINED/OVERCLAIMED**: the stats re-ingest "wipes 99 entities" claim (per-document delete, not cross-document) and the fallback-stage "RLS dead" framing (bounded by `record_bot_id` fence). The single structural lever with the highest blast-radius reduction is a **schema-vs-usage AST pin-test** (kills the whole S1 class) plus a **one-page wiring-audit + un-mocked integration test** (kills the whole S2 class).

---

## S1 — LangGraph undeclared-state-key drop

### S1.0 The phenomenon (FACT — empirically reproduced)

`langgraph==1.2.4` (verified `importlib.metadata.version`) drops **every** state key not declared in the `GraphState` TypedDict: from the initial input dict, from node return dicts, and in-place `state[...] = x` mutations do not cross node boundaries.

Probe (`scratchpad/lg_probe.py`, run against the installed lib):
```
n1: undeclared_input=<MISSING>          # input key dropped before first node
n2: undeclared_return=<MISSING> inplace_key=<MISSING>
final keys: ['declared', 'out']
```
This is a **reducer contract**, not a config toggle — the TypedDict is a de-facto allowlist. `state.py:150-197` repeats the rule four times; commit `15406d8` fixed one instance (`_mq_speculative_variants`) without adding a guard — so the class recurs.

### S1.1 Affected-key ledger (re-verified against current tree)

Authoritative declared set = **58 keys** (AST-extracted from `state.py`). An AST walk of `orchestration/**/*.py` for `state[...]=`, `state.get("...")`, `state["..."]` and `return {"...":}` finds **22 undeclared-but-used keys**. Triaged:

| Key | Written | Read | Cross-node? | Verdict | Live effect |
|---|---|---|---|---|---|
| `bot_extra_output_tokens_per_response` | graph_assembly.py:193 (input) | generate.py:739 | input→node | **CONFIRMED** | paid output cap always system-default (F3) |
| `bot_created_at` | graph_assembly.py:192 (input) | query_graph.py:453 | input→node | **CONFIRMED** | XML-wrap date-default dead (F5) |
| `raw_user_message` | graph_assembly.py:177 (input) | generate.py:251 | input→node | **CONFIRMED (mitigated)** | dead; slot path survives via `original_query` |
| `_total_graph_iterations` | grade.py:83…536 (return ×6) | grade.py:76, routing.py:245 | node→node | **CONFIRMED** | loop cap dead → only `recursion_limit=50` bounds reflect loop (F6) |
| `crag_skip_retry` | grade.py:109,154 (return) | routing.py:167 | node→node | **CONFIRMED** | fast-path dead; masked by `retrieval_adequate` |
| `_corpus_version` | check_cache.py (return) | query_graph.py:903, persist.py:183 | node→node | **CONFIRMED** | memo dead → 2-3 redundant resolves/turn (F19) |
| `embedding_column` | query_graph.py:1337,1393 (in-place) | query_graph.py:388, :2697 | in-place | **CONFIRMED** | cross-node loss → permanent `semantic_cache_preflight_no_embedding_column` false alarm |
| `retrieval_degraded` | query_graph.py:403 (in-place) | — **zero readers** | — | **CONFIRMED** | advertised HALLU-safety flag is fiction (F8) |
| `embed_degraded` | query_graph.py:1500 (in-place) | — **zero readers** | — | **CONFIRMED** | same (F8) |
| `multi_query_skipped_simple` | query_graph.py:1904 (in-place) | — zero readers | — | CONFIRMED | dead observability |
| `grounding_async_task` | query_graph.py:830 (in-place) | :830 (same expr) | — | CONFIRMED | unreachable from final state |
| **`rerank_score_mode`** | **rerank.py:498 (return)** | **grade.py:486** | **node→node** | **NEW · CONFIRMED** | **rerank→grade hand-off dead: `if state.get("rerank_score_mode")=="rerank"` never true → grade's rerank-mode branch never taken** |
| `resolved_answer_model` | generate.py:408 (in-place) | generate.py:376 (same node) | within-node | **CONFIRMED (self-referential)** | cascade write never consumed by the LLM call (see S2 cascade) |
| `action_state` | generate.py:306 in-place + 1080 return | generate.py:219 + DB reload :230 | node→node BUT DB-backed | **OVERCLAIM-guard** | drop is **cosmetic** — slots persist via `conversation_state.save_state` → `conversations.action_state` JSONB, reloaded at :230 |
| `_persist_meta` | persist.py:251 (return, terminal node) | persist.py:248 (before return) | within-node, terminal | REFINED | read always `{}`; persist is terminal (→END) so the return is never re-fed |
| `bot_id`,`bot`,`bot_config`,`debug_full`,`rerank_score_mode`(dup),`_uq_cache_hit`,`_generate_empty_answer` | various | various | mixed | mixed | `bot_id`/`bot`/`bot_config` = never-set reads → log noise + cascade_router_helper reads a `bot` that is always absent |

**NEW FINDING vs pass-1**: `rerank_score_mode` is a genuine cross-node dead-drop pass-1 missed. `rerank` returns it, `grade` reads it to decide whether the pass-1 rerank score should gate the retry — with the key dropped, grade always treats the score as non-rerank. Add it to the S1 remediation batch.

**Refinement vs pass-1**: `action_state` is NOT a slot-loss bug — the DB-backed conversation-state path makes the GraphState drop cosmetic. This is exactly the kind of over-claim a skeptical pass must catch: not every undeclared key is a live defect.

### S1.2 Case-study chain (worst instance: F3 paid-token budget)

- **Problem (repro-able)**: a bot with `plan_limits.extra_output_tokens_per_response=2048` never gets the extra budget; answers truncate at the platform default.
- **Direct cause**: `generate.py:739` `int(state.get("bot_extra_output_tokens_per_response", 0) or 0)` → always 0.
- **Root chain (immutable)**: L1 read-returns-0 ← L2 key absent from state at generate ← L3 key set only in the **initial input dict** (`graph_assembly.py:193`) ← L4 `GraphState` (58 keys) does not declare it ← L5 langgraph 1.2.4 reducer drops undeclared input keys before node 1 (probe). Immutable cause = **L4 (schema omission)**; L5 is the invariant library behaviour.
- **Expert solution (correct layer = the TypedDict + a guard, NOT a per-key patch)**:
  - *short*: declare the 12–13 genuinely-cross-node keys in `GraphState`; convert the 6 in-place writes that must cross a boundary (`embedding_column`, `retrieval_degraded`, `embed_degraded`) to node returns.
  - *mid*: land the **AST pin-test** (S1.3) so the class cannot recur — this is the SOTA "make illegal states unrepresentable at the boundary" pattern; it is the structural fix, the declare-keys step is the point fix.
  - *long*: consider a thin `GraphState` accessor module (`get_state(state, KEY)` with a registry) so every read/write is centrally lintable — optional, higher-touch.
- **Self-critique — expert or patch?** Declaring keys alone is a *patch* (3rd occurrence after M17). The **guard** is the expert move: it converts a recurring silent-drop class into a collection-time failure.
- **Trade-offs**: declaring ~12 keys is zero-risk (TypedDict is `total=False`). The guard needs a curated allowlist of legitimately-non-state dicts (e.g. `context_base`, `pipeline_config` nested reads) to avoid false positives — a one-time cost.
- **Impact**: Correctness (paid feature, loop safety, HALLU-degraded flags) + Cost (redundant corpus_version resolves). Blast radius = every turn, every bot; revenue-visible for paid knobs.

### S1.3 The guard: AST pin-test (prototype WORKS)

I built and ran `scratchpad/ast_pin_prototype.py`. It:
1. loads `GraphState.__annotations__` (58 keys) as the allowlist;
2. AST-walks `orchestration/**/*.py` collecting `state["k"]=`, `state.get("k")`, `state["k"]`, and (extend) `return {"k":…}` inside functions whose first param is `state`;
3. asserts `used ⊆ declared`, printing each violation with W/R sites.

Output today: **22 undeclared-but-used** (proves it catches the class). Ship it as `tests/unit/test_graphstate_key_pin.py` with a small `_ALLOWED_NON_STATE_KEYS` set for the genuine within-node scratch keys (`_generate_empty_answer`, `_uq_cache_hit`, `_persist_meta`, `action_state` DB-backed) so the pin fails **only** on cross-node drops. This is the guard the M17 fix should have added.

---

## S2 — Last-mile DI wiring gap

### S2.0 The phenomenon (FACT)

A feature can have Port + Strategy + Registry + Null-Object + green unit tests and still **do nothing in production**, because the container has no provider reading its documented `system_config` key, or the service ctor has no param, or a call-site kwarg name is wrong. Unit tests validate the *strategy*; nothing validates the *wiring*. Verified for 9 features:

| Feature | Kill mechanism (current tree) | Evidence | Verdict |
|---|---|---|---|
| **PII redaction** | provider frozen: `bootstrap.py:447-449` passes compile-time `DEFAULT_PII_REDACTOR_PROVIDER` ("null") into a Singleton; `pii_redactor_provider` has **zero `.get(` readers** | grep empty | **CONFIRMED** |
| **CleanBase Tier-0 sanitizer** | `ingest_stages.py:310` `getattr(self,"_sanitizer",None)`; **no `_sanitizer =` assignment anywhere** (grep empty); no ctor param | grep empty | **CONFIRMED** |
| **Source-URL allowlist** | `document_worker.py:523` `hasattr(container,"source_validator")`; **no bootstrap provider** (grep empty) → always False | grep empty | **CONFIRMED** |
| **GraphRAG query** | `graph_retriever.py:61` `query_graph(bot_id=…)` vs sig `query_graph(query, record_bot_id, …)` (knowledge_graph.py:179) → `TypeError`, swallowed by `except Exception` | file:line | **CONFIRMED** |
| **GraphRAG ingest** | `ingest_core.py:802` `store_triples(bot_id=…)` vs sig `store_triples(record_bot_id, …)` (knowledge_graph.py:128) → `TypeError`, swallowed | file:line | **CONFIRMED** (LLM triple extraction cost paid, then discarded) |
| **Cascade routing** | `generate.py:408` `state["resolved_answer_model"]=…` — **undeclared (S1) AND the only reader is :376 within the same node before the write**; the actual `_invoke_llm_node` (:843) never consumes it | file:line | **CONFIRMED** (no-op; owner pays resolve+log) |
| **XML-wrap** | `xml_wrap_enabled` in **neither** pipeline_config builder (grep empty in both `chat_worker/pipeline_config.py` + `test_chat/_pipeline_config.py`) → `_pcfg(…,None)`; `bot_created_at` dropped (S1) → resolver returns `DEFAULT_XML_WRAP_ENABLED=False` | file:line | **CONFIRMED** |
| **Parent-child (small-to-big)** | main vector SELECT (pgvector_store.py:331) projects `id,record_document_id,chunk_index,content,metadata_json,score` — **no `parent_chunk_id`**; DB: **0 / 979 chunks** have `parent_chunk_id` populated; fallback stage-4 reads `c.get("parent_chunk_id")` → empty → no-op | DB-verified | **CONFIRMED (2-layer)** |
| **Modality-boost / narrate default-vi** | narrate default OFF; `narrate_lang` never threaded (would use `vi` pack for all) | (pass-1 §1.7) | CONFIRMED (moot until re-enabled) |

Note the builder-path moved this cycle: builders now live at `interfaces/workers/chat_worker/pipeline_config.py` and `interfaces/http/routes/test_chat/_pipeline_config.py`. Both use an **explicit-whitelist** shape (`resolve_bot_limit(bot_cfg, "<key>", …)` per key) — there is **no wholesale `plan_limits` passthrough**, which is exactly why `xml_wrap_enabled`/`cross_doc_reconcile_enabled` are unreachable: a knob that is not explicitly listed simply never appears in `pipeline_config`.

### S2.1 Case-study chain (worst instance: GraphRAG both directions)

- **Problem (repro-able)**: operator sets `graph_rag_default_mode='adaptive'`. Ingest burns LLM triple-extraction tokens; `knowledge_edges` stays empty; every multi-hop query logs `graph_retrieve_failed` and contributes 0 chunks. Feature shows "on" in config + step metadata.
- **Direct cause**: kwarg-name mismatch `bot_id=` vs `record_bot_id` on both `query_graph` and `store_triples`.
- **Root chain (immutable)**: L1 zero graph chunks / zero stored edges ← L2 `TypeError` on every call ← L3 caller passes `bot_id=` but the method declares `record_bot_id` (the historical external-vs-internal naming drift, now at the **kwarg** layer) ← L4 the `except Exception` swallow makes it silent ← L5 node tests mock `kg.query_graph = AsyncMock()` which accepts any kwargs → the pin can't see it. Immutable cause = **L3 (naming-convention violation at the call site)**; L4/L5 are why it's invisible.
- **Expert solution**:
  - *short*: rename both call-site kwargs to `record_bot_id=`. (Or, if GraphRAG is not a near-term priority, **gate it OFF at the resolver** so ingest doesn't pay extraction cost — cheaper and honest.)
  - *mid*: the S2 guard (S2.2) — one un-mocked integration test that constructs the REAL `KnowledgeGraphService` and calls the real adapter; it fails today with `TypeError`.
  - *long*: replace the broad `except Exception` swallows on these paths with narrow types + a fail-loud-once metric so a wiring break surfaces in the journal instead of degrading to zero.
- **Self-critique — expert or patch?** Renaming kwargs is a patch that fixes one instance of a class the project has hit ≥3× (memory `feedback_v2_bug_lessons`). The **un-mocked integration test + narrowing the swallow** is the expert, class-killing move.
- **Trade-offs**: un-mocking costs test setup (real service, in-memory/fixture session) but is the only thing that would have caught all five S2 features. Gating GraphRAG off trades a (currently non-functional) capability for cost honesty.
- **Impact**: Correctness (multi-doc join — owner's explicit concern) + Cost (wasted extraction tokens). Blast radius = every ingest + every query when the mode is enabled.

### S2.2 The guard: wiring-audit + un-mocked integration test

Two artefacts, both structural:
1. **One-page wiring audit** (`docs/dev/WIRING_AUDIT.md`, table): per registry — *(a)* does bootstrap have a provider? *(b)* does that provider read the documented `system_config.<x>_provider` key (not a compile-time constant)? *(c)* does one integration test exercise the REAL class un-mocked? Rows failing (a)/(b)/(c) = PII, sanitizer, source-validator, GraphRAG×2, cascade, xml-wrap, parent-child.
2. **`tests/integration/test_di_wiring_smoke.py`**: for each safety/quality feature, build the real container, resolve the provider, assert it is **not** the Null object when the documented flag is on, and call the real method with the 4-key kwargs. GraphRAG's test fails today (`TypeError`); PII's asserts `redact()` masks a CCCD when `pii_redactor_provider='vn_regex'`. AsyncMock is banned in this file (it hides kwarg drift — the F6 lesson).

---

## S3 — Happy-case box

### S3.0 Box boundary (FACT — documented + code-verified)

The stats/entity extractor is vocabulary-gated, not shape-gated. Header detection (`shared/document_stats.py:156-159`) requires a header cell to **exactly match a known column-label token** from finite frozensets `_NAME_COL_TOKENS` / `_CATEGORY_COL_TOKENS` / `_PRICE_COL_TOKENS` / `_ALIASES_COL_TOKENS` (lines 174-203) — vi+en literals. The in-repo comment is candid (lines 164-169): *"These role token sets DEFINE what column headers the platform parses cleanly … A document whose headers fall outside these sets is 'out of scope' → the checker flags it and the customer fixes the SOURCE."* The box = **{header ∈ known vi/en vocab · money-cell shape · Path-A entry · VND-ish integer price}**.

### S3.1 The degrade-to-zero failure mode (FACT)

Outside the box the pipeline degrades to **zero rows**, not to a graceful partial:
1. header row not in vocab → no header detected → columns become positional `col_N` placeholders (`_COL_N_PLACEHOLDER_RE`, line 236);
2. `_is_noise_entity` (line 245-266) drops any row whose only attributes are `col_N` placeholders with no price;
3. → the entire sheet yields **0 entities** → stats route (count/list/superlative/range) silently falls back to top-k vector retrieval → the deterministic B-AGG feature is dead for that corpus, with no error surfaced.

This matches pass-1's canary "25/25 fail" shape. It is an **intentional** design (documented, checker-enforced) — but "intentional" ≠ "graceful": a Khmer/Thai/French/Spanish price sheet, or a vi sheet with an unlisted synonym, gets **coverage 0**, not coverage 0.6.

### S3.2 Related S3 confirmations + refinements

- **`int(_price)` (F4/L2-5) — CONFIRMED**: `query_graph.py:2391` (dedup key), `:2413`, `:2419` render `int(_price)` while the adjacent comment (`:2403-2405`) claims currency-neutrality. USD 19.99 → "19" grounded fact; 19.99 vs 19.50 collide in the dedup key `(name, 19)`. The ING-F1 commit + its revert touched the **ingest-side** numeric-column guard, NOT this query-side rendering. Anti-HALLU *misinterpret*-class corruption fed as a score=1.0 synthetic chunk that skips rerank AND grade.
- **Coverage gate (L1-5) — CONFIRMED OBSERVE-only**: `ingest_stages.py:864-897` emits `chunk_numeric_coverage_gap` + `chunk_char_coverage_gap` via `check_chunk_gaps` — **detect, no repair**; the `uncovered_spans` are computed and thrown away.
- **Re-ingest stats delete (L1-6) — REFINED / partially OVERCLAIMED**: `ingest_stages_final.py:534-548` deletes `delete_by_document(doc_id)` guarded by `if _stats_entities:`. This is **per-document**, not cross-document — re-ingesting doc A does NOT delete doc B's entities. Pass-1's "sửa 1/100 row → 99 entity biến mất" is only true if the 100 rows are one document AND its re-parse yields a non-empty-but-smaller set. The empty-set case is *safe* (delete skipped). Real residual risk: a re-parse that yields non-empty-but-wrong entities wipes the good rows for **that document**. Downgrade to per-document correctness, not systemic wipe.

### S3.3 Expert solution + guard

- **short**: (a) stop `int()`-ing prices — carry `Decimal`/str end-to-end (fixes F4 without touching layer); (b) repair coverage using the already-computed `uncovered_spans` (append the missed span as a tail chunk) — ~15 lines, no new subsystem.
- **mid (the structural S3 fix)**: add a **shape-only header fallback** — when no vocab token matches, detect the header row by FORM (all-label cells, no value cell, value-contrast with the next row) per the `table-header-detect-structural` skill, and assign roles positionally. This turns "0 entities" into "best-effort entities" for out-of-vocab corpora — graceful, not zero. Currency becomes a per-locale config (`language_packs`/`system_config`), not a baked VND assumption.
- **long**: the **executable-spec canary** — the format checker (`scripts/check_happy_case.py`) already imports the same frozensets; promote the INV-1/INV-2 canary docs into a CI canary corpus (out-of-vocab header, decimal currency, `;`-CSV, multi-row header) that asserts entity-count > 0. The canary IS the spec: if the box must stay, the canary makes its edges visible and regression-guarded.
- **Self-critique**: the short items are patches; the **shape-only fallback + canary** is the expert move because it changes the failure mode from *silent-zero* to *graceful-degrade + measured*. Respects domain-neutral (shape not vocab) and sacred #10 (no answer injection — this is retrieval-tier).
- **Impact**: Correctness/Coverage for every non-vi/en or non-VND corpus; blast radius = any tenant outside the demo locales — precisely the owner's "mới support happy case" concern.

---

## SECURITY / TENANT

### SEC-1 — Middleware order disables per-tenant CORS + all 3 post-auth rate-limiters (F1) · CONFIRMED (probe)

- **FACT (order)**: `app.py` add order — TenantContext@497, then SlidingRateLimit@518, BotRateLimit@536, SourceRateLimit@549, CORSPerTenant@559. Starlette wraps in **reverse insertion order**, so the four added *after* TenantContext wrap **outside** it and run **before** it.
- **Probe** (`scratchpad/mw_probe.py`, mirrors the exact add order):
  ```
  1. CORS       sees tenant=None
  2. SourceRL   sees tenant=None
  3. BotRL      sees tenant=None
  4. SlidingRL  sees tenant=None
  5. TenantContext (binds tenant)
  ```
- **Downstream degrade (each verified)**:
  - **CORS** — `cors_per_tenant.py:232-236`: `record_tenant_id is None` → returns `self._global_origins`, never reaching the per-tenant cache. Every tenant gets the global env list; the per-tenant `allowed_origins` whitelist is inert.
  - **BotRateLimit** — `_resolve_bot_identity` strategy-2 needs `request.state.record_tenant_id` (`:122-124`) → None → bypass. Strategy-1 (`request.state.bot_identity`) is **never assigned anywhere in src/** (grep empty) → the per-4-key fairness limiter is dead for every request. The module docstring even asserts *"Sits AFTER TenantContextMiddleware (so record_tenant_id is available)"* — contradicted by the wiring.
  - **SourceRateLimit** — `_resolve_tenant` (`:97-99`) → None → bypass. Per-source ingest fairness dead.
  - **SlidingRateLimit** — degraded keying (falls to bearer-sha256 branch instead of `tok:{tenant}:{user}`); still enforces a cap, so degraded not bypassed.
- **Test masks it**: `tests/integration/test_cors_per_tenant_enforce.py:145-152` adds the `_TenantContextStubMiddleware` **last** (runs outer/first), the INVERSE of production, and the comment claims it mirrors prod order.
- **Root chain**: L1 whitelist inert ← L2 `tenant=None` at read time ← L3 CORS/RL added after TenantContext ← L4 Starlette LIFO wrap. Immutable cause = **L3 add-order**.
- **Expert fix**: add `TenantContextMiddleware` **last** among the auth-dependent group (so it wraps outermost and runs first), OR convert the four to plain ASGI middleware that resolve tenant themselves. **Guard**: a regression test that boots the real `create_app()` and asserts, via a probe route, that CORS/BotRL observe a non-None tenant — test the production factory, not a hand-wired stub.
- **Impact**: Correctness/Security — cross-origin isolation + multi-tenant fairness both silently absent. Blast radius = every tenant, every request. (Mitigation today: the coarse Layer-1 per-tenant limiter in TenantContext + per-token Sliding still cap volume; the *per-tenant CORS whitelist* is the sharpest loss.)

### SEC-2 — RLS is dead at runtime (superuser escape) + fallback stages ignore tenant · CONFIRMED (DB) / REFINED

- **FACT [DB-verified]**: `.env:110` `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, no `DATABASE_URL_APP`/`DATABASE_URL_SYSTEM` (only `DATABASE_URL`). Live DB: `current_user=postgres`, `rolsuper=t`, `rolbypassrls=t`. `ragbot_app` exists with `rolbypassrls=f` but is unused. → all `tenant_isolation` policies enforce **nothing** today; isolation rests 100% on explicit `record_tenant_id`/`record_bot_id` WHERE clauses.
- **Fallback stages (L2-6) — REFINED**: `bm25_only_stage2.py:86`, `keyword_stage3.py:112`, `parent_expand_stage4.py:81` open plain `session_factory()` (no `session_with_tenant`, no `SET LOCAL app.tenant_id`) — the "RLS dead in fallback" framing is TRUE, but each stage SQL filters `WHERE d.record_bot_id = :rbid` (stage2 `:79`, stage3 `:104`, stage4 `:89`). Since `record_bot_id` is a unguessable UUID PK unique across tenants, the practical fence holds per the identity rule. **Not a cross-tenant leak** — downgrade to defence-in-depth gap (no second fence if a bot UUID leaks).
- **Soft-delete resurrection (L2-6 second half) — CONFIRMED**: the three fallback stages have **zero `is_deleted`/`deleted_at` filter** (grep empty), whereas the main store filters `doc_deleted_at IS NULL` (pgvector_store.py:270). A soft-deleted document's chunks **resurface** via the BM25/keyword rescue path. This is a real correctness bug (deleted content answerable), independent of RLS.
- **Expert fix**: (ops) provision `DATABASE_URL_APP`→`ragbot_app`, remove the escape env — **but fix SEC-4 workspace-slug writes first** or worker inserts start failing WITH CHECK; (code) add `AND d.deleted_at IS NULL` to the three fallback stage queries. **Guard**: a test that soft-deletes a doc then asserts BM25/keyword fallback returns 0 of its chunks.
- **Impact**: Security posture (RLS inert) is HYPOTHESIS on exploitability today (app-WHERE covers it); soft-delete resurrection is FACT-correctness.

### SEC-3 — `ai_keys` targets a non-existent schema (F1 repos) · CONFIRMED (DB)

- **FACT [DB-verified]**: `ai_config_repository.py:664,689,708,731,747` hardcode `ragbot.ai_keys`. Live DB: `to_regclass('ragbot.ai_keys')` = NULL, `pg_namespace` count for `ragbot` = **0**, table is `public.ai_keys` with **0 rows**. Wired live: `admin_ai.py:159` (rotate_key), `:187` (add key).
- **Failure**: any `POST /admin/ai/keys` → asyncpg `UndefinedTableError: relation "ragbot.ai_keys" does not exist` → 500. The V16 DB-backed key-pool is built-but-broken; 0 keys ever persisted (consistent with the 0-row count). Only `.env` keys work.
- **Expert fix**: drop the `ragbot.` prefix (or interpolate `RAGBOT_SCHEMA="public"`, already defined at `models.py:53` "kept for backward compat"). 5-line change. **Guard**: an integration test that inserts+reads one key through the repo against the real DB.
- **Impact**: Correctness/ops; blast radius = the entire encrypted key-rotation feature.

### SEC-4 — Ingest idempotency key omits `record_bot_id` + `workspace_id` (ports F1) · CONFIRMED

- **FACT**: `idempotency_key.py:40-52` `for_ingest_document` builds `sha256("ingest"|tenant|source_url|corpus_version=0)` — **no bot, no workspace**. Contrast `for_chat_message` (`:23-37`) which correctly includes `record_bot_id`.
- **Gate**: `use_cases/ingest_document.py:76` `if existing is None and await self._idem.is_duplicate(idem_key)`. For bot B (same tenant, same URL, within the 24h TTL), `existing = get_by_tool_name(record_bot_id=B, …)` → None (B has no such doc) → `is_duplicate` sees bot A's key (bot-agnostic) → True → returns bot A's `job_id` with 202; **no doc/job/chunks created for bot B**, response looks successful.
- **Root chain**: L1 bot-B doc silently swallowed ← L2 idempotency hit on a bot-agnostic key ← L3 `for_ingest_document` parts omit bot/workspace. Immutable cause = **L3 key composition**.
- **Expert fix**: add `str(record_bot_id)` + `workspace_id` + `tool_name` to `for_ingest_document` parts (mirror `for_chat_message`). Domain-neutral, 1-line. **Guard**: a test asserting two bots ingesting the same URL both get distinct jobs.
- **Impact**: Correctness/Revenue (multi-bot data-loss on the canonical API). HYPOTHESIS on frequency (needs two same-tenant bots sharing a URL within 24h), FACT on mechanism.

### SEC-5 — "stats-rows-fabricated-tenant-uuid" · REFINED (two distinct things)

The task's phrase maps to two verified-but-distinct facts, neither a data-corruption on the production stats table:
1. **Shared fallback `UUID(int=1)`** (`chat_async.py:74`, `test_chat/_shared.py:47`) — demo/harness callers with no tenant claim all resolve to `00000000-…-0001`, co-mingling their bots/history under one tenant bucket. **FACT**; blast radius **demo-only** (routes under `/api/ragbot/test/*`, gateway-blocked per CLAUDE.md) — a mixing point only if the `/test/*` block is mis-configured.
2. **Stats docstring drift** (`stats_index_repository.py:4-8`) — claims every read filters `record_tenant_id` + `session_with_tenant` + GUC `app.current_tenant_id`. Reality: only `bulk_insert` binds tenant; all 6 reads + `delete_by_document` use plain `self._sf()` with `record_bot_id`-only; the GUC name in the docstring (`app.current_tenant_id`) is wrong — the real GUC is `app.tenant_id` (session.py:82). The stats **write** path receives a real tenant from ingest (not fabricated). **FACT** (drift), not a fabrication bug.
- **Verdict**: OVERCLAIM-guard — there is **no production stats row written with a fabricated tenant UUID**. The real issues are the demo fallback (bounded) + a materially-misleading docstring. Fix: correct the docstring; keep the demo fallback gateway-blocked.

---

## Cross-cutting: the two structural levers

Of ~30 verified instances, two guards collapse the recurrence surface:
- **AST pin-test** (S1.3) — kills the undeclared-key class at collection time. Prototype runs, catches 22 today.
- **Wiring-audit + un-mocked integration smoke** (S2.2) — kills the built-not-wired class. Would have caught PII, sanitizer, source-validator, GraphRAG×2, cascade, xml-wrap.
Plus the S3 **shape-only header fallback + canary corpus** to convert silent-zero into graceful-degrade + measured.

## OVERCLAIM / REFUTE ledger (skeptical corrections vs pass-1)

| Pass-1 claim | Pass-2 verdict | Correction |
|---|---|---|
| S1 = "≥11 undeclared keys" | **REFINED (undercount)** | 22 used-undeclared; **`rerank_score_mode`** is a NEW live cross-node drop; `action_state` is cosmetic (DB-backed) |
| L1-6 "re-ingest 1 row → 99 entities vanish" | **OVERCLAIMED** | delete is per-`doc_id`, guarded by `if _stats_entities`; not a cross-document wipe |
| L2-6 "RLS chết fallback stage" (leak framing) | **REFINED** | stages DO filter `record_bot_id` (unique UUID) → no cross-tenant leak; real bug is **soft-delete resurrection** (no `deleted_at` filter) |
| "stats-rows-fabricated-tenant-uuid" | **REFINED / OVERCLAIM-guard** | no fabricated tenant on production stats writes; = demo `UUID(int=1)` fallback (bounded) + docstring GUC-name drift |
| F5 stats "sets `app.current_tenant_id`" | **CONFIRMED as drift** | real GUC is `app.tenant_id`; docstring names a GUC no policy reads |

## Compliance note (all proposed fixes)
- Sacred #10: every S3 detector / verification node is retrieval-tier or observe-only — **no answer override**.
- Sacred #7: no psql hot-fix proposed; `ai_keys` fix is code, RLS cutover is ops+alembic-tracked.
- Domain-neutral: S3 shape-only header fallback keyed on FORM, not vocab; currency → config.
- Zero-hardcode: `cross_doc_reconcile_enabled` inline `True` (query_graph.py:2380) + `_autocut` gap are the residual nits.
- 4-key: SEC-4 fix restores bot+workspace into the idempotency key.
- HALLU=0: F4 (int-price), F-2 (inverted grounding gate), stats-route-skip are the HALLU-adjacent items — all retrieval/guard tier, no fabricated text.
