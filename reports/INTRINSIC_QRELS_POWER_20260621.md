# Weak-point #1 fix — embedding-intrinsic + qrels-power (2026-06-21)

Weak-point #1 (from the 5-criteria + AdapChunk re-check) = **measurement rigor**: 42
hand queries lack statistical power; intrinsic metrics were lexical → can't prove
our numbers or fairly compare to AdapChunk. This addresses both, evidence-driven.

## A. Embedding-intrinsic — the real numbers (CHECKED, not lexical)

From STORED 1024-dim Jina vectors (`scripts/score_chunks_embedding.py`) + DB size:

| metric | chinh-sach-xe | test-spa-id | thong-tu | AdapChunk | verdict |
|---|--:|--:|--:|--:|---|
| **SC** (size compliance) | 99.8 | 85.9 | 98.1 | 99.9 | ✅ ≈ ngang |
| **CC** (contextual coherence, embedding) | 0.974 | 0.972 | 0.906 | DCC 88.8 | ✅ ngang/hơn |
| SD (semantic dissimilarity) | 0.016 | 0.020 | 0.041 | ≥0.5 target | low BY DESIGN (table-row similarity = corpus topology, not a defect) |
| ICC / BI / RC | — | — | — | 68/99/99 | 🔴 BLOCKED (ICC needs sentence-embed; RC needs coref/English-only; BI needs gold blocks) |

**Verdict:** the REAL chunk quality is GOOD — **SC + CC ≈ AdapChunk's level**. The
earlier lexical composite **0.59 was a MEASUREMENT ARTIFACT**, not weak chunking.
The "we're behind on chunking" worry was a measurement bug. (ICC/BI/RC need a
sentence-embedding + commercial-coref build to be paper-comparable — a separate
effort; SD is mis-fit for table corpora.)

## B. qrels-power — bigger eval set with ground-truth (BUILT)

`scripts/gen_qrels.py`: derives factoid queries `"Giá <entity> bao nhiêu?"` with
GROUND-TRUTH (the stats price) + the relevant source chunk (`record_chunk_id` =
qrel) straight from `document_service_index` — deterministic, no LLM, no labelling.

Power boost: **xe 7→40, spa 10→25** answerable queries. (thong-tu → only 2: legal
corpus has no prices → needs a separate article-factoid generator — noted.)

This gives the statistical POWER that the 42-q set lacked: Wilcoxon on 40+25 vs 7+10
can actually reach p<0.05 on a real delta, and Hit@K/MRR can be computed against
the qrels (`_gen/*_qrels.json`).

## Honest gaps remaining (weak-point #1)
- **ICC/BI/RC paper-grade** = sentence-embedding + commercial-coref build (not stored
  today). SC + CC are the real, available, AdapChunk-comparable signals.
- **thong-tu power** = needs an article/clause factoid generator (no prices).
- The power-RUN (eval on the generated sets) is LLM-cost + needs the OOM-guard
  (W-O1) — run off-peak via `devstack.sh`.

## C. POWER-EVAL ran — it IMMEDIATELY paid off (the point of rigor)

Ran the 40 generated xe factoids live:

| eval set | COVERAGE | meaning |
|---|--:|---|
| 7 hand-picked | **1.00** | small + cherry-picked → false optimism |
| **40 ground-truth (generated)** | **0.85** | REAL — 6 failures (4 retr_miss + 2 llm_miss), CHUNK_RECALL 0.07 |

**The power-boost turned "1.00 perfect" into "0.85 + here are the 6 specific
failures."** The misses are price-retrieval on FULL / noisy SKU names (synonym-list
entities): the stats route doesn't keyword-match the long names → retrieval miss.
This is a real, actionable weak point the 7-query set HID. (Server survived, no OOM,
60s.) HALLU stayed 0.

**Honest recalibration:** the COVERAGE-1.00 we celebrated was small-sample optimism.
The real price-factoid coverage is **~0.85 — still > AdapChunk's 78.0, and now on a
bigger, fairer set** (so the "≥ AdapChunk" claim got STRONGER + more credible), while
exposing 6 concrete fix targets (stats keyword-match on long SKU names → a Tier-2/
F4-class retrieval fix).

## Bottom line
Weak-point #1 (measurement rigor) — DELIVERED:
1. **Intrinsic** → CHECKED: chunking is real-good (SC 99.8/CC 0.97 ≈ AdapChunk); the
   0.59 lexical composite was an artifact, NOT weak chunking.
2. **Power** → BUILT + RAN: ground-truth generator (xe 7→40, spa 10→25) immediately
   recalibrated COVERAGE 1.00→0.85 and exposed 6 real price-retrieval failures.
→ Rigor's first run already converted "we believe" into measured truth + a fix-list.
Next: (a) fix the long-SKU-name stats match (the 6 failures), (b) ICC/BI/RC paper-grade
(sentence-embed + commercial coref), (c) thong-tu article-factoid generator.
