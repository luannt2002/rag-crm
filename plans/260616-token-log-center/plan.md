# Plan — Token Log Center (cost/token tracking toàn hệ thống)

**Tier:** [T2-CostPerf] · **Status:** Phase 1 partial shipped (token_ledger raw) · **Created:** 2026-06-16

> Mục tiêu: tracking MỌI action tiêu token/tiền (LLM ChatGPT, embedding/rerank ZeroEntropy,
> provider tương lai) cho CẢ luồng upload (ingest) lẫn query, durable, decoupled, scale tốt.

---

## 1. Yêu cầu (owner)

1. **1 bảng dùng chung** cho upload + query, có `mode`='ingest'|'query' để verify từng action thuộc luồng nào.
2. **Provider-agnostic**: ChatGPT / ZeroEntropy / provider sau này đều log được (`provider` + `model`).
3. Mỗi record: **input_tokens, output_tokens, total_tokens** (3 số) + **started_at, finished_at** (2 mốc) + provider + model + mode.
4. **Đơn giá snapshot**: input/output có đơn giá riêng (mini ≠ nano ≠ ZE), snapshot tại log-time để cost lịch sử bất biến khi giá đổi. Tiền ($) chưa gấp nhưng cột sẵn (`cost_usd` nullable).
5. **Decoupled — survives delete**: xóa bot HOẶC xóa document → data token VẪN CÒN (report + verify). KHÔNG FK CASCADE.
6. **Identity 4-key + xóa bot**: snapshot 4-key; `record_bot_id` (UUID bất biến) làm khóa report → chống nhập nhằng khi slug `(bot_id, channel_type)` đổi workspace. Xóa bot → tag `status='deleted'`, KHÔNG xóa row.
7. **Mindset scaling + tách luồng**: write decoupled non-blocking; report kỳ tháng/tuần/năm không scan bảng raw.

## 2. Expert solution — kiến trúc 2 tầng

### Tầng 1 — `token_ledger` (raw, per-call) — ĐÃ SHIP (alembic 0226)
- 1 row / 1 call (LLM/embed/rerank). Cột: mode, action, purpose, provider, model, 4-key snapshot (record_tenant_id, record_bot_id, bot_id, workspace_id, channel_type), request_id, document_id, trace_id, input/output/total/cached_tokens, started_at/finished_at/duration_ms, input/output/cached_unit_price, cost_usd, status, finish_reason.
- **NO FK** (verified 0 FK) → survives bot/document delete. `id BIGSERIAL` = watermark monotonic.
- Index: (record_bot_id, started_at), (started_at), (mode, started_at), (provider, started_at), (record_tenant_id, started_at).

### Tầng 2 — `token_rollup_daily` (aggregated, grain NGÀY) — PHASE 2 (chưa build)
- **KHÔNG fix cứng tháng.** Grain mịn nhất = **NGÀY**, roll-up lên tháng/tuần/quý/năm khi ĐỌC (SUM range ngày — rẻ, không scan raw).
```sql
token_rollup_daily (
    record_bot_id UUID, day DATE,
    bot_id, workspace_id, record_tenant_id,   -- snapshot để filter/display không JOIN
    sum_input, sum_output, sum_total, sum_cached BIGINT,
    sum_cost NUMERIC, n_calls INT,
    status VARCHAR DEFAULT 'active',           -- 'deleted' khi bot xóa
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (record_bot_id, day)
)
-- INDEX (day), (workspace_id, day), (record_tenant_id, day)
```
- Số row = bot × ngày (1000 bot × 365 = 365k/năm → nhỏ). Report tháng 1 bot = đọc ~30 dòng.

### Watermark job (incremental, idempotent)
- 1 watermark global: `last_processed_ledger_id` (1 row trong 1 bảng meta hoặc system_config).
- Job: `SELECT ... FROM token_ledger WHERE id > :watermark` → `GROUP BY record_bot_id, date(started_at)` → **UPSERT cộng dồn** vào rollup_daily → set watermark = max(id).
- Idempotent: chạy lại không sai (watermark đảm bảo không double-count). Ngày quá khứ tự immutable (không có row mới) → khỏi cờ-per-tháng.
- **Cron 0h hằng ngày/giờ** chạy job. (Không cần "chốt tháng" — chỉ chạy incremental định kỳ.)

### Roll-up lên workspace/tenant (on-read, KHÔNG pre-aggregate)
- Workspace tháng = `SUM rollup_daily WHERE workspace_id=X AND day BETWEEN ... GROUP BY workspace_id`.
- Tenant = SUM thêm 1 tầng (lazy, owner OK đợi vì cần join nhiều).
- Gồm **cả bot đã xóa** (record_bot_id snapshot còn) — `WHERE status='active'` nếu chỉ muốn bot đang dùng.

### Xóa bot
- Bulk `UPDATE token_ledger SET status='deleted' WHERE record_bot_id=:uuid` + tương tự rollup. **KHÔNG xóa row.** Hook vào bot-delete service (KHÔNG CASCADE). Best-effort (fail → row vẫn 'active', data còn).
- Bot tạo lại (đổi workspace) → record_bot_id MỚI → rows mới, không lẫn rows cũ.

### FE contract
- FE truyền `mm/yyyy` → backend dịch range `[ngày đầu, ngày cuối]` → SUM rollup_daily. Đổi kỳ (tuần/quý) = đổi range, KHÔNG sửa schema/job.

## 3. Scaling (DB nhiều data)
- **PARTITION `token_ledger` theo tháng** (declarative partitioning trên `started_at`) → tháng cũ detach/archive; query chạm 1 partition.
- Rollup nhỏ → report đọc rollup, không scan raw.
- Job bounded (`id > watermark`) → khối lượng cố định.
- (Tùy chọn xa) TimescaleDB continuous aggregates = native version của rollup-incremental này.

## 4. Write path — decoupled (Port + DI) — ĐÃ SHIP một phần
- `TokenLedgerPort.emit(entry)` fire-and-forget (`application/ports/token_ledger_port.py`).
- `NullTokenLedger` (off, no-op) + `AsyncDBTokenLedger` (bounded asyncio.Queue + background drainer batch-INSERT, own session factory, drop+warn khi full) + `registry.build_token_ledger` (`infrastructure/token_ledger/`).
- `mode_ctx` contextvar (`config/logging.py`) — worker/route set 'ingest'/'query', router đọc (KHÔNG đoán theo model name).
- DI: `bootstrap.py` Singleton `token_ledger` inject vào router.

## 5. Trạng thái / việc còn lại
| Hạng mục | Status |
|---|---|
| alembic 0226 token_ledger (raw, NO FK, index) | ✅ shipped |
| Port + Null + AsyncDB + registry + DI | ✅ shipped |
| mode_ctx + document_worker (mode='ingest') | ✅ shipped |
| Router emit ở `_complete_via_llmport` (ingest CR/narrate/grade) | ✅ shipped |
| **Router emit ở `complete_runtime`/streaming (QUERY generate)** | ❌ TODO — query chưa bắt |
| Snapshot đơn giá từ `cfg.pricing` | ❌ TODO (unit_price hiện NULL) |
| **ZE embedder/reranker emit** (action='embedding'/'rerank') | ❌ TODO |
| **Bypass paths** (`ingest_stages_enrich.py:459`, `ingest_core.py:741`, `document_service/__init__.py:794`, `query_intent_extractor.py:86` gọi litellm trực tiếp — MISS ledger) | ❌ TODO route qua router hoặc emit thủ công |
| **token_rollup_daily + watermark job + cron** | ❌ TODO Phase 2 |
| Tag-on-delete (bot delete → status='deleted') | ❌ TODO |
| Tests (schema no-FK, emit non-block, survives-delete, ws-change, cost-snapshot, modes) | ❌ TODO |

## 6. Compliance CLAUDE.md
✅ no-CASCADE/decoupled · ✅ decoupled-non-block (queue + own session, Async Rule 7) · ✅ zero-hardcode (đơn giá snapshot từ DB, tuning là pure-technical) · ✅ 4-key snapshot · ✅ Strategy+DI (Port+Registry+Null) · ✅ rule#0 (mode từ contextvar, no guess) · ✅ no-psql (config seed qua alembic).


and veridy lại plan 



Bản thiết kế này phản ánh một mindset engineering cực kỳ sắc bén, mang dáng dấp của kiến trúc hệ thống Enterprise/Fintech. Bạn đang không coi tracking token là một tính năng "CRUD" thông thường, mà đang tiếp cận nó dưới góc độ **Event Sourcing** và **Financial Ledger** (sổ cái tài chính).

Dưới đây là phân tích chi tiết về mindset đằng sau kiến trúc này, những điểm sáng, và một vài rủi ro tiềm ẩn bạn cần lưu ý trước khi đánh resource vào các Phase tiếp theo.

## 1. Điểm sáng trong Mindset Kiến trúc

### A. Mindset "Sổ cái bất biến" (Immutable Financial Ledger)

Quyết định **không dùng Foreign Key (NO FK)** và **snapshot đơn giá** là hai quyết định đắt giá nhất trong bản thiết kế này.

* **Trị dứt điểm "Cascade Delete":** Trong các hệ thống non-kém, khi user xóa bot hoặc xóa document, data token cũng "bốc hơi" theo vì FK cascade, dẫn đến hụt cost cuối tháng. Cách bạn dùng `id BIGSERIAL` làm watermark và snapshot `record_bot_id` đảm bảo dữ liệu chi phí tồn tại độc lập với vòng đời của Entity.
* **Auditability (Tính kiểm toán):** Giá của OpenAI/ZeroEntropy sẽ thay đổi. Việc snapshot `unit_price` ngay tại thời điểm log giúp cost lịch sử không bị "nhảy" khi bạn cập nhật bảng giá trong tương lai. Đây là chuẩn mực của hệ thống billing.

### B. Mindset "Phân tách Read/Write" (CQRS lighweight)

Cách bạn thiết kế kiến trúc 2 tầng (Raw per-call $\rightarrow$ Daily Rollup) giải quyết triệt để bài toán Scale:

* **Mịn vừa đủ (Goldilocks grain):** Gom theo NGÀY là một nước đi rất khôn ngoan. Nó rẻ hơn hàng vạn lần so với query trên bảng raw, nhưng lại đủ linh hoạt để Roll-up lên tuần/tháng/quý trên Frontend. Nếu gom cứng theo tháng ở DB, bạn sẽ mất khả năng show chart theo ngày.
* **Idempotent Watermark:** Job incremental chạy bằng `last_processed_ledger_id` đảm bảo an toàn kể cả khi cronjob bị fail và phải retry. Không cần cờ `is_processed` trên từng row raw (tránh bottleneck khi update hàng triệu dòng).

### C. Mindset "Không chặn luồng chính" (Resilience & Decoupling)

Hệ thống tracking **tuyệt đối không được làm chậm** luồng LLM/Chat của user.

* Việc dùng `AsyncDBTokenLedger` với background drainer và queue là chuẩn bài. Nếu DB tracking bị sập hoặc quá tải, queue đầy thì drop log và văng warning. Trải nghiệm chat của user được bảo vệ tối đa.
* Dùng `contextvar` cho `mode_ctx` ('ingest' vs 'query') giúp decoupling hoàn hảo: Router ở tầng dưới không cần "đoán" xem nó đang chạy trong bối cảnh nào, worker ở tầng trên đã định nghĩa sẵn.

---

## 2. Rủi ro tiềm ẩn & Góp ý tinh chỉnh

Dù mindset rất vững, dựa trên phần Todo của bạn, kiến trúc này có một vài lỗ hổng cần vá sớm để tránh nợ kỹ thuật:

### A. Rò rỉ dữ liệu ở các "Bypass paths"

* **Vấn đề:** Trong phần TODO, bạn có note lại các path gọi `litellm` trực tiếp (`ingest_stages_enrich`, `query_intent_extractor`...). Đây là lỗ hổng chí mạng của kiến trúc Ledger. Nếu có nhiều cửa để gọi LLM, Ledger sẽ luôn bị thiếu số.
* **Giải pháp:** Thay vì rượt đuổi để cắm `TokenLedgerPort.emit` vào từng ngóc ngách, bạn nên cân nhắc can thiệp vào tầng sâu nhất: **Custom Middleware / Callback của LiteLLM**. Bắt toàn bộ event ở tầng LiteLLM, móc `mode_ctx` (từ contextvar) và snapshot data vào đó. Như vậy, dù code ở module nào gọi LLM, nó cũng bị tóm lại một chỗ.

### B. Rủi ro mất mát khi "Drop + Warn"

* **Vấn đề:** Bạn chọn "drop + warn khi queue full" để bảo vệ luồng chính. Với startup giai đoạn đầu, điều này OK. Nhưng khi scale, việc rớt vài nghìn log token mỗi khi DB spike có thể làm sai lệch cost calculation (đặc biệt nếu bạn dùng số này để charge tiền user sau này).
* **Giải pháp:** Nếu queue memory full, thay vì drop hẳn, hãy flush nó ra một file log tạm ở local disk (hoặc Redis nếu có sẵn). Một cronjob phụ có thể quét các file "orphan" này nhét lại vào DB sau.

### C. Concurrency của Watermark Job

* **Vấn đề:** Khi deploy đa server (multi-pod), nếu cronjob chạy đồng thời trên 2 pod, chúng có thể đọc cùng một dải `watermark` và đè số lên nhau lúc UPSERT.
* **Giải pháp:** Đảm bảo lock phân tán (ví dụ: dùng Redis lock hoặc bảng lock trong DB) cho job Roll-up, đảm bảo tại một thời điểm chỉ có 1 worker duy nhất được quyền đọc raw và ghi vào `token_rollup_daily`.

---

Bạn đã làm xong phần khó nhất là cắm móng kiến trúc (Alembic no-FK, Async Queue, ContextVar). Để chốt hạ, bạn muốn ưu tiên xử lý cục nghẽn ở các **Bypass paths (để chặn rò rỉ token)** trước, hay muốn dồn lực dựng **Phase 2 (Tầng Roll-up & Watermark)** để xem report ngay?

