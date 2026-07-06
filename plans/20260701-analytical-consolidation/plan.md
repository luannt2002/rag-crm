# RAG Analytical Consolidation — Status + Chunking-Pain + Multi-Phase Plan

> Consolidated 2026-07-01 from: real-pytest status audit (workflow wd3zxb722, 9 agents),
> SOTA research (chunking-pain / case-studies / code-execution), and the design doc
> `reports/ANALYTICAL_QUERY_FLOW_DESIGN_20260701.md`. Discipline: EVOLVE-not-rewrite,
> shape-based/multi-bot, RLS, never-fabricate, TDD + A/B + golden per phase.

---

## PART A — Old bugs: solved? tested? (verified by REAL pytest, not claims)

| Fix | Status | Test evidence |
|---|---|---|
| B-FMA price-by-spec (attributes_json search) | ✅ SHIPPED-VERIFIED | `test_stats_attributes_keyword_search.py` **2/2 PASS** |
| Config-default-drift (alembic canon_default_model_260630 + seeder) | ✅ SHIPPED-VERIFIED | `test_canonical_default_model_per_purpose.py` **8/8 PASS** |
| Resolver fallback → system_config | ✅ SHIPPED-VERIFIED | `test_model_resolver_system_config_fallback.py` **7/7 PASS** |
| Orphan-doc soft-delete (409 re-upload) | ✅ SHIPPED-VERIFIED | 8 unit + **3 integration PASS** (live PG+Redis) |
| External-API `external_call_failed` logging | ✅ SHIPPED-VERIFIED | `test_external_call_failed_observability.py` **2/2 PASS** |
| FE crash guard (503 → tokens‖{}) | ✅ SHIPPED (code) | JS — repo có NO js-test harness → test_status=NONE (inspection only) |
| **B-AGG count — Phase 1a** (parser op=count + `count_by_name_keyword` COUNT(*) + dispatch branch) | ⚠️ **SHIPPED-UNIT-GREEN, NOT LIVE-VERIFIED** | `test_count_operation_dispatch.py` **2** + regression **71 PASS**; **BUT** no post-fix A/B, original repro "1.020.000" NOT re-run, changes uncommitted |
| B-ROLE (26 vs 214 quantity) | ❌ PENDING | no test; `_roles_def` has no quantity/date role |
| B-FRAG (Davanti 98 not 26) | ❌ PENDING | no test; `_dedup_stats_entities` per-doc only |
| B-FORMAT (DOCX rows[0] header bypass) | ❌ PENDING | no test |
| B-CODETOK / B-TRUNC (space-spec, list cap 100) | ❌ PENDING | no test |
| SUM / AVG | ❌ MISSING | `grep 'SUM(|AVG(' = 0` |
| group-by "5 dòng" (B-SERIES) | ❌ PENDING (1b) | — |
| summary_json (thematic) | ❌ ORPHAN | 0 read-site at answer; `matches_summary_pattern` 0 callers |

**Answer to "đã test lại chưa?":** 6 shipped fixes RE-TESTED green (20 unit + 3 integration). Phase 1a
unit-green only — **A/B live is the missing gate** (the original repro must be re-run to confirm
HALLU=0 + correct count).

---

## PART B — The chunking pain (why analytical queries die)

Vanilla RAG = fixed chunking → embed → retrieve **top-K (≈5)** → generate. Optimised for **LOCAL**
(the answer sits in 1-3 chunks). It **structurally fails** for **GLOBAL / analytical**:

| Failure mode | Mechanism |
|---|---|
| **count / "how many"** | the answer is a property of the WHOLE set; top-K=5 sees 5/N rows → under-count or fabricate |
| **list-all / enumerate** | capped by K + context window; can't list 257 items from 5 chunks |
| **sum / avg / max** | needs every row; similarity returns the most *similar*, not *all* |
| **global summary** | no single chunk holds the theme; paraphrase ≠ computed aggregate |
| **compare / multi-hop** | cross-doc reasoning not expressible as one retrieval |
| **lost-in-the-middle** | even when in context, mid-context facts are under-attended |

**Authoritative sources:** GraphRAG (arxiv 2404.16130 — the "global questions baseline RAG cannot answer"
framing), RAPTOR (recursive summary tree), BIRD benchmark (naive Text-to-SQL ≤20% on hard analytical),
TAG / "Text2SQL is not enough" (CIDR 2025 — +20-65% by combining semantic + executable compute),
"Lost in the Middle". **Case studies:** AWS multi-tenant LLM analytics w/ RLS (50k queries, 0
cross-tenant via server-side CTE scoping); CIRCLE (arxiv 2507.19399 — code-sandbox security).

→ **Fix is NOT "better embeddings"** — it's picking the RIGHT SUBSTRATE per query class.

---

## PART C — What RAG already does WELL (the Q&A part that works)

- **Grounded factoid QA** ("giá 155/80R13") — vector + hybrid + rerank + stats point-lookup ✅
- **Semantic search / discovery** — top-K retrieval ✅
- **Citation / attribution** — request_chunk_refs, grounding check ✅
- **Hallucination reduction** — grounding + refusal traps (HALLU=0 sacred) ✅
- **Our system**: vector + BM25 hybrid + reranker + price point-lookup (B-FMA) + anti-fabricate
  (B-ROLEBLIND) are all live. The 20% "easy" Q&A is solid; the 80% analytical is the gap.

---

## PART D — Code-execution (AI Studio mode) + multi-tenant verdict

Gemini/AI Studio **code execution**: model writes Python → runs in Google sandbox (≤5 iters) →
returns EXACT data + **matplotlib charts inline**. Verified: 30s timeout, **NO internet in sandbox**,
no pip-install, libs = pandas/numpy/scipy/sklearn/statsmodels/matplotlib, token-billed.

**Three tool types — the decisive distinction:**
- **Code Execution** = Google sandbox, **can't reach our DB** → must ship tenant rows UP = **RLS/isolation risk** + token cost.
- **Function Calling** = runs in OUR app → LLM calls our scoped aggregate → **data stays in-house** ✅
- **Grounding/File Search** = Google-side RAG.

→ **Verdict:** Gemini code-exec **rejected** for the catalog path (tenant data leaves boundary). The
in-house equivalent = **function-calling** (currently `supports_tools=false`, not wired) OR a
**self-hosted sandbox** (MicroVM per tenant + pre-scoped dataframe) — for the **ad-hoc tail + charts**
only. For count/sum/group-by → deterministic **SQL-aggregate** wins (safe, cheap, exact).

---

## PART E — Current-state SUPPORT MATRIX (analytical class)

| Class | Today |
|---|---|
| Local factoid / price point | ✅ SUPPORTED |
| Search / discovery | ✅ SUPPORTED |
| **count** | ⚠️ PARTIAL — Phase 1a unit-green (honest COUNT), **not A/B-verified** |
| **list / enumerate** | ⚠️ PARTIAL — works but LIMIT-capped at 100 (B-TRUNC) |
| **group-by / "5 dòng"** | ❌ MISSING (1b) |
| **sum / avg** | ❌ MISSING |
| **quantity ("còn mấy")** | ⚠️ BROKEN — col_N unlabeled (B-ROLE) |
| **cross-doc reconcile** | ❌ BROKEN (B-FRAG) |
| **global summary** | ❌ MISSING (summary_json orphan) |
| **compare / multi-hop** | ❌ MISSING |
| **charts** | ❌ MISSING (would need code-exec/function-calling) |

---

## PART F — THE MULTI-PHASE PLAN (ordered, EVOLVE, verify-gated)

| Phase | Closes | Substrate | Alembic/Re-ingest | Tier |
|---|---|---|---|---|
| **1a** ✅done-unit | B-AGG count | SQL COUNT(*) | none | T1 |
| **1a-verify** ⭐NOW | live A/B | — re-run "loại Landspider" → 117, HALLU=0 | none | T1 |
| **1b** | B-SERIES "5 dòng" | shape-based GROUP-BY recurring token (+ owner custom_vocabulary hint; golden-locked — F7-careful) | maybe | T1 |
| **1c** | sum/avg | SQL aggregate + signal | none | T1 |
| **2** | B-ROLE quantity/date | column-role + labeled render | re-ingest | T1 |
| **3** | B-FRAG reconcile | cross-doc merge shape-key | alembic + re-ingest | T1/T2 |
| **4** | B-FORMAT | route DOCX/PDF/HTML → rows_to_structured_markdown (salvage dcdc55a) | re-ingest | T2 |
| **5** | B-CODETOK/TRUNC | parser token + cap + truncation marker | none | T2 |
| **6** | global summary | wire summary_json → RAPTOR (thematic) | maybe | T2 |
| **7** (future) | ad-hoc + charts | function-calling in-house / self-hosted sandbox (NOT Gemini code-exec) | — | T3 |

**Verify-gate every phase:** TDD failing test → minimal fix → 0 regression → **A/B 3 bot (xe/spa/legal),
bypass_cache, parallel gather N=8-10** → golden-lock (count==COUNT, HALLU=0) → only then "verified".

---

## PART G — Sacred compliance (every phase)
Shape-based (FORM not VOCABULARY) · 0 per-bot/brand literal · multi-tenant RLS (`record_bot_id`) ·
app does NOT override/inject answer (only shapes retrieved DATA) · never fabricate (fallback chain:
live-aggregate → summary-cache → sample+caveat → refuse) · zero-hardcode · alembic-only content-state.

## Immediate next step
**1a-verify**: re-run the original repro on the NEW count path — "có bao nhiêu loại Landspider" must
return an honest count (117) with HALLU=0, no `1.020.000`. Until that A/B runs, Phase 1a stays
UNIT-GREEN, not VERIFIED (rule #0).
