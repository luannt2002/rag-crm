# Residual flow-hardening — fix TẤT CẢ residual + deferred (audit 2026-06-23)

> **Nguồn**: audit 44-agent (27 confirmed). Batch A+B đã fix 7 (CRIT+HIGH). Plan này = phần CÒN LẠI:
> 4 residual self-found (R1–R4) + verification-gap (R5) + deferred MED/LOW.
> **Ràng buộc (binding)**: chuẩn CLAUDE.md · generic — KHÔNG support riêng lẻ · TDD (RED→fix→GREEN, test thật) ·
> fix ĐÚNG TẦNG (data L1/L3, không sysprompt) · zero-hardcode → constants · sacred rules KHÔNG đụng · evidence-driven.
> **Mỗi phase = 1 commit riêng + full-suite verify**. Order: A→B→C→D→E (E là gate runtime).

---

## Phase A — T1 COVERAGE (data-layer, anti-HALLU) — ưu tiên cao nhất
| # | Finding | File:line | Fix generic | TDD test |
|---|---|---|---|---|
| A1 | **R3** aggregate exact-match INCOMPLETE — "Tổng thanh toán"/"Lũy kế"/"Cộng dồn" vẫn leak | `document_stats.py:159,441` | prefix-match aggregate-lead token GATED bằng word-count ≤ cap (total label terse 2–3 từ; "Tổng hợp dịch vụ…" 5 từ → giữ). Cap → constant | total đa-từ reject + "Tổng hợp dv" giữ |
| A2 | overflow-truncation — cell vượt `len(header)` bị drop (data loss) | `tabular_markdown.py:201` | iterate `max(len(header), len(row))`, pad header `colN` | row 4-cell / header 3-cell → cell 4 còn |
| A3 | header money-veto — header bị reject khi 1 label giống money ("30km","Tr5") | `tabular_markdown.py:89-98` | money-veto chỉ khi cell có currency-unit thật (không phải đo lường) | header "Vùng \| 30km" vẫn mở table |
| A4 | `_STATS_SECTION/METADATA_LEAD` false-drop tên "Phòng 12: Deluxe","VIP: Premium" | `document_stats.py:437-438,72-74,50` | siết regex: chỉ drop khi lead là stats-keyword thật, không phải "<từ> <số>: <desc>" | tên dạng "Phòng 12: X" giữ + price giữ |
| A5 | column-width mismatch sau mid-table heading → role lệch, drop row | `document_stats.py:348-361,567-570` | re-resolve header width per sub-table sau heading | row sau heading width khác → vẫn parse |

## Phase B — T2 tooling + retrieval guardrail
| # | Finding | File:line | Fix generic | TDD test |
|---|---|---|---|---|
| B1 | **R1** checker heading-misroute — catalog ≥3 `##` → DOC (price N/A). #5 chưa trọn cho multi-section (catalog THẬT của bot) | `check_happy_case.py:66 _is_doc` | có `\| --- \|` table → là SHEET dù ≥3 heading (table-bearing = sheet) | multi-section md → SHEET + HAPPY |
| B2 | CSV 1-comma mis-tag — prose 1 dấu phẩy → table | `analyze.py:189-194` | require ≥2 comma (≥3 field) qua `DEFAULT_CSV_MIN_COMMAS` | prose "a, b" không là table |
| B3 | **R4** #2 K-shorthand chỉ "199k", miss "1.500k"/"2tr"/"1.5tr" (drift-guardrail yếu) | `jsonb_conversation_state.py:56 _PRICE_RE` | mở rộng regex bắt `tr`/`triệu`/`m` suffix + apply multiplier; reuse skeleton từ `number_format` nếu an toàn | "1.5tr"→1500000, "2tr"→2000000 |
| B4 | phone-capture `_PRICE_CELL_RE` quá lỏng (bắt sđt/ngày) | `generate.py:90` | thêm magnitude-bound / reject phone-shape | "0901234567" không thành price |

## Phase C — T3 cleanup (orphan / dead-code)
| # | Finding | File | Fix | Note |
|---|---|---|---|---|
| C1 | **R2a** `summary_json` WRITE-ONLY orphan (0 reader sau xóa route) | `ingest_stages_final.py:405-408`, `document_service:296-309` | GỠ write (không reader; summary feature = summary-doc ingest). Verify `aggregate_summary` không dùng nơi khác | hoặc giữ + build reader đúng — chọn GỠ (simplicity) |
| C2 | **R2b** `query_graph` dead import + `matches_summary_pattern` giờ fully dead | `query_graph.py:100`, `query_range_parser.py:559,581` | gỡ dead import; function unused → gỡ + `__all__` | ruff F401 |
| C3 | **R2c** stale `fetch_summaries_by_bot` mock + comment | `test_retrieve_stats_index_routing.py:165,200,370,431,475,560` | gỡ dead mock setup + comment stale | harmless nhưng rác |
| C4 | `_md_escape` không escape backslash trước pipe (NIT) | `tabular_markdown.py:101-102` | escape `\` trước khi escape `\|` | round-trip an toàn |
| C5 | floor divergence `_is_pure_money`(0) vs `document_stats`(10000) | `tabular_markdown.py:56-68` | unify floor qua `DEFAULT_PRICE_MIN_VND` | nhất quán gate |

## Phase D — T3 test-gap (lock invariant — RED-first không áp dụng, là coverage add)
- D1 pipe escape↔split round-trip test (`tabular_markdown` escape ↔ `document_stats` split).
- D2 price-bucket boundary off-by-one (strict-`<`) test.
- D3 TSV (tab-separated) split branch test (`document_stats:281-282`).
- D4 empty / all-blank converter input test.
- D5 strengthen weak assertion `test_happy_case_template` (table atomicity thật, không chỉ set-membership).

## Phase E — RUNTIME VERIFY (GATE) — biến baseline-static → VERIFIED (R5)
> Rule #0: code-correct (unit) ≠ runtime-proven. Đây là gate bắt buộc trước khi tuyên bố "đạt".
1. **Unblock integration**: `/var/www/html/ragbot` hardcode → env/relative (`tests/integration/*:ROOT`, `conftest`). 6 collection-error hiện chặn full e2e.
2. **Re-ingest 3 bot** happy-case qua luồng real L1→L7 (verify chunk vào DB).
3. **Load-test** Q1→Q8 toàn bộ câu hỏi → chấm **Coverage + Faithfulness** (delta before/after batch A+B+này).
4. **Gate pass**: Coverage tăng (hoặc giữ) · HALLU=0 · no-regression. Fail → post-mortem, rollback fix gây hại.

---

## Tổng quan
- **~18 fix** (A:5 · B:4 · C:5 · D:5) + runtime gate (E).
- **Tier**: A=T1 (Coverage/anti-HALLU) · B=T2 (tooling/guardrail) · C/D=T3 (cleanup/test) · E=verify.
- **Files dự kiến**: `tabular_markdown`, `document_stats`, `analyze`, `jsonb_conversation_state`, `generate`, `check_happy_case`, `retrieve`, `query_graph`, `query_range_parser`, ingest pipeline, integration tests, constants.
- **Sacred KHÔNG đụng** · mỗi phase commit riêng + verify · A là cao nhất (đụng Coverage trực tiếp).

## Status: A ⏳ · B ⏳ · C ⏳ · D ⏳ · E ⏳ (chờ user approve / chọn phase start)
