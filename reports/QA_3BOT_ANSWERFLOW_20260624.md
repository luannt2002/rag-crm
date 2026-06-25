# QA 3-bot answer-flow — live deep-test · 2026-06-24

> 3 per-bot QA agents, each: read corpus → generate ~18 corpus-derived questions → run live
> `/api/ragbot/test/chat` → agent-score (NO LLM judge) → evidence-pinned to `document_service_index` + psql.

## Scorecard
| Bot | Answer-rate | HALLU | Fail class |
|---|---|---|---|
| test-spa-id | **83.3%** (15/18) | **0** ✅ | 3 retrieval (category-grouping) |
| chinh-sach-xe | **44%** (8/18) | **0** ✅ | 7 retrieval (Z-fold/alias/list) + 3 generation |
| thong-tu-09-2020 | **88.9%** (16/18) | **0** ✅ | 1 retrieval-miss + 1 struct-collapse |

**HALLU = 0 trên cả 3 bot** (mọi refuse-trap refuse đúng — sacred holds). **TẤT CẢ fail là RETRIEVAL under-coverage** (corpus CÓ đáp án nhưng không retrieve/group được) = **silent false-refuse** (Coverage fail, KHÔNG phải honest-refuse). Faithfulness 1.0, Coverage thấp.

## Root causes (evidence-pinned, T1-Smartness, RETRIEVAL layer — KHÔNG fix bằng sysprompt)

### R1 — stats-index `entity_category` rỗng (spa: 152/163 rows) → category-grouping refuse
- "triệt lông combo nào" / "triệt lông rẻ nhất" / "massage dưỡng sinh" → node aggregation không assemble được subset vì category trống → refuse oan. Per-entity rows CÓ trong index.
- Fix: **populate `entity_category` khi ingest stats-index** (triệt lông/massage/gội đầu/CSD). Ingest: `document_service/__init__.py:_insert_stats_index` + `ingest_stages_final.py:430`.

### R2 — stats-index keyword match gaps (xe)
- **Z-fold gap**: `_fold` (`stats_index_repository.py:485-494`) collapse separator GIỮA 2 chữ số; `265/50ZR20` có Z → "26550zr20" ≠ "2655020" → variant Z-rated drop (6 fail).
- **Aliases column không search**: cả 2 dòng đều có "265/50R20" trong cột aliases/question, nhưng `query_by_name_keyword` chỉ search `entity_name`/`entity_category`.
- Fix (cleanest): **index cột aliases vào 1 field search-able** (entity_synonyms) + `query_by_name_keyword` OR-match field đó → giải cả Z-variant lẫn alias-phrasing generic.

### R3 — stats-index 4× duplicate (spa) → re-ingest dedup leak. Data-hygiene. Fix: dedup khi insert stats-index.

### R4 — aggregate-route bất nhất (xe ag1/ag2): "rẻ nhất/đắt nhất" → bot né thay vì chạy `top_by_price`. (spa rẻ/đắt nhất WORK → phụ thuộc intent-classify + category). Fix: đảm bảo aggregation intent → stats `top_by_price` path, không fallback refuse.

### R5 — vector/grade collapse về 1 chunk (legal struct "Chương I gồm điều nào" + xe alias-factoid): intent multi_hop/factoid → top_k thu về 1 graded → trả thiếu. Fix: per-intent top_n cho struct/list + grade-leniency (đã có DEFAULT_RERANK_TOP_N_BY_INTENT, cần tune).

## Priority (T1 — bot trả lời thông minh)
1. **R1** populate entity_category (spa +3, lớn nhất) · **R2** aliases-index + Z-fold (xe +6).
2. **R3** dedup stats-index · **R4** aggregate-route.
3. **R5** grade/top_n tune (legal struct + xe alias-factoid).

> Tất cả fix ở **RETRIEVAL/INGEST layer**, KHÔNG sysprompt (lesson 2026-06-03: fix sai tầng = lãng phí). Mỗi fix cần TDD + re-run 3-bot QA đo Coverage delta.
