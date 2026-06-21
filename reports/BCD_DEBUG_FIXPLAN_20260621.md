# B/C/D Parallel Debug — Fix-Plan (3 agents, 2026-06-21)

3 read-only rag-debugger agents, evidence-backed. Opus executes serially + A/B-gated (each like B-1). Sorted by value × (1/risk).

## TIER 1 — high value, low risk (do first)

### F1. q02 keyword-pollution (Agent 2, Fix 1a) — REVERSES my earlier "accept"
- **Root:** `parse_list_query("Shop có những loại lốp nào, liệt kê giúp mình")` → keyword `"Shop lốp , giúp"` ("Shop"/"giúp"/"mình" NOT in `_LIST_STRIP_PHRASES`). Polluted → forward+reverse both 0 → `list_all_entities` (oldest 100, CITYTRAXX at rank 734+ by created_at) → no CITYTRAXX. Strip them → keyword `"lốp"` → 1033 rows → CITYTRAXX rank 77-78 (in first 100) → surfaces.
- **Fix:** add conversational fillers to `_LIST_STRIP_PHRASES` (query_range_parser.py:339-356): shop/bên shop/cửa hàng/giúp/mình/nhé/ạ (domain-neutral functional words, `\b` word-boundary safe).
- **Gate:** `parse_list_query(...).keyword == "lốp"`; eval xe COVERAGE ≥0.857 (q02 PASS); HALLU=0.
- **Note:** my earlier "accept q02 / model-line unsafe" was based on INCOMPLETE root-cause — missed the keyword pollution. This is the real fix (1 stopword list).

### F2. Reranker tie-break determinism (Agent 3, Issue 1 / D5a)
- **Root:** `jina_reranker.py:292-305` + `litellm_reranker.py:108` sort by `score` only; tied 4-dp scores → API arrival order → flip across runs. (D5b temp-0 already shipped.)
- **Fix:** secondary sort key `(-score, -retrieval_score, chunk_index)` — both adapters. retrieval_score already in dict.
- **Gate:** flip-rate N=3 = 0 on thong-tu; 0 COVERAGE regression. Risk ~0 (sort order only).

### F3. Stats narration-noise (Agent 1, Issue 5)
- **Root:** `parse_table_chunks` reads `chunk["content"]` = CR-enriched ("Đoạn X nằm trong phần…"); contaminates 18% xe / 40% thong-tu entities. `ingest_stages_final.py:367` passes persisted (narrated) rows.
- **Fix (Option A, 3 lines):** build shadow rows using `meta.raw_chunk` for stats parse + cleanup query on document_service_index. No re-ingest for the code; existing rows need DELETE+re-extract (idempotent).
- **Gate:** contamination count → 0; COVERAGE price-range no-drop; HALLU=0.

## TIER 2 — medium (after Tier 1 measured)

### F4. condense_question price-token drop (Agent 2, Fix 2 / W-R5)
- **Root:** `retrieve.py:205` reads `original_query` (= Turn-N short text "rẻ hơn không") not the condensed query with the price → parse_range_query None → vector fallback → refuse.
- **Fix:** dual-parse — try original then `state["query"]` for range/code/list/price-of-entity parsers (retrieve.py:205-239).
- **Gate:** multi-turn price test fires stats route; HALLU=0.

### F5. q13/q11 precise-chunk capped (Agent 2, Fix 1c+W-I9)
- **Root:** synthetic "Nách: 199000 | Giá Combo 10 buổi: 1199000" — value present but de-contextualized ("Nách" ≠ "triệt lông nách"). B-1 A/B fed ALL 14 chunks → dilution (q11 regressed). 
- **Fix:** `stats_precise_chunk_enabled` flag (default off) feeding **capped-3** precise chunks for the MATCHED entity only (not 14); + strip `<chunk_context>` tags into a "context:" label. New const `DEFAULT_STATS_PRECISE_CHUNK_LIMIT=3`.
- **Gate:** spa q13 PASS (1199000); existing PASS hold; HALLU=0; CHUNK_RECALL up. Per-bot A/B.

## TIER 3 — CSV ingest (needs re-ingest; W-I1..6)

### F6. CSV RFC-4180 (Agent 1, Issue 1) — `csv_chunker.py:42,284` split("\n") shatters quoted multiline cells. Fix: `csv.reader` logical-row assembler. Re-ingest. Gate CHUNK_RECALL@5.
### F7. CSV header detection (Agent 1, Issue 2) — `_doc_table_header:217` first CSV-shape line wins (boilerplate). Fix: `_looks_like_column_header` guard. Re-ingest.
### F8. CSV oversized row (Agent 1, Issue 3) — depends on F6; sentence-split capped rows.
- F9. narrate-then-embed (Agent 1, Issue 4): **keep OFF** — code comment + CR_ROW_GATED prove neutral-to-negative on table corpora + HALLU risk. No fix.

## TIER 4 — observability (Agent 3, B-3)

### F10. Grounding cap + degraded (Agent 3, Issue 2 / W-G1) — `local_guardrail.py:422` max_sentences=5 hardcoded (zero-hardcode violation). Fix: `DEFAULT_GROUNDING_MAX_SENTENCES` const + `_pcfg` gate (set 10). D7a degraded-counter already shipped but not in dashboard.
### F11. Live coverage split (Agent 3, Issue 3 / B-3) — `tenant_analytics_service.py:216` conflates OOS-refuse vs corpus-miss-refuse; `RagasMetricAdapter` is a stub. Fix: split `refusal_reason` (RETRIEVAL_MISS vs OOS) + `coverage_pct` field. No schema migration (uses existing column).

---
*Agents: a098c089584e89a84 (ingest), ac9b41a7fb1777595 (retrieval), a56f4d173b43a0faf (determinism/obs) — resumable via SendMessage.*
