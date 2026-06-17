# Ekimetrics Adaptive-Chunking — dive toàn bộ luồng + so với ragbot

> Repo: `_external_refs/adaptive-chunking` (LREC 2026, arXiv 2603.25333).
> Đọc từ code thật: pipeline.py · metrics.py · splitters.py · paper/analysis.py.

## 1. Luồng đầy đủ (6 bước)

```
PDF/Excel
  │
  ▼ [1] PARSE — parsing.py (Docling / PyMuPDF / AzureDI / Excel)
  │   → JSON {document_name, pages, full_text, split_points, titles}
  │
  ▼ [2] SPLIT — chạy 8 method ỨNG VIÊN cho CÙNG 1 doc:
  │     page · sentence · LangChain-recursive(default,1100) ·
  │     our-recursive(1100,600) · semantic(Qwen3-Embed) · LLM-regex(GPT sinh regex)
  │   → mỗi method = 1 bộ chunk ứng viên
  │
  ▼ [3] POST-PROCESS — postprocessing.py
  │     split chunk quá to · merge chunk quá nhỏ (min_chunk_tokens) ·
  │     gap detect/repair · gắn page+title metadata
  │
  ▼ [4] COMPUTE 5 METRIC — metrics.py (per method, per doc, KHÔNG cần ground-truth)
  │     SC  Size Compliance      = % chunk trong khoảng token cho phép
  │     ICC Intrachunk Cohesion  = cosine(câu trong chunk, embedding chunk)  [Jina v3]
  │     DCC Contextual Coherence = sim(chunk, cửa sổ ngữ cảnh xung quanh)    [Jina v3]
  │     BI  Block Integrity      = % block cấu trúc (đoạn/bảng/list) giữ nguyên
  │     RC  Filtered Missing Ref = % chuỗi coref (entity–đại từ) KHÔNG bị cắt qua ranh chunk [maverick-coref]
  │
  ▼ [5] SELECT BEST per doc — paper/analysis.py::find_best_method()
  │     score(method) = weighted_avg(5 metric)   → idxmax → method tốt nhất CHO DOC ĐÓ
  │     (ADAPTIVE = doc khác nhau chọn method khác nhau)
  │
  ▼ [6] RAG EVAL — paper/rag_utils.py (Haystack hybrid retrieval)
        → Retrieval Completeness + Answer Correctness (Groq LLM judge)
```

## 2. Cốt lõi SELECT (find_best_method)
```python
# score mỗi method = trung bình CÓ TRỌNG SỐ của 5 metric (bỏ qua NaN)
for method in candidates:
    score[method] = Σ(metric_i × weight_i) / Σweight_i
best_method = argmax(score)   # idxmax
```
→ **Evaluate-then-pick**: chạy HẾT method → chấm 5 metric → chọn cao nhất. Khác "đoán theo feature".

## 3. Benchmark (33 doc, 3 domain, ~1.18M token)
| | Adaptive | LangChain recursive | Page |
|---|---|---|---|
| Retrieval Completeness | **67.7** | 58.1 | 59.1 |
| **Answer Correctness** | **78.0** | 70.1 | 73.3 |
| Answered queries | **65/99** | 49/99 | 49/99 |

Mean 5-metric: Adaptive **91.07** > LLM-regex 89.8 > LangChain 88.6 > Semantic 76.5 > Sentence 73.3.

## 4. Models repo dùng (pinned)
Jina-embeddings-v3 (ICC/DCC) · Qwen3-Embedding-0.6B (semantic chunk) · maverick-coref (RC) · tiktoken o200k_base (token) · Groq (RAG eval + coref) · OpenAI (embed/semantic).

---

## 5. SO VỚI RAGBOT — ai làm gì

| | **Ekimetrics (paper)** | **Ragbot production** (`select_strategy`) | **Ragbot ekimetrics selector** (`_19_sprint3`, OFF) |
|---|---|---|---|
| Cách chọn | chạy HẾT method → chấm 5 metric → idxmax | nhìn **feature doc** (bảng? heading?) → chọn 1 method | dùng **5 metric làm THRESHOLD-rule** (RC>0.8, DCC>0.5, BI>0.6, SC>0.7) tinh chỉnh feature-pick |
| Method | page/sentence/recursive/semantic/LLM-regex | table_csv/hdt/semantic/recursive/hybrid | (như production + metric gate) |
| Chi phí | CAO (embed+coref MỖI ứng viên) | RẺ ($0/doc, heuristic) | TRUNG BÌNH (metric nhẹ, không chạy hết ứng viên) |
| Trạng thái | repo nghiên cứu | ✅ ĐANG DÙNG | ⚠️ **default OFF** |

**Phát hiện:** ragbot **đã port 5 metric của paper** thành threshold-rule (`ekimetrics_5metric_selector_enabled`), cite đúng arXiv 2603.25333 — nhưng **chưa bật**. Tức ragbot không chạy full evaluate-all-candidates (đắt) mà dùng **bản nhẹ**: feature-pick + metric-threshold refine.

## 6. Ý nghĩa cho ragbot
- ✅ Verify report cũ "chunking OK" **vẫn đúng** — chunk có type, không flat.
- 🎯 **Lever chưa khai thác**: bật `ekimetrics_5metric_selector_enabled` (A/B đo) — paper cho thấy adaptive selection lift Answer Correctness **70→78%**. Nhưng:
  - Ragbot bản nhẹ (threshold) KHÔNG bằng full evaluate-all (paper) — kỳ vọng lift nhỏ hơn.
  - Cần **A/B đo trên corpus thật** (rule#0) trước khi default ON — đo Coverage/Faithfulness, không đoán %.
- ⚠️ Khác biệt corpus: paper test PDF dài (legal/technical 1.18M token); ragbot corpus chính là **bảng giá CSV** (table_csv đã tối ưu) → lift từ adaptive có thể nhỏ với bot bảng giá, lớn hơn với bot legal/doc dài.

## 7. Kết luận
- Ekimetrics = **evaluate-then-pick** (chạy hết method, chấm 5 metric, chọn tốt nhất per doc). Mạnh nhưng đắt.
- Ragbot = **feature-pick + metric-threshold (nhẹ)**, đã port nhưng OFF.
- **Đường cải thiện**: A/B bật ekimetrics selector cho bot doc-dài (legal), đo lift thật. KHÔNG cần đổi kiến trúc — chỉ flip flag + đo.
