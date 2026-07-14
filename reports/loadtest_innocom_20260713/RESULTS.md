# Load-test innocom "version mới" — kết quả + thông số đầy đủ — 2026-07-13

> Test bot xe (`chinh-sach-xe_deepdive60.json`, 60 câu) qua `reliability_probe.py`,
> concurrency 8. Window UTC **08:52:20 → 09:02:19 (599s)**. Server: `ragbot-py.service`
> (uvicorn 1 worker, port 3004). Provider innocom (`openai/claude` gateway), cap=6.
> Mọi số từ log thật + probe output (rule#0). File kèm để phân tích: xem §7.

---

## 1. Kết quả probe (số cứng)

```
RELIABILITY PROBE — concurrency=8 n=60
  answered             55  (91.7%)
  transport_error       4  ( 6.7%)   ← câu treo >180s (client timeout)
  upstream_503          1  ( 1.7%)
  error_rate           8.3%
  latency ms  p50=54574  p95=180009  max=180023
```

## 2. So baseline 2026-07-10 (cap6)

| Chỉ số | Baseline 10/07 (n16) | Nay 13/07 (n60) | Verdict |
|---|---|---|---|
| answered | 93.8% | 91.7% | ~ngang |
| **p50 latency** | 32.8s | **54.6s** | 🔴 chậm hơn +66% |
| **p95 latency** | 93.4s | **180s** (chạm timeout) | 🔴 chậm hơn nhiều |
| user 503 | 6.2% | 1.7% | 🟢 tốt hơn |
| transport timeout | — | 6.7% | 🔴 mới |
| **Truncation (cụt)** | ~7% | **0/57** | 🟢 hết cụt |

## 3. Truncation (đo bằng log `llm_generation_finish`)
- Tổng generation call log: **57** · finish_reason: **{stop: 57}** (100% stop, 0 length)
- completion_tokens: min=4, max=248, avg=90 (cap=450 → **không câu nào tới gần cap**)
- **Truncation-suspect: 0/57** → **version mới đã hết cụt** (điểm cải thiện DUY NHẤT)

## 4. Error breakdown nội bộ (đếm event thật trong window)

| Event | Số | Nghĩa |
|---|---|---|
| `structured_output_repair_retry` | **62** | innocom trả JSON hỏng → sửa+gọi lại |
| `grounding_check_degraded` | 51 | grounding lỗi → fail-open (bỏ qua) |
| `grounding_async_pass` | 34 | grounding chạy nền pass |
| `grounding_check_timeout` | 28 | grounding call timeout |
| `multi_query_expand_timeout` | 27 | MQ call timeout (5s) |
| `structured_output_provider_call_failed` | 15 | 500 trên structured call |
| `external_call_failed` (llm) | **14** | **wire-call fail thật** (500/timeout) |
| `rewrite_in_parallel_failed` | 6 | rewrite call fail |
| `decomposer_llm_call_failed` | 5 | decompose 500 |

⚠️ Lưu ý: "151 InternalServerError" khi grep string là **lạm phát** (1 lỗi echo ở nhiều dòng). Fail wire thật = **14** (`external_call_failed`).

## 5. TỔNG số call qua innocom + throughput

**➡️ TỔNG CALL QUA INNOCOM cho 60 câu hỏi (window 599s):**
- **Đếm được từ log (floor):** **299 call-attempt** (generation 57 + fail/timeout/retry 242).
- **Ước lượng thật:** **~480–660 call** (pipeline ~8–11 call/câu × 60 câu; floor 299 là undercount vì understand/grade/rewrite thành công KHÔNG log per-call).
- **Trung bình mỗi câu:** **~8–11 call** qua innocom (trace thật §6 = 11 call).

| Cách đo | Kết quả |
|---|---|
| Floor (event đếm được) | **299 call-attempt / 599s = 0.50 call/s** |
| Ước lượng thật (pipeline ~8-11 call/câu × 60) | **~480-660 call → ~0.8-1.1 call/s** |
| Đồng thời tối đa (cap) | **≤ 6 call in-flight** |
| User-facing | 60 câu / 599s = **0.10 câu/s** (~1 câu mỗi 10s) |

**Con số thực tế: ~0.5-1.1 call/giây** qua innocom (floor 0.50 là undercount vì understand/grade/rewrite thành công không log per-call; ước lượng 0.8-1.1 sát hơn).

## 6. ⭐ VÌ SAO 1 CÂU HỎI = ~10 CALL innocom?

**KHÔNG phải bug — đây là kiến trúc RAG đa tầng (SOTA), MỖI tầng 1 LLM call**, cộng retry khi innocom lỗi.

**Trace 1 câu thật (`170b2c3f…`) = 11 sự kiện LLM, kéo dài 170 giây:**
```
08:54:58  multi_query_expand_timeout          ← call 1: mở rộng query (timeout)
08:54:58  structured_output_validation_failed ← call 2: JSON hỏng
08:54:58  structured_output_repair_retry      ← call 3: gọi lại sửa JSON
08:55:14  multi_query_expand_timeout          ← call 4: MQ lại (timeout)
08:56:34  external_call_failed                ← call 5: 500
08:56:34  rewrite_in_parallel_failed          ← call 6: rewrite fail
08:56:55  structured_output_provider_call_failed ← call 7: 500
08:57:18  llm_generation_finish               ← call 8: SINH đáp án (OK)
08:57:48  grounding_check_timeout             ← call 9: kiểm chứng (timeout)
08:57:48  grounding_check_degraded            ← fail-open
08:57:48  grounding_async_pass                ← call 10: grounding nền
```

**Pipeline chuẩn cho 1 câu (query_graph):**
| Tầng | LLM call | Ghi chú |
|---|---|---|
| understand | 1 | hiểu ý + condense |
| decompose | 0-1 | nếu comparison/multi_hop |
| rewrite | 0-1 | viết lại query |
| multi_query | 0-1 | mở rộng N biến thể |
| grade (CRAG) | 0-1 | chấm chunk (skip nếu score≥0.7) |
| **generate** | **1** | sinh đáp án (bắt buộc) |
| grounding judge | 1 | kiểm chứng chống bịa |
| reflect | 0-1 | self-RAG (mặc định off) |
| **+ RETRY** | **+N** | mỗi 500/timeout → gọi lại (structured repair 62 lần!) |

→ **factoid đơn giản: ~1-3 call · comparison/listing: ~5-8 call · + retry khi innocom lỗi = ~10-11 call.**

**Điểm mấu chốt:** ~10 call/câu là BY DESIGN (đa tầng để chống bịa + tăng recall). Khi innocom ổn → nhanh. Khi innocom 500/timeout → **mỗi call chờ đến 72s rồi retry → 1 câu mất 170s** như trace trên. Đây là lý do latency tệ: KHÔNG phải nhiều câu, mà mỗi câu nhiều call × innocom chậm.

## 7. Kết luận

- innocom "version mới": ✅ **hết truncation (0/57)** nhưng 🔴 **500/timeout còn nặng + latency TỆ HƠN** (p50 +66%, p95 chạm 180s).
- 91.7% answered là nhờ **graceful degradation của app** (decompose/MQ/grounding fail → fallback), KHÔNG phải innocom ổn.
- **Chưa production-ready về provider.** Đòn bẩy: (a) giảm số call/câu (bỏ reflect thừa, gate MQ), (b) seed fallback provider, (c) báo innocom fix 500 + latency (đã hết cụt).

## 8. File kèm (đem đi phân tích)

| File | Nội dung |
|---|---|
| `server_log_window.jsonl` | **1727 dòng, 744K** — toàn bộ log server trong window (JSON/dòng) |
| `probe_raw_output.txt` | Output gốc reliability_probe |
| `RESULTS.md` | File này |

**Cách lọc log (jq/grep):**
```bash
# mọi generation call + finish_reason
grep llm_generation_finish server_log_window.jsonl | jq '{tok:.completion_tokens, fr:.finish_reason}'
# mọi lỗi innocom
grep external_call_failed server_log_window.jsonl | jq '{err:.error_type, ms:.duration_ms}'
# trace 1 câu cụ thể
grep '"trace_id": "170b2c3f' server_log_window.jsonl
```
