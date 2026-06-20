# RAG deep root-cause — live eval 2026-06-20

**Harness:** `scripts/eval_rag_endtoend.py` (live + layer-split) · raw:
`reports/rag_endtoend_raw_20260620.jsonl` · agent: `rag-debugger` (installed).
**Discipline:** rule #0 — every line is FACT (evidence) or HYPOTHESIS (labelled).
Two false alarms were caught and retracted mid-investigation (logged below).

---

## 0. Headline (honest)

- **HALLU = 0 — sacred INTACT (verified).** The 3 raw "HALLU_BREACH" were
  detector false-positives: the bot correctly refused ("em chỉ hỗ trợ về lốp",
  "chưa hỗ trợ được") or returned empty ("Điều 99" → ""). Empty ≠ fabrication.
- **COVERAGE raw 0.77 is UNDERSTATED** — a measured "failure" was proven to be
  the bot being CORRECT against a WRONG scenario (see §2). The real coverage is
  higher; it cannot be trusted until the ground-truth is audited.
- **Real RAG weaknesses found (FACT):** aggregation-query retrieval, low dense
  match on tabular CSV, a CITYTRAXX false-refusal, 15 chunks with Chinese
  contextual-enrichment, and a non-hybrid answer path that logs no chunk refs.

Raw scorecard (post detector-fix):

| bot | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss |
|---|---:|---:|---:|---:|---:|
| chinh-sach-xe | 0.71 | 0.29 | 0.00 | 1 | 0 |
| test-spa-id | 0.60 | 0.40 | 0.00 | 1 | 0 |
| thong-tu | 1.00 | 0.60 | 0.00* | 0 | 0 |

\* thong-tu/q10 raw-flagged HALLU but answer was empty = no fabrication = pass.

---

## 1. TWO false alarms caught (rule #0 working)

1. **"COVERAGE 7%, HALLU 100%"** (first run) — was **QUOTA_EXHAUSTED**. All
   bots had `tokens_used` (31801/27048/65229) > limit (`max_tokens_total`
   default 10 000). Requests blocked at the quota gate in ~9 ms, empty answer,
   `chunks_used=0`. NOT a RAG failure. Lifted via `bots.bypass_token_check=true`
   (ops kill-switch) + Redis cache bust. **⚠ bypass is STILL ON for the 3 test
   bots — revert to false when testing ends.**
2. **"bot fabricated 60.000"** — RETRACTED. `'60.000'` (dotted) = 0 chunks but
   `'60000'` = 3 chunks; corpus has "Gội đầu thư giãn - dầu thường,30 phút,
   **60000**". The bot was GROUNDED and CORRECT.

---

## 2. Ground-truth (scenario) errors — the #1 contaminant

**FACT — test-spa-id/q08** "Dịch vụ nào rẻ nhất?" `expect=99000`. Bot answered
"Gội đầu thư giãn - dầu thường, 60.000đ". Corpus row: `Gội đầu thư giãn - dầu
thường,30 phút,60000`. **60000 < 99000 ⇒ the BOT is right, the SCENARIO expect
is wrong.** This "WRONG" is a TEST-layer error, not a RAG bug.

**HYPOTHESIS** — q06 (`129000`), q09 (`3000000`) likely the same (specific
price/format mismatches). The spa scenario needs a full audit before its
COVERAGE 0.60 means anything.

**Implication:** you cannot tune RAG against a contaminated ground-truth (the
ekimetrics lesson: ground-truth quality gates the whole eval). **Audit the 3
scenarios FIRST.**

---

## 3. Real RAG findings (FACT, layer-attributed)

### A. Aggregation queries collapse retrieval — RETRIEVAL layer
`chinh-sach-xe/q02` "liệt kê tất cả lốp" → only **2** chunks retrieved at
`score 0.110 / 0.059` (both contained CITYTRAXX via refs join). The query has no
product token, so dense similarity to specific CSV product-rows is inherently
low; top-K returns 2 of ~197 product rows. **"List-all / enumerate" intent
needs whole-table retrieval (`table_dual_index` group chunk), not top-K
semantic.** FACT: refs showed rank0=0.110, rank1=0.059.

### B. Low dense match on tabular CSV corpus — RETRIEVAL layer
Across xe/spa, real retrieved scores are 0.06–0.46 (vs a healthy ≥0.5). The
corpus is CSV rows ("Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P"); prose
queries embed far from tabular rows. Levers: row-narration at ingest
(narrate-then-embed, flag-OFF today), CSV-aware query expansion, or BM25 weight
bump for exact tokens (sizes/brands).

### C. CITYTRAXX false-refusal — SYSPROMPT/retrieval boundary
`q03` "CITYTRAXX H/T đặc điểm" → "không nằm trong danh mục" despite 197 corpus
chunks. The sysprompt frames the bot as "chỉ phân phối Landspider và Rovelo";
CITYTRAXX **is** a Landspider model line, so the LLM is over-gatekeeping. This
arrived via the no-ref path (§D) so retrieval grounding is unconfirmed.
HYPOTHESIS: sysprompt brand-gate + weak retrieval combine. NOT to be fixed by a
sysprompt rule before retrieval is confirmed (spa-07 wrong-layer lesson).

### D. Non-hybrid answer path logs no chunk refs — OBSERVABILITY + measurement
`q03/q10/spa-q08` returned `top_score=1.0, chunks_used=1, ZERO
request_chunk_refs`. Semantic cache is bypassed (verified: `check_cache.py:50`
honours `bypass_cache`, propagated at `chat_routes.py:432`). So a different path
(rerank degenerate 1.0 / CAG / intent-router) answers WITHOUT writing refs ⇒
`CHUNK_RECALL` is structurally **understated** for these, and retrieval
grounding is invisible. HYPOTHESIS (unverified): exact mechanism of the 1.0
score. Fix: log refs on every answer path.

### E. Chinese contextual-enrichment — INGEST layer (limited)
15/549 chinh-sach-xe chunks carry CJK text inside `content`, e.g.
`<chunk_context>货物描述 - 城市轨迹CITYTRAXX系列乘用车轮胎...</chunk_context>`. The
enrichment prompt says "use the same primary language as the document"
(`contextual_chunk_enrichment.py:53`) but gpt-4.1-nano/mini emitted Chinese on
ambiguous CSV rows. 2 chunks leaked CJK into `content_segmented` (BM25). Limited
(2.7%) but pollutes those product chunks' vectors. Fix: re-enrich the 15 or
disable cr_enhanced for CSV ingest.

---

## 4. Prioritised fixes (right layer, T1 first)

1. **AUDIT the 3 scenarios** (TEST) — fix wrong `expect` (q08 99000→60000, recheck
   q06/q09). Cheapest, unblocks a trustworthy COVERAGE. **Do this before any RAG
   tuning.**
2. **Aggregation-query retrieval** (RETRIEVAL) — route "liệt kê/list-all/tất cả"
   intent to `table_dual_index` whole-table chunks.
3. **Instrument the no-ref path** (OBS) — write `request_chunk_refs` on every
   answer path so CHUNK_RECALL is real.
4. **CSV retrieval quality** (RETRIEVAL/INGEST) — narrate-then-embed rows / BM25
   exact-token boost; measure with the harness before/after.
5. **CITYTRAXX brand-gate** (SYSPROMPT) — only after 2–3 confirm retrieval
   surfaces CITYTRAXX; then relax the "chỉ Landspider/Rovelo" framing.
6. **Re-enrich 15 CJK chunks** (INGEST).

## 5. Evidence ledger
- Quota: response `blocked_reason=QUOTA_EXHAUSTED`, `bots.tokens_used` query.
- q02 refs: `request_chunk_refs` JOIN → rank0 0.110 / rank1 0.059, both CITYTRAXX.
- q08: corpus `Gội đầu thư giãn - dầu thường,30 phút,60000` (ILIKE '%60000%').
- CJK: `content ~ '[一-鿿]'` = 15 (xe), 0 (spa/thong-tu).
- Cache bypass: `check_cache.py:50`, `chat_routes.py:432`.
- Detector fix: `eval_rag_endtoend.py` `_REFUSAL_MARKERS` broadened.
