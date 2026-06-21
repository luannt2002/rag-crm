# Port AdapChunk → ragbot: measurement-driven adaptive chunking that SURPASSES the paper

**Goal:** bring ekimetrics AdapChunk's strengths (adaptive per-doc chunking selection + the 5 intrinsic metrics + rigorous eval methodology) INTO ragbot — to (a) close the chunking gap, (b) light up our dead-wired ideas with proof, (c) SURPASS on the dimensions their research-library structure cannot reach.

**Root cause being fixed** (why we "lose despite having the ideas"): we built features but never closed the MEASUREMENT LOOP → ideas sit OFF (unproven) + chunking stays rule-based (can't optimize blind). AdapChunk's entire contribution IS the measurement-driven selection. We port that discipline, then beat it with the real objective.

## License + scope (verified)
- AdapChunk core = **MIT** → metrics.py (SC/ICC/DCC/BI/SD), splitters, selector logic are **commercially portable**. ✅
- `[coref]` (maverick-coref, RC metric) = **CC-BY-NC-SA non-commercial** AND **English-only** → DO NOT port. Their RC does not even apply to our VN corpus. → our chance to surpass with a multilingual reference metric.
- EVOLVE-not-rewrite: port the METHODOLOGY + MIT code INTO our Port+Adapter+DI+Registry; never rewrite the pipeline.

---

## Phase 0 — MEASUREMENT FOUNDATION (prerequisite; rule #0)
Without this nothing is provable — this is the actual root-cause fix.
- ✅ **B-1 done**: CHUNK_RECALL is real (stats attribution shipped 61b7a7a).
- **B-2 rigorous eval harness**: port AdapChunk's RAG-eval *methodology* (`paper/rag_eval.py`, MIT) adapted to our pipeline. Build qrels (≥15 golden/bot, expected_doc_ids) across ≥3 domains; N≥3 runs; report Retrieval-Completeness + Answer-Correctness + **HALLU** (the dim they lack) with Wilcoxon p-values.
- **Gate:** an A/B can produce a significant (p<0.05) before/after on real qrels. *(no significance harness = can't claim "hơn" — forbidden by rule #0.)*

## Phase 1 — PORT intrinsic metrics (MIT, embedding-cosine)
- New Port `application/ports/chunk_quality_port.py` (Protocol: `score(chunks, doc) -> dict[metric, float]`).
- Adapter `infrastructure/chunk_quality/ekimetrics_quality.py` porting metrics.py (MIT): SC, ICC (`compute_intrachunk_cohesion`), DCC (`compute_contextual_coherence`), BI (`compute_block_integrity`), SD. Reuse our Jina embedder (we already have CC/SD in `scripts/score_chunks_embedding.py` — extend to the full suite, replace the lexical `intrinsic_metrics.py`).
- RC: **skip maverick**. Stub RC=N/A OR a domain-neutral multilingual reference proxy (LLM-based entity-continuity check) — flagged separate, off by default.
- Registry + Null adapter + DI per the project's Strategy rules.
- **Gate:** the ported metrics reproduce AdapChunk's published numbers on a shared English doc (sanity), then run on our 3 bots — replacing today's lexical 0.59 composite with real embedding scores.

## Phase 2 — PORT the adaptive selector (their SOTA)
- Replace the dead-wired lexical `select_strategy` ekimetrics path (`shared/chunking/analyze.py`) with the REAL embedding-metric selector: for each doc, compute metrics × {our strategy pool} → pick best (port `compute_metrics_per_origin` + best-pick logic, MIT).
- Strategy pool = OUR richer set (table-CSV, HDT, parent-child, recursive, semantic) > their 4 methods.
- Wire via existing Registry; per-bot config flag; default off until Gate passes.
- **Gate (A/B on B-2 harness):** adaptive selection ≥ best fixed-strategy on intrinsic AND on CHUNK_RECALL; HALLU=0 hold; 0 COVERAGE regression.

## Phase 3 — SURPASS: select by REAL retrieval, not the proxy ⟵ the unlock
- AdapChunk selects by intrinsic metrics (a *proxy* for retrieval — forced, they have no serving). **We have B-1 CHUNK_RECALL** → select the chunking strategy that maximizes ACTUAL downstream retrieval on a query sample, not the proxy.
- Hybrid objective: intrinsic (fast, ground-truth-free, for cold docs) → validated/overridden by CHUNK_RECALL on docs with query traffic.
- **Gate:** retrieval-grounded selection beats intrinsic-only selection on CHUNK_RECALL (the metric that actually matters), with significance. This is strictly-better than optimizing a proxy.

## Phase 4 — PROVE "hơn" + win the dimensions they lack
- Run the B-2 rigorous comparison: ragbot-adaptive vs AdapChunk-recursive vs page, multi-domain, N≥3, Wilcoxon.
- Add the dims AdapChunk's eval omits → where we structurally win:
  - **FAITHFULNESS / HALLU** (they measure RC + Answer-Correctness, NOT hallucination).
  - **Multilingual (VN)** (their corpus + coref are English-only).
  - **Live-replay** (real traffic vs a fixed 99-query benchmark).
- **Gate:** demonstrate (p<0.05) ragbot ≥ AdapChunk on THEIR metrics + strictly wins on HALLU + VN + live → published-grade claim of "hơn".

---

## Constraints (woven through)
EVOLVE-not-rewrite · Port+Adapter+Registry+Null+DI for every new component · HALLU=0 sacred (chunking changes A/B-gated on it) · domain-neutral · no-version-ref · zero-hardcode (metric thresholds in `system_config`/`shared/constants`) · MIT-only ports (no maverick) · every phase gated on B-2 significance (no claim without measured proof).

## Honest sequencing
Phase 0 (B-2) is the hard gate — it is the missing measurement loop, the literal root cause. Phases 1-2 = catch the paper (port MIT metrics+selector). Phase 3 = the structural surpass (real objective). Phase 4 = prove it + win their blind spots. **Order is immutable: measure → port → surpass-by-real-objective → prove. Skipping measure = repeating the mistake that left us behind.**
