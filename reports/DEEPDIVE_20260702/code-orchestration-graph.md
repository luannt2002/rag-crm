# DEEPDIVE — Orchestration Graph (query_graph.py + state.py + top-level orchestration/*)

- **Date**: 2026-07-02 · **Branch**: `fix-260623-ingest-expert` (HEAD `949a3a4`)
- **Scope (every line read)**: `src/ragbot/orchestration/query_graph.py` (2893 L), `state.py` (207 L), `graph_assembly.py` (208 L), `query_graph_helpers.py` (201 L), `retrieval_filter.py` (223 L), `__init__.py` (14 L). `nodes/` excluded per mandate; targeted evidence reads into `nodes/routing.py`, `nodes/grade.py`, `nodes/generate.py`, `nodes/check_cache.py`, `nodes/understand.py`, `nodes/persist.py`, `nodes/retrieve.py` where a scope-file claim required cross-checking.
- **Method**: full read + grep cross-reference + 2 runtime probes (empirical LangGraph behavior test; production journal). Every claim labeled **FACT** (evidence attached) or **HYPOTHESIS**.

---

## 0. Headline: one empirical fact reclassifies a whole bug class

**FACT (empirically verified)**: installed `langgraph==1.2.4` **drops every state key not declared in the `GraphState` TypedDict** — from the *initial input dict*, from *node return dicts*, and in-place `state[...] = x` mutations **do not cross node boundaries**.

Evidence — reproduction script run against the installed library (scratchpad `lg_key_drop_test.py`):

```
n1 declared: n1 saw undeclared_input=<MISSING>     # input key dropped before first node
n2 out: ... undeclared_return=<MISSING> inplace_key=<MISSING>
final keys: ['declared', 'out']
```

The codebase *knows* this rule — `state.py:150-197` repeats "MUST be declared here — LangGraph's reducer drops keys absent from the TypedDict schema" four times, `nodes/retrieve.py:1880` says "direct state[...] mutation would not propagate", and commit `15406d8` ("declare `_mq_speculative_variants` on GraphState (M17)") fixed exactly this class before. Yet **at least 11 keys** in the scope files are read/written without declaration (§4.1). Production journal confirms the class is live:

```
Jun 30 07:17:35 ragbot-py: {"event": "semantic_cache_preflight_no_embedding_column",
  "logger": "ragbot.orchestration.query_graph", "func_name": "_run_semantic_cache_preflight", ...}
(3 hits within 2 s; fires because `embedding_column` set in check_cache never survives to query_complexity)
```

---

## 1. Per-file summary — what each file actually does

### 1.1 `orchestration/__init__.py` (14 L)
Re-exports `build_graph`, `GraphState`, `START`, `END`. Docstring is stale: says "wires 10 nodes" (`__init__.py:3-5`) — the graph registers **18 nodes** (§3). Cosmetic drift.

### 1.2 `state.py` (207 L)
`GraphState(TypedDict, total=False)` — the single mutable state threaded through the graph. Declares 57 keys: identity (`record_tenant_id`, `record_bot_id`, `channel_type`, `workspace_id` — 4-key compliant), query lifecycle (`query`, `rewritten_query`, `sub_queries`, `original_query`), retrieval pools (`retrieved_chunks`/`reranked_chunks`/`graded_chunks`), answer contract (`answer`, `answer_type`, `answer_reason`, `citations`), config (`pipeline_config`), per-request carriers (`step_tracker`, `bot_system_prompt`, `kg_service`, `session_factory` — this is what makes the compiled-graph singleton tenant-safe), and parallel-path hand-off slots (`_understand_skipped_by_parallel`, `_mq_queries`, `_mq_speculative_variants`, `_speculative_*`, `stats_entities`).

**Because of §0, this file is a de-facto allowlist**: any key used elsewhere but missing here is silently dead. §4.1 lists the 11 missing keys.

### 1.3 `graph_assembly.py` (208 L)
Canonical DI + initial-state builder shared by all transports (verified in use: `chat_stream.py:177/297`, `test_chat/chat_routes.py:341/438/843`, worker path).
- `build_graph_di_kwargs(container)` — introspects `build_graph`'s signature (`graph_assembly.py:63-68`) so new DI params auto-resolve; 6 required deps fail-loud as `GraphAssemblyError` (`:45-54`, `:104-116`), the rest degrade to `None` with one `graph_di_assembled` event. Broad-except at `:106` is policy-compliant (re-raised typed).
- `resolve_kg_service(pipeline_config)` — GraphRAG gate on `graph_rag_mode != "disabled"` (`:125-133`).
- `build_chat_initial_state(...)` — canonical initial GraphState. **Sets 3 keys the TypedDict does not declare** (`raw_user_message` `:177`, `bot_created_at` `:192`, `bot_extra_output_tokens_per_response` `:193-195`) — all three are **dropped at `ainvoke` before the first node runs** (§0). Details §4.1-a/b/c.

### 1.4 `query_graph_helpers.py` (201 L)
Pure stateless helpers: `parse_decomposed_sub_queries` (JSON-array parse; **not** re-imported by query_graph — see §4.2 broken re-export), `expand_parent_chunks` (small-to-big parent swap + dedup), `_uuid_or_none`, `_parse_doc_type_vocabulary` (CSV/JSON-list → frozenset), `_render_captured_slots` (slot DATA rendering for owner `{captured_slots}` placeholder — sacred-rule-10 clean: data only, no instruction text), `_compute_bot_cache_version` (SHA-256 over sysprompt|oos|vocab, vocab segment appended only when non-empty for cache back-compat), `_pcfg` (pipeline-config read treating `None` as missing — the Bug #12 fix), `_is_null_lexical` (Null-Object probe by `get_provider_name()=="null"`, not isinstance — DI-friendly).

### 1.5 `retrieval_filter.py` (223 L)
Pure post-processing filters, all **alive** (used by `nodes/retrieve.py`, `nodes/rerank.py`, `nodes/grade.py`): CRAG grade vocabulary (`CRAG_GRADE_*`), `_is_retrieval_adequate` (count+fraction gate), `_remap_grade_for_intent` (compound-intent leniency: `irrelevant`→`ambiguous` for comparison/multi_hop/aggregation — never demotes), `_autocut` (score-cliff cut, default `min_gap_ratio=0.3` inline — zero-hardcode nit at `:83`), `_cliff_detect_filter` (floor + gap cut + empty-context safety keep-top-1), `_rerank_threshold_gate` (refuse gate, only for `mode=="rerank"`; empties chunks rather than injecting refuse text — QG#10 clean). **The promised query_graph re-export of these names is missing** (§4.2).

### 1.6 `query_graph.py` (2893 L) — the god-file
Module level: `_CITATION_RE` (`:278`, hex-uuid-only), cross-doc reconcile (`_absorb_fragment_attrs` `:281`, `_reconcile_cross_doc` `:297`), `retry_hybrid_with_original` (`:363`), `_lang` (`:418`, DB language-pack rows preferred, static `get_pack` fallback), `_resolve_xml_wrap_enabled` (`:435`), `_resolve_generate_schema` (`:481`, flat vs sub-answers schema per intent), `_understand_greeting_short_circuit` (`:505`, **orphan** — §4.3), `_required_channel_type` (`:553`, fail-loud identity), `_resolved_oos_template`/`_oos_text` (`:563/:597`, 7-tier resolver hand-off), `_check_embed_model_consistency` (`:610`), `_resolve_and_complete` (`:638`, **dead** — §4.3), `_resolve_stats_keyword_synonyms` (`:673`, owner-taught synonym map), async grounding judge scheduler (`:701-834`).

Inside `build_graph` (`:837-2850`): `_audit` (best-effort), `_resolve_corpus_version` (`:894`, broken memo — §4.1-f), `_invoke_llm_node` (`:919`, streaming + speculative-router gate + TTFT capture + SSE back-pressure), `_invoke_structured_llm_node` (`:1160`, JSON-mode via capability-driven `_call_with_schema`), `_prewarm_embedding_cache` (`:1324`), `_embed_query` (`:1386`, HyDE swap + prefix + Redis cache), `_run_speculative_retrieve` (`:1546`), `cache_check_and_understand_parallel` (`:1655`, up to 4 concurrent tasks), `_run_multi_query_expansion` (`:1867`, 7 gates + entity path + dedup), `rewrite_and_mq_parallel` (`:2144`), `_do_stats_lookup` (`:2207`, B-AGG count / keyword-list / superlative / price-range SQL routes + synthetic chunk build + cross-doc reconcile), 3 pre-retrieval telemetry branches (`:2619-2709`), then node registration + edges (`:2724-2848`) and the compiled-graph singleton `get_graph` (`:2868`, first-caller-wins by design; callsite drift closed by graph_assembly).

---

## 2. Complete pipeline diagram (every node, edge, conditional route)

18 registered nodes (`query_graph.py:2725-2766`). Conditional deciders live in `nodes/routing.py` (read for evidence).

```
START
  └─► guard_input                                            (:2768)
        ├─ _input_blocked: input-stage blocked flag ──────► persist        (routing.py:49-53)
        └─ else ──────────────────────────────────────────► cache_check_and_understand_parallel
              [inside node, flag pipeline_parallel_cache_understand_enabled — DEFAULT **ON**
               (constants _11:290; docstring at :1659 falsely says OFF):
                 check_cache ∥ understand_query
                 ∥ speculative retrieve  (speculative_retrieve_enabled, default OFF, _20:129)
                 ∥ speculative multi-query (pipeline_multi_query_speculative_enabled, default OFF, _20:145)]
  cache_check_and_understand_parallel — _cache_route (routing.py:56-62)
        ├─ cache hit + answer ─────────────────────────────► persist
        ├─ merge_condense_router (default True, inline) ───► understand_query   (short-circuits: _understand_skipped_by_parallel)
        └─ False (legacy) ─────────────────────────────────► condense_question ─► router
  understand_query — _understand_query_route (routing.py:65-90)
        ├─ adaptive_router_l1_enabled (default True, _14:168)
        │   ∧ intent ≠ multi_hop ∧ sub_queries < 2 ───────► query_complexity
        └─ else → _router_route:
              ├─ intent ∈ {multi_hop, comparison} ∧ decompose_enabled(True inline)
              │   ∧ tokens ≥ decompose_min_tokens ∧ confidence ≥ decompose_confidence_gate(0.7) ─► decompose
              ├─ intent ∈ skip_rewrite_intents (factoid, greeting, …) ─► retrieve
              └─ else ─► rewrite_and_mq_parallel
  condense_question ─► router — _router_route (same 3-way as above)      (:2804-2809)
  query_complexity  [3 parallel branches: L1 heuristic classifier + router_select_model telemetry
                     + semantic_cache preflight (:2619-2716)]
        — _complexity_route (routing.py:93-97)
        ├─ complexity_label == "complex" ──────────────────► adaptive_decompose ─► retrieve  (:2803)
        └─ else → _router_route (3-way as above)
  rewrite_and_mq_parallel ─► retrieve                        (:2810)
        [inside: rewrite ∥ _run_multi_query_expansion → _mq_queries; skipped whole for
         exact code-lookup (parse_code_query) or ≥2 pre-existing sub_queries]
  decompose ─► retrieve                                      (:2811)
  retrieve — _retrieve_route (routing.py:219-241)
        ├─ 0 chunks ───────────────────────────────────────► generate   (refuse short-circuit downstream)
        ├─ retrieve_mode startswith "stats" ───────────────► generate   (SKIPS rerank+mmr+grade entirely)
        ├─ graph_rag_mode == "disabled" (default) ─────────► rerank
        ├─ graph_rag_mode == "adaptive" ∧ intent ∉ {multi_hop, aggregation} ─► rerank
        └─ else ──────────────────────────────────────────► graph_retrieve ─► rerank   (:2817)
  rerank ─► mmr_dedup ─► neighbor_expand ─► grade            (:2818-2823; neighbor_expand no-op unless flag)
  grade — _grade_route (routing.py:152-194)
        ├─ crag_skip_retry flag ──► generate   [**DEAD**: key dropped by reducer — §4.1-e; falls through
        │                            to retrieval_adequate=True which grade also returns → same target]
        ├─ retrieval_adequate ────► generate
        └─ inadequate ∧ grade_retries < max_grade_retries(1) ∧ top_score < crag_skip_retry_above_score(0.7)
                                   ─► rewrite_retry ─► retrieve   (loop; bounded by grade_retries + recursion_limit)
  generate ─► critique_parse ─► guard_output                 (:2836-2837; critique no-op unless self_rag flag)
  guard_output — _output_blocked (routing.py:197-216)
        ├─ output-stage blocked ──► persist
        ├─ NOT reflection_enabled (default False) ─────────► persist
        ├─ intent ∈ skip_reflect_intents ──────────────────► persist
        └─ else ──────────────────────────────────────────► reflect
  reflect — _reflect_route (routing.py:244-252)
        ├─ _total_graph_iterations ≥ max_total_graph_iterations(8) ─► persist  [**DEAD** — counter always 0, §4.1-d]
        ├─ no answer ─────────────────────────────────────► generate  (loop)
        └─ answer ────────────────────────────────────────► persist
  persist ─► END                                             (:2848)
```

Loops and their real bounds: `grade→rewrite_retry→retrieve` bounded by `grade_retries` (declared key, survives). `reflect→generate` bounded **only** by transport `recursion_limit` (50, `chat_worker/pipeline_config.py:97`, `chat_stream.py:346`) because the intended `max_total_graph_iterations` cap is dead (§4.1-d).

---

## 3. FINDINGS

Severity-ranked. Axis tags: [multi-doc] [multi-bot] [multi-format] [multi-tenant] [T1] [T2] [T3].

### F1 · CRITICAL · [multi-bot][T1] Undeclared-GraphState-key class: ≥11 keys silently dead (empirical + runtime evidence)
**FACT** (§0 reproduction + journal). Individual instances in §4.1. The most damaging: paid output-token budget always 0 (F3), XML-wrap rollout dead (F5), loop cap dead (F6), HALLU "degraded" flags unread (F8). One structural fix (declare the keys, return instead of mutate) closes all of them; a schema-vs-usage pin test would prevent recurrence (the M17 commit fixed one instance without adding the guard).

### F2 · HIGH · [T3→T1] Broken re-export contract — 7 unit-test files fail at collection on this branch
**FACT**. `query_graph.py:273-277` comments that CRAG vocab + chunk filters are "re-exported here so existing call sites + test imports … are unchanged", and `retrieval_filter.py:9-11` + `query_graph_helpers.py:8-10` promise the same — **but no import statement exists**.
```
$ pytest tests/unit/test_cliff_detect_filter.py
ImportError: cannot import name '_cliff_detect_filter' from 'ragbot.orchestration.query_graph'
1 error during collection
```
Symbols verified missing: `_cliff_detect_filter`, `_rerank_threshold_gate`, `CRAG_GRADE_IRRELEVANT`, `_CRAG_VALID_GRADES`, `parse_decomposed_sub_queries`. Broken test files: `tests/unit/test_cliff_detect_filter.py`, `test_reranker_threshold_gate.py`, `test_crag_three_states.py`, `orchestration/test_crag_compound_query.py`, `test_query_decompose.py`, `test_output_guardrail_tuning.py`, `test_t2_perf_fixes.py`. Consequence: the regression pins for the cliff filter, refuse-threshold gate and CRAG vocabulary are **not running** — those invariants are currently unguarded.

### F3 · HIGH · [multi-bot][T1] Paid `extra_output_tokens_per_response` always 0
**FACT**. `graph_assembly.py:193-195` puts `bot_extra_output_tokens_per_response` into the initial state; the key is not in `GraphState` → dropped at `ainvoke` (§0). `nodes/generate.py:738-740` reads `state.get("bot_extra_output_tokens_per_response", 0)` → always 0 → `compute_output_cap` = system default for every bot. Failure scenario: a paying bot configured with `extra_output_tokens_per_response=2048` still gets answers truncated at the platform default cap; the owner sees the paid knob not working with zero error anywhere.

### F4 · HIGH · [multi-format][multi-doc][T1] `int(_price)` truncates NUMERIC prices in the stats synthetic chunk
**FACT**. `document_service_index.price_primary` is `NUMERIC nullable` (`stats_index_repository.py:20`). `_do_stats_lookup` renders `f"{_name}: {int(_price)}"` / `f"price: {int(_price)}"` and dedups on `int(_price)` (`query_graph.py:2391, 2411-2419`). The adjacent comment claims currency-neutrality ("the corpus may be in any currency", `:2403-2405`) — but any decimal-currency corpus (USD 19.99, EUR 12.50) is fed **19** / **12** as "grounded" context. Failure scenario: US-tenant XLSX price list, query "how much is X" via stats route → bot confidently answers a wrong price sourced from a synthetic chunk with score=1.0 that skipped rerank AND grade (F11). This is an anti-HALLU *misinterpret*-class corruption injected at the retrieval tier. Also distinct products priced 19.99 vs 19.50 collide in the dedup key `(name, 19)`.

### F5 · HIGH · [multi-bot] XML-wrap feature 100% unreachable in production
**FACT**, two independent kills:
1. Explicit knob: `xml_wrap_enabled` is populated by **neither** pipeline_config builder (verified key-diff vs `chat_worker/pipeline_config.py` and `test_chat/_pipeline_config.py`) → `_pcfg(state,"xml_wrap_enabled",None)` at `query_graph.py:450` is always `None`.
2. Date default: `bot_created_at` is dropped by the reducer (§0) → `query_graph.py:453-455` always returns `DEFAULT_XML_WRAP_ENABLED=False` (constants `_00:88`).
So `XML_WRAP_DEFAULT_ON_FROM_DATE="2026-05-18"` (constants `_00:92`) and the whole resolution chain documented at `:436-449` never fire; `nodes/generate.py:622,641` `_xml_wrap` is always False. Unit tests calling the helper with a plain dict pass, production never activates — classic built-but-not-wired.

### F6 · MEDIUM-HIGH · [T1] Graph-iteration cap is dead; loop safety is only `GraphRecursionError`
**FACT**. `nodes/grade.py:76-84,536` accumulates and returns `_total_graph_iterations`; the key is undeclared → dropped → every grade pass reads 0 again (`grade.py:76`), and `_reflect_route` (`routing.py:245-249`) compares 0 ≥ 8 forever. `crag_iteration_cap` and `graph_iteration_cap_reached` warnings can never fire. Failure scenario: reflection-enabled bot + empty `oos_answer_template` (legal per sacred rule #3) + 0-chunk turn → `generate` produces empty answer → `_reflect_route` → `generate` → … until transport `recursion_limit=50` raises `GraphRecursionError` → 500 to the caller instead of the designed graceful persist. **HYPOTHESIS** on frequency (needs a reflection-enabled bot); the dead counter itself is FACT.

### F7 · MEDIUM-HIGH · [multi-bot][multi-doc] `cross_doc_reconcile_enabled` is a mirage knob — force-ON, no opt-out
**FACT**. `query_graph.py:2380` reads `_pcfg(state, "cross_doc_reconcile_enabled", True)` (inline `True`, not a constant — zero-hardcode violation for a behavior toggle) and the key is populated by **neither** config builder (key-diff verified) → `_reconcile_cross_doc` runs for **every bot on every stats turn** with no off switch, while the comment `:2379` claims "Per-bot opt-out". Any tenant whose catalog triggers a false digit-key merge (F12) cannot disable it.

### F8 · MEDIUM · [T1] `retrieval_degraded` / `embed_degraded` HALLU-safety flags: written in-place, zero readers
**FACT**. Set at `query_graph.py:403` and `:1500` with comments promising "the degraded flag distinguishes error-empty from genuine no-match (HALLU-safety)" and "flag the turn degraded so the answer path won't fabricate". Project-wide grep: **no reader exists** anywhere in `src/` or `tests/`, and the in-place write wouldn't survive the node boundary anyway (§0). The advertised protection (answer path treating error-empty differently from no-match) does not exist — a retrieval outage turn is indistinguishable downstream from a real "corpus has nothing" turn.

### F9 · MEDIUM · [multi-bot][T2] `skip_understand_for_greeting` feature: helper + flag + schema + 20 tests, zero callers
**FACT**. `_understand_greeting_short_circuit` (`query_graph.py:505-550`) is referenced only by `tests/unit/orchestration/test_skip_understand_for_greeting.py`. No node calls it (understand has a separate Layer-1 heuristic, `nodes/understand.py:109-112`). A bot owner setting `plan_limits.skip_understand_for_greeting=true` gets nothing. Built-but-not-wired (Stream B3).

### F10 · MEDIUM · [multi-bot][T1] Embedding-cache key derived from system_config while the vector comes from the per-bot resolved model
**FACT** (divergence possible), **HYPOTHESIS** (production hit). `_embed_query` builds the Redis cache key from `_pcfg("embedding_provider"/"embedding_model"/"embedding_dimension")` (`query_graph.py:1436-1439`) but computes the vector via `model_resolver.resolve_runtime(purpose="embedding")` spec (`:1450-1475`). The two sources can differ — that is precisely what `_check_embed_model_consistency` (`:610-635`) warns about. When they differ, vectors are cached under the *config* model tag: flip the per-bot binding and cached vectors from the old model keep serving under the same key (wrong vector space / dimension for pgvector compare). Note `embedding_provider` is additionally absent from the worker builder (key-diff), so the worker cache key always uses `DEFAULT_EMBEDDING_PROVIDER`. Same scheme in `_prewarm_embedding_cache` (`:1341-1343`). Also multi-bot: `state["embedding_column"]` is only ever assigned the constant `DEFAULT_EMBEDDING_COLUMN` (`:1337, :1393`) — a second embedding column can never be selected per bot despite the plumbing that forwards the key (`:388-389`).

### F11 · MEDIUM · [T1] Stats route: score=1.0 synthetic chunk bypasses rerank+grade; sentinel id unciteable by both citation regexes
**FACT**. Synthetic chunks carry `score: 1.0` + `chunk_id: "stats_index_synthetic"` (`query_graph.py:2259-2264, 2453-2463`; constant `_21:109`). `_retrieve_route` short-circuits `retrieve_mode=stats*` straight to generate (`routing.py:232-233`) and grade has a stats bypass (`grade.py:100-112`) — intentional (fuzzy grader rejects SQL truth) but it means **no quality gate at all** sits between the range-parser/SQL and the LLM: `_rerank_threshold_gate` never applies, cliff filter never applies. Combined with F4 (truncated numbers) and F12 (merged fragments), whatever the stats builder emits is authoritative context. Citation side: `_CITATION_RE = r"\[chunk:([0-9a-f\-]+)\]"` (`query_graph.py:278`) and generate's history-strip regex (`generate.py:690`) are hex-only → an LLM-emitted `[chunk:stats_index_synthetic]` marker is (a) not extractable as a citation, (b) counted in neither valid nor invalid metrics, (c) **not stripped from conversation history** re-fed to later turns. Citations for stats answers survive only via post-hoc top-chunk attribution (`generate.py:895-910`).

### F12 · MEDIUM · [multi-doc][multi-format] `_reconcile_cross_doc` is happy-case-shaped for one specific two-sheet corpus
**FACT** on shape, **HYPOTHESIS** on collision frequency. `query_graph.py:297-360`: an anchor qualifies only if its alias mega-cell lives at `attributes_json["question"]` as a comma-separated string (`:318-324`) — i.e., the ingest happened to map the alias column into a header literally named/mapped `question`. Grouping key = digits-only, ≥5 (`:313-314, 326-329`). Silent failure modes: (a) corpora whose alias column maps to any other attribute name get **no reconcile at all** (fragments stay split → the exact "giá + tồn + ngày về" deflection the function exists to fix); (b) two different products whose spec digits coincide (e.g. size-only codes `205/55R16` variants distinguished by letter suffixes stripped by `_digkey`) → fragment fields of product A absorbed into anchor B (`_absorb_fragment_attrs`, anchor-wins mitigates but foreign fields like arrival-date/stock are added). Skip-key literals differ between absorb (`:290` includes `"image"`, `col_` prefix) and the row renderer (`:2438` excludes neither `image` nor `col_`) — inline magic strings, inconsistent.

### F13 · MEDIUM · [multi-tenant] RLS tenant threading is adapter-signature-conditional — silent degrade on a security parameter
**FACT** on mechanism, **HYPOTHESIS** on exposure (shipped `PgVectorStore` accepts the param). Three call sites forward `record_tenant_id` only `if "record_tenant_id" in sig.parameters` (`query_graph.py:391-392, 1595-1600, 1632-1637`). An adapter that misses the kwarg silently runs hybrid search **without** `SET LOCAL app.tenant_id` — no warning, no fail-loud. For a security-relevant parameter the pattern should be invert-and-fail (or at minimum warn once). Positive: stats/doc lookups correctly use `record_bot_id`-only per the identity rule (`:2242, 2272-2303, 2480-2483`), and `_required_channel_type` fails loud (`:553-560`).

### F14 · MEDIUM · [multi-locale/multi-bot] Language-specific logic hardwired outside language packs
**FACT**:
- Vietnamese structural prefilter `detect_vn_structural_anchor` / `build_vn_structural_like_clauses` imported from `shared/chunking` and applied in the speculative path (`query_graph.py:106-108, 1643-1648`) — name and semantics are vi-only; a Khmer/English legal bot gets no structural anchoring (silent recall gap), and the vi implementation runs regardless of `state["language"]`.
- `DEFAULT_GREETING_PATTERNS` are vi/en regex literals in constants (`_17_pipeline_audit.py:79-81`) rather than `language_packs[locale]` data (per-bot override exists but the default is locale-biased — moot today because F9 makes the whole gate dead).
- `_lang` fallback `get_pack(language)` (`query_graph.py:433`) → static `PACKS = {"vi", "en"}` (`i18n.py:665`); any other locale gets the empty-signal pack — graceful, but the static vi pack carries a non-empty hardcoded `refuse_message` (`i18n.py:522`) which sits in tension with sacred rule #3 ("empty string nếu bot không set") whenever the 7-tier OOS chain bottoms out at the i18n tier.
- Good counter-example: `_run_multi_query_expansion` threads `state["language"]` + `language_pack_service` into expansion (`:2045-2073`) — this is the pattern the above should follow.

### F15 · MEDIUM · [multi-bot] test_chat pipeline_config omits `bot_custom_vocabulary` — QA/prod behavior split
**FACT** (key-diff): worker builder sets it (`chat_worker/pipeline_config.py:286`), test_chat builder does not → on the internal test UI, `_resolve_stats_keyword_synonyms` (`query_graph.py:686`) always returns `[]` and `_compute_bot_cache_version`'s vocab segment (`query_graph_helpers.py:150-161`) never contributes. A bot that answers correctly in production (synonym-expanded stats LIST route) can refuse in the QA harness and vice-versa; vocabulary edits don't bust caches on the test path.

### F16 · MEDIUM · [multi-doc][T1] Aggregation coverage is price-only; count-within-range falls through
**FACT**. `_do_stats_lookup` handles: pure keyword-count (real `COUNT(*)`, `:2236-2266`), keyword list (+list-all fallback `:2267-2288`), max/min on price (`:2289-2296`), price-range (`:2298-2304`). The comment at `:2233-2235` admits "a range+count ('dưới 2tr có bao nhiêu') keeps the existing range handling … (count_by_price_range wiring is Phase 3)" — so a count-under-bound query dumps up to `stats_limit=100` rows (`_21:74`) and lets the LLM count them: miscount risk, and silent undercount for catalogs >100 rows (the exact cap-dishonesty the B-AGG COUNT(*) fix closed for the pure-count case). Aggregations over any non-price numeric column (weight, duration, stock) have no structured route at all and fall to top-k vector retrieval (incomplete enumeration).

### F17 · LOW-MEDIUM · [T3] Dead code cluster inside the god-file
All **FACT** (grep-verified zero callers):
- `_resolve_and_complete` (`query_graph.py:638-670`) — dead function.
- `_SUPERLATIVE_ENRICHER = _SuperlativeContextEnricher()` (`:238`) — dead module singleton (and language-pinned to default); the live path uses `_get_superlative_enricher(lang)` inside `nodes/retrieve.py:1870`.
- L3 metadata-extractor soft-import block (`:127-146`) incl. the `DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL` re-bind gymnastics — dead here; the real consumer is `nodes/retrieve.py:139-148` with its own import.
- 6 dead metric imports: `citation_validation_fail_total` (`:120-125`), `decompose_skipped_low_confidence_total`+`intent_classifier_confidence` (`:155-162`), `llm_resolved_purpose_total` (`:173-178`), `cliff_drop_total` (`:180-185`), `_grounding_fail_total_metric` (`:187-192`) — imported+None-guarded, never referenced again in this module (live copies exist in the node modules).
- `raw_user_message` (graph_assembly.py:177) — dead key; its promise ("slot extraction reads THIS", `:174-177`) is false (dropped, §0), but the 2026-06-15 slot fix still holds via the `original_query` fallback (`generate.py:250-254`; `original_query` is set pre-condense by `condense_question.py:100` and `understand.py:236-238`). Removing that fallback would silently re-open the bug.
- `_VALID_INTENTS` (`:415`) — used by tests only (acceptable as a pin surface, worth a comment).

### F18 · LOW · [T3] Docstring/config-default drift on the two parallel wrappers
**FACT**. `cache_check_and_understand_parallel` docstring: "Gated by pipeline_parallel_cache_understand_enabled (default OFF)" (`:1659`); `rewrite_and_mq_parallel`: "(default OFF)" (`:2147`). Constants: both **True** (`_11:289-290`). Same drift class as the 2026-06-30 config-default-drift fix (commit `c1e96b9` note). Also stale: `_run_multi_query_expansion` comment "query_complexity node runs AFTER this on the graph" (`:1891-1893`) — on the default L1 path query_complexity runs *before* rewrite_and_mq_parallel (§2); the inline re-classification is redundant-but-harmless. `__init__.py:3` "10 nodes" vs 18.

### F19 · LOW · [T2] `_corpus_version` memo never survives → duplicate Redis/DB resolves per turn
**FACT**. `check_cache` returns `"_corpus_version"` (`nodes/check_cache.py:94,173,199`) but the key is undeclared → dropped; `persist.py:183-185`'s `state.get("_corpus_version") or await _resolve_corpus_version(state)` always takes the second branch, and the retrieve port path re-resolves too (`query_graph.py:1590`, `retrieve.py:373,1000`). Correctness holds (service re-fetch), cost = 2-3 redundant lookups/turn; consistency edge: an ingest completing mid-turn can make cache-store write under a different corpus_version than cache-lookup used.

### F20 · LOW · [T3] Broad `contextlib.suppress(..., Exception)` in parallel-cancel cleanup + minor observability nits
**FACT**:
- `suppress(asyncio.CancelledError, Exception)` ×8 (`query_graph.py:1732-1754, 1761-1766`) and `suppress(CancelledError, TimeoutError, Exception)` (`:1841-1843`) — swallow *any* failure of the awaited sibling task with no log line. The cleanup context is policy-tolerated, but a task that failed for a non-cancel reason (programmer bug in understand) disappears silently; the sequential path would at least log it.
- `_run_query_complexity` logs `bot_id=str(state.get("bot_id"))` (`:2638`) — `bot_id` is never a GraphState key → always empty string in the `adaptive_router_l1` event.
- HyDE passes `trace_id=state.get("trace_id","")` (`:1421`) — key never set → always "".
- In-place writes lost to §0: `fanout_bypassed` (`:1926,1949,2163,2183` — retrieve recomputes its own local copy at `retrieve.py:1188-1190`, so only the observability promise in `state.py:115-119` is false), `multi_query_skipped_simple` (`:1904`, no reader), `grounding_async_task` (`:830`, unreachable from final state).
- `semantic_cache_preflight_no_embedding_column` warning fires on every query_complexity traversal (journal-verified 2026-06-30) — a permanent false alarm caused by §0, and its docstring misdiagnoses the cause as "embedder DI mis-configured" (`:2687-2694`).

---

## 4. Supporting inventories

### 4.1 Undeclared-key ledger (the F1 class)

| Key | Written at | Read at | Effect after drop |
|---|---|---|---|
| a. `raw_user_message` | graph_assembly.py:177 (input) | generate.py:251 | dead; mitigated by `original_query` fallback |
| b. `bot_created_at` | graph_assembly.py:192 (input) | query_graph.py:453 | XML-wrap date default dead (F5) |
| c. `bot_extra_output_tokens_per_response` | graph_assembly.py:193 (input) | generate.py:739 | paid token budget always 0 (F3) |
| d. `_total_graph_iterations` | grade.py:83,108,156,268,413,536 (return) | grade.py:76, routing.py:245 | loop caps dead (F6) |
| e. `crag_skip_retry` | grade.py:109,154 (return) | routing.py:167 | fast-path dead; masked by `retrieval_adequate` |
| f. `_corpus_version` | check_cache.py:94,173,199 (return) | query_graph.py:903, persist.py:183 | memo dead (F19) |
| g. `embedding_column` | query_graph.py:1337,1393 (in-place) | query_graph.py:388, :2697 | cross-node loss; permanent preflight false alarm (journal) |
| h. `retrieval_degraded` | query_graph.py:403 (in-place) | — none | F8 |
| i. `embed_degraded` | query_graph.py:1500 (in-place) | — none | F8 |
| j. `multi_query_skipped_simple` | query_graph.py:1904 (in-place) | — none | dead observability |
| k. `grounding_async_task` / `trace_id` / `bot_id` | :830 / — / — | tests / :1421 / :2638 | unreachable / always "" / always "" |

(`fanout_bypassed` IS declared but its module-helper writes at `:1926,1949,2163,2183` are in-place → lost; only `retrieve.py`'s own copy matters.)

### 4.2 `_pcfg` key audit (task question: "are they all real per-bot knobs?")
51 distinct keys read in scope (multiline-aware extraction). Cross-checked against both pipeline_config builders (`chat_worker/pipeline_config.py`, `test_chat/_pipeline_config.py` — static whitelists, no generic plan_limits passthrough):
- **Populated in both** (real knobs): 47/51 — incl. all multi_query_*, speculative_*, decompose_*, crag_*, top_k, graph_rag_mode, reflection_enabled, merge_condense_router, adaptive_router_l1_enabled, generation_temperature, hyde_enabled, embedding_model/dimension/query_prefix, bot_name, oos_answer_template.
- **Mirage knobs** (read but populated nowhere → per-bot control impossible): `cross_doc_reconcile_enabled` (F7), `xml_wrap_enabled` (F5).
- **Transport-asymmetric**: `bot_custom_vocabulary` (worker only — F15), `embedding_provider` (test_chat only — worker always falls to `DEFAULT_EMBEDDING_PROVIDER`, feeds the F10 cache-key issue).
- **Inline literal defaults instead of constants** (zero-hardcode nits): `structured_subanswer_enabled` False (`:496`, admitted), `speculative_streaming_enabled` False (`:1000`), `speculative_hallu_verify_enabled` False (`:1016`), `cross_doc_reconcile_enabled` True (`:2380`), `merge_condense_router` True (routing.py:60), `decompose_enabled` True (routing.py:112), `graph_rag_mode` "disabled" literal (routing.py:234), `_STRUCTURED_SUBANSWER_INTENTS` local frozenset (`:476-478`, admitted), `_autocut` gap 0.3 (retrieval_filter.py:83).

### 4.3 Dead / orphan / built-but-not-wired summary
F2 (missing re-exports breaking 7 test files), F5 (xml wrap), F8 (degraded flags), F9 (greeting skip), F17 (dead function/singleton/imports), 4.1-a/j/k.

### 4.4 CLAUDE.md compliance snapshot (scope files only)
- **QG#10 no app-inject/override**: PASS with two documented judgment calls — stats synthetic chunk & count-fact are retrieved DATA not instruction (`:2250-2254, 2360-2363`); `_render_captured_slots` emits data only; `_rerank_threshold_gate` empties chunks, never writes refuse text. No `state["answer"]` writes outside generate/critique contract in scope.
- **4-key identity**: PASS — `_required_channel_type` fail-loud; internal queries `record_bot_id`-only per rule; tenant threading concern = F13.
- **Zero-hardcode**: mostly constants-driven; violations listed in §4.2 bullet 4 (behavior-toggle defaults inline).
- **Domain-neutral**: PASS — no brand/tenant literals found in scope; digit-shape/value-shape heuristics only. Language-neutrality gaps = F14.
- **No version-ref**: PASS in scope (`LEGACY_CORPUS_VERSION_TAG` value "latest" is fine; `DEFAULT_EMBEDDING_FALLBACK_VERSION="v1"` is a data namespace tag, borderline).
- **Broad-except policy**: 3 `# noqa: BLE001` sites are within policy (background wrapper `:748`, speculative wrapper `:1651`, re-raise `graph_assembly:106`); the unlogged `suppress(Exception)` cluster = F20.
- **Async rules**: gather/cancel discipline generally good (layered tasks, cancel+collect, no gather across sessions in scope).

### 4.5 What is genuinely solid (đừng đụng)
Compiled-graph singleton with per-request data on state (`:2853-2884`) — correct tenant-safe design; graph_assembly's signature-introspecting DI (fail-loud required / warn optional); `_pcfg` None-as-missing; the B-AGG real `COUNT(*)` cap-honesty fix (`:2236-2247`); B-ROLEBLIND price-less-row fallthrough (`:2307-2325`); doc-dump suppression when a synthetic record exists (`:2464-2501`); `_cliff_detect_filter` empty-context safety; streaming path's SSE back-pressure + usage capture (`:1088-1135`).

---

## 5. Suggested fix order (evidence-based, not yet executed)
1. **F2** — restore the re-export lines (1-line import each) → un-breaks 7 test files immediately; then run the restored pins.
2. **F1 class** — declare the 11 keys in `GraphState`, convert in-place writes to node returns; add a pin test that greps `state["…"] =` / returned keys against the TypedDict (prevents recurrence — this is the 3rd occurrence of the class after M17 and the `_mq_queries` comment block).
3. **F4** — preserve `Decimal`/str rendering of prices (no `int()`); add a decimal-price stats fixture.
4. **F5/F7/F15** — add the 3 missing keys to both pipeline_config builders.
5. **F10** — derive the embed-cache key from the *resolved spec*, not `_pcfg`.
6. F8/F9 — either wire the flags/helper or delete them + their comments (they currently document safety that doesn't exist).
