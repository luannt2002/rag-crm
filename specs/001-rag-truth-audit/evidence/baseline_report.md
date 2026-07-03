# Phase-B Statistical Baseline — chinh-sach-xe, N=15/probe (2026-07-03)

**Run**: `scripts/rag_trace_capture.py --scenario tests/scenarios/chinh-sach-xe_probe9.json
--repeat 15 --concurrency 6` → `evidence/baseline_runs.json` (135 runs, exit 0,
cache 100% bypassed, corpus stable: 401 chunks / md5 stamped start=end).

## Kết quả per-probe (SỰ THẬT — mọi số đối chiếu stats DB)

| Probe | Câu | n hợp lệ | Đúng | Sai | Cơ chế sai (verbatim + DB) |
|---|---|---|---|---|---|
| P-01 | Neoterra 195/65R16 giá? (shell + stray date1=26) | 15 | 0 | **15** | **8× bịa "26.000.000đ + còn 26 lốp"** (26 = ngày về; giá KHÔNG tồn tại trong DB) + **7× lệch "1.350.000"** (= giá thật của `2-R16 195/75 RVL` — KHÁC size, KHÁC brand) |
| P-02 | Rovelo 185/55R15 giá? (shell, sibling Landspider có giá) | 15 | 0 | **15** | 15× lệch: 810.000đ = giá `2-R15 185/55 LPD` gán cho Rovelo |
| P-03 | Rovelo 195/55R16 giá? | 15 | 0 | **15** | 15× lệch: 1.044.000đ + "còn 222 lốp" — CẢ giá lẫn tồn copy từ Landspider |
| P-04 | Rovelo 205/65R15 giá? | 15 | 0 | **15** | 15× lệch: 999.000đ/303 lốp từ Landspider |
| P-05 | LT235/75R15 WILDTRAXX giá? | 15 | 15 | 0 | RECLASSIFIED: probe label "pure-gap" SAI — entity `2-R15 LT235/75 LPD` CÓ giá 1.953.000; bot trả đúng 15/15 → control |
| P-06 | Landspider 245/75R16 giá? (pure-gap THẬT) | 15 | 15 (refuse đúng) | 0 | Duy nhất hành xử chuẩn: "chưa tìm thấy" 15/15 |
| P-07 | Landspider 225/45R17 giá? (CONTROL — cả 2 record đầy đủ) | 15 | 6 | **9** | 🔴 MỚI: chunk served có ĐỦ 2 dòng (Rovelo 1.170.000/4 đứng TRƯỚC, Landspider 1.242.000/507 sau). 9/15 run lấy giá+tồn dòng Rovelo gán cho "Landspider" — **misattribution với data hoàn hảo, nghi primacy-bias** |
| P-08 | DX640 215/60R17 giá? (control) | 15 | 15 | 0 | 1.485.000 đúng 15/15 |
| P-09 | Davanti 275/40ZR21 giá? (control) | 6 | 6 | 0 | 9 run RATE_LIMITED (harness thiếu retry-429 — lỗi đo, không phải bot; loại khỏi mẫu) |

## Verdicts (theo ngưỡng data-model.md)

1. **Stray-number hypothesis: CONFIRMED (SỰ THẬT).** 8/8 giá trị bịa-thật-sự (không tồn tại
   trong DB) đều = "26.000.000" — 100% chứa stray `date1:"26"` (ngưỡng confirm ≥70%).
   Fabrication rate P-01 = 53% (8/15); giá trị bịa ổn định bimodal, không random.
2. **Cơ chế sai CHỦ ĐẠO là wrong-entity attribution (lệch), không phải bịa thuần**:
   61/69 câu sai của shell-probes + control = số THẬT của entity KHÁC (brand khác hoặc
   size khác). → numeric-fidelity gate (so số với DB) bắt được P-01-fabricate nhưng
   **KHÔNG bắt được 61 case lệch** — cần thêm **entity-attribution check** (số quote phải
   thuộc đúng row có brand/size khớp câu trả lời).
3. **Bot KHÔNG BAO GIỜ tự refuse khi có bất kỳ record nào được serve**: 0/60 refusal trên
   4 shell-probes; refuse chỉ xảy ra khi retrieval trả rỗng hoàn toàn (P-06). Luật
   sysprompt "không kèm số → không bịa" không kích khi có số HÀNG XÓM trong context.
4. **P-07 misattribution 60% trên control hoàn hảo** — nghiêm trọng nhất về diện rộng:
   không cần shell, chỉ cần ≥2 record cùng size khác brand trong 1 chunk là có xác suất
   gán nhầm. Khớp với QA 10/40 sai. GIẢ THUYẾT cơ chế: primacy (chọn dòng đầu) — cần
   probe đảo thứ tự dòng để chốt (thêm vào Phase 5).
5. **Harness fix cần**: retry-on-RATE_LIMITED (backoff theo `retry_after_s`) trước
   GP-100 (300 requests sẽ dính RL nặng hơn).

## Hệ quả cho remediation ladder (cập nhật tasks.md)

- Phase 3 (serve filter option b) diệt P-02/03/04 mechanism (row shell không tới LLM nữa)
  nhưng **không** diệt P-07 (2 row đều priced — filter không lọc).
- Phase 4 numeric-fidelity: bắt P-01-fabricate; **mở rộng bắt buộc**: attribution check
  (brand/size token của câu hỏi × row sở hữu con số) — deterministic, schema-free.
- Ứng viên fix P-07 đúng tầng: stats-lookup lọc theo brand token trong câu hỏi (đã có
  alias match — cần ưu tiên exact-brand row khi câu hỏi nêu brand) + prompt-build đánh
  nhãn row rõ ràng hơn. Đo lại bằng chính P-07 probe sau mỗi step.
