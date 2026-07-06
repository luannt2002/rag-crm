# PASS-2 DEEP RE-ANALYSIS — LUONG 3 (Observability · Cost · Grounding · Eval · Test-health)

- **Slug**: `pass2-L3-obs-eval-test`
- **Date**: 2026-07-03 · **HEAD during pass**: `6796cd9` "revert(ING-F1): restore pure-money fallback — owner decision"
- **Branch**: `fix-260623-ingest-expert`
- **Stance**: skeptical SECOND look. Every pass-1 claim was RE-READ at source and given a verdict: **CONFIRMED / REFUTED / REFINED / OVERCLAIMED**. rule#0: every claim carries `file:line` or runtime evidence; **FACT** vs **HYPOTHESIS** labelled. Read-only except this report. EVOLVE-not-REWRITE.
- **Independent test run** (this session, my own): `python -m pytest tests/unit/ -q --continue-on-collection-errors -p no:cacheprovider` → **`67 failed, 6439 passed, 32 skipped, 36 xfailed, 33 xpassed, 8 errors in 179.42s`** — **byte-identical** to pass-1's headline. The 8 collection errors + 25 canary reds + 6 REAL-bug tests all reproduced. Evidence: `scratchpad/full_unit_run.txt`.

---

## 0. Executive verdict (second pass)

Pass-1's LUONG-3 core claims are **accurate and hold up under re-reading** — I confirmed the two most consequential ones (grounding-gate inversion, stats-route grounding revert) with git diffs and code, and reproduced the full test failure set independently. The engine layer is genuinely well-built (ports/registries/null-objects, exactly-once bus design, tenant-scoped audit); the systemic failure is **last-mile wiring + one-locale/one-currency assumptions + rotted test escalation channels**. Two pass-1 claims are OVERCLAIMED and corrected here: (a) the web-eval report said "no CI workflow invokes eval_gate (unverified)" — FALSE, `.github/workflows/eval-gate.yml` + 4 sibling eval workflows exist; (b) pass-1 implied a warn grounding hit "can trigger a reflect-retry regenerate" — it cannot; the warn hit only disables a reflect *optimization*, it never causes a regenerate. One finding is REFINED into a sharper root-cause: the fallback-hop cost-mispricing traces to a **missing `fallback_pricing` field on `ModelRuntimeConfig`**, not merely a code oversight. The 6 "REAL bugs" are real, but only **E1 (stats grounding revert) is high-severity HALLU-sacred**; E2/E3 are low/medium (config-wiring + bounded cost), E4×3 are meta-guard debt. Net: pass-1 LUONG-3 is trustworthy; the fixes below are correctly-layered, mostly small-diff, and none violates a sacred rule.

---

## 1. Verdict table (pass-1 claim → pass-2 verdict)

| Pass-1 finding | Pass-2 verdict | Evidence (re-read this session) |
|---|---|---|
| F-2 / L2-11 Grounding gate inverted | **CONFIRMED** (secondary "reflect-retry" claim OVERCLAIMED) | `local_guardrail.py:539-552`, `:933`; `guard_output.py:499-515,534-542` vs `:359-381`; zero `hitl` consumers (grep) |
| E1 / L2-3 `stats_route_skip_grounding` dead knob + unconditional skip | **CONFIRMED — highest severity** | `guard_output.py:105-106`; `bot_limits.py:67-69`; git `062d6fa`→`3097755` diff |
| F-10 invocation_logger finally-INSERT unprotected | **CONFIRMED** | `invocation_logger.py:244-246` (no try/except) vs `:249-268,271-276` (wrapped) |
| F-1 bus recovery never re-dispatches | **CONFIRMED** | `redis_streams_bus.py:571-618` (returns `len(claimed)`, no dispatch), `:358` uuid consumer, `:508-510` only reads `">"`, no `xautoclaim` |
| F-9 webhook_dispatcher wrong Redis exc classes | **CONFIRMED (runtime-verified)** | `webhook_dispatcher.py:331,354`; redis 7.4.0 MRO probe: `ConnectionError` ⊄ builtin |
| L2-14 streaming cost / fallback pricing | **REFINED** (streaming cost now logged; fallback mispricing root-caused to missing DTO field) | `dynamic_litellm_router.py:1039-1077` (fixed), `_build_fallback_cfg:664-674` + `model_runtime.py:83-85` (no `fallback_pricing`) |
| E2 `cross_doc_reconcile_enabled` unwired + inline default | **CONFIRMED (LOW)** | `query_graph.py:2380` (inline `True`, zero other refs) |
| E3 per-intent max-tokens orphan | **CONFIRMED (T2, bounded)** | `_06_llm_defaults.py:22` (0 consumers), `generate.py:741-744` (no intent arg) |
| F-8 math_lockdown = cache-skip only, VND/vi-only | **CONFIRMED (sacred #10 safe)** | `persist.py:149-152` (only caller); `find_ungrounded_numbers` 0 prod callers |
| F-16 sla_metrics zero consumers | **REFINED** (0 *code* consumers; 1 doc coupling in YAML) | grep: only `scripts/sla_alerting_rules.yaml:16` comment |
| Web-eval "no CI eval workflow (unverified)" | **OVERCLAIMED → CORRECTED** | `.github/workflows/eval-gate.yml` + `eval-ragas.yml` + `per-bot-golden.yml` exist |
| Test-health: 8 errors + 67 fail + 6 REAL bugs | **CONFIRMED (independently reproduced)** | my run = `scratchpad/full_unit_run.txt` |
| RAGAS adapter is a stub | **CONFIRMED (stub returns 0.0)** | `ragas_metric_adapter.py:68,81-88`; `DEFAULT_RAGAS_STUB_SCORE=0.0` |

---

## 2. Case-study chains (CONFIRMED / REFINED findings)

### CS-1 [CRITICAL · HALLU-sacred] Stats-route grounding gate reverted to unconditional skip — dead per-bot knob + false comment

**Concrete problem (repro path).** A bot on the stats/aggregation route (`retrieve_mode` starts with `"stats"`) answers a factoid whose value is NOT present in the matched structured entity (e.g. an entity lacking the queried field; the LLM fills it from conversation history). Because the grounding judge is skipped for the stats route **unconditionally**, the ungrounded number ships. Owner sets `plan_limits.stats_route_skip_grounding=false` expecting grounding to run — **nothing changes** (dead knob).

**Direct cause.** `guard_output.py:105-106`:
```python
if str(state.get("retrieve_mode") or "").startswith("stats"):
    _grounding_eligible = False
```
No `_pcfg(state, "stats_route_skip_grounding", ...)` guard. The comment at `:104` still reads "Per-bot overridable." — false.

**Root-cause chain (immutable).**
`ungrounded stats number ships` ← `_grounding_eligible=False for every stats turn` ← `commit 3097755 removed the _pcfg gate that 062d6fa added` ← `062d6fa itself documents a REAL prior HALLU breach` (git message, verbatim: *"a stock query matched an entity that LACKED the stock field; the LLM answered '26' … leaked from history … passed unchecked = HALLU breach"*). L1 ← L2 ← L3 all evidenced by `git show 3097755 -- .../guard_output.py` (removes the `and bool(_pcfg(...))` clause) and `git show -s 062d6fa`.
- `DEFAULT_STATS_ROUTE_SKIP_GROUNDING=False` (`_15_m2_neighbor_window_expansion.py:124`) is now consumed **only** by the `bot_limits.py:67-69` schema, not by any decision point (grep confirms).

**Expert solution (correct layer = the grounding decision node, NOT sysprompt).**
- *Short*: restore the one-line gate `if stats-route AND _pcfg(state,"stats_route_skip_grounding",DEFAULT...): _grounding_eligible=False`. Re-greens the pin test `test_guard_output_intent_gating.py:267`. This is exactly the `062d6fa` shape.
- *Mid*: SOTA "Sufficient Context" (Google, ICLR 2025, arXiv:2411.06037) — the stats route already HAS a deterministic sufficiency signal (did the structured index return the queried field for the matched entity?). Gate grounding-skip on *field-presence* rather than route-type: skip only when the answer's numbers are a verbatim relay of returned index cells (deterministic, <5ms), else run the judge. This kills the false-block motivation (`062d6fa`'s "còn 338 cái vs quantity:338") without reopening the breach.
- *Long*: per RefusalBench/UAEval4RAG, add stats-route HALLU traps (query a field the entity lacks) to the per-bot golden set so this regression class trips CI, not production.

**Self-critique — expert or patch?** The short fix is a legitimate revert (restores an intentionally-shipped HALLU-safety gate), not a symptom patch — it puts the decision back at the correct layer (grounding-eligibility) with the owner knob honored. The mid fix is the true expert move (deterministic sufficiency > route-type heuristic). Sacred #10 safe: grounding here refuses-with-`oos_answer_template` (existing contract), never rewrites the answer.

**Trade-offs.** Restoring grounding on stats adds one LLM judge call on stats turns (latency + cost) — mitigated by the mid-fix deterministic pre-check. False-block risk on legitimately-reformatted numbers returns unless the mid-fix lands.

**Impact.** Correctness/HALLU: **high** (reopens a documented breach class). Cost: +1 judge call/stats-turn if short-only. Blast radius: every bot using the stats/aggregation route (the priced/count answers owner cares most about — banking/legal corpora).

---

### CS-2 [HIGH · HALLU] Grounding gate is inverted: judge-says-ungrounded SHIPS, judge-unavailable REFUSES

**Concrete problem.** Bot emits a fabricated answer; the LLM grounding judge runs and returns 4/5 NOT_SUPPORTED (ratio 0.8 > threshold 0.3). The user **still receives the fabricated answer**; only `grounding_fail_total` ticks and a `warn` flag is persisted. Conversely, when the judge cannot run (LLM unwired), the answer is **refused** (fail-closed → `oos_answer_template`).

**Direct cause.** `local_guardrail.py:539-552`: a measured breach returns `GuardrailHit(severity="warn", action="hitl")`. `check_output` (`:933`) raises `GuardrailBlocked` only for `severity=="block"`. In `guard_output.py` both the parallel (`:499-515`) and serial (`:534-542`) paths only append the warn hit to `flags` and `return` — the answer field is untouched. Compare the fail-closed branch (`:359-381`) which DOES replace `answer` with `_oos_template`.

**Root-cause chain.** `confirmed-ungrounded answer reaches user` ← `warn/hitl hit has no blocking effect` ← `action="hitl" has ZERO consumers` (grep across `src/`: only definitions/docstrings; no HITL queue exists) ← `by design the judge is observability-only, but the fail-closed branch is worded "honouring HALLU=0 sacred" (guard_output.py:225), overstating what the net does`. Immutable cause: the grounding subsystem measures but has no wired *action* on a positive detection; the only action is on *non-detection*.

**Correction vs pass-1 (OVERCLAIMED).** Pass-1 said a warn hit "can only trigger a reflect-retry regenerate." RE-READ `reflect.py:139-176`: the retry decision is driven by reflect's OWN separate LLM verdict (`should_retry = "rewrite" in verdict`, `:137`). The `llm_grounding_fail` flag is read only *inversely* (`:154-158`): its presence DISABLES the `reflect_skip_if_grounded` optimization (default OFF). A grounding warn hit therefore never *causes* a regenerate. F-2's core verdict stands; this sub-claim is corrected.

**Expert solution (correct layer = grounding-eligibility policy + a per-bot escalation, NOT sysprompt).**
- *Short*: make the failure-mode symmetric and owner-visible — either (a) rename/document the judge as observability-only and drop the "HALLU=0 sacred" wording from the fail-closed comment, or (b) add a per-bot opt-in `grounding_confirmed_fail_mode="refuse"` that routes a *confirmed* breach (ratio>threshold, checked≥N) through the same `oos_answer_template` refuse contract the fail-closed branch already uses. Owner decision required (this is a policy, not a bug per se).
- *Mid*: SOTA middle tier (web-eval R1) — the warn/ship decision is fragile because the judge is a single LLM (kappa-deflation 33-41pp, arXiv:2606.19544; cross-lingual degradation on Vietnamese, BabelJudge arXiv:2606.22329). Replace the free-floating single judge with a deterministic NLI verifier (Bespoke-MiniCheck-7B / HHEM-2.1) **on the eval side first** (offline), validated on 50 Vietnamese hand-labels before any runtime use.
- *Long*: HALT-RAG-style calibrated abstention (arXiv:2509.07475) as a per-bot opt-in surfaced via ADR — never a blanket runtime override (sacred #10).

**Self-critique.** (a) is the honest minimum (the code lies about what it does); (b) is the expert move but MUST be per-bot opt-in and route through the existing refuse text (`oos_answer_template`), never inject application text. Correct layer.

**Trade-offs.** Escalating confirmed-ungrounded to refuse raises False-Refusal-Rate (V15-1 "refuse oan" risk) — which is why it must be per-bot and measured with FRR/MRR (web-eval R3). Doing nothing keeps HALLU exposure on the vector path for any bot whose sysprompt anti-fabricate rules are weak.

**Impact.** Correctness/HALLU: high (the net that looks like a HALLU guard is a no-op on positive detection). Cost/Perf: neutral (judge already runs). Blast radius: every grounding-eligible intent on the vector path.

---

### CS-3 [HIGH · reliability/multi-doc] Redis-Streams recovery XCLAIMs but never re-dispatches → zero retries, straight to DLQ

**Concrete problem (repro).** A `document.uploaded` handler hits a transient embed-API 429 → raises → no XACK → message idles. ~60s later `recover_pending_messages` XCLAIMs it to the current consumer and **returns without dispatching**. Every subsequent recovery pass re-claims the same entry (its `times_delivered` climbing) until it crosses `DEFAULT_BUS_DLQ_MAX_DELIVERIES=5` → dead-lettered to `{stream}:dlq`. The document is stuck DRAFT, **never once reprocessed**. The at-least-once contract is effectively at-most-once-then-DLQ.

**Direct cause.** `redis_streams_bus.py:607-615`: `claimed = await self._redis.xclaim(...); … return len(claimed)`. The `claimed` payloads are discarded. The consumer loop reads only new messages (`:508-510`, `{key: ">"}`). No `{key: "0"}` PEL re-read and no `xautoclaim` anywhere (grep: 0 hits).

**Root-cause chain.** `stuck DRAFT doc` ← `claimed message never handled` ← `recover_pending_messages has no dispatch path` ← `per-process uuid consumer name (`:358` `f"{group}:{uuid4().hex[:8]}"`) orphans the old PEL on every restart, so the reclaim loop is the ONLY path back — and it's dead-ended`. Immutable cause: recovery was implemented as claim-only, not claim-then-dispatch. The comments at `document_worker.py:756,777` / `chat_worker/pipeline.py:761` describe retry behavior that does not exist.

**Expert solution (correct layer = the bus recovery method).**
- *Short*: after XCLAIM, feed `claimed` through the same `_dispatch_one(msg_id, data)` path the main loop uses (the payloads are already in hand).
- *Mid (SOTA)*: switch to `XAUTOCLAIM` (Redis ≥6.2) with a periodic PEL cursor + a bounded re-dispatch — the idiomatic reliable-consumer pattern; it returns the claimed entries for processing and advances a cursor, eliminating the re-claim-forever loop.
- *Long*: reserve DLQ for true poison (deterministic handler bug), not transient upstream faults — add an exponential re-delivery backoff before the delivery-count DLQ threshold so a 429 storm doesn't dead-letter a whole ingest batch.

**Self-critique.** Short fix is correct-layer and minimal (dispatch the already-claimed payloads). XAUTOCLAIM is the real expert answer (removes the delivered-count inflation bug). Not a patch.

**Trade-offs.** Re-dispatching claimed entries needs idempotency at the handler (already present via content-hash for ingest; chat is naturally idempotent per message_id). XAUTOCLAIM raises the min Redis version floor.

**Impact.** Correctness/reliability: high for multi-document ingest under transient failure. Cost: wasted DLQ-replay ops. Blast radius: every stream subscriber (ingest, ai_config, chat).

---

### CS-4 [MEDIUM · observability/cost] `InvocationLogger` finally-block INSERT is unprotected → a DB blip fails an already-successful LLM turn

**Concrete problem.** During a traffic spike the pool is momentarily exhausted; the LLM answer has already been produced (`yield ctx` returned). In `finally:`, the audit INSERT+commit raises `TooManyConnections` → the exception propagates out of the `invoke_model` context manager → the caller sees a 5xx on a turn whose answer had already arrived.

**Direct cause.** `invocation_logger.py:244-246`:
```python
async with self._sf() as session:
    await session.execute(stmt)
    await session.commit()
```
No try/except — unlike the Prometheus emit (`:249-268`, wrapped) and span close (`:271-276`, wrapped) immediately below. This contradicts the module's own contract "observability MUST never break the LLM call" (`:158`).

**Root-cause chain.** `answer loss on successful LLM call` ← `unhandled exception in finally` ← `the aux audit sink is treated as fail-loud in the money path` ← `graceful-degradation rule (aux sink must not kill main path) not applied to this one write`. Secondary: `_span_cm.__exit__` (`:276`) is only reached after the INSERT, so the span leaks on the failure path.

**Expert solution (correct layer = the aux sink).**
- *Short*: wrap the INSERT/commit in `try/except (SQLAlchemyError, OSError, asyncio.TimeoutError): logger.warning("invocation_persist_failed", exc_info=True)` and put `_span_cm.__exit__` in a `finally` so the span always closes. Aux sink → degrade silent (claude-mem graceful-degradation).
- *Mid*: route the audit row through the existing `AsyncDBTokenLedger` bounded-queue + drop-count pattern (`token_ledger/async_db_token_ledger.py`) so audit writes are decoupled from the request path entirely (the ledger already implements queue-full drop-and-count).

**Self-critique.** Short fix is correct-layer and directly restores the stated invariant. Mid fix is the true expert decoupling (the ledger is the SSoT pattern already in-repo). Not a patch.

**Trade-offs.** Degrading-silent on the audit write means a transient DB blip loses an audit row (acceptable per the module's own stated priority — the LLM call must not break). The mid fix trades a small delivery-latency for full decoupling.

**Impact.** Correctness: prevents answer loss during the exact spike that stresses the pool (self-amplifying). Cost: negligible. Blast radius: every LLM/embed/rerank invocation (the audit chain is INVARIANT #2).

---

### CS-5 [MEDIUM · notify/reliability] webhook_dispatcher catches builtin exceptions but redis-py raises its own hierarchy → "never raises" + "fail open" both broken

**Concrete problem.** Redis goes down (the incident alerts exist for). `_is_duplicate` / `_is_rate_limited` call `self._redis.set/incr`, which raise `redis.exceptions.ConnectionError`. The `except (OSError, ConnectionError, TimeoutError)` does NOT catch it → `dispatch()` raises despite its "never raises" docstring → the fire-and-forget `asyncio.create_task(dispatcher.dispatch(...))` caller (`error_notify_hook.py:71`) drops the alert with only an unhandled-task traceback. The documented "fail open (allow the alert through)" becomes fail-closed-silent.

**Direct cause + runtime evidence.** `webhook_dispatcher.py:331,354`. Verified in this venv (redis 7.4.0):
```
redis.exceptions.ConnectionError MRO: ConnectionError → RedisError → Exception
issubclass(redis.exceptions.ConnectionError, builtins.ConnectionError) == False
issubclass(redis.exceptions.ConnectionError, OSError) == False
```

**Root-cause chain.** `alert dropped when Redis dies` ← `redis exception escapes the except clause` ← `redis-py exceptions inherit only from RedisError, not the builtins` ← `the dispatcher copied the builtin-exception idiom instead of importing redis.exceptions.RedisError` (contrast `redis_streams_bus.py` which imports `RedisError` correctly, and `webhook_notifier.py:76` which uses broad-except for the same pattern).

**Expert solution.** *Short*: `from redis.exceptions import RedisError` and catch `(RedisError, OSError, asyncio.TimeoutError)` in both methods (mirror the bus). *Mid*: add a unit test that injects `redis.exceptions.ConnectionError` and asserts `dispatch()` returns a status dict (never raises) + logs `notify_*_check_failed` — pin the contract.

**Self-critique.** Correct-layer, minimal, mirrors the existing correct call sites. Not a patch.

**Trade-offs.** None material; broadening to `RedisError` is strictly more correct.

**Impact.** Correctness/reliability: alerts (incl. quota-exhaustion + error alerts) stop exactly when they matter most. Blast radius: all Redis-backed notify dedup/rate-limit.

---

### CS-6 [MEDIUM · cost-metering] Fallback LLM hop logs cost at the PRIMARY model's pricing — root cause is a missing DTO field

**Concrete problem.** Primary model trips its circuit-breaker → `complete_runtime` fails over to the fallback model (`:627-641`). The answer is produced by the fallback, and `invocation_logger` records `model_id=fallback_wire_model_id` (correct) — but `cost_usd` is computed from `cfg.pricing` which is still the PRIMARY model's rate card. If the fallback has different rates, every failover turn is mis-costed.

**Direct cause + root-cause (REFINED beyond pass-1).** `_build_fallback_cfg` (`dynamic_litellm_router.py:664-674`) does `replace(cfg, provider=..., wire_model_id=..., litellm_name=...)` — it swaps provider/model but **cannot** swap pricing because `ModelRuntimeConfig` (`application/dto/model_runtime.py:61-85`) has `fallback_model_row_id`, `fallback_wire_model_id`, `fallback_provider` but **NO `fallback_pricing`** field. `_complete_runtime_one` then computes `compute_cost_usd(cfg.pricing, ...)` (`:814`) at primary rates. Immutable cause: the resolver never loads/threads the fallback model's pricing row.

**REFUTED sub-claim (streaming logs $0).** Pass-1/synthesis implied streaming cost is unmeasured. RE-READ `dynamic_litellm_router.py:1039-1077` (OBS-F6): the stream path now (a) `estimate_tokens_fallback` fills zero totals from tiktoken, (b) `compute_cost_usd(cfg.pricing, ...)`, (c) forwards to `usage_sink` so `invocation_logger` records non-zero. **Streaming cost is now logged.** BUT streaming still has **no fallback wrapper** (the generator has no `_FAILOVER_TRIGGERS` catch), so a streamed generation that would benefit from failover just fails — FACT, and a separate gap from cost.

**Expert solution (correct layer = resolver DTO + router).**
- *Short*: add `fallback_pricing: Pricing | None` to `ModelRuntimeConfig`; resolver loads the fallback model's pricing row alongside `fallback_wire_model_id`; `_build_fallback_cfg` sets `pricing=cfg.fallback_pricing or cfg.pricing`.
- *Mid*: give `complete_runtime_stream` the same single-hop failover wrapper as `complete_runtime` (extract the failover envelope into a shared helper so streaming + non-streaming share it).

**Self-critique.** Correct-layer (the pricing must originate at the resolver, not be guessed in the router). Threading a field is the minimal honest fix; the mid fix closes the streaming-failover gap that pass-1 flagged.

**Trade-offs.** One extra pricing-row load per binding with a fallback configured (cached with the runtime config). Streaming failover adds retry complexity to a generator (partial-output-already-sent constraint) — must only fail over on stream-OPEN error, not mid-stream.

**Impact.** Cost accuracy on the failover path (only when fallback rates ≠ primary). Blast radius: bots with `record_fallback_model_id` set. Severity MEDIUM (accounting, not correctness/HALLU).

---

### CS-7 [MEDIUM · cost/UX] Per-intent output-token cap is dead — `_intent_max_tokens` has no intent dimension

**Concrete problem.** A `greeting`/`chitchat`/`vu_vo` turn is no longer capped at 60–80 tokens; it gets the full per-response cap. Cheap intents cost more than designed; the variable name lies.

**Direct cause.** `generate.py:741-744` — `_intent_max_tokens = compute_output_cap(system_output_default=..., bot_extra_output=...)`; `compute_output_cap` (`shared/token_budget.py`) takes **no intent argument**. `DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT` (`_06_llm_defaults.py:22`, a 10-entry dict `greeting:60 … multi_hop:400`) has **zero consumers** in `src/` (grep: only its definition + `__all__` re-export). Removed by `24f2451` (per pass-1 `git log -S`).

**Root-cause chain.** `cheap intents uncapped` ← `intent dimension dropped from the output-cap computation` ← `24f2451 large integration commit deleted the per-intent branch, kept the misleading variable name + orphan constant`. Test pin `test_generate_intent_max_tokens.py:91` catches it (in my FAILED set).

**Expert solution.** *Short*: either restore an intent-aware cap `min(compute_output_cap(...), DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT.get(intent, ∞))` or delete the orphan constant + rename the variable to `_max_output_tokens` and re-pin. Owner decision on whether the per-intent cap is wanted (it was a deliberate T2 cost feature). Correct layer = generate node.

**Self-critique.** Bounded blast radius (LLMs usually emit short greetings anyway), so this is a *soft* cost regression — real but not urgent. The honest move is decide-and-repin, not silently keep a lying name.

**Impact.** Cost (T2): small per-turn on cheap intents; Correctness: none. Blast radius: all bots, cheap intents.

---

### CS-8 [MEDIUM · multi-locale/HALLU-adjacent] Numeric cache-skip is VND/Vietnamese-only → non-VND bots re-import stale-number HALLU risk

**Concrete problem.** A USD/EUR bot answers "$29.99". `persist.py:152` calls `extract_numeric_claims(answer)` to decide whether to SKIP semantic-caching (the skip exists to prevent the cosine cache returning a near-duplicate query with a *different* number). `extract_numeric_claims` (`math_lockdown.py:35-76`) recognizes only VND units + Vietnamese duration words → `$29.99` yields no claim → the numeric answer IS cached → a later similar query can hit the stale number.

**Direct cause + root-cause.** `math_lockdown.py` regexes are VND/vi-baked. Immutable cause: money/duration recognition is hardcoded to one currency+locale instead of config-sourced (violates the metadata-hint / currency-neutral mindset). Confirmed sacred #10 SAFE: `math_lockdown` is used ONLY for the cache-skip *decision* (`persist.py:149-152` is its sole production caller; `find_ungrounded_numbers` has 0 prod callers) — never to override an answer.

**Expert solution.** *Short*: make the numeric-claim detector currency/scale-neutral — detect any digit-group with a decimal/thousands separator + optional currency token (config `system_config.currency_symbols`), not just VND. *Mid*: thread the bot/doc locale (already resolved upstream) into the detector; per the `metadata-optional-hint` + `multilingual-no-vocab` repo skills.

**Self-critique.** Correct layer (detector config, not per-bot code). Domain-neutral compliant.

**Impact.** Correctness/HALLU on non-VND bots: medium (stale-number cache hit). Blast radius: any bot whose numbers aren't VND — the owner's #1 "only happy-case" concern shape.

---

### CS-9 [LOW+ · observability] `sla_metrics.py` has zero code consumers (REFINED)

**Verdict REFINED.** Pass-1 F-16 "zero consumers" is correct for *code* paths (grep: no import of `sla_metrics`/`classify_latency`/`SLAStatus`/`thresholds_from_config` outside the module + its unit test). The one reference is a **comment** in `scripts/sla_alerting_rules.yaml:16` naming `sla_metrics.sla_threshold_snapshot()` as SSoT — a documented-but-unenforced coupling, so YAML/constants can drift. Built-not-wired. *Fix*: either wire a `/health/sla` route (the module's promised consumer) or delete the module and hand-maintain the YAML honestly.

---

## 3. Test-health — independent re-run + REAL-bug judgment

**FACT (my run).** `67 failed, 6439 passed, 32 skipped, 36 xfailed, 33 xpassed, 8 errors in 179.42s` — identical to pass-1. The 8 collection errors (5 × RC1 `24f2451` re-export deletion, 3 × RC2 FastAPI-private-import) and the 25 seeded canary reds all reproduced. Deterministic.

**Independent judgment of the 6 "REAL bugs":**

| # | Claim | My verdict | Severity (mine) |
|---|---|---|---|
| E1 | stats grounding revert (`3097755`) | **REAL** — git-confirmed reopen of a documented HALLU breach; dead knob + false comment | **CRITICAL/HALLU** (CS-1) |
| E2 | `cross_doc_reconcile_enabled` unwired + inline `True` | **REAL but LOW** — feature still runs (default True); it's a config-reachability + zero-hardcode gap, not a functional break | LOW |
| E3 | per-intent max-tokens orphan | **REAL** — bounded T2 cost regression, misleading name | MEDIUM (CS-7) |
| E4a | price-domain-coupling 133>127 | **REAL guard regression** — engine deepening `price_*` first-class coupling; same root as canary | MEDIUM (debt) |
| E4b | broad-except 250>249 | **REAL** — one noqa-annotated site (`llm_usage.py` tiktoken fallback); ceiling not bumped per policy | LOW (debt) |
| E4c | version-ref 9>7 | **REAL** — all in `tests/`; incl. `ragbot_v2_dev:document.uploaded.v1` literal (brushes tenant-literal rule) | LOW (debt) |

**Conclusion:** the "6 REAL bugs" label is accurate, but severity is concentrated — **only E1 is HALLU-critical**; the rest are one bounded cost regression (E3) plus config/guard debt. Pass-1 did NOT overclaim severity (it ranked E1 highest).

**Test escalation-channel rot (CONFIRMED, agrees with pass-1 quality audit):** front-door `pytest tests/unit -q` aborts on 8 collection errors → CI-style "run the suite" is broken; 25 canary reds are committed UNMARKED (should be `xfail(strict=True)`) so red is the steady state; 31/66 `_xfail_list.txt` entries are deterministically XPASS (8-week-stale) — resilience contracts (CB fallback, Redis fail-open, webhook retry) demoted to non-alerting `xfail(strict=False)`. Repair order: fix the 8 imports (mechanical) → mark canary reds `xfail(strict=True)` → prune 31 stale xfail lines.

**NEW correction (pass-1 web-eval OVERCLAIM):** the web-eval report's R7 "wire the gates into CI" and its HYPOTHESIS "no `.github/workflows` reference to eval_gate" are both **stale/wrong** — `.github/workflows/eval-gate.yml` (scorer-lock always + live-gate conditional on `RAGBOT_EVAL_BASE_URL`), `eval-ragas.yml`, `per-bot-golden.yml`, `cross-tenant-rls.yml`, `audit-agent-diff.yml` all exist. The gate SHAPE + CI wiring are done; the *enforcement* is conditional on a configured staging target (live-gate `exit 0` when unset). Remaining real gap: no seeded staging target means HALLU=0/coverage isn't enforced on every PR — an ops-config task, not a missing feature.

---

## 4. EXPERT DELIVERABLE — Agent-Grader / RAGAS-parallel harness + 6-type ground-truth schema

Designed to fit what already exists (`scripts/eval_gate.py` deterministic scorer, `eval_per_bot_golden.py` per-bot regression, stubbed `RagasMetricAdapter` Port seam, CI workflows) and to honor sacred rules (#10 observe-only, domain-neutral, zero-hardcode, `record_bot_id`-keyed files, `feedback_ragas_parallel` asyncio.gather+semaphore).

### 4.1 Ground-truth schema (extends the existing `{question, expected_answer, expected_intent, must_cite}`)

Per-bot JSONL keyed by `record_bot_id` (existing convention `eval_per_bot_golden.py:9-13`). New fields — **`question_type` is the 6-way taxonomy from program spec §8.2**:

```jsonc
{
  "id": "gt-042",
  "question": "…",                         // verbatim user question (bot's locale)
  "question_type": "table_related",         // 6 types below
  "expected_answer": "…",                   // canonical answer text (for substring + claim grading)
  "expected_answer_claims": ["…","…"],      // atomic claims (RAGChecker-style; optional, enables recall grading)
  "expected_source_chunk_ids": ["ch_..."],  // ground-truth supporting chunks (enables context-recall)
  "expected_intent": "factoid",             // reuse existing gate
  "must_cite": true,                        // reuse existing gate
  "answerable": true,                       // false = TRAP (must refuse) → drives MRR
  "trap_class": null                        // when answerable=false: false_premise|missing_info|ambiguity|contradiction|granularity|epistemic (RefusalBench)
}
```

**6 question_types** (program spec §8.2, domain-neutral names):
1. `single_fact` — one atomic fact, single chunk.
2. `heading_dependent` — answer requires the section/heading context (disambiguation).
3. `table_related` — answer lives in a table cell / row.
4. `formula_related` — answer requires a formula/computed value.
5. `multi_passage_synthesis` — answer spans ≥2 chunks (aggregation/summary).
6. `cross_reference` — answer requires following a reference between docs/sections.

### 4.2 Harness architecture (parallel, deterministic-first, judge-audited-second)

```
ground_truth[bot].jsonl
   │  asyncio.gather + Semaphore(N=8-10)   ← feedback_ragas_parallel (NOT sequential)
   ▼
[Answering pipeline]  POST /api/ragbot/chat (bypass_cache, loadtest token)
   │  capture: answer, citations, retrieved_chunk_ids+scores (see gap below), intent, latency, cost
   ▼
[Deterministic scorer]  (eval_gate.py logic — no LLM)
   ├─ answerable=false → PASS iff _is_refusal(answer)          → MRR
   ├─ answerable=true  → coverage: expected_answer ⊆ answer     → 1−FRR component
   ├─ must_cite        → ≥1 citation present
   ├─ context_recall   → expected_source_chunk_ids ⊆ retrieved_chunk_ids   (deterministic)
   └─ citation_validity→ cited_ids ⊆ retrieved_ids               (fabricated-source detector)
   ▼
[Agent-Grader]  (SEPARATE agent, NOT the answering model)  ← only for claim-level nuance
   │  per turn → JSON: {faithfulness, context_precision, context_recall,
   │                    answer_relevance, strategy_selection_accuracy}
   │  advantage over single LLM-judge: re-reads expected_source_chunk_ids,
   │  compares against retrieved, not just free-text (synthesis §4.3)
   ▼
[Auditor batch]  per question_type: %faithfulness-low, p50/p95 latency, cost/turn,
                  coverage_fail_retrieval vs coverage_fail_generation (Sufficient-Context split),
                  FRR (answerable refused) + MRR (trap answered = HALLU breach, must=0)
```

### 4.3 Metrics reported every run (SOTA-aligned)

- **HALLU / MRR** = traps answered / total traps → sacred **must be 0** (RefusalBench MRR).
- **FRR** = answerable-questions refused / total answerable (RefusalBench False-Refusal — the "V15-1 refuse oan" number, currently unmeasured).
- **Coverage** = correct-when-answerable / total-answerable, **split by Sufficient-Context** into `coverage_fail_retrieval` (expected chunks not retrieved) vs `coverage_fail_generation` (retrieved but wrong/refused) — automates the layer-attribution the CLAUDE.md 5-step protocol does by hand (kills the "3 alembic sai tầng" waste class).
- **Per-question_type breakdown** of all of the above → trace low-faithfulness types back to Luong-1 (chunking) or Luong-2 (retrieval).
- **Cost/turn, p50/p95** from `model_invocations` + `request_steps` (already logged).

### 4.4 Judge-reliability guardrails (mandatory before any Agent-Grader gates)

Per web-eval R4 (kappa-deflation 33-41pp arXiv:2606.19544; BabelJudge cross-lingual arXiv:2606.22329): validate the Agent-Grader on ~100 Vietnamese hand-labels, report **Cohen's kappa** (not raw agreement), swap-order to cancel position bias, prefer ≥2-judge ensemble for HALLU adjudication (FACTS Grounding uses 3). Deterministic scorer stays the **gate**; Agent-Grader is **diagnostic** until kappa-validated.

### 4.5 Prerequisite gaps that BLOCK this harness (must land first)

1. **Response must expose `retrieved_chunk_ids` + per-chunk scores.** Today `/chat` returns `citations` (`chat_routes.py:702`) but NOT the retrieved-chunk set with scores (synthesis §4.2 "THIẾU retrieved_chunk_ids"). Without it, `context_recall` and `citation_validity` grading is impossible. Add an observe-only `retrieved_chunk_ids`/`retrieval_scores` block to the response (or a debug-scoped field) — no answer change (sacred #10 safe).
2. **RAGAS adapter is a 0.0 stub** (`ragas_metric_adapter.py:68`, `DEFAULT_RAGAS_STUB_SCORE=0.0`). If claim-level faithfulness is wanted, replace behind the existing Port with real `ragas` Faithfulness + Context-Recall ONLY (web-eval R8), or a MiniCheck/HHEM NLI verifier validated on Vietnamese first (R1). Keep it **eval-side** (offline) — never runtime.
3. **Missing middle tier**: no deterministic NLI grounding verifier exists (grep `hhem|minicheck|lynx`=0). The Agent-Grader partially fills this offline; a classifier is the durable answer.

---

## 5. new_findings (pass-1 misses / corrections)

1. **[CORRECTION] CI eval workflows exist** — web-eval R7 + its "no workflow" HYPOTHESIS are stale. `.github/workflows/{eval-gate,eval-ragas,per-bot-golden,cross-tenant-rls,audit-agent-diff}.yml` present. `eval-gate.yml` runs the deterministic scorer-lock on every PR + a conditional live gate. Real remaining gap = no seeded staging target → live HALLU=0/coverage not enforced per-PR (ops-config, not code).
2. **[REFINED root cause] Fallback cost mispricing = missing `fallback_pricing` DTO field** (`model_runtime.py:83-85`), not a router oversight — the fix must originate at the resolver. Pass-1 stated the symptom; this is the immutable cause.
3. **[CORRECTION] Streaming cost IS now logged** (`dynamic_litellm_router.py:1039-1077`, OBS-F6) — refutes any residual "streaming logs $0". But streaming still has **no failover wrapper** (separate real gap).
4. **[CORRECTION] Grounding warn hit does NOT trigger reflect-retry** — `reflect.py:137` retry is driven by reflect's own LLM verdict; the `llm_grounding_fail` flag only disables an optimization (`:154-158`). Pass-1's F-2 secondary claim overclaimed the effect.
5. **[Sharper] `find_ungrounded_numbers` has zero production callers** (only self-reference in `math_lockdown.py` + a comment in `chat_routes.py:777`) — the numeric-grounding *check* function was built and never wired; only `extract_numeric_claims` (cache-skip) is live. Reinforces "built-not-wired" theme and confirms sacred #10 (no math override in production).
6. **[Sharper] `action="hitl"` is a phantom label platform-wide** — zero consumers in `src/` (only `GuardrailHit` definitions + a JSONB state emit). Any future reliance on a HITL queue is unbacked; the grounding warn/hitl path is pure telemetry.

---

## 6. Priority (LUONG-3 scope, second-pass)

| # | Sev | Finding | Anchor | Fix size |
|---|-----|---------|--------|----------|
| CS-1 | CRITICAL | Stats-route grounding revert reopens HALLU breach; dead knob | `guard_output.py:105-106` | S (restore `_pcfg` gate) |
| CS-2 | HIGH | Grounding gate inverted (measured-ungrounded ships) | `local_guardrail.py:539-552` | S+owner-decision |
| CS-3 | HIGH | Bus recovery never re-dispatches → DLQ w/o retry | `redis_streams_bus.py:607-615` | S (dispatch claimed) / M (XAUTOCLAIM) |
| CS-4 | MED | Invocation audit finally-INSERT unprotected → answer loss | `invocation_logger.py:244-246` | S |
| CS-5 | MED | webhook_dispatcher wrong Redis exc classes → alerts die | `webhook_dispatcher.py:331,354` | S |
| CS-6 | MED | Fallback hop mis-costs at primary pricing (missing DTO field) | `model_runtime.py:83-85` | M |
| CS-7 | MED | Per-intent output cap dead (cost regression) | `generate.py:741-744` | S+owner-decision |
| CS-8 | MED | Numeric cache-skip VND/vi-only → non-VND stale-number risk | `math_lockdown.py:35-76` | M |
| CS-9 | LOW+ | sla_metrics zero code consumers | `observability/sla_metrics.py` | S |
| — | — | Test escalation rot (8 errors + 25 unmarked canary + 31 stale xfail) | `full_unit_run.txt` | S (imports) + repin |

**Cross-cutting (agrees with pass-1 S1/S2/S3):** LUONG-3's failures are dominated by **last-mile wiring** (grounding action unwired, PII null, sanitizer/allowlist unwired, bus recovery dead-ended, HITL phantom) and **one-locale/one-currency assumptions** (numeric cache-skip, math_lockdown). The engine primitives are expert-grade; a one-page "wiring audit" (each registry has a bootstrap provider reading its documented `system_config` key + one un-mocked integration test) plus the eval harness in §4 would catch this whole class. All fixes are correct-layer and sacred-rule-safe.
