# RAG SCORECARD — 2026-06-21 (multi-agent re-score, LIVE)

3 read-only agents re-scored ingest / query / AdapChunk-compare against
`docs/RAG_SCORING_TEMPLATE.md` with live DB + live eval. Supersedes the 2026-06-20
scorecard in the template. Rule #0: every cell has a number.

## 8-step scorecard (current, all 3 bots)

| step | xe | spa | thong-tu | verdict |
|---|---|---|---|---|
| 1 PARSE | ✅ structured | ✅ | ✅ | table/parent-child OK; **structural_path NOT in leaf chunks** (heading only in parents → legal BM25 gap) |
| 2 CHUNK | ✅ P50 368c | ✅ 235c | ✅ 348c | parent-child wired; size healthy; atomic protect on |
| 3 EMBED null_leaf | **0** | **0** | **0** | ✅ SACRED · dim 1024 · 100% leaf coverage |
| 4 STORE tsvector | 100% | 100% | 100% | ✅ BM25 full |
| 4 STORE stats_index | 🔴 **26% ≤5-char + 18% narrative + 93% null-price + 2 corrupted price** | 🟡 short-zone drop | 🔴 41% narrative entity | extraction noise = #1 lever |
| 4 STORE KG edges | 0 | 0 | 0 | dormant (config OFF) |
| 5 RETRIEVE (factoid) | 0.86 | 0.90 | 0.80 | ✅ ≥0.8 |
| 5 RETRIEVE (D13 conv.) | ~1.0* | 0.33 | 0.60 | 🔴 conversational gap (data-layer) |
| 6 COVERAGE (factoid) | 1.00 | 1.00 | 1.00 | ✅ |
| 6 COVERAGE (D13 conv.) | ~1.0* | 0.33 | 0.60 | 🔴 spa/legal gaps |
| 7 HALLU | 0 | 0 | 0 | ✅ **SACRED** (12 traps + 4 spot-checks all refuse) |
| L1 intrinsic (lexical) | 0.57 | 0.66 | 0.54 | 🟡 lexical; **real embedding SC 99.8 / CC 0.97 ≈ AdapChunk** |

\* xe D13 shows 0.86 in the eval but a live re-run ×3 returns "1.044.000đ" stably —
the single miss (d04) is LLM format-variance ("1.044.000"↔"1.040.000"), a HARNESS
artifact, not a retrieval failure. True xe conversational coverage ≈ 1.00.

## Load test (conversational vs factoid — the headline)
| bot | factoid COVERAGE | D13 conversational | gap | cause |
|---|--:|--:|--:|---|
| xe | 1.00 | **~1.00** (0.86 w/ artifact) | ~0 | FIXED (notation-fold) |
| spa | 1.00 | **0.33** | −0.67 | min-len drops short zone names (data) |
| legal | 1.00 | **0.60** | −0.40 | clause-density semantic gap (data) |

**HALLU = 0 across all 6 runs + 12 traps + 4 live spot-checks.** Every D13 miss is
**RETRIEVAL_MISS, 0 LLM_MISS** → the LLM uses what it's given; the gap is purely
retrieval/data, NOT generation.

## Upload (ingest) flow — current health
PASS core: parse → chunk → embed → tsvector all green (null_leaf=0 sacred holds, dim 1024,
100% tsvector). **The single ingest lever = `document_service_index` extraction noise**:
xe 26% ≤5-char entities + 18% narrative + 93% null-price + 2 date-as-price (`2025122435548`);
thong-tu 41% narrative entities. Fix at extraction (length/price-range validation +
price-gated write). Also: `structural_path` never reaches leaf chunks → legal section-name
BM25 can't match (ingest fix: assign structural_path to leaves).

## Query flow — current health
Pipeline correct: stats routing + notation-fold (`stats_index_repository.py` `_fold`,
confirmed live), hybrid BM25, HALLU guard all functioning. Remaining gaps ALL data-layer
(0 query-fixable levers left, proven): spa min-len reverse-match cutoff, legal clause
contextual-header absence. xe price fabrication FIXED + stable.

## AdapChunk detailed compare — net position
Ragbot is **architecturally BROADER**, BEHIND on the paper's two core claims.

| axis | ragbot vs AdapChunk |
|---|---|
| strategy pool | **AHEAD** (9 strategies vs 4) |
| VN legal hierarchy (HDT, Chương/Điều) | **AHEAD** (AdapChunk English-only) |
| atomic block protect (IMAGE/FORMULA/TABLE/CODE) | **AHEAD** |
| multimodal | **AHEAD** (IMAGE block modeled + NEW VLM parser; AdapChunk text-only) |
| multi-tenant + HALLU=0 + live-serving | **AHEAD** (AdapChunk = offline benchmark) |
| **intrinsic metrics** | 🔴 **BEHIND** — ragbot lexical (Jaccard/regex), AdapChunk embedding-cosine. Self-disclaimed `intrinsic_metrics.py:11-20` |
| **coreference / MRE** | 🔴 **LACK** — AdapChunk maverick-coref; ragbot regex xref only (+ coref model is English-only, VN needs different) |
| **adaptive selection** | 🟡 **DORMANT** — ekimetrics selector flag `False` (`ingest_stages.py:496`) + `parsed_blocks=[]` always empty → falls to legacy. AdapChunk runs a post-chunking TOURNAMENT (all 4 methods, pick winner); ragbot does pre-chunking single-inference heuristic even when ON |

**Highest-value architecture gap:** port `shared/intrinsic_metrics.py` ICC/DCC from lexical
→ embedding-cosine (Jina vectors already in pgvector; `scripts/score_chunks_embedding.py`
proves it). That makes the selector paper-comparable AND lets the dormant ekimetrics flag
turn ON with confidence — closes the metric gap + activates the selector in one change.

## Overall verdict
- **Faithfulness: A** — HALLU=0 sacred holds end-to-end (the hardest thing, done).
- **Coverage: B−** — factoid 1.00; conversational xe~1.0 / spa 0.33 / legal 0.60. All
  gaps are **data/ingest-layer** (extraction noise + clause headers), NOT query logic.
- **Architecture: broad but partly dormant** — wider than AdapChunk, but ekimetrics
  selector + KG + multimodal + narrate all OFF; metrics lexical not embedding.

**Top 3 highest-value fixes (all ingest/data-layer, gated on D13):**
1. stats_index extraction validation (length + price-range + price-gated write) → fixes
   spa conversational coverage + xe price-density. INGEST + re-index.
2. legal clause contextual headers + structural_path-to-leaf → fixes legal MFA + section BM25. INGEST.
3. lexical→embedding intrinsic metrics → activates dormant ekimetrics adaptive selector.

Evidence detail: the 3 agent sections are reproducible via `qa_chat.py` + the D13 sets +
`psql document_service_index` + the file:line citations above.
