# Phân tích 3-chiều: AdapChunk-doc vs Ekimetrics-paper vs Ragbot THẬT

> Đối chiếu: (1) AdapChunk design doc v1.0 (thiết kế gốc ragbot, 03/2026), (2) Ekimetrics
> LREC 2026 (arXiv 2603.25333), (3) code ragbot hiện tại. Verify từ code, không tin doc.

## 1. Hai TRIẾT LÝ adaptive-chunking khác nhau

| | **AdapChunk** (doc ragbot) | **Ekimetrics** (paper) |
|---|---|---|
| Triết lý | **STRUCTURE-driven** — phân tích cấu trúc doc → chọn strategy phù hợp | **OUTPUT-QUALITY-driven** — chạy HẾT method → đo 5 metric → chọn tốt nhất |
| Bước chọn | classify doc (block + feature) → LLM Selector → cross-check | run all candidates → compute metrics → idxmax |
| Chạy mấy chunker? | **1** (chọn rồi mới chunk) | **N** (chunk hết rồi mới chọn) |
| Chi phí chọn | 1 LLM call (Selector) | N× chunk + N× (embed+coref) metric |
| Strategy | HDT / SEMANTIC / PROPOSITION / HYBRID | page / sentence / recursive / semantic / LLM-regex |
| Đo chất lượng | KHÔNG (tin cấu trúc) | CÓ (5 metric trên output thật) |
| Atomic block (bảng/công thức) | ✅ trung tâm thiết kế (không cắt ngang) | ❌ không nhấn (PDF văn xuôi) |
| Narrate-then-embed | ✅ (LaTeX/bảng → câu mô tả) | ❌ |

→ **Khác bản chất:** AdapChunk "nhìn doc đoán strategy" (rẻ, 1 chunker). Ekimetrics "thử hết, đo, chọn" (đắt, N chunker, output-validated).

## 2. RAGBOT THẬT = lai cả hai + DETERMINISTIC hoá

| Tầng AdapChunk doc | Doc thiết kế | **Ragbot code thật** |
|---|---|---|
| T1 Block Detection & Tagging | Mistral OCR + tag | ✅ qua parser (Kreuzberg, không Mistral) |
| T2 Feature Extraction (Document Profile) | rule-based | ✅ `doc_profile_port` / `analyze_document()` |
| T3 **LLM Strategy Selector** | **LLM call** chọn strategy | 🔄 **THAY = rule-based weighted scorer** (`select_strategy(profile)`, `_W["hdt"]`...) — KHÔNG LLM |
| T4 Rule Cross-check | rule override LLM | ✅ gộp vào weighted score + fast-path (table_csv) |
| T5 Chunking Executor (4 strategy) | HDT/SEM/PROP/HYBRID | ✅ đủ 5 (+ table_csv, + recursive) |
| T6 Narrate-then-embed | LLM narrate | ✅ `narrate_service` (gather song song, 57s/9doc) |
| T7 Embedding + Vector DB | BGE/sbert + Qdrant | 🔄 **THAY = zembed-1 + pgvector** |
| (+) Ekimetrics 5-metric | (không có trong doc) | ✅ `intrinsic_metrics.py` (lexical proxy, **OFF**) |

→ Ragbot **giữ khung AdapChunk** (structure-driven) NHƯNG:
1. **Thay LLM Selector → rule scorer** (deterministic, $0, HALLU-safe)
2. **Thay engine**: Mistral→Kreuzberg, Qdrant→pgvector, BGE→zembed-1 (README "engine swaps")
3. **Thêm Ekimetrics 5-metric** làm refinement gate (lexical proxy, OFF) — lai triết lý output-quality

## 3. Vì sao ragbot deterministic-hoá CẢ HAI selector?

| | Doc/Paper gốc | Ragbot |
|---|---|---|
| AdapChunk LLM Selector | LLM chọn strategy | rule weighted-score |
| Ekimetrics 5-metric | Jina embed + Maverick coref | Jaccard/regex/size proxy |
| **Lý do** | — | **HALLU=0 sacred + $0/doc + reproducible** (docstring: "no embedder fabrication") |

→ Cả 2 nguồn dùng **model (LLM/embed/coref) để chọn chunker** → ragbot xem đó là **rủi ro** (non-deterministic, cost, vendor lock) → **proxy hoá thành rule/lexical**. Đánh đổi: **fidelity thấp hơn**, nhưng **chạy production thật**.

## 4. Tradeoff fidelity (đã verify ở report trước)
- Rule scorer (thay LLM Selector): mất khả năng "đọc hiểu" nuance của LLM, nhưng deterministic.
- Lexical 5-metric (thay Jina/Maverick): SC cao, BI trung bình, ICC/DCC/RC thấp (đo thứ khác paper).
- **Cả 2 đều OFF/coarse** → cần A/B đo Coverage/Faithfulness thật trước khi tin.

## 5. Ý nghĩa cho corpus ragbot
- Corpus chính = **bảng giá CSV** (spa/xe) → `table_csv` fast-path (1 dòng = 1 chunk, atomic) — **đã tối ưu**, không cần AdapChunk/Ekimetrics phức tạp.
- Corpus **legal (thông tư)** = doc dài có điều/khoản → PROPOSITION/HDT phù hợp (AdapChunk đúng tầng). Đây là chỗ **adaptive selection có giá trị nhất**.
- Verify load test: legal 10/10, spa 18/18 → **chunking hiện tại WORK** cho corpus thật.

## 6. Kết luận 3-chiều
1. **AdapChunk** (doc ragbot) = structure-driven, atomic-aware, narrate-embed — **thiết kế gốc, đã evolve trong code** (rule scorer thay LLM).
2. **Ekimetrics** (paper) = output-quality-driven, 5-metric evaluate-all — **ragbot port bản nhẹ, OFF**.
3. **Ragbot thật** = **AdapChunk structure-pick (rule, production)** + **Ekimetrics metric-gate (proxy, OFF)** — lai 2 triết lý, deterministic-hoá cả hai cho HALLU=0 + $0.

→ Ragbot KHÔNG thua doc/paper — nó **chọn deterministic over fidelity** có chủ đích. Đường nâng cấp (nếu cần, cho legal): (a) tính ICC/DCC bằng **zembed-1 thật** (đã có, không cần Jina) thay Jaccard → fidelity cao hơn mà vẫn không vendor mới; (b) A/B bật ekimetrics/proposition cho bot legal đo lift. KHÔNG cần LLM Selector lại (rule scorer đủ + deterministic).
