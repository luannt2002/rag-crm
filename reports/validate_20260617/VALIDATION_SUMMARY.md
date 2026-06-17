# Project Validation — 2026-06-17 (Jina migration + full end-to-end)

All logs in this dir: `unit_suite.log`, `reseed.log`, `loadtest_qa.log`,
`multiturn_replay.log`, `data_flow_verify.log` + `reports/LOADTEST_*validate20260617.json`.

## Step 1 — Unit suite
**6 failed / 5918 passed.** All 6 failures are no-git-env (`git ls-files` / worktree
/ secret-grep / template-grep). **0 code regression** from this session's changes
(csv table-fix trial reverted, csv tests green again).

## Step 2 — Clean reset + re-ingest (9 docs / 3 bots, pure-Jina)
700 chunks, **700/700 embedded** (0 missing). xe 486 · spa 134 · legal 80.
2-key round-robin + per-key concurrency (`{"jina":[2,2]}`) → ingest ~130s, no storm.
Ingest path = parse→chunk→**Jina embed**→store, **0 nano LLM** (5 nano paths OFF).

## Step 3 — Load test (QA harness, all 3 bots) + multi-turn consultation replay
| Bot | Coverage | HALLU | Notes |
|---|---|---|---|
| thong-tu (legal) | **100%** | 0 | perfect — exact dates/deadlines + article citation |
| chinh-sach-xe | ~86% | 0 | 1 retrieval miss (warranty), 1 data-gap |
| test-spa-id | 60–70% | 0 | aggregation/numeric questions miss (dưới-500k, đắt-nhất) |

**Multi-turn consultation (human-like) — validated:**
- Coreference ✓ ("quy trình của **nó**" → resolved to trị mụn from prior turn)
- Factoid ✓ (spa "trị mụn 700k", legal "01/01/2021 Điều 56", "báo cáo sự cố 24h Điều 54")
- **HALLU = 0** ✓ — all traps (phun-xăm / Michelin / mức-phạt) refused, no fabrication
- Consultation ✓ — asks clarification safely (no runaway-list fabrication after the 0233 HALLU-safe fix)

## Step 4 — Data-flow + table verification
**Upload flow:** documents 9 · chunks 700 · embedded 700 · document_service_index 1651.
**Query flow:** request_logs 52 · request_steps 1120 (15+ pipeline steps instrumented:
multi_query_fanout/rerank/retrieve/grade/filter_min_score/mmr_dedup/cache_check/generate/
prompt_build/guard_in/guard_out/litm_order/prompt_compression/query_complexity) ·
conversations 34 (multi-turn) · token_ledger query 155 rows ($0.0635 logged).
→ Every flow is observable + cost-logged. No dead pipeline.

## Verdict
- **HALLU = 0 across every bot + every trap (sacred holds).**
- Legal bot production-grade (100%). spa/xe solid except aggregation/numeric questions.
- Ingest pure-Jina, fast, no storm; query pipeline fully instrumented.

## Known-remaining (NOT blocking VN bots)
1. **Aggregation/numeric questions** (spa "đắt nhất/dưới 500k", measured): dense
   retrieval can't do numeric reasoning. Real fix = per-table LLM description
   (RAG-Anything Technique 1, O(tables)) or query-time aggregation node. The CSV
   key:value text reformat was trialled + measured neutral → reverted.
2. **xe warranty retrieval miss** — corpus has it, not in top-K (xe-specific tuning).
3. **P1 multi-language EN gates** (hybrid_search VN-tokenizer-always-on, math_lockdown,
   superlative, structured_ref) — affect EN bots only; VN bots unaffected.
4. **P2-2 reranker layering**, **P3 hardcode ~30 sites** — T3 code-quality.
</content>

---
## FINAL state + measured experiments (end of session)
Config: `embedding_provider=jina`, `narrate_then_embed_enabled=false` (pure-Jina known-good).
Data: 9 docs · 700 chunks · 700 embedded · req_logs 42 · req_steps 908 · conversations 32.

**Final load test (all 3, quiet):**
| Bot | Coverage | HALLU |
|---|---|---|
| legal | 100% | 0 |
| xe | 85.7% | 0 |
| spa | 60–70% | 0 |

**Aggregation — TWO text approaches MEASURED + reverted (rule#0):**
1. CSV→key:value rendering: cosine 0.418→0.377 (q02), 0.325→0.263 (q06) = no lift → reverted.
2. narrate-per-table (Technique 1, paced, CB_OPEN=0 — safe): spa flat 70%, **xe 86%→57% (worse)** → reverted.
Conclusion: dense retrieval + LLM cannot do NUMERIC-RANGE/aggregation ("đắt nhất", "dưới 500k")
via ANY text representation. The correct fix is a QUERY-TIME numeric aggregation node
(detect aggregate intent → scan price chunks → compare numerically in app code), NOT ingest text.
This is the one real remaining T1 user-facing feature.

**Deferred (do not affect VN bots):** P1 EN language-gates (capability: segment_vi_compounds
already takes `language`; needs caller wiring), P2-2 reranker layering, P3 hardcode, AdapChunk
full wire (Ekimetrics dead-code).
