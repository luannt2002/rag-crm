# Bug investigation: comparison flow 0/4 (G-095/096/097/098)

> Evidence-only (rule #0). Data from live DB (document_service_index, request_logs,
> request_steps) + code file:line. A fix was attempted, measured, and REVERTED
> because it regressed — documented honestly here.

## 1. Bug gì
- Câu hỏi (verbatim): "So sánh giá Landspider 195/60R15 G/P và Landspider 265/60R18 H/T, loại nào đắt hơn?" (G-097)
- Đáp án đúng (corpus): 265/60R18 LPD = **1.944.000** > 195/60R15 = 963.000 → Landspider 265/60R18 đắt hơn.
- Bot trả (req c6d81780, load-test 2026-07-08): tìm được vế-1 (195/60R15 = 963.000) nhưng "**chưa có thông tin về 265/60R18 H/T**" → refuse vế-2.
- Diff: corpus CÓ vế-2 đúng giá (verified: `document_service_index` có "2-R18 265/60 LPD" price=1944000) → **false refuse**, không phải data gap.

## 2. Nguyên nhân trực tiếp
- Layer: RETRIEVAL (stats-index route).
- Số liệu: `request_steps` retrieve = `{"source":"stats_index","operation":"keyword","entity_count":1}` → chỉ **1 entity** tra được, comparison cần 2.
- Evidence: request_steps của c6d81780; `adaptive_decompose` chạy 45.5s nhưng `_decompose_active` = False (single stats route đã fire).

## 3. Gốc rễ (chain) — 2 tầng
- **L1**: single stats route lấy 1 code. `parse_code_query` (`shared/query_range_parser.py:489`) dùng `re.search` → **chỉ code ĐẦU** ("195/60R15"), bỏ code thứ 2.
- **L2**: fallback đáng lẽ là decompose fan-out (`nodes/retrieve.py:367` guard `not _decompose_active`), NHƯNG LLM decomposer (`nodes/query_decomposer.py`) **không tách** "So sánh A và B" cho spec-code → `state["sub_queries"]` < 2 → `_decompose_active`=False → single route chạy.
- **L2b (latent)**: kể cả khi decompose fire, `_stats_chunks_for_sub_queries` (`nodes/retrieve.py:152`) dedup theo `chunk_id`, mà mọi synthetic chunk dùng CHUNG hằng `DEFAULT_STATS_SYNTHETIC_CHUNK_ID="stats_index_synthetic"` → leg-2 bị drop as duplicate.
- **L3 (immutable, lộ ra khi fix L1/L2)**: `query_by_name_keyword(code)` (ILIKE '%code%') **over-match theo SIZE, bỏ qua BRAND/pattern**: "265/60R18" khớp 3 biến thể (LPD 1.944.000, RVL 2.295.000, LPD-116TWT 2.412.000). Câu hỏi chỉ ra "Landspider ... H/T" = LPD 1.944.000, nhưng 3 lựa chọn → LLM không chốt được → defer.

## 4. Expert solution
- **Đã thử (REVERTED)**: thêm `extract_all_codes` (`query_range_parser.py`) + nhánh multi-code deterministic trong `retrieve.py` (tra từng code, unique synthetic id). **Đo LIVE 4 câu**: retrieve nay lấy đủ 2 vế (`source=stats_index_multi`, entity_count=2-3) NHƯNG answer **regress → defer cả 2 vế** (brand over-match L3). Vẫn 0/4 + tệ hơn UX vế-1 → **REVERT nhánh** (giữ `extract_all_codes` + test cho fix đúng).
- **Fix đúng tầng (chưa ship)**: L3 brand+size disambiguation — per-leg lookup phải lọc theo BRAND cạnh code trong query (entity_name chứa "LANDSPIDER"/"ROVELO"/"DAVANTI"), không chỉ size code. Tận dụng brand-aware retrieval ADR-0008 (`shared/brand_scope.py`/`document_stats.py`). Đây là retrieval-quality change lớn hơn, cần đo N≥10 trước khi bật.
- Pattern: multi-vector/entity-scoped structured retrieval; deterministic (không phụ thuộc decompose LLM chậm/flaky).

## 5. Trạng thái
- `extract_all_codes` + `tests/unit/test_extract_all_codes.py` (6/6) GIỮ (utility cho fix L3).
- Nhánh multi-code trong `retrieve.py` + 2 import: **REVERTED** (không ship regression).
- Comparison vẫn 0/4 — chờ fix L3 (brand disambiguation), sẽ do deep-audit (workflow) soi kỹ tầng stats/retrieve rồi ship có đo.
