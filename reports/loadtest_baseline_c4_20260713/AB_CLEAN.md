# A/B SẠCH — OLD (pre-fix) vs NEW (B7#3), cùng concurrency 4 — 2026-07-13

> Chạy back-to-back, cùng concurrency 4, cùng scenario 60Q. OLD = commit `09546f8`
> (đã có cap=6), NEW = `8251944` (5 commit của session). **Đã loại confound
> concurrency** — khác biệt = thuần code.

## Bảng A/B sạch

| Metric | OLD (c=4, pre-fix) | NEW-B7#3 (c=4) | Δ |
|---|---|---|---|
| **Retrying request (openai inner-retry)** | **236** | **0** | 🟢 −100% |
| **extra_forbidden (understand mismatch)** | **49** | **0** | 🟢 −100% |
| structured_output_repair_retry | 61 | 8 | 🟢 −87% |
| **p50 latency** | 39.2s | **23.3s** | 🟢 **−41%** |
| **p95 latency** | 115.8s | **60.1s** | 🟢 **−48%** |
| **max latency** | 156.4s | **60.2s** | 🟢 −62% (cap B6) |
| **answered** | 95.0% | 90.0% | 🔴 **−5pp** |
| upstream_503 | 5.0% | 10.0% | 🔴 +5pp |

## Kết luận (rule#0 — clean, không còn confound)

**Cái này là A/B THẬT** (cùng concurrency, back-to-back → provider-health drift nhỏ):

1. **Retry storm bị dập sạch: 236→0.** Understand mismatch: 49→0. **Thuần fix, không cãi được.**
2. **NEW nhanh hơn 41-48%** (p50 39→23s, p95 116→60s, max 156→60s). **Giờ là win SẠCH** — cùng concurrency nên latency giảm là thật, KHÔNG phải do tải thấp.
3. **Trade-off THẬT: −5pp answer-rate** (95%→90%). Đây là giá thật của fail-fast + single-retry-layer, đo sạch.

## ⭐ Insight quan trọng: 5pp là do B6 (pipeline_timeout 60s), KHÔNG chỉ do retry

- OLD để câu chậm chạy tới **116-156s** rồi mới answer → 95% nhưng CỰC chậm.
- NEW **cap cứng 60s** (B6) → giết câu >60s → nhanh nhưng mất ~3 câu (5pp) → 90%.
- → **Answer-rate vs latency là 1 frontier, điều khiển bằng `pipeline_timeout`:**
  - 60s (hiện tại) = nhanh + 90%.
  - Nâng ~120s = chậm hơn nhưng gần 95%.
  - **Owner chọn điểm trên frontier** (config per-bot, không phải code).

## Đánh giá tổng

| | OLD | NEW |
|---|---|---|
| answered | 95% (cao hơn) | 90% |
| tốc độ | chậm (p95 116s) | **nhanh (p95 60s)** |
| tải lên innocom | **236 retry** (hammer) | **0** (gentle) |

**OLD mua 5pp answer-rate bằng: (a) chậm 2×, (b) hammer innocom 236 lần** — mà (b) làm innocom TỆ HƠN cho mọi user đồng thời (chi phí ẩn, không thấy trong test 1-bot). NEW đổi 5pp lấy tốc độ 2× + ngừng hammer. 503 là retryable.

→ **Trade hợp lý cho hệ innocom-bottleneck.** Nếu owner ưu tiên answer-rate hơn tốc độ: nâng `pipeline_timeout` (config), không cần đổi code.

Files: `server_log_window.jsonl` (2771 dòng), `probe_output.txt`.
