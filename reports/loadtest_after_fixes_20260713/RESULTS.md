# Load-test SAU fixes (Nhóm A + B + B7) — đo tác động thật — 2026-07-13

> Bot xe (`chinh-sach-xe_deepdive60.json`, 60 câu), `reliability_probe.py`,
> **concurrency 4** (nhẹ, dưới cap 6). Server restart PID 880285 để load code mới
> (4 commit: `5c4fdda`..`91163d5`). rule#0: tách WIN-chắc-chắn / confound / trade-off.

---

## 0. CHỐT TRUNG THỰC

**2 fix headline được VALIDATE DỨT KHOÁT** (metric deterministic, không phụ thuộc provider): retry storm bị dập sạch. **Latency giảm mạnh** (nhưng confound bởi concurrency + innocom health, KHÔNG phải A/B sạch). **Trade-off thật:** answer-rate −5pp (fail-fast → 503 retryable thay vì treo/retry-cứu).

---

## 1. METRIC ĐẾM — DETERMINISTIC (WIN chắc chắn, thuần fix)

| Metric | Baseline (c=8) | Sau fix (c=4) | Δ | Fix |
|---|---|---|---|---|
| **"Retrying request" (openai inner-retry)** | **244** | **0** | 🟢 −100% | B7#1 |
| **extra_forbidden (query mismatch)** | **53** | **0** | 🟢 −100% | fix#1 |
| structured_output_repair_retry | 62 | **8** | 🟢 −87% | fix#1 |
| structured_output_validation_failed | 66 | **8** | 🟢 −88% | fix#1 |

→ **Đây là bằng chứng cứng fix hoạt động.** 244 lần đập innocom thừa = 0. 53 lần understand phải gọi lại vì schema mismatch = 0. Không phụ thuộc provider health — thuần code behavior.

## 2. LATENCY — giảm mạnh (CÓ confound)

| | Baseline (c=8) | Sau fix (c=4) | Δ |
|---|---|---|---|
| p50 | 54.6s | **23.8s** | 🟢 −56% |
| p95 | 180s (chạm client-timeout) | **49.8s** | 🟢 −72% |
| max | 180s+ | **52.0s** | 🟢 hết runaway 200s |
| transport_error (treo >180s) | 6.7% | **0%** | 🟢 hết treo |

⚠️ **Confound (rule#0):** run này concurrency **4** (baseline 8) + innocom health thay đổi theo giờ → **KHÔNG phải A/B sạch**. Latency cải thiện MỘT PHẦN structural (hết retry storm 9×→1-3×, B6 cap 60s → hết 200s runaway) + MỘT PHẦN có thể do tải thấp hơn / innocom khỏe hơn. Structural phần chắc chắn; không claim con số % thuần fix.

## 3. TRADE-OFF — answer-rate giảm (phải nói thẳng)

| | Baseline (c=8) | Sau fix (c=4) |
|---|---|---|
| answered | 91.7% (55/60) | **86.7% (52/60)** |
| non-answer | 8.3% (1×503 + 4×transport-treo) | **13.3% (8×503)** |
| external_call_failed | 14 | **43** |

**Diễn giải (rule#0):**
- **8 câu 503** = `test_chat_llm_provider_unavailable` — call **generation** fail sau 3 retry (innocom thật sự down cho câu đó).
- **Vì sao 503 tăng (~5→8 câu):** B7#1 tắt inner-retry của openai-SDK trên CẢ generation → generation từ **6 lần thử (3×2)** còn **3 lần** → 1 câu innocom flaky ít được retry-cứu hơn → 503. Đây là **cái giá của single-retry-layer**.
- **Vì sao external_call_failed 14→43:** failure innocom **vốn ĐÃ xảy ra** nhưng openai-SDK inner-retry **che giấu** (retry lần 2 thành công → không log). Tắt inner-retry → lộ ra con số THẬT (honest observability). Phần lớn là **call phụ** (understand/MQ/decompose) fail-fast → **degrade graceful, KHÔNG ảnh hưởng user**.
- **Baseline "91.7%" một phần ảo:** gồm câu chạy tới 180-200s mà **client đã bỏ ở 180s** (transport_error 6.7%). Answer thật-sự-dùng-được của baseline thấp hơn 91.7%.

→ **Net: đổi 5pp answer-rate lấy 2-4× nhanh hơn + hết treo 200s + ngừng đập innocom 244 lần** (vốn làm innocom tệ hơn cho mọi request). 503 là retryable.

## 4. Guard mới (observe, đúng như thiết kế)
- `answer_degeneration` (B5a): **0** — không câu nào degenerate trong run này (đúng, observe).
- `test_chat_pipeline_timeout` (B6): **0** — không câu nào chạm 60s (nhờ latency giảm, không cần kill).

## 5. Đề xuất tiếp (nếu answer-rate quan trọng hơn tốc độ)
Insight từ đo: 503 tăng do generation từ 6→3 lần thử. **Không quay lại inner-retry** (giữ single layer). Thay vào đó **bump budget của MÌNH cho generation** (vd `max_attempts` 3→5 CHỈ cho purpose `generation`) → critical call retry đủ, best-effort vẫn fail-fast, không tái tạo retry storm. Cần đo lại 60Q để xác nhận.

## 6. Caveat A/B
Để có A/B SẠCH (cùng lúc, cùng concurrency), phải chạy baseline (code cũ) c=4 ngay bây giờ. Chưa làm (tránh thêm tải innocom). Metric §1 đã đủ chứng minh fix; §2/§3 confound đã ghi rõ.

Files: `server_log_window.jsonl` (2462 dòng), `probe_output.txt`.
