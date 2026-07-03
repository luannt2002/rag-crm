# Implementation Plan: 002 Deep-Debug luannt — 4 cluster fixes

**Date**: 2026-07-04 · **Spec**: [spec.md](spec.md) · **Findings**: [evidence/debug_findings.json](evidence/debug_findings.json)
(4-agent workflow, 203 tool-calls, 5-step protocol; mọi root-cause = SỰ THẬT có file:line + DB/trace)

## Root-cause map (đã chốt)

| Cluster | IMMUTABLE ROOT | Fix tầng (KHÔNG sysprompt, KHÔNG override answer) |
|---|---|---|
| **A Coreference** | **Policy DRIFT 2 bản cài cùng 1 gate**: fix 2026-05-27 ("first follow-up triggers condense", `<2`) chỉ vào node legacy `condense_question.py:55`; node MERGED `understand.py:170` vẫn `>` strict. Prod chạy merged (`pipeline_merge_condense_router=true`) → fix cũ CHƯA BAO GIỜ hiệu lực → turn-2 mọi hội thoại mất history ở condense. Amplifier: speculative giữ kết quả raw-query (cosine=1.0). Đối chứng: turn-3 (history=4) đúng. | Orchestration: (a) `understand.py:170` `>`→`>=`; (b) DRY pure-helper `has_meaningful_history()` dùng chung 2 node (diệt drift class); (c) giữ speculative (tự discard khi condense đổi query — L-083 chứng minh) |
| **B "SKU ảo"** | **BOT VÔ TỘI** — oan sai do (i) harness capture cắt chunk 500 chars (phản bội chính docstring "grader sees exactly what LLM saw") + (ii) `parse_code_query` keyword cắt tại space → group-dump 11 rows thay vì fold-match. 315/35=2.889.000/158 & 285/45ZR21=3.402.000/0 TỒN TẠI. | Eval-harness: capture cap → constant + `truncated` flag; re-grade audit mọi verdict sai_bia/lech cũ chưa SQL-verify. (retrieval keyword-cụt → gộp vào C) |
| **C Câu phức** | 3 nhánh: (i) **speculative-hit VỨT decompose/MQ** — `retrieve.py:642-660` return TRƯỚC fan-out 1127 → sub-queries sinh ra mà không bao giờ retrieve; (ii) stats route bị DISABLE khi decompose active (`retrieve.py:293-306` — vá triệu chứng cũ); (iii) stats DSL đơn-chiều giá (không có op measure/attribute: "xuất xứ", "tồn<5"). | Orchestration retrieve routing: (1) speculative keep thêm điều kiện `not decompose_active and not mq_variants` (biến có sẵn); (2) thay guard disable-stats bằng LOOP stats per-sub-query; (3) L-088/L-028 → nhánh index/DSL (mid-term) |
| **D Bảo hành** | **mmr_dedup collapse 6→1**: threshold 0.88 calibrate cho embedding CŨ, không recalibrate sau swap ZE zembed-1 (đúng bài học threshold-drift-post-migration đã có trong memory); same-doc distinct-section cosine 0.882–0.979 > 0.88 → mục Phạm-vi bị vứt → LLM đói context → bịa scope/8mm. Config đã từng vá per-intent (0.98 agg) = vá symptom. | Config-only (alembic/admin — CẤM psql): `mmr_similarity_threshold_by_intent.factoid` 0.88→**đo rồi chốt** (replay đo cosine same-doc-distinct vs true-dup) + key mới `mmr_min_keep` floor ≥3 (~10 dòng node). L-080 mòn↔còn: render bracket 2 chiều ở data (không math_lockdown) |

## Score corrections (oan sai đã xác minh)

- v1: 78 → **81/100** (L-009, L-032, +audit đang chạy có thể thêm)
- v2 (luannt100b): 74 đạt strict → **75+** (B-030 oan: raw chunk CÓ 1.872.000 — stats-extraction mất giá, cùng class 265/70; note owner ĐÚNG, FACTS em sai)
- Ghi nhận: grader-agents tự SQL-verify đè FACTS nhiễm (B-003/004/007/009 tự sửa đúng) — bằng chứng phương pháp agent-grade + tool access hoạt động.

## Ladder steps (chờ owner duyệt, 1 fix = 1 step + đo)

1. **A-fix** `understand.py` `>=` + DRY helper — RED: chain fixture thật 3 câu; đo: chain-probe N≥10 lệch=0
2. **C-fix-1** speculative composition-aware — RED: GraphState có sub_queries + speculative → phải fan-out; đo: L-072/L-014/L-005 probe
3. **C-fix-2** stats-per-sub-query loop — đo cùng bộ
4. **D-fix** mmr recalibrate (ĐO trước chốt số) + mmr_min_keep — RED: fixture 8 vector 1280-dim thật; đo: nhóm bảo hành 20 câu
5. **B-fix** harness cap constant + truncated flag + re-grade audit toàn bộ verdict cũ
6. **Ingest price-loss** (265/70 + 235/65R16C — 2 instance cùng class): data-row continuation-merge (task cũ T-013 mở rộng)
7. Re-run **luannt100b + GP-100 + spa100** full — agent-graded, target spec 002: ≥90 đạt

## Constitution check: PASS — mọi fix schema/config-keyed, 0 bot-literal, red-test-first, one-change-per-step, đo trước-sau, blast-radius per step (ghi trong tasks).
