# Consolidated Assessment + Multi-Phase Plan — Ragbot (2026-06-21)

> Synthesis of 3 evidence streams: (A) `program/` Expert-Build memory (6-axis DoD, 226-item gap catalog, D1–D17, 6-wave plan), (B) scoring template + deep-dive reports (weakness inventory), (C) **fresh runtime ekimetrics** (2026-06-21). Reconciled with this session's data-quality work (noise-fix `e6e56cc`, q02, re-ingest).

---

## 0. EXECUTIVE VERDICT (honest)

1. **The project is ALREADY comprehensively assessed.** `program/` (Expert Build) holds a 6-axis Definition-of-Done, a **226-item labeled gap catalog (109 resolved / 48%, 54 open bugs)**, decisions D1–D17 + Wave 6, a **6-wave execution plan (W1→W6)**, and 5 gates. **A multi-phase plan already exists** — `program/EXPERT-PLAN.md`. This session's findings (stats-noise, q02, ekimetrics) are ONE SLICE (the data-quality cluster), not the whole picture.
2. **Framework = expert-grade; the gap = "built-but-not-wired."** The self-named meta-pattern: code is written + shipped but flag-OFF / 0-callsites / ops-gated. → confirms EVOLVE-not-rewrite.
3. **3 of 6 axes are RED**, and they are NOT the RAG-quality axes I focused on this session:
   - 🔴 **ĐỦ (coverage)** — not measurable live (RagasMetricAdapter stub; analytics conflate OOS-refuse vs silent-miss).
   - 🔴 **AN TOÀN** — RLS inert at runtime + API keys plaintext + exactly-once=at-most-once. **Code SHIPPED (W1)**, but **ops-gate OPEN** (needs `ragbot_app` DSN, KEK env, alembic 0196–0199).
   - 🔴 **KIỂM SOÁT** — feedback loop write-path live, read/learn path DEAD (0 subscribers/callers).
4. **Chunking is actually GOOD** — the low ekimetrics composite (0.54–0.65) is a **measurement artifact** (lexical approximation + blocked real metrics), NOT a real chunk-quality problem. Real signals (SC, embedding-CC) are excellent. **Do not chase the chunk-metric score.**
5. **NOT all issues are resolved** — ~48% (109/226). My session closed a handful (noise-fix, q02-decision); the data-quality cluster (CSV parser, record_chunk_id, narrate, aggregation-retrieval) is largely OPEN.

**My earlier (this-session) proposal was CORRECT but PARTIAL** — it named the data-quality + measurement themes, but the program's catalog is far larger and the true P0 blockers are security/data-loss (code-shipped, ops-gated), not the RAG-quality items.

---

## 1. EKIMETRICS — FRESH RUNTIME (2026-06-21, post xe re-ingest)

`scripts/score_chunks_intrinsic.py` (lexical) + `scripts/score_chunks_embedding.py` (real Jina-1024 cosine):

| Metric | xe | spa | thong-tu | Tag / reliability |
|---|---:|---:|---:|---|
| **SC** Size Compliance | **0.999** | **0.956** | **0.981** | REAL — ✅ excellent (chunks well-sized) |
| **CC** Contextual Coherence (embedding) | **0.974** | **0.972** | **0.906** | REAL embedding — ✅ excellent (chunks fit doc) |
| **SD** Semantic Dissimilarity | 0.016 | 0.020 | 0.041 | REAL embedding — low BY DESIGN (table rows similar; not a bug) |
| ICC Intrachunk Cohesion | 0.298 | 0.544 | 0.115 | ⚠️ LEXICAL Jaccard — NOT paper-comparable |
| DCC (lexical) | 0.296 | 0.446 | 0.363 | ⚠️ LEXICAL — superseded by CC |
| BI Block Integrity | 0.248 | 0.315 | 0.225 | ⚠️ SIZE-PROXY — misleading (real table integrity good) |
| RC Reference Completeness | 1.000 | 1.000 | 1.000 | ⚠️ VACUOUS (no xref markers; coref blocked) |
| **Composite (lexical)** | 0.568 | 0.652 | 0.537 | inflated by RC=1.0; lexical |

**Honest interpretation:** the **REAL** signals (SC 0.96–0.99, embedding-CC 0.91–0.97) say chunking is **GOOD**. The low composite is driven by (a) lexical Jaccard approximations of ICC/DCC (not the paper's embedding-cosine), (b) BI as a size-proxy (real atomic-row integrity is preserved by `table_csv`), (c) RC vacuous. **BLOCKED for paper-comparable:** ICC (needs sentence-level embeddings), RC (needs non-commercial Maverick coref), true-BI (needs gold block labels). → **Chunk-quality is not the bottleneck; the metric impl is the gap.** (W-E1.)

---

## 2. 6-AXIS EXPERT STATUS (program DoD)

| Axis | Target | Status | Blocker |
|---|---|---|---|
| **ĐÚNG** (correct/anti-hallu) | HALLU=0 + faith≥0.95 | 🟡 | grounding ≤5-sentence cap; nano-judge self-bias; tie-order nondeterminism |
| **ĐỦ** (coverage) | recall≥0.9, coverage≥0.95 LIVE | 🔴 | not measurable live (Ragas stub; analytics conflation); aggregation-retrieval broken |
| **AN TOÀN** (tenant-iso) | leak-test 0-row as `ragbot_app` | 🔴 | RLS inert (superuser DSN); keys plaintext — **code shipped, ops-gate open** |
| **NHANH** (latency) | p95 tiered + SLO alert | 🟡 | no SLO-breach alerting; ingest fairness; OOM on big docs |
| **RẺ** (cost) | per-(tenant,purpose), cache≥30% | 🟡 | ingest LLM cost=$0 hardcoded; no per-tenant cost read-query |
| **KIỂM SOÁT** (governed) | every decision logged + feedback loop closed | 🔴 | feedback read/learn path dead; judge self-family; config drift |

**Expert = all 6 green.** Currently 3🔴 / 3🟡 / 0🟢. The RAG-quality I optimized this session lives mostly under ĐÚNG/ĐỦ but the axes are RED for **measurement + wiring** reasons, not core-RAG reasons.

---

## 3. RESOLVED vs OPEN (this session + program)

**Resolved this session:** stats question:/date1: noise (`e6e56cc`), q02 decision (accepted, model-line proven-unsafe), re-ingest xe with no churn. **Program-resolved (sample):** W1 code 6/6 (RLS hook `c2bf270`, key-encrypt `83fee63`, exactly-once `e85f2b8`, lifecycle-purge `6121de8`, DI-parity `1b06a46`, S10-adjudication `18a31f5`), W2 core (workspace entity `b77dc6a`, quota-wire `cc39346`, ingest-fairness `8f91839`), finalize-resilience `7a60c47`, per-key limiter `e17c0f4`, attribution-bucket `93e77d9`.

**OPEN — the big clusters** (from deep-dive + program):
- **Data-quality / ingest** (W-I1..14): block-pipeline dead-wired (`parsed_blocks=[]`), narrate dormant, **CSV no RFC-4180 parser** (multi-line cell shatter), **wrong header detection** (boilerplate-as-header → embedding collapse), **FAQ mega-cell** (5287-char row), **`record_chunk_id` never populated** (price answers diluted with full table), **`answer` column dropped on stats path**, narration-sentence stats noise (this session's new finding), CJK contamination.
- **Retrieval** (W-R1..12): **aggregation/list-all intent broken** (q02 root), low dense-match on tabular CSV (0.06–0.46), condense_question drops price tokens (flaky routing), non-hybrid path logs no chunk-refs (CHUNK_RECALL understated), CITYTRAXX false-refuse.
- **Eval** (W-E1..5): L1 intrinsic not embedding-cosine; **L2 Hit@K harness unfed (no real qrels)**; scenario ground-truth contamination (partial-fixed).
- **Security/ops**: RLS ops-gate, keys ops-gate, `bypass_token_check` ON, OOM on big-embed, key-table drift (`ai_keys`/`api_keys`).
- **Application** (W6): feedback loop, SLO alerting, PDPL 91/2025, DR (RPO≈24h).

---

## 4. RECONCILED MULTI-PHASE PLAN (priority order)

> Backbone = program's 6 waves. Overlay = this-session data-quality + measurement-first principle. **Reordered by leverage × risk**, respecting T1>T2>T3 + EVOLVE-not-rewrite + don't-risk-verified-good.

### Phase A — CLOSE P0 (security/data-loss) · code DONE, OPS-GATE
Finish W1: provision `ragbot_app` (NOBYPASSRLS) DSN + run leak-test 2-tenant 0-row; set `RAGBOT_CONFIG_KEK` + alembic 0196–0197 (key-encrypt) + `value_plain=NULL`; alembic 0198 (event_inbox, exactly-once) + 0199. Revert `bypass_token_check`. **Why first:** code already shipped; this is the highest-severity axis (AN TOÀN 🔴) and it's an ops finish, not a build. *(Mostly ops/human-track.)*

### Phase B — MAKE IT MEASURABLE (ĐỦ + KIỂM SOÁT observability) ⟵ **highest dev leverage**
1. **L2 retrieval qrels** — author ~15 golden queries/bot with `expected_doc_ids`; feed `eval_retrieval_hit_at_k.py` (today a 3-row stub) → real Recall@K/MRR/nDCG gate.
2. **Live coverage** — replace `RagasMetricAdapter` stub; split analytics OOS-correct-refuse vs corpus-has-answer silent-miss.
3. **STEP-5 attribution** — populate `record_chunk_id` at ingest + write `request_chunk_refs` on stats/non-hybrid paths → CHUNK_RECALL becomes real (today 0.14–0.60 is partly artifact).
**Why:** rule #0 — cannot improve ĐỦ blind. Unblocks evidence for Phase C. Low-risk (observability/eval, not query-path).

### Phase C — DATA-QUALITY (chunking + retrieval; the RAG-quality you care about)
Maps to program W3 (AdapChunk) + the W-I/W-R catalog. Highest-impact, evidence-gated by Phase B:
1. **CSV parser hardening** — RFC-4180 quoted-cell parser, header-detection fix, mega-cell split, boilerplate de-weight (W-I3/4/5/6). Root cause of low tabular dense-match.
2. **`record_chunk_id` populate + `answer`-column surface** on stats path (W-I9/I10) — stops full-table dilution + un-drops FAQ answers.
3. **Narrate-then-embed A/B** (W-I2) + **block-feed wire** (W-I1, D1) — the AdapChunk ceiling.
4. **Aggregation/list-all retrieval** route (W-R1) — whole-table intent (q02 class).
5. **Stats narration-noise** (this session) — stats parse raw_chunk not narrated content.
**Gate:** A/B coverage↑ on real qrels (Phase B), HALLU=0 hold, 0 regression. **No forced re-ingest churn** — fix in code, apply on natural re-ingest.

### Phase D — RETRIEVAL + ANSWER DETERMINISM (program W4)
Content-aware tie-break (D5a), claim-level grounding judge (D7b), true BM25 via VectorChord A/B (D-trueBM25), `rerank_input_pool`≠`top_n` two-stage.

### Phase E — COST + CONFIG GOVERNANCE (program W5)
Ingest LLM cost ledger (not $0), per-(tenant,purpose) cost read-query, config-drift reconcile (init vs alembic), embed-model-change guard, fix `validate_constants.sh` dead guard.

### Phase F — APPLICATION + OPS EXPERT (program W6)
Feedback read/learn loop (thumbs→eval→FAQ), SLO-breach alerting, sysprompt preview endpoint (ADR-S10 condition), PDPL 91/2025 (consent+export), WAL/PITR DR, key-table reconcile (`ai_keys`).

---

## 5. SINGLE HIGHEST-LEVERAGE RECOMMENDATION

**Do Phase B (measurement) before any more RAG-quality work.** Right now ĐỦ is 🔴 *because we can't see it live* — CHUNK_RECALL 0.14–0.60 is partly a measurement artifact (no `request_chunk_refs` on stats path; no real qrels). We are optimizing COVERAGE end-to-end while blind to the retrieve layer. **Fixing measurement is low-risk, unblocks evidence-driven improvement, and directly turns one RED axis measurable.** Then Phase C (data-quality) can be A/B-gated on real numbers instead of guessed.

This is the same lesson as q02 this session: without the right measurement, "improvements" (model-line) silently regressed verified-good bots. **See first, then improve.**

---

*Evidence anchors: `program/00-charter.md`, `program/EXPERT-PLAN.md`, `program/EXPERT-STATE-REPORT.md`, `program/decisions/00-DECISION-REGISTER.md`, `program/gaps/P2-*`, `reports/DEEPDIVE_{CHUNKING,RETRIEVAL,COMPLIANCE}_20260617.md`, `reports/CHUNK_SCORING_MASTER_20260620.md`, `docs/RAG_SCORING_TEMPLATE.md`, `STATE_SNAPSHOT.md`. Ekimetrics: `score_chunks_intrinsic.py` + `score_chunks_embedding.py` runtime 2026-06-21.*
