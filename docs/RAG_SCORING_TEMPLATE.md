# RAG SCORING TEMPLATE — 8 step · 3 layer · 1 metric/step · fail→trace ngược

> Template chuẩn để CHẤM ĐIỂM toàn bộ pipeline RAG. Mỗi step có: **metric · công thức ·
> data source · ngưỡng PASS · cách trace ngược khi FAIL**. Hợp nhất (a) ekimetrics
> intrinsic 6-metric (`ref_rag/adaptive-chunking/src/adaptive_chunking/metrics.py`),
> (b) khung 8-step (`scripts/debug_rag_8step.py`), (c) COVERAGE/HALLU
> (`scripts/eval_rag_endtoend.py`). Rule #0: chỉ điểm có DATA thật, không đoán.

## Cách ekimetrics chấm (đối chiếu) — 6 intrinsic metric, GROUND-TRUTH-FREE
Embed chunk + câu + window bằng SentenceTransformer rồi cosine. KHÔNG cần đáp án vàng
(trừ BI cần gold block). Đây là cái họ dùng để xếp hạng Adaptive vs các chunker khác.

| ekimetrics | đo gì | công thức (đúng repo) | hướng |
|---|---|---|---|
| **SC** size_compliance | chunk trong [min,max] token | `1 − out_of_span/n` | cao=tốt |
| **ICC** intrachunk_cohesion | câu TRONG 1 chunk đồng nhất | `mean cos(sent_i, chunk_embed)` | cao=tốt |
| **CC** contextual_coherence | chunk hợp ngữ cảnh lân cận | `cos(chunk, window 3000-tok)` | cao=tốt |
| **BI** block_integrity | split KHÔNG cắt gold block | `intact_blocks/total` (±tolerance) | cao=tốt |
| **SD** semantic_dissimilarity | chunk kề nhau KHÁC chủ đề | `1 − Σ(w·cos)/Σw − small_chunk_penalty` | cao=tốt |
| **MRE** missing_ref_error | coref KHÔNG bị split cắt | `pairs_split/total` | **thấp**=tốt |

Composite (paper) = mean các metric. **Ragbot replicate**: `scripts/score_chunks_intrinsic.py`
(`ragbot.shared.intrinsic_metrics`) — HIỆN là **lexical xấp xỉ** (Jaccard/regex), nên
RC=1.0 vacuous, ICC/DCC không so trực tiếp paper được. → **GAP cần fix: port sang
embedding-cosine** (dùng Jina embedder sẵn có) để có số thật.

---

## TEMPLATE — điền cho mỗi bot/doc

### CỤM INGEST (chunk-level, ground-truth-free — chấm trên DB chunks)

**STEP 1 — PARSE**
- metric: `% doc parse ra markdown CÓ CẤU TRÚC` (heading/table/atomic giữ?)
- data: `document_chunks.chunk_type='table'` count · `content LIKE '%[%>%]%'` (structural_path) · heading count
- PASS: table-chunk>0 cho CSV/XLSX · structural_path>0 cho doc phân cấp · heading>0 cho PDF/DOCX
- FAIL→trace: parser registry route sai (flat OCR thay vì markdown) → `detect_parser` / byte-sniff

**STEP 2 — CHUNK** (← ekimetrics SC + BI + SD)
- metric: **SC** (size band) · **BI** (block không bị cắt) · **SD** (chunk kề khác chủ đề) · atomic uncut · parent-child
- data: `chunk_chars` phân phối · `parent_chunk_id` · `score_chunks_intrinsic.py`
- PASS: SC≥0.9 · BI≥0.7 · SD≥0.5 · atomic block (bảng/điều) không bị cắt giữa
- FAIL→trace: chunk_size sai · strategy sai (HDT/semantic/proposition) · executor

**STEP 3 — EMBED** (← ekimetrics ICC/CC dùng chính embedding)
- metric: `null_non_parent=0` · dim đúng · **ICC** (câu trong chunk cohesive) · **CC** (chunk hợp ngữ cảnh)
- data: `embedding IS NULL AND NOT EXISTS(child)` (null_leaf) · `vector_dims(embedding)` · cosine ICC/CC
- PASS: **null_leaf=0** (BẮT BUỘC — leaf không vector = vô hình) · dim=1024 · ICC/CC > baseline
- FAIL→trace: embedder 429/rate-limit (→ per-key TPM limiter) · provider sai · narrate fail

**STEP 4 — STORE**
- metric: tsvector 100% (BM25) · stats_index có entity+price SẠCH · KG edges
- data: `search_vector IS NOT NULL` · `document_service_index` (entity_name SẠCH, price_primary int) · `knowledge_edges`
- PASS: tsvector=100% · stats_index entity KHÔNG rác (`"Hiện tại"`/empty) · price là int
- FAIL→trace: tsvector trigger · **stats extraction noise** (header→entity) · KG dormant

### CỤM QUERY (cần đáp án vàng — scenario file)

**STEP 5 — RETRIEVE**
- metric: **Hit@K / CHUNK_RECALL** = answer-chunk có vào top-K? (dense+BM25+RRF+stats+rerank)
- data: `eval_rag_endtoend.py` `chunk_recall` · `request_chunk_refs` (record_request_id→chunk)
- PASS: Hit@K≥0.8 cho câu corpus-có-đáp-án
- FAIL→trace: route sai (stats vs vector) · entity-name granularity · price-range parse · embed miss
- ⚠ **BUG hiện tại**: attribution `retr_miss` báo 0 sai (spa có 3 miss mà báo 0) → cần fix harness

**STEP 6 — GENERATE**
- metric: **COVERAGE** = `answer_correct_when_corpus_has_answer / total_corpus_has_answer`
- data: `eval_rag_endtoend.py` `coverage` (so expected substring)
- PASS: COVERAGE≥0.95 (blocker để ship); HIỆN spa 0.70 / xe 0.86 / thong-tu 1.00
- FAIL→trace: chunk đúng vào context mà LLM không dùng (llm_miss) · false-refuse · scenario expect quá chặt

**STEP 7 — GUARD/REFLECT**
- metric: **HALLU rate** (fabricate/misinterpret/extrapolate/conflate) · refusal đúng
- data: `eval_rag_endtoend.py` `hallu_rate` · refusal-marker check
- PASS: **HALLU=0 SACRED** (hiện 0/3 bot ✓) · refuse chỉ khi corpus thực sự không có
- FAIL→trace: grounding check · sysprompt (bot-owner) · app KHÔNG override answer (QG#10)

### CỤM EVAL

**STEP 8 — SCORE (tổng)**
- metric: **L1 intrinsic composite** + **L2 Hit@K** + **L3 COVERAGE/HALLU** + layer-attribution
- data: gộp 3 layer; `debug_rag_8step.py --live`
- PASS: L1 composite tăng vs baseline · L2≥0.8 · L3 COVERAGE≥0.95 & HALLU=0
- FAIL→trace: chỉ ra step gốc (mỗi fail ở 1-7 quy về 1 layer)

---

## Bảng điểm hiện tại (2026-06-20, sau fix per-key limiter + dọn null_leaf)
| step | xe | spa | thong-tu | verdict |
|---|---|---|---|---|
| 1-2 PARSE+CHUNK | ✅ | ✅ | ✅ | structured |
| 3 EMBED null_leaf | 0 | 0 | 0 | ✅ PASS |
| 4 STORE tsvector | 100% | 100% | 100% | ⚠ stats noise · KG=0 |
| 5 RETRIEVE Hit@K | 0.14 | 0.20 | 0.60 | ⚠ + attribution bug |
| 6 COVERAGE | 0.86 | 0.70 | 1.00 | ⚠ spa thấp |
| 7 HALLU | 0 | 0 | 0 | ✅ SACRED |
| L1 composite (lexical) | 0.570 | 0.656 | 0.537 | ⚠ cần embedding-impl |

## GAP / fix theo độ ưu tiên (T1 smartness trước)
1. **stats_index extraction noise** (STEP 4) → STEP 5/6 spa fail. Lọc header/label→entity, price int. INGEST-side + re-ingest.
2. **L1 intrinsic lexical→embedding** — port ICC/CC/SD/MRE sang Jina-cosine (như ekimetrics) để điểm thật.
3. **STEP-5 attribution** harness bug (retr_miss=0 sai).
4. q12 entity-granularity ("Triệt lông nách combo" ↛ entity "Nách").
