# Feature Specification: Deep-Debug luannt-100 — 4 failure clusters

**Feature Branch**: `002-deepdebug-luannt`

**Created**: 2026-07-04 · **Status**: Draft · **Constitution**: v1.0.0 (001 program) — kế thừa
toàn bộ 10 nguyên tắc (EXISTS≠WORKS≠VERIFIED-GOOD, no-guess, statistical, deterministic-gate,
red-test-first, one-change-per-step, platform-neutral, HALLU=0+Coverage, blast-radius, artifacts).

**Input**: bộ 100 câu QA-authored (owner, 2026-07-04) — agent-graded **78/100 đạt**;
22 không-đạt phân cụm 4 class. Evidence: `../001-rag-truth-audit/evidence/luannt100_run.json`
(all-step logs) + `luannt100_verdicts.json` (verdict per câu).

## Baseline SỰ THẬT (đã đo)

| Cluster | Câu dính | Class | Fail-step (từ steps-log) |
|---|---|---|---|
| A — Coreference multi-turn | L-082, L-085, L-087 (+L-089 nhẹ) | 3 lệch + 1 refuse-oan / 10 câu chain | retrieve (query không mang tham chiếu "nó") |
| B — SKU-không-tồn-tại → sản phẩm ảo | L-009, L-032 | 2 sai-bịa | generate (sibling rows serve không kèm signal not-found) |
| C — Câu phức → retrieval lạc | L-005, L-014, L-028, L-072, L-088 | 1 bịa + 2 refuse-oan + 1 thiếu + 1 lệch | retrieve (score_max=0 trên L-088 dù corpus có đáp án) |
| D — Bảo hành: suy luận số sai chiều + nói thêm | L-080, L-060, L-056, L-068, L-090, L-077 | 1 bịa-logic + 1 bịa-scope + 4 chưa-chuẩn | generate + retrieve (thiếu mục Phạm-vi) |

Ngoài scope 002 (đã có đường xử lý riêng): Neoterra class (L-008/L-098) → blocking-mode
owner-gate; 265/70 continuation-merge (001 queue); detector FP phone (001 tune).

## User Stories

### US1 — Coreference chains trả lời đúng tham chiếu (P1)
Khách hỏi tiếp "nó/loại đó/cái này" phải được resolve về entity của turn trước; đo bằng
chain-probe N≥10: 0 lệch-size trên nhóm 6. **Acceptance**: L-082/085/087 fixture chạy lặp
→ size đúng 100%; câu chain mới (size khác) không regress.

### US2 — SKU không tồn tại → nói không có, không dựng hàng ảo (P1)
Hỏi mã/size không có trong corpus → bot trả "chưa tìm thấy quy cách này" (wording từ
sysprompt/oos-template của owner), KHÔNG mượn số row hàng xóm. **Acceptance**: L-009/L-032
fixture + 5 SKU-ảo probe mới → 0 sản-phẩm-ảo, N≥10; câu size CÓ thật không bị refuse-oan
(control giữ 100%).

### US3 — Câu phức (so sánh/thuộc tính/lọc-điều-kiện) không lạc retrieval (P2)
So sánh 2 entity, hỏi thuộc tính (xuất xứ), lọc theo điều kiện (tồn <5) phải lấy đúng nguồn.
**Acceptance**: 5 câu C-cluster + biến thể → chunk đúng vào top (đo chunks-serve chứa
ground-truth), refuse-oan → 0 trên tập này; pinned-60 không regress.

### US4 — Bảo hành: bracket % đọc đúng chiều + không nói thêm ngoài corpus (P3)
**Acceptance**: L-080 fixture (mòn 40% → bồi 60%) đúng N≥10; câu scope (L-060) trả đúng
"PCR/du lịch"; chưa-chuẩn class giảm ≥50% trên nhóm 4 (policy nói-thêm do owner quyết định
mức chặt).

## Functional Requirements

- FR-1: Mỗi cluster fix theo đúng root-cause do debug-workflow xác lập (findings =
  `evidence/debug_findings.json`), KHÔNG fix sai tầng (anti-pattern CLAUDE.md).
- FR-2: Mỗi fix = 1 ladder step riêng: RED test fixture thật → GREEN → re-run probe cụm +
  pinned-60 → delta + rollback rule (kế thừa ladder 001).
- FR-3: Zero per-bot/brand literal; mọi signal schema/shape-keyed; không override answer
  (fix dạng data/signal/route — wording vẫn của owner).
- FR-4: Mỗi fix ship kèm blast-radius statement + grep-gate 0 hit.
- FR-5: Sau khi đủ 4 cluster: re-run **luannt-100 full** (agent-graded, cùng phương pháp) —
  target ≥90/100 đạt, 0 sai-bịa mới, refuse-oan giảm ≥75%.

## Success Criteria

- SC-1: luannt-100 re-run ≥90 đạt; sai-bịa nhóm A/B/C = 0.
- SC-2: chain-probe N≥10 lệch=0 (US1); SKU-ảo=0 (US2).
- SC-3: pinned-60 + GP-100 không regress (chuẩn-rate không giảm).
- SC-4: mọi verdict/delta là agent-graded trên trace thật (owner mandate: "verify bằng
  agent, không tin code-match"); code chỉ thu thập/đếm.
