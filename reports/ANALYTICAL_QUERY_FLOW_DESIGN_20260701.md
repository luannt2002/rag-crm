# Analytical Query Flow — Canonical Design (RAG platform)

> Deep-dive: how the system should handle **count / sum / list / group-by / summarize / compare**
> (the analytical class) — beyond vanilla top-K Q&A. Grounded in a code audit (evidence `file:line`)
> + SOTA research (cited) + synthesis + an adversarial sacred-rule check.
> Date 2026-07-01. Method: multi-agent workflow (audit + research) → main-session synthesis
> (workflow's own synth/verify steps hit the session limit; redone here in main session).

---

## 0. Core finding — we have NO analytical engine, only a price-row enumerator

The audit (Phase "Map" of workflow `wf_3e3ea2da-08a`) found the structured path is **not** an
aggregation engine. Evidence:

| Symptom | Evidence (`file:line`) |
|---|---|
| **2 decoupled classifiers, never cross-validate**: an `intent` label (cosmetic — only tunes grade/rerank caps) and the real `operation` classifier; intent='aggregation' has **zero** effect on whether the stats path fires | `nodes/understand.py:113-142`, `nodes/retrieve.py:193-201` ("intent is a hint, not required"), `heuristic_intent_classifier.py:90-136` |
| **No local-vs-global router** — routing = binary "price/keyword filter matched? → SQL index, else vector" | `nodes/retrieve.py:215-298` |
| **`operation='count'` is PARSED but NEVER dispatched** → collapses into the `else`/keyword branch → returns ROWS → the **LLM counts rows itself** | parse: `query_range_parser.py:130-138`; dispatch has only `keyword`/`max`/`min`/`else`: `query_graph.py:2121-2159` |
| **Silent-wrong count over the cap**: rows are `LIMIT`-capped (`stats_index_limit`), so a count larger than the cap is silently undercounted — a real fabrication path | `query_graph.py:2140-2143` |
| **`count_by_price_range` (a real `COUNT(*)`) is DEAD** — referenced only by its own unit test | `stats_index_repository.py:351-404` |
| **`matches_summary_pattern` (summary intent) is DEAD** — 0 call sites | `query_range_parser.py:548-560` |
| **NO `SUM` / `AVG` anywhere** — no signal, no parser, no SQL (`grep 'AVG(|SUM(' = 0`) | `stats_index_repository.py` |
| **Aggregates ONLY price columns** — generic numeric-attribute index (F7) was built then reverted | revert `9416f4d` |
| **`summary_json` write-only ORPHAN** — computed at ingest, 0 read sites at answer | `document_stats.py:~1125` (write) vs `orchestration/*` (0 read) |
| **Locale bug**: EN seed disables the measure-unit carve-out (`measure_unit_re=''`) → English "how many days" can mis-route to the catalog count path | `i18n.py` EN seed |

→ **This is the root of B-AGG/B-SERIES**: there is no engine that returns an aggregate NUMBER to the
LLM — only a row enumerator over price columns. "Có bao nhiêu loại Landspider" has no correct path.

---

## 1. The three substrates for analytical queries (SOTA research)

| | Substrate | Computes | Best for | Cannot |
|---|---|---|---|---|
| **A** | **Precompute summary** — RAPTOR (recursive summary tree) / GraphRAG (community summaries) | at INGEST | thematic / "summarize all", unstructured prose, anticipated | exact count/sum (summaries are paraphrase, not arithmetic) |
| **B** | **Text-to-SQL / structured aggregate** over an extracted table | at QUERY-time, SQL | exact count/sum/groupby/filter on a **table**, auditable, deterministic | mixed structured+unstructured; free-form custom math |
| **C** | **Code-interpreter** — LLM writes+runs pandas/python | at QUERY-time, code | **ad-hoc / user-invented** statistics, custom logic | reproducibility; live-DB; needs heavy sandbox security |

**Benchmarks** (research): naive Text-to-SQL ≤20% exact-match on BIRD's hard analytical queries;
TAG (Table-Augmented Generation, combining semantic + executable compute) +20–65%; LLM NL→code
60–70% accuracy → **a code path can silently produce a wrong number** (HALLU at the compute layer, not
just retrieval). Sources: GraphRAG (arxiv 2404.16130), RAPTOR, BIRD, TAG (CIDR 2025), AWS multi-tenant
RLS analytics, CIRCLE sandbox-security benchmark (arxiv 2507.19399).

**For OUR system**: we already extract entities into a real SQL table (`document_service_index`) — so
**B is the natural keystone**. A is for thematic. C is only for the unpredictable tail.

---

## 2. Gemini / AI Studio "code execution" — verified, and why it does NOT fit us

Gemini code-execution: LLM generates Python → runs in **Google's sandbox** → refines ≤5× → answers.
Verified (ai.google.dev): 30s timeout (standard) / 300s (enterprise), **NO internet in sandbox**, no
`pip install`, libs = pandas/numpy/scipy/sklearn/statsmodels/matplotlib, token-billed.

**The decisive distinction (3 tool types):**

| Tool | Runs where | Reaches OUR DB? |
|---|---|---|
| **Code Execution** | Google sandbox | ❌ no internet → must **ship tenant data UP to Google** |
| **Function Calling** | **our app** | ✅ LLM says "call `f(args)`", **we** run it (RLS-scoped SQL) |
| Grounding / File Search | Google | tenant docs uploaded to Google |

→ **Gemini code-execution is wrong for our multi-tenant catalog**: sandbox can't reach
`document_service_index`, so we'd have to upload tenant rows to Google = (a) **data leaves our boundary**
(breaks RLS / tenant isolation — sacred), (b) per-query token cost, (c) non-deterministic.
The correct in-house equivalent of "LLM computes on our data" is **function-calling** (LLM calls our
scoped aggregate), not code-execution. Current state: `ai_models.supports_tools=false` for all 3 LLMs
and the router doesn't pass `tools` → **function-calling is NOT wired**.

---

## 3. Canonical analytical flow (router → substrate → fallback) — shape-based

```
query
  └─► ROUTER (shape-based; unify the 2 classifiers into ONE analytical decision)
        ├─ structural anchor (Điều/Khoản/Article)      → prose/legal vector path        [EXISTS ✅]
        ├─ operation ∈ {count, groupby}                → STRUCTURED AGGREGATE (B)         [BUILD]
        │      COUNT(*) / COUNT(DISTINCT series) / GROUP BY attribute on the table,
        │      scoped record_bot_id + RLS — returns a NUMBER + group labels, no price rows
        ├─ operation ∈ {sum, avg}                      → STRUCTURED AGGREGATE (B)         [BUILD]
        ├─ operation ∈ {list, enumerate}               → table scan + pagination          [HARDEN cap]
        ├─ operation ∈ {max, min}                      → top_by_price / top_by_attribute  [EXISTS ✅]
        ├─ summary/thematic (matches_summary_pattern)  → summary_json → RAPTOR (A)         [WIRE later]
        └─ else (factoid / search)                     → vector / hybrid                   [EXISTS ✅]

  + FALLBACK CHAIN (never fabricate):
       live SQL aggregate  →  summary_json cache  →  sample-K chunks + explicit caveat  →  refuse
  + always attach a few citation chunks for grounding
  + cap honesty: if a count/list is LIMIT-capped, surface "N of M (capped)", never a silent wrong number
```

**Parametrized, NOT dynamic SQL** (multi-tenant safety, research-backed): the router/parser fills slots
(`agg_fn`, `group_by_col`, `filter`) into **fixed safe repository methods** — the LLM never emits raw SQL
and never controls tenant scope. The `record_bot_id`/RLS filter is server-enforced. This gives B's
flexibility with maximal safety + determinism + auditability, and avoids C's sandbox burden.

---

## 4. BUILD vs ALREADY-EXISTS (our-code mapping)

| Capability | Status | Where |
|---|---|---|
| Vector/hybrid factoid + search | ✅ EXISTS | retrieve/hybrid path |
| Superlative max/min | ✅ EXISTS | `query_graph.py:2144-2151` `top_by_price` |
| Price range/point | ✅ EXISTS | `stats_index_repository.py:191-216` |
| `COUNT(*)` SQL | ⚠️ EXISTS but **DEAD** | `stats_index_repository.py:351-404` `count_by_price_range` — wire it |
| **count dispatch** in `_do_stats_lookup` | ❌ BUILD | add `count`/`groupby` branch at `query_graph.py:2121` |
| **GROUP BY attribute / COUNT(DISTINCT)** (the "5 series" answer) | ❌ BUILD | new shape-based repo method |
| **SUM / AVG** | ❌ BUILD | new signal + repo method (additive) |
| Summary intent routing | ⚠️ parsed, DEAD | wire `matches_summary_pattern` |
| `summary_json` read at answer | ❌ BUILD | wire as cache for thematic |
| Function-calling (option C in-house) | ❌ NOT wired | `supports_tools=false`; future, ad-hoc only |
| EN measure-unit carve-out | ❌ BUG | `i18n.py` EN seed `measure_unit_re=''` |

---

## 5. Phased rollout (priority = T1 pain first, EVOLVE not rewrite)

1. **Phase 1 — count + group-by** (B keystone): unify router so `operation=count` dispatches; wire the
   dead `count_by_price_range` for cardinality; add a **shape-based GROUP BY attribute** method
   (gom variant→series → "5 loại Landspider"); cap-honesty. **+ golden-guard** (count == `COUNT(DISTINCT)`,
   HALLU=0). No alembic, no re-ingest (summary already exists; series token already in `attributes_json`).
2. **Phase 1b — sum/avg** (additive signal + repo method).
3. **Phase 2 — column-role** quantity/date (B-ROLE; labeled render; needs re-ingest, forward-effective).
4. **Phase 3 — cross-doc reconcile** (B-FRAG; alembic unique index + re-ingest).
5. **Later — A**: wire `summary_json` + RAPTOR for thematic "summarize whole corpus".
6. **Future — C (function-calling in-house)** ONLY for the ad-hoc tail, IF demanded: enable
   `supports_tools`, expose a **scoped, parametrized** aggregate tool (never Gemini code-exec; never
   raw LLM SQL). Defer — Simplicity-First.

---

## 6. Adversarial sacred-rule check (done in main session; workflow's verify step hit session limit)

| Check | Verdict |
|---|---|
| App-override LLM answer? | ✅ NO — we only shape retrieved DATA; LLM writes the answer. Aggregate = a retrieved fact, not an injected/overridden answer. |
| Per-bot / brand hardcode? | ✅ NO — router keys on `operation` signal (shape), group-by on ANY attribute by frequency/shape, not "Landspider"/"CITYTRAXX". |
| Fabricate risk? | ⚠️ ADDRESSED — fallback chain refuses over inventing; **cap-honesty is mandatory** (the audit found the existing silent-undercount-over-cap path — Phase 1 must return a real `COUNT(*)`, not a capped row-count). |
| EVOLVE not rewrite? | ✅ YES — wire dead methods + add aggregate; keep the working vector path untouched. |
| Multi-tenant RLS? | ✅ YES — `record_bot_id`/RLS server-enforced in the SQL; LLM never controls scope (parametrized, not dynamic SQL). |
| Over-engineering (Simplicity / T1>T2>T3)? | ✅ Avoid C/code-interpreter/GraphRAG now; B = the minimal correct fix. Defer A and C. |
| Determinism / audit? | ✅ B is deterministic + the aggregate query is loggable. |

**Verdict: APPROVED** — proceed with Phase 1 (B keystone) as the minimal, sacred-compliant fix.
Code-execution (Gemini) explicitly **rejected** for the catalog path on tenant-isolation + cost grounds;
function-calling (in-house) parked for the ad-hoc tail only.

---

## Appendix — sources (research agents)
GraphRAG arxiv 2404.16130 · RAPTOR · BIRD bench · TAG CIDR 2025 (vldb p11-biswal) · AWS multi-tenant
LLM analytics w/ RLS · Blaxel multi-tenant isolation · CIRCLE sandbox security arxiv 2507.19399 ·
ai.google.dev/gemini-api/docs/code-execution · /function-calling · /tools.
