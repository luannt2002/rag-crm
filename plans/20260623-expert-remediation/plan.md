# Expert Remediation — ALL flows → expert · 2026-06-23

> Master plan to lift every audited flow from its current grade to **expert** (bar = 5-criteria:
> Nhanh / Đúng / UX / Performance / Cost, multi-tenant). Source of findings:
> `reports/EXPERT_DEEP_AUDIT_20260623.md` (Sections A–J) + jump-table memory `project_deep_audit_20260623`.
> **Done = (a) fix at the correct layer · (b) failing test first (TDD) → green · (c) A/B metric measured
> (HALLU stays 0, p95 ceiling) · (d) sacred 11/11 re-audit · (e) no new ruff / no version-ref / domain-neutral.**
> No claim of "done/expert" without runtime evidence (rule#0).

## Global sequencing (T1 > T2 > T3)

| Wave | Theme | Items | Why first |
|---|---|---|---|
| **W1** | Multi-format input correctness (T1) | A-I1, A-I2, A-I5/B-2, B-3, B-4, B-1 | parser→Block (A-I5=B-2) is the cross-flow lever: unblocks ingest **and** AdapChunk L2/L3/L6/L7 |
| **W2** | Retrieval coverage (T1) | E-1, I-1 | entity-fairness RRF closes comparison coverage; narrate VN-hardcode blocks non-VN bots |
| **W3** | Cost-log / observability (T2) | G-1, G-2, G-3, G-4, G-6 | streaming emit + Port-boundary emit + ws/tenant rollup = the CRM cost center |
| **W4** | Perf (T2) | J-1, J-3, J-4, A-I4 | async grounding factoid, bound fan-out, rerank skip, late_chunking memory |
| **W5** | Hardening (T3) | D-B1, D-B2, F-1, F-5, F-4, F-6 | RBAC harness, IDOR-write fence, RLS policy-in-git |
| **W6** | Hygiene (T3) | comment-EN, H-1, B-6, C-2, A-I6, F-2, E-2 | domain-neutral SSoT, comment standardization, doc drift |

---

## FLOW A — INGEST / UPLOAD (HAS_GAPS → target EXPERT 9.0)

| Fix | File:line | How (expert) | Done-criteria | Tier |
|---|---|---|---|---|
| **A-I1** worker robust-detect | `document_worker.py:379,544` | thread `raw_bytes=raw` into `doc_service.ingest()` + **delete worker-local parse block** → service is single parse SoT (uses `sniff_real_mime`+`detect_parser_robust`) | TDD: octet-stream XLSX URL → correct parser; sync+async parse identical on same bytes | T1 |
| **A-I2** OCR suffix forces .docx | `ocr/kreuzberg_parser.py:96-118` | route `_suffix_for_mime` through `sniff_real_mime`/`_peek_zip_office_subtype` (reads `[Content_Types].xml`); add OLE2 magic branch | TDD: octet-stream XLSX bytes → `.xlsx`; `.doc` OLE2 → explicit unsupported, not `.bin` | T1 |
| **A-I3** legacy .doc/.xls | `mime_sniff.py` + registry | OLE2 magic (`\xd0\xcf\x11\xe0`) → either LibreOffice-headless convert adapter OR fail-fast `UNSUPPORTED_LEGACY_FORMAT` | TDD: `.doc` bytes → clear actionable error | T1 |
| **A-I4** late_chunking memory | `ingest_stages_store.py:319`, `late_chunking.py:99` | slice `late_chunk_embed` by `DEFAULT_EMBED_DOC_BATCH_SIZE` + config max-chunks-per-doc | A/B: 224KB sheet, RSS before/after | T2 |
| **A-I5** typed Block emission | parser adapters + `DocumentParserPort` | extend Port with optional typed Block list; structured parsers emit HEADING/TABLE/TEXT + `is_atomic` (= same model OCR already emits) | TDD: docx/xlsx parse → non-empty blocks; **also unblocks B-2/3/4/5** | T1 |
| A-I6 ocr_factory drift | `ocr_factory.py` | pick one contract: re-raise OR fix docstring + preflight resolved==configured | test asserts behavior matches docstring | T3 |
| A-stream-disabled doc | `documents_stream_upload.py` | one-line DISABLED module docstring or ADR-remove | grep reads as disabled | T3 |
**Lift map**: A-I1 → `document_worker.py` 6.0→9.0 · A-I2 → `ocr/kreuzberg_parser.py` 6.5→9.0 · A-I5 → 4 parsers 8.5→9.5.

## FLOW B — CHUNKING / AdapChunk (C+ 6.0 → target EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **B-2** block-pipeline no-op | depends on **A-I5** | once parser emits blocks, `ctx.blocks` non-empty → L2 runs for all formats | TDD: registry-parsed docx → block path (not text-flatten); add structlog warn when flag ON + blocks empty | T1 |
| **B-3** atomic-protect OFF | `__init__.py:490,653` | route executor through `smart_chunk_atomic(blocks)`; flip `formula_image_atomic_protect_enabled` ON after soak | TDD: TABLE never cut mid-block; load-test soak | T1 |
| **B-4** L3 entity not fed | `ingest_stages.py:595-602` | make `profile_entity`/`profile_to_dict` the single selector input; delete parallel dict | TDD: selector decision uses entity features | T1 |
| **B-1** LLM selector orphan | `infrastructure/chunking_strategy/` | ADR: wire into U4 via bootstrap Singleton keyed on `system_config chunking_strategy_provider` (default 'rule') **OR delete** | ADR + (if wired) resolver invoked in U4 | T1 |

## FLOW C — ANSWER / GENERATION (A− 9.2 → EXPERT 9.7)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| C-1 `price_buoi_le` literal | `jsonb_conversation_state.py:200` | drop the legacy fallback (TTL-bounded rows expire) + scrub docstrings; pre-commit grep `buoi_le\|price_goc` | grep src/ = 0; tests green | T3 |
| C-2 stale math-lockdown docstrings | `chat_routes.py:762-772` | delete the removed-override doc paragraph; mark `find_ungrounded_numbers` cache-only or delete + retire 2 tests | grep 'replace' doc = 0 | T3 |
| C-3 `_extract_locked_prices` in node | `generate.py:90-109` | (optional) relocate behind conversation_state Port | no sacred change | T3 |
→ Already sacred-10 compliant; this flow only needs hygiene to reach 9.7.

## FLOW D — CHAT-ENTRY + TEST-CHAT (OK_MINOR / HAS_GAPS → EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **D-B1** destructive endpoints ungated | `monitoring_routes.py:55-97`, `chat_routes.py:1182`, `bot_admin_routes.py:444` | router-level `dependencies=[require_min_level_dep(80)]` on `test_chat.router` | TDD: non-owner → 403 on reinit/clear/delete | T3 |
| **D-B2** harness never-external | `router.py:101` | env-flag `RAGBOT_TEST_HARNESS_ENABLED` mount gate (default OFF prod) | mount skipped when flag off | T3 |
| D-A1 streaming ledger | = G-1 | (covered in Flow G) | — | T2 |
| D-A2 tenant strictness divergence | `chat_async.py:82-99` | gate `UUID(int=1)` fallback behind same harness flag | strict in prod | T3 |
| D-A3 bypass-probe silent | `tenant_context.py:205` | `logger.debug` instead of bare pass | log emitted | T3 |

## FLOW E — RETRIEVAL (OK_MINOR → EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **E-1** entity-fairness RRF dead | `rrf_round_robin.py` | wire into `rrf_fuse` (`retrieve.py:1334`) for comparison/multi_hop with `per_entity_quota` from per-bot config (0=plain RRF) | A/B: comparison query retrieves both entities; single-entity bit-exact | T1 |
| E-2 bm25_flags=5 ×3 | `pgvector_store.py:364` +2 | import `DEFAULT_BM25_NORMALIZATION_FLAGS` | grep literal = 0 | T3 |
| E-3 safety-net stamp vs CRAG | `rerank.py:457-493` | empty-pool safety chunk → stamp ≥ `crag_min_fallback_score` | TDD: zerank-burial repro survives | T2 |

## FLOW F — MULTI-TENANT / RLS (B+ 7.8 → EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **F-1** IDOR-write | `document_repository.py:96-114`, `conversation_repository.py:158` | fenced `UPDATE … WHERE id AND record_tenant_id RETURNING id`, assert rowcount==1 | TDD: foreign-uuid overwrite → 0 rows | T3 |
| **F-5** RLS policies not in git | `alembic/versions/` | idempotent migration re-asserting ENABLE/FORCE RLS + CREATE POLICY for 20 tables; pin with `pg_policies` introspection test | fresh clone has policies; test green | T3 |
| F-2 job_repo no fence | `job_repository.py:74-76` | split scoped vs system_fail (BYPASSRLS) | TDD | T3 |
| F-4 stats_index RLS-blind | `stats_index_repository.py` | route reads through `session_with_tenant` | survives DSN flip probe | T3 |
| F-6 D2 quota cascade | `quota_repository.py` | add workspace_id + resolve chain OR close D2 as slug-sufficient | ADR decision | T3 |

## FLOW G — COST-LOG CENTER (C+ 6.0 → EXPERT 9.0) — the RAG+CRM report center

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **G-1** streaming no emit | `dynamic_litellm_router.py:790-991` | emit `TokenLedgerEntry` post-stream from existing `prompt_total/completion_total/cost_decimal`; shared helper w/ non-stream | TDD: SSE turn writes 1 ledger row | T2 |
| **G-2** per-adapter emit | `build_embedder/build_reranker` | Ledger-emitting **decorator at Port boundary** → fires for every provider | TDD: non-jina provider emits | T2 |
| **G-3** no ws/tenant rollup | `token_ledger_analytics_repository.py` + `admin_metrics.py` | add rollup methods (per-bot/ws/tenant + bot_count) + endpoints `/metrics/usage/rollup` (RBAC 60) + `/all-tenants` (RBAC 100), time-range bounded (Section G SQL) | endpoints return correct sums; RBAC enforced | T2 |
| **G-4** emit fidelity | router emits + `aux_usage` | real `started_at`/`duration_ms`/`purpose`/unit-price/`request_id` on every row | rows fully populated | T2 |
| **G-6** request_id ctx | `aux_usage` + router | add `request_id_ctx` + reconciliation test `Σledger.cost by request_id ≈ request_logs.cost` | reconciliation test green | T2 |
| G-8 purpose breakdown | `..._repository.py:24-29` | add `purpose` to whitelist | breakdown works | T2 |

## FLOW H — DOMAIN-NEUTRAL (8.5 STRONG → 9.5)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| H-1 bare-literal fallbacks | `pipeline_config.py` ×2 | import existing constants; add `DEFAULT_*` for the ~5 keys lacking one | grep bare-literal = 0 | T3 |

## FLOW I — MULTI-LANGUAGE (B 7.5 → EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **I-1** narrate VN-hardcode | `llm_narrate.py:58-73` | add `language` param threaded from bot; per-block prompt from `language_packs[locale]`; EN fallback (not VN) | TDD: EN bot → EN narrate prompt | T1 |
| I-2 get_pack VN fallback | `i18n.py:402-407` | unknown locale → EN pack + warn | TDD: 'km' bot → EN not VN | T2 |

## FLOW J — COST / PERF (B− → EXPERT 9.0)

| Fix | File | How | Done | Tier |
|---|---|---|---|---|
| **J-1** sync grounding factoid | `guard_output.py`, constants | flip `DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED=True` for factoid (async set already scopes it) | A/B: factoid p95 before/after; HALLU=0 held | T2 |
| **J-3** unbounded fan-out | `retrieve.py:1307,1381` | wrap with `asyncio.Semaphore(DEFAULT_RETRIEVE_FANOUT_CONCURRENCY_N)` like `grade.py:322` | load-test: pool not saturated | T2 |
| J-4 rerank_skip empty | constants | seed `DEFAULT_RERANK_SKIP_INTENTS=('greeting','chitchat','vu_vo','out_of_scope')` (NOT factoid) | A/B: refuse-rate + HALLU unchanged | T2 |
| J-5 cache-hit unverified | `check_cache.py:91` | instrument hit/miss → measure 7-day ratio before tuning | ratio measured | T2 |

---

## Cross-cutting workstream — comment standardization (T3, all flows)
Per `plans/20260623-ingest-flow-clean/plan.md`: VN→EN, strip temporal/version refs (`260525`, `Sprint`,
`Bug #`, `v2/legacy`), module+function docstrings (purpose + contract + WHY). No logic change; pytest green per batch.

## Verification gate (every wave)
1. `set -a && source .env && set +a` · 2. `pytest tests/unit -q` green (0 regression) · 3. ruff touched files = 0 new ·
4. grep guards (version-ref / domain-literal / `if provider==`) = 0 · 5. for T1/T2: a load-test A/B (HALLU=0,
coverage delta, p95) — **no "done" without runtime number** · 6. sacred 11/11 re-audit pasted per PR.

## Status (live)
- **A-I2 ✅ DONE (TDD)** — kreuzberg 6.5→9.0. OCR fallback sniffs real OOXML subtype; +2 tests; 28 pass, 0 new ruff.
- **A-I6 ✅ DONE** — ocr_factory 7.0→9.0. Doc-drift fixed: docstring now honestly describes the *test-pinned*
  graceful kreuzberg→simple fallback (NOT fail-loud). Behavior-preserving (AST-identical); switching to
  fail-loud is an ADR decision, not a doc edit. 11 pass, 0 new ruff.
- **Comment clean-batch (8 files) ✅ re-applied** (4 agents) — all AST-IDENTICAL (logic untouched):
  `ingest_core · __init__ · google_link_service · tabular_markdown · sync · documents · router · documents_stream_upload`.
- **A-I1 — DESIGNED, queued (needs focused TDD turn)**. Root cause: detection at `document_worker.py:379`
  runs BEFORE the body is fetched (fetch is at `:385-390`, gated on `parser is not None`), so there are no
  bytes to sniff → it uses non-robust `detect_parser`. **Fix design**: (1) fetch `_raw` FIRST for refetchable
  URLs; (2) `parser = detect_parser_robust(mime, ext, _raw)` (registry.py:143 — sniffs octet-stream); (3) on
  registry miss, pass the already-fetched `_raw` to `ocr.parse(_raw, mime_type_hint=...)` (OCR `_resolve_bytes`
  accepts bytes) → SINGLE fetch + robust detect + A-I2 sniff both fire. Risk: worker is a hard-to-unit-test
  embedded consumer — build a focused test (mock httpx + parser registry) before editing. Lifts worker 6.0→9.0.
- **A-I4 — queued**: `late_chunking.py:99` slice whole-doc embed by `DEFAULT_EMBED_DOC_BATCH_SIZE` + max-chunks guard.
- **A-I5 — queued (BIG, the cross-flow lever)**: extend `DocumentParserPort` with typed Block emission; 4 structured
  parsers populate HEADING/TABLE/TEXT + `is_atomic`; thread `blocks=` so AdapChunk L2 stops no-op'ing (unblocks
  B-2/B-3/B-4/B-5). Own focused effort with TDD per parser.
