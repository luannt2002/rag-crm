# P2-K — API CONTRACT / MULTI-CHANNEL / SCHEMA-VERSION / INGESTION-SURFACE

> Auditor P2-K · Phase 2 (Debug-all) · READ-ONLY · branch `fix-260604-action-slotmachine-dead-key`
> STANCE: **EVOLVE, không rewrite**. Evidence `file:line`. SỰ THẬT vs GIẢ THUYẾT gắn nhãn.
> Scope: app shell + HTTP routes + middleware + schemas + multi-channel transport parity + ingestion surface.

Nhãn: ✅ chuẩn · 🕰 cũ/thiếu chuẩn 2026 · ↔️ lệch/inconsistency · 🐛 bug/landmine

---

## 0. TÓM TẮT NHÃN (component × verdict)

| Component | File:line | Nhãn | Ghi chú |
|---|---|---|---|
| Schema-version header negotiation | `middlewares/schema_version.py:52-96` | ✅ | `X-Schema-Version` header, `SUPPORTED_SCHEMA_VERSIONS=(1,)`, default→1, 400 on malformed/unknown. ĐÚNG header-based (no `/v1/` URL). |
| No-version-ref (URL/class/file) | toàn bộ `interfaces/http/` | ✅ | grep `/v[0-9]/`, `class *V1/V2`, `*_v1.py` = **0 hit** (1 false-pos = docstring giải thích vì-sao-KHÔNG-dùng `/api/v1/` tại `schema_version.py:8`). |
| 4-key chat boundary | `routes/chat.py:47-93`, `schemas/chat_schema.py:34-81` | ✅ | body `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL; tenant từ JWT `request.state.record_tenant_id`; `extra="forbid"` chặn smuggle system_prompt. |
| Workspace slug validation | `chat_schema.py:57-65`, const `_09_*.py:105` | ✅ | `WORKSPACE_ID_PATTERN=^[a-zA-Z0-9-]+$`, max 64, fallback `resolve_workspace_id(... record_tenant_id)`. |
| Anti-spoof tenant từ body | `chat.py:51-55`, `chat_stream.py:94-97`, `documents.py:82-87`, `jobs.py:20-23` | ✅ | Mọi route prod lift tenant từ JWT, KHÔNG nhận từ body. |
| `sync.py` legacy `tenant_id` INT body | `routes/sync.py:88-120,164,191,221` | ✅ | body INT là **bridge** NestJS upstream → resolve UUID server-side + **cross-tenant guard** (`jwt_tenant == resolved` else 403 `sync.py:112-120`). KHÔNG dùng làm auth authority. |
| Ingest surface contract | `routes/documents.py:90-180` | ✅ | `X-Idempotency-Key` header replay-safe (TTL), 202-async, 4-key resolve, body fingerprint hash. |
| Stream upload (large body) | `routes/documents_stream_upload.py:1-40` | ✅ | URL purpose-named `/api/ragbot/documents/upload-stream`, multipart streaming bounded mem, 4-key, 202, 413 on over-size. |
| Error envelope | `errors.py:26-96` | ✅ | chuẩn hóa `{ok,data,error{code,message,details},trace_id,timestamp}`, 12 typed handler + catch-all 500. |
| SSE framing contract | `_sse_helper.py:7-22,53-74` | ✅ | 2 mode: legacy bare `data:` + W3C named `event:`; shared giữa demo + prod stream. |
| Chat webhook callback (result) | `chat_worker.py:1596-1624` | ✅ | callback_url chain request>bot>tenant>poll; HMAC-signed; retry+timeout config-driven; admin secret-rotation `admin_webhooks.py`. |
| `get_graph` DI singleton | `query_graph.py:8062-8079` | 🐛 | **process-wide singleton bỏ qua kwargs sau lần build đầu** → transport nào build trước thắng, kwargs transport kia bị silent-ignore. |
| graph_retrieve worker-vs-stream | `chat_stream.py:330` vs `chat_worker.py:1357-1360,1471` | 🐛 | kg_service None ở stream / wired ở worker (đã ghi P2-A 🐛-2). |
| stream `get_graph` thiếu 4 DI kwargs | `chat_stream.py:243-264` vs `chat_worker.py:1376-1401` | ↔️ | stream KHÔNG truyền `understand_query_cache`, `hyde_generator`, `stats_index_repo`, `doc_repo`. |
| stream `initial_state` thiếu keys | `chat_stream.py:304-332` vs `chat_worker.py:1441-1473` | ↔️ | stream thiếu `workspace_id`, `user_groups`, `bot_extra_output_tokens_per_response` trên state. |
| Body `schema_version` ingest vs header | `schemas/document_schema.py:64-77` | ↔️ | ingest dùng **body field** `schema_version:int` + validator riêng, SONG SONG với header middleware → 2 cơ chế schema-version. |
| Ingest-done success webhook | `document_worker.py:441-454` (chỉ error hook) | 🕰 | KHÔNG có webhook khi ingest **thành công** — partner phải poll `/jobs/{id}`. Chỉ `error_notify_hook` on failure. |
| `_tenant_violation` trace_id access | `errors.py:30` | ↔️ | `_.state.trace_id` (attr trực tiếp) thay vì `getattr(...,"")` như 11 handler còn lại → AttributeError nếu state thiếu. Minor. |
| SSE `replace` event (guardrail rewrite) | `_sse_helper.py:25-26,77-85` | 🕰 | docstring nhắc "math-lockdown, guardrail rewrite" replace answer client đã thấy — cần xác nhận rewrite ở **guardrail node** (output-security) chứ KHÔNG phải app-override (sacred #10). |

**Đếm nhãn**: ✅ ×11 · ↔️ ×4 · 🐛 ×2 · 🕰 ×2 (tổng 19 dòng; 🐛-2 graph_retrieve trùng P2-A).

---

## 1. NO-VERSION-REF COMPLIANCE — ✅ PASS

**SỰ THẬT** (grep evidence):
```
grep -rnE "/v[0-9]+/|/api/v[0-9]|class \w+V[0-9]+\b|_v[0-9]\.py" src/ragbot/interfaces/http/
→ 1 hit: schema_version.py:8  (docstring: "...proliferation of /api/v1/ / /api/v2/ parallel router files")
   = false positive (giải thích VÌ SAO không dùng). 0 vi phạm thật.
```
- Schema-version qua **header `X-Schema-Version`** (`schema_version.py:63`, const `SCHEMA_VERSION_HEADER="X-Schema-Version"`, `_09_*.py:125`). URL ổn định purpose-named (`/chat`, `/chat/stream`, `/documents/create`, `/documents/upload-stream`).
- `SUPPORTED_SCHEMA_VERSIONS=(1,)` — mở rộng lên `(1,2)` chỉ cần sửa const + branch trong handler, KHÔNG tạo route file mới. Đúng intent CLAUDE.md.
- **Verdict câu 1**: ✅ ĐÃ CHUẨN — đừng đụng. Khớp chuẩn 2026 (xem §7 — header-based hợp lệ khi "URL stability là hard requirement và control caching end-to-end"; Ragbot B2B partner integration = đúng use-case này).

---

## 2. 4-KEY Ở API BOUNDARY — ✅ PASS

**SỰ THẬT**:
- `ChatRequest` (`chat_schema.py:34-73`): `bot_id` (pattern+len), `channel_type` (pattern+len) REQUIRED; `workspace_id: str|None` OPTIONAL với `pattern=WORKSPACE_ID_PATTERN` + `max_length=WORKSPACE_ID_MAX_LEN`. `model_config = frozen + extra="forbid"` → chặn payload smuggle `system_prompt` (comment `:35-40` ghi rõ lý do sacred #10).
- Route (`chat.py:53-66`): `record_tenant_id = request.state.record_tenant_id` (JWT-bound, comment `:51-52` "body never carries the UUID"); `workspace_id = resolve_workspace_id(req.workspace_id, record_tenant_id=...)` fallback `str(record_tenant_id)`; rồi `registry.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — **đủ 4 key**.
- KHÔNG route prod nào nhận tenant UUID từ body. `sync.py` nhận **legacy INT** `tenant_id` (bridge) NHƯNG có cross-tenant guard `sync.py:112-120` (`jwt_tenant != resolved → 403`). KHÔNG phải spoof leak.
- Validator slug format ✅ (Pydantic `pattern` enforce `^[a-zA-Z0-9-]+$` len 1-64). Invalid → 422 (`WorkspaceIdInvalid` handler `errors.py:52-58`).
- **Verdict câu 2**: ✅ ĐÃ CHUẨN. Documents/feedback/jobs routes đồng nhất cùng pattern.

---

## 3. MULTI-CHANNEL TRANSPORT PARITY — 🐛 + ↔️ (mìn chờ flip)

`channel_type` (web/zalo/api...) là opaque string xử lý đồng nhất ở schema + lookup. NHƯNG **transport divergence giữa worker (async 202) và stream (SSE)** vượt quá 🐛-2 (P2-A đã thấy kg_service). P2-K phát hiện thêm:

### 🐛-K1 `get_graph` DI singleton ⇒ "ai build trước thắng" (LATENT, nghiêm trọng nhất)
**SỰ THẬT** (`query_graph.py:8062-8079`):
```python
async def get_graph(**di_kwargs):
    if _GRAPH_SINGLETON is not None:
        return _GRAPH_SINGLETON          # ignore kwargs sau lần đầu
    _GRAPH_SINGLETON = build_graph(**di_kwargs)
```
- Đồ thị là **singleton process-wide**, build 1 lần, các call sau **bỏ qua kwargs**.
- Worker truyền 4 DI kwargs mà stream KHÔNG: `understand_query_cache`, `hyde_generator`, `stats_index_repo`, `doc_repo` (`chat_worker.py:1394-1398` vs `chat_stream.py:243-264` thiếu).
- `build_graph` THẬT-SỰ dùng các kwargs này: `query_graph.py:1698-1711` (hyde), `:2087` (uq_cache), `:3095,3188` (stats_index_repo), `:3123-3142,3520` (doc_repo).
- **Hệ quả**: nếu **tiến trình build graph lần đầu qua SSE path** (request `/chat/stream` tới trước request `/chat`), thì HyDE + understand-query-cache + price-range stats + parent-child doc lookup = **None cho TOÀN BỘ request kể cả worker path** — silent degrade. Ngược lại nếu worker build trước, stream "ké" được. **Behavior phụ thuộc thứ tự warm-up = non-deterministic.**
- **GIẢ THUYẾT (chưa load-test)**: hôm nay nhiều khả năng worker build trước (worker chạy continuous, prewarm). Cần đo: restart → gửi `/chat/stream` đầu tiên → kiểm `hyde_generator is None` trong graph closure. CHƯA verify.

**Repro sketch**:
```python
# tests/unit/test_get_graph_di_parity.py — PROPOSED (không tạo, Phase 4)
async def test_stream_first_build_does_not_drop_worker_only_di():
    _reset_graph_singleton_for_test()
    g = await get_graph(**STREAM_KWARGS)   # thiếu hyde/uq_cache/stats/doc_repo
    # sau đó worker call get_graph(**WORKER_KWARGS) → trả CÙNG singleton thiếu deps
    assert g closes over hyde_generator is not None  # FAIL nếu stream build trước
```

### 🐛-2 (đã ghi P2-A) kg_service None ở stream
`chat_stream.py:330` hardcode `"kg_service": None`; worker `chat_worker.py:1471` wired conditionally. kg_service ở **initial_state (per-request)** nên KHÔNG bị singleton nuốt → đây là divergence THẬT per-call. Hôm nay inert (graph_rag_mode=disabled toàn platform, P2-A §0) → mìn chờ flip.

### ↔️-K2 stream `initial_state` thiếu 3 key
`chat_stream.py:304-332` thiếu so với worker (`chat_worker.py:1441-1473`):
- `workspace_id` (worker `:1448`) — stream KHÔNG đặt lên state dù đã resolve `workspace_id` ở `:128`. Node nào đọc `state["workspace_id"]` (vd forensic scope, RLS GUC) sẽ thấy thiếu trên SSE path.
- `user_groups` (worker `:1449`) — stream KHÔNG có → group-based ACL/filter (nếu node dùng) lệch.
- `bot_extra_output_tokens_per_response` (worker `:1466`) — stream KHÔNG có → per-bot output budget không áp trên SSE.

**Verdict câu 3**: transport KHÔNG parity. Danh sách divergence: **(1) get_graph DI singleton order-dependent [🐛-K1] · (2) kg_service None [🐛-2] · (3) 4 DI kwargs thiếu [↔️] · (4) 3 initial_state keys thiếu [↔️-K2]**. Gốc rễ chung: **2 transport tự assemble state + DI riêng, KHÔNG share 1 builder** → drift. EVOLVE: trích 1 hàm `build_chat_graph_state(...)` + `resolve_chat_di(container)` dùng chung cả 2 path (Phase 4, ADR).

---

## 4. INGESTION SURFACE — ✅ + 🕰

**SỰ THẬT**:
- `documents.py:90-180` ingest: ✅ idempotency `X-Idempotency-Key` (header opt-in, TTL replay → 200 + original job_id), 202-async, 4-key resolve, body-hash fingerprint chống key-reuse-with-diff-payload.
- `documents_stream_upload.py`: ✅ streaming multipart (500 MiB), bounded mem, Redis Stream hand-off, 202 `state="uploading"`, 413 over-size, orphan-cleanup cron.
- Document **state machine** (`domain/entities/document.py:29-39`): ingest tạo `DRAFT` (`:185,199`) → worker complete flip `active` (`document_service.py:3682-3683`, atomic state+progress, count embedding trước khi flip — fix Bug B 2026-05-13). `active`/`DRAFT` đều → `{PUBLISHED,ARCHIVED,INVALIDATED}`. API KHÔNG lộ raw state machine ra ngoài; partner thấy qua `/jobs/{id}` status (`jobs.py:16-36`).
  - **Lưu ý liên kết P2-F**: doc default chuyển `active` (retrievable) ngay sau ingest, KHÔNG dừng ở DRAFT-review-gate. Đây là **chủ đích** (no human-publish gate ở MVP) — không phải bug ở API layer; ghi nhận để Wave 6 (bot-owner publish workflow) cân nhắc.
- 🕰 **Ingest-done webhook**: `document_worker.py:441-454` CHỈ có `error_notify_hook` khi FAIL. KHÔNG có success-callback → partner BUỘC poll `/jobs/{id}`. Chat result CÓ webhook (`chat_worker.py:1596-1624`) nhưng ingest thì không → **bất đối xứng**. Chuẩn 2026 (§7): hybrid poll-as-truth + webhook-as-optimization; ingest hiện chỉ có poll. EVOLVE: thêm ingest success-callback dùng lại `create_delivery`/`webhook_notifier` đã có (delivery infra sẵn) — KHÔNG cần xây mới.
- ↔️ **Body `schema_version` vs header**: `document_schema.py:64-77` ingest có **body field** `schema_version:int` + validator riêng SONG SONG với `X-Schema-Version` header middleware. 2 nguồn schema-version → có thể mâu thuẫn (header=1, body=2). EVOLVE: hợp nhất — body field nên đọc default từ `request.state.schema_version` hoặc bỏ, để header là SSoT.

**Verdict câu 4**: ingest contract đủ idempotency + 202 + state-machine; THIẾU success-webhook (🕰) + trùng cơ chế schema-version (↔️).

---

## 5. API CONTRACT STABILITY / ERROR SHAPE — ✅

**SỰ THẬT**:
- `errors.py:26-96`: envelope chuẩn `{ok:false, data:null, error:{code,message,details}, trace_id, timestamp}` cho 12 typed exception + catch-all `Exception→500 INTERNAL_SERVER_ERROR`. `logger.exception` preserve traceback (P20 lesson). ↔️ minor: `_tenant_violation` `:30` dùng `_.state.trace_id` (attr trực tiếp) ≠ `getattr(...,"")` của 11 handler khác → AttributeError nếu state chưa set (vd violation trước TenantContext mw). Low-risk.
- SSE (`_sse_helper.py`): contract framing rõ (legacy bare-`data:` + W3C named-events), shared demo+prod → 1 nguồn.
- Versioning story: header-negotiation rõ ràng (`SUPPORTED_SCHEMA_VERSIONS`), KHÔNG ad-hoc. ↔️ duy nhất = body `schema_version` ở ingest (§4).
- **Verdict câu 5**: ✅ error shape + SSE stable; versioning story header-based rõ (trừ ingest body-field overlap).

---

## 6. WEBHOOK / CALLBACK — ✅ (chat) + 🕰 (ingest)

**SỰ THẬT**:
- Chat async result: `chat_worker.py:1590-1624` resolve callback_url **request > bot > tenant > None(poll-only)**, `create_delivery(...).deliver(...)` HMAC-signed, retry/timeout/verify-ssl config-driven (`callback_max_retries/timeout_s/verify_ssl/hmac_secret`). Memory `webhook_callback_design` Action-1+202 (a30fc87) = **đã ship & live**.
- Webhook secret lifecycle: `admin_webhooks.py` POST rotate-secret (RBAC level 80, tenant-isolated, audit_log, scrypt hash, plain secret returned once + grace period). ✅ production-grade.
- 🕰 Ingest success-callback: KHÔNG có (§4). Đây là gap duy nhất của webhook story.
- **Verdict câu 6**: chat callback ✅ full (signed + retry + rotation); ingest-done callback 🕰 thiếu → poll-only.

---

## 7. CHUẨN 2026 (WebSearch cho 🕰/↔️) + VERDICT EVOLVE

**(a) API versioning header-based** — Header versioning hợp lệ khi "URL stability là hard requirement và bạn control caching end-to-end" (đúng Ragbot B2B: 1 canonical URL cho partner integration, không CDN-cache POST). URL-path là default cho public API có CDN; Ragbot chọn header là **defensible** cho B2B write-API. KHÔNG cần đổi. (digitalapi.ai, speakeasy, asoasis 2026)

**(b) Async ingestion poll-vs-webhook** — Consensus 2026 = **hybrid: poll-as-source-of-truth + webhook-as-optimization** (client poll tới khi webhook tới rồi dừng; webhook drop/firewall/out-of-order nên KHÔNG được là cơ chế duy nhất). Ragbot ingest hiện CHỈ poll → **đúng phần "truth" nhưng thiếu optimization layer**. EVOLVE: thêm signed ingest-done webhook (delivery infra `create_delivery` đã sẵn), giữ `/jobs/{id}` poll làm fallback. (tyk.io, docsie 202-accepted, hookdeck 2026)

**(c) Multi-channel transport parity** — SSE 2026 = transport-aware (HTTP/2/3 buffering, proxy timeout, flush). Ragbot SSE framing đã chuẩn; vấn đề KHÔNG ở SSE-mechanics mà ở **2 transport tự assemble state/DI riêng** → drift (§3). Best practice: 1 shared pipeline builder, transport chỉ khác lớp framing/delivery. EVOLVE: extract shared graph-state+DI builder. (thebackenddevelopers, channel.tel 2026)

### VERDICT TỔNG: **EVOLVE, KHÔNG REWRITE**
Khung API đã expert: header-versioning đúng intent, 4-key boundary chặt, anti-spoof tenant-from-JWT, idempotency header, error envelope chuẩn, SSE shared contract, chat webhook full HMAC+rotation. **KHÔNG có vi phạm no-version-ref, KHÔNG có tenant-from-body leak.** Vấn đề = **"dây chưa nối hết giữa 2 transport"** (state/DI drift) + 1 gap webhook ingest + 1 overlap schema-version, ĐÚNG luận điểm strangler-fig. Sửa = WIRE (shared builder) + HOÀN THIỆN (ingest webhook reuse delivery) + dọn 1 overlap. Phase 4, theo ADR.

---

## "ĐÃ CHUẨN — ĐỪNG ĐỤNG"
1. `schema_version.py` header-negotiation (`X-Schema-Version`, SUPPORTED set, 400 mapping). No-version-ref clean.
2. 4-key resolve boundary: `chat.py`/`documents.py`/`feedback`/`jobs` — tenant từ JWT, `extra="forbid"`, slug pattern, `resolve_workspace_id` fallback.
3. `sync.py` legacy-INT bridge + cross-tenant guard (`:112-120`) — KHÔNG phải leak, đừng "sửa" thành nhận UUID body.
4. `errors.py` envelope (giữ 12 typed handler + traceback-preserving catch-all).
5. `documents.py` idempotency (`X-Idempotency-Key` + body-hash) + 202-async.
6. Chat callback delivery + `admin_webhooks` secret-rotation (HMAC, audit, scrypt, grace).
7. `_sse_helper` shared framing contract (demo+prod).

## TOP-3 PHÁT HIỆN NẶNG NHẤT
1. **🐛-K1 `get_graph` DI singleton order-dependent** (`query_graph.py:8062` + `chat_stream.py:243-264` thiếu 4 kwargs) — graph build lần đầu qua SSE path → HyDE/uq-cache/stats/doc-repo None cho TOÀN platform, non-deterministic theo warm-up order. CHƯA load-test verify; cần đo restart→stream-first.
2. **🐛-2 kg_service None ở stream** (`chat_stream.py:330`) — GraphRAG sống ở worker, chết ở SSE; inert hôm nay (mode disabled) nhưng mìn chờ flip (trùng P2-A).
3. **↔️ ingest success-webhook thiếu + body/header schema-version overlap** — chat có callback, ingest chỉ poll (bất đối xứng vs chuẩn 2026 hybrid); ingest `schema_version` body-field song song header middleware = 2 SSoT.
