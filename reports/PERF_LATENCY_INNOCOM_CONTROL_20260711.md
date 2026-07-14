# Performance code + Độ trễ LLM innocom + Cách app xử lý khi innocom lỗi — 2026-07-11

> Báo cáo gộp 3 trục theo yêu cầu. Mọi con số ĐO được ghi rõ nguồn; mọi con số
> chưa đo gắn nhãn **GIẢ THUYẾT** (rule#0 — không đoán). "innocom" = cổng LLM
> external đang deploy (nhà cung cấp AI). Trong code tracked, tên này đã được
> genericize thành `<llm-provider>` / "upstream gateway".

---

## TRỤC 1 — Phân tích + tối ưu PERFORMANCE code

### 1.1 Sự thật nền (quan trọng nhất)
Mỗi câu hỏi chạy qua **~6–10 lần gọi LLM** (understand → rewrite → multi-query →
grade → generate → grounding-judge → reflect…). Mỗi call innocom ~1–30s. **Ngân
sách LLM lấn át mọi chi phí khác 1–2 bậc.** → Chỉ **giảm SỐ lần gọi LLM** mới
kéo được latency/câu; tối ưu Redis/CPU/DB chỉ giúp **throughput dưới tải**, không
giúp latency 1 câu.

### 1.2 Đã tối ưu (shipped, verified an toàn tuyệt đối)
| Việc | file:line | Kiểm chứng |
|---|---|---|
| Gộp N lệnh đọc cache embedding song song (thay vì tuần tự) | `query_graph.py:1500` | 107 test pass |
| Xoá lệnh ghi Redis L2 **dead** (ghi rồi không ai đọc) trong `resolve_runtime` | `service.py:541` | 27 test pass, verified 4 bước không reader |

*Cả 2 là behavior-preserving — không đổi kết quả, chỉ bớt việc thừa. CHƯA đo
speedup → không tuyên bố nhanh hơn bao nhiêu.*

### 1.3 Backlog tối ưu (chưa làm, xếp theo giá trị)
**Tier A — giảm LLM call (đòn bẩy latency THẬT, đụng answer-path → cần test 60Q):**
- A1: node `reflect` gọi LLM **vô điều kiện**, cổng bỏ-qua lại nằm SAU nó → chuyển
  cổng lên trước → **bỏ ~1 LLM call/câu (~1–2s)** trên câu factoid. (`reflect.py:90` vs `:148`)
- A2: `grade` batch chỉ bật khi có cấu hình → bot thiếu cờ trả **N call thay vì 1**. (`grade.py:186` vs `:318`)

**Tier B — an toàn, chỉ giúp throughput:** cache shingle sysprompt, cache
`inspect.signature`, bỏ over-fetch vector 1280-dim (`pgvector_store.py:530`)…

**Tier C — dưới tải:** doc-shingle sha256 block event loop (`guard_output.py:570`),
chunking/parse chưa `to_thread` khi nạp tài liệu, `pool_pre_ping` SELECT 1 mỗi checkout.

Chi tiết đầy đủ: `reports/PERF_AUDIT_20260711.md`.

### 1.4 Xác nhận code TỐT (không cần đụng)
0 lỗi gather-trên-cùng-session · HNSW index có sẵn + ef_search tuned · bulk INSERT
không N+1 · engine build 1 lần · fg/bg LLM lane tách riêng.

---

## TRỤC 2 — ĐỘ TRỄ của LLM innocom (đo thật)

### 2.1 Số đo (từ `reports/RELIABILITY_FIX_20260710.md`, tool `reliability_probe.py`)
| Chỉ số | Trước (cap=16) | Sau (cap=6) | Δ |
|---|---|---|---|
| Latency p50 | 50.5s | **32.8s** | **−35%** |
| Latency p95 | 159.8s | **93.4s** | **−42%** |
| Latency max | 159.8s | 93.4s | −42% |

### 2.2 Vì sao trễ (root cause, có trace)
- Timeout mỗi call: **mặc định 30s** (`DEFAULT_LLM_TIMEOUT_S`), **innocom override 90s** (DB, mirror `raise_innocom_timeout_90s`).
- Đuôi 160s (cap 16) = **1 call chạm timeout 90s → RỒI retry** → tổng ~160s. Tức
  cap cao (16) làm innocom quá tải → call treo quá 90s → retry → chậm.
- Hạ cap xuống 6 → xếp hàng call, mỗi call xong trước timeout → đuôi 160s biến mất.

### 2.3 Kết luận trục 2
**Trễ là do innocom quá tải khi bắn nhiều call đồng thời, KHÔNG phải do bot/RAG.**
Đòn bẩy app kiểm soát = **giảm concurrency (đã làm 16→6)**. Muốn giảm thêm =
giảm SỐ call/câu (Tier A) hoặc thêm nhà cung cấp dự phòng (trục 3).

---

## TRỤC 3 — App CONTROL thế nào khi innocom LỖI / RỖNG / 503

### 3.1 Bảng xử lý theo từng loại lỗi (verified code)
| Loại lỗi innocom | App phát hiện được? | App làm gì (hiện tại) | Kết quả tới consumer | Hở? |
|---|---|---|---|---|
| **503 ServiceUnavailable** | ✅ CÓ (exception) | retry 3 lần (backoff ~0.25–2.25s) → nếu vẫn lỗi + là `_FAILOVER_TRIGGERS` → **failover 1-hop** sang binding dự phòng → hết cách: raise `LLMError` | **HTTP 503 `ok:false`** (thất bại trung thực) | Bot **không cấu hình binding dự phòng** → 0 failover |
| **500 Internal** | ✅ CÓ (exception) | như 503 | 503 `ok:false` | như trên |
| **Timeout (30s/90s)** | ✅ CÓ (exception) | retry → failover | 503 `ok:false` hoặc câu trả lời từ provider dự phòng | đuôi latency cao |
| **Circuit breaker mở** (>5 fail liên tiếp) | ✅ CÓ | fast-fail ngay → failover (không chờ retry) | nhanh, không treo | — |
| **Trả RỖNG** (HTTP 200, body rỗng) | ❌ KHÔNG (không phải exception) | non-stream **chấp nhận im lặng, chỉ log** `llm_generation_finish` | giao **`ok:true status:"success" answer:""`** | 🔴 **HỞ (T2-4)**: consumer tưởng thành công |
| **Trả CỤT** (HTTP 200, `finish_reason="stop"` nhưng cắt giữa chừng) | ❌ KHÔNG (innocom mask, luôn báo "stop") | chấp nhận im lặng, chỉ log | giao **`ok:true`** câu cụt | 🔴 **HỞ**: chỉ prevention (cap) giảm được, không diệt |

### 3.2 Các "cần gạt" app ĐANG CÓ (verified)
1. **Concurrency cap** (`ai_providers.max_concurrent`, đã hạ 16→6) — phòng ngừa.
2. **Retry + backoff** (`retry_policy.py`, 3 lần) — chỉ với exception.
3. **Failover 1-hop** (`dynamic_litellm_router.py:605`, triggers: CircuitBreakerOpen,
   LLMError, ServiceUnavailable-503, APIConnection).
4. **Circuit breaker** per-provider (mở sau 5 fail).
5. **Timeout** (30s / innocom 90s).
6. **Observe log** `llm_generation_finish` (`:804`) — thấy cụt/rỗng nhưng CHƯA chặn.

### 3.3 Các "cần gạt" app CÒN THIẾU (đề xuất, xếp theo giá trị)
1. **⭐ Cấu hình binding provider dự phòng** — đòn bẩy #1. 503/500 là lỗi **bắt được
   qua exception** → chuỗi failover có sẵn (`:605`) sẽ tự cứu ngay, **chỉ thiếu 1
   binding thứ 2** cho bot. Đây là việc cao giá trị nhất.
2. **Rỗng → `status="empty"` + `ok:false`** (sửa T2-4 tại `callbacks.py:231,303`) —
   để consumer phân biệt "rỗng do lỗi" vs "thành công". An toàn (chỉ đổi nhãn khi
   answer thật rỗng, không đụng câu refuse hợp lệ có text).
3. **Cụt (truncation)**: KHÔNG detect được (innocom nói dối `finish_reason`) → chỉ
   **prevention**: cap thấp + failover. Không thể retry/chặn theo metadata.
4. **Dead-letter khi giao callback thất bại** (T2-2, `callback_delivery.py:150`) —
   hiện giao thất bại thì **âm thầm bỏ**, chưa phát `chat.delivery_failed.v1`,
   chưa re-queue → consumer mất câu trả lời.

### 3.4 Kết luận trục 3
- **503/500/timeout: app xử lý ĐÚNG** (retry → failover → 503 ok:false trung thực),
  **NHƯNG failover chỉ chạy nếu bot có binding dự phòng** → hiện phần lớn = raise 503.
- **Rỗng/cụt: app CHƯA xử lý đúng** — giao `ok:true` như thành công. Đây là 2 lỗ
  cần bịt (rỗng: đổi status; cụt: chỉ phòng ngừa được).
- **Anti-HALLU vẫn nguyên**: cổng số-học (numeric-fidelity) độc lập với innocom →
  dù innocom trả bậy, số bịa vẫn bị chặn (đã chứng minh case "0909").

---

## Việc nên làm tiếp (1 câu)
Thứ tự giá trị: **(1) thêm binding provider dự phòng** (biến 503/500 thành tự-cứu)
→ **(2) rỗng→status** (bịt lỗ giao-rỗng-báo-thành-công) → **(3) A1 reflect-skip**
(giảm 1 LLM call/câu, có test 60Q) → **(4) dead-letter callback**. Mỗi bước đo
trước/sau, không gộp.
