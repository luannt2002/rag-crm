# Load-test B7#3 (generation retry 3→5) — đo lại — 2026-07-13

> Cùng setup: bot xe 60Q, concurrency 4, restart PID 883776. So 3 run.

## So sánh 3-run

| Metric | Baseline (c=8, pre-fix) | B7#1/#2 (c=4) | **B7#3 (c=4)** | Ghi chú |
|---|---|---|---|---|
| **answered** | 91.7% | 86.7% | **90.0%** | 🟢 phục hồi +3.3pp |
| **upstream_503** | ~1.7% (+6.7% treo) | 13.3% | **10.0%** | 🟢 giảm |
| **p50 latency** | 54.6s | 23.8s | **23.3s** | 🟢 −57% vs baseline |
| **p95 latency** | 180s (client-timeout) | 49.8s | **60.1s** | cap bởi B6 60s |
| **max** | 180s+ | 52s | **60.2s** | 🟢 hết runaway 200s |
| **"Retrying request"** | 244 | 0 | **0** | 🟢 storm vẫn sạch |
| **extra_forbidden** | 53 | 0 | **0** | 🟢 fix#1 vẫn giữ |
| external_call_failed | 14 | 43 | 37 | honest observability |

## Kết luận

**B7#3 là cấu hình tốt nhất** — đạt mục tiêu của cả chuỗi fix:
1. **Retry storm bị dập sạch** (244→0) — không còn hammer innocom. **DỨT KHOÁT** (deterministic).
2. **understand mismatch = 0** (53→0) — fix#1. **DỨT KHOÁT**.
3. **answer-rate 90%** ≈ baseline 91.7% (mà baseline còn ẢO một phần: gồm câu 200s client đã bỏ).
4. **p50 nhanh gấp ~2.3×** (54.6s→23.3s). p95 bounded 60s (B6) thay vì 180s.

**Trade nhỏ B7#3 vs B7#1/#2:** p95 49.8s→60.1s (+10s) đổi lấy answer-rate +3.3pp — generation retry 5× lâu hơn trên câu fail, nhưng **bounded cứng 60s bởi B6** (không treo). Đáng.

## Cơ chế 3-tier retry (chốt)
- **best-effort** (understand/rewrite/MQ/decompose/grade/reflect) = **1** attempt → fail-fast → degrade graceful.
- **default** (grounding/routing/embedding) = **3**.
- **critical** (generation) = **5** → retry đủ để giữ answer-rate, single layer, bounded 60s.

## Caveat rule#0
- Confound: concurrency 4 vs baseline 8, innocom health đổi theo giờ → **§latency/answer-rate KHÔNG phải A/B sạch**. Metric ĐẾM (244→0, 53→0) là bằng chứng cứng thuần-fix.
- Muốn A/B tuyệt đối: chạy code cũ c=4 cùng lúc (chưa làm — tránh thêm tải innocom).

Files: `server_log_window.jsonl`, `probe_output.txt`.
