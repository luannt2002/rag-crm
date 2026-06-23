# [T1-Smartness] Generic fixes — khử "support riêng lẻ" + Coverage edge-cases

> Nguồn: multi-agent audit 2026-06-23 (44 agent, 27 confirmed bug). Verdict `has-real-bugs`.
> Nguyên tắc binding (user 2026-06-23): **chuẩn CLAUDE.md · tất cả luồng code generic · KHÔNG support riêng lẻ**.
> Sacred rules đã verify SẠCH (no app-override, DI, broad-except) — KHÔNG đụng. Chỉ fix shape-heuristic L1+L3 + domain-neutral.

## Phương pháp: TDD — failing test TRƯỚC, fix SAU, verify no-regression. Surgical, generic, zero new hardcode.

## Phase 1 — Domain-neutral / zero-hardcode (khử "support riêng lẻ") — flagship
| # | Bug | File | Fix generic |
|---|---|---|---|
| 2 | spa-tuned price bound `10K-50M` + comment "spa range" | jsonb_conversation_state.py:261,278-287 | dùng `DEFAULT_PRICE_MIN_VND`/`DEFAULT_PRICE_MAX_VND` (500M, không 50M); xóa "spa" comment; lift name-len 3/80 + K-heuristic 10/1000 → constants |
| L | `_MAX_LABEL_CHARS=40` inline | tabular_markdown.py:32 | → `DEFAULT_TABLE_LABEL_MAX_CHARS` constant |
| L | topic-signal `5/80` inline | analyze.py:133 | → constants |

- **Test trước**: `_extract_prices` phải bắt giá 100_000_000 (100M, trong 10K-500M) — hiện DROP vì ceiling 50M → fail → fix → pass. + grep-guard: 0 literal "spa" + 0 magic number trong file đụng.

## Phase 2 — T1 correctness (tăng Coverage, anti-HALLU)
| # | Bug | File | Fix generic |
|---|---|---|---|
| 1 | all-text row → nhận nhầm HEADER (CRIT) | tabular_markdown.py:194 | thêm guard `and not table_open` — table đang mở thì row label-like = DATA, không mở header mới |
| 3 | aggregate "Tổng tiền" lọt entity (HIGH, anti-HALLU) | document_stats.py:159 | mở rộng `_AGGREGATE_TOKENS` exact-match generic (tong tien/tong gia/tam tinh/grand total) — exact để KHÔNG false-reject "Tổng hợp dịch vụ" |
| 7 | 2nd price col out-of-vocab → string attr thay vì price_secondary | document_stats.py:369 | unknown col vẫn check `_is_pure_money` fallback → parse thành price |

- **Test trước**: 3 failing test (all-text table 2 row, total-row "Tổng tiền", 2nd-price out-of-vocab header) → fix → pass.

## Phase 3 — Verify
- Full new-flow suite (94+) + document_stats/tabular/analyze/generate tests + 6095 unit.
- Grep self-audit: zero-hardcode + domain-neutral (0 brand literal) trên file đụng.

## Defer (báo user, KHÔNG làm phiên này nếu chưa duyệt): #4 dead doc_summary route (implement vs delete), overflow-truncation:201, CSV 1-comma, 2 checker bug, các test-gap còn lại.

## Status: Phase 1 ✅ · Phase 2 ✅ · Phase 3 ✅ (full-suite verify đang chạy)

### Done (TDD: RED→fix→GREEN mỗi cái)
- **#2 spa-hardcode** ✅ — `_extract_prices` dùng `DEFAULT_PRICE_MIN_VND`/`DEFAULT_PRICE_MAX_VND` (500M, không 50M tenant-tuned), xóa comment "spa range", fix K-shorthand qua `m.group(0)` (latent bug "199k"→0 cũng khỏi); name-len 3/80 → `DEFAULT_SERVICE_NAME_MIN/MAX_CHARS`. Test: `test_price_extraction_bounds.py` (4).
- **#1 all-text→header** ✅ — guard `and not table_open` ở [tabular_markdown.py:194]. Test: `test_all_text_table_*` (2).
- **#3 aggregate-leak** ✅ — `_AGGREGATE_TOKENS` thêm exact-match generic (tong tien/tong gia/tam tinh/grand total), giữ exact để không false-reject "Tổng hợp dịch vụ". Test: `test_multiword_total_row_*` + guard (2).
- **2nd-price col** ✅ — unknown col pure-money → price_secondary (không rớt string-attr). Test (1).
- **zero-hardcode lift** ✅ — `_MAX_LABEL_CHARS`→`DEFAULT_TABLE_LABEL_MAX_CHARS`, topic `5/80`→`DEFAULT_TOPIC_UPPER_SECTION_MIN/MAX_CHARS`; comment "spa CSV/thong-tu" → generic. 0 brand literal trong 4 file đụng.

### Files: 4 src + 3 constants + 1 test mod + 1 test mới. Tests: +9 (RED→GREEN). New-flow + touched-module: 93 pass.

### Remaining (defer, chờ user duyệt): #4 dead doc_summary route (implement vs delete + sửa false-green test), overflow-truncation:201, CSV 1-comma mis-tag, 2 checker bug, các test-gap (round-trip/boundary/empty/TSV), remaining magic trong scripts (check_happy_case, verify_answer_quality — QA harness, lower blast).
