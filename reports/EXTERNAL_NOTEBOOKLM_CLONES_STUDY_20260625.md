# Study — 4 open-source "NotebookLM clones": chúng code gì, có phải RAG không?

> **Loại**: READ-ONLY code study (0 dòng `src/` của platform bị sửa).
> **Ngày**: 2026-06-25 · **Branch**: `fix-260623-ingest-expert`
> **Cloned về**: `/var/www/html/ragbot/_external_refs/` (đã gitignore, 458MB).
> **Phương pháp**: 2 agent Opus deep-read (open-notebook, tldw_server) + đọc trực tiếp (PDF2Audio, NotebookLlama). Mọi claim có file:line.
> **Liên quan**: [[NOTEBOOKLM_VS_RAGBOT_DEEPDIVE_20260625]] — báo cáo so sánh bot `chinh-sach-xe` vs NotebookLM.

---

## 0. TL;DR — phân loại 4 repo theo "có phải RAG không?"

| Repo | Là gì | RAG? | Độ sâu RAG |
|---|---|---|---|
| **rmusser01/tldw_server** | "NotebookLM cho media" — API backend ingest video/audio/doc/web → RAG → chat/eval | ✅ **CÓ, rất sâu** | **Vượt cả naive RAG, ngang hàng/hơn platform mình ở vài trục** |
| **lfnovo/open-notebook** | NotebookLM clone self-host (notebook→sources→notes→podcast) | ⚠️ **RAG phụ, bolt-on** | Mặc định là **nhồi full-doc vào context**; chunk-RAG chỉ ở feature "Ask", và là cosine full-scan không index |
| **meta-llama/NotebookLlama** | Tutorial PDF→Podcast (4 notebook) | ❌ **KHÔNG** | Không embedding/retrieval — chỉ LLM + TTS |
| **lamm-mit/PDF2Audio** | Gradio app PDF→audio (podcast/lecture/summary) | ❌ **KHÔNG** | Không embedding/retrieval — chỉ chunk-PDF-theo-token rồi prompt |

**Phát hiện cốt lõi**: "NotebookLM" trong cộng đồng OSS có **2 nghĩa khác nhau** — (a) *podcast/audio-overview generator* (PDF2Audio, NotebookLlama — **không RAG**), và (b) *grounded Q&A over your docs* (tldw_server, open-notebook — **có RAG**). 2 repo lớn xác nhận lại bài học [[NOTEBOOKLM_VS_RAGBOT_DEEPDIVE_20260625]]: bản "personal notebook" (open-notebook) **né retrieval bằng long-context**; bản "production media engine" (tldw_server) **làm RAG nặng đô** giống hướng platform mình.

---

## 1. tldw_server (rmusser01) — RAG engine nặng đô, đáng học nhất

**Stack**: Python 3.10+, FastAPI, **SQLite mặc định / PostgreSQL optional**, vector DB **ChromaDB hoặc pgvector** (pluggable adapter), BM25 = **SQLite FTS5 `bm25()`**. ~5,600 file Python, ~90 core package. GPLv3 beta.

### RAG pipeline (file:line)
- **`RAG/rag_service/unified_pipeline.py` — 6,977 dòng**, 1 hàm `unified_rag_pipeline()`. Flow: query-classify → expansion/HyDE → retrieve (FTS5 + vector) → **RRF fusion** → rerank → parent/sibling expand → document grading → generate → citations → **faithfulness/claims verify** → abstention.
- Request schema `rag_schemas_unified.py` có **~70-100 tham số** (`Field()` 398 lần) — "50+ tuning params" của README là thật.
- **Vector store Port/Adapter** (giống DI của mình): `vector_stores/base.py` `VectorStoreAdapter(ABC)` + `factory.py` register; adapter `chromadb_adapter.py`, `pgvector_adapter.py` (HNSW+IVFFlat).
- **Embeddings provider pattern**: OpenAI / SentenceTransformers (`all-MiniLM-L6-v2`) / HuggingFace / Anthropic + multi-tier cache.

### Chunking — viên ngọc của repo: **14 strategy** (`Chunking/base.py:20-32`)
`words, sentences, paragraphs, tokens, semantic (TF-IDF cosine boundary), json, xml, ebook_chapters, propositions (clause-level), rolling_summarize (LLM), fixed_size, structure_aware, code, code_ast (Python AST)`.
- Language-aware tokenizer: jieba (中文), fugashi (日本語), konlpy (한국어), pythainlp (ไทย).
- Grapheme-safe (không cắt emoji/ZWJ, `base.py:154-215`).
- **Anthropic-style contextual retrieval** built-in: `structure_aware.py:_build_contextual_header` prepend breadcrumb `folder > doc > H1 > H2` vào mỗi chunk.
- Timecode-mapped chunking cho transcript media.

### Hybrid + rerank — đầy đủ
- BM25 FTS5 (`database_retrievers.py:763`) + vector + **RRF** (`_reciprocal_rank_fusion` :1908, `retrieve_with_fusion` :3249, mode rrf/weighted/max, `hybrid_alpha` default 0.7).
- **7 reranker strategy** (`advanced_reranking.py`): FlashRank (TinyBERT), cross-encoder, LLM-scoring, two-tier, llama.cpp GGUF, MMR diversity, hybrid.
- HyDE, PRF, query expansion (synonym/multi-query/entity/acronym/semantic), decomposition/multi-hop, graph retrieval, evidence chains, document grading — **class CRAG/self-RAG**.

### Media ingest — "NotebookLM cho media" thực thụ
- PDF: pymupdf/pymupdf4llm/**docling** + **8 OCR backend** (tesseract, dots.ocr, deepseek_ocr, hunyuan_ocr, nemotron_parse...).
- Audio: faster_whisper, NeMo Canary/Parakeet, Qwen3-ASR + diarization + VAD + WebSocket streaming.
- Video/YouTube: yt_dlp + ffmpeg. Web: trafilatura + Playwright stealth. EPUB, MediaWiki dump, email.

### Eval harness — thật (hơn đa số OSS)
`core/Evaluations/`: G-Eval, `metrics_retrieval.py` (hit@k, recall@k, mrr, ndcg), OCR eval, embeddings A/B test. In-pipeline: `faithfulness.py` (claim extract + per-claim verify), quality_grading, post_generation_verifier, abstention.

### Multi-tenant — KHÁC mình
**DB-per-user file** (`Databases/user_databases/<user_id>/*.db`), KHÔNG phải RLS row-scoping. Auth: JWT/API-key, org/team RBAC, MFA, BYOK. Đơn giản hơn nhưng scale kém hơn single-DB + `record_tenant_id` của mình.

### So với platform mình
| | tldw_server | Ragbot mình |
|---|---|---|
| Retrieval sophistication | HyDE/PRF/CRAG/14-chunk/7-rerank — **rất mạnh** | mạnh, nhưng ít chunk-strategy hơn |
| Multi-tenant | file-per-user | **RLS single-DB (mạnh hơn)** |
| Pipeline | 1 hàm 6,977 dòng + param request-time | **node nhỏ + config chain DB (sạch hơn)** |
| Ingest endpoint | nhiều route per-format | **1 canonical `documents/create` (gọn hơn)** |
| Vector | SQLite-first, pgvector opt-in | **pgvector-first** |

→ **Đáng học**: 14-strategy chunker (đặc biệt `structure_aware` contextual header + `propositions` + `code_ast`), 7-reranker registry, faithfulness/claims verifier. **Mình hơn**: kiến trúc multi-tenant + pipeline decomposition + config-driven.

---

## 2. open-notebook (lfnovo) — notebook long-context, RAG chỉ là phụ

**Stack**: FastAPI (port 5055) + Next.js 16 frontend + **SurrealDB v2** (1 DB cho record + graph edge + vector), LangGraph, lib `esperanto` (18+ provider). MIT, v1.10.0.

### RAG verdict — đây là điểm so sánh quan trọng
**Mặc định KHÔNG retrieve — nhồi full-doc người dùng chọn vào prompt.** 3 surface:
| Surface | Retrieval? | Context |
|---|---|---|
| **chat** (`graphs/chat.py`) | ❌ KHÔNG | user-picked whole-doc qua `context_config` (`routers/chat.py:421`) |
| **source_chat** | ❌ KHÔNG | toàn bộ `full_text` 1 source |
| **ask** (`graphs/ask.py`) | ✅ CÓ | LLM sinh ≤5 search term → mỗi term `vector_search(10)` → synthesis |

- **KHÔNG có ANN index** — `fn::vector_search` (`migrations/5.surrealql:75-133`) là **cosine full-table scan O(N)**, top-K hardcode **10** (`ask.py:104`), `min_similarity=0.2`.
- KHÔNG hybrid fusion (text_search BM25 có nhưng **không nối vào answer**, bị comment `ask.py:101-103`), **KHÔNG reranker, KHÔNG query rewrite**.
- Chunking: LangChain `MarkdownHeaderTextSplitter`/`RecursiveCharacterTextSplitter`, **chunk=400 token, overlap 15%** (`utils/chunking.py`).
- Grounding **mềm**: prompt chỉ dặn "based on documents... acknowledge uncertainty" (`ask/final_answer.jinja:5`) — KHÔNG có refusal template/anti-hallu lockdown như sacred HALLU=0 của mình. Citation `[source:id]` chỉ prompt-engineered, **không validate post-hoc**.
- **KHÔNG multi-tenant** — 1 password global, mọi query global, không workspace/tenant.
- **Podcast** (signature NotebookLM): lib `podcast-creator`, prompt `podcast/outline.jinja` + `transcript.jinja`, 1-4 speaker.
- Điểm hay: credential Fernet-encrypted UI-managed (`domain/credential.py`), agentic multi-query trong Ask, async job queue (surreal-commands).

### So với platform mình
Mô hình "**bạn tự curate doc nào cho AI thấy**" + long-context — **ngược** với "retrieve tự động trên corpus scoped" của mình. Đúng minh họa cho luận điểm: bản personal **né retrieval bằng long-context**. Trên mọi trục retrieval-quality + multi-tenant-scale, nó **cố tình nhẹ** chứ không expert-grade.

---

## 3. NotebookLlama (meta-llama cookbook) — KHÔNG phải RAG

4 Jupyter notebook = tutorial **PDF → Podcast**:
- Step 1: `Llama-3.2-1B` clean text PDF → `.txt` (chỉ dọn ký tự, không summarize).
- Step 2: `Llama-3.1-70B` viết transcript podcast.
- Step 3: `Llama-3.1-8B` thêm kịch tính/ngắt lời.
- Step 4: TTS `parler-tts-mini` + `bark/suno` → audio 2 speaker.

requirements: `PyPDF2, torch, transformers` — **không embedding, không vector DB, không retrieval**. Đây là **content-generation pipeline**, không phải knowledge-retrieval. "Open version of NotebookLM" ở đây = **bắt chước feature Audio Overview**, KHÔNG phải Q&A grounded.

---

## 4. PDF2Audio (lamm-mit) — KHÔNG phải RAG

1 file `app.py` + Gradio UI. PDF → audio (podcast/lecture/summary/...). Dùng OpenAI GPT generate text + OpenAI TTS. Có template instruction (podcast/lecture/summary). `requirements: gradio, openai, pypdf, promptic` — **không embedding/vector/retrieval**. "chunk" duy nhất trong code là **audio byte chunk** khi ghi file, không phải text chunk. Cùng họ với NotebookLlama: **PDF→Podcast generator**, không RAG.

---

## 5. Bài học rút ra cho platform mình

1. **"NotebookLM clone" tách 2 nhánh**: *audio-overview generator* (không RAG) vs *grounded Q&A* (có RAG). Mình thuộc nhánh 2 — đừng so nhầm với PDF2Audio/NotebookLlama.
2. **Long-context vs retrieval**: open-notebook chọn né retrieval (full-doc stuffing) — đúng cái NotebookLM làm. Là lựa chọn hợp lý cho personal scale, **không hợp** multi-tenant 100K+ docs của mình → mình PHẢI làm retrieval đúng (xem [[NOTEBOOKLM_VS_RAGBOT_DEEPDIVE_20260625]] mục 7).
3. **tldw_server là kho ý tưởng retrieval**: 14-strategy chunker (`structure_aware` contextual header, `propositions`, `code_ast`), 7-reranker registry, faithfulness/claims verifier, query expansion 6-kiểu. Đối chiếu với gap của mình (Nhóm B lạc cột, Nhóm C no-reranker) — họ đã giải.
4. **Mình mạnh hơn họ ở**: RLS single-DB multi-tenant, pipeline node decomposition + config-chain DB-driven, 1 canonical ingest endpoint, sacred HALLU=0 + app-không-inject/override. Đừng đập cái đã chuẩn.
5. **Cơ hội cụ thể**: nếu muốn "long-context mode kiểu NotebookLM cho bot nhỏ" (hướng (c) đã đề xuất), open-notebook `context_builder.py` + `provision.py:23-28` (auto-switch large_context >105k token) là mẫu tham khảo gọn.

---

## 6. Vị trí file đã clone (để đọc tiếp)
```
_external_refs/tldw_server/tldw_Server_API/app/core/RAG/rag_service/unified_pipeline.py   # RAG core 6977 dòng
_external_refs/tldw_server/tldw_Server_API/app/core/Chunking/strategies/                   # 14 chunker
_external_refs/tldw_server/tldw_Server_API/app/core/RAG/rag_service/advanced_reranking.py  # 7 reranker
_external_refs/open-notebook/open_notebook/graphs/ask.py                                   # RAG path duy nhất
_external_refs/open-notebook/open_notebook/database/migrations/5.surrealql                 # cosine full-scan
_external_refs/open-notebook/open_notebook/utils/context_builder.py                        # long-context stuffing
_external_refs/llama-cookbook/end-to-end-use-cases/NotebookLlama/                          # 4 notebook PDF→podcast
_external_refs/PDF2Audio/app.py                                                            # Gradio PDF→audio
```

---

*Lập bởi Claude Opus 4.8 (1M context). READ-ONLY: chỉ clone + đọc, 0 dòng `src/` platform bị sửa. `_external_refs/` đã gitignore.*
