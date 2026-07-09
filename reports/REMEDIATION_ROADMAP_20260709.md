# REMEDIATION ROADMAP — Ragbot · 2026-07-09
> Workflow wf_83fa6a69-1ba (24 agents, 0 err). 11 fixes adversarially-verified. Reclassify: xe 95.9%, spa 95.3%.

# REMEDIATION ROADMAP (code-truth)

*Lead-architect synthesis of the reclassified correctness picture + 11 adversarially-verified fix designs. Every claim carries `file:line` or a DB number. Baseline batch = graded `connect_id lt260708-G/S-*`, request_logs 07-08 window = 302 rows (295 success / 7 failed).*

---

## 1. Reclassified truth — what actually needs fixing

### 1.1 True correctness after removing infra 5xx

| Bot | Report said | **Code-truth** | Why the report was wrong |
|---|---|---|---|
| XE `c6e1fc56` | 91/97 = 94% | **93/97 = 95.9%**, HALLU=0 | Counted 2 innocom-5xx rows (G-064, G-095) as arrival/comparison **logic**-wrong |
| SPA `5f2e12a8` | 91/99 = 92% | **82/86 = 95.3%** | Counted the coref-4 (S-057/060/064/068) as orchestration-wrong; they are a **test-harness** defect |

Net: reclassification **raises** both bots. The graded batch had 5 innocom `InternalServerError`-after-retries rows (`completion_tokens=0`, `last_success_step=litm_order`, generate never returned): **G-053, G-064, G-095, S-048, S-056** — all INFRA, 0 logic content. All 7 failed rows in the full window are the same signature.

### 1.2 The REAL remaining logic-misses (this is the correctness backlog)

Only **8** real misses survive reclassification. Grouped by root layer:

| # | qid(s) | Bot | Flow | Root layer | Evidence | Addressed by |
|---|---|---|---|---|---|---|
| M1 | G-097, G-098 | XE | comparison | orchestration | status=success, only 1 of 2 entities answered; `adaptive_decompose` ran (60599ms / 8363ms) but `retrieve entity_count=1`; DB has both codes incl. price chunks (195/60R15=963k, 265/60R18=1944k) | **item 9(a)** + **item 6-prompt** (+ item 2 complement) |
| M2 | G-063, G-067 | XE | arrival_date | **retrieval/indexing** | status=success, false-refuse; corpus `chunk_type=table 'NGÀY VỀ … 28-thg 11'` contains the size; `retrieve entity_count=1` = PRICE entity only, arrival table **not linked** to the tire entity | **⚠ NO design in this batch** |
| M3 | S-039, S-046, S-075 | SPA | s3_buffet / s4_quytrinh / s6_lapluan | retrieval surfacing / rerank ordering | all status=success false-refuse; corpus HAS the answer (S-046 '16 bước' ×2 returned correctly on 4 other same-window runs); on the graded run the correct chunk missed top-K | **item 4** (grade mixed-branch rescue), measured |
| M4 | S-005 | SPA | s1_congty | generate | status=success, **fabricated `hotline 0909.999.999`** — 0 matches in `document_chunks`; `rerank top_score=0.148`, `grade_path=timeout_fallback` → thin context | already-planned **claim-fidelity Tier-1b** (non-numeric grounding gate) — not in this batch |

**Two items in this backlog have NO design in the batch — flag to owner (Section 5, GAP-A / GAP-B).**

### 1.3 Not-a-bug (drop from correctness work, route elsewhere)

- **5 innocom 5xx** (G-053/064/095, S-048/056) → external provider reliability, not code. See `defer_external`.
- **Coref-4** (S-057/060/064/068) → harness defect: `prompt_build.history_msgs=0`, `record_conversation_id=NULL` — no antecedent turn sent; `rerank top_score` 0.844–0.918 (retrieval healthy). Fix the **load-test harness** to chain multi-turn history, not the bot.

---

## 2. Ranked roadmap (by value × safety)

*Order: safe `do_now` first (highest value first), then `measure_first`, then `defer_external`.*

| # | Item | Rec | Risk | Effort | Value | Exact change (file:line) | Test | Measure-what |
|---|---|---|---|---|---|---|---|---|
| **8** | **stats delete scope** | do_now | low | S | **HIGH** (tenant-isolation sacred) | `stats_index_repository.py:251-256` add `AND record_bot_id = :bot_id`; kwarg at `ingest_stages_final.py:560` + `delete_document.py:92` | update 3 tests + pin `record_bot_id` bound not inlined | none (row-equivalent write-path; happy path unchanged) |
| **11** | **request_logs verdict persist** | do_now | low | S | **HIGH** (unblocks all measures) | thread `build_verdict_meta(final_state)` into `finalize_request_log` at `callbacks.py:230`, `chat_stream.py:379`, `chat_routes.py:609/:991`; **fix derivation: `'grounding' in rule_id`** (not `.startswith`) | `test_request_log_verdict_persist.py` incl. `llm_grounding_fail`→`flagged` | none (observability); post-ship SQL `SELECT … metadata_json->>'grounding_verdict'` reproduces 93/95.9% split |
| **1** | **URL-ingest OOM cap** | do_now | med | S–M | **HIGH** (prevents shared-API-process OOM) | replace unbounded `.content` at `document_worker.py:446-448` with bounded `_fetch_bounded` stream; **base sentinel on `ValueError` (not IngestError)**; ceiling = **`DEFAULT_UPLOAD_STREAM_MAX_BYTES` 500 MiB (not 50)** | new `test_document_worker_fetch_bounded.py` (a-d) | none (bytes identical ≤ cap); live VERIFY: 1 URL ingest per parser family |
| **7** | **health worker liveness** | do_now | low | S | MED (ops: dead worker → status ok today) | `app.py:444` set `app.state.embedded_worker_tasks`; `health.py` add `_check_workers` (`.done()`→`down`) before overall at `:150` | `test_health_worker_liveness.py` (live/dead-no-exc/dead-exc/empty) | none; manual: kill task → `/health` degraded, HTTP 200 |
| **5** | **config-parity guard (Part A)** | do_now | low | S | MED (detector; catches next drift) | widen scan `test_pipeline_cfg_keys_parity.py:121-123` to `nodes/*.py` glob; `_KNOWN_PCFG_DRIFT` = **9-key** frozenset; fix 4 stale comments (`_pipeline_config.py:128/:715`, `config.py:191`, docstring:16) | `pytest -v`; bogus-key proof | none (test+comment only) |
| **3a** | **cliff-floor clone parity** (split from item 3) | do_now | low | S | MED (clone ≠ prod) | const `_01_…:169` cliff `0.05→0.2`; seed migration cliff=0.2; **seed clone mmr at prod-actual 0.88** (parity, not flip); update `test_cliff_floor_calibrated.py` | migration round-trip; clone-parity DB assert | none (prod DB already 0.2) |
| **9a+6p** | **comparison unique-id + SKU-atomic prompt** | measure_first | med | M | **HIGH** (fixes M1 G-097/G-098) | 9(a): unique per-leg synthetic id in `_stats_chunks_for_sub_queries` (`retrieve.py:152-193`) — `f"{DEFAULT_STATS_SYNTHETIC_CHUNK_ID}:{leg_ix}:{sub_ix}"` before dedup (`retrieve.py:188-192`). 6-prompt: atomic-identifier rule in `query_decomposer.py:58-64` + `i18n.py` VI:502/EN:630 | `test_stats_per_subquery.py` (2 synthetic → 2 distinct); prompt-pin (scope "over-split" absence to `DECOMPOSER_SYSTEM_PROMPT` only) | `loadtest_graded.py chinh-sach-xe` G-095/096/097/098 + variants; ≥9/10 comparison, HALLU=0, single-lookup G-043..054 ≤0 regression |
| **4** | **CRAG mixed-branch top-1 rescue** | measure_first | med | S | **HIGH** (fixes M3 spa coverage) | `grade.py:508` else-branch: rescue top-1-by-rerank if dropped and clears per-intent floor; `or _rescued` at `:528`; flag `DEFAULT_CRAG_MIXED_TOP1_RESCUE_ENABLED` `_10_rbac.py` | `test_crag_mixed_top1_rescue.py` (fail-first + 3 guards) | graded-batch loadtest: S-039/046/075 improve/hold, HALLU=0; backward-trace rescued chunk reached prompt_build |
| **2** | **rrf_round_robin wire** (complement to 9a) | measure_first | low | M | MED (vector-fusion fairness; 9a is the primary M1 fix) | wire at `retrieve.py:1448` gated `decompose_active AND quota>0`; **restrict gate to `INTENT_COMPARISON`**; **thread `decompose_entity_quota` through BOTH builders** (`chat_worker/pipeline_config.py`, `test_chat/_pipeline_config.py`) — currently missing = un-enableable | `test_retrieve_decompose_round_robin.py` (no-op regression + minority-survives) | quota=2 vs 0 on comparison set; confirm **lexical is Null** on measure bot else `:1824` re-truncation confounds |
| **3b** | **mmr 0.88→0.98 live flip** (split from item 3) | measure_first | med | M | LOW-MED | prod DB flip only after measure; **do NOT** delete pre-existing `by_intent` row on downgrade (Bug#10) | resolve `test_per_intent_caps` `0.98>0.98` first | rag-loadtest 3 bots empty/unknown-intent path; coverage δ, HALLU=0 |
| **10** | **grounding-judge 30s→8s + per-bot override** | measure_first | low | S | MED (T2 perf: −22s on ~26% runs) | `_06_llm_defaults.py:122` add purpose-named `DEFAULT_GROUNDING_CHECK_TIMEOUT_S=8`; swap `local_guardrail.py:568/:605`; **ship per-bot `grounding_check_timeout_s` override WITH it, not YAGNI** | timeout→(0,0) unit; observe-path answer unchanged | request_steps `p95 30000→<8000`, 30s-count `52→0`; per-qid answer-diff=0; **hard gate: re-confirm 0/6 bots block-mode** |
| **6r** | **routing comparison short-circuit** (split from item 6) | defer (own repro) | med | S | LOW (LATENT, no repro) | `routing.py:111` short-circuit `INTENT_COMPARISON→decompose` | — | needs an actual suppressed-comparison repro first (with L1_ENABLED=True, `_router_route` rarely reached) |
| **INFRA** | innocom 5xx (5 rows) | defer_external | — | — | — | provider `InternalServerError` after retries — not code | — | retry-budget / failover / provider SLA |
| **GAP-A** | arrival table not linked (M2 G-063/067) | needs design | — | S–M | HIGH | attach/booster-retrieve `NGÀY-VỀ` table chunk when `intent=arrival` | — | new design required |

---

## 3. Dependency graph

```
                 ┌─────────────────────────────────────────────┐
                 │ #11 request_logs verdict persist (do_now)   │  ← foundational:
                 │  (+ 'grounding' in rule_id fix)             │    makes baseline
                 └───────────────┬─────────────────────────────┘    DB-reproducible
                                 │ enables DB-provable lift for every measure_first
        ┌────────────────────────┼───────────────────────────────┐
        ▼                        ▼                                ▼
 ┌──────────────┐   ┌──────────────────────────┐      ┌────────────────────┐
 │ #4 grade     │   │ COMPARISON CLUSTER (M1)   │      │ #10 async grounding│
 │ rescue (M3)  │   │  #9a unique-id  ── AND ──┐│      │  (T2 perf)         │
 └──────────────┘   │  #6-prompt SKU-atomic   ││      └────────────────────┘
                    │        (ship together)   ││
                    │  #2 rrf_round_robin  ◀────┘│ complement, BUT blocked on:
                    │    └─ needs key threaded   │   thread decompose_entity_quota
                    │       through BOTH builders │   through both _build_pipeline_config
                    └────────────────────────────┘
     #9c brand-aware  ── must come AFTER #9a, OWN measure (can regress G-097) ──┘

 #5a config guard (do_now) ──► #5b builder reconcile (measure_first, later)
 #3a cliff parity (do_now, independent)   #3b mmr flip (measure_first, after test_per_intent_caps decision)
 #8 stats-delete, #7 health, #1 OOM  ── all independent, no upstream dep ──
```

Key dependencies:
- **M1 comparison** needs **9(a) unique-synthetic-id AND 6-prompt SKU-atomic together** — 9(a) is the load-bearing fix (constant `DEFAULT_STATS_SYNTHETIC_CHUNK_ID` at `_21_streaming_upload.py:118` collapses per-leg priced chunks in dedup at `retrieve.py:188-192`); 6-prompt ensures legs aren't shredded (`query_decomposer.py:63` "Be aggressive: over-split is safer" has no atomic guard). **Item 2 (rrf) is a complement on the vector-chunk layer, NOT a substitute, and is currently un-enableable** until `decompose_entity_quota` is threaded through both builders.
- **9(c) brand-aware per-leg** must follow 9(a) and carry its OWN measure — it can *regress* the same-brand-different-size case (G-097).
- Every **measure_first** item should ship **after #11** so lift is provable from a single SQL, not hand-graded structlog.

---

## 4. Next 5 fixes — the approvable sequence

**1. `#8 stats-delete scope` (do_now).**
Why first: highest-severity class (tenant isolation is sacred) and *zero behavioral risk* — `record_document_id=X AND record_bot_id=bot(X)` matches exactly the rows `record_document_id=X` matched before (a doc belongs to one bot; `record_bot_id` UUID NOT NULL FK, alembic 0118). It is the **only hot-path write with no scope** (`stats_index_repository.py:251-256`, plain `self._sf()`, no GUC) while all 5 reads scope `dsi.record_bot_id`. Ship the safest, most-severe fix first.

**2. `#11 request_logs verdict persist` (do_now, with the `'grounding' in rule_id` correction).**
Why second: it is the *observability foundation* every subsequent `measure_first` needs. `is_correct/quality_evaluator/refusal_reason` are NULL 302/302; the verdict already sits in `final_state` (read at `persist.py:277-278`) but no finalize caller passes it. Ship it **before** any measured correctness fix so lift on M1/M3 is reproducible from DB, not re-graded by hand. **Must include the derivation fix**: primary sync grounding signal is `rule_id='llm_grounding_fail'` (`local_guardrail.py:542`), which does NOT `.startswith('grounding')` → the design would persist grounding-flagged answers as `'clean'`, under-reporting the exact bucket the fix exists for.

**3. `#1 URL-ingest OOM cap` (do_now, with two corrections).**
Why third: independent, high-severity (a multi-GB remote body OOM-kills concurrent chats because embedded workers run in-process, `embedded_workers.py:20-22`). Root cause confirmed (`document_worker.py:446-448` buffers `.content` unbounded). **Two corrections are mandatory or the fix backfires**: (a) base the sentinel on `ValueError`, not `IngestError` — `IngestError ∈ _TRANSIENT_INGEST_ERRORS` (`document_worker.py:109`) so it would be *re-raised* (`:760-761`) into ~5× XCLAIM redelivery each re-fetching the oversize body, leaving idempotency stuck at 'processing' (`:739`); (b) ceiling = `DEFAULT_UPLOAD_STREAM_MAX_BYTES` (500 MiB), not 50 MiB — XLSX/CSV/Sheets have no parser cap, so 50 MiB would newly hard-fail large docs that ingest today.

**4. `#9a + #6-prompt` comparison cluster (measure_first).**
Why fourth: largest XE correctness cluster (M1, G-097/G-098). Ship the two load-bearing edits as a *measured pair* — unique per-leg synthetic id + SKU-atomic decompose prompt. **Explicitly defer 9(c) brand-aware and 6-routing short-circuit** (both RISKY, see Section 5). Gate on `loadtest_graded.py chinh-sach-xe`: comparison ≥9/10 from 0/4, HALLU=0, no single-lookup regression, XE stays ≥95.9%.

**5. `#4 CRAG mixed-branch rescue` (measure_first).**
Why fifth: fixes the SPA coverage cluster (M3, S-039/046/075). Root cause confirmed: `grade.py:296-297/385-386` append only RELEVANT/AMBIGUOUS so an 'irrelevant' verdict drops the chunk regardless of rerank score, and the `all_irrelevant` rescue (`elif` at `:462-507`) is unreachable when `ambiguous>0`. Ship after #11 so the coverage lift is DB-provable, and mandatory-measure because the top-1 was positively graded 'no' (larger HALLU surface than the existing rescue) — `grounding_check` backstop stays intact.

---

## 5. FLAWED / RISKY designs — do this instead

| Item | Verdict | Do NOT ship as-designed | Corrected action |
|---|---|---|---|
| **#2 rrf_round_robin** | **FLAWED** | `decompose_entity_quota` is not threaded through *either* `_build_pipeline_config` (`chat_worker/pipeline_config.py`, `test_chat/_pipeline_config.py`) → `_pcfg` returns 0 forever → wiring is permanent dead no-op AND the MEASURE step "set quota=2" is silently dropped (the exact recurring bug at `pipeline_config.py:175-180`). Gate `decompose_active` also fires for `INTENT_MULTI_HOP` where leg-index-as-entity is invalid. | Thread the key through **both** builders first; **restrict to `INTENT_COMPARISON`**; move `_decompose_leg` tag-pop to the shared path after `:1449`; confirm lexical is Null on the measure bot (else `:1824` re-truncation confounds). Treat as **complement** to 9(a), not the primary M1 fix. |
| **#3 mmr_floor_drift** | **RISKY** | Bundles a *live prod value-flip* (mmr 0.88→0.98, never written to DB per grep=0 non-archive migrations) with the pure-safe cliff parity, and `downgrade()` **deletes the pre-existing prod `mmr_similarity_threshold_by_intent` row** → Bug#10 aggregation-collapse on any rollback. Factual error: the flat-key runtime fallback is HARDCODED 0.88 at `pipeline_config.py:465` / `_pipeline_config.py:828`, not the const. | **Split**: ship cliff const `0.05→0.2` + seed clone mmr at prod-actual **0.88** (true parity, `do_now`, pure-safe). Treat mmr→0.98 as a *separate measured decision*; downgrade must not touch the pre-existing by_intent row. Fix the two hardcoded-0.88 (zero-hardcode violation) in that separate change. |
| **#6 decomposer_atomic** | **RISKY** | The **routing short-circuit (fix #2)** is LATENT with no repro — with `L1_ENABLED=True` (default), `_understand_query_route` sends comparison to `query_complexity`, so `_router_route`'s comparison branch is rarely reached; the unit test proves nothing end-to-end. Test-bug: asserting "over-split absent" fails closed on i18n EN's legit `"do not over-split"` (`i18n.py:636`). | Ship the **prompt edits only** (atomic-identifier rule + i18n mirror). Scope the absence assertion to `DECOMPOSER_SYSTEM_PROMPT` and target `"Be aggressive"`. **Split the routing short-circuit out** — needs its own reproducing case first. |
| **#9 comparison_L3** | **RISKY** | Ships **(c) brand-aware per-leg unconditionally** on the xe bot (`stats_brand_aware=True`). If decompose doesn't copy the brand token into each leg (G-097 states "LANDSPIDER" once, at the tail), the per-leg filter guard `if 0 < len(_keep) < len(entities)` (`query_graph.py:2597`) no-ops → the size-1 leg keeps BOTH brands (LANDSPIDER + Rovelo both in DB) = wrong-brand/HALLU risk on the very case it targets. The `≥9/10` gate + a (b)-only ablation can mask this G-097 regression. | Ship **(a) unique-synthetic-id alone** (strictly safer than a+c for case B). Keep (b) inert (flag default False). **(c) needs its own measure** isolating the same-brand-different-size case; do not fold it into the aggregate gate. |
| **#10 async_grounding** | **RISKY** | "Answer-neutral" rests on the *mutable, owner-flippable* fact "0/6 bots block-mode" — nothing couples `grounding_confirmed_action=block` (self-service `plan_limits`) to the global 8s timeout. The instant any owner flips block-mode, the 8s ceiling silently fail-opens (`local_guardrail.py:527`, returns None → answer ships) on ~33% of that bot's grounding-eligible requests = HALLU-relevant regression on a HALLU=0-sacred platform. Also raises fail-open rate ~26%→~33% by timing out the 7% 8-29s mid-tail. | Ship the per-bot `grounding_check_timeout_s` override **WITH** the constant (not "YAGNI"), OR enforce a coupled invariant "block-mode ⇒ larger timeout". Make "re-confirm 0/6 bots block-mode" a **hard pre-ship gate**, not a note. This is T2-perf — sequence after all T1 correctness. |
| **#5 config_parity** | SOUND (2 defects) | Design says "11 drift keys" — really **9 unique** (2 both-missing double-counted per-check). Glob only covers `nodes/*.py`, so a `_pcfg` site in any other orchestration module (incl. the builder itself) stays invisible — "any future knob fails CI" is overstated. | Ship Part A (pure-safe test+comment). Use a **9-key** `_KNOWN_PCFG_DRIFT`. Part B (builder reconciliation of the 9 live mirage-knobs — `adaptive_context_*`, `heuristic_intent_*`, `guard_output_parallel_enabled`, `rerank_max_chunks_to_llm`, etc.) is a *separate* measured change; ship A as fixing the **detector**, not the drift. |
| **#7 health** | SOUND (coverage cap) | Fine to ship, but do not close the finding as fully resolved: the highest-impact worker `document_consumer` blocks on `asyncio.Event().wait()` (`embedded_workers.py:87`) while XREADGROUP runs in a separate `bus.subscribe` task the supervisor never awaits → inner-loop death leaves `.done()=False`, health still `ok`. | Ship (covers 4/5 workers). Mark **PARTIALLY resolved**; file follow-up "supervisor should await the subscribe loop". |
| **#11 request_logs** | SOUND (1-line bug) | `grounding_verdict` derivation via `rule_id.startswith('grounding')` misses `'llm_grounding_fail'` (`local_guardrail.py:542`) and mislabels non-grounding blocks (numeric_fidelity/brand_scope) as grounding. | 1-line fix: `'grounding' in rule_id`; rename field to `guard_verdict`. Otherwise SOUND, `do_now`. |

### Unaddressed backlog to escalate (no design exists)

- **GAP-A (M2, G-063/G-067 arrival, HIGH value):** the `NGÀY-VỀ` table chunk (`chunk_type=table '… 28-thg 11'`, contains the size) is a *separate* table chunk not linked to the price entity; `retrieve entity_count=1` returns only the PRICE entity. **No fix in this batch.** Grade-rescue (#4) will NOT help — the arrival chunk never enters `inp`. Needs a new retrieval/indexing design: booster-retrieve or attach the arrival table when `intent=arrival`. Sibling G-065/G-068 answer `28-thg 11` correctly → intermittent surfacing confirms retrievability.
- **GAP-B (M4, S-005 HALLU):** fabricated `0909.999.999` (0 corpus matches, `rerank top_score=0.148`, `grade_path=timeout_fallback`). Target is the already-planned **claim-fidelity Tier-1b non-numeric grounding gate** — confirm it is scheduled; it is the one genuine generate-layer HALLU keep.
- **Harness fix (coref-4):** chain multi-turn history in the s5_followup load-test flow (`history_msgs=0`, `conversation_id=NULL`). Not a bot fix — do it in the harness so future runs grade coref honestly.
- **Provider (5 innocom 5xx):** `InternalServerError` after retries, `completion_tokens=0` — escalate retry-budget / failover / provider SLA; not a code correctness item.