# AdapChunk — Adaptive Document Chunking Strategy Selection for RAG

> via Structure-Aware Document Classification
> Phiên bản 1.0 · Cập nhật 03/2026 · TÀI LIỆU KĨ THUẬT CHI TIẾT (target architecture / spec)
>
> Đây là **kiến trúc đích** (target spec) cho luồng ingest/chunk của ragbot. Mọi audit code hiện tại đối chiếu về spec này + CLAUDE.md sacred rules (domain-neutral, zero-hardcode, multi-bot, multi-language, multi-format).

---

## 1. Tổng quan hệ thống

### 1.1 Vấn đề cần giải quyết
RAG phụ thuộc trực tiếp vào chất lượng chunking. Hầu hết hệ thống dùng **một** chiến lược duy nhất (vd recursive character splitting 512 token) cho mọi loại tài liệu → vấn đề nghiêm trọng:
- Chunk bị cắt giữa chừng mất ngữ cảnh (bảng bị cắt đôi, công thức tách khỏi giải thích)
- Vector embedding không phản ánh đúng ngữ nghĩa
- Retrieval trả về đoạn không liên quan
- LLM hallucinate vì thiếu thông tin nền

AdapChunk: **tự động phân tích cấu trúc tài liệu và chọn chiến lược chunking tối ưu tương ứng.**

### 1.2 Kiến trúc 7 tầng tuần tự
| Tầng | Tên | Loại | Chức năng |
|---|---|---|---|
| 1 | Mistral OCR | External API | PDF → Markdown có cấu trúc |
| 2 | Block Detection & Tagging | Code (rule-based) | Scan Markdown, gán tag từng block |
| 3 | Feature Extraction | Code (rule-based) | Trích Document Profile (thống kê) |
| 4 | LLM Strategy Selector | LLM call | Phân tích cấu trúc, chọn chiến lược |
| 5 | Rule-based Cross-check | Code (rule-based) | Kiểm tra tính hợp lệ quyết định LLM |
| 6 | Chunking Executor | Code + LLM | Thực thi chiến lược đã chọn |
| 7 | Embedding + Vector DB | Model + Qdrant | Embed + lưu chunk kèm metadata |

### 1.3 Luồng dữ liệu
```
PDF → Mistral OCR → Markdown → Block Detection → Feature Extraction
  → LLM Selector (nhận Document Profile + danh sách block đầy đủ)
  → Rule Cross-check → Chunking Executor (tôn trọng atomic block)
  → Narrate special blocks → Embedding → Qdrant + Metadata
```

---

## 2. Tầng 1: Block Detection & Tagging

### 2.1 Mục đích
Sau Mistral OCR trả Markdown, scan toàn file từ trên xuống, gán tag từng block:
- Xác định loại nội dung (text, heading, bảng, công thức, ảnh)
- Đánh dấu "vùng cấm cắt" (atomic blocks) — Chunking Executor không được cắt ngang
- Tạo đầu vào cho Feature Extraction + LLM Selector

### 2.2 Các loại block
| Tag | Cách phát hiện | Atomic? | Ví dụ |
|---|---|---|---|
| HEADING | Dòng bắt đầu `#` / `##` / `###` | Không | `# Chương 1` |
| TEXT | Dòng văn bản thường liên tiếp | Không | Đoạn văn xuôi... |
| TABLE | Các dòng chứa `\|...\|` liên tiếp | **Có** | `\| Model \| Acc \| F1 \|` |
| FORMULA | Block `$$...$$` hoặc `$...$` | **Có** | `$$E = mc^2$$` |
| IMAGE | `![desc](url)` hoặc annotation OCR | **Có** | `![Sơ đồ](img.jpg)` |

### 2.3 Xử lý đặc biệt từ Mistral OCR (BBox annotation)
- `image_type`: "image" / "table" / "graph" → gán tag trực tiếp
- `description`: mô tả tự nhiên → lưu vào block để embedding sau
- `bounding_box`: tọa độ → gắn ảnh vào đoạn văn xung quanh

### 2.4 Context Binding cho Atomic Blocks
Mỗi atomic block gắn văn bản xung quanh:
- **FORMULA**: 1–2 câu trước (câu dẫn) + 1–2 câu sau (diễn giải) → một block.
- **TABLE**: dòng tiêu đề phía trên ("Bảng 3.1...") + ghi chú/phân tích phía dưới.
- **IMAGE**: caption + đoạn văn tham chiếu ("Như Hình 2.1...").

### 2.5 Output
Danh sách block có thứ tự: `{ type, level?, content/word_count, is_atomic, context_before?, context_after?, ocr_metadata? }`

---

## 3. Tầng 2: Feature Extraction

### 3.1 Mục đích
Từ danh sách block → **Document Profile** (số liệu thống kê định lượng). Phục vụ: (1) số liệu cứng cho Rule Cross-check, (2) bổ sung cho LLM Selector.

### 3.2 Các đặc trưng
| Đặc trưng | Kiểu | Cách tính | Ý nghĩa |
|---|---|---|---|
| heading_counts | {H1,H2,H3} | Đếm HEADING theo level | Cấu trúc phân cấp |
| has_toc | Bool | Pattern "Mục lục"/"Table of Contents" | Tài liệu chính quy |
| table_count | Int | Đếm TABLE | Mật độ bảng |
| table_avg_rows | Float | TB số hàng các bảng | Kích thước bảng |
| formula_count | Int | Đếm FORMULA | Mật độ công thức |
| image_count | Int | Đếm IMAGE | Mật độ hình |
| avg_text_block_length | Float | TB word_count chỉ TEXT | Văn xuôi hay mục |
| heading_ratio | Float | heading / tổng block | Độ dày cấu trúc |
| mixed_content_score | Float | Tỉ lệ block không phải TEXT | Độ hỗn hợp |
| detected_language | String | Phát hiện ngôn ngữ chính | Chọn embedding model |

**Nguyên tắc**: tất cả tính bằng **code thuần (rule-based), KHÔNG LLM** → định lượng chính xác, tái lập, làm ground truth cho Cross-check.

---

## 4. Tầng 3: LLM Strategy Selector

### 4.1 Đầu vào (cả hai)
- **Document Profile**: số liệu định lượng
- **Danh sách block đầy đủ** (không truncate): heading giữ text, TEXT gửi word_count + 1–2 câu đầu, TABLE gửi header row + kích thước, FORMULA gửi LaTeX, IMAGE gửi description

### 4.2 04 chiến lược
| Chiến lược | Cơ chế | Phù hợp |
|---|---|---|
| **HDT** (Hierarchical Document Tree) | Phân đoạn theo heading hierarchy. Mỗi chunk mang đường dẫn đầy đủ (Chương 3 > 3.1 > 3.1.2). Hỗ trợ Parent-Child Retrieval. | Luận văn, báo cáo có mục lục rõ |
| **SEMANTIC** | Cosine similarity giữa câu liên tiếp. Cắt tại điểm chuyển ngữ nghĩa lớn nhất. | Sách giáo khoa, văn xuôi dài |
| **PROPOSITION** | LLM tách thành phát biểu nguyên tử (atomic facts). Mỗi chunk là mệnh đề độc lập, tự đủ nghĩa. | Pháp lý, hợp đồng, quy chế |
| **HYBRID** | HDT macro-level + PROPOSITION micro-level cho section > 300 từ. **Default khi không chắc.** | Hỗn hợp, fallback |

### 4.3 Output JSON cố định
```json
{ "strategy": "HDT|SEMANTIC|PROPOSITION|HYBRID", "confidence": 0.0-1.0,
  "reasoning": "...", "detected_type": "...", "risk_factors": ["..."] }
```

---

## 5. Tầng 4: Rule-based Cross-check

Kiểm tra tính hợp lệ quyết định LLM bằng quy tắc cứng dựa Document Profile (lớp an toàn).

| Điều kiện | Hành động |
|---|---|
| confidence < 0.6 | Override → HYBRID (fallback an toàn) |
| LLM chọn HDT nhưng tổng heading < 5 | Override → SEMANTIC |
| LLM chọn SEMANTIC nhưng avg_text_block_length < 50 | Override → PROPOSITION |
| LLM chọn PROPOSITION nhưng avg_text_block_length > 300 và heading > 20 | Override → HDT |
| mixed_content_score > 0.4 và LLM không chọn HYBRID | Cảnh báo (log), không override |

Bộ quy tắc mở rộng theo thời gian. Mọi override **ghi log đầy đủ** (quyết định gốc, lý do, rule trigger).

---

## 6. Tầng 5: Chunking Executor

### 6.1 Nguyên tắc chung (mọi chiến lược)
- **KHÔNG BAO GIỜ cắt ngang atomic block**: TABLE/FORMULA/IMAGE giữ nguyên vẹn, gắn chunk gần nhất.
- **Context buffer**: mỗi atomic block mang 1–2 câu trước/sau.
- **Metadata đầy đủ**: strategy_used, document_type, structural_path (HDT), confidence_score.

### 6.2 Chi tiết
- **HDT**: quét block, gặp HEADING tạo node cây; block giữa hai heading gộp thành node. Mỗi chunk mang structural_path. Section > 1000 token → tách chunk con giữ structural_path cha.
- **SEMANTIC**: duyệt TEXT, cosine giữa câu liên tiếp, similarity < ngưỡng → điểm cắt. Atomic block không bao giờ là điểm cắt (dời ra ngoài ranh giới).
- **PROPOSITION**: gửi đoạn cho LLM tách phát biểu nguyên tử — (1) độc lập tự đủ nghĩa, (2) đủ chủ ngữ+ngữ cảnh (không đại từ "nó"/"điều này"), (3) kiểm chứng độc lập. Atomic block giữ nguyên, không qua LLM.
- **HYBRID**: macro HDT + micro PROPOSITION cho section > 300 từ. Section ngắn giữ nguyên. Default fallback.

---

## 7. Tầng 6: Embedding & Vector DB

### 7.1 Vấn đề nội dung không phải văn xuôi
Embedding model chỉ hiểu ngôn ngữ tự nhiên:
| Loại | Vấn đề embed trực tiếp | Giải pháp |
|---|---|---|
| Công thức LaTeX | Tokenize vô nghĩa, vector ~ngẫu nhiên | LLM narrate LaTeX → câu mô tả |
| Bảng Markdown | Mất quan hệ hàng-cột | Linearize từng hàng → câu, hoặc tóm tắt LLM |
| Bảng/biểu đồ trong ảnh | Text model không nhìn ảnh | Embed description từ OCR |
| Hình minh họa | Text model không xử lý | Embed description từ OCR |

### 7.2 Quy trình "Narrate then Embed"
Mọi chunk trước embed đều chỉ chứa ngôn ngữ tự nhiên:
- **TEXT**: giữ nguyên, embed trực tiếp.
- **FORMULA**: LLM → câu mô tả; embed; lưu LaTeX gốc trong metadata.
- **TABLE (Markdown)**: linearize/tóm tắt → embed; lưu bảng gốc metadata.
- **TABLE/GRAPH (ảnh)**: description OCR → làm giàu nếu cần → embed.
- **IMAGE**: embed description OCR trực tiếp.

### 7.3 Metadata mỗi chunk
`strategy_used, document_type, confidence_score, structural_path, block_types, original_content (LaTeX/bảng Markdown gốc), page_number`

**Ghi chú**: `original_content` quan trọng — retrieval trả chunk, LLM đọc cả embedded text (ngữ nghĩa) lẫn original_content (số liệu chính xác từ bảng/công thức gốc).

---

## 8. Đánh giá & Thực nghiệm

### 8.1 Nguyên tắc — end-to-end
Không đánh giá chunk cô lập. Chunk tốt = khi user hỏi, retrieval tìm đúng chunk chứa câu trả lời, LLM trả lời chính xác. → đánh giá **câu hỏi → retrieval → trả lời**.

### 8.2 Ground Truth
Mỗi tài liệu 15–20 câu hỏi thiết kế có chủ đích phơi bày khác biệt strategy: nằm-gọn-1-đoạn (baseline) / cần-context-heading / liên-quan-bảng / liên-quan-công-thức / cần-tổng-hợp-nhiều-đoạn / tham-chiếu-chéo. Mỗi câu gán: (1) đáp án đúng, (2) đoạn gốc chứa câu trả lời (tính context precision/recall).

### 8.3 Thực nghiệm
- **6 cấu hình**: Baseline (fixed 512) / HDT-only / SEMANTIC-only / PROPOSITION-only / AdapChunk (đầy đủ) / AdapChunk (no cross-check).
- **Chỉ số**: Faithfulness, Context Precision, Context Recall, Answer Relevance, Strategy Selection Accuracy, Chunk Boundary Quality.
- **Quy trình**: mỗi tài liệu chạy 6 cấu hình → 6 bộ chunk → embed Qdrant riêng → cùng bộ câu hỏi → retrieval+LLM → so ground truth → RAGAS → tách theo loại câu hỏi → phân tích AdapChunk mạnh/yếu đâu, tại sao.

---

## 9. Ghi chú triển khai

### 9.1 Tech Stack gợi ý
OCR=Mistral OCR API · LLM Selector=Mistral/GPT-4o-mini/Gemini Flash · LLM Proposition=cùng/mạnh hơn · Embedding=BGE-m3 (đa ngữ) hoặc vietnamese-sbert · Vector DB=Qdrant · Framework=LangChain/LlamaIndex · Eval=RAGAS.

### 9.2 Thứ tự triển khai
1. Block Detection & Tagging (rule, test 5–10 doc mẫu)
2. Feature Extraction (rule, unit test từng đặc trưng)
3. Từng Chunking Executor (HDT → SEMANTIC → PROPOSITION → HYBRID)
4. LLM Strategy Selector + Rule Cross-check
5. Narrate then Embed + Qdrant
6. Ground Truth (câu hỏi + đáp án)
7. Evaluation + ablation studies
8. Phân tích + báo cáo

### 9.3 Lưu ý quan trọng
- Ground truth phải phơi bày điểm yếu fixed chunking (đừng để mọi câu nằm gọn 1 đoạn).
- Người tạo ground truth không nên biết hệ thống hoạt động (tránh thiên vị); hoặc LLM sinh, người review.
- Cần baseline mạnh hơn fixed (vd semantic cố định) để chứng minh lợi ích đến từ **chọn đúng** strategy, không chỉ từ strategy tốt hơn.
- Error analysis: khi AdapChunk chọn sai, sai ở bước nào (Feature Extraction hay LLM Selector)? Log đầy đủ để debug.
