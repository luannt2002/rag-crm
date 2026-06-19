# Luồng UPLOAD tài liệu — Tài liệu đầy đủ (cho Technical Support)

> Mô tả **end-to-end**: từ lúc nhận file/link → đến khi tài liệu `active` (bot dùng được). Mỗi bước ghi rõ **Đầu vào → Phương pháp → Đầu ra** kèm ví dụ thật. Cập nhật 2026-06-19.
> Stack: FastAPI + LangGraph + PostgreSQL/pgvector + Redis Streams + Jina embedding + gpt-4.1-mini.

---

## 1. Tổng quan — 2-action async (không bắt client chờ)

Upload **không** xử lý xong trong 1 request. Vì parse + cắt mẩu + gọi API embed cho hàng trăm–nghìn đoạn mất nhiều giây→phút. Nên chia 2 pha:

```
NGƯỜI DÙNG / BE gửi link hoặc text
        │
   ┌────▼──────────────────────────────────────────────┐
   │ ACTION 1 (đồng bộ, ~1-10s) — chỉ NHẬN + LƯU thô     │
   │  validate link → tải text về → lưu DB state=DRAFT   │
   │  + ghi 1 event → trả HTTP 202 "đã nhận"            │
   └────┬──────────────────────────────────────────────┘
        │  event "document.uploaded.v1"
   ┌────▼──────────────────────────────────────────────┐
   │ OUTBOX → Redis Stream (đảm bảo không mất/không trùng)│
   └────┬──────────────────────────────────────────────┘
        │
   ┌────▼──────────────────────────────────────────────┐
   │ ACTION 2 (worker chạy NGẦM) — XỬ LÝ NẶNG           │
   │  B1 validate → B2 parse → B3 clean → B4 chunk       │
   │  → B5 enrich → B6 tách-từ → B7 embed+lưu            │
   │  → B8 finalize: DRAFT → active (hoặc failed)        │
   └───────────────────────────────────────────────────┘
```

---

## 2. ACTION 1 — Nhận file/link (đồng bộ, trả 202 ngay)

**Endpoint** (BE↔BE): `POST /api/ragbot/documents/create`. (Bản test mà script dùng: `POST .../bots/{bot_id}/{channel_type}/documents`.)

| | |
|---|---|
| **Đầu vào** | 1 link Google Doc/Sheet (hoặc text thuần) |
| **Làm gì** | 1) **Validate link** (probe HTTP, nhận diện Docs/Sheets/HTML). 2) **Tải text thô về NGAY** — fetch fail/rỗng → trả `400` (không nhận rác). 3) **Chống trùng**: `content_hash = sha256(text)`; đã có bản `active` cùng hash → bỏ qua. 4) **Lưu 1 transaction**: `INSERT documents(state='DRAFT', raw_content=<text thô>)` + `INSERT outbox(document.uploaded.v1)`. |
| **Đầu ra** | 1 dòng `documents` ở `DRAFT` (đã có text thô, **chưa có chunk/vector**) + trả **HTTP 202** `{document_id, state:"DRAFT"}` |

> Vì sao lưu `raw_content` ngay: để worker **không phải tải link lần 2** (link Google `/edit?gid=` tải lần 2 trả về trang login → hỏng).

---

## 3. Hand-off — Outbox → Redis Stream (exactly-once)

- **Outbox publisher** (worker nền) đọc bảng `outbox` bằng `FOR UPDATE SKIP LOCKED` (1 event chỉ 1 worker lấy) → `XADD` lên Redis Stream `ragbot:documents:ingest` → đánh dấu đã publish.
- Đảm bảo: **event không mất** (đã commit cùng tx với document), **không xử lý trùng** (inbox dedup).

---

## 4. ACTION 2 — Worker xử lý ngầm (7 bước biến đổi tài liệu)

Worker nhận event → gắn tenant (cho bảo mật RLS) → cập nhật job = `running` → đọc lại `raw_content` từ DB → chạy 7 bước:

### B1. VALIDATE
- **Vào**: text thô · **Làm**: chặn nếu quá lớn (mặc định ~500.000 ký tự) hoặc trùng `content_hash`/`source_url` · **Ra**: text hợp lệ (sai → `failed`).

### B2. PARSE
- **Vào**: text/file thô · **Làm**: đọc cấu trúc bằng Kreuzberg (OCR/PDF), openpyxl (Excel), Google Sheets, Markdown — nhận ra bảng / tiêu đề / đoạn · **Ra**: text **có cấu trúc**.

### B3. CLEAN
- **Vào**: text có cấu trúc · **Làm**: chuẩn hoá unicode (NFC), bỏ ký tự rác/nối từ, **chặn câu lệnh chèn độc (prompt-injection)** · **Ra**: text **sạch**.

### B4. CHUNK ⭐ (chỗ hay lo "chunk cùi bắp")
- **Vào**: text sạch
- **Làm** — **AdapChunk**: đo cấu trúc tài liệu (đếm heading/bảng/độ dài đoạn) → **chọn cách cắt phù hợp**:
  - Bảng giá / CSV → **mỗi dòng 1 mẩu** (kèm tiêu đề cột).
  - Văn bản thường → cắt theo ý / theo heading.
  - Văn bản luật → **mỗi Điều/Khoản 1 mẩu** (giữ "Chương > Điều" làm mốc).
  - Cắt **2 tầng (small-to-big)**: `parent` (đoạn lớn, ngữ cảnh rộng) + `child` (mẩu nhỏ, để tìm chính xác).
- **Ra**: **danh sách chunk** (các mẩu nhỏ child + mẩu lớn parent).

### B5. ENRICH
- **Vào**: mỗi chunk · **Làm**: AI (`gpt-4.1-mini`) sinh **1 câu mô tả ngữ cảnh** ghép vào đầu mẩu (kỹ thuật Contextual Retrieval — giúp tìm trúng hơn) · **Ra**: chunk = `[câu context] + [text gốc]`.

### B6. TÁCH TỪ tiếng Việt
- **Vào**: chunk tiếng Việt · **Làm**: tách từ ghép (underthesea) phục vụ tìm theo từ khoá (BM25) · **Ra**: text đã tách từ.

### B7. EMBED + LƯU
- **Vào**: các **child** chunk · **Làm**: gọi **Jina `jina-embeddings-v3`** đổi mỗi mẩu thành **vector 1024 số**, embed theo **batch** (có circuit-breaker khi lỗi) → lưu bảng `document_chunks` (vector HNSW + text BM25). *Parent KHÔNG embed — chỉ dùng để mở rộng ngữ cảnh lúc trả lời.* · **Ra**: chunk **có vector → tìm kiếm được**.

### B8. FINALIZE (chốt trạng thái)
- **Vào**: toàn bộ chunk · **Làm**: kiểm mọi child đã có vector chưa · **Ra**: `state` lật **`DRAFT → active`** ✅ (dùng được) hoặc **`failed`** ❌.

---

## 5. Ví dụ END-TO-END thật (file Thông tư 09/2020/TT-NHNN)

**Đầu vào** — 1 link Google Doc, text thô liền mạch:
```
NGÂN HÀNG NHÀ NƯỚC VIỆT NAM   Số: 09/2020/TT-NHNN
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM   Độc lập – Tự do – Hạnh phúc ...
```

**Đầu ra cuối** — `state=active`, **576 chunk** (489 child có vector + 87 parent không vector).

**1 chunk thật trông như này** (thấy rõ B5 thêm câu context ở đầu):
```
[context AI thêm]  Đoạn đầu của tài liệu, giới thiệu về Thông tư số 09/2020/TT-NHNN
                   ban hành ngày 21/10/2020 về an toàn hệ thống thông tin trong ngân hàng.
[text gốc]         NGÂN HÀNG NHÀ NƯỚC VIỆT NAM ... Căn cứ Luật Các tổ chức tín dụng ...
```
→ mỗi mẩu ~300–400 ký tự, gọn đúng 1 ý → bot hỏi "Thông tư có hiệu lực khi nào?" sẽ tìm trúng mẩu chứa "01/01/2021".

---

## 6. Trạng thái + cách kiểm tra "done"

```
DRAFT ──(worker bắt đầu)──► (enriching → embedding) ──► active   ✅ dùng được
   └────────────────────────────────────────────────► failed   ❌ lỗi
```
- BE/UI **poll** `GET .../bots/{bot_id}/{channel_type}/documents` tới khi `state=active`. Chat bị chặn cho tới khi doc `active`.
- **Verify bằng SQL**:
```sql
SELECT b.bot_id, d.document_name, d.state,
       count(dc.id)                                         AS chunks,
       count(dc.id) FILTER (WHERE dc.embedding IS NOT NULL) AS embedded
FROM documents d JOIN bots b ON b.id=d.record_bot_id
LEFT JOIN document_chunks dc ON dc.record_document_id=d.id
WHERE d.deleted_at IS NULL GROUP BY 1,2,3 ORDER BY 1;
```
"Done" = `state=active` + `chunks>0` + `embedded>0`. *(`embedded < chunks` là BÌNH THƯỜNG — phần chênh là `parent` cố ý không embed.)*

---

## 7. Cách CHECK chunk có "cùi bắp" không

1. **Xem mẩu thật**:
   ```sql
   SELECT left(content,150) FROM document_chunks dc
   JOIN documents d ON d.id=dc.record_document_id JOIN bots b ON b.id=d.record_bot_id
   WHERE b.bot_id='<bot>' ORDER BY chunk_index LIMIT 20;
   ```
2. **Số mẩu / tài liệu hợp lý?** Vài chục–vài trăm = ổn. **Hàng nghìn mẩu cho 1 file** = tài liệu quá to / parse sai (ví dụ thật: file `xe-3` 1 sheet → **2643 mẩu** = bất thường, cần tách nhỏ).
3. **Cờ cảnh báo trong log lúc ingest**:
   - `ingestion_validation_issues` — báo mẩu **quá ngắn** (1–2 ký tự) hoặc **trùng nhau** (near-duplicate).
   - `chunk_quality_below_threshold` — mẩu có điểm liên quan thấp lúc tìm kiếm.

---

## 8. Tham số hiện tại (mặc định)

| Knob | Giá trị |
|---|---|
| Embedding | `jina-embeddings-v3` (1024-dim), lưu cột `document_chunks.embedding` |
| Reranker (lúc trả lời) | `jina-reranker-v3` |
| Chunk size (parent / child / overlap) | `1024 / 256 / 128` |
| Giới hạn kích thước doc | `~500.000` ký tự |
| Parse | Kreuzberg / openpyxl / Google Sheets / Markdown (self-hosted) |
| Recovery | worker quét doc kẹt `DRAFT` → thử lại (cooldown 3600s) |

---

## 9. ⚠️ Vấn đề ĐÃ BIẾT (lưu ý khi review)

**Tài liệu quá lớn (1 Sheet khổng lồ) → nghẽn embed → kẹt `DRAFT` âm thầm.**
- Ca thật 2026-06-19: file `xe-3` = Google Sheet 224KB → parse ra 1 bảng khổng lồ → **2643 child chunk** (27 batch embed).
- B7 gọi Jina embed 2643 mẩu → **đụng trần token/phút của Jina** (gói free ~100.000 token/phút): log `HTTP 429: Token rate limit exceeded 100,038/100,000 tokens per minute`.
- Hậu quả: ingest rất chậm, có thể kẹt `DRAFT` không báo lỗi rõ, và **bỏ đói luôn việc embed câu hỏi của chat** → chat trả rỗng.
- **Phân biệt**: số request song song (concurrency, vd 2 key × 2 = 4 lane) **≠** token/phút (TPM). Thêm lane KHÔNG cứu khi trần là tổng token/phút.

**Hướng xử lý:** (1) tách nhỏ tài liệu lớn trước khi upload; (2) tách riêng lane embed-nền khỏi lane chat (chat ưu tiên); (3) nâng gói Jina (TPM cao hơn); (4) khi embed fail/timeout → set `failed` + log rõ, không để `DRAFT` âm thầm.

---

## 10. Code map (cho ai cần đào code)

| Bước | File |
|---|---|
| Action 1 (nhận, 202) | `interfaces/http/routes/documents.py` · `routes/test_chat/document_routes.py:171` (`add_document`) |
| Validate/fetch link | `application/services/google_link_service.py` |
| Outbox publish | `interfaces/workers/outbox_publisher.py` |
| Consumer (Action 2) | `interfaces/workers/document_worker.py:83` (`handle_document_uploaded`) |
| Pipeline B1–B8 | `application/services/document_service/ingest_core.py:177` (`ingest`) + `ingest_stages*.py` |
| Chunk (AdapChunk) | `shared/chunking/` |
| Enrich | `application/services/contextual_chunk_enrichment.py` |
| Embed + lưu | `infrastructure/embedding/jina_embedder.py` · `infrastructure/vector/pgvector_store.py` |
| Recovery doc kẹt | `interfaces/workers/document_recovery_worker.py` |

> Tài liệu này mô tả **phương pháp**. Nếu fact lệch code → **code đúng**. Cần trích code đoạn nào báo em.
