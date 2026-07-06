# Web Research — Agentic RAG + Query Planning SOTA (2025–2026) → Recommendations for Ragbot's Analytical Weak Spots

> Slug: `web-agentic-query` · Date 2026-07-02 · READ-ONLY research (no src/tests/alembic touched)
> Method: 12 web searches + 12 primary-source fetches (arxiv abstracts / MS Research blog) + code-anchor
> verification in ragbot (`file:line` checked live this session, not copied from prior docs unless noted).
> Rule #0 discipline: every claim carries a URL (web) or `file:line` (code). **FACT** = verified this
> session; **FACT-prior** = evidence from a prior audited doc in-repo (cited); **HYPOTHESIS** = labeled,
> not verified — needs measurement before any ship claim.

---

## 0. Scope

Requested axes: query decomposition · Self-RAG · CRAG · adaptive retrieval routing · structured/SQL-hybrid
retrieval over tabular data (text-to-SQL vs stats-index) · multi-hop reasoning · cross-document
join/aggregation ("compare X and Y", "total of all Z") · GraphRAG/LightRAG for entity-relation queries.
Target: ragbot's known weak spots — **combined queries (price+stock+date across docs), comparisons,
aggregations** (per `plans/20260701-analytical-consolidation/plan.md` PART E support matrix).

---

## 1. SOTA landscape 2025–2026 (web evidence, URLs)

### 1.1 The agentic-RAG paradigm — surveys & principles

- **Agentic RAG survey** (Singh, Ehtesham, Kumar, Talaei Khoei, Vasilakos) — "Agentic Retrieval-Augmented
  Generation: A Survey on Agentic RAG", [arxiv 2501.09136](https://arxiv.org/pdf/2501.09136). Four agentic
  design patterns: **reflection, planning, tool use, multi-agent collaboration**. Taxonomy dimensions:
  agent cardinality, control structure (sequential → adaptive), autonomy level, knowledge representation.
- **Reasoning agentic RAG survey (System 1 vs System 2)** — [arxiv 2506.10408](https://arxiv.org/pdf/2506.10408):
  the field's arc = atomic improvements (decomposition, rewriting, selective retrieval) → iterative
  plan/retrieve/reflect loops (ReAct, Self-RAG, PlanRAG, Search-o1/Search-R1). Three tests for "truly
  agentic": autonomous strategy choice, iterative execution, interleaved tool use.
- **Query optimization survey** — [arxiv 2412.17558](https://arxiv.org/html/2412.17558v2) (decomposition /
  rewriting / expansion taxonomy).
- 2026 direction: hierarchical retrieval interfaces for agents (**A-RAG**,
  [arxiv 2602.03442](https://arxiv.org/html/2602.03442v1)), RL process supervision for agentic RAG
  (**TreePS-RAG**, [arxiv 2601.06922](https://arxiv.org/pdf/2601.06922)), dual-agent global planning
  (**D²Plan**, [arxiv 2601.08282](https://arxiv.org/pdf/2601.08282)).
- Production framing 2026: LangGraph is the mature substrate for production agentic RAG loops
  (grade-retrieval → rewrite → retry) — [LangChain agentic-RAG docs](https://docs.langchain.com/oss/python/langgraph/agentic-rag),
  [Qdrant tutorial](https://qdrant.tech/documentation/tutorials-build-essentials/agentic-rag-langgraph/).
  **Ragbot is already on LangGraph with grade/rewrite/reflect nodes — the skeleton matches SOTA.**

### 1.2 Query planning & decomposition

- **Plan\*RAG** — "Efficient Test-Time Planning for Retrieval Augmented Generation",
  [arxiv 2410.20753](https://arxiv.org/pdf/2410.20753) / [OpenReview](https://openreview.net/forum?id=cUuOKnjVQJ).
  Reasoning plan = **DAG of interrelated atomic sub-queries held OUTSIDE the LM's working memory**;
  enables parallel execution of independent sub-queries + precise per-sub-query retrieval; outperforms
  RQ-RAG and Self-RAG at comparable cost. Key vs ReAct: externalized structure avoids plan fragmentation.
- **Exploration–exploitation decomposition** — [arxiv 2510.18633](https://arxiv.org/abs/2510.18633):
  balance broad sub-query fan-out vs focused follow-ups.
- **GDP-RAG (delta planning, 2026)** — "Only Ask What You Don't Know",
  [arxiv 2606.22681](https://arxiv.org/pdf/2606.22681): gap-aware decomposition — run preliminary
  retrieval first, generate sub-queries **only for the information delta** (minimal skeletal trajectory,
  not a full reasoning chain). Directly a token-cost (T2) lever.
- **GraphSearch** — agentic deep-search workflow over GraphRAG,
  [arxiv 2509.22009](https://arxiv.org/pdf/2509.22009).

### 1.3 Reflection / corrective loops

- **Self-RAG** — [arxiv 2310.11511](https://arxiv.org/abs/2310.11511): fine-tuned reflection tokens
  (retrieve-on-demand + self-critique). **Production limitation: requires a fine-tuned model** — not
  applicable to an API-LLM multi-tenant platform like ragbot.
- **CRAG (Corrective RAG)** — [arxiv 2401.15884](https://arxiv.org/abs/2401.15884): lightweight retrieval
  evaluator with 3 confidence bands (correct / incorrect / ambiguous) → act (use / discard+fallback /
  decompose-then-recompose filtering). Plug-and-play with any RAG stack.
- **FAIR-RAG** — faithful adaptive iterative refinement,
  [arxiv 2510.22344](https://arxiv.org/pdf/2510.22344).
- **Self-Routing RAG** — selective retrieval + knowledge verbalization,
  [arxiv 2504.01018](https://arxiv.org/pdf/2504.01018).

### 1.4 Adaptive retrieval routing (query-complexity routing)

- **Adaptive-RAG** lineage (T5 classifier routes no-retrieval / single-step / multi-step) — described and
  extended in **RAGRouter-Bench**: "Lightweight Query Routing for Adaptive RAG"
  [arxiv 2604.03455](https://arxiv.org/pdf/2604.03455) (2026). Headline results: **TF-IDF + SVM router hits
  macro-F1 0.928 / 93.2% accuracy** routing {factual, reasoning, summarization} over 7,727 queries;
  **lexical features beat sentence embeddings by +3.1 macro-F1**; 28.1% token savings vs always-expensive.
  → *Cheap surface-pattern routers are SOTA-competitive; an LLM router is NOT required.*
- Practitioner corroboration that vanilla similarity search fails on numerical/constraint queries and that
  metadata/structured filtering fixes constraint satisfaction:
  [dev.to — When Similarity Search Breaks: Why RAG Fails on Numerical Queries](https://dev.to/akshay_rajinikanth/when-similarity-search-breaks-why-rag-fails-on-numerical-queries-1c3g);
  enterprise deep-search benchmark reports LLM-extracted-metadata filtering lifting retrieval Hits@4 by up
  to **+25pp** ([arxiv 2506.23139](https://arxiv.org/pdf/2506.23139), per search-result summary).

### 1.5 Structured / SQL-hybrid retrieval over tabular data

- **TAG** — "Text2SQL is Not Enough: Unifying AI and Databases with TAG" (Berkeley/Stanford),
  [arxiv 2408.14717](https://arxiv.org/abs/2408.14717): Text2SQL only covers questions expressible in
  relational algebra; RAG only covers point lookups; on their benchmark **standard methods (Text2SQL,
  RAG) answer ≤20% correctly**, TAG-style pipelines reach 40%+
  ([VentureBeat coverage](https://venturebeat.com/data-infrastructure/table-augmented-generation-shows-promise-for-complex-dataset-querying-outperforms-text-to-sql)).
  TAG = query synthesis → **database execution** → generation over exact rows.
- **TableRAG** — [arxiv 2506.10380](https://arxiv.org/html/2506.10380v1) (EMNLP 2025): hybrid **SQL
  execution + text retrieval** over heterogeneous docs; 4-step iterative loop: (1) context-sensitive query
  decomposition → (2) text retrieval → (3) SQL programming & execution → (4) compositional intermediate
  answers. SOTA on their HeteQA benchmark. **This is the closest published blueprint to what ragbot's
  combined-query flow should become** (per-sub-query choice of SQL vs vector).
- **SynTQA** — mixture-of text-to-SQL and end-to-end table QA,
  [arxiv 2409.16682](https://arxiv.org/pdf/2409.16682).
- **LOTUS** — semantic operators (`sem_filter`, `sem_join`, `sem_topk`, semantic aggregations) over
  dataframes, Berkeley/Stanford — [arxiv 2407.11418](https://arxiv.org/html/2407.11418v1),
  [github.com/lotus-data/lotus](https://github.com/lotus-data/lotus): the declarative "LLM as an operator
  inside a query plan" endpoint of this design space (up to 1000× claimed speedups vs naive LLM loops).
- **Text-to-SQL SOTA 2025** — BIRD leaderboard ([bird-bench.github.io](https://bird-bench.github.io/)):
  **Agentar-Scale-SQL 81.67% execution accuracy** (test) via orchestrated test-time scaling
  ([arxiv 2509.24403](https://arxiv.org/abs/2509.24403)); mid-2025 systems 71–77% (CHASE-SQL 76.02%
  multi-agent divide-and-conquer). Caveat for production: BIRD's binary EX metric agrees with human experts
  only ~62% of the time ([Beehive analysis](https://beehive-advisors.com/blog/bird-bench)) → **even SOTA
  free-form text-to-SQL is a ~1-in-5-wrong compute layer — a HALLU vector, not a safety upgrade, for a
  HALLU=0 platform.**
- **GlobalQA / GlobalRAG** — "Towards Global RAG: A Benchmark for Corpus-Level Reasoning",
  [arxiv 2510.26205](https://arxiv.org/pdf/2510.26205): tests **counting, extremum, sorting, top-k** over
  a corpus. **Strongest conventional RAG baseline = 1.51 F1**; their agentic GlobalRAG (chunk retrieval +
  LLM filters + symbolic aggregation modules) = 6.63 F1 on Qwen2.5-14B (4.4×, still terrible in absolute
  terms). → *Corpus-level aggregation over unstructured chunks is UNSOLVED at query time; the winning move
  is a symbolic/SQL substrate populated at ingest — exactly ragbot's `document_service_index` bet.*

### 1.6 Multi-hop & cross-document join/aggregation

- **MultiHop-RAG** — [arxiv 2401.15391](https://arxiv.org/abs/2401.15391): benchmark of multi-hop queries
  (inference / **comparison** / temporal / null) over a news corpus; existing RAG (GPT-4, PaLM, Llama2-70B)
  "perform unsatisfactorily" on retrieval AND answering. Comparison = a first-class hard class.
- Hop-count degradation is steep and retrieval-bounded: accuracy 0.68 (2-hop) → 0.48 (5-hop), min-similarity
  across hops most predictive of error (GRADE, [arxiv 2508.16994](https://arxiv.org/pdf/2508.16994), per
  search summary).
- **SAG** — "SQL-Retrieval Augmented Generation with Query-Time Dynamic Hyperedges",
  [arxiv 2606.15971](https://arxiv.org/abs/2606.15971) (2026): each chunk → one semantically-complete
  event + indexing entities at ingest; **SQL JOINs at query time dynamically link events sharing entities**
  (local hyperedges) — no pre-built global graph. 80.0% Recall@5 on MuSiQue (best on 8/9 metrics),
  production-scale (100M+ items), incremental updates. → *Graph-quality multi-hop linking using ONLY a
  relational DB — the best infra-fit pattern for ragbot's Postgres-centric, RLS-scoped stack.*
- **HippoRAG 2** — KG-as-long-term-memory, query→triples→Personalized PageRank; +7 F1 associative QA over
  SOTA embedding retrievers ([MarkTechPost](https://www.marktechpost.com/2025/03/03/hipporag-2-advancing-long-term-memory-and-contextual-retrieval-in-large-language-models/),
  [EmergentMind](https://www.emergentmind.com/topics/hipporag-2)).

### 1.7 GraphRAG family for entity-relation queries

- **GraphRAG** (Microsoft) — [arxiv 2404.16130](https://arxiv.org/abs/2404.16130): the canonical "global
  questions baseline RAG cannot answer" framing; entity graph + Leiden communities + multi-level summaries.
  Indexing cost is the killer (~$5–20 per moderate corpus; $33k reported for large corpora —
  [BuildMVPFast overview](https://www.buildmvpfast.com/blog/graphrag-vs-vector-rag-knowledge-graph-ai-2026)).
- **LightRAG** — [arxiv 2410.05779](https://arxiv.org/abs/2410.05779),
  [github HKUDS/LightRAG](https://github.com/HKUDS/LightRAG): dual-level (low-level entity / high-level
  topic) retrieval over a graph+vector index, incremental updates; ~6,000× cheaper indexing than full
  GraphRAG at comparable accuracy (per [CallSphere 2026 comparison](https://callsphere.ai/blog/vw6g-microsoft-graphrag-knowledge-graph-2026)).
- **LazyGraphRAG** — [Microsoft Research blog](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/):
  **indexing cost = vector-RAG-identical (0.1% of full GraphRAG)** via NLP noun-phrase extraction (NO LLM
  at index time); LLM deferred entirely to query time (iterative-deepening best-first+breadth-first);
  global-query answer quality comparable to GraphRAG Global Search at **>700× lower query cost**; at 4% of
  GraphRAG's global query cost it beats all competing methods on both local and global queries.
  → *The 2025-26 verdict: eager LLM graph-building lost; lazy/structural indexing won.*
- Counter-evidence to graph hype: embedding RAG beats GraphRAG on page-level retrieval F1 in an industrial
  study ([arxiv 2509.16780](https://arxiv.org/pdf/2509.16780)); graph methods are brittle on exact entity
  matching (Cypher-RAG → 0% when entity case changes, same study). Unified graph-RAG analysis:
  [arxiv 2503.04338](https://arxiv.org/pdf/2503.04338).

### 1.8 RL-trained search agents (for completeness — NOT recommended for ragbot)

- **Search-R1** — [arxiv 2503.09516](https://huggingface.co/papers/2503.09516): RL (outcome reward,
  retrieved-token masking) trains the LLM to interleave reasoning and search-query emission.
- **Search-o1** — agentic search-enhanced large reasoning models,
  [ACL 2025](https://aclanthology.org/2025.emnlp-main.276/) / arxiv 2501.05366.
- Both require training custom models → out of scope for an API-LLM platform (same reason Self-RAG is out).

---

## 2. Where ragbot stands vs this SOTA (code evidence — FACT, verified this session)

| SOTA pattern | Ragbot state | Evidence |
|---|---|---|
| Adaptive routing via cheap lexical classifier (§1.4) | ✅ ALIGNED — heuristic, config-driven, domain-neutral complexity classifier (commas/conjunctions/numbers/length, weights from `system_config`) | `src/ragbot/orchestration/nodes/query_complexity.py:1-120` |
| LLM query decomposition on complex queries | ✅ PRESENT — L3 decomposer node behind the L1 classifier; ≥2 sub-queries seed `sub_queries` | `src/ragbot/orchestration/nodes/adaptive_decompose.py:120-140`; comparison few-shot "Compare A and B" at `nodes/query_decomposer.py:71` |
| Corrective loop (CRAG-style grade → rewrite → retry) | ✅ PRESENT — grader + rewrite_retry + reflect nodes exist | `src/ragbot/orchestration/nodes/` listing: `grade.py`, `rewrite_retry.py`, `reflect.py`, `application/services/crag_grader/` |
| Self-query structured routing (parsed filter → SQL index) | ✅ PRESENT for price-range / code / price-of-entity / list; routing keys on **filter shape**, not intent label (domain-neutral) | `src/ragbot/orchestration/nodes/retrieve.py:180-306` |
| TAG-style DB execution for aggregates | ⚠️ PARTIAL — COUNT(*) wired (B-AGG Phase 1a, unit-green, **not live-verified**); `count_by_price_range` dead; **SUM/AVG/GROUP-BY absent** | `query_graph.py:2207-2295` (`_do_stats_lookup`, count branch `:2236-2265`); `stats_index_repository.py:351` (dead COUNT-by-range), `:406` (live COUNT-by-keyword); SUM/AVG grep=0 per `plans/20260701-analytical-consolidation/plan.md` PART A (FACT-prior) |
| Per-sub-query hybrid routing (TableRAG §1.5) | ❌ **MISSING — the comparison hole**: when decompose fires (≥2 sub_queries), the stats route is **skipped entirely** and sub-queries go to vector/hybrid fan-out only | `retrieve.py:300-306` (`_decompose_active` gate: "skip the single-entity stats route and let the multi-query fan-out retrieve every sub_query"); fanout bypass at `query_graph.py:1913-1927` |
| Comparison fairness at fusion | ✅ PRESENT — round-robin RRF so one entity can't crowd out the other | `nodes/rrf_round_robin.py:7-9`; compare-set coverage note `nodes/guard_output.py:73` |
| DAG / dependent-hop planning (Plan\*RAG §1.2) | ❌ MISSING — decomposer returns a **flat list**, no dependency edges, no iterative retrieve→reason→follow-up loop | `adaptive_decompose.py:139` (`return {"sub_queries": cleaned, ...}`) |
| Corpus-level summary substrate (RAPTOR/LazyGraphRAG §1.7) | ❌ ORPHAN — `summary_json` computed at ingest, 0 read sites at answer; `matches_summary_pattern` 0 callers | `shared/query_range_parser.py:552` (helper exists); orphan status per `reports/ANALYTICAL_QUERY_FLOW_DESIGN_20260701.md` §0 (FACT-prior) |
| Function-calling / tool use (agentic pattern #3, §1.1) | ❌ NOT WIRED — `ai_models.supports_tools=false` for all LLMs, router doesn't pass `tools` | `reports/ANALYTICAL_QUERY_FLOW_DESIGN_20260701.md` §2 (FACT-prior) |
| Cross-doc entity join (SAG §1.6) | ❌ MISSING — B-FRAG: `_dedup_stats_entities` is per-doc only; no shape-key merge across documents | `plans/20260701-analytical-consolidation/plan.md` PART A B-FRAG row (FACT-prior) |
| Multi-attribute predicates (price+stock+date) | ❌ MISSING — stats index aggregates **price columns only**; quantity/date roles unlabeled (B-ROLE); generic numeric-attribute index was built then reverted (`9416f4d`) | `ANALYTICAL_QUERY_FLOW_DESIGN_20260701.md` §0 (FACT-prior) |

---

## 3. Recommendations for the three weak spots

### 3.1 Aggregations ("total of all Z", counts, averages)

**SOTA verdict (FACT, web):** query-time aggregation over retrieved chunks is a dead end — best
conventional baseline **1.51 F1**, best agentic system **6.63 F1** on GlobalQA counting/extremum/sort/top-k
([arxiv 2510.26205](https://arxiv.org/pdf/2510.26205)); TAG shows ≤20% for both Text2SQL and RAG on
compound analytical questions ([arxiv 2408.14717](https://arxiv.org/abs/2408.14717)). The winning
architecture is TAG's: **synthesize a constrained query → execute in the DB → generate over exact rows**.

**Ragbot fit (FACT, code):** `document_service_index` + `_do_stats_lookup` IS a TAG pipeline with the
"query synthesis" step replaced by a deterministic parser (`query_range_parser.py:130,189,296,373,451`) and
parameterized repo SQL (`stats_index_repository.py`). This is **safer than SOTA free-form text-to-SQL**:
BIRD SOTA = 81.67% EX ([arxiv 2509.24403](https://arxiv.org/abs/2509.24403)) → ~1-in-5 silently-wrong SQL,
unacceptable under HALLU=0 + multi-tenant RLS.

**Recommendations (priority order):**
1. **Do NOT adopt LLM text-to-SQL for the catalog path.** Keep parser-constrained + parameterized SQL
   (already the plan's Part D verdict; SOTA numbers above now quantify WHY). [T1]
2. **Finish the aggregate verb set as deterministic SQL** — Phase 1c SUM/AVG, Phase 1b GROUP-BY — exactly
   the "aggregation modules for precise symbolic computation" that GlobalRAG credits for its 4.4× lift.
   Keep the cap-honesty property of `count_by_name_keyword` (`stats_index_repository.py:406-419`,
   real COUNT(*) not len(rows)). [T1]
3. **Run the Phase 1a live A/B first** (plan's own gate; also rule #0) — count fix is unit-green only.
4. **Medium-term: expose aggregates as in-house function-calling tools** (agentic pattern "tool use",
   [arxiv 2501.09136](https://arxiv.org/pdf/2501.09136)): LLM emits `count(keyword=…)` / `sum(attr=…,
   filter=…)`; **our** code executes RLS-scoped SQL. This is the SOTA-consistent replacement for both
   free-form SQL and Gemini code-execution (which the design doc correctly rejects — tenant rows would
   leave the boundary). Prereq: flip `supports_tools` + router `tools` pass-through. [T1/T2, Phase 7]
   **HYPOTHESIS:** function-calling will beat the regex parser on long-tail phrasings — must A/B; the
   parser stays as the deterministic fast path.

### 3.2 Comparisons ("compare X and Y", "so sánh A và B")

**SOTA verdict (FACT, web):** comparison is a first-class hard class in MultiHop-RAG
([arxiv 2401.15391](https://arxiv.org/abs/2401.15391)); the published fix is per-sub-query planning with
per-sub-query **substrate choice** — Plan\*RAG's atomic sub-queries each doing their own retrieval
([arxiv 2410.20753](https://arxiv.org/pdf/2410.20753)) and TableRAG's decompose→{SQL | text} per step
([arxiv 2506.10380](https://arxiv.org/html/2506.10380v1)).

**Ragbot gap (FACT, code):** decomposition exists and even has a "Compare A and B" few-shot
(`query_decomposer.py:71`), but the `_decompose_active` gate (`retrieve.py:300-306`) **turns OFF the stats
route for ALL sub-queries** — so "so sánh giá A và B" retrieves vector chunks for A and B and never hits
the exact price rows the stats index holds. The gate exists for a good reason (the point-lookup only parses
the FIRST spec code and would short-circuit the whole retrieve — comment at `retrieve.py:~295-300`), but
the SOTA-correct fix is the opposite direction:

**Recommendation:** **route each sub-query independently through the full router** (parser → stats
point-lookup if it matches, else hybrid), then merge per-entity results before generation — i.e., move the
routing decision from "whole query" to "per sub-query", the TableRAG step-2/3 pattern. The labeled-evidence
merge has an in-repo precedent (`query_graph.py:2249-2265` count-fact synthetic chunk with
`source: stats_index`), so it stays on the right side of sacred rule #10 (evidence assembly, not answer
injection). Round-robin RRF (`rrf_round_robin.py`) already solves fusion fairness for the hybrid side. [T1]
**HYPOTHESIS:** per-sub-query stats routing converts comparison answers from paraphrase-with-misattribution-risk
to exact per-entity rows; needs a golden comparison set + A/B (no lift number claimed).

**Defer:** full DAG dependency planning (Plan\*RAG) — ragbot's flat fan-out covers independent-entity
comparison; dependent-hop ("giá của cái rẻ nhất trong nhóm X so với Y") needs DAG edges or an iterative
loop. Cheapest increment when needed: one bounded follow-up round, GDP-RAG delta style
([arxiv 2606.22681](https://arxiv.org/pdf/2606.22681)) — decompose only the unanswered gap after the first
retrieve. [T2]

### 3.3 Combined queries (price + stock + date, across documents)

**SOTA verdict (FACT, web):** constraint queries fail on similarity search and are fixed by
structured/metadata filtering (practitioner evidence:
[dev.to numerical-queries](https://dev.to/akshay_rajinikanth/when-similarity-search-breaks-why-rag-fails-on-numerical-queries-1c3g);
benchmark evidence: metadata filtering +25pp Hits@4, [arxiv 2506.23139](https://arxiv.org/pdf/2506.23139)).
For the cross-document join half, the 2026 infra-fit winner is **SAG**
([arxiv 2606.15971](https://arxiv.org/abs/2606.15971)): extract event+entities at ingest, link
cross-document records at query time with **plain SQL joins on shared entity keys** — graph-level multi-hop
recall (80% R@5 MuSiQue) with zero graph infrastructure, incremental updates, DB-native scaling.

**Ragbot gap (FACT-prior):** stats index is price-only (generic attribute index reverted `9416f4d`);
quantity/date columns unlabeled (B-ROLE); cross-doc reconcile missing (B-FRAG, `_dedup_stats_entities`
per-doc only) — all in `plans/20260701-analytical-consolidation/plan.md` PART A/E.

**Recommendations:**
1. **Phase 2 (B-ROLE) is the keystone** — typed column roles (price / quantity / date) in
   `document_service_index`. Without labeled roles, no multi-predicate query can execute regardless of
   router quality. [T1]
2. **Extend the parser to conjunctive predicates** (price AND stock AND date) over those typed columns —
   still deterministic, still parameterized; this is self-query/metadata-filtering SOTA, not exotic. [T1]
3. **Phase 3 (B-FRAG) should be built as SAG-lite**: shape-keyed entity identity + SQL join/merge across
   `record_document_id` — explicitly NOT a graph store. SAG is the citation that this pattern is
   SOTA-competitive with graphs. RLS scoping (`record_tenant_id`) composes naturally since it's all
   Postgres. [T1/T2]
4. **Long-term watch, not build:** LOTUS-style semantic operators
   ([arxiv 2407.11418](https://arxiv.org/html/2407.11418v1)) for predicates SQL can't express
   ("services suitable for sensitive skin under 500k") — a `sem_filter` over the SQL-prefiltered candidate
   set. [T3]

### 3.4 Routing layer (cross-cutting)

- **Keep the heuristic router; don't LLM-ify it.** RAGRouter-Bench: lexical TF-IDF beats embeddings for
  complexity routing, 93.2% accuracy, 28.1% token savings
  ([arxiv 2604.03455](https://arxiv.org/pdf/2604.03455)). Ragbot's shape-based, config-driven classifier
  (`query_complexity.py`) is the same family — SOTA-validated. [FACT]
- **Unify the two classifiers** (cosmetic `intent` vs real `operation`) into ONE analytical router with
  explicit classes {factoid, count, sum, group-by, list, compare, global-summary, multi-hop} and a logged
  routing decision per turn — this is the design doc's §3 router, and Adaptive-RAG-lineage evidence says a
  small classifier suffices. Log = the eval substrate for router accuracy (GlobalQA-style per-class
  scoring). [T1/T2]

### 3.5 Global summary / entity-relation queries (adjacent weak spot)

- **Wire the orphan before buying anything**: `summary_json` is already computed at ingest (paid compute,
  0 read sites) — it is a level-1 RAPTOR/community-summary. Wiring it to a `global-summary` route is the
  zero-new-infra move. [T1, Phase 6]
- **If/when entity-relation queries become real demand**: LazyGraphRAG's lesson is decisive — defer LLM
  work to query time, index structurally
  ([MS Research](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)).
  Between LightRAG (new graph store, [arxiv 2410.05779](https://arxiv.org/abs/2410.05779)) and SAG
  (Postgres joins), **SAG wins on infra-fit and RLS**. Full GraphRAG indexing is rejected on cost and on
  the industrial counter-evidence ([arxiv 2509.16780](https://arxiv.org/pdf/2509.16780)). [T2/T3]

### 3.6 Explicit NON-adoptions (with reasons)

| SOTA item | Verdict for ragbot | Reason |
|---|---|---|
| Self-RAG reflection tokens | ❌ No | Requires fine-tuned model ([arxiv 2310.11511](https://arxiv.org/abs/2310.11511)); ragbot is API-LLM, multi-provider via DI |
| Search-R1 / Search-o1 RL agents | ❌ No | RL training out of scope; same constraint |
| LLM free-form text-to-SQL | ❌ No (catalog path) | 81.67% SOTA EX = silent-wrong compute vs HALLU=0; injection/RLS surface ([bird-bench](https://bird-bench.github.io/)) |
| Gemini code execution | ❌ No (already rejected) | Tenant rows leave boundary; sandbox can't reach our DB (`ANALYTICAL_QUERY_FLOW_DESIGN_20260701.md` §2) |
| Full GraphRAG eager indexing | ❌ No | Cost + LazyGraphRAG/LightRAG obsolete it; embedding-RAG beats it on page-level retrieval ([arxiv 2509.16780](https://arxiv.org/pdf/2509.16780)) |
| CRAG-style corrective loop | ✅ Already have | `grade.py` / `rewrite_retry.py` / `crag_grader` — maintain, don't rebuild |

---

## 4. One-paragraph synthesis

The 2025–2026 literature converges on exactly the architecture ragbot is halfway through building:
**a cheap lexical/shape router in front of multiple substrates — deterministic DB execution for
aggregates (TAG/TableRAG), per-sub-query planning for comparisons (Plan\*RAG), typed metadata predicates
for constraint queries, SQL-native entity joins for cross-document linking (SAG), lazy summaries for
global questions (LazyGraphRAG) — with an LLM that only ever narrates exact, executed results.** Ragbot's
stats-index bet is validated hard by GlobalQA (pure RAG = 1.51 F1 on counting) and by TAG (≤20%); its
gaps are not architectural but wiring: SUM/AVG/GROUP-BY verbs, per-sub-query routing (the comparison hole
at `retrieve.py:300-306`), typed column roles, cross-doc merge, and the orphaned summary path. The one
genuinely new capability worth scheduling is in-house function-calling (`supports_tools` flip) as the
governed agentic "tool use" layer — never text-to-SQL, never external code sandboxes.

---

## Appendix — full source list

**Surveys/paradigm:** [2501.09136](https://arxiv.org/pdf/2501.09136) · [2506.10408](https://arxiv.org/pdf/2506.10408) · [2412.17558](https://arxiv.org/html/2412.17558v2) · [2602.03442](https://arxiv.org/html/2602.03442v1) · [2601.06922](https://arxiv.org/pdf/2601.06922) · [2601.08282](https://arxiv.org/pdf/2601.08282) · [LangChain docs](https://docs.langchain.com/oss/python/langgraph/agentic-rag) · [Qdrant tutorial](https://qdrant.tech/documentation/tutorials-build-essentials/agentic-rag-langgraph/)
**Decomposition/planning:** [2410.20753](https://arxiv.org/pdf/2410.20753) · [OpenReview Plan-RAG](https://openreview.net/forum?id=cUuOKnjVQJ) · [2510.18633](https://arxiv.org/abs/2510.18633) · [2606.22681](https://arxiv.org/pdf/2606.22681) · [2509.22009](https://arxiv.org/pdf/2509.22009)
**Reflection/corrective:** [2310.11511](https://arxiv.org/abs/2310.11511) · [2401.15884](https://arxiv.org/abs/2401.15884) · [2510.22344](https://arxiv.org/pdf/2510.22344) · [2504.01018](https://arxiv.org/pdf/2504.01018)
**Routing:** [2604.03455](https://arxiv.org/pdf/2604.03455) · [dev.to numerical queries](https://dev.to/akshay_rajinikanth/when-similarity-search-breaks-why-rag-fails-on-numerical-queries-1c3g) · [2506.23139](https://arxiv.org/pdf/2506.23139) · [humanloop RAG architectures](https://humanloop.com/blog/rag-architectures)
**Tabular/SQL-hybrid:** [2408.14717](https://arxiv.org/abs/2408.14717) · [VentureBeat TAG](https://venturebeat.com/data-infrastructure/table-augmented-generation-shows-promise-for-complex-dataset-querying-outperforms-text-to-sql) · [2506.10380](https://arxiv.org/html/2506.10380v1) · [2409.16682](https://arxiv.org/pdf/2409.16682) · [2407.11418](https://arxiv.org/html/2407.11418v1) · [lotus-data/lotus](https://github.com/lotus-data/lotus) · [bird-bench.github.io](https://bird-bench.github.io/) · [2509.24403](https://arxiv.org/abs/2509.24403) · [Beehive BIRD analysis](https://beehive-advisors.com/blog/bird-bench) · [2510.26205](https://arxiv.org/pdf/2510.26205) · [2504.01346 RAG-over-tables](https://arxiv.org/pdf/2504.01346)
**Multi-hop/cross-doc:** [2401.15391](https://arxiv.org/abs/2401.15391) · [2508.16994](https://arxiv.org/pdf/2508.16994) · [2606.15971](https://arxiv.org/abs/2606.15971) · [HippoRAG2 MarkTechPost](https://www.marktechpost.com/2025/03/03/hipporag-2-advancing-long-term-memory-and-contextual-retrieval-in-large-language-models/) · [2502.14245 lost-in-retrieval](https://arxiv.org/pdf/2502.14245)
**Graph:** [2404.16130](https://arxiv.org/abs/2404.16130) · [2410.05779](https://arxiv.org/abs/2410.05779) · [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) · [LazyGraphRAG MS blog](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) · [2509.16780](https://arxiv.org/pdf/2509.16780) · [2503.04338](https://arxiv.org/pdf/2503.04338) · [CallSphere 2026](https://callsphere.ai/blog/vw6g-microsoft-graphrag-knowledge-graph-2026) · [BuildMVPFast](https://www.buildmvpfast.com/blog/graphrag-vs-vector-rag-knowledge-graph-ai-2026) · [2510.10114 LinearRAG](https://arxiv.org/pdf/2510.10114)
**RL search agents:** [2503.09516 Search-R1](https://huggingface.co/papers/2503.09516) · [Search-o1 ACL](https://aclanthology.org/2025.emnlp-main.276/)
