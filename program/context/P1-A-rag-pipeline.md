# P1-A — RAG QUERY PIPELINE (read-only report · Phase 1)

> Agent: P1-A RAG Pipeline Expert · Date: 2026-06-10 · Branch: `fix-260604-action-slotmachine-dead-key`
> SSoT examined: `src/ragbot/orchestration/query_graph.py` (8087 lines), `build_graph()` at `query_graph.py:1139`,
> graph wiring at `query_graph.py:7908-8044`. Every claim below carries `file:line` or commit-hash evidence.
> Labels: **SỰ THẬT** = verified by code/git read; **GIẢ THUYẾT** = flagged in §(e) open questions only.

---

## (a) Domain map — the REAL wired graph (21 nodes)

`StateGraph(GraphState)` constructed at `query_graph.py:7908`; entry point `guard_input` (`:7952`);
compiled at `:8044`; process-wide singleton `get_graph()` at `:8062-8078` (ignores kwargs after first call,
`_reset_graph_singleton_for_test()` at `:8081`). All nodes are **closures inside `build_graph()`** — DI handles
captured at build time, per-request data (`step_tracker`, `bot_system_prompt`, `kg_service`, `session_factory`)
read from `GraphState` at execution time (`:1166-1175` docstring).

### 21 wired nodes (add_node calls `query_graph.py:7909-7950`)

| # | Node name | Impl (file:line) | 1-line purpose |
|---|---|---|---|
| 1 | `guard_input` | `query_graph.py:1794` | Input guardrail (length/injection/PII) + pre-load DB language-pack rows |
| 2 | `cache_check_and_understand_parallel` | `:2426` | Parallel wrapper: `check_cache` (`:1854`) ∥ understand ∥ optional speculative retrieve (`_run_speculative_retrieve` `:2317`); gated `pipeline_parallel_cache_understand_enabled`, OFF = plain check_cache |
| 3 | `understand_query` | `:2071` | Merged condense+router 1-LLM-call: intent classify + standalone query; idempotency guard vs node 2 (`:2073-2080`) |
| 4 | `condense_question` | `:1995` | Legacy path: condense history + question into standalone query (threshold `< 2` since 2026-05-27, `:1998-2003`) |
| 5 | `router` | `:2618` | Legacy intent router LLM call (only reached via condense path, `_cache_route` `:7459`) |
| 6 | `rewrite_and_mq_parallel` | `:2973` | Parallel wrapper: `rewrite` (`:2641`, per-intent skip gate) ∥ multi-query expansion (`_run_multi_query_expansion` `:2722`); gated `pipeline_parallel_rewrite_mq_enabled` |
| 7 | `decompose` | `:3016` | Legacy LLM decompose multi-hop → 2-4 sub-questions (structured-output path + text fallback) |
| 8 | `query_complexity` | `:7731` (`query_complexity_node`) | Adaptive Router **L1** parallel wrapper: `_run_query_complexity` (`:7639`) ∥ `_run_router_select_model` (`:7662`) ∥ `_run_semantic_cache_preflight` (`:7707`); impl in `nodes/query_complexity.py` |
| 9 | `adaptive_decompose` | `:7797` | Adaptive Router **L3** LLM decomposer (domain-neutral, `nodes/query_decomposer.py`); failure → original query passes through |
| 10 | `retrieve` | `:3162` | The monolith (~1630 lines): stats-index B3 self-query routing (`_do_stats_lookup` `:3077`) → HyDE (`:1698-1711`) → metadata-aware filter → multi-query fanout + embed_batch (`:3824`) → hybrid dense+BM25 RRF (`_run_hybrid_for_query` `:3904`) → fallback ladder (`retry_hybrid_with_original` `:393`) → parent-child expand (`expand_parent_chunks` `:433`) → stats-vs-vector race (`_race_vector` `:3250`) |
| 11 | `graph_retrieve` | `:7609` (`graph_retrieve_node`) | GraphRAG KG traversal; returns `{"graph_context": []}` when `kg_service`/`session_factory` absent on state (`:7611-7614`) |
| 12 | `rerank` | `:4795` | Cross-encoder rerank via `reranker_resolver` (per-bot, ZeroEntropy zerank-2 default); per-intent `top_n` (`:4799-4803`); cliff-detect filter (`_cliff_detect_filter` `:792`), autocut (`:781`), threshold gate (`:863`) |
| 13 | `mmr_dedup` | `:5678` | MMR dedup over reranked chunks; per-intent similarity threshold (aggregation loosened, `:5681-5687`) |
| 14 | `neighbor_expand` | `:5725` | M2 ±N chunk_index neighbour expansion; per-bot opt-in `neighbor_expand_enabled`, OFF → `{}` identity; impl `nodes/neighbor_expand.py` (lazy import `:5757`) |
| 15 | `grade` | `:5203` | CRAG-lite LLM relevance grading (batch path + per-chunk fallback `_grade_one_chunk` `:5436`); iteration cap `max_total_graph_iterations` |
| 16 | `rewrite_retry` | `:5786` | CRAG retry: rewrite query + increment `grade_retries` (cap `max_grade_retries`) |
| 17 | `generate` | `:5812` | Prompt build + compression + LitM reorder + cascade routing (`apply_cascade_routing` `:6003`) + action slot-machine (slot_extractor `:5847-5866`, drift detect `:6581`) + LLM call (stream/sync via `_invoke_llm_node` `:1221`, speculative streaming gate `:1299-1333`) + citation parse |
| 18 | `critique_parse` | `:6650` | Self-RAG critique-token post-processor; per-bot `self_rag_critique_enabled`, OFF → `{}`; unsupported-ratio ≥ threshold → swap answer for bot-owned `oos_answer_template` (Quality-Gate-#10-compatible: template is bot data) |
| 19 | `guard_output` | `:6719` | Output guardrail (system-leak shingle) + async grounding check (`_run_grounding_check_background` `:1005`, scheduler `:1088`); explicitly NO regex-override of answer (`:6723-6728` comment) |
| 20 | `reflect` | `:7143` | Self-RAG reflection: judge keep-vs-rewrite the answer (LLM, structured or text path) |
| 21 | `persist` | `:7276` | Terminal: synchronous `query_completed` audit (Async Rule #8) → `_persist_meta` → background semantic-cache write (`_bg_cache_write` `:7288`) |

### Edges / routing functions (all `query_graph.py`)

- entry → `guard_input` (`:7952`); `_input_blocked` `:7453` → persist | cache_check_and_understand_parallel (`:7953-7957`)
- `_cache_route` `:7459` → persist (HIT) | `understand_query` (merged path) | `condense_question` (legacy path) (`:7958-7962`)
- `_understand_query_route` `:7467` → rewrite_and_mq_parallel | retrieve | decompose | query_complexity (`:7963-7972`)
- `_complexity_route` `:7494` → adaptive_decompose | rewrite | retrieve | decompose (`:7975-7984`); `adaptive_decompose → retrieve` (`:7987`)
- `condense_question → router` (`:7988`); `_router_route` `:7500` (`:7989-7993`); `rewrite_and_mq_parallel → retrieve`, `decompose → retrieve` (`:7994-7995`)
- `_retrieve_route` `:7623` → rerank | graph_retrieve | generate (`:7996-8000`); `graph_retrieve → rerank` (`:8001`)
- `rerank → mmr_dedup → neighbor_expand → grade` (`:8002-8007`); `_grade_route` `:7543` → rewrite_retry | generate (`:8008-8012`); `rewrite_retry → retrieve` (`:8013`)
- `generate → critique_parse → guard_output` (`:8020-8021`); `_output_blocked` `:7588` → persist | reflect (`:8022-8026`)
- `_reflect_route` `:8027` → generate (re-loop, capped by `max_total_graph_iterations`) | persist (`:8037-8041`); `persist → END` (`:8042`)

### Per-bot/feature flag surface

**36 distinct `*_enabled` flags** read via `_pcfg(state, ...)` inside query_graph.py (verified:
`grep -o '_pcfg(state, "[a-z_0-9]*enabled"' | sort -u | wc -l` = 36). Notables: `hyde_enabled`,
`multi_query_enabled`, `decompose_enabled`, `self_rag_critique_enabled`, `reflection_enabled`,
`speculative_{retrieve,streaming,hallu_verify}_enabled`, `neighbor_expand_enabled`,
`structured_subanswer_enabled`, `metadata_{aware_retrieval,layer3_llm,extraction,fallback_relax}_enabled`,
`pipeline_parallel_{cache_understand,rewrite_mq}_enabled`, `cr_enhanced_enabled`, `autocut_enabled`,
`grounding_check_enabled`, `xml_wrap_enabled`. **The real production topology depends on
system_config/plan_limits DB rows, NOT visible from code** — flagged in §(e) Q1.

### DEFINED-but-NOT-WIRED (dead / inert code)

| Item | Evidence | Status |
|---|---|---|
| `rrf_round_robin()` entity-quota RRF | `src/ragbot/orchestration/nodes/rrf_round_robin.py:88`; `grep -c rrf_round_robin query_graph.py` = **0**; only consumer is `tests/unit/test_rrf_round_robin.py:10` | **DEAD in pipeline** — built in `93a5483` (2026-06-08, "minority-entity drop fix") and never imported by the graph |
| `check_cache` closure | `query_graph.py:1854` | NOT a graph node (registration dropped `9068bb8`); still alive as fallback invoked inside wrapper node 2 when flag OFF (`:7910-7913` comment) |
| `rewrite` closure | `query_graph.py:2641` | Same pattern: not registered (`:7921-7924` comment), invoked inside `rewrite_and_mq_parallel` |
| Structured sub-answer path | `_resolve_generate_schema` `query_graph.py:554-569`, flag `structured_subanswer_enabled` default **False** | Wired but inert by default — shipped flag-OFF `c94bac9` + parity fix `108bbeb` (both 2026-06-08); no evidence of a flip |
| ColBERT late-interaction scaffold | `src/ragbot/application/ports/multi_vector_embed_port.py:1`, `infrastructure/embedding/multi_vector_registry.py:29` | Port + registry exist; **zero references from query_graph.py** — scaffold only ("Full ColBERT-style adapters land in later phase", `shared/constants/_02_per_intent_rerank_skip_gate_.py:162`) |
| `graph_retrieve` in practice | `query_graph.py:7609-7614` | Wired but short-circuits to `[]` unless `kg_service` + `session_factory` are placed on state — whether any production caller populates `kg_service` is unverified (§e Q4) |

### Doc drift (SỰ THẬT — docs vs code)

- `docs/master/04-D-pipeline-orchestration.md:1` claims "24-step canonical · 32-step observable", last-updated 2026-05-12, and **still lists "math lockdown" inside Q14** (`04-D:114`) — math_lockdown override was removed `cad52dc` (2026-04-29) and its dead config removed `6e9041d` (2026-06-09). Q-numbering (17 nodes) and line refs (e.g. "Q1 at query_graph.py:875", `04-D:133`) no longer match the 21-node/8087-line reality.
- `RAGBOT_STEP_PIPELINE.md:3` claims "25-32 query steps adaptive" (post 2026-05-19). Step-count framing ≠ LangGraph node count; observable `request_steps` rows ≠ nodes (sub-steps fire inside retrieve/rerank/generate — `04-D:233-246` explains this correctly).
- `docs/master/11-K-pipeline-code-mapping.md:4` dated 2026-04-20 ("v1.2", 24-step) — oldest of the three; benchmark/reference table still useful, code mapping stale.

---

## (b) Evolution from git (241 commits touch query_graph.py)

Timeline of node ADD/REMOVE with WHY (commit message + diff evidence):

| Date | Commit | Change | Why |
|---|---|---|---|
| 2026-04-16 | `c146482` | v1.0.0 initial graph | First multi-tenant RAG release |
| 2026-04-17 | `bddf723` | +`mmr_dedup` (et al.), score 7.3→8.5 | "intelligence upgrade Phase 1+2" |
| 2026-04-18 | `f845fd7` | +`graph_retrieve` (GraphRAG), prompt compression, whole-doc | "complete all remaining roadmap" |
| 2026-04-20 | `d44aeaf` | BM25 hybrid upgrade, parent-child, streaming | Phase 4 P0+P1 |
| 2026-04-20 | `9c9b20d` | +`understand_query` (merged condense+router → 1 LLM call) | Cost: kill one LLM round-trip; legacy condense+router kept as alternate path |
| 2026-04-21 | `8daae7a` | CRAG 3-state (`grade`/`rewrite_retry` loop) | P1/P2 reliability |
| 2026-04-22 | `a28d294` | +`decompose` node (multi-hop sub-queries) | P15 |
| 2026-04-23 | `a69fa09` | +math_lockdown app-override in `generate` | P29-A — **later judged a violation** |
| **2026-04-29** | **`cad52dc`** | **− app text-injection + answer-override layers (math_lockdown out)** | Platform-mindset turn: "bot owner owns everything", Quality Gate #10 born |
| 2026-05-01 | `de6573f` | +`cache_check_and_understand_parallel`, +`rewrite_and_mq_parallel` wrappers | v3-wave2 latency: parallelise independent awaits |
| **2026-05-08** | **`65b2c10`** | **build_graph singleton + state-lift of closure params** | Multi-tenant-safe compiled-graph reuse (no per-request rebuild; per-request data on state not closures) |
| 2026-05-12 | `0fe8b10` | +`query_complexity` (L1) + `adaptive_decompose` (L3) | S6 Pipeline-Opt Adaptive Router, domain-neutral |
| **2026-05-15** | **`9068bb8`** + `4c0de55` | **− dead node registrations `check_cache`/`rewrite`** (orphaned by the parallel-wrapper refactor) | mega-sprint-G22 hygiene; closures retained as flag-OFF fallbacks |
| 2026-05-18 | `e0a70d8` | +`neighbor_expand` (M2, opt-in) | RAG-Anything Tier-2 mindset |
| 2026-05-19 | `fc1fc61` | +`critique_parse` (Self-RAG critique tokens, opt-in) | WA-4 |
| 2026-05-19 | `8245fea` / `5a80848` | Cascade routing wired into generate / HyDE production wire (opt-in) | CT-2 / T1.4 |
| 2026-05-26 | `40683b9`+`6cb2c1d`+`e19bdf4`+`94f3698` | Pre-retrieval parallel (F16) + heuristic intent L1 + stats-vs-vector race retrieve (F17) + multi-query embed_batch | T2 latency wave |
| 2026-05-26 | `4931869` | B3 Self-Query stats-index routing in retrieve | Aggregation/comparison queries → SQL path |
| 2026-05-30 | `f0c88b4` | X2 Tier-2 hardening: multi-turn drift block | Multi-turn HALLU 15%→0% |
| 2026-06-04 | `f6eeb42` | Action slot-machine wired into `generate` (`conversation_state`+`slot_extractor` DI `:1163-1164`) + metadata L3 (`81becf6` same day) | Conversational booking actions; dead-Anthropic-key route fix |
| 2026-06-08 | `c94bac9`+`108bbeb` | Structured sub-answer generation (flag OFF) | Multi-fact intents; not yet flipped on |
| 2026-06-08 | `93a5483` | rrf_round_robin helper added — **never wired** | Minority-entity drop fix, helper-only |
| 2026-06-09 | `6e9041d` | − dead math_lockdown config/constants | Hygiene completion of `cad52dc` |
| **2026-06-10** | **`6547fb6` → `2f5ed41`** | Deterministic tie-break in retrieval ordering added then **REVERTED same day** | `7dd1f84` records "85/91 (revert confirmed) + tie-break A/B verdict" — determinism fix regressed graded suite; ordering nondeterminism on dense corpora is an OPEN issue |

**Top turning points**: (1) `cad52dc` 2026-04-29 — removal of all app-side answer-override (math_lockdown):
defined the platform's sacred "no inject/no override" identity. (2) `65b2c10` 2026-05-08 — graph singleton +
state-lift: the architectural move that made one compiled graph safe across all tenants and enabled all later
parallel wrappers. (3) `0fe8b10`→`9068bb8` May 2026 — adaptive-router layer added and dead legacy registrations
removed: the graph became *adaptive* (flag-gated per intent) rather than linear, which is also why 36 flags now
determine the real topology per bot.

---

## (c) Plans — done / doing / not-done (query-pipeline related, from `plans/`)

| Plan | Status (evidence) |
|---|---|
| `plans/260605-rag-full-fix-master/plan.md` | header "Status: ⏳ in-flight" (plan.md:2) — multi-agent paper-backed fixes; several phases landed as the 2026-06-08 commits |
| `plans/260608-rag-quality-rootcause/plan.md` | partially DONE — Phase 1 note "CODE đã ship 14ec96d, default OFF" (AdapChunk legal→HYBRID flag-gated, 2026-06-08) |
| `plans/260604-expert-rag-action-architecture/plan.md` | "Status: ⏳ DRAFT — chờ duyệt" but core shipped on branch `f6eeb42` (slot-machine) — **NOT merged to main** (memory note: branch `fix-260604-action-slotmachine-dead-key`) |
| `plans/260610-ga-hardening/plan.md` | DOING — RLS P0 + retrieval determinism + silent-degrade; determinism item set back by revert `2f5ed41` (2026-06-10) |
| `plans/260609-query-graph-split/plan.md` | NOT DONE — "[T3-Refactor] Tách query_graph.py (8087 dòng) → 8 file ≤~1.7K"; file is still 8087 lines today |
| `plans/260608-path-to-9.5-expert/plan.md` | "Status: ⏳ planned" — not started |
| `plans/260605-generation-discipline-fix`, `260605-rc4-generation-discipline-fix`, `260605-multistep-quality-master`, `260608-multiagent-fix-retest` | sibling quality campaigns; per `reports/GRADED_*` working-tree mtimes, grading reruns active as of 2026-06-09/10 (git status shows 13 GRADED files modified) |
| Charter Wave 6 (application layer: dashboard, sysprompt editor, feedback→eval loop) | NOT STARTED — `program/00-charter.md:39-40` lists it as post-engine work |

---

## (d) vs SOTA RAG 2026 — HAS / LACKS (objective inventory, NO judgement)

### HAS (each with code anchor)

- Hybrid dense (pgvector HNSW) + BM25 with RRF fusion (`_run_hybrid_for_query` `query_graph.py:3904`)
- Cross-encoder reranking, per-bot resolver + registry DI (rerank `:4795`, `application/services/reranker_resolver.py`)
- CRAG-style relevance grading + rewrite-retry loop with iteration caps (grade `:5203`, rewrite_retry `:5786`)
- Self-RAG: reflection node (`:7143`) + critique-token parser (`:6650`, opt-in)
- HyDE hypothetical-answer embedding, per-bot opt-in (`:1698-1711`)
- Multi-query expansion + batch embed + RRF (`:2722`, `:3824`)
- LLM query decomposition — two paths: legacy `decompose` (`:3016`) and Adaptive-Router L3 (`:7797`)
- Adaptive routing L1 heuristic complexity classifier (`nodes/query_complexity.py`, wrapper `:7731`)
- Two-tier semantic cache: exact hash + pgvector cosine, corpus-version invalidation (`check_cache` `:1854`, `_resolve_corpus_version` `:1196`)
- GraphRAG knowledge-graph retrieval hook (`:7609`, optional)
- MMR dedup with per-intent thresholds (`:5678`); autocut + cliff-detect + rerank-threshold gates (`:781`, `:792`, `:863`)
- Parent-child + neighbour context expansion (`expand_parent_chunks` `:433`; neighbor_expand `:5725`)
- Lost-in-middle reorder + prompt compression flags (`lost_in_middle_reorder_enabled`; generate `:5812`)
- Speculative streaming with draft-model race + hallu verifier gates (`:1299-1333`; `infrastructure/llm/speculative_router.py`)
- Speculative retrieval racing cache/understand (`:2317`, `:2463`)
- Cascade (cost-tier) model routing per intent (`apply_cascade_routing` `:6003`; binding_purpose split `:1229-1239`)
- Self-query / stats-index structured retrieval for aggregation intents (`_do_stats_lookup` `:3077`)
- Metadata-aware retrieval incl. Layer-3 LLM extractor (`metadata_layer3_llm_enabled`; shipped `81becf6`)
- Per-intent knob matrix: top_k, rerank top_n, MMR threshold, rewrite/MQ skip (`5fd9df1`, `eb817cd`, `79a52d0`, `4289687`)
- SSE streaming with TTFT capture + lagging-consumer guard (`:1388-1418`)
- Anthropic-style Contextual Retrieval at ingest + BM25 boost (`cr_enhanced_enabled`; ingest side, `RAGBOT_STEP_PIPELINE.md:5`)
- Conversational action slot-filling + drift detection in generate (`:5847-5866`, `:6581`)
- Structured-output (JSON-schema) variants for decompose/grade/reflect/generate (`_invoke_structured_llm_node` `:1462`)
- Async grounding/faithfulness check, non-blocking (`:1005-1088`)
- In/out guardrails with DB-driven language packs (`:1794`, `:6719`)
- Full per-step observability (`request_steps` via `step_tracker`; cost labels `feature_name=query.<purpose>` `:1261`)

### LACKS (objective absence; verified by grep / scaffold-only)

- ColBERT/late-interaction multi-vector scoring — port+registry scaffold only, not in query path (`multi_vector_embed_port.py:1`; 0 refs in query_graph.py)
- RAPTOR-style hierarchical summary tree retrieval — 0 hits repo-wide for "RAPTOR" outside the ColBERT-scaffold comments
- Agentic tool-use loop in the graph (no function-calling/ReAct node; actions limited to slot-fill in generate)
- Web-search / external-knowledge fallback node when corpus has no answer (no such node in `:7909-7950`)
- Multi-collection / corpus-routing (single `record_bot_id` corpus per query; no router across knowledge bases)
- Trained/learned router or classifier (L1 is heuristic, L3 is prompt-LLM; no fine-tuned model)
- Production feedback loop (thumbs → eval → retrieval tuning) — charter D12/Wave 6 explicitly not built (`program/00-charter.md:37-40`)
- Deterministic retrieval ordering on score ties — attempted `6547fb6`, reverted `2f5ed41` (2026-06-10)
- Entity-quota RRF (minority-entity preservation) — helper exists, unwired (`nodes/rrf_round_robin.py:88`)
- Graph checkpointing / resumable runs (graph compiled without checkpointer, `:8044` — `graph.compile()` bare)

---

## (e) 10 open questions for Phase 2

1. **Real production topology unknown**: 36 `*_enabled` flags decide which of the 21 nodes actually do work per bot. Which flags are ON in production `system_config` / per-bot `plan_limits`? Needs DB query (psql read-only) — code alone cannot answer.
2. **Retrieval determinism still open**: tie-break `6547fb6` reverted `2f5ed41` same day with graded suite 85/91 (`7dd1f84`). What exactly regressed, and what is the accepted plan for kill-the-flip on dense corpora (260610-ga-hardening ISSUE list)?
3. **Is GraphRAG dead in production?** `graph_retrieve` short-circuits unless `kg_service` is on state (`:7611-7614`). Does any production request path populate `kg_service`, or is node 11 effectively a no-op since `f845fd7`?
4. **Speculative streaming + hallu verifier**: has `speculative_streaming_enabled`/`speculative_hallu_verify_enabled` ever been ON for a real bot, and is the Phase-3 verifier validated under load (HALLU=0 evidence)?
5. **Double-decompose cost risk**: legacy `decompose`, adaptive L3 `adaptive_decompose`, and `_run_multi_query_expansion` can all produce sub-queries. When `decompose_enabled` + `adaptive_router_l1` + `multi_query_enabled` are simultaneously ON, do they duplicate LLM calls / fight over `sub_queries` state? (S2 bypass contract `:7985-7987` suggests mitigation, not verified.)
6. **understand/cache parallel race**: when speculative retrieve completes but cache HITs, are partial state writes (retrieved_chunks from the cancelled branch) ever merged into the persisted turn? Idempotency guard `:2073-2080` covers understand only.
7. **reflect→generate loop economics**: how often does `_reflect_route` (`:8027`) actually re-loop in production, and does `max_total_graph_iterations` cap fire? (request_steps aggregation needed.)
8. **critique_parse answer-swap vs Quality Gate #10**: swapping answer for bot-owned `oos_answer_template` (`:6650` docstring) is treated as compliant because the template is bot data — Phase 2 should formally rule on this interpretation (same pattern used by guardrail `response_message`).
9. **structured_subanswer flag-OFF**: `c94bac9`/`108bbeb` shipped it OFF on 2026-06-08. Is there a measured A/B that blocked the flip, or is it simply unevaluated?
10. **Does the closure architecture block the split plan?** `plans/260609-query-graph-split` wants 8 files ≤1.7K lines, but all 21 nodes are closures sharing ~30 helper closures inside `build_graph()` (`:1177-7905`) plus the compiled-graph singleton. What is the dependency-cut design, and does `get_graph()`'s ignore-kwargs-after-first-call behaviour (`:8065-8071`) survive the split?

---
*P1-A read-only report complete. No file outside `program/context/P1-A-rag-pipeline.md` was written.*
