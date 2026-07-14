# Code Deep-Dive Review — all flows × CLAUDE.md mindset — 2026-07-11

> Method: 6 parallel read-only Explore agents mapped 5 pipeline flows (ingest,
> retrieval, generate+guard, async-callback+router) + a sacred-rule grep sweep +
> a clean-code/SOLID structural audit. Main session (Opus) cross-checked and
> graded. Every finding carries a `file:line` anchor.
>
> **rule#0 labelling:** the `file:line` facts below are SỰ THẬT (the code IS thus,
> verified by reading). The *runtime impact* of each (how much coverage/cost it
> costs) is GIẢ THUYẾT until load-test-measured — flagged where it matters. This is
> a STATIC code review, not a runtime eval. Nothing here is "fixed"; this is the
> backlog with evidence.

---

## 0. Headline

- **Architecture is genuinely expert-grade** (confirms the "EVOLVE not REWRITE" stance): Hexagonal boundary real (56 Port files), Registry+NullObject for all 5 swappables, DI container real (52 `providers.Singleton`), **0 provider `if/elif` ladders** in orchestration, **0 un-annotated broad-except**, tenant/bot isolation **correct** (`pgvector_store.py:258` WHERE + null-guard `:310` + RLS `session_with_tenant`). Sacred-rule sweep: **10/12 clean**.
- **The risk is not the frame — it's the wiring on the answer path.** The two things that can actually let a wrong answer through are both in the grounding gate, and both are default-off/observe today. Plus a cluster of async-callback reliability gaps (dup turn, silent drop, orphan event) that are cost/UX, not HALLU.
- **Two domain-neutral leaks** (bilingual connector vocab in chunk-split; `innocom` brand in ~8 prod comments) and one **doc footgun** (ZeroEntropy dim 2560 vs 1280).

---

## 1. Truth-table — every stage graded L1/L2/L3

`L1 EXISTS` (code present) · `L2 WORKS` (runs on real request) · `L3 VERIFIED-GOOD` (measured, meets target). Grades reflect *this* review's evidence; L3 requires the runtime eval we ran 2026-07-10.

| Stage | Grade | Evidence | Gap to next level |
|---|---|---|---|
| HTTP ingest `/documents/create` | **L2** | `documents.py:91-189`, idempotency `:117-161` | Replay returns empty `job_id`/`tool_name` when still processing (`:154-160`) |
| Parser registry (mime→ext→sniff) | **L2** | `parser/registry.py:97-179`, 8 adapters | Fail-soft to NullParser masks real breakage (`:83-89,:110`) |
| Chunking (proposition merge) | **L2** | `strategies.py:631-714` merge `:667-690` | Bilingual connector vocab hardcoded `:661-665` (domain-neutral FAIL) |
| Embedding (ZE zembed-1 1280d) | **L2** | `zeroentropy_embedder.py`, dim-verify `:163` | Docstring says 2560 vs constant 1280 (`:1,65`) — footgun |
| Stats index (ADR-0008 shape-typing) | **L2** | `document_stats.py:660-670` shape-name pick | `name_by_shape=False` legacy default (`:636`); `parse_money_vn` locale copy |
| Retrieve (hybrid BM25+vector) | **L3*** | `pgvector_store.py:258`, isolation correct; measured 2026-07-10 | *L3 for delivered answers only; 3 size-miss gaps observed |
| Rerank (zerank-2 DI) | **L2** | `rerank.py:56-523`, NullReranker isinstance bypass | clean; no provider hardcode |
| Grade (CRAG) | **L2** | `grade.py:60-564` | clean |
| Prompt-build (SysPromptAssembler) | **L3** | append-only confirmed `sysprompt_assembler.py:141`; sacred-#10 clean | — |
| Stats null-price formatter | **L2** | `query_graph.py:366-438` emits marker | Marker suppressed for all-priceless sets (`:414-424`) |
| Generate (verbatim answer) | **L3*** | `generate.py:175`; answer read verbatim, citations filtered only | measured HALLU=0 delivered 2026-07-10 |
| Guard: numeric-fidelity gate | **L3** | `numeric_fidelity.py`; proved block works (0909 case) 2026-07-10 | default OBSERVE; owner must enable block |
| Guard: grounding gate | **L2** | `guard_output.py:702-718` | **fail-OPEN on judge exception**; block no-op in serial path |
| Async outbox + publisher | **L2** | `outbox_publisher.py:97-146` FOR UPDATE SKIP LOCKED | fallback path lacks exactly-once `:209-237` |
| chat_worker consume→ACK | **L2** | `pipeline.py:97,757-774` | no consume-side idempotency → dup turn on redeliver |
| Callback delivery | **L2** | `callback_delivery.py:123-151` | exhaustion silently drops answer; empty=success |
| LLM router (semaphore/CB/failover) | **L2** | `dynamic_litellm_router.py` | binding-less bots get 0 failover; 200-empty not retried |

`*` = L3 only for the *delivered* subset per the 2026-07-10 clean run (coverage ~91%, HALLU=0); the residual provider-truncation cases keep the whole path off a clean L3.

---

## 2. Findings ranked by CORE-MVP tier (T1 smartness > T2 cost/perf/UX > T3 refactor)

### T1 — answer correctness / smartness (highest priority)

| # | Finding | Anchor | Impact (label) | Fix tier |
|---|---|---|---|---|
| T1-1 | **Grounding judge EXCEPTION → fail-OPEN**, ships unverified answer; contradicts fail-CLOSED default for the unwired case | `local_guardrail.py:514-520`, `guard_output.py:702-718` | GIẢ THUYẾT: a judge crash silently disables the non-numeric HALLU net. Numeric-fidelity gate still independent → numbers still safe | short: make exception path honor `grounding_failure_mode` |
| T1-2 | **`grounding_confirmed_action="block"` is a no-op in the serial path** — only wired in parallel branch; serial returns `severity="warn"` which never blocks | `guard_output.py:814` vs `:825-867`, `local_guardrail.py:541-544` | SỰ THẬT (code): owner enabling block w/o parallel gets no enforcement | short: wire block in serial or force-parallel when block on |
| T1-3 | **Bilingual connector vocab hardcoded in chunk-split** decides proposition boundaries (`và\|hoặc\|...\|and\|or\|but`) | `strategies.py:661-665` | GIẢ THUYẾT: any non-VN/EN doc mis-chunks → retrieval miss → coverage loss. Domain-neutral violation on a structure path | mid: move to language-pack data / shape-based split |
| T1-4 | **ZeroEntropy dim docstring 2560 vs constant 1280** | `zeroentropy_embedder.py:1,65` vs `_02_*.py:68` | SỰ THẹT (contradiction). Silent vector-corruption footgun if anyone trusts the docstring | short: fix docstring (doc-only, no code change) |
| T1-5 | **Stats null-price marker suppressed for all-priceless sets** — bare name, no `price:` field → number-borrow risk the marker exists to prevent | `query_graph.py:414-424,:2571-2575` | GIẢ THUYẾT: re-introduces the ADR-0008 fabrication vector on all-priceless queries | mid: always emit marker on price-ask |
| T1-6 | **`parse_money_vn` reimplemented in `document_stats.py`** — independent copy of the number standard drives every stats header/value gate | `document_stats.py:280` vs canonical `number_format.py:156` | GIẢ THUYẾT: stats-vs-ingest number-parse drift → wrong entity typing | mid: delegate to canonical under parity tests |

### T2 — cost / perf / UX / reliability (medium)

| # | Finding | Anchor | Impact | Fix |
|---|---|---|---|---|
| T2-1 | **Crash-before-ACK duplicates the whole turn** (re-run generate + re-POST callback) — no consume-side idempotency | `pipeline.py:97,757-774` | GIẢ THUYẾT: duplicate LLM spend + duplicate customer webhook on any mid-run crash | ON CONFLICT on request_id at consume (B3) |
| T2-2 | **Callback exhaustion silently drops the answer** — returns False, marks `delivery_failed`, never emits `chat.delivery_failed.v1`, no DLQ | `callback_delivery.py:150-151`, `callbacks.py:311-315` | SỰ THẬT (code): from caller's view the answer is lost | delivery-retry worker / emit the event (B2) |
| T2-3 | **`chat.answered.v1` outbox event has NO consumer** — produced, published to a Redis stream nothing drains → unbounded orphan | `callbacks.py:120-140`, only produced | SỰ THẬT: resource leak + dead contract | add consumer or stop producing |
| T2-4 | **Empty answer recorded/delivered `status="success"`** — no empty-body check; 200-empty never retried (exception-only retry) | `callbacks.py:231-246,:294-309`, `retry_policy.py:46-52` | SỰ THẬT: masks truncated/empty generations end-to-end (the 2026-07-10 truncation class) | empty→`status="empty"`+`ok:false` (B4) |
| T2-5 | **Binding-less bots get ZERO router failover** (fallback fields None) + resolver fallback re-implemented 4× with divergent behavior | `_binding_mixin.py:292-294`, `model_resolver/service.py:411-412` vs `:470-494` | SỰ THẬT: `resolve_embedding` raises w/o system_config fallback while `resolve_runtime` falls back — the recurring resolver bug | unify the NullObject fallback chain |
| T2-6 | **Second divergent entrypoint `/chat-async`** bypasses outbox, weaker tenant scoping (`UUID(int=1)` fallback vs strict 403) | `chat_async.py:74,95,266-269` | SỰ THẬT: dual pipeline, weaker isolation | consolidate onto outbox path or gate off |

### T3 — clean-code / SOLID / design-pattern (lowest — do NOT prioritize over T1/T2)

| # | Finding | Anchor | Sev | Effort |
|---|---|---|---|---|
| T3-1 | `query_graph.py` **3071 LOC** god-file; `build_graph` a 2000-line closure factory w/ ~20 nested nodes | `query_graph.py:981` | High | L — mechanical extract under characterization tests |
| T3-2 | `generate()` **single 924-line async fn**; `retrieve()` **752 LOC, 370 lines ≥7 nesting levels** | `generate.py:175`, `retrieve.py:210` | High | L — answer path, guard with tests |
| T3-3 | **Infra imports leak into orchestration** (Port boundary): `query_graph.py:209,1166`, `retrieve.py:144,1634`, `rerank.py:100` (NullObject OK) | as listed | Med | M — route via Port/registry |
| T3-4 | **`state: Any` untyped graph** (63 `Any` in query_graph, 35 retrieve, 16 generate) — defeats static checking on answer path | those files | Med | M — introduce typed `GraphState` |
| T3-5 | `DynamicLiteLLMRouter` does routing+CB+metering+streaming+cost in one class (SRP) | `dynamic_litellm_router.py:370` | Med | M — infra, off answer path (safe first) |
| T3-6 | Node boilerplate + `.as_dict()` coercion copy-paste; adjacent duplicate `elif chunk_has_price` blocks | `neighbor_expand.py:235,440`, `query_graph.py:414,421` | Low | S |
| T3-7 | `innocom` brand literal in ~8 prod comments/docstrings + `INNOCOM-3SVC-SWAP` plan ref | `dynamic_litellm_router.py:789,985,1054`, `ai_config_repository.py:46-50`, etc. | Low | S — genericize to `<llm-provider>` |
| T3-8 | Minor hardcodes outside constants: `_STREAM_INCLUDE_USAGE_PROVIDER_CODES` inline `router:318`, `User-Agent "Ragbot-Webhook/1.0"` `callback_delivery.py:108`, sniff-magic literals `parser/registry.py:133-140`, `_MIN_DECOMPOSITION_SUBQUERIES` `decompose.py:27`, `flags=5` `retrieve.py:1118` | as listed | Low | S |

---

## 3. Sacred-rule scorecard (CLAUDE.md 11 Quality Gate + sweep)

| Rule | Verdict | Evidence |
|---|---|---|
| #0 no-guess / label | ✅ (this report labels SỰ THẬT vs GIẢ THUYẾT) | — |
| Zero-hardcode | ⚠️ mostly clean; a handful of inline literals (T3-8) | spot-check clean, `_pcfg` defaults + T3-8 exceptions |
| Domain-neutral | ❌ 2 leaks | connector vocab `strategies.py:661`; `innocom` in prod comments |
| No version-ref | ✅ 0 prod violations | sweep 1a/1b/2a/2b all comments/tests/vendor URLs |
| Strategy+DI / Port | ⚠️ pattern strong; infra imports leak in orchestration (T3-3) | 56 ports, 0 provider ladders; 5 boundary leaks |
| Broad-except policy | ✅ 0 un-annotated | all carry `# noqa: BLE001`; some fail-open masking noted |
| 4-key identity / tenant iso | ✅ correct | `pgvector_store.py:258,310`, RLS; ⚠️ `/chat-async` weaker |
| App no-inject / no-override (#10) | ✅ compliant-by-design | append-only assembler; all overrides → DB `oos_answer_template`, default-off. Large surface but governed |
| HALLU=0 sacred | ✅ (numeric gate independent, proved) | but T1-1/T1-2 weaken the non-numeric net |
| No secret/tenant literals | ✅ 0 real | all DSN/IP hits are guards/tests/SSRF-blocklist |
| No psql hotfix (#7) | ✅ (this session: alembic only) | — |
| Model tier (Opus main / Sonnet subagent) | ✅ this review: Opus synthesized, Explore gathered | — |

---

## 4. Recommended order (respects T1>T2>T3; one change per step; measure each)

**Phase A — T1 correctness, cheap + high-value (do first):**
1. T1-4 fix ZeroEntropy docstring (doc-only, 0 risk).
2. T1-1 grounding exception → honor `grounding_failure_mode` (red test first: judge raises → answer blocked when fail-closed).
3. T1-2 wire `grounding_confirmed_action=block` in serial path (or force parallel when block enabled).
4. Re-run the pinned 60Q eval → confirm coverage no-regress + HALLU still 0. **No two enabled at once.**

**Phase B — T1 structure-path (needs measurement):**
5. T1-3 connector vocab → language-pack data; T1-5 always-emit null-price marker; T1-6 dedup `parse_money_vn`. Each with parity/characterization tests + 60Q delta.

**Phase C — T2 reliability (cost/UX, independent of provider):**
6. T2-4 empty→status (B4), T2-2 callback DLQ/event (B2), T2-1 consume-side ON CONFLICT (B3), T2-3 orphan event, T2-5 unify resolver fallback. These are the "our-control" items from RELIABILITY_FIX_20260710 §3.

**Phase D — T3 refactor (only after T1/T2 green):**
7. Typed `GraphState` (T3-4) first (high leverage, mechanical), then god-file extraction (T3-1/2/3) under characterization tests, then genericize `innocom` (T3-7) + hardcode sweep (T3-8).

**Discipline:** Phases A–B touch the answer path → red-test-first, one fix per step, re-run pinned 60Q and attribute the delta to that fix alone (constitution). C–D are structurally isolated. Do NOT start D while any T1 item is open (anti-pattern: refactor while bot still mis-answers).

---

## 5b. DEEP-DIG corrections (2026-07-11, after reading the actual code)

Traced the "not-fully-clear" T1 items to source. **Two self-refuted, one downgraded** — this is the rule#0 discipline working (static agent evidence over-claimed; reading the code corrected it).

| # | Original claim | Verdict after reading code | Evidence |
|---|---|---|---|
| T1-1 | grounding judge exception → fail-OPEN, ignores `fail_closed` | ✅ **CONFIRMED REAL** | serial `local_guardrail.py:514-520` `return None`; parallel `guard_output.py:703-718` ships. `fail_closed` default only covers the UNWIRED case (`_grounder_dead`, `:511-523`), NOT a runtime judge crash. Bonus: serial grounding returns `severity="warn"/action="hitl"` even on CONFIRMED-ungrounded (`local_guardrail.py:541`) → serial never blocks. Residual = non-numeric fabrication shipped when judge crashes; numbers still safe (numeric-fidelity gate independent, runs first). |
| T1-2 | grounding `block` no-op in serial (High) | ⚠️ **CONFIRMED but DOWNGRADED → Med-low** | `DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED=True` (`_11_*.py:294`) → parallel default ON → block works on the default path. Only bites a bot that BOTH disables parallel AND sets `grounding_confirmed_action=block` — narrow config combo, not a default bug. |
| T1-5 | null-price marker suppressed for all-priceless sets → borrow risk | ❌ **REFUTED / mitigated** | `query_graph.py:2571` `_chunk_has_price = _force_price_absent or any(...)`. A price-ask on an all-priceless set sets `_force_price_absent=True` → marker IS emitted. Suppression only on a NON-price-ask with zero priced siblings — where there is no neighbour number to borrow. Correct by design (`_serialize_stats_entity_row:388-389`). |
| T1-6 | `parse_money_vn` reimplemented in document_stats → drift | ❌ **REFUTED** | `document_stats.py:37` `from ragbot.shared.number_format import parse_money_vn as _canonical_parse_money`; `:280` is a thin delegating wrapper, not a copy. The clean-code agent mis-read a wrapper as a reimplementation. No drift. Naming-smell only (`_vn` in a shape path). |
| T2-3 | `chat.answered.v1` orphan event | ✅ **CONFIRMED** | `callbacks.py:120-141` `uow.add_outbox(ChatAnswered)` + commit; grep: 0 subscribers (only chat.received / system_config / token_revoked / document.uploaded subscribed). Published to a Redis stream nothing drains. (Unbounded-growth depends on the publisher's MAXLEN trim — not re-verified.) |

**Net after deep-dig — the T1 list that survives as real:** T1-1 (grounding fail-open on judge crash, real but numeric-safe), T1-3 (connector-vocab hardcode — SỰ THẬT it's hardcoded, impact still GIẢ THUYẾT), T1-4 (dim docstring — FIXED). T1-2 is a latent config trap; T1-5/T1-6 refuted.

**Fixes shipped this pass (verified):** T1-4 docstring 2560→1280 (runtime confirmed 1280 via constant + `.env EMBEDDING_DIMENSION=1280`); T3-7 `innocom` genericized in 5 prod comments.

## 5. What this review did NOT verify (honesty)
- Runtime impact of each T1 finding is **GIẢ THUYẾT** — needs the pinned-60Q A/B per fix.
- Grounding fail-open: not reproduced at runtime (no forced judge-crash trace yet).
- Connector-vocab mis-chunk: not measured on a 3rd-language doc.
- Refactor items are structural risk, not behavioral bugs — no correctness claim.
