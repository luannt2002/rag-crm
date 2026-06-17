# ADR-W2-D8 — Ingest fairness (per-tenant fair-queue) + wire IngestQuotaService orphan

> Phase 3 ADR · Wave W2 · Tier **[T2-CostPerf — fairness/noisy-neighbour]** (không phải leak; không T1)
> Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · alembic head `0260`
> Nguồn gap: P2-C §(1) "Ingest fairness (per-tenant)" 🐛 + §2; P2-F §1 D8 🕰 + §(6); P2-H 🐛 IQ-1 (`(1)#10`, §2, §6 → D8/D2)
> Decision-register: **D8** = "Noisy neighbor: fair queuing ingest + per-tenant rate limit nhẹ nhất" (`00-DECISION-REGISTER.md:17`, P1-C §3)
> STANCE = **EVOLVE**: GIỮ Redis Streams + consumer-group + Semaphore primitive; chỉ đổi (a) Semaphore global→per-tenant key, (b) WIRE service orphan đã viết sẵn, (c) thêm 1 query-path gate mirror `tenant_rate_limiter`. KHÔNG đổi broker, KHÔNG đổi publisher, KHÔNG đụng vùng D8b.

---

## 1. Context (SỰ THẬT — evidence `file:line`)

Noisy-neighbour trên **ingest** path có 2 mặt độc lập, cả 2 hiện mở:

**(A) Consumer-side concurrency là GLOBAL, không per-tenant.**
- `redis_streams_bus.py:170` `sem = asyncio.Semaphore(max(1, int(concurrency)))` với `concurrency` default = `DEFAULT_BUS_HANDLER_CONCURRENCY = 5` (`_07_llm_sampling_defaults.py:111`).
- Semaphore này bao quanh `_dispatch_one` (`:183 async with sem`) — **1 budget chung cho TẤT CẢ tenant** trên `document.uploaded.v1` (worker `document_worker.py:522-527`). Tenant A enqueue 100 doc → batch của A chiếm cả 5 slot → handler của tenant B chờ sau A trong cùng `asyncio.gather` fan-out (`:271-278`). Đây là noisy-neighbour ở **tầng nhận-message-concurrency**, KHÔNG phải dedup.
- **SỰ THẬT**: không có per-tenant ordering nào — `xreadgroup` trả FIFO theo stream-entry-id, A flood trước thì A chiếm trước.

**(B) Quota gate built-but-not-wired (orphan).**
- `IngestQuotaService.check_and_increment` (`ingest_quota_service.py:67`) = atomic `SELECT … FOR UPDATE` + daily-rollover + `0=unlimited` + fail-loud missing-row (`:105-116`). Logic ĐÚNG.
- Docstring contract `:23-24`: "route handler MUST call this BEFORE INSERT INTO documents". **Contract UNMET**: `grep check_and_increment` trong `documents.py` + `documents_stream_upload.py` = **0** (P2-H §2 IQ-1); không có trong `bootstrap.py` (grep=0); callsite DUY NHẤT = demo `test_chat.py:2532`.
- Hệ quả: 2 upload route thật — `documents.py:97 ingest_document` (UC enqueue) + `documents_stream_upload.py:217 upload_stream` (`_enqueue_upload` → `xadd` `:182`) — **không có quota check**. Tenant flood `/documents/stream-upload` không bị chặn ở entry.

**(C) Per-tenant ingest rate-limit (query-path) chưa có.**
- Query/chat path ĐÃ có `TenantRateLimiter` (`tenant_rate_limiter.py:70`, key `rl:tenant:{uuid}:{bucket}` `:128`, fixed-window INCR+EXPIRE, fail-open). Ingest path KHÔNG mirror pattern này — chỉ có quota *đếm theo ngày* (orphan), không có *rate* (burst/phút).

**Charter mapping**: D8 = trục **RẺ** ("cost/query đo được per-tenant" `00-charter.md:15`) + **NHANH** (p95 tier-3 < 15s) — A flood không được đẩy B's ingest p95 vượt SLA. Chỉ thị D8 nguyên văn: **"per-tenant rate limit NHẸ NHẤT"** → recommend giải pháp ít state nhất.

---

## 2. Decision

### (a) Per-tenant fairness — đổi global Semaphore → **per-tenant semaphore (keyed, lazy, bounded)**

3 ứng viên, trade-off:

| Cơ chế | State cần | Fairness chất lượng | Xâm lấn (lines) | Verdict |
|---|---|---|---|---|
| **Per-tenant `asyncio.Semaphore` (keyed dict)** | in-proc dict `{record_tenant_id: Semaphore(N_per_tenant)}`, không persist | Đủ: mỗi tenant cap riêng N slot → 1 tenant KHÔNG nuốt cả 5; budget tenant-khác độc lập | **NHỎ NHẤT** — thay 1 dòng `:170` + lift tenant từ payload header trong `_dispatch_one` | ✅ **CHỌN** |
| Per-tenant token-bucket (Redis) | Redis key + refill timestamp/tenant | Tốt hơn (rate-shaping, smooth burst) nhưng = bucket-(C) job; trùng | +1 Redis round-trip/msg, +refill logic | ❌ thừa cho (a) — token-bucket thuộc (C) rate-path, không phải (a) concurrency-path |
| Weighted-fair-queue (WFQ / DRR scheduler) | per-tenant virtual-clock queue + reorder buffer | Tốt nhất lý thuyết (proportional share) | Phải viết scheduler reorder TRƯỚC `xreadgroup` consume — đụng đúng vùng dispatch D8b sửa | ❌ vi phạm "nhẹ nhất" + đụng D8b |

**CHỐT (a): per-tenant `asyncio.Semaphore`** — `_dispatch_one` đọc `record_tenant_id` từ payload (đã có trong message: `documents_stream_upload.py:169` `"record_tenant_id"`, UC enqueue tương tự), lấy/tạo `Semaphore(DEFAULT_INGEST_CONCURRENCY_PER_TENANT)` từ dict keyed theo tenant, `async with` semaphore **đó** thay vì global. Global Semaphore GIỮ làm **outer cap** (tổng tải worker không vượt `DEFAULT_BUS_HANDLER_CONCURRENCY`); per-tenant là **inner cap** (1 tenant ≤ N). Hai cap lồng nhau = `async with global_sem: async with tenant_sem[t]:`.
- Lý do "nhẹ nhất đúng": in-proc dict, 0 Redis round-trip thêm, không persist (concurrency là transient runtime state — restart reset là đúng), không đụng broker/publisher/dispatch-order. Mỗi tenant được đảm bảo tối thiểu-share; flood của A chỉ block A's slots, B's `Semaphore(N)` riêng → B không degrade N×.
- Constant mới: `DEFAULT_INGEST_CONCURRENCY_PER_TENANT` trong `shared/constants.py` (zero-hardcode). Memory bound: dict grows theo số tenant active đồng thời; LRU-evict idle entries (hoặc weakref) để không leak — bounded.

### (b) WIRE `IngestQuotaService` vào 2 upload route thật (orphan → live)

- Construct service trong `bootstrap.py` (DI Singleton — stateless, `ingest_quota_service.py:58-65` "construction cheap") + expose qua container.
- `documents.py ingest_document` (`:97`): sau resolve 4-key (`:103` `_record_tenant` + `resolve_workspace_id`), TRƯỚC khi gọi `ingest_document_uc()` enqueue — `await svc.check_and_increment(session, record_tenant_id=record_tenant)`. Session phải bound RLS qua `session_with_tenant` (service docstring `:28-31` giả định app.tenant_id set).
- `documents_stream_upload.py upload_stream` (`:217`): gate **TRƯỚC** khi stream bytes to disk + `_enqueue_upload` (`:333`) — tốt nhất ngay sau `_resolve_bot_uuid` (`:238`) để reject 429 sớm, KHÔNG ghi temp file cho upload sẽ bị từ chối (tránh /tmp fill — đã có cleanup `:295` nhưng reject-early rẻ hơn).
- `QuotaExceeded` (`shared/errors.py`) → map HTTP **429** + echo `(used, limit)` headroom (service return `:73` `(new_count, limit)` cho SLA partner).
- **KHÔNG đụng** logic service (đã ĐÃ CHUẨN per P2-H §6 #8) — chỉ wire callsite + DI + HTTP mapping.

### (c) Per-tenant ingest rate-limit (query-path) — mirror `tenant_rate_limiter`

- Tái dùng `TenantRateLimiter` (`tenant_rate_limiter.py:70`) với **prefix riêng** `rl:ingest:tenant:` (KHÔNG share bucket với chat `rl:tenant:` — comment `:48-50` đã cảnh báo prefix-isolation). Burst-cap/phút cho upload, fail-open như chat-path (`:204-210`).
- Resolve chain mirror: `tenants.ingest_rate_limit_per_min → system_config → DEFAULT_INGEST_RATE_LIMIT_PER_MIN` (constant mới). Đây là **rate** (burst/phút) bổ trợ cho **quota** (đếm/ngày) ở (b) — 2 trục khác nhau, không trùng.
- Đặt cùng chỗ với (b) gate (entry của 2 route). Phân biệt rõ với (a): (a) = consumer-side worker concurrency; (c) = HTTP entry burst rate. Cả 3 lớp khác tầng.

---

## 3. Alternatives rejected

| Alt | Lý do reject |
|---|---|
| **Giữ global `Semaphore(5)`** | Noisy-neighbour không giải; A flood vẫn nuốt budget của B (Context A). D8 mandate không đạt. |
| **Separate Redis stream per tenant** (`ragbot:document.uploaded.v1:{tenant}`) | KHÔNG scale: N tenant = N stream + N consumer-group + N subscribe-loop; xreadgroup không multiplex N>~vài-chục stream/loop; vận hành (XPENDING/XCLAIM recovery per-stream) bùng nổ. Vi phạm Simplicity-First + EVOLVE (đập kiến trúc 1-stream đang đúng). |
| **Weighted-fair-queue scheduler** | Phải reorder TRƯỚC consume = đụng đúng `_dispatch_one`/dispatch-order mà D8b đang sửa → conflict. Quá nặng cho ~21 bot. Defer tới khi tenant-count >> hiện tại. |
| **Token-bucket Redis cho (a) concurrency** | Token-bucket là rate-shaper (thuộc (C) entry-path), không phải concurrency-limiter (worker-side). Dùng cho (a) = +Redis round-trip/msg trên hot consumer loop không cần thiết. |
| **Workspace-tier quota ngay bây giờ** (`quotas.workspace_id` predicate) | P2-H WS-2: cột `workspace_id` có nhưng workspace chưa-là-entity (D2). Thêm predicate giờ = advertise capability code chưa honor. **Defer → D2**; ADR này tenant-tier only. |

---

## 4. Compatibility note với ADR-W1-D8b (VÙNG CODE CHUNG — đọc kỹ)

`redis_streams_bus.py` đang được sửa SONG SONG bởi **ADR-W1-D8b** (exactly-once inbox). Đã đọc `ADR-W1-D8b-exactly-once-inbox.md` §2. Phân định tầng để KHÔNG conflict:

- **D8b sửa**: thứ tự *bên trong* `_dispatch_one` — vị trí dedup-mark (Redis `SET NX` → Postgres `event_inbox` INSERT trong tx của handler), XACK sau commit, DLQ branch (`:348-359` → XADD `dlq` stream). D8b = **dedup/exactly-once tầng dispatch**.
- **D8 (ADR này) sửa**: chỉ dòng `:170` (global `Semaphore` → per-tenant keyed) + 1 lookup `record_tenant_id` ở ĐẦU `_dispatch_one` (trước cả dedup). D8 = **fairness tầng nhận-concurrency, NGOÀI/TRƯỚC dedup**.
- **Không giao nhau về semantics**: fairness quyết *bao nhiêu message của tenant nào chạy song song*; exactly-once quyết *message chạy có bị double/drop không*. Trực giao.
- **Giao nhau về FILE + hàm** (`_dispatch_one`): cùng sửa 1 function → **merge-conflict ở mức text, không ở mức logic**. **Thứ tự land**: **D8b LAND TRƯỚC** (T1-data-loss, ưu tiên cao hơn T2-fairness). D8 rebase lên D8b: chèn per-tenant-semaphore wrap **bao NGOÀI** khối dedup-INSERT-XACK của D8b (semaphore acquire → [D8b: dedup check → handler → inbox-INSERT → commit → XACK] → semaphore release). Cấu trúc lồng: `async with global_sem: async with tenant_sem[t]: <D8b block nguyên vẹn>`.
- **GIẢ THUYẾT (cần verify lúc land)**: `record_tenant_id` có sẵn trong payload mọi subject D8 quan tâm (`document.uploaded.v1` CÓ — `documents_stream_upload.py:169`; subject khác như `registry_changed` không cần fairness, fallback global-sem-only khi payload thiếu tenant). Verify bằng đọc payload schema mỗi subject trước khi assume.
- **Publisher KHÔNG đụng** (cả D8 lẫn D8b): `outbox_publisher` + `_xadd_or_raise` GIỮ NGUYÊN (P2-F §6 #3).

---

## 5. Implementation plan Phase 4 (failing-test-first)

**(a) Fairness:**
1. `tests/integration/test_ingest_fairness.py` (fakeredis): publish 100 msg tenant A + 5 msg tenant B trên cùng stream; handler sleep mô phỏng. Đo: B's last-msg completion time. **Assert B p95-proxy KHÔNG degrade ≥ N×** vs baseline-B-alone (RED hôm nay: global sem → B chờ sau toàn bộ batch A). Post-fix: B's `Semaphore(N)` riêng → B hoàn thành ~baseline.
2. Constant `DEFAULT_INGEST_CONCURRENCY_PER_TENANT` + dict-eviction bound test (số tenant active → dict size bounded, idle evict).

**(b) Quota wire:**
3. `tests/integration/test_ingest_quota_wired.py`: tenant A `quotas.documents_per_day_limit=2`, POST `/documents/stream-upload` 3× → today **all 3 accepted** (RED, gate absent); post-wire 3rd → **429 QuotaExceeded** + body có `(used,limit)`. Lặp cho `/documents/ingest`.
4. Negative: missing quota row → fail-loud 429 `mis-provisioned` (service `:113`), KHÔNG silent-allow.

**(c) Rate-limit:**
5. `tests/integration/test_ingest_rate_limit.py`: burst `DEFAULT_INGEST_RATE_LIMIT_PER_MIN + 1` upload/phút từ 1 tenant → cuối cùng 429; tenant khác cùng phút KHÔNG bị ảnh hưởng (prefix-isolation `rl:ingest:tenant:`); Redis down → fail-open (allowed).

6. Regression: toàn bộ suite bus/worker/document hiện có + `document.uploaded.v1` e2e vẫn flow; rebase-clean lên D8b sau khi D8b land.

---

## 6. Gate metric (Phase 4 DoD)

- 5 test trên GREEN; bus + document suite 0 regression.
- **Fairness soak**: 1 tenant flood 200 doc + 1 tenant 5 doc song song → đo p95 ingest của tenant-nhỏ; **assert < 1.5× baseline-alone** (vs hôm nay block-tới-hết-flood). Per-tenant cost đo được (charter RẺ `00-charter.md:15`).
- **Quota gate fires**: trên 2 route THẬT (không phải demo) — grep `check_and_increment` trong `documents.py` + `documents_stream_upload.py` ≥ 1 (hôm nay = 0).
- HALLU=0 sacred giữ nguyên (ingest-path, không đụng answer-path).

---

## 7. CLAUDE.md compliance check

- **Rule #0 no-guess**: mọi claim có `file:line`; (a)-payload-tenant-availability đánh nhãn GIẢ THUYẾT cần verify lúc land. ✅
- **Sacred #10 no app-inject/override answer**: ingest-path thuần, KHÔNG đụng LLM prompt/answer. ✅
- **Zero-hardcode**: 3 constant mới (`DEFAULT_INGEST_CONCURRENCY_PER_TENANT`, `DEFAULT_INGEST_RATE_LIMIT_PER_MIN`, + window nếu cần) → `shared/constants.py` SSoT; resolve chain qua `system_config`. ✅
- **Domain-neutral**: per-tenant generic, không brand/bot literal. ✅
- **No-version-ref**: tên reflect purpose (`ingest_fairness`, `INGEST_CONCURRENCY_PER_TENANT`). ✅
- **Strategy/DI**: `IngestQuotaService` wire qua DI Singleton `bootstrap.py`; `TenantRateLimiter` tái dùng (không class mới cho (c)). ✅
- **Tenant isolation**: quota gate chạy trong `session_with_tenant` (RLS); rate-key UUID-prefixed; fairness keyed `record_tenant_id`. ✅
- **4-key**: 2 route đã resolve 4-key tại boundary (`documents.py:103`, `documents_stream_upload.py:235-244`); gate dùng `record_tenant_id` sau resolve. ✅
- **T2 tier declared**: fairness/noisy-neighbour = T2-CostPerf (không T1-smartness, không leak). ✅
- **Model tier**: ADR-author Phase 3 = read-only; Phase 4 code = Opus main session. ✅
- **D8b coordination**: §4 — D8b land trước, D8 rebase NGOÀI dedup-block; publisher không đụng. ✅
- **EVOLVE not rewrite**: giữ Redis Streams + consumer-group + Semaphore primitive + service logic; chỉ wire/đổi-key. ✅
