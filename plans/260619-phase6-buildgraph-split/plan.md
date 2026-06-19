# [T3-Refactor] Phase 6 — build_graph god-file split

> **STATUS 2026-06-19**: Phase A–D DONE. query_graph.py 3945→2828 (−1117, ~28%),
> 20 green commits (5912/0 each). Runtime LOAD-TEST PASS: HALLU=0 (3/3 traps
> refused) + coverage intact across 3 bots. **Phase E + services-refactor
> DEFERRED** (user decision): composites high-coupling + infra-closures (~700
> lines) need a services-object refactor, not the partial pattern — <1200
> unreachable without it. See STATE_SNAPSHOT.md "build_graph surgery" section.

> Goal: shrink `src/ragbot/orchestration/query_graph.py` (3820 lines) toward the
> <1200 target by extracting `build_graph`'s inline node-closures into
> `nodes/*.py`, **completing the established `functools.partial(_node, di=…)`
> pattern** already used by retrieve/rerank/grade/generate.
>
> SACRED CONSTRAINT: HALLU=0 pipeline. Behavior-preserving ONLY. Green-gate
> (`5912 pass / 0 fail`, identical skip/xfail/xpass) after EVERY step. Load-test
> at end of each phase. Any drift → revert that step, post-mortem.

## Established pattern (already in file — we COMPLETE it, not invent)
- Node logic = module-level `async def <name>(state, *, <di_kwargs>, <infra_closures>)`
  in `nodes/<name>.py`.
- `build_graph` binds: `name = functools.partial(_name_node, vector_store=vector_store,
  _audit=_audit, _pcfg=_pcfg, …)` then `graph.add_node("name", name)`.
- Infra-closures that CAPTURE di_kwargs + are shared by-ref STAY in build_graph:
  `_audit`, `_resolve_corpus_version`, `_invoke_llm_node`,
  `_invoke_structured_llm_node`, `_so_usage`, `_prewarm_embedding_cache`,
  `_embed_query`, `_llm_complete_fn`. They are passed INTO extracted nodes as params.

## Inventory of inline closures still in build_graph
**Routing-edge deciders (capture NOTHING — only `state` + module-level `_pcfg`/const/logger/metrics):**
`_input_blocked`, `_cache_route`, `_understand_query_route`, `_complexity_route`,
`_router_route`, `_grade_route`, `_output_blocked`, `_retrieve_route` → ~155 lines.

**Node closures (capture di_kwargs + infra-closures):**
`guard_input`, `check_cache`, `condense_question`,
`cache_check_and_understand_parallel`, `router`, `rewrite`,
`rewrite_and_mq_parallel`, `decompose`, `mmr_dedup`, `neighbor_expand`,
`rewrite_retry`, `critique_parse`, `query_complexity_node`, `adaptive_decompose`,
`graph_retrieve_node` + sub-helpers (`_run_speculative_retrieve`,
`_run_multi_query_expansion`, `_do_stats_lookup`, `_run_query_complexity`,
`_run_router_select_model`, `_run_semantic_cache_preflight`).

## Phases (strangler-fig, lowest-risk first)
- **A. `_pcfg` → query_graph_helpers** (pure dict access; 21 refs via re-export). Green-gate.
- **B. Routing deciders → `nodes/routing.py`** (zero param threading; import `_pcfg`
  from helpers + const/metrics direct). Bind by module-level ref in build_graph. Green-gate + load-test.
- **C. Simple nodes** (`mmr_dedup`, `critique_parse`, `rewrite_retry`, consolidate
  `neighbor_expand` into existing module) — capture `_pcfg`/`_audit`/const only. Green-gate.
- **D. di_kwarg nodes** (`guard_input`, `check_cache`, `condense_question`, `router`,
  `rewrite`, `decompose`, `query_complexity_node`, `adaptive_decompose`,
  `graph_retrieve_node`). One node per commit, green-gate each. Load-test at phase end.
- **E. Composite/parallel nodes** (`cache_check_and_understand_parallel`,
  `rewrite_and_mq_parallel` + sub-helpers). Highest risk — green-gate + load-test each.

## Per-step protocol (NON-NEGOTIABLE)
1. Move body to `nodes/<name>.py`, captured vars → explicit kwargs.
2. In build_graph: `name = functools.partial(_name_node, <bindings>)`. Verify EVERY
   captured var is bound (grep the moved body for free names).
3. Re-export any test-imported symbol from query_graph.
4. `ruff check` new module = 0 errors.
5. Full unit suite = `5912 pass / 0 fail` IDENTICAL. Drift → revert.
6. Commit (small, one logical unit). Load-test at phase boundaries.

## Stop / rollback rule
HALLU>0 in load-test, OR suite count changes, OR a captured var missed (NameError)
→ revert the step's commit, post-mortem in this plan. T3 is LOWEST priority; never
trade pipeline correctness for line-count.
