# Luồng UPLOAD tài liệu — tóm tắt cho Technical Support review

> Mục đích: mô tả **hệ thống HIỆN TẠI đang làm gì** từ lúc nhận file/link đến lúc tài liệu `active` (xài được cho chat). Mọi bước đều kèm `file:line` để review code.
> Cập nhật 2026-06-19. Stack: FastAPI + LangGraph + PostgreSQL/pgvector + Redis Streams + Jina embed.

---

## 0. Ý tưởng cốt lõi: **2-action async** (không xử lý đồng bộ quá 30s)

Upload KHÔNG xử lý xong trong 1 request. Chia 2 pha:

```
NGƯỜI DÙNG/BE gửi file/link
        │
   ┌────▼─────────────────────────────────────────────┐
   │ ACTION 1 (đồng bộ, < 1s) — chỉ NHẬN + LƯU thô     │
   │  validate link → fetch content → INSERT DRAFT     │
   │  + ghi outbox event → trả HTTP 202 "đã nhận"      │
   └────┬─────────────────────────────────────────────┘
        │  (event document.uploaded.v1)
   ┌────▼─────────────────────────────────────────────┐
   │ OUTBOX PUBLISHER → Redis Stream (exactly-once)    │
   └────┬─────────────────────────────────────────────┘
        │
   ┌────▼─────────────────────────────────────────────┐
   │ ACTION 2 (ngầm, worker) — XỬ LÝ NẶNG              │
   │  U1 validate → U2 parse → U3 clean → U4 chunk     │
   │  → U6 enrich → U6 vn_segment → U7 embed+store     │
   │  → finalize: state DRAFT → active (hoặc failed)   │
   └──────────────────────────────────────────────────┘
```

Lý do tách: parse + chunk + **embed** (gọi API Jina cho hàng trăm–hàng nghìn đoạn) tốn nhiều giây→phút, không thể bắt client chờ. Trả `202` ngay, UI poll trạng thái tới khi `active`.

---

## 1. ACTION 1 — Nhận file/link (đồng bộ, trả 202 ngay)

**Endpoint** (BE-to-BE canonical): `POST /api/ragbot/documents/create` — [`routes/documents.py:91`](../src/ragbot/interfaces/http/routes/documents.py#L91)
**Endpoint test (script `init_bots_from_urls.py` dùng)**: `POST .../bots/{bot_id}/{channel_type}/documents` — [`routes/test_chat/document_routes.py:171`](../src/ragbot/interfaces/http/routes/test_chat/document_routes.py#L171) → hàm `add_document()`

Các bước trong Action 1 (đều trong handler trên):
1. **Validate link** — `google_link_service.validate_link()` → nhận diện Google Docs/Sheets/HTML; sai → `400`.
2. **Fetch content** — `google_link_service.fetch_content()` lấy text thô NGAY tại đây. Fetch fail / rỗng → `400` (fail-loud, không nhận rác).
3. **Dedup** — `content_hash = sha256(content)`; nếu đã có doc cùng `content_hash` + `active` → bỏ qua (idempotent).
4. **Lưu 1 transaction**: `INSERT documents(state='DRAFT', raw_content=<text thô>)` **+** `INSERT outbox(subject='document.uploaded.v1')` → commit.
   - **Quan trọng**: lưu `raw_content` ngay → worker KHÔNG fetch lại link (Google `/edit?gid=` trả trang login nếu fetch lần 2).
5. Trả **HTTP 202** `{document_id, state:"DRAFT"}`.

> Sau bước này doc đã nằm trong DB ở trạng thái `DRAFT`, chưa có chunk/embedding.

---

## 2. Event bus — outbox → Redis Stream (exactly-once)

- **Outbox publisher** (worker nền): đọc bảng `outbox` bằng `FOR UPDATE SKIP LOCKED` (1 event chỉ 1 worker lấy) → `XADD` lên Redis Stream `ragbot:documents:ingest` → đánh dấu published. — [`workers/outbox_publisher.py`](../src/ragbot/interfaces/workers/outbox_publisher.py)
- Đảm bảo **không mất event, không xử lý trùng** (transactional outbox + inbox dedup).

---

## 3. ACTION 2 — Worker xử lý ngầm (pipeline ingest U1→U7)

**Consumer**: `handle_document_uploaded(payload)` — [`workers/document_worker.py:83`](../src/ragbot/interfaces/workers/document_worker.py#L83)
1. `bind_request_context(record_tenant_id, workspace_id)` — gắn tenant cho RLS + log (line 94).
2. `job_repo.update_status(running)` (line 145).
3. Gọi `doc_service.ingest(...)` (line 457).
4. Xong: `state → active`; lỗi: `state → failed` + ghi outbox event lỗi; cập nhật job (line 496/526).

**Pipeline `ingest()`** — [`document_service/ingest_core.py:177`](../src/ragbot/application/services/document_service/ingest_core.py#L177). Gọi tuần tự 7 stage:

| Stage | Làm gì | Code |
|---|---|---|
| **U1 VALIDATE** | guard kích thước `MAX_DOCUMENT_CONTENT_CHARS=500_000` + dedup `content_hash`/`source_url` | đầu hàm `ingest()` |
| **U2 PARSE** | text thô → cấu trúc: Kreuzberg OCR / openpyxl (xlsx) / google-sheets / markdown. **Đọc `raw_content` từ DB, không fetch lại link** | `infrastructure/parser/registry.py` |
| **U3 CLEAN** | chuẩn hoá NFC + bỏ nối từ + **strip prompt-injection** | `_stage_u3_clean` ([:565](../src/ragbot/application/services/document_service/ingest_core.py#L565)) |
| **U4 CHUNK** | **AdapChunk**: phân tích cấu trúc doc (heading/bảng/đoạn) → chọn chiến lược cắt; **small-to-big**: tạo `parent` (đoạn lớn) + `child` (đoạn nhỏ) | `_stage_u4_chunk` ([:566](../src/ragbot/application/services/document_service/ingest_core.py#L566)), `shared/chunking/` |
| **U5 ENRICH** | thêm câu context vào mỗi chunk (Anthropic Contextual Retrieval) bằng `gpt-4.1-mini` | `_stage_u5_enrich` ([:567](../src/ragbot/application/services/document_service/ingest_core.py#L567)) |
| **U6 VN_SEGMENT** | tách từ ghép tiếng Việt (underthesea) cho BM25 | `_stage_u6_vn_segment` ([:568](../src/ragbot/application/services/document_service/ingest_core.py#L568)) |
| **U7 EMBED + STORE** | gọi **Jina `jina-embeddings-v3` (1024-dim)** embed các **child** (parent KHÔNG embed, chỉ để mở rộng lúc trả lời) → lưu `document_chunks` (vector HNSW + tsvector BM25). Embed theo **batch** + circuit-breaker | `_stage_u7_embed_store` ([:701](../src/ragbot/application/services/document_service/ingest_core.py#L701)) |
| **finalize** | nếu còn chunk chưa embed → `failed`; ngược lại `active` | `_stage_finalize` ([:702](../src/ragbot/application/services/document_service/ingest_core.py#L702)) |

---

## 4. State machine + cách kiểm tra "done"

```
DRAFT ──(worker bắt đầu)──► (enriching → embedding) ──► active   ✅ xài được
   └────────────────────────────────────────────────► failed   ❌ lỗi
```
- UI/BE **poll** `GET .../bots/{bot_id}/{channel_type}/documents` ([`document_routes.py:30`](../src/ragbot/interfaces/http/routes/test_chat/document_routes.py#L30)) tới khi `state=active`. Chat bị chặn cho tới khi doc `active`.
- **Verify bằng SQL** (support hay dùng):
```sql
SELECT b.bot_id, d.document_name, d.state,
       count(dc.id)                                    AS chunks,
       count(dc.id) FILTER (WHERE dc.embedding IS NOT NULL) AS embedded
FROM documents d JOIN bots b ON b.id=d.record_bot_id
LEFT JOIN document_chunks dc ON dc.record_document_id=d.id
WHERE d.deleted_at IS NULL GROUP BY 1,2,3 ORDER BY 1;
```
"Done" = `state=active` + `chunks>0` + `embedded>0`. *(Lưu ý: `embedded < chunks` là BÌNH THƯỜNG — phần chênh là `parent` cố ý không embed.)*

---

## 5. Tham số hiện tại (mặc định)

| Knob | Giá trị | Ghi chú |
|---|---|---|
| Embedding model | `jina-embeddings-v3` (1024-dim) | per-bot binding; lưu cột `document_chunks.embedding` |
| Chunk size (parent / child / overlap) | `1024 / 256 / 128` | small-to-big |
| Giới hạn kích thước doc | `500_000` ký tự | guard U1 |
| Parse | Kreuzberg / openpyxl / google-sheets / markdown | self-hosted |
| Recovery | worker quét doc kẹt `DRAFT` → re-emit (cooldown 3600s) | `document_recovery_worker.py` |

---

## 6. ⚠️ Vấn đề ĐÃ BIẾT (cần support lưu ý khi review)

**Doc quá lớn (1 sheet to) → nghẽn embedding → kẹt `DRAFT` âm thầm.**

- Ca thật (2026-06-19): file `xe-3` = Google Sheet **224KB → parse ra 1 bảng khổng lồ → 2643 child chunk** (27 batch embed).
- U7 gọi Jina embed 2643 chunk → **đụng trần Jina TPM** (free tier ~**100.000 token/phút**): log `HTTP 429: Token rate limit exceeded 100,038/100,000 tokens per minute`.
- Hậu quả: ingest **rất chậm** (mỗi batch chờ ~29s), có thể **OOM** server nếu chạy lúc tải cao, và để doc ở `DRAFT` **không báo lỗi rõ** (event vẫn "processed"). Recovery re-ingest lại → tiếp tục ngốn TPM → **bỏ đói luôn query-embed của chat** (chat trả rỗng).
- **Phân biệt 2 giới hạn**: số request song song (concurrency, ví dụ 2 key × 2 = 4 lane) ≠ **token/phút (TPM)**. Thêm lane KHÔNG cứu khi trần là tổng token/phút.

**Hướng fix đề xuất (cho team review):**
1. **Cap kích thước/đoạn** mỗi doc; sheet lớn → tách nhỏ trước khi ingest.
2. **Tách lane ingest khỏi lane query** (query ưu tiên; ingest throttle/off-peak) để doc lớn không bỏ đói chat.
3. **Nâng Jina tier** (TPM cao hơn) hoặc embedder dự phòng.
4. **Surface-loud**: ingest fail/timeout phải set `failed` + log lỗi rõ, KHÔNG để `DRAFT` âm thầm; chặn recovery re-ingest vô hạn doc luôn-fail.

---

## 7. Code map nhanh (để review)

| Bước | File |
|---|---|
| Action 1 (nhận, 202) | `interfaces/http/routes/documents.py` · `routes/test_chat/document_routes.py:171` |
| Validate/fetch link | `application/services/google_link_service.py` |
| Outbox publish | `interfaces/workers/outbox_publisher.py` |
| Consumer (Action 2) | `interfaces/workers/document_worker.py:83` |
| Pipeline U1–U7 | `application/services/document_service/ingest_core.py:177` + `ingest_stages*.py` |
| Chunk (AdapChunk) | `shared/chunking/` |
| Enrich | `application/services/contextual_chunk_enrichment.py` |
| Embed + store | `infrastructure/embedding/jina_embedder.py` · `infrastructure/vector/pgvector_store.py` |
| Recovery doc kẹt | `interfaces/workers/document_recovery_worker.py` |

> Tài liệu sâu hơn: [`docs/FLOW_INGEST_DETAIL.md`](FLOW_INGEST_DETAIL.md) · [`README.md`](../README.md) §7. Nếu fact ở đây lệch code → **code đúng**.
