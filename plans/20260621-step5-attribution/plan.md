# [T2-CostPerf/Observability] Phase B-1 — STEP-5 retrieval attribution (make CHUNK_RECALL real)

**Goal:** make the retrieve layer MEASURABLE. Today CHUNK_RECALL = 0.14–0.60 is partly an artifact: the stats route's answer chunk is never written to `request_chunk_refs`, so the eval is blind to whether retrieval brought the answer. This is the #1 measurement blind spot (program axis ĐỦ 🔴; rule #0 measure-don't-guess).

---

## Root cause (evidence)

1. Stats route returns a SYNTHETIC chunk with `chunk_id = DEFAULT_STATS_SYNTHETIC_CHUNK_ID` (a non-UUID sentinel) — [query_graph.py:2418](src/ragbot/orchestration/query_graph.py#L2418).
2. The ref-writer skips non-UUID chunk_ids (FK to `document_chunks.id`) — [request_log_repository.py:200-206](src/ragbot/infrastructure/repositories/request_log_repository.py#L200). → synthetic chunk → **0 refs written** → CHUNK_RECALL blind.
3. Deeper: `document_service_index.record_chunk_id` is **100% NULL** (W-I9) — entities don't link to their source chunk. Verified: 2107 entities, 0 have FK, but **2107 have `chunk_index` in attributes_json**, and `(record_document_id, chunk_index)` → `document_chunks` is a clean **1-to-1** map (1954 distinct = 1954 chunks). → backfillable WITHOUT re-ingest.

## ⚠️ Risk gate (why this is Phase-4-class, not a quick patch)

Naively populating `record_chunk_id` makes the stats route fetch `linked_chunks` and ADD them to the LLM context. The code comment at [query_graph.py:2426-2430](src/ragbot/orchestration/query_graph.py#L2426) documents the hazard: adding raw table chunks "reintroduces variant-blob noise" and the LLM "fabricates a price/stock from a near-duplicate row" → **HALLU=0 sacred at risk**. The synthetic-only design exists precisely to avoid this.

→ The fix MUST **decouple attribution from context**: write the real source chunk_ids as `request_chunk_refs` (for measurement) WITHOUT adding raw chunks to the LLM context (preserve HALLU=0).

## Parts

### Part 1 — Backfill `record_chunk_id` (alembic, no re-ingest)
- Migration `backfill_stats_chunk_fk_20260621` (down_revision `align_model_stack_jina_20260619`).
- `UPDATE document_service_index si SET record_chunk_id = dc.id FROM document_chunks dc WHERE si.record_chunk_id IS NULL AND si.attributes_json ? 'chunk_index' AND dc.record_document_id = si.record_document_id AND dc.chunk_index = (si.attributes_json->>'chunk_index')::int`.
- Downgrade: NULL only the chunk_index-derived rows (faithful — all currently NULL).

### Part 2 — Forward: `bulk_insert` writes `record_chunk_id` + 2 repo SELECTs add it
- `stats_index_repository.bulk_insert`: resolve `record_chunk_id` from `(record_document_id, entity.chunk_index)` so future ingests populate it.
- Add `record_chunk_id` to `query_by_price_range` ([:233](src/ragbot/infrastructure/repositories/stats_index_repository.py#L233)) + `list_all_entities` ([:396](src/ragbot/infrastructure/repositories/stats_index_repository.py#L396)) SELECTs (keyword + top_by_price already have it).

### Part 3 — DECOUPLE attribution from context (the HALLU-safe core)
- In `_do_stats_lookup`: the synthetic chunk carries `attribution_chunk_ids = [e.record_chunk_id for e in entities if e.record_chunk_id]` (its real sources), but the LLM context stays **synthetic-only** (do NOT append `linked_chunks`).
- The ref-writing path (callbacks → `record_request_log`) writes `request_chunk_refs` from `attribution_chunk_ids` (real UUIDs) in addition to the context chunks. Decouple the two lists so attribution is complete while context is unchanged.
- Net: CHUNK_RECALL can now check the real source chunks; LLM sees the same synthetic text → HALLU=0 preserved.

## TDD + Gate (mandatory before ship)
- Unit: `_build_chunk_refs` writes attribution_chunk_ids; synthetic-context unchanged; backfill SQL maps 1-to-1.
- **A/B eval (xe + spa + thong-tu)**: gate = HALLU=**0** hold (sacred) · COVERAGE no-drop (xe≥0.86, spa/thong-tu=1.00) · **CHUNK_RECALL materially up** (the win). If any HALLU breach or COVERAGE drop → the decouple failed (context leaked) → fix before ship.
- Reviewer ≠ builder (program sacred #6).

## Why NOT done ad-hoc this session
Turn ~1750 of a marathon; HALLU=0 is sacred; the program reserves query-path changes for gated Phase-4 (failing-test-first, A/B, independent review). Scoped here for a focused execution with a fresh context. Feasibility is proven (backfill 1-to-1); execution is ~1 focused session.

## DoD
- [ ] CHUNK_RECALL real on stats path (refs written for stats-route turns)
- [ ] HALLU=0 hold · COVERAGE no-drop (A/B verified, N≥1 per bot)
- [ ] record_chunk_id populated (backfill) + forward-write (bulk_insert)
- [ ] LLM context unchanged on stats path (decouple verified — no raw-chunk leak)
- [ ] unit + A/B tests green; CLAUDE.md compliance self-audit
