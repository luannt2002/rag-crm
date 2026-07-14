# EXPERT-GRADE AUDIT — toàn bộ RAG stack — 2026-07-13

> 7 agent read-only, mỗi cái 1 subsystem. Bằng chứng: `file:line` + **DB live** (`system_config`,
> `request_steps`, `pg_stat_user_indexes`, `EXPLAIN ANALYZE`). rule#0: không claim khi không có số.

---

## 0. CHỐT 1 CÂU

**Kiến trúc EXPERT. Tính năng expert thì TỐI ĐÈN.**

Chủ đề lặp lại ở **cả 7 subsystem**: cơ chế ĐÚNG **đã được viết, đã test** — rồi **không cắm dây** hoặc **để OFF**. Đây không phải "chưa làm". Đây là **"làm rồi, quên bật"**.

---

## 1. 🔥 5 THỨ SẼ LÀM MẤT KHÁCH (xếp theo thiệt hại × chắc chắn)

### #1 — HNSW index **KHÔNG BAO GIỜ ĐƯỢC DÙNG**. Dense retrieval đang **brute-force**.
```
pg_stat_user_indexes (DB live):
  ix_chunks_embedding_hnsw   idx_scan =      0    ← HNSW: KHÔNG DÙNG LẦN NÀO
  idx_chunks_search_vector   idx_scan = 19,020    ← BM25 GIN: dùng 19k lần
```
`EXPLAIN ANALYZE` trên đúng dense-CTE: `Sort Key: (embedding <=> $0)` → **BRUTE FORCE**. Planner chọn bitmap `record_bot_id` + full cosine sort thay vì HNSW. → `SET hnsw.ef_search` là **no-op**.
pgvector 0.8.1 CÓ `hnsw.iterative_scan` (đúng thuốc cho filtered-ANN) → **grep = 0 hit**.
> **906 chunk = 2ms, vô hình. 100k chunk/bot = full scan + sort MỖI TURN. Vách đá latency rơi đúng lúc có khách thật.** Và comment trong code **khẳng định ngược lại** ("HNSW activates") → sẽ **không ai đi tìm**.

### #2 — Model swap CÙNG DIM = **sụp chất lượng IM LẶNG. VÀ NÓ ĐÃ XẢY RA.**
`document_chunks` **không có** cột `embedding_model_version`. `VectorStorePort` **khai báo** tham số đó — `PgVectorStore` **không implement**.
Migration `20260626_embed_swap_to_openai.py`: jina-1024 → OpenAI-1024. **Cùng width → không lỗi DB, không guard nào nổ, retrieval "vẫn chạy" trên vector của KHÔNG GIAN NHÚNG KHÁC.**
`_check_embed_model_consistency` = *"Detection-only, never raises"* → **log rồi CHẠY TIẾP với model lệch.**

### #3 — Fallback embedder là **provider SAI DIM, còn sống**
`null_embedder.py` **comment 100%** → **KHÔNG có Null Object**. `build_embedder` fallback về `DEFAULT_EMBEDDING_PROVIDER="litellm"` (OpenAI 1536/1024) trong khi cột là `vector(1280)`.
→ 1 typo `embedding_provider`, hoặc 1 lần Redis miss → **âm thầm dựng embedder SAI DIM**.
> *"Nó degrade thành provider ĐANG CHẠY và SAI, thay vì null báo động."*

### #4 — Tokenizer VN **BẤT ĐỐI XỨNG** trên lexical adapter ĐANG LIVE
Index build trên `content_segmented` → lexeme `chăm_sóc`.
`pgvector_store.hybrid_search:409` **có** mirror (`segment_vi_compounds`).
`pg_bm25_retrieval.py:113,119` **KHÔNG** — query thô đi thẳng vào. Và `lexical_retrieval_provider = "pg_textsearch"` **đang LIVE**.
→ Index chứa `chăm_sóc`; adapter tìm `chăm` AND `sóc` → **MỌI từ ghép tiếng Việt MISS** ở đúng cái nhánh sinh ra để cứu dense retrieval.

### #5 — **18.1% query vào LLM với ĐÚNG 1 CHUNK** (runtime thật)
Cliff áp `absolute_floor=0.2` **TRƯỚC** khi hỏi `min_keep=3`:
```
empty_context_safety_keep_top1   79   (6.5 chunk vào → 1.0 giữ lại)
below_floor_or_single            55   (6.7 chunk vào → 1.0 giữ lại)
                          = 134/741 = 18.1%
```
Mâu thuẫn trực tiếp comment của chính constant: *"1 lần reranker chấm sai KHÔNG được làm sập tập giữ lại còn 1 chunk"*.
→ **Câu trả lời đa-dữ-kiện BẤT KHẢ THI về cấu trúc trên 18% query.**

---

## 2. 💀 MULTI-DOC — thất bại kinh điển, và nó ĐƯỢC TEST như tính năng

**2 doc mâu thuẫn giá → LLM thấy cả hai, không nhãn, đoán bừa.** 4 sự thật cộng dồn:

1. Reconciler **cố ý không đụng** giá (`query_graph.py:493`).
2. **Key dedup NGƯỢC:** `_key = (_name, price)` → cùng giá thì **gộp**, khác giá thì **CẢ HAI SỐNG**. → *Dedup gộp cái đồng thuận, giữ cái mâu thuẫn.*
3. Row sống sót **`"document_name": ""`** → LLM nhận `Michelin: 2500000` / `Michelin: 3200000`, **không nguồn, không ngày** → **không thể phân giải kể cả về nguyên tắc**.
4. `authority_score` / `valid_until` / **`superseded_by`** — **ĐÃ BỊ DROP** (migration 0010). `compute_freshness()` **0 caller**. Ingest vẫn **nhận** `authority_score` rồi **vứt**.

**Và:** `test_crossdoc_reconcile.py:68` assert `len(out)==2` → **xung đột sống sót là hành vi ĐƯỢC TEST, CỐ Ý.**

> Tenant upload bảng giá mới, **không xoá bảng cũ** (không ai xoá bao giờ) → bot báo giá cũ **~50% ngẫu nhiên** → trông như "LLM hâm". **Không sửa được bằng prompt** vì nguồn không tới prompt.

**Cộng:** không có per-doc quota → 1 catalog 500 dòng **nuốt hết** chỗ của 1 PDF chính sách 3 trang. **Thuốc chữa `rrf_round_robin.py` ĐÃ VIẾT + ĐÃ TEST + 0 CALLER.**

---

## 3. 📊 BẢNG EXPERT / CHƯA-EXPERT (7 subsystem)

| Subsystem | Expert ở đâu | CHƯA expert |
|---|---|---|
| **Upload/Ingest** | byte-sniff cứu octet-stream PDF · idempotency 3 lớp race-safe · U7 **từ chối lưu NULL embedding** · soft-fail sentinel có floor · orchestrator **0 branch theo format** · security (allow-list URL, PII boundary, quota) | 🔴 **U2 `ingest_parse` CHẾT trên API canonical** (worker tự parse, flatten → **row-as-chunk của Excel/Sheets KHÔNG với tới được**) · coverage invariant **chỉ log** · `if extracted is not None` → parse rỗng **xoá content tốt** · không VLM/agentic PDF · `text/plain` là hố catch-all |
| **Chunking** | `resolve_chunking_policy` 3-tier chuẩn · detect **bằng SHAPE không vocab** · bỏ subordinating connector (bắt lỗi "nếu" tinh vi) · HDT pop theo level tuyệt đối | 🔴 **Default THẬT = `hybrid→proposition`** (fragmenter mạnh nhất) vì `MIN_CONFIDENCE 0.45 < L5 threshold 0.6` → `recursive` **không bao giờ sống sót** · **coverage gate MÙ đúng chỗ default** (proposition mutate text → `ratio=0.000` trên output không mất gì) · atomic-protect **OFF** → formula/code **bị cắt ngang** · intro/footer bảng **bị vứt** · không multi-row header merge |
| **Embedding/Vector** | Port+Registry · CB + retry + **TPM bucket per-API-key** · per-batch count guard **fail doc thay vì ghi NULL** · tenant scope **RAISE** · matryoshka 2560→1280 ở wire · query-cache key đủ (provider/model/dim) | 🔴 **HNSW idx_scan=0** · **không guard dim per-vector** · **wire-dim là CONSTANT, không lift từ spec** → dial matryoshka trong DB **chỉ để trang trí** · **VN tokenize bất đối xứng** trên adapter live · dense query **không NFC-normalize** (chỉ sparse) · **không version model trên vector row** · RRF weight rows trong `system_config` **mồ côi** (0 hit trong src) |
| **Rerank/Retrieval** | Port+Strategy+Null chuẩn · fail-soft 3 lớp · **score-scale awareness** (cross-encoder 0..1 vs RRF 0.01) — *"hầu hết codebase làm hỏng"* · **retrieval safety-net** re-inject top-2 bị chôn · **chunk-survival forensics** | 🔴 **CRAG 97.7% BÌNH PHONG** (17/741 grade thật; correction **chạy 1 lần từ trước tới nay**) · **18% query → 1 chunk** · **57.7% retrieval bỏ qua rerank** · cold-boot rơi vào **key Jina CHẾT** · MMR chạy ở ngưỡng **chính repo đo là SAI** · **trần recall cứng** — không stage nào THÊM được chunk |
| **Query graph** | **compiled-graph singleton ĐÚNG** (không rò prompt/session giữa tenant — *"cái hầu hết team làm sai"*) · parallel wrapper byte-identical khi OFF · refuse short-circuit **KHÔNG** bỏ guard_output · số cache với **NULL embedding** | 🔴 **cache hit BỎ QUA guard_output + key không tính guardrail rule** → *safety control có cửa sổ bypass 1 tiếng* · **câu RỖNG gắn nhãn `"answered"`** · **`trace_id` đọc mà không bao giờ ghi** → mọi log `trace_id=""` · `_total_graph_iterations` **không tăng** → cap vô hiệu · 10 state key **0 reader** · `crag_grader` 5 file + knob operator = **chết hoàn toàn** |
| **Multi-bot/tenant** | 🟢 **4-key airtight** (re-validate tenant khi đọc cache, evict nếu lệch) · corpus-version thật (xoá cũng đổi hash) · delete/replace **không thể drift** · **113 knob per-bot** · cache key **không leak** | 🔴 **RLS INERT** (superuser runtime) · 1 stream chat **FIFO toàn cục** → tenant A block tenant B · semaphore **per-process không per-tenant** · **`DeleteDocumentUseCase` chết+HỎNG nhưng ĐÃ ĐĂNG KÝ DI** (bẫy gài sẵn) |
| **CLAUDE.md compliance** | 4-key **PASS** · broad-except **PASS** (0 thiếu noqa) · SysPromptAssembler **governed đủ ADR + 5-test pin** · `math_lockdown` **thật sự đã gỡ** | 🔴 **2 vi phạm Sacred#10**: (a) grounding fail-closed **override câu trả lời, BẬT mặc định, KHÔNG ADR**; (b) `<documents>` XML-wrap **tự bật theo NGÀY**, owner **không nhìn thấy được** · zero-hardcode **1019** literal · version-ref **72** hit · domain-neutral: **111-từ VI dict** feed thẳng retrieval |

---

## 4. ⚖️ MÂU THUẪN GIỮA AGENT — em phân xử (rule#0)

**"Late chunking" — agent 2 KHEN, agent 3 BÁC.**
- Agent 2: *"Late chunking: CÓ và BẬT mặc định — SOTA thật. Credit where due."*
- Agent 3 (đào sâu hơn): với provider = **zeroentropy**, late-chunking THẬT của Jina (`late_chunking: True` trên wire) **không với tới được**. Cái đang chạy là `shared/late_chunking.py:86` — **prepend chuỗi 200 ký tự** `f"[Document context: {prefix}]\n\n{chunk}"`, rồi **trích arxiv 2409.04701 (+24.47% nDCG)** cho một **kỹ thuật HOÀN TOÀN KHÁC**.

**→ Agent 3 THẮNG** (nó trace theo provider live). **"Late chunking" trên luồng thật là context-prefix hack, KHÔNG phải late chunking.** Không được tính là SOTA.

**"Grounding fail-closed" — agent 5 KHEN, agent 6 CHÊ.**
- Agent 5: default an toàn đúng.
- Agent 6: vi phạm Sacred#10 (app override answer, bật mặc định, không ADR).
→ **Cả hai đúng.** Đây là căng thẳng THẬT: **an toàn tốt** nhưng **vi phạm rule thiêng của chính dự án**. → Phải **chọn**: viết ADR hợp thức hoá, hoặc flip về `observe`. Không được lờ.

---

## 5. 🕯️ CHỦ ĐỀ CHÍNH: "Đã xây, đã test, và TỐI ĐÈN"

| Cơ chế đúng | Trạng thái |
|---|---|
| `rrf_round_robin` (per-doc quota) | viết + test, **0 caller** |
| `chunking_strategy/registry.py` | viết, **DISABLED** |
| `crag_grader` (5 file + Port + operator knob) | **0 call site** |
| multi-vector / ColBERT | **comment hết** |
| `self_rag_router` | **comment hết** |
| `null_embedder` | **comment hết** → sinh ra thảm hoạ #3 |
| hyde registry | **comment hết** |
| atomic-protect (formula/code) | viết + test, **flag OFF** |
| header/footer chunk của bảng | viết, **flag OFF** |
| contextual retrieval | viết, **OFF** (cố ý, có lý do đo được) |
| `EmbedCache` | dựng ở bootstrap, **inject cho không ai** |
| `authority_score`/`superseded_by` | **DROP khỏi DB** |
| coverage invariant | viết, **chỉ log** |
| **HNSW index** | build + maintain, **idx_scan = 0** |
| RLS (24 policy, 46/46 grant) | **inert** (superuser runtime) |

---

## 6. 🎯 ƯU TIÊN SỬA (theo T1/T2/T3 của CLAUDE.md)

### T1 — BOT TRẢ LỜI THÔNG MINH (làm trước, không bàn cãi)
1. **Cliff floor bỏ qua `min_keep`** → sửa để `min_keep=3` được tôn trọng. **18% query đang chỉ có 1 chunk.** *(1 dòng, tác động lớn nhất)*
2. **Seam bug chunking**: `MIN_CONFIDENCE 0.45` < `L5 threshold 0.6` → default thật là `hybrid→proposition`. Nâng fallback conf ≥0.6 hoặc miễn `recursive` khỏi Rule 1. *(1 dòng, blast-radius toàn corpus)*
3. **VN tokenize bất đối xứng** ở `pg_bm25_retrieval.py` → mirror `segment_vi_compounds`. *(mọi từ ghép VN đang miss ở nhánh lexical)*
4. **Cross-doc conflict**: khôi phục `superseded_by`/`valid_until` + đưa `document_name`+ngày vào synthetic chunk + sửa key dedup. *(bug multi-doc kinh điển)*
5. **Câu RỖNG gắn nhãn `"answered"`** → trả error, đừng ship `""` như thành công.

### T2 — COST + PERF + UX
6. **HNSW không được dùng** → bật `hnsw.iterative_scan` (pgvector 0.8) hoặc restructure filter. *(vách đá latency đang chờ khách thật)*
7. **CRAG 97.7% bình phong** → hoặc nâng grade timeout cho khớp gateway 90s, hoặc bỏ hẳn grade LLM (đang trả latency cho phán xét bị vứt).
8. **Cache hit bỏ qua guard_output** → đưa guardrail-rule-hash vào cache key.
9. **`trace_id` không bao giờ ghi** → mọi log mất correlation.
10. **Per-doc quota** → cắm `rrf_round_robin` (code đã sẵn).

### T3 — COMPLIANCE / DEBT
11. **2 vi phạm Sacred#10** → viết ADR hoặc flip observe.
12. **Fallback embedder sai dim** → khôi phục Null Object + dim-compat check ở `build_embedder`.
13. **Model version trên vector row** → chặn silent collapse khi swap cùng dim.
14. Gỡ dead code (`crag_grader`, `self_rag_router`, `DeleteDocumentUseCase` khỏi DI).

---

## 7. Vị thế vs thế giới (cập nhật sau audit)

| Trục | Vị thế thật |
|---|---|
| **Multi-tenant/multi-bot** | 🟢 **Trên mặt bằng** (4-key + RLS design + 113 knob). ⚠️ RLS đang TẮT → chưa hiện thực hoá. |
| **Multi-doc** | 🔴 **DƯỚI mặt bằng** — không có conflict resolution, không per-doc quota, không ngày trên metadata. |
| **Parsing/multi-format** | 🔴 **Dưới** — 8 format, heuristic, không VLM/agentic (SOTA: Reducto 99.6%, Docling). Nhưng **Port+Registry → cắm Docling = 1 file**. |
| **Retrieval** | 🟡 **Kiến trúc ngang SOTA, vận hành dưới** — hybrid+rerank đúng chuẩn, nhưng CRAG bình phong, recall toggle tắt hết, HNSW không dùng. |
| **Differentiator thật** | ⭐ stats-index SQL + numeric-fidelity gate deterministic (đa số platform để LLM bịa số) — **nhưng gate đang observe**. |

---

## 8. Phương pháp
7 agent read-only song song. Bằng chứng: code `file:line` + DB live (`system_config`, `request_steps` n=1752 retrieve / 741 grade / 1751 generate, `pg_stat_user_indexes`, `EXPLAIN ANALYZE`). Mâu thuẫn giữa agent được main-session phân xử, ghi rõ ở §4.
