# Decision Record — Shell entities (69 price-less) serving policy

**Date**: 2026-07-03 · **Owner**: LuanNT (project owner) · **Status**: DECIDED

## Decision

**Option (b) — filter shell entities khỏi customer-facing stats retrieval.**
Owner sign-off: tin nhắn "b" (2026-07-03, sau khi đọc baseline_report.md + phân tích 3 option).

## Options considered

| Option | Cost | Diệt được | Rejected vì |
|---|---|---|---|
| **(b) serve filter** ✅ CHỌN | S — WHERE clause value-bearing trong customer stats queries (`stats_index_repository.py`), constant + per-bot opt-out `plan_limits` | P-02/03/04 mechanism (45/45 conflation baseline); shell/garbage của MỌI bot (111/123 = 100% shell) tự vô hiệu; list_all_entities fallback hết trả rác | — |
| (a) marker "chưa có giá" | S | P-02..04 một phần (vẫn là LLM-obedience, P-IV không đủ) | Xác suất, không cấu trúc; mất ít existence hơn (b) nhưng đổi lấy rủi ro lệch còn nguyên |
| (c) pending_price lifecycle | L — alembic + ingest + filter + admin | như (b) + lifecycle | Effort L; làm SAU khi (b) ổn (không loại trừ — nâng cấp tương lai) |

## Trade-off chấp nhận + rollback

- Câu existence ("có bán Rovelo X không?") có thể chuyển từ khẳng-định-có sang "em kiểm tra lại" — đo bằng pinned-60 (xe-exist-*). **Rollback rule (pre-declared)**: existence-question chuẩn-rate rớt >2 câu so baseline → revert flag, escalate lên hybrid (b)+(a).
- (b) KHÔNG diệt P-07 misattribution (2 row đều có giá) và KHÔNG diệt đường raw-document-chunk (P-01) — 2 cái đó thuộc Phase 4 gate + brand-priority lookup, đúng thiết kế ladder.

## Constraint compliance

Schema-keyed (`price_primary IS NULL AND price_secondary IS NULL` + no value attrs), zero bot/brand literal, per-bot opt-out qua plan_limits resolve chain, tenant-scoped sẵn (mọi query đã scope record_bot_id). P-V/P-VII/FR-006 thỏa.
