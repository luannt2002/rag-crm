# DEEPDIVE 2026-07-02 — `src/ragbot/orchestration/nodes/` + non-query-graph orchestration files

Reader scope: every file in `src/ragbot/orchestration/nodes/` (30 files) plus `graph_assembly.py`,
`query_graph_helpers.py`, `retrieval_filter.py`, `state.py`, `system_prompts/*`, `__init__.py`.
`query_graph.py` belongs to the query-graph reader; it was consulted ONLY for wiring evidence
(helper closures threaded into the nodes) — line refs to it are marked `[qg]`.

Method: full read of every in-scope file, cross-checked against `shared/constants/*`,
`shared/i18n.py`, `shared/query_range_parser.py`, `shared/chunking/vn_structural.py`,
`application/services/heuristic_intent_classifier.py`, live DB (`system_config`), one pytest run,
and **two empirical LangGraph probes** run against the project's own `GraphState` (langgraph 1.2.4).
Every claim below is labeled **FACT** (evidence attached) or **HYPOTHESIS** (needs a named verify step).

---

## 0. Executive summary

1. **Systemic state-schema drift (FACT, runtime-probed)** — LangGraph 1.2.4 with `StateGraph(GraphState)`
   **drops every key not declared in the `GraphState` TypedDict**, both from the initial input dict and
   from node returns. At least 12 keys used across node boundaries are undeclared:
   `rerank_score_mode`, `_total_graph_iterations`, `crag_skip_retry`, `crag_skip_reason`,
   `grade_timeout_fallback`, `intent_corrected`, `citations_source`, `action_state`,
   `resolved_answer_model`, `raw_user_message`, `bot_created_at`, `bot_extra_output_tokens_per_response`,
   `_corpus_version`, `embedding_column`, `trace_id`, `embed_degraded`. Consequences range from
   a dead CRAG score-calibration gate (HALLU-relevant) to a dead paid-tier output-token feature.
2. **Stats-route grounding skip is unconditional and its pin test FAILS on this tree (FACT, pytest run)** —
   `guard_output` skips the HALLU grounding judge for every `retrieve_mode=stats*` answer with no
   per-bot escape; constants + `bot_limits` schema say the default must be grounding-ON.
3. **Cascade routing is built-but-not-wired (FACT)** — the resolved tier model is written to a state key
   nothing reads; the actual LLM call always resolves by binding purpose.
4. **GraphRAG chunks can never reach the LLM prompt (FACT)** — synthesized graph chunks carry
   `chunk_id=None` and `generate` drops falsy-id chunks from `<documents>`.
5. **The stats/vector routing in `retrieve` is shape-driven and largely well-guarded, but the whole
   structured-lookup feature only exists for `vi`+`en` locales**, and several early-return paths
   (race winner, speculative hit) bypass the permission pre-filter.
6. Several per-locale abstractions exist but are not passed at the call site (heuristic classifier
   signals), and one heuristic threshold boundary (`0.85 >= 0.85`) defeats the documented
   "borderline → force LLM" contract.

---

## 1. Per-file inventory — what each file does + pipeline connection

### 1.1 `nodes/retrieve.py` (1886 lines) — hybrid-search core
Wired via `functools.partial` in `build_graph` [qg:2509-2533]; runs after
`rewrite_and_mq_parallel`/`decompose`/`adaptive_decompose`, before `graph_retrieve`/`rerank`
(routing in `nodes/routing.py::_retrieve_route`). Key stages in body order:

1. **Stats-index routing (B3 Self-Query)** — retrieve.py:180-618. See §2 for exact trigger conditions.
2. **Pre-seeded fallback** when `vector_store is None` — retrieve.py:620-622.
3. **Speculative-retrieve gate** — retrieve.py:631-666: reuses pre-raced chunks when
   `cosine(raw_embed, rewritten_embed) >= speculative_similarity_threshold`.
4. **VN preprocessing** — abbreviation expansion (per-bot overrides + custom vocab), retrieve.py:668-695.
5. **Per-intent `top_k` + aggregation-keyword promote** — retrieve.py:699-730.
6. **Generic vocab expansion** (per-locale expander + bot synonyms) — retrieve.py:732-759.
7. **Metadata filters, 3 layers** — LLM intent extractor (gated on 2 flags), regex
   `metadata_filter_strategy` (DI strategy, LLM keys win collisions), Layer-3 LLM extractor
   (default OFF; prompt from `language_packs`) — retrieve.py:761-893.
8. **`_embed_batch_queries` / `_run_hybrid_for_query`** inner closures — retrieve.py:895-1124:
   3-protocol adaptive dispatch (`HybridQuery` port; kwargs-style `hybrid_search`; plain `search`)
   via `inspect.signature` feature-probing; threads `record_tenant_id` for RLS when the adapter
   accepts it; per-intent RRF weights; VN structural pre-filter (`detect_vn_structural_anchor`).
9. **Decompose/multi-query fan-out + RRF merge** — retrieve.py:1126-1401 (S2 bypass gates,
   preset paraphrases from `_mq_queries`/`_mq_speculative_variants`, batch pre-embedding).
10. **Metadata relax retry / verbatim-query fallback / multi-stage fallback chain (S8)** —
    retrieve.py:1420-1619.
11. **Diacritic-restoration supplementary search** — retrieve.py:1621-1681.
12. **Lexical BM25 branch + RRF fuse** (Null-object gated) — retrieve.py:1683-1745.
13. **Permission pre-filter** (`access_groups` overlap, default-public) — retrieve.py:1747-1765.
14. **Parent-child expansion (small-to-big) via direct SQL** — retrieve.py:1767-1814 —
    tenant scoping via `JOIN documents d ... d.record_bot_id = :rbid` (bot-scoped, OK per identity rule).
15. **Autocut, audit events, superlative enrichment (`context_base`)** — retrieve.py:1816-1883.

### 1.2 `nodes/understand.py` (314) — merged condense+router
Fires after `check_cache` (or is skipped when the parallel wrapper already ran it).
Layer 0 = Redis memo (`understand_query_cache`, bot-scoped); Layer 1 = regex heuristic
(`classify_heuristic`); Layer 2 = LLM structured output (`UnderstandOutput`), with in-prompt
history condense when `has_meaningful_history`. Writes `intent`, `intent_confidence`,
`intent_source`, and condensed `query`/`original_query`.

### 1.3 `nodes/grade.py` (567) — CRAG-lite grader
After `neighbor_expand`. Early exits: iteration cap (grade.py:76-84), **stats-route bypass**
(grade.py:99-111), **smart-skip on high top score** (grade.py:120-162). Batch structured grade →
per-chunk bounded-gather fallback → all-ambiguous fallback. Adequacy = count+fraction gate
(`retrieval_filter._is_retrieval_adequate`); all-irrelevant path has a score-floor fallback whose
mode calibration is broken by the state-drop bug (§4.1). Intent self-correction OOS→factoid.

### 1.4 `nodes/generate.py` (1084) — answer generation
After `grade` (via `_grade_route`). Stages: action/booking Tier-2 state (load/extract/merge/lock
prices), refuse short-circuit on 0 chunks (bypassed for chitchat + action bots), cascade routing
wire (§4.3), prompt compression, adaptive context sizing, LitM reorder, token-opt, char cap,
XML/context fences (+F5 verbatim fence), history strip, structured vs free-form generation with
citation validation, post-hoc top-chunk attribution, drift detect + state save, SLA/empty-answer
observability.

### 1.5 `nodes/guard_output.py` (579) — output guardrail
After `critique_parse`. Grounding judge (sync / parallel / async-background B5 / fail-closed AG-A2),
sysprompt-leak shingles with doc-shingle subtraction, per-intent+stats-route skips, OOS-template
substitution on block.

### 1.6 `nodes/rerank.py` (501) — rerank + score filters
After `retrieve` (or `graph_retrieve`). Per-bot resolver override, intent whitelist + skip set,
bypass-mode taxonomy, cliff vs threshold filter strategies, refuse gate, max-chunks cap,
retrieval safety-net re-injection (score-stamped), score-mode propagation (`rerank_score_mode`
— dropped, §4.1).

### 1.7 Remaining nodes (one-liners)
- `check_cache.py` (200): semantic-cache lookup; bypass/multi-turn skip; restores `graded_chunks`
  snapshot on hit; returns `_bot_cache_version`/`_corpus_version` (latter dropped, §4.1-g).
- `persist.py` (256): terminal audit + fire-and-forget cache write (strong-ref task set;
  numeric answers stored exact-hash-only via NULL embedding; multi-turn writes skipped).
- `condense_question.py` (105): legacy condense path (only when `merge_condense_router=False`);
  applies `normalize_vn_section_numerals` to EVERY query unconditionally (condense_question.py:49).
- `router.py` (50): legacy intent router — substring scan of LLM text over `_VALID_INTENTS`
  (router.py:45-48; order-dependent substring matching, see §6.7).
- `rewrite.py` (103): query rewriter; per-intent skip map; hardcoded EN instruction + VN example
  literal in code (rewrite.py:74-83, §7.3).
- `rewrite_retry.py` (44): CRAG retry — calls rewrite + bumps `grade_retries`.
- `decompose.py` (97): legacy multi-hop decomposer (structured + free-form paths).
- `adaptive_decompose.py` (140): Adaptive-Router L3 LLM decomposer (via `query_decomposer`).
- `query_decomposer.py` (201): domain-neutral decomposer engine + prompt (module constant).
- `query_complexity.py` (244): L1 additive heuristic (commas/conjunctions/numbers/…);
  `has_aggregation_keyword` per-language.
- `query_complexity_node.py` (93): parallel wrapper of 3 pre-retrieval branches.
- `routing.py` (252): all conditional-edge deciders (`_input_blocked`, `_cache_route`,
  `_understand_query_route`, `_complexity_route`, `_router_route`, `_grade_route`,
  `_output_blocked`, `_retrieve_route`, `_reflect_route`).
- `guard_input.py` (80): input guardrail + language-pack preload (`_language_pack_rows`).
- `mmr_dedup.py` (70): MMR diversity filter, per-intent threshold override.
- `neighbor_expand.py` (529): ±N chunk-index window expansion (opt-in), token-budget capped.
- `reflect.py` (185): Self-RAG reflect, smart-skip when grounded + score floor.
- `critique_parser.py` (222): Self-RAG `[Supported]/[Unsupported]` marker parsing + optional refuse.
- `speculative_retrieve.py` (101): pure helpers (cosine, keep-decision, MQ-intent gate).
- `graph_retrieve.py` (25): thin wrapper over `infrastructure.graph.graph_retriever`.
- `cascade_router_helper.py` (206): cascade tier-model resolve helper (§4.3).
- `rrf_round_robin.py` (180): entity-quota RRF — **orphan** (§4.4).
- `__init__.py` (7): docstring only.

### 1.8 Non-node orchestration files
- `state.py` (207): the `GraphState` TypedDict — the single schema LangGraph uses to build channels.
  Its own comments (state.py:150-153, 164-167, 174-177, 192-194) document that undeclared keys are
  dropped at reducer-merge. §4.1 shows the codebase violates its own rule ~12 times.
- `graph_assembly.py` (208): canonical DI kwargs + initial `GraphState` for all transports.
  Sets 3 keys that GraphState does not declare (graph_assembly.py:177,192,193) — all silently dropped.
- `query_graph_helpers.py` (201): pure helpers (`_pcfg` with None-means-missing semantics,
  `parse_decomposed_sub_queries`, `expand_parent_chunks`, `_render_captured_slots`,
  `_compute_bot_cache_version`, `_is_null_lexical`).
- `retrieval_filter.py` (223): pure CRAG vocab + `_autocut` + `_cliff_detect_filter` +
  `_rerank_threshold_gate` + `_remap_grade_for_intent`.
- `system_prompts/context_aware_refusal_template.py` (146): reference template (never injected at
  runtime — verified: only exported, no `role=system` usage) + `resolve_sysprompt_version` metadata.

---

## 2. SPECIAL FOCUS — retrieve.py routing: exactly when stats vs vector fires

**FACT (code trace, retrieve.py:202-618 + [qg:2207-2507]):**

The **stats route** is attempted iff ALL of:
1. `stats_index_repo` DI-wired (retrieve.py:202).
2. A filter parses from the **raw pre-condense text** (`original_query or query`, retrieve.py:208)
   using the bot-locale routing signals (retrieve.py:212-215). Parser precedence:
   price-range → code lookup (flag `stats_code_lookup_enabled`) → price-of-entity (flag
   `stats_price_of_entity_enabled`) → keyword/list (retrieve.py:215-250).
   Superlative (max/min) filters can be killed per-bot (`stats_superlative_enabled`, retrieve.py:254-262).
3. NO structural reference in the query — DI strategy `.extract()` OR the always-on fallback regex
   `DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN` (VN+EN structural words + number;
   constants `_21_streaming_upload.py:146`) (retrieve.py:271-292).
4. Decompose NOT active (`len(sub_queries) < 2`) (retrieve.py:302-306).
5. `filter.confidence >= range_query_min_confidence` (retrieve.py:306-308).

Then: **race mode** (per-bot `stats_index_race_enabled`, default False, constants
`_21:162`) runs stats SQL vs a minimal single-shot vector arm concurrently, stats preferred on tie
(retrieve.py:328-566); **sequential mode** runs `_do_stats_lookup` and only short-circuits when it
returns non-empty `linked_chunks` (retrieve.py:569-618) — empty stats falls through to hybrid.
On stats success the node ALSO seeds `graded_chunks` + `retrieval_adequate=True`, and
`_retrieve_route` (routing.py:232) skips rerank→mmr→grade entirely; `grade` has a second
belt-and-suspenders bypass (grade.py:100).

The **vector route** = everything else: speculative-hit reuse → single or fan-out hybrid
(decompose sub-queries > preset paraphrases > inline LLM expansion, gated per intent + token count
+ chitchat skip + exact-code-lookup skip, retrieve.py:1126-1216) → RRF → relax/fallback/multistage →
lexical fuse → permission filter → parent-child → autocut.

**Where this only fits one corpus style (owner's #1 concern):**
- The stats route is the platform's ONLY completeness mechanism ("list all", "how many",
  "cheapest") — and its trigger vocabulary lives in locale packs that exist **only for `vi` and `en`**
  (`PACKS = {"vi": …, "en": …}`, i18n.py:665; unknown locale → vi seed, i18n.py:750-753;
  `DEFAULT_AGGREGATION_KEYWORDS_BY_LANG["ja"] = ()`, constants `_24:53`). A ja/fr/km bot silently
  loses list/count/superlative correctness — vector top-k gives incomplete lists with no signal.
- The stats index itself models `entity_name / price_primary / price_secondary / category /
  attributes_json` — a price-list/catalog corpus shape. Narrative corpora (contracts, manuals)
  never populate it, so route conditions correctly no-op — good degrade — but combined with the
  by-intent context-cap default gap (§5.2) aggregation over narrative corpora has NO completeness path.
- The synthetic stats chunk truncates rows at the **platform constant** `DEFAULT_STATS_INDEX_LIMIT`
  (=100) regardless of a bot's higher `stats_index_limit` override [qg:2448 vs retrieve.py:309-311] — FACT.

---

## 3. SPECIAL FOCUS — understand.py heuristic fast-path

**FACT chain:**
1. understand.py:117 calls `_classify_heuristic(state.get("query") or "")` — **without the `signals`
   kwarg**, although `classify_heuristic(query, *, signals)` exists precisely so "a non-Vietnamese bot
   classifies on ITS locale's patterns" (heuristic_intent_classifier.py:96-115). Every bot on the
   platform classifies on the **vi seed patterns** (i18n.py:274-298). Locale support built, not wired.
2. Threshold boundary defeats the documented safety: mid-string patterns (aggregation `bao nhiêu`,
   multi_hop `tại sao|giải thích|why`, comparison `khác gì|vs|difference between`) return
   confidence **0.85** (heuristic_intent_classifier.py:126-129) which the classifier docstring says
   should "force LLM check on anything borderline" — but `HEURISTIC_INTENT_CONFIDENCE_THRESHOLD = 0.85`
   (constants `_21:205`) and understand.py:125 gates with `>=` → 0.85 ≥ 0.85 **passes**, LLM skipped.
3. The heuristic gate is not history-aware (understand.py:116) and its early return
   (understand.py:138-142) skips the condense step entirely.

**Concrete failure (FACT on control flow; end-to-end effect = HYPOTHESIS, needs a 2-turn eval):**
multi-turn turn-2 "vậy nó khác gì với gói kia?" matches comparison mid-string → intent=comparison,
conf 0.85 ≥ 0.85 → return without condense → `retrieve` runs on the raw pronoun query → retrieval
quality depends on pronoun-laden text; the rewriter (which does thread history, rewrite.py:63-84)
only runs if `_router_route` doesn't shortcut and the comparison decompose gate lets it through.

**Related (FACT):** the Redis understand-cache GET is not history-gated (understand.py:85-107)
while the SET is (`not has_meaningful_history`, understand.py:264-269). A raw text cached from a
single-turn conversation will hit for the SAME text arriving mid-conversation (bot-scoped, TTL
window), returning single-turn intent and skipping condense for a context-dependent follow-up.

**Related orphan (FACT):** `force_re_understand` — the CRAG-retry escape hatch read at
understand.py:76,116 — is **never set anywhere** in `src/` (grep: only understand.py + state.py).
After `rewrite_retry`, the intent is never re-derived; the hatch is dead.

---

## 4. Top defects (verified)

### 4.1 SYSTEMIC — GraphState schema drift: LangGraph drops undeclared keys
**FACT — empirically probed twice** (scratchpad probe, langgraph 1.2.4, `StateGraph(GraphState)`
from this repo): initial-state keys `raw_user_message`, `bot_created_at`,
`bot_extra_output_tokens_per_response` are `<MISSING>` inside the first node; node-returned keys
`resolved_answer_model`, `action_state`, `rerank_score_mode`, `_total_graph_iterations`,
`crag_skip_retry` are `<MISSING>` in the next node and in the final state, while declared keys
(`workspace_id`, `retrieve_mode`) survive. `state.py` itself documents this drop semantic 4×
(state.py:150-153, 164-167, 174-177, 192-194).

Downstream impact, per key (all code refs = FACT; runtime impact severity per item):

| # | Key | Producer | Consumer | Impact |
|---|-----|----------|----------|--------|
| a | `rerank_score_mode` | rerank.py:498 | grade.py:486 | **HIGH / T1.** `state.get("rerank_score_mode") == "rerank"` is always False → the CRAG all-irrelevant fallback ALWAYS uses the scale-invariant relative gate, never the absolute cross-encoder floor. `crag_min_fallback_score` + `crag_min_fallback_score_by_intent` are dead config. Failure: real reranker returns garbage 0.05-0.08 scores, all graded irrelevant → relative gate keeps everything within ratio-of-top → irrelevant chunks feed `generate` where the 0.25 floor would have refused. HALLU-risk ↑. |
| b | `bot_extra_output_tokens_per_response` | graph_assembly.py:193 | generate.py:738-744 | **HIGH / revenue.** Always 0 → `compute_output_cap(system_default, 0)` → paid-tier extra output tokens never honored for any bot. |
| c | `bot_created_at` | graph_assembly.py:192 | [qg:453] `_resolve_xml_wrap_enabled` | **MEDIUM.** Always None → M14 "bots created ≥ 2026-05-18 get XML chunk-wrap by default" never fires; only explicit `plan_limits.xml_wrap_enabled` works. |
| d | `raw_user_message` | graph_assembly.py:177 | generate.py:250-254 | **MEDIUM.** The 2026-06-15 slot-extraction fix key is dead; saved in most paths by the `original_query` fallback (set only when condense/understand actually rewrote). A turn where the query was NOT rewritten still passes the raw text via `query`, so net behavior mostly survives by accident. |
| e | `_total_graph_iterations` | grade.py:76-84 | routing.py:245-248 | **MEDIUM.** Counter resets every grade pass (always 0+1) → global iteration cap + `_reflect_route` cap dead. Loops remain bounded only because `grade_retries`/`reflect_retries` ARE declared. |
| f | `crag_skip_retry`, `crag_skip_reason`, `grade_timeout_fallback`, `intent_corrected`, `citations_source` | grade.py:109-156,267; generate.py:1071 | routing.py:168; final-state consumers | **LOW-MED.** Routing saved by `retrieval_adequate` (declared); final-state/observability consumers see nothing (e.g. `citations_source="posthoc_top_chunk"` never visible to the API layer). |
| g | `_corpus_version` | check_cache.py:95,174,199 | persist.py:183-185 | **LOW.** persist re-resolves; if corpus version changed mid-request, cache write key ≠ read key (orphan row). Memoization contract broken. |
| h | `action_state` | generate.py:1080 | transport reading final state | **LOW-MED.** DB save inside generate still works; any final-state consumer gets nothing. |
| i | `resolved_answer_model` | generate.py:408 | (none — see 4.3) | dead either way. |
| j | `embedding_column`, `trace_id`, `embed_degraded` | [qg:1337,1393,1500] | check_cache.py:108, [qg:1421,2697] | **LOW.** `embedding_column` is only ever the constant `DEFAULT_EMBEDDING_COLUMN` and never survives across nodes → `_run_semantic_cache_preflight` [qg:2696-2708] warns every turn (dead validation); `embed_degraded` is write-only (its comment claims "the answer path won't fabricate from a vector-less context" — nothing reads it); `trace_id` is always "". |

**Root cause:** no pin test asserting "every key any node returns or reads ∈ GraphState annotations".
The team knew the rule (4 warning comments) but has no enforcement.
**Expert fix (short):** declare the 12 keys in `GraphState`. (mid): add a unit test that walks
`nodes/*.py` AST for returned dict literals / `state.get("…")` and diffs against
`GraphState.__annotations__`. (long): typed per-channel reducers.

### 4.2 Stats-route grounding skip unconditional — failing pin test on tree
**FACT (pytest run, this session):**
`tests/unit/test_guard_output_intent_gating.py::test_guard_output_wires_stats_route_skip_grounding_flag`
**FAILS** (`assert "stats_route_skip_grounding" in src` — 1 failed, 15 passed).
guard_output.py:105 sets `_grounding_eligible = False` for every `retrieve_mode.startswith("stats")`
answer, unconditionally. But the platform contract (Fix B 2026-06-25) is the opposite default:
`DEFAULT_STATS_ROUTE_SKIP_GROUNDING = False` — "grounding ALSO applies to stats answers (HALLU-safe)…
a stock number leaked from history passed unchecked" (constants `_15:112-124`), with the per-bot key
registered in `bot_limits.py:67-69`. Grep confirms `stats_route_skip_grounding` appears NOWHERE in
`src/ragbot/orchestration/` — the wiring was never landed (or was lost in a merge), leaving the
sacred HALLU net off for the whole stats route with no per-bot escape. Live DB has no
`stats_route_skip_grounding` row either (asyncpg check, ragbot_v2_dev).
Note also grade.py:100-111 + routing.py:232 bypass CRAG for stats — intentional per comments,
but with 4.2 the stats route now has **no LLM verification at any layer**; the only remaining
guards are the regex output rules.

### 4.3 Cascade routing — resolved model never used
**FACT:** generate.py:399-417 resolves a tier model and writes `state["resolved_answer_model"]`.
Grep shows exactly two references in the codebase (generate.py:376 read, generate.py:408 write).
The actual LLM call resolves its model exclusively via
`model_resolver.resolve_runtime(purpose=lookup_purpose)` [qg:937-943] — `resolved_answer_model`
is consulted nowhere in `_invoke_llm_node` / `_invoke_structured_llm_node`. Even if it were,
the key is undeclared (§4.1). The per-bot `cascade_routing_enabled` feature performs an LLM-tier
resolve + 1-2 INFO log lines per turn (`cascade_routing_wire_entered` at every generate entry,
generate.py:390-397 — log noise) and changes nothing. The Wave-D/E "observability chain" comments
show the team chased the missing `cascade_routing_applied` event several times without finding
this: the event CAN fire (helper returns a name), but the name is discarded.

### 4.4 `rrf_round_robin` — orphan module
**FACT:** `grep -rn rrf_round_robin src/` → zero imports outside the module itself. The
entity-quota fairness layer (docstring: minority entity starved in comparisons → context contains
only one side) was built, unit-tested, and never wired into `retrieve` (which uses
`mq_rrf_merge_chunks` at retrieve.py:1381,1445,1726). Multi-doc/comparison starvation it was
designed to fix is therefore still possible.

### 4.5 GraphRAG context can never be cited or read by the LLM
**FACT chain:** `graph_retriever.py:78-87` synthesizes triple-chunks with `"chunk_id": None`
(+ hardcoded `score: 0.5`) and merges them into `retrieved_chunks`; generate.py:625-634
(`cid = c.get("chunk_id") or c.get("id")` … `if not cid: continue`) silently drops every
falsy-id chunk from the `<documents>` block. So even when a bot enables `graph_rag_mode`, a
graph chunk that survives rerank/mmr/grade is excluded at prompt build. Additionally the
`graph_context` state key (triples) has **no consumer** anywhere (grep: producer only).
GraphRAG is effectively a latency+SQL cost with zero prompt contribution.
(*graph_retriever.py is infrastructure scope; the drop point generate.py:633-634 is in-scope.*)

### 4.6 Early-return retrieval paths bypass the permission pre-filter
**FACT (code order):** the `permission_filtering_enabled` ACL filter runs at retrieve.py:1747-1765,
but three paths return before it:
- stats/race stats winner — retrieve.py:529-538 / 602-611 (stats chunks are bot-scoped, no ACL model);
- **vector race winner** — retrieve.py:554-557 returns raw hybrid chunks;
- **speculative hit** — retrieve.py:656-660 returns pre-raced chunks.
A bot with `permission_filtering_enabled=true` + `stats_index_race_enabled=true` (or the
speculative wrapper active) can serve chunks whose `access_groups` exclude the requesting user.
Both flags default OFF → gated exposure, but this is an access-control interaction, not a quality one.
The same early returns also skip parent-child expansion, lexical fusion, and autocut (quality drift
between paths).

### 4.7 Neighbor window is a per-document SPAN UNION, not per-seed windows
**FACT:** `plan_neighbor_windows` merges all seed windows of a document into one
`(min_lo, max_hi)` range (neighbor_expand.py:170-183) and `fetch_neighbors_sql` fetches
`chunk_index BETWEEN :lo AND :hi` (neighbor_expand.py:355-357). Two seeds far apart in one large
document (e.g. idx 3 and idx 490, window ±2) fetch **every chunk between** (489 rows) instead of
2×5. The token budget then keeps the *(document_id, chunk_index)-sorted head* of that span
(neighbor_expand.py:250-298) — i.e. chunks near the document head, NOT seed-adjacent chunks.
Failure: opt-in bot with a long PDF gets its budget filled by unrelated front-matter; the actual
neighbor of seed #2 is dropped. Also fetched-row memory spike on wide spans.

### 4.8 Understand-cache + heuristic multi-turn interactions — §3 items 2-4 (boundary `>=`,
cache GET not history-gated, `force_re_understand` never set).

### 4.9 by-intent context-cap default chain unwired
**FACT:** generate.py:560-562 reads `generate_context_chars_cap_by_intent` with default `None`;
`DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT` (documented as the fallback floor, constants
`_16:27-51`) is consumed **nowhere at runtime** (grep: constants + one comment). Contrast:
`retrieve_top_k_by_intent` (retrieve.py:703) and `rerank_top_n_by_intent` (rerank.py:72) DO fall
back to their constants. This deployment is saved by a live `system_config` row (asyncpg check:
aggregation=8000 …) that has **no alembic seed** (grep `alembic/versions` = 0 hits — out-of-band
config drift; cf. CLAUDE.md no-psql-hotfix rule for `system_config.value`). A fresh deployment
reverts aggregation queries to the flat 2900-char cap — the exact verified 2026-05-21 bug
("1tr499 có mấy dịch vụ" lost 3 of 7 chunks) the constant was written to fix.

---

## 5. Multi-axis gap analysis

### 5.1 Multi-doc (cross-document queries/joins)
- Cross-document synthesis relies on decompose fan-out + RRF; the fairness layer that guarantees
  both compared entities survive (`rrf_round_robin`) is orphan (§4.4) — majority-entity starvation
  remains possible on unbalanced corpora (FACT: not wired; starvation itself = HYPOTHESIS, needs eval).
- Stats path does have a cross-doc reconcile (`_reconcile_cross_doc`, [qg:2380-2381], per-bot
  opt-out) for split-sheet price/stock/date fragments — the ONLY cross-doc join in the pipeline,
  and it exists only for the stats-index corpus shape.
- `neighbor_expand` span-union bug (§4.7) degrades precisely on multi-seed multi-doc results.
- `mq_rrf_merge_chunks` + `_retrieve_top_k` slice after fusion (retrieve.py:1381-1382) is the only
  cross-branch balancing — no per-document quota; a single dominant document can fill all slots.

### 5.2 Multi-bot (per-bot config honored vs hardcoded)
Honored well: ~90 knobs go through `_pcfg` → `pipeline_config` (per-bot plan_limits chain).
Broken/partial:
- `stats_route_skip_grounding` per-bot escape not wired (§4.2 — FACT).
- `generate_context_chars_cap_by_intent` constant default unwired (§4.9 — FACT).
- Stats synthetic rows capped at platform `DEFAULT_STATS_INDEX_LIMIT` regardless of per-bot
  `stats_index_limit` [qg:2448] (FACT).
- `bot_extra_output_tokens_per_response` dropped (§4.1-b — FACT): a per-bot PAID feature.
- `embedding_column` is always `DEFAULT_EMBEDDING_COLUMN` ([qg:1337,1393], retrieve.py:921):
  the "routes the cache lookup to the column matching this bot's embedding dim" comment
  (check_cache.py:107-108) describes behavior that does not exist — per-bot dim selection happens,
  if anywhere, inside the adapters, not via this state key (FACT for the state key; adapter-side
  handling not audited here).
- Heuristic classifier ignores the bot's locale signals (§3.1 — FACT).
- `_race_vector` uses flat `top_k`, not the per-intent promoted `_retrieve_top_k`
  (retrieve.py:355-357 vs 699-726) — race-winner turns lose the aggregation-width guarantee (FACT).

### 5.3 Multi-format (PDF/DOCX/XLSX/CSV/Sheets/PPTX/HTML/TXT/MD parity)
Query-side nodes are mostly format-agnostic (they consume chunks), but format leaks in:
- `_extract_locked_prices` + `_PRICE_CELL_RE = ^[\d.,]{4,}$` (generate.py:93,152-168): the
  cross-turn price-lock only recognizes pipe/comma-delimited lines with a ≥4-char pure-digit cell —
  markdown-table and CSV shapes. Fails: DOCX/PDF narrative prices ("giá 500.000 đồng" — cell contains
  words), sub-1000 prices ("999"), suffixed cells ("500.000đ", "$45.99"), while **false-matching**
  years ("2024") and phone numbers on the same line → wrong `price_primary` locked (FACT on regex;
  wrong-lock occurrence = HYPOTHESIS, needs a booking-flow eval on a DOCX corpus).
- The stats index (the aggregation-completeness path) is populated by ingest only for
  row/record-shaped sources — table-corpus feature; narrative formats get no equivalent.
- `chunk_type` fallback `DEFAULT_CHUNK_TYPE_TEXT` in XML wrap (generate.py:645-649) — fine, but
  since `bot_created_at` is dropped (§4.1-c), the modality-labelled XML wrap barely ever runs,
  so table/image chunk typing built at ingest isn't surfaced to the LLM for most bots.

### 5.4 Multi-tenant
Overall solid: `record_tenant_id` threaded into hybrid search where adapters accept it
(retrieve.py:1006-1010,1081-1086,1116-1120,1574-1575), neighbor SQL joins on
`d.record_tenant_id` (neighbor_expand.py:348-364), parent-child SQL scopes by `record_bot_id`
(retrieve.py:1779-1785, per identity rule "internal queries use record_bot_id ONLY"), semantic
cache keys carry tenant+bot+workspace (persist.py:213-215).
Gaps:
- `conversation_state.load_state(conversation_id=…)` (generate.py:231-233) has no tenant argument,
  while `save_state` passes `record_tenant_id` (generate.py:1052-1056). Read scoping relies on the
  UUID PK's unguessability / the adapter's internal scoping — asymmetric defence-in-depth
  (HYPOTHESIS on exploitability; FACT on the asymmetry).
- Permission-filter bypass on race/speculative early returns (§4.6) — user-level, not tenant-level.

---

## 6. Suspected bugs (concise list with failure scenarios)

1. **grade fallback mode miscalibration** — §4.1-a. Scenario: cross-encoder scores all <0.25,
   all graded "no" → chunks pass anyway → answer generated from irrelevant context.
2. **stats answers unverified** — §4.2. Scenario: LLM relays a number not present in the synthetic
   chunk (e.g. from history); grounding judge never runs; regex guards can't catch it → HALLU ships.
3. **paid output tokens ignored** — §4.1-b. Scenario: bot buys +1024 output tokens; long
   aggregation answers truncate at the platform default exactly as before purchase.
4. **cascade routing no-op** — §4.3. Scenario: owner enables cascade + seeds tier models; complex
   query logs `cascade_routing_applied` (helper-level, cascade_router_helper.py:196-202) yet the
   answer is generated by the unchanged binding model; A/B shows zero delta.
5. **GraphRAG invisible** — §4.5. Scenario: `graph_rag_mode=always`; triples retrieved; prompt
   contains none of them; owner sees cost and no lift.
6. **heuristic 0.85≥0.85** — §3.2. Scenario above.
7. **router.py substring intent match** (legacy path, `merge_condense_router=false`):
   router.py:45-48 picks the FIRST schema intent that appears as a substring of the raw LLM text —
   an answer like "this is not a comparison, it's a factoid" matches whichever label happens
   first in `_VALID_INTENTS` order; no word-boundary, no negation handling. (FACT on code;
   only reachable on the legacy path.)
8. **understand-cache multi-turn hit** — §3.4. Scenario above.
9. **neighbor span union** — §4.7.
10. **`_race_vector` arm on wait-timeout leaks tasks briefly** — retrieve.py:464-471: on
    `asyncio.wait` timeout both tasks are cancelled (505) and the FULL sequential path re-runs —
    correct, but the stats SQL + embed cost is paid twice per timeout turn (T2 cost only).
11. **`_REGISTRY_CACHE` keyed by `id(signals)`** (heuristic_intent_classifier.py:73-88): a
    DB-hydrated `RoutingSignals` object that is garbage-collected can hand its `id` to a new
    object of a DIFFERENT locale → stale compiled registry (HYPOTHESIS — depends on
    LanguagePackService caching upstream; currently moot because callers pass no signals).
12. **speculative/race winners skip permission filter** — §4.6.

---

## 7. CLAUDE.md violations

1. **Zero-hardcode:**
   - retrieve.py:1051 — `flags = 5` inline fallback for invalid `bm25_normalization_flags`.
   - grade.py:81 — iteration-cap fallback returns `reranked_chunks[:2]` (magic 2).
   - generate.py:286 — service-lock scans `graded[:5]` (magic 5).
   - persist.py:199,202 — snapshot caps `[:2000]` chars / `[:8]` chunks inline.
   - heuristic_intent_classifier.py:127,129 — confidences 0.90/0.85 inline (application layer).
   - graph_retriever.py:82 — `score: 0.5` (infrastructure, noted for completeness).
2. **Domain-neutral / multilingual:**
   - rewrite.py:74-83 — app-authored English instruction text + Vietnamese example literal
     ("'có ưu đãi không' … 'dịch vụ X'") hardcoded in code and sent to the rewrite LLM for every
     locale — prompt text belongs in `language_packs` like every other node prompt
     (violates the language-as-data rule; borderline QG#10 since it is an internal rewriter prompt,
     not the answer prompt).
   - condense_question.py:48-53 + retrieve.py:1093-1098 — VN structural normalization/anchor logic
     runs unconditionally for all locales (pattern-guarded so effectively no-op elsewhere — smell,
     not a break).
   - `_VI_ROUTING_SIGNALS.list_strip_phrases` includes commerce-flavored tokens
     ("dịch vụ", "shop", "cửa hàng", i18n.py:210-218) — inside the designated locale-pack mechanism,
     but service-industry vocabulary shipping as platform seed is a gray zone.
   - guard_output.py:274-280 — sysprompt-leak shingles split on whitespace: for zh/ja/th prompts
     the shingle set degenerates (< shingle_size "words" → single whole-prompt hash) → leak guard
     effectively dead for non-space-delimited languages.
3. **App-injects/overrides answer (QG#10):** none found in the nodes — refuse substitutions all
   source `bots.oos_answer_template` via the 7-tier resolver, `DEFAULT_OOS_ANSWER_TEMPLATE = ""`
   (constants `_04:37`), critique/guard substitutions are per-bot-opt-in with owner templates.
   The stats synthetic chunk and `{captured_slots}` substitution are data-only (consistent with
   the documented exceptions).
4. **Broad-except:** all `except Exception` sites carry `# noqa: BLE001` + reason
   (generate.py:234,261,418,454,1043,1057; retrieve.py:490,501,888; cascade_router_helper.py:152,174;
   critique_parser.py:168; persist.py:127) — policy-compliant in form. Substance gap: the action-path
   catches log at **debug** level (generate.py:236,263,1044,1058) — a broken slot-extractor or
   conversation-state store is invisible in production logs (INFO default), silently disabling the
   feature the bot owner paid to configure.
5. **Out-of-band config state:** live `system_config.generate_context_chars_cap_by_intent` row has
   no alembic seed (§4.9) — if it was set via psql (not the audited admin UI), that is a direct
   violation of the no-psql-hotfix rule; verify via `audit_log`.
6. **No version-refs** found in-scope (grep patterns from CLAUDE.md return only alembic/docs hits).
7. **Provider if/elif in business logic:** none — dispatch is capability-probing
   (`inspect.signature`) and Strategy/Null-object based. The signature-probing itself
   (retrieve.py:985-987,1005,1039…) is a soft port-contract smell (3 protocols live at once) but
   not a rule breach.

## 8. Dead code / orphans / built-but-not-wired (rollup)

| Item | Evidence | Status |
|---|---|---|
| `rrf_round_robin.py` (whole module) | zero imports | orphan |
| cascade routing effect | §4.3 | built-not-wired |
| `resolve_sysprompt_version` + `CONTEXT_AWARE_REFUSAL_TEMPLATE` | exports only; no runtime consumer found in-scope | reference-only by design (docstring says so) — OK |
| `_understand_greeting_short_circuit` | [qg:505-550], zero callers | dead (~46 lines) |
| `force_re_understand` | never set | orphan escape hatch |
| `state["embed_degraded"]` | [qg:1500], zero readers | write-only flag, misleading comment |
| `graph_context` state key | producer only | orphan output |
| `_run_semantic_cache_preflight` | [qg:2687-2709] validates a key that never survives node boundaries | dead validation, warns every turn |
| `DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT` | §4.9 | dead default |
| `crag_min_fallback_score(_by_intent)` absolute branch | §4.1-a | dead config on langgraph 1.2.4 |
| `classify_heuristic(signals=…)` locale support | §3.1 | built-not-wired at call site |
| `chunk_ids` in `_do_stats_lookup` | [qg:2352-2353] `_ = chunk_ids` | intentional (attribution note) |

## 9. Ranked recommendations

1. **[T1] Declare the 12 missing GraphState keys + add an AST pin test** (§4.1). Smallest diff,
   fixes grade calibration, paid tokens, XML-wrap default, iteration cap in one commit.
2. **[T1/HALLU] Wire `stats_route_skip_grounding` in guard_output** (make the failing pin test pass,
   default grounding-ON for stats).
3. **[T1] Wire cascade `resolved_answer_model` into `_invoke_llm_node` or delete the wire + helper**
   (currently pure cost/noise).
4. **[T1] Fix GraphRAG chunk_id sentinel** (give synthesized chunks a synthetic id like the stats
   route's `DEFAULT_STATS_SYNTHETIC_CHUNK_ID` pattern) or gate the feature off until wired.
5. **[T1] understand fast-path: pass locale signals + change gate to `>` or raise threshold; gate
   heuristic on empty history** (§3).
6. **[T2] Wire `DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT` as the `_pcfg` fallback + alembic-seed
   the system_config row** (§4.9).
7. **[T2] Apply permission filter inside race/speculative early returns** (§4.6).
8. **[T2] Per-seed neighbor windows instead of span union** (§4.7).
9. **[T3] Sweep the small hardcodes (§7.1), lift rewrite.py prompt text into language packs,
   decide fate of `rrf_round_robin` (wire behind a flag for comparison intents or delete).**

---

*Verification artifacts: pytest output (1 failed / 15 passed) for §4.2; two LangGraph probes
(scratchpad `test_langgraph_key_drop.py`) for §4.1; asyncpg `system_config` query for §4.9;
all other claims are file:line static evidence, labeled HYPOTHESIS where runtime impact
was not directly measured.*
