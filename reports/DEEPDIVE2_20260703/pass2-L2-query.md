# PASS-2 DEEP RE-ANALYSIS — LUỒNG 2 (QUERY PIPELINE)

- **Slug**: `pass2-L2-query`
- **Date**: 2026-07-03 · **Branch**: `fix-260623-ingest-expert` (HEAD `949a3a4`)
- **Role**: Staff/Principal RAG engineer, skeptical SECOND look. DO NOT trust pass-1.
- **Method**: for every flagged finding I RE-READ the cited `file:line`, re-ran a langgraph probe against the repo's own `GraphState`, ran the failing pin test, queried the live DB (`ragbot_v2_dev`), and traced git history (`git show`/`git blame`) to pin the immutable cause. Every claim carries evidence and a label **FACT** / **HYPOTHESIS**. Verdicts: **CONFIRMED / REFUTED / REFINED / OVERCLAIMED**.
- **Scope**: query flow — question → understand/intent → embed → retrieve (stats vs vector, hybrid BM25+vector RRF) → rerank → grade → generate → guard_output.
- **Stance**: EVOLVE not REWRITE. The skeleton (Hexagonal · Port/Registry/DI · 4-key · RLS design · compiled-graph-singleton-with-per-request-state) is expert-grade. Every defect below is an *unwired loop / dropped key / reverted fix / happy-case gap*, never "the architecture is wrong".

---

## 0. HEADLINE — two independent regressions from "phaseN integrate" merge commits, plus one immutable framework trap

Pass-2 confirms the single most important structural fact and adds a git-forensic root cause pass-1 did not pin:

1. **Framework trap (immutable, re-probed)**: `langgraph==1.2.4` with `StateGraph(GraphState)` **drops every key not declared in the `GraphState` TypedDict** — from the input dict, from node returns, and in-place `state[...]=x` never crosses a node boundary. Re-probed against the *repo's own* `GraphState` this session (evidence in §1). This is the root of ≥8 dead features.

2. **Regression forensics (NEW this pass)**: two of the CRITICAL findings are not "never wired" — they are **working fixes that a later merge clobbered**:
   - **Stats-route HALLU net**: commit `062d6fa` (Jun 25) added the per-bot `stats_route_skip_grounding` gate to close a verified HALLU breach ("stock number 26 leaked from history"). Commit **`3097755`** (Jun 27, "fix(phase1): integrate…") **reverted it** — `git blame guard_output.py:105` = `3097755`. The pin test `test_guard_output_wires_stats_route_skip_grounding_flag` **FAILS on this tree** (re-ran: `1 failed, 15 passed`).
   - **Re-export contract**: commit **`24f2451`** (Jun 26, "fix(phase0): integrate…") deleted the `from ragbot.orchestration.retrieval_filter import (_cliff_detect_filter, _rerank_threshold_gate, CRAG_GRADE_IRRELEVANT, …)` block **and left the comment promising it**. 5 test modules now fail at collection (re-ran).

   Both regressions share a signature: a **"phaseN integrate" merge commit that resurrected pre-fix code**. This is a *process* root cause (no rebase-conflict guard / no green-CI gate on merge) as much as a code one — and it is the highest-leverage thing to fix, because it will recur.

3. **Pass-1 accuracy**: of the flagged findings, I CONFIRM 12, REFINE 2 (magnitude/count), and mark 0 outright REFUTED — but I downgrade one COUNT claim to **OVERCLAIMED** (F2 "7 test files" → the re-export break causes **5**; the other 3 are a separate FastAPI env drift). Pass-1's own false-positive (RETR-F1 digit-key) was in Luồng-1 scope; nothing in the L2 flagged set was a false positive, but two were imprecise.

---

## 1. FRAMEWORK TRAP — langgraph drops undeclared keys (re-probed) — **CONFIRMED (FACT)**

**Re-verification (this session, not trusting pass-1's probe):**

```
$ python  # StateGraph(GraphState) from ragbot.orchestration.state, langgraph 1.2.4
n2 sees query= Q1                                   # declared key survives node return
n2 sees undeclared_return= <MISSING>                # undeclared node-return key dropped
n2 sees _total_graph_iterations= <MISSING>          # undeclared node-return key dropped
n2 sees rerank_score_mode= <MISSING>                # undeclared node-return key dropped
n2 sees raw_user_message(input)= <MISSING>          # undeclared INPUT key dropped pre-first-node
n2 sees bot_extra_output_tokens_per_response(input)= <MISSING>
FINAL keys: ['answer', 'query']                     # undeclared keys absent from final state
```

`state.py:150-197` documents this drop-rule **4 times** ("MUST be declared here — LangGraph's reducer drops keys absent from the TypedDict schema"). The M17 commit (`_mq_speculative_variants`) fixed one instance without adding a guard. **Immutable cause**: a schema-vs-usage invariant with no enforcement test → every new cross-node key regresses silently. This is the parent of §2, §3, §4, §5, §6, §8 below.

---

## PER-FINDING CASE STUDIES (CLAUDE.md 5-step)

### F-A · CRITICAL · Stats-route HALLU net reverted; pin test FAILS — **CONFIRMED (FACT + git + pytest)**

**1. Problem (repro).** A stats/aggregation turn (`retrieve_mode` starts with `"stats"`) skips the grounding judge **unconditionally**. Repro shape (from the 062d6fa commit message, chinh-sach-xe corpus): user asks a stock/quantity question; the matched `document_service_index` entity **lacks** the stock field; the LLM answers a number (`"26"`) leaked from conversation history; because grounding is skipped, the fabricated number ships. HALLU=0 (sacred) broken. Re-ran pin test:
```
tests/unit/test_guard_output_intent_gating.py::test_guard_output_wires_stats_route_skip_grounding_flag  FAILED
assert "stats_route_skip_grounding" in src   →   1 failed, 15 passed
```

**2. Direct cause (layer + số liệu).** Layer = orchestration (guard_output). `guard_output.py:105-106`:
```python
if str(state.get("retrieve_mode") or "").startswith("stats"):
    _grounding_eligible = False
```
No `_pcfg(state, "stats_route_skip_grounding", …)` read. Grep: `stats_route_skip_grounding` exists in `bot_limits.py:67` + constant `_15:124` but **appears nowhere in `src/ragbot/orchestration/`**.

**3. Root cause chain (immutable).**
`L1 unconditional skip` ← `L2 commit 3097755 reverted the gate` ← `L3 "fix(phase1): integrate…" merge resurrected the pre-062d6fa body`.
Evidence: `git blame -L 105,106 guard_output.py` → `3097755c (2026-06-27)`; `git show 062d6fa` shows the exact gated form (`… .startswith("stats") and bool(_pcfg(state, "stats_route_skip_grounding", …))`) added Jun 25; `git show 3097755` shows it deleted Jun 27. Immutable cause = **no green-CI/merge gate → a batch "integrate" commit reintroduced a known HALLU breach** (the pin test that would have caught it is the one now failing).

**4. Expert solution (fix at the root layer).**
- *Short*: re-apply the 062d6fa one-liner — gate the skip behind `_pcfg(state, "stats_route_skip_grounding", DEFAULT_STATS_ROUTE_SKIP_GROUNDING)` (default `False` = grounding ON). Un-breaks the pin test. This restores the correct-by-design default and keeps the per-bot escape for owners hitting reformatting false-blocks ("còn 338 cái" vs "quantity: 338").
- *Mid*: make the pin test a **merge gate** — a pre-merge hook that runs the `tests/unit/test_*_pin.py` set; a red pin blocks the merge. SOTA pattern: *regression pin as executable invariant* (Hyrum's-law defence). The gate is the real fix; the code is a 1-liner.
- *Long*: governance — "integrate" batch commits must be rebased onto a green tree and re-run the pin suite; forbid squash-merges that discard the intermediate green state.

**5. Self-critique — expert or patch?** The 1-liner alone is a **patch** (it will be reverted again by the next bad integrate). The *expert* fix is the merge gate + pin-as-invariant; the code change is necessary but not sufficient. This is why F-A's "expert_solution" leads with the gate, not the line.

**Trade-offs.** Re-enabling grounding on stats adds one LLM judge call on stats turns (latency/cost). That is the *designed* cost of HALLU=0; owners with proven false-blocks opt out per-bot. No sacred-rule conflict (no app-inject; refusal text still from `oos_answer_template`).

**Impact.** Correctness/HALLU — sacred. Blast radius = every bot using the stats route (catalog/price bots = the platform's primary demo shape). For a banking/legal corpus this = shipping a wrong number as an authoritative fact. **CRITICAL.**

---

### F-B · CRITICAL · Re-export contract broken → 5 pin modules dead — **CONFIRMED, count REFINED (OVERCLAIMED in pass-1)**

**1. Problem.** `query_graph.py:273-276` promises "CRAG grade vocabulary + pure chunk/grade filters … Re-exported here so existing call sites + test imports … are unchanged" — but no import exists. Re-ran:
```
from ragbot.orchestration.query_graph import _cliff_detect_filter   → ImportError
from ragbot.orchestration.query_graph import _rerank_threshold_gate → ImportError
from ragbot.orchestration.query_graph import CRAG_GRADE_IRRELEVANT  → ImportError
from ragbot.orchestration.query_graph import parse_decomposed_sub_queries → ImportError
```
Collection errors (re-ran `pytest tests/unit/ --co`): **8 total**, of which **exactly 5** are this class:
`test_cliff_detect_filter`, `test_reranker_threshold_gate`, `orchestration/test_crag_compound_query` (imports `CRAG_GRADE_AMBIGUOUS`), `test_query_decompose` (`parse_decomposed_sub_queries`), `test_output_guardrail_tuning` (`_rerank_threshold_gate`).

**Pass-1 said "7 unit-test files fail at collection" and listed 7 (incl. `test_t2_perf_fixes`, `test_crag_three_states`).** Re-verified: those two did **not** surface as collection errors this run; the true re-export-caused count is **5**. → **OVERCLAIMED on count** (the mechanism and the "pins not running" consequence are CONFIRMED). The other 3 collection errors (`test_feedback_loop_wire`, `test_admin_documents_debug_route`, `test_route_workspace_scope_pin`) are a **separate** cause: `ImportError: cannot import name '_EffectiveRouteContext' from 'fastapi.routing'` — a FastAPI version drift, NOT the re-export break.

**2. Direct cause.** Layer = orchestration module surface. The re-export import block was deleted; the comment left behind.

**3. Root cause chain.** `L1 missing import` ← `L2 commit 24f2451 deleted the block`. `git show 24f2451 -- query_graph.py` shows removed lines:
```
-from ragbot.orchestration.retrieval_filter import (  # noqa: E402
-    CRAG_GRADE_IRRELEVANT,
-    _cliff_detect_filter,
-    _rerank_threshold_gate,
-    parse_decomposed_sub_queries,
```
Immutable cause = same class as F-A: a **"fix(phase0): integrate…" merge commit** (`24f2451`, Jun 26) dropped a working re-export while the strangler-Phase-2 comment stayed. **Consequence**: the cliff-filter, refuse-threshold-gate, and CRAG-vocabulary invariants are **currently unguarded** — the exact regression pins for the retrieval quality gates are not executing.

**4. Expert solution.**
- *Short*: restore the one import block (5 lines) → 5 modules collect again; run them.
- *Mid*: add a "collection-error == 0" CI gate (`pytest --co -q` must exit clean). SOTA: *fail the build on import-time errors*, don't let them silently park tests. Separately, pin FastAPI (or fix the `_EffectiveRouteContext` import) to clear the other 3.
- *Long*: finish the strangler — either delete the re-export shim and update the 5 test imports to point at `retrieval_filter` directly (cleaner boundary), or keep the shim with a `__all__` + a test asserting the shim exports the promised names.

**5. Self-critique.** Restoring the import is the correct minimal fix (the strangler intent is to keep call sites unchanged), but the *expert* move is the collection-error CI gate — without it, the next integrate silently re-parks tests. Patch = import line; expert = the gate.

**Trade-offs.** None material; restoring a re-export is pure additive.

**Impact.** Correctness (invariants unguarded) + process. Blast radius = the retrieval-quality regression net. **HIGH** (downgraded from pass-1's implicit CRITICAL because the runtime code paths still work; it's the *guards* that are off).

---

### F-C · HIGH · `int(_price)` truncates NUMERIC decimal prices in the stats synthetic chunk — **CONFIRMED (FACT)**

**1. Problem.** `document_service_index.price_primary` is `numeric` (nullable) — verified in `alembic/squashed_baseline.sql:308` and `stats_index_repository.py:20`. `_do_stats_lookup` renders and dedups prices via `int(_price)`:
- dedup key `query_graph.py:2391`: `_key = (_name, int(_price) if _price is not None else -1)`
- render `:2413`: `f"{_name}: {int(_price)}"`; `:2419`: `f"price: {int(_price)}"`.
Repro: US/EU tenant XLSX with `19.99` / `19.50` → the synthetic (score=1.0, rerank+grade-bypassed) chunk feeds the LLM `"19"` as a grounded price, AND the dedup key `(name, 19)` **collides** two distinct products (19.99 vs 19.50) → one silently dropped.

**2. Direct cause.** Layer = orchestration stats builder. `int()` floor on a `numeric`. The adjacent comment `:2403-2404` explicitly claims currency-neutrality ("the corpus may be in any currency") — the code contradicts its own contract.

**3. Root cause chain.** `L1 int() truncation` ← `L2 the stats renderer assumes integer VND prices` ← `L3 the whole stats index was shaped for a VND catalog corpus (happy-case box)`. Immutable cause = **currency/scale baked as integer** in a path documented as currency-neutral. Reachability confirmed: `query_range_parser.py:220-224` produces two-bound `price_column="any"` filters from "từ X đến Y", and the range route reaches `_do_stats_lookup`.

**4. Expert solution.**
- *Short*: render `Decimal`/`str(_price)` (drop `int()`); dedup on the normalized `Decimal` (or the exact `str`), not `int`.
- *Mid*: a currency/scale config per bot (already the direction of the `metadata-optional-hint` skill) so formatting is data-driven, not baked. SOTA: *money as decimal, never float/int truncation* (classic financial-correctness rule).
- *Long*: type the stats index price at the DTO layer as `Decimal` end-to-end; add a decimal-price stats fixture to the pin suite.

**5. Self-critique.** Dropping `int()` is the correct fix at the exact root layer (the renderer). It is expert (not a symptom patch) because the root *is* the truncation; the only "patch-y" alternative (post-hoc string-fix) would be worse. The mid/long currency-config is the generalization that escapes the VND happy-case box.

**Trade-offs.** `str(Decimal)` may print trailing zeros ("19.50" vs "19.5") — cosmetic; prefer `format(Decimal(p).normalize())`. No sacred conflict (it's retrieved DATA, not injected text).

**Impact.** Correctness/HALLU-misinterpret for any decimal-currency corpus; silent product-collision even for VND catalogs with equal integer parts. Blast radius = every stats answer on a decimal or fractional-price catalog. **HIGH.**

---

### F-D · CRITICAL · GraphRAG is dead in BOTH directions (kwarg mismatch + chunk_id=None drop) — **CONFIRMED (FACT, bind-checked)**

**1. Problem.** A bot with `graph_rag_mode != "disabled"` pays the entity/triple extraction cost but the LLM prompt receives **zero** graph context — two independent kills.

**2. Direct cause (bind-checked).**
- *Kill 1 — kwarg mismatch*: `knowledge_graph.py:179-188` declares `query_graph(self, query, record_bot_id, session, *, max_hops=…, …)`. The caller `graph_retriever.py:59-64` calls `kg_service.query_graph(query=…, bot_id=record_bot_id, session=…, max_hops=…)`. Re-verified with `inspect.signature(...).bind`: `BIND FAILS -> missing a required argument: 'record_bot_id'` (and `bot_id` is not a param). Every call raises `TypeError`, swallowed at `graph_retriever.py:107 (except Exception)` → always `{"graph_context": []}`.
- *Kill 2 — falsy-id drop*: even if Kill 1 were fixed, synthesized chunks carry `"chunk_id": None` (`graph_retriever.py:83`); `generate.py:633` does `if not cid: continue`, dropping every graph chunk from the `<documents>` block.

**3. Root cause chain.** `L1 zero graph context` ← `L2a caller passes bot_id= (wrong keyword) / L2b generate drops falsy chunk_id` ← `L3 GraphRAG shipped without an integration test exercising the real kg_service un-mocked` (the wrapper's broad-except hides the TypeError; unit tests mock `kg_service` so the signature drift never surfaces). Immutable cause = **broad-except in the wrapper + mock-only tests hide a signature contract break**.

**4. Expert solution.**
- *Short*: `bot_id=` → `record_bot_id=` at `graph_retriever.py:61`; give synthesized chunks a synthetic id (mirror the stats route's `DEFAULT_STATS_SYNTHETIC_CHUNK_ID` pattern) so `generate` keeps them — OR, if GraphRAG is not a near-term priority, **gate the feature OFF** so it stops burning extract tokens.
- *Mid*: one integration test per registry that instantiates the **real** `KnowledgeGraphService` un-mocked and asserts a non-empty `graph_context` on a seeded triple. SOTA: *contract test at the port boundary*, not a mock. Narrow the wrapper's `except Exception` to the real failure types so a signature break fails loud.
- *Long*: if kept, make the citation regex + generate accept the graph synthetic-id namespace so triples are citeable (today they'd be un-citeable like the stats sentinel).

**5. Self-critique.** The `bot_id→record_bot_id` swap + synthetic id is the correct fix at both root layers. It is expert *provided* the mid-term contract test lands — otherwise the broad-except will hide the next drift. The honest expert call may be "gate OFF until wired" (evolve, don't ship a cost-only feature).

**Trade-offs.** Fixing without the citeable-id long-term means graph chunks influence the answer but can't be cited → a citation-coverage gap. Gating off is zero-risk but forfeits multi-doc/entity-hop recall.

**Impact.** Correctness (multi-doc/entity queries get no graph lift) + Cost (extract tokens burned then discarded). Blast radius = any bot that enabled GraphRAG. **CRITICAL for those bots; default `disabled` limits fleet exposure.**

---

### F-E · HIGH · Paid `extra_output_tokens_per_response` always 0 (state-key drop) — **CONFIRMED (FACT)**

**1. Problem.** A bot that bought extra output tokens (`extra_output_tokens_per_response=2048`) still truncates answers at the platform default cap; owner sees the paid knob do nothing, zero error anywhere.

**2. Direct cause.** `graph_assembly.py:193-195` puts `bot_extra_output_tokens_per_response` into the **input** state dict → dropped before node 1 (§1 probe: `<MISSING>`). `generate.py:739` reads `state.get("bot_extra_output_tokens_per_response", 0) or 0` → always 0 → `compute_output_cap(system_default, 0)` = system default for every bot.

**3. Root cause chain.** `L1 always 0` ← `L2 key set on input dict but undeclared in GraphState` ← `L3 no schema-vs-usage pin test` (the §1 framework trap). Immutable cause = the same undeclared-key class.

**4. Expert solution.** *Short*: declare `bot_extra_output_tokens_per_response` (and the sibling input keys `raw_user_message`, `bot_created_at`) in `GraphState`. *Mid*: **one AST pin test** that walks `nodes/*.py` + `graph_assembly.py` for returned-dict-literal keys and `state.get("…")` reads, diffs against `GraphState.__annotations__`, and fails on any key used-but-undeclared. This single test closes F-E, F-F, F-G, F-H at once and prevents the 4th recurrence. *Long*: typed per-channel reducers (langgraph `Annotated[..., reducer]`) so the schema is the single source.

**5. Self-critique.** Declaring the key is a patch if done alone (4th time this class appears). The **AST pin test is the expert fix** — it makes the framework trap a compile-time-ish error. That is why the solution leads with the test.

**Trade-offs.** The AST test needs care (multiline returns, dynamic keys) but is deterministic and cheap. No runtime cost.

**Impact.** Revenue (paid feature silently off) + Correctness (answer truncation). Blast radius = every paying bot with the knob set. **HIGH.**

---

### F-F · HIGH · `rerank_score_mode` drop → absolute CRAG floor dead → HALLU risk↑ — **CONFIRMED, magnitude REFINED**

**1. Problem.** In the CRAG all-irrelevant fallback, the code intends to apply an **absolute** cross-encoder floor when the reranker is active, else a scale-invariant relative gate. Because `rerank_score_mode` is dropped, the absolute branch is dead; the relative gate always runs.

**2. Direct cause.** Producer `rerank.py:498` returns `{"reranked_chunks": out, "rerank_score_mode": mode}` (node return → dropped). Consumer `grade.py:486`: `if state.get("rerank_score_mode") == "rerank":` → always False → falls to the relative gate (`grade.py:490-501`, keep ≥ `top × DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO(0.5)`). The absolute floor `DEFAULT_CRAG_MIN_FALLBACK_SCORE=0.3` (`_10:93`) + `crag_min_fallback_score_by_intent` are dead config.

**3. Root cause chain.** `L1 absolute floor never applies` ← `L2 rerank_score_mode undeclared → dropped` ← `L3 §1 framework trap`. Immutable cause = undeclared-key class.

**REFINEMENT vs pass-1.** Pass-1 said "irrelevant chunks feed generate where the 0.25 floor would have refused." Re-read: the floor default is **0.3** (not 0.25; the by-intent map may differ), and the weakening is corner-specific: it bites only when (reranker active) ∧ (all chunks graded irrelevant) ∧ (top rerank score low-but-nonzero, e.g. 0.1). At top=0.1 the relative gate keeps ≥0.05 (garbage passes) whereas the absolute 0.3 floor would reject → refuse. At healthy top scores (0.9) both gates behave the same. So the finding is **CONFIRMED as a real HALLU-guard weakening**, but the magnitude is "narrow corner", not "always". Severity stays HIGH because the corner is exactly the low-signal case where HALLU risk is highest.

**4. Expert solution.** *Short*: declare `rerank_score_mode` in GraphState (part of the F-E AST-test batch). *Mid*: same AST pin test. *Long*: replace the mode string with a self-describing score (carry `score_scale` on each chunk dict, which DOES survive because it's inside a declared list) so the gate never depends on a scalar cross-node key. SOTA: *make the calibration signal travel with the data it calibrates*.

**5. Self-critique.** Declaring the key fixes it; the long-term "scale on the chunk" is the more robust expert design (immune to the framework trap by construction). Patch = declare; expert = co-locate the scale with the chunk.

**Trade-offs.** None; the absolute floor is the intended stricter behavior.

**Impact.** Correctness/HALLU on low-signal turns with an active reranker. Blast radius = fleet (ZE reranker is the production default). **HIGH.**

---

### F-G · MEDIUM-HIGH · Graph-iteration cap dead; reflect loop bounded only by recursion_limit — **CONFIRMED (FACT), frequency HYPOTHESIS (correctly labeled)**

**1. Problem.** A reflection-enabled bot with an empty `oos_answer_template` (legal per sacred rule #3) + a 0-chunk turn → `generate` emits empty answer → `_reflect_route` → `generate` → … until transport `recursion_limit` raises `GraphRecursionError` → 500, instead of the designed graceful persist.

**2. Direct cause.** `grade.py` accumulates+returns `_total_graph_iterations` (`:83,108,156,268,413,536`); `_reflect_route (routing.py:245)` reads `state.get("_total_graph_iterations", 0)` → always 0 → `0 >= max(8)` never true → `graph_iteration_cap_reached` warning never fires.

**3. Root cause chain.** `L1 cap never trips` ← `L2 _total_graph_iterations undeclared → each pass reads 0` ← `L3 §1 framework trap`. Immutable cause = undeclared-key class.

**4. Expert solution.** Declare the key (F-E batch). The reflect→generate loop then obeys the intended `max_total_graph_iterations=8` cap and persists gracefully.

**5. Self-critique.** Declaring is the correct root fix (the counter logic is otherwise sound). Expert, not patch.

**Trade-offs.** None.

**Impact.** Correctness/UX (500 vs graceful degrade) — but **gated**: `reflection_enabled` defaults False (`_01:274`, verified) and is per-bot opt-in, so only reflection-enabled bots are exposed. Pass-1 correctly labeled the dead counter FACT and the frequency HYPOTHESIS. **MEDIUM-HIGH.** (Sibling `crag_skip_retry` drop is **masked** by the declared `retrieval_adequate` co-set — I confirm pass-1 did NOT overclaim it: `_grade_route:167` reads the dead key but the fast-path still lands on `generate` via `retrieval_adequate`; no behavior bug there.)

---

### F-H · MEDIUM · Cascade routing no-op (`resolved_answer_model` written, never consumed) — **CONFIRMED (FACT)**

**1. Problem.** Owner enables `cascade_routing_enabled` + seeds tier models; complex queries log `cascade_routing_applied`; the answer is still generated by the unchanged binding model. A/B shows zero delta; pure cost + log noise.

**2. Direct cause.** `generate.py:408` sets `state["resolved_answer_model"] = _cascade_resolved`. Grep: consumed only at `generate.py:376` (for the log's `current_model`), never by `_invoke_llm_node`, which resolves via `resolve_runtime(purpose=lookup_purpose)` (`query_graph.py:939-947`). And the key is undeclared → dropped anyway. Bonus: `generate.py:392,415` log `bot_id=str(state.get("bot_id") or "")` — `bot_id` is never a GraphState key → always "".

**3. Root cause chain.** `L1 model unchanged` ← `L2 resolved_answer_model consumed nowhere in the LLM call path (and dropped)` ← `L3 the cascade wire was built to write a key the call path never reads`. Immutable cause = **half-wired feature: producer without consumer** (independent of the state-drop; even a declared key wouldn't help because `_invoke_llm_node` doesn't read it).

**4. Expert solution.** *Short*: either (a) make `_invoke_llm_node` accept an optional `override_model` and thread the cascade result into it, OR (b) delete the wire + `cascade_router_helper` (currently pure cost/noise). Given EVOLVE-not-rewrite and that cascade adds a resolve + 2 log lines/turn for nothing, **(b) delete** is the honest expert call unless there's a validated A/B lift. *Mid*: if kept, an integration test asserting the LLM call actually used the cascade-resolved model. *Long*: cascade as a real Strategy at the resolve boundary (resolver returns the tier model), not a post-resolve state key.

**5. Self-critique.** Deleting a no-op is more expert than "declaring the key" — declaring it would still change nothing because the call path ignores it. Pass-1's "wire or delete" is right; I lean delete absent an A/B number (rule #0: no evidence of lift).

**Trade-offs.** Deleting forfeits a future cascade feature; but it's not a feature today, it's cost. Re-add behind a validated experiment.

**Impact.** Cost + T1 (owner believes a smartness feature is on). Blast radius = cascade-enabled bots. **MEDIUM.**

---

### F-I · CRITICAL (ops) · All 5 `ai_keys` methods query a non-existent schema `ragbot.ai_keys` — **CONFIRMED (FACT + live DB)**

**1. Problem.** Admin key-rotation/health endpoints (`POST /admin/ai/keys` etc.) 500 with `UndefinedTableError`. The V16 DB-backed key-pool is broken at the repo layer; `public.ai_keys` has 0 rows (no key ever persisted through this path).

**2. Direct cause (live DB).** `ai_config_repository.py:664/689/708/731/747` hardcode `ragbot.ai_keys`. Live `ragbot_v2_dev` (asyncpg this session): `pg_namespace nspname='ragbot'` count = **0**; `to_regclass('ragbot.ai_keys')` = **None**; `to_regclass('public.ai_keys')` = **ai_keys**. `models.py:53 RAGBOT_SCHEMA="public"` is the leftover — the raw-SQL strings kept the `ragbot.` prefix after the schema was collapsed into `public` (baseline `CREATE TABLE public.ai_keys`).

**3. Root cause chain.** `L1 UndefinedTableError` ← `L2 raw SQL literal 'ragbot.'` ← `L3 schema-collapse migration didn't sweep raw-SQL string literals (only ORM/`RAGBOT_SCHEMA` const updated)`. Immutable cause = **raw SQL string literals not covered by the schema-rename refactor**.

**4. Expert solution.** *Short*: drop the `ragbot.` prefix (or interpolate `RAGBOT_SCHEMA` = "public") in all 5 statements. *Mid*: a grep gate forbidding `\bragbot\.` schema-qualified identifiers in `src/` (there is exactly one schema now). *Long*: prefer ORM/`Table` metadata over raw SQL for these CRUDs so schema is centralized.

**5. Self-critique.** The prefix fix is the exact root fix. Expert. The grep gate prevents recurrence.

**Trade-offs.** None.

**Impact.** Correctness/ops — the entire DB key-pool admin feature is bricked; only `.env`-sourced keys work. **CRITICAL** for key management, though runtime answering still functions via env keys. (This is a repo-layer finding but it directly gates the query-flow's key supply, so it's in-scope for L2.)

---

### F-J · HIGH · `count_by_name_keyword` ≠ `query_by_name_keyword` match set → count contradicts list — **CONFIRMED (FACT)**

**1. Problem.** "có bao nhiêu gói triệt lông nách?" → `count_by_name_keyword` returns **0** (forward unaccent-ILIKE misses granular entity "Nách"), while the list route returns rows via its reverse fallback. Count and list about the same catalog contradict each other — a coherence bug in the B-AGG count-honesty feature (`949a3a4`).

**2. Direct cause.** `count_by_name_keyword` (`stats_index_repository.py:406-464`) has forward unaccent-ILIKE + attributes ONLY. `query_by_name_keyword` (`:566-645`) additionally has (a) notation-fold matcher `_fold()` (`:571`) and (b) reverse/token fallback fired on empty forward (`:615-645`). The count docstring (`:419-423`) justifies omitting the **fold** ("ranking detail, does not change cardinality") — but that's incorrect (a fold-only match *adds* a row) and the docstring is **silent on the reverse-fallback omission**, which is the bigger divergence.

**3. Root cause chain.** `L1 count 0 vs list N` ← `L2 two independent WHERE builders drifted` ← `L3 count and list were written/patched separately with no shared match-set contract`. Immutable cause = **duplicated match logic without a single source**.

**4. Expert solution.** *Short*: extract one `_name_match_where(variants)` builder + one reverse-fallback and call it from both count and list; count = `COUNT(*)` over the identical predicate (including reverse fallback when forward is empty). *Mid*: a pin test asserting `count == len(list)` for a fixture with a granular-entity + notation-variant. SOTA: *count and list must be the same query modulo projection* (aggregate/list coherence). *Long*: fold this into a single `SelfQuery` predicate object.

**5. Self-critique.** Sharing the WHERE builder is the exact root fix (not a patch on either side). Expert.

**Trade-offs.** Reverse fallback on count adds one query on empty-forward; negligible and only on the miss path.

**Impact.** Correctness on the aggregation query class the B-AGG feature was built to make honest. Blast radius = every count-vs-list pair on a catalog with granular/notation-variant entities. **HIGH.**

---

### F-K · HIGH · Price-range "any" default matches rows where NEITHER price is in range (cross-column OR/AND) — **CONFIRMED (FACT)**

**1. Problem.** "dịch vụ nào giá từ 100k đến 200k?" returns an entity with `price_primary=1,200,000` + `price_secondary=90,000` — neither price in [100k,200k]. Bot confidently lists an out-of-range service.

**2. Direct cause.** `stats_index_repository.py:238-248` (`query_by_price_range`) and `:381-391` (`count_by_price_range`), default `price_column="any"` (`:197`), both bounds set → WHERE = `(pp>=min OR ps>=min) AND (pp<=max OR ps<=max)` evaluated **across different columns**. The 1.2M row satisfies `(1.2M>=100k …)` and `(… OR 90k<=200k)`. Reachability: `query_range_parser.py:220-224` emits `price_min, price_max, price_column="any"` for "từ X đến Y" / "X - Y" — user-reachable.

**3. Root cause chain.** `L1 out-of-range row returned` ← `L2 min-bound and max-bound OR-clauses span different columns joined by AND` ← `L3 the "any" semantics should be per-column BETWEEN, not cross-column`. Immutable cause = **incorrect boolean decomposition of a per-row range predicate**.

**4. Expert solution.** *Short*: change "any" to `(price_primary BETWEEN :min AND :max) OR (price_secondary BETWEEN :min AND :max)` in both methods (share one builder to avoid re-drift — combine with F-J's extraction). *Mid*: a pin test with the (1.2M, 90k) fixture. *Long*: a typed range-predicate builder used by both range + count.

**5. Self-critique.** The BETWEEN rewrite is the exact semantic fix at the SQL layer (the root). Expert, not a patch.

**Trade-offs.** None; the new predicate is strictly more correct.

**Impact.** Correctness/T1 on the core price-range query class the stats index exists to make deterministic. Blast radius = any two-column-priced catalog with a two-bound range query. **HIGH.**

---

### F-L · HIGH · Per-bot `EmbeddingSpec.dimension` ignored by Jina/ZE adapters (matryoshka pinned by constant) — **CONFIRMED (FACT)**

**1. Problem.** A per-bot binding declaring a non-default embedding dimension is silently ignored; vectors come back at the ctor-constant dim.

**2. Direct cause.** `zeroentropy_embedder.py:77` (`dimensions=DEFAULT_ZEROENTROPY_EMBEDDING_DIM`=1280) and `jina_embedder.py:125` (`dimensions=DEFAULT_JINA_EMBEDDING_DIM`=1024) take dimensions as a **ctor constant** and send `self._dimensions` on the wire (`ze:148,229` / `jina:238,299`). `embedding/registry.py:100-122` assembles kwargs = {key_pool_factory, model, ledger, tpm_*} and **never forwards `dimensions`**. Per-bot `EmbeddingSpec.dimension` (resolved by `model_resolver/_binding_mixin.py`) never reaches the wire.

**3. Root cause chain.** `L1 per-bot dim ignored` ← `L2 registry doesn't forward dimensions; adapters constant-default it` ← `L3 platform is single-embedding-space by design (one global document_chunks.embedding column) so the resolved dim was never plumbed to the wire`. Immutable cause = **resolve→wire plumbing gap masked by a single global column**.

**4. Expert solution.** *Short*: forward `spec.dimension` through `build_embedder` into the adapter ctor (all three adapters already accept `dimensions`). *Mid*: the ZE health-check-style length assert per bot (currently only catches a global flip). *Long*: multi-column / per-space storage if true multi-dim per bot is a product goal; else document that dim is platform-global and remove the per-binding `extra_params.dimension` knob to stop the config theater. SOTA: *don't expose a knob you don't honor*.

**5. Self-critique.** Forwarding the dim is the correct plumbing fix; but the honest expert call is to decide FIRST whether multi-dim-per-bot is a real requirement (single global column today), then either plumb-and-store or remove the knob. Plumbing without a matching column would produce insert/query dim-mismatch — so the *design decision* is the expert step, not just the forward.

**Trade-offs.** Forwarding without multi-column storage risks dim-mismatch errors — must be paired with the storage decision.

**Impact.** Correctness (multi-bot) — latent; masked by the single global column today. Blast radius = the moment a second bot needs a different dim. **HIGH (latent).**

---

### F-M · HIGH (opt-in) · `SPECULATIVE_REDO_SENTINEL` leaks verbatim into the answer — **CONFIRMED (FACT)**

**1. Problem.** With `speculative_streaming_enabled` + `speculative_hallu_verify_enabled` (the documented HALLU-safe combo), a verifier reject yields the literal `"__SPECULATIVE_REDO__"` into the user answer and it is persisted.

**2. Direct cause.** `speculative_router.py:423,500` yield `SPECULATIVE_REDO_SENTINEL`. Grep: zero consumers in `orchestration/`/`interfaces/routes`; the intended strip helper `_sse_helper.py:88 redo_event` has **zero callers**. The stream consumer `query_graph.py:1095` does `buffer.append(delta)` unconditionally (→ `answer_text` → cache/log) and forwards to the SSE sink unfiltered.

**3. Root cause chain.** `L1 sentinel in answer` ← `L2 no consumer strips it; redo_event never called` ← `L3 Phase-3 speculative-verify wiring is half-done (sentinel producer shipped, SSE-side redo protocol not)`. Immutable cause = **half-wired protocol: sentinel emitted with no receiver**.

**4. Expert solution.** *Short*: in the stream consumer, on encountering the sentinel, discard the accumulated draft buffer and re-stream main (the intended redo), never appending the sentinel; OR gate `speculative_hallu_verify_enabled` OFF until the redo receiver lands. *Mid*: an integration test that forces a verifier reject and asserts the sentinel never appears in `answer_text` or the SSE stream. *Long*: finish the `redo_event` wiring so the client gets a clean redo signal.

**5. Self-critique.** Gating off is the honest short-term expert move (ship-safe); the buffer-discard is the real fix. Either is at the correct layer (the consumer that owns the buffer). Not a patch — it addresses the missing receiver directly.

**Trade-offs.** Gating off forfeits the speculative-verify HALLU feature until wired; shipping garbage is worse.

**Impact.** Correctness/HALLU-visible (user sees a control token). Blast radius = bots on the opt-in speculative-verify combo. **HIGH (opt-in).**

---

### F-N · MEDIUM-HIGH · Heuristic `0.85 >= 0.85` defeats "force-LLM-on-borderline"; locale signals not threaded — **CONFIRMED (FACT)**

**1. Problem.** A mid-string aggregation/multi_hop/comparison match (e.g. a turn-2 "vậy nó khác gì với gói kia?") hard-classifies at confidence 0.85, skips the LLM understand pass AND the history condense, and retrieval runs on the raw pronoun query. Every bot classifies on the **vi** seed regardless of `bots.language`.

**2. Direct cause.** `heuristic_intent_classifier.py:129` returns `confidence = 0.85` for those intents (`:127` returns 0.90 for anchored greeting/chitchat). `understand.py:125` gates `if _h_result.confidence >= _h_threshold` with `HEURISTIC_INTENT_CONFIDENCE_THRESHOLD = 0.85` (`_21:205`) → `0.85 >= 0.85` **passes**. The docstring (`:55,:109`) says 0.85 is set *specifically to force an LLM check*. Separately, `understand.py:117` calls `_classify_heuristic(state.get("query") or "")` with **no `signals`** → `heuristic_intent_classifier.py:121` falls to `_DEFAULT_SIGNALS` (vi seed).

**3. Root cause chain.** `L1 LLM skipped on borderline + condense skipped` ← `L2 boundary is >= not >` ← `L3 threshold constant equals the "borderline" confidence value` — an off-by-boundary that nullifies the safety margin. And `L1 vi-only classify` ← `L2 call site passes no signals` ← `L3 the DB-hydrated RoutingSignals path is built but never threaded from bot config`. Immutable causes = **boundary-equality bug** + **locale-signals-not-wired**.

**4. Expert solution.** *Short*: change the gate to `>` (or raise the threshold above 0.85, e.g. 0.86) so 0.85 forces the LLM; thread `signals=get_routing_signals(state["language"])` into the classifier call. *Mid*: gate the heuristic fast-path on empty history (a mid-conversation follow-up must not fast-path a pronoun query); make the understand-cache GET history-aware (today GET is not history-gated, SET is — a single-turn cached intent hits a mid-conversation same-text turn). *Long*: locale packs as data threaded end-to-end (the pattern `vocabulary_expander`/superlative already use). SOTA: *language as data, per-locale packs keyed by code*.

**5. Self-critique.** The `>` fix + signal threading are exact root fixes at the two layers. Expert. The history-gating is the higher-value follow-up (the boundary bug is only harmful on context-dependent follow-ups).

**Trade-offs.** `>` sends true-0.85 turns to the LLM (slight latency/cost) — that is the intended safety trade.

**Impact.** Cost (misroute price-factoid→aggregation inflates top_k) + Correctness (pronoun follow-ups retrieved raw) + multi-locale (all bots vi-classified). Blast radius = every non-vi bot + every context-dependent follow-up. **MEDIUM-HIGH.**

---

### F-O · MEDIUM · `cross_doc_reconcile_enabled` / `xml_wrap_enabled` are mirage knobs — **CONFIRMED (FACT)**

**1. Problem.** `cross_doc_reconcile_enabled` is force-ON with no opt-out despite a "Per-bot opt-out" comment; `xml_wrap_enabled` is unreachable (feature 100% off in production).

**2. Direct cause.** Grep of both pipeline_config builders (`chat_worker/pipeline_config.py`, `test_chat/_pipeline_config.py`): neither sets `cross_doc_reconcile_enabled` or `xml_wrap_enabled`. So `_pcfg(state,"cross_doc_reconcile_enabled",True)` (`query_graph.py:2380`, inline `True`) always returns True (force-ON, zero-hardcode nit for a behavior toggle), and `_pcfg(state,"xml_wrap_enabled",None)` (`:450`) always None. XML-wrap is doubly dead: the fallback also depends on `bot_created_at`, an input key dropped by §1. Confirmed additionally: `bot_custom_vocabulary` worker-only (`:286`), `embedding_provider` test_chat-only (`:370`) → transport asymmetry (F15/F10 in pass-1).

**3. Root cause chain.** `L1 knob not honored` ← `L2 key populated by no builder` ← `L3 the builders are static whitelists with no generic plan_limits passthrough`. Immutable cause = **static config whitelist missing the key**.

**4. Expert solution.** *Short*: add the missing keys to both builders (and lift the inline `True`/`None` defaults into constants). *Mid*: a generic `plan_limits` passthrough (allowlisted) so a new per-bot knob doesn't require touching two builders. *Long*: single config-builder shared by worker + test_chat (the transport asymmetry F15/F10 disappears). SOTA: *one config assembler, one source*.

**5. Self-critique.** Adding the keys is the direct fix; the shared-builder is the expert generalization that kills the whole mirage-knob + transport-asymmetry class. Patch = 2 keys; expert = one builder.

**Trade-offs.** A generic passthrough needs an allowlist to avoid injecting arbitrary knobs; manageable.

**Impact.** Correctness (no opt-out for cross-doc reconcile; XML-wrap dead) + multi-bot (config theater). **MEDIUM.**

---

### F-P · MEDIUM · `retrieval_degraded` / `embed_degraded` HALLU-safety flags: written, zero readers — **CONFIRMED (FACT)**

**1. Problem.** The advertised protection — "answer path won't fabricate from a vector-less/degraded context" — does not exist. A retrieval-outage turn is indistinguishable downstream from a genuine no-match turn.

**2. Direct cause.** Set in-place at `query_graph.py:403` (`retrieval_degraded`) and `:1500` (`embed_degraded`) with comments promising HALLU-safety. Project-wide grep: **no reader** in `src/` or `tests/`; and the in-place write wouldn't survive the node boundary (§1) even if a reader existed.

**3. Root cause chain.** `L1 no distinction error-empty vs no-match` ← `L2 flags write-only + undeclared` ← `L3 the degraded-safety design was documented but never wired to the refuse/answer decision`. Immutable cause = **write-only observability with a false safety promise**.

**4. Expert solution.** *Short*: either wire a reader (declare the key; in the refuse short-circuit, if `retrieval_degraded`, prefer a transient-error refusal path over the no-context path) OR delete the flags + their misleading comments. *Mid*: if wired, an integration test forcing a retrieval error and asserting the degraded refusal path. *Long*: a first-class "retrieval health" signal on state consumed by generate. SOTA: *graceful degradation — transport error ≠ empty result*.

**5. Self-critique.** Deleting the false-promise comment is the minimum honesty fix; wiring the reader is the real feature. Given EVOLVE, wiring is preferable (the safety was intended) but only if a concrete error→refusal behavior is defined — otherwise delete to stop documenting non-existent safety.

**Trade-offs.** Wiring adds a branch in the refuse path; must not inject text (sacred #10) — use `oos_answer_template`, not a hardcoded transient message.

**Impact.** Correctness/HALLU-adjacent (a retrieval outage looks like "corpus has nothing"). **MEDIUM.**

---

## NEW FINDINGS (not in the flagged set)

### N1 · MEDIUM · Two CRITICAL regressions came from the SAME merge-commit family — process root cause
Both F-A (`3097755`) and F-B (`24f2451`) are "fix(phaseN): integrate…" batch merges dated Jun 26–27 that **resurrected pre-fix code** and left the now-stale comment/pin behind. This is a *process* immutable cause: integrate/rebase batches land without re-running the pin suite or a collection-error gate. **Expert fix (governance)**: a pre-merge hook running `pytest tests/unit/test_*_pin.py` + `pytest --co` (0 collection errors) as a **blocking gate**; forbid squash-merges of "integrate" commits onto a non-green tree. This single control would have caught BOTH criticals. (FACT: git blame/show pin both commits to this pattern.)

### N2 · LOW-MEDIUM · `_do_stats_lookup` dedup `-1` sentinel collides all price-less entities under one name
`query_graph.py:2391`: `_key = (_name, int(_price) if _price is not None else -1)`. Two genuinely distinct price-less entities that happen to share a `_name` (e.g. two "Combo" rows with prices on sibling notation-variants) collapse to `(_name, -1)` and one is dropped from the synthetic chunk. Compounds F-C (the `int()` collision) and F-J (count/list drift). **Expert fix**: dedup on the entity PK / `record_chunk_id`, not `(name, int(price))`. (FACT on the key shape; collision frequency HYPOTHESIS.)

### N3 · LOW · `bot_id` logged as always-"" in cascade + adaptive_router events
`generate.py:392,415` and `query_graph.py:2638` log `bot_id=str(state.get("bot_id") or "")` — `bot_id` is not a GraphState key (only `record_bot_id` is), so these observability events always emit an empty `bot_id`. Debuggability regression for cascade + adaptive-router-L1 telemetry. **Expert fix**: log `str(state.get("record_bot_id") or "")`. (FACT.)

---

## VERDICT LEDGER

| # | Finding | Pass-1 sev | Pass-2 verdict | Note |
|---|---|---|---|---|
| F-A | stats-route grounding reverted (3097755) | CRIT | **CONFIRMED** | pin test FAILS; git blame = 3097755 |
| F-B | re-export break | HIGH | **REFINED / count OVERCLAIMED** | 5 modules (not 7); +3 separate FastAPI drift |
| F-C | int(_price) truncation | HIGH | **CONFIRMED** | numeric col; two-bound reachable |
| F-D | GraphRAG dead both ways | CRIT | **CONFIRMED** | sig.bind fails; chunk_id=None dropped |
| F-E | paid tokens always 0 | HIGH | **CONFIRMED** | input key dropped (probe) |
| F-F | rerank_score_mode → CRAG floor dead | HIGH | **CONFIRMED / magnitude REFINED** | floor=0.3; narrow low-signal corner |
| F-G | iteration cap dead | MED-HIGH | **CONFIRMED** | reflection opt-in gates blast |
| F-H | cascade no-op | HIGH | **CONFIRMED** | producer w/o consumer |
| F-I | ai_keys ragbot.* schema | CRIT | **CONFIRMED** | live DB: schema absent |
| F-J | count≠list match set | HIGH | **CONFIRMED** | list has fold+reverse; count doesn't |
| F-K | price-range OR/AND | HIGH | **CONFIRMED** | "any" default; parser reaches two-bound |
| F-L | per-bot embed dim ignored | HIGH | **CONFIRMED** | registry never forwards dimensions |
| F-M | REDO sentinel leak | HIGH | **CONFIRMED** | no consumer; redo_event 0 callers |
| F-N | 0.85≥0.85 + no locale signals | MED-HIGH | **CONFIRMED** | boundary + call-site both |
| F-O | mirage knobs | MED | **CONFIRMED** | neither builder sets them |
| F-P | degraded flags no reader | MED | **CONFIRMED** | write-only, undeclared |
| crag_skip_retry masked | — | **CONFIRMED not-a-bug** | pass-1 correctly said "masked" |

---

## FIX ORDER (evidence-based, EVOLVE)
1. **N1 governance gate** (pin + collection-error blocking merge) — prevents F-A/F-B recurrence; the highest-leverage single change.
2. **F-A** re-apply the 062d6fa `stats_route_skip_grounding` gate (un-breaks the failing pin, closes the HALLU breach).
3. **F-B** restore the retrieval_filter re-export import (5 modules collect) + fix/pin FastAPI for the other 3.
4. **F-E/F-F/F-G** declare the undeclared keys in `GraphState` + land the **AST pin test** (one commit closes the whole §1 class).
5. **F-C/F-K/F-J** stats SQL correctness: Decimal prices, per-column BETWEEN, shared count/list match builder (+ N2 dedup on PK).
6. **F-D** GraphRAG `bot_id→record_bot_id` + synthetic chunk_id, or gate OFF; **F-I** drop `ragbot.` schema prefix.
7. **F-M** strip/gate the speculative REDO sentinel; **F-N** `>` boundary + thread locale signals; **F-O** shared config builder; **F-P/F-H** wire-or-delete; **F-L** decide multi-dim then plumb-or-remove; **N3** log `record_bot_id`.

All fixes respect sacred rules: no app-inject/override (grounding judge + refuse text stay `oos_answer_template`-sourced; stats/synthetic chunks are DATA); content via alembic (no psql hotfix); domain-neutral (shape/value heuristics, currency-config not VND-baked); 4-key preserved; HALLU=0 restored by F-A. Nothing here rewrites the framework — every fix is a wire, a declaration, a reverted-regression re-apply, or a happy-case-box escape.
