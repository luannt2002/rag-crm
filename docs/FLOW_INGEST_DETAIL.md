# Luồng UPLOAD / INGEST tài liệu — chi tiết từng step

> Verified từ `src/ragbot/application/services/document_service.py` (INGEST_STEP_NAMES) +
> chunking/enrich/embed (2026-06-15). Model enrich = `gpt-4.1-mini` · embed = ZeroEntropy `zembed-1`.
> Pattern: **2-action async** — HTTP trả 202 ngay, worker xử lý nền (không chặn request).

---

## TỔNG QUAN — 2-action async pattern
```
Action 1 (HTTP, ~ms)              Action 2 (worker nền, async)
─────────────────────             ──────────────────────────────
nhận file/text → validate         consume event → parse → clean → chunk
→ tạo row documents (DRAFT)        → enrich → vn_segment → embed → store
→ emit event (Redis Streams)       → update state active/failed
→ trả 202 Accepted                 → UI poll thấy "ready"
```
2 graph (ingest vs query) **chỉ giao tiếp qua** vector store (`document_chunks`) + event bus — không gọi trực tiếp.

---

## STEP 0 — Nhận upload + resolve bot (HTTP, Action 1)
| | |
|---|---|
| **Input** | `POST /bots/{bot_id}/{channel_type}/documents` (text/link) hoặc `/documents/upload` (file) · JWT bearer · `workspace_id?` |
| **Việc** | resolve bot 4-key (hoặc 3-key unique nếu thiếu workspace) → tạo row `documents` state=`DRAFT` → emit `document.uploaded` event lên Redis Streams |
| **Output** | **HTTP 202** + `document_id` (worker xử lý tiếp nền) |
| **Code** | `routes/test_chat.py::upload_document_file` / `add_document` → `document_service.ingest()` |

→ Các step dưới chạy trong **embedded worker** (consumer trong cùng process `ragbot-py`).

---

## STEP 1 — ingest_validate
| | |
|---|---|
| **Việc** | (1) size guard `MAX_DOCUMENT_CONTENT_CHARS = 500_000` · (2) `content_hash` dedup (trùng nội dung → skip) · (3) `source_url` dedup (UPSERT: link trùng thay nội dung) |
| **Output** | job hợp lệ hoặc reject (quá to / trùng) |

## STEP 2 — ingest_parse
| | |
|---|---|
| **Việc** | Trích text có cấu trúc theo loại file: **Kreuzberg OCR** (PDF/ảnh) · **openpyxl** (xlsx) · **Google Sheets** · **markdown**. Nếu `raw_content` đã có trong DB → SKIP refetch. |
| **Output** | text thô + metadata (loại file, bảng/đoạn) |
| **Code** | `infrastructure/parser/registry.py` |

## STEP 3 — ingest_clean
| | |
|---|---|
| **Việc** | Chuẩn hoá: **NFC unicode** + nối từ bị ngắt dòng (hyphenation) + strip prompt-injection + bỏ URL nhiễu (URL không mang tín hiệu retrieval). |
| **Output** | text sạch |
| **Code** | `shared/text_normalization.py` |

## STEP 4 — ingest_chunk (AdapChunk — rule-based, KHÔNG LLM)
| | |
|---|---|
| **Việc** | `select_strategy()` chấm rule-based (KHÔNG LLM, 0 cost/doc) chọn 1 trong: **table_csv** (1 dòng = 1 chunk, cho bảng giá/Excel) · **hdt** (heading-based) · **semantic** · **recursive** · **hybrid**. Rồi `smart_chunk()` cắt theo strategy đó. |
| **Knobs** | `chunk_size=1024` · `parent_chunk_size=1024` · `child_chunk_size=256` · `chunk_overlap=128` |
| **Output** | `List[Chunk]` (+ chunk_type, parent/child nếu dual-index) |
| **Vì sao rule-based** | deterministic, reproducible, không tốn LLM mỗi doc |
| **Code** | `shared/chunking.py::smart_chunk()` |

## STEP 5 — ingest_enrich (Contextual Retrieval) 🤖
| | |
|---|---|
| **Việc** | Mỗi chunk được gắn 1 đoạn **context prefix** (mô tả chunk này thuộc phần nào của doc) để retrieval chính xác hơn. 1 LLM call (`gpt-4.1-mini`) per chunk, context window **`max_context_tokens=100`** (nhỏ → rẻ). Anthropic Contextual Retrieval pattern. |
| **Tối ưu** | OpenAI auto prompt-cache (doc context lặp lại giữa các chunk → cache 75-99%) + **gather + semaphore** song song (không serial) |
| **Output** | chunk + `chunk_context` |
| **Cost** | ~133 call/9-doc ingest ≈ **$0.05-0.17** (rẻ vì context cap 100 token) |
| **Code** | `application/services/contextual_chunk_enrichment.py` + `chunk_context_enricher.py` |

## STEP 6 — ingest_vn_segment
| | |
|---|---|
| **Việc** | Tách từ tiếng Việt (underthesea) — phục vụ BM25/tsvector matching. Null fallback cho non-VI. |
| **Output** | text đã segment (cho lexical index) |
| **Code** | `shared/vi_tokenizer.py` |

## STEP 7 — ingest_embed_store
| | |
|---|---|
| **Việc** | (1) **Embed**: chunk (narrate + context) → vector 1280-dim qua ZeroEntropy `zembed-1` (matryoshka, batch + `embed_inter_batch_sleep_s=0.5` tránh rate-limit, circuit-breaker + bounded concurrency) · (2) **Store**: ghi `documents` + `document_chunks` (vector HNSW + `tsvector` BM25) · (3) cập nhật state `DRAFT → active` (hoặc `failed`). |
| **Output** | chunk có embedding (NULL=0 = retrieval sống) + state=active |
| **Code** | `infrastructure/embedding/zeroentropy_embedder.py` + `vector/pgvector_store.py` |

---

## TỔNG KẾT 7 step ingest

| # | Step | LLM? | Output | Cost |
|---|---|---|---|---|
| 1 | ingest_validate | — | job hợp lệ (size/dedup) | $0 |
| 2 | ingest_parse | — | text thô (Kreuzberg/xlsx/md) | $0 |
| 3 | ingest_clean | — | text sạch (NFC/inject-strip) | $0 |
| 4 | ingest_chunk | — (rule) | List[Chunk] (AdapChunk) | $0 |
| 5 | ingest_enrich | 🤖 mini | chunk + context prefix | ~$0.01-0.02/doc |
| 6 | ingest_vn_segment | — | text segment (BM25) | $0 |
| 7 | ingest_embed_store | ZE zembed-1 | vector + tsvector, state=active | ZE API |

→ **Chỉ STEP 5 (enrich) dùng OpenAI** — rẻ (context 100 token + cache). Embed dùng ZeroEntropy (API riêng). Parse/clean/chunk/segment = $0 (CPU).

## Quan sát + state
- UI poll qua `GET /documents` → thấy `current_step` + `progress_percent` + state badge (preparing/processing/ready/failed).
- `request_steps` ghi latency từng step (metadata `step_kind=ingest`).
- 9 doc (3 bot) ingest hết: ~**57s** (sau fix narrate gather; trước 6-7 phút).

## Lưu ý chi phí (đã đo)
- Upload **RẺ**: ~$0.013/lần ingest 9 doc (enrich context cap 100 token).
- Đừng re-ingest lặp khi debug — mỗi lần re-run enrich lại (không miễn phí, dù rẻ).
- Embed (ZeroEntropy) = API riêng, không tính vào hoá đơn OpenAI.

## Đổi embedding model = phải RE-INGEST
Corpus embed bằng `zembed-1` 1280-dim. Đổi sang model khác dim khác (vd Gemini 768-dim) → vector KHÔNG tương thích → phải xoá + re-embed lại toàn bộ chunks. → Giữ ZeroEntropy.
