# MASTER PLAN — ragbot ⊇ (AdapChunk + RAG-Anything) + BEAT every direction

> From-scratch integration plan over the WHOLE `ref_rag/`: ekimetrics **AdapChunk**
> (chunking, MIT) + HKUDS **RAG-Anything** (multimodal + Knowledge-Graph, MIT) +
> the private specs. Goal: have EVERYTHING both refs have, and surpass on every axis.

---

## 0. WHY WE "LOSE" DESPITE HAVING THE CODE (root cause, evidence-backed)

The prior audit (`ref_rag/USEFUL_FOR_RAGBOT.md`) + this session prove it: **we are NOT missing the ideas — they are DORMANT or UNMEASURED.**
- **AdapChunk 7 layers → ragbot = 4 WIRED · 2 FLAG-OFF · 1 STUB.** L4 ekimetrics-selector = STUB (never passed True), L3 doc-profile + L7 narrate = flag-off.
- **RAG-Anything KG** → ragbot has `KnowledgeGraphService` + `knowledge_edges` table but **ingest never calls extraction → KG永 empty.**
- **Measurement** → until B-1 this session, CHUNK_RECALL was a blind artifact → couldn't prove which dormant piece helps → left OFF (safe).

**Root cause = no closed measurement loop → ideas built but unproven → stay OFF.** AdapChunk's entire contribution IS measurement-driven selection — the discipline we skipped. Fix = activate + wire + MEASURE, **not rewrite** (EVOLVE).
**Only genuinely-absent capability: multimodal VLM** (ragbot is text-only).

---

## SPINE — MEASUREMENT (gates everything; the root-cause fix)
**P0.** ✅ B-1 (CHUNK_RECALL real, shipped 61b7a7a) · **B-2 rigorous harness**: qrels ≥15/bot × ≥3 domains, N≥3 runs, Wilcoxon p<0.05, **+HALLU dim** (port AdapChunk `paper/rag_eval.py` methodology, MIT). **No track ships without a significant gate here.** (rule #0)

---

## TRACK A — AdapChunk completion → match + surpass chunking
| step | what | file | gate |
|---|---|---|---|
| A1 | wire **L4 ekimetrics selector** with REAL embedding metrics (port `metrics.py` MIT: SC/ICC/DCC/BI/SD) replacing lexical `intrinsic_metrics.py`; flip `ekimetrics_enabled`+L3 doc-profile | `shared/chunking/analyze.py:357`, new `infrastructure/chunk_quality/` Port+Adapter | adaptive ≥ best-fixed on B-2 |
| A2 | wire **L7 narrate-then-embed** + **context-aware narration** (RAG-Anything #2: inject ±N surrounding paragraphs) | `narrate_dispatch.py`, `ContextExtractor` port | CHUNK_RECALL↑ table corpora, HALLU=0 |
| **A3** | **SURPASS:** select chunking by **real CHUNK_RECALL (B-1)**, not the intrinsic proxy they're forced to use | selector objective | recall ↑ vs intrinsic-only (strictly better) |

## TRACK B — KNOWLEDGE GRAPH (RAG-Anything #1 — highest-value, scaffolded-not-wired)
| step | what | file | gate |
|---|---|---|---|
| B1 | **wire KG entity/relation extraction AT INGEST** (LLM NER+RE per chunk → `knowledge_edges` + entity VDB). port the extraction loop logic (MIT) | `KnowledgeGraphService` (exists, 0 callsites) → ingest stage | KG populated; multi-hop Q&A answerable |
| B2 | **KG-aware retrieval** — local/global/hybrid modes into retrieve node (cross-doc entity resolution) | `orchestration/nodes/retrieve.py` | multi-hop/cross-doc query COVERAGE↑ |
| | *unlocks what ragbot structurally CANNOT do today: multi-hop + cross-doc entity Q&A* | | per-bot `plan_limits` gated |

## TRACK C — MULTIMODAL (RAG-Anything core — the ONE genuinely-missing capability)
| step | what | file | gate |
|---|---|---|---|
| C1 | **VLM image/table/equation captioning at ingest** — port modal processors (MIT) into `ModalCaptionPort`+Adapter; each modal block → VLM description → first-class chunk+KG entity | new `infrastructure/multimodal/` | image-bearing doc retrievable, HALLU=0 |
| C2 | context-aware modal narration (shared with A2) | — | — |
| C3 | (defer) VLM-enhanced query — re-feed retrieved images to VLM at query time | `query.py` analog | after C1 measured |

## TRACK D — DEFENSIVE INFRA (cheap, high-leverage)
- **D1** robust LLM-JSON parse + thinking-tag strip (RAG-Anything #3, stdlib) → `shared/` util used by reranker/guardrail/structured-output. Prevents silent corruption multi-provider.
- **D2** OMML equation extraction from DOCX (stdlib) — only if technical corpora appear.

## TRACK E — SURPASS on the axes they LACK (ragbot already has — prove it)
1. **FAITHFULNESS/HALLU dim in eval** — neither ref measures hallucination; ragbot HALLU=0. → 1 axis they leave blank.
2. **Multi-tenant + serving + doc-lifecycle + feedback-loop** — RAG-Anything has none (library); ragbot has all.
3. **Multilingual (VN)** — AdapChunk coref + RAG-Anything tested English/zh; ragbot VN-native. RC-commercial multilingual = ours alone.
4. **Live-replay eval** — real traffic vs their fixed 99-query benchmark.

---

## NOT adopting (waste — ragbot already better)
MinerU/Docling parsers (have Kreuzberg multi-format) · enhanced-markdown→PDF (headless BE) · batch-parser (have semaphore) · parse-cache (have idempotency-key) · maverick-coref (non-commercial + English-only).

## SEQUENCING (immutable — measure first or repeat the mistake)
1. **P0 B-2** (measurement) — *hard gate; the literal root cause.*
2. **D1** (robust-JSON, cheap, no re-ingest) — quick safety win.
3. **B1 KG-at-ingest** (T1 highest-value missing) → **A1+A2+A3 chunking** (activate dormant) → **C1 multimodal** (the genuinely-new build).
4. **E** dims proven throughout (HALLU/VN/live always in the B-2 harness).

## CONSTRAINTS
EVOLVE-not-rewrite · Port+Adapter+Registry+Null+DI every new component · HALLU=0 sacred (A/B-gated) · domain-neutral · MIT-only ports (no maverick) · re-ingest only when a flag proves out on B-2 (no churn) · every claim gated on measured significance.

## HONEST BOTTOM LINE
ragbot already ⊇ ~90% of both refs *in scaffolding*. "Thua" = dormant + unmeasured, not absent. The path to "⊇ everything + beat every direction" = **(1) measurement loop → (2) activate KG + chunking dormant code → (3) build the one missing piece (multimodal) → (4) win the 4 axes they structurally lack.** No rewrite. The single unlock is **measure first** — it turns the dormant code from "we think it helps" into "proven, on, and beating the paper."
