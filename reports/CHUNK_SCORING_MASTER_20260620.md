# Chunk-scoring master scorecard — ekimetrics methodology on live ragbot

**Date:** 2026-06-20 · **Branch:** expert-rag-squash-conflate-logcenter
**Method source:** Ekimetrics *Adaptive Chunking* (LREC 2026, arXiv:2603.25333)
**Harness:** `scripts/score_chunks_intrinsic.py` (L1) · `scripts/bakeoff_chunking_strategies.py` (bake-off)
**Raw outputs:** `reports/intrinsic_live_20260620.{md,json}` · `reports/bakeoff_chunking_20260620.{md,json}`

> Rule #0 (CẤM ĐOÁN) discipline: every number below is from a real run on the
> live corpus. Every caveat is labelled. Absolute composites are NOT claimed
> comparable to the paper's published 91.07 (different metric impl — see §3).

---

## 1. What was measured

Two ground-truth-FREE passes over the 3 live bots (8 documents):

| Pass | Question | Script |
|---|---|---|
| **L1 intrinsic** | How do the *currently stored* chunks score on the 5 ekimetrics metrics? | `score_chunks_intrinsic.py` |
| **Bake-off** | Does the adaptive `select_strategy` pick the highest-scoring strategy per doc? | `bakeoff_chunking_strategies.py` |

Metric impl = `ragbot.shared.intrinsic_metrics` — **lexical** (Jaccard / regex /
size-band), NO embedder, NO coreference. The paper's ICC/DCC use embedder
cosine; RC uses Maverick coref. **⇒ absolute numbers are an internal proxy,
not a paper-parity claim.**

---

## 2. L1 — intrinsic scores on stored chunks

| bot | docs | RC | ICC | DCC | BI | SC | composite |
|---|---:|---:|---:|---:|---:|---:|---:|
| chinh-sach-xe | 3 | 1.000 | 0.369 | 0.319 | 0.355 | 0.957 | 0.600 |
| test-spa-id | 4 | 1.000 | 0.543 | 0.446 | 0.344 | 0.954 | 0.657 |
| thong-tu-09… | 1 | 1.000 | 0.029 | 0.307 | 0.528 | 0.879 | 0.549 |
| **ALL (mean)** | 8 | 1.000 | 0.314 | 0.357 | 0.409 | 0.930 | **0.602** |

**Honest reading:**
- **RC = 1.000 = VACUOUS.** Defaults to 1.0 when no academic xref markers
  ("see section N", "figure N") are found. These corpora have none ⇒ RC
  carries no signal and inflates the composite by a flat +0.2. Discount it.
- **SC = 0.88–0.96 = real + healthy.** Child chunks (~256 char target) sit in
  band. The one solid positive.
- **ICC/DCC/BI low (0.03–0.53)** = partly real, partly lexical harshness on
  small leaf chunks. NOT directly informative vs the paper.
- **Composite 0.602 ≠ paper 0.91** — different impl. Do not cross-compare.

---

## 3. Bake-off — does the selector track the oracle?

Per-doc: re-chunk with 5 prose strategies at a common 1024-char budget, score
each, compare `adaptive_pick` vs `oracle_best` (highest composite).

Raw headline: **adaptive == oracle 0/8, lift over recursive +0.001**. This
headline is **MISLEADING** — decomposed honestly:

| Class | Docs | Verdict |
|---|---|---|
| **Table docs** (adaptive → `table_csv`) | 5/8 | Pick is **operationally correct** (row-as-chunk for price/service tables). Bake-off's "hdt wins" is an **artifact** — the lexical metric linearises the table and can't see row structure. Exclude from agreement. |
| **Ties** (1-chunk docs) | 2/8 | All strategies score equal. Not a real disagreement. |
| **Prose docs w/ headings** | 2/8 | **Real signal:** selector chose `recursive`, but `HDT` scored 0.07–0.16 higher. |

**The 2 prose docs:**
- `chinh-sach-xe/92e50a4c`: recursive 0.554 vs **hdt 0.625** (+0.071)
- `test-spa-id/c852544c`: recursive 0.598 vs **hdt 0.758** (+0.160)

**WHY HDT scores higher — likely metric artifact, NOT proven superiority:**
HDT prepends a structural path `[H1 > H2 > H3]` to every chunk. That repeats
high-frequency heading tokens, which inflates lexical **DCC** (chunk∩doc-gist)
and **ICC** (adjacent-sentence overlap). The lexical metric **over-rewards
heading repetition**. Whether HDT actually retrieves better is unknown from
this proxy.

---

## 4. Two findings, both pointing the same way

1. **The adaptive selector does not track the intrinsic oracle** on heading-rich
   prose docs (picks `recursive` over higher-scoring `HDT`). Worth investigating
   — BUT only after a trustworthy metric confirms HDT is genuinely better.
2. **The lexical intrinsic metric is itself weak** (RC vacuous; HDT heading-
   repetition bias). It is fine for a rough internal signal, NOT for driving
   selector changes.

⇒ **Do NOT "fix" the selector on this proxy** (would be fix-before-understand /
fix-on-biased-evidence — the exact anti-pattern CLAUDE.md §BUG-MANDATE forbids).

---

## 5. The decisive next metric — L2 retrieval (= paper Table 5)

The paper's headline win is **Retrieval Completeness** (Table 5), not the
intrinsic scores. The project already has the harness
(`scripts/eval_retrieval_hit_at_k.py`: Hit@K / nDCG@K / MRR) — it is **UNFED**
(only `sample_bot.jsonl` 3-row stub). To get the real, decisive numbers:

1. Build ~15 golden queries/bot with `expected_doc_ids` (qrels) — derive from
   `tests/scenarios/*_scenario.json`, human-review to avoid bias.
2. Wire a `RetrievalRunner` against live retrieval (HTTP to the running server,
   or a direct pgvector query).
3. Run → first **Recall@K / MRR / nDCG** on the real corpus.
4. THEN, if HDT helps retrieval on the 2 prose docs, revisit the selector.

L3 (answer Faithfulness / Coverage / Correctness = paper Table 5 col 2) follows
via `scripts/multistep_ragas_report.py` once L2 is green.

---

## 6. Scoring mindset (the reusable core)

```
L1  intrinsic (ground-truth-free, lexical)   → cheap signal, weak absolutes  [DONE]
       └─ bake-off: selector vs oracle        → selector audit               [DONE]
L2  retrieval (Hit@K / MRR / nDCG, qrels)     → DECISIVE, = Retr.Completeness [NEXT]
L3  answer (Faithfulness / Coverage)          → end-to-end, = Answer Correct. [AFTER L2]
```

Trust order: **L2 > L3 > L1**. L1 ranks ragbot's own strategies; only L2/L3
decide quality. Build qrels once → L2+L3 become repeatable regression gates.
