# Phân tích chuyên sâu — làm sao cải thiện RAG (cơ chế + chuỗi nhân-quả + cách đo)

Dựa trên scorecard multi-agent (`RAG_SCORECARD_20260621.md`). Mỗi lever: **cơ chế kỹ
thuật → tại sao cải thiện metric → impact kỳ vọng → gate đo → effort/dependency.** Rule
#0: impact "kỳ vọng" là giả thuyết, phải A/B trên D13 mới thành SỰ THẬT.

Nguyên lý xuyên suốt: **mọi gap coverage = data/ingest-layer (0 LLM_MISS).** Nên cải
thiện = sửa cái LLM NHẬN, không phải sửa LLM. Và "fix đúng tầng" — gap retrieval thì sửa
retrieval/data, KHÔNG sửa sysprompt.

---

## LEVER 1 — stats_index extraction validation (impact CAO NHẤT, đụng cả 3 bot)

### Cơ chế hiện tại (sao noise)
`structured_ref_extractor` chạy trên MỌI chunk (bảng lẫn văn xuôi), ghi 1 row/entity vào
`document_service_index` không lọc. Hậu quả đo được: xe 26% entity ≤5 ký tự (`H/P`,`G/P`),
18% narrative (CR context prefix rò vào `entity_category`), 93% null-price, 2 dòng
`price_primary=2025122435548` (date 20251224 bị parse thành giá); legal 41% narrative.

### Cơ chế cải thiện
Thêm validation TRƯỚC khi ghi row (tại extraction, INGEST-side):
1. **Reject entity_name shape-noise**: length ∉ [6,50] → bỏ (cắt 26% short + narrative dài).
2. **Validate price**: `price_primary` phải ∈ [1.000, 500.000.000] VND → reject date-as-price.
3. **Price-gate hoặc tách index**: row không có price → KHÔNG vào price-index (hoặc tách
   `entity_index` vs `price_index`). Bỏ 93% null-price crowding → scan O(n) giảm + match sạch.

### Chuỗi nhân-quả (sao coverage tăng)
noise giảm → khi query "X giá?" stats route khớp ĐÚNG entity có price (không bị dòng rác/
null-price chen) → priced chunk vào context → LLM quote đúng. **xe price-density 7%→cao;
spa short-zone không bị rác che.**

### Gate đo
D13 spa COVERAGE 0.33→? · xe price-stability giữ · 42-q 1.00 no-reg · HALLU=0.
*Cần re-index (re-run extraction trên chunk có sẵn, KHÔNG re-embed) → rẻ.*

### Effort: M · Dependency: none · **Đây là 1 fix đụng spa + xe + legal cùng lúc.**

---

## LEVER 2 — spa zone category at extraction (đóng spa listing/zone gap)

### Cơ chế hiện tại (sao miss)
Zone triệt lông ("Mép"/"Nách"/"Mặt") có `entity_category` RỖNG + tên 3-4 ký tự. Forward-
match "triệt lông" không khớp tên zone; reverse-match bị `MIN_LEN=4` chặn ("Mép"=3). Hạ
min_len → over-match "da **mặt**" ↔ zone "Mặt" (đã test, tạo bug mới).

### Cơ chế cải thiện (đúng tầng = extraction, KHÔNG phải min_len)
Tại extraction: derive `entity_category="triệt lông"` cho zone rows từ section-context của
source chunk (chunk header "Bảng giá triệt lông theo vùng"). Khi đó:
- Listing "liệt kê triệt lông" → forward-match BY CATEGORY → trả ĐỦ zone (không cần bare-
  3-char reverse-match rủi ro).
- Single-zone "triệt lông mép" → match category + "mép" trong query → đúng.

### Chuỗi nhân-quả
category đúng → listing gom đủ sibling + single-zone không cần reverse-match → bỏ luôn
min_len trade-off (không over-match "da mặt" vì match qua category, không qua bare name).

### Gate: D13 spa d02/d04/d05 PASS · spa booking/price no-reg · HALLU=0.
### Effort: M · Dependency: cần section-context của chunk (đã có trong `<chunk_context>` CR prefix).

---

## LEVER 3 — legal clause contextual header (đóng MFA semantic gap)

### Cơ chế hiện tại (sao 4 query-lever fail)
Chunk 289 ("cấp độ 4 ... xác thực đa yếu tố khi truy cập quản trị") chỉ retrieve khi query
chứa chính chữ "truy cập quản trị máy chủ". Query generic "MFA cấp độ mấy?" embed gần các
chunk "cấp độ" chung; distractor "cấp độ 2 ... biện pháp an toàn" thắng → LLM conflate.
Bản chất: **embedding chunk không discriminate {control=MFA, threshold=4}.**

### Cơ chế cải thiện (tại U5 contextual enrichment, INGEST)
Prepend 1 dòng header VÀO CONTENT chunk (trước embed) nêu cặp control-threshold nổi bật:
`"Xác thực đa yếu tố (MFA) — bắt buộc cho hệ thống thông tin cấp độ 4 trở lên khi truy cập
quản trị."` Header này nằm trong embedding → query "MFA cấp độ mấy" cosine cao với chunk
289 → vào top-K. (Đây là contextual-retrieval Anthropic-style, ragbot ĐÃ có cơ chế U5 —
chỉ cần emit header control-aware cho legal clause.)

### Tại sao KHÔNG sửa sysprompt (đúng tầng)
Gap là retrieval (chunk không vào context). Dặn LLM "MFA là cấp độ 4" qua sysprompt = app-
inject + vẫn sai khi câu khác. Sửa ở enrichment = chunk tự retrieve được, LLM tự đọc.

### Chuỗi nhân-quả
header control-aware → embedding chunk 289 gần query MFA → vào top-K → LLM đọc "cấp độ 4"
đúng. Distractor "cấp độ 2" không còn thắng vì chunk 289 giờ rank cao.

### Gate: D13 legal d01/d05 PASS ("cấp độ 4") · legal 42-q no-reg · HALLU=0 · re-embed legal doc.
### Effort: M · Dependency: U5 enrichment (đã wired) + re-ingest legal (1 doc, rẻ).

---

## LEVER 4 — intrinsic metrics lexical→embedding-cosine (mở khóa selector)

### Cơ chế hiện tại
`shared/intrinsic_metrics.py` ICC=Jaccard, DCC=token-freq, RC=regex → composite không tin
được → selector không dám bật. Bản embedding (`score_chunks_embedding.py`) đã CÓ nhưng chỉ
offline-audit, không wire.

### Cơ chế cải thiện
Port ICC/DCC/CC sang cosine dùng Jina vectors ĐÃ có trong pgvector:
- ICC = mean cos(sentence_embed, chunk_centroid) — câu trong chunk cohesive.
- CC = cos(chunk_embed, window-3000-tok embed) — chunk hợp ngữ cảnh.
- DCC = cos(chunk_embed, doc_centroid).
(Bỏ RC coref — xem dưới.)

### Chuỗi nhân-quả (đây là "1 thay đổi đóng 2 gap")
metrics đáng tin → selector scoring đáng tin → **dám bật ekimetrics selector** → adaptive
per-doc strategy thật → chunking tối ưu hơn cho doc khó (legal nested vs catalog flat).

### Gate: composite số THẬT (so AdapChunk SC/CC) · selector A/B: D13 không regress khi bật.
### Effort: S-M · Dependency: none cho metric; selector cần thêm block-list (Lever 5).

---

## LEVER 5 — activate ekimetrics adaptive selector (sau Lever 4 + block-list)

### Cơ chế hiện tại (sao dormant)
Flag OFF + `parsed_blocks=[]` luôn rỗng (Kreuzberg trả flat-text, B1 chưa ship) → selector
chạy cũng vô dụng. AdapChunk chạy TOURNAMENT (chunk 4 cách, chấm, chọn) = 4× ingest cost.

### Cơ chế cải thiện (production-fit, KHÔNG copy tournament đắt)
1. **B1**: rewrite Kreuzberg adapter emit block-list (heading/table/atomic) thay flat-text.
2. Selector dùng metrics-embedding (Lever 4) chọn strategy per-doc — **single-inference từ
   doc-profile** (rẻ), KHÔNG tournament 4× (ragbot multi-tenant không kham 4× ingest).
3. Đo: doc legal (nested) có nên semantic-chunk? doc catalog (flat) recursive? — A/B per type.

### Tại sao single-inference không phải "thua tournament"
Tournament 4× = research-affordable, production-prohibitive. Single-inference với metrics-
đáng-tin đạt ~80% lợi ích, 25% cost → đúng trade-off cho live platform.

### Gate: per-doc-type D13 tăng khi adaptive vs fixed · ingest-cost ≤ 1.3× · HALLU=0.
### Effort: L (gồm B1) · Dependency: Lever 4.

---

## BỎ — coref/MRE (đừng đốt effort)
maverick-coref English-only + non-commercial → ragbot VN-thương-mại không dùng được. Và
corpora ragbot (bảng/điều-luật/catalog) mật độ đại-từ-coref thấp → metric value thấp. Đuổi
theo = effort cho cái không đổi UX. **Skip có chủ đích.**

---

## SEQUENCING (impact × tractability, gate D13 sẵn)
1. **Lever 1** (stats validation) — đụng 3 bot, 1 fix, re-index rẻ. **CAO NHẤT.**
2. **Lever 2** (spa category) + **Lever 3** (legal header) — đóng 2 coverage gap còn lại.
3. **Lever 4** (embedding metrics) — rẻ, mở khóa selector + đóng AdapChunk metric-gap.
4. **Lever 5** (selector + B1) — cao cấp, sau 4.
5. Capability: KG backfill, multimodal-activate (đã wired) — gated.

## Nguyên tắc đo (mọi lever)
Trước/sau qua `eval_rigor.py --compare` trên D13 (conversational, không phải factoid).
DONE = coverage↑ Wilcoxon-significant + HALLU=0 + 42-q 1.00 no-reg. Không "1-run pass".
**Đặc biệt: re-index/re-embed phải verify null_leaf=0 giữ (sacred STEP-3).**

## Câu chốt
Cải thiện RAG ragbot KHÔNG phải thêm tính năng AdapChunk — mà **(a) làm sạch data LLM
nhận (Lever 1-3 = đóng coverage thật), (b) wire cái đã build + đo để dám bật (Lever 4-5 =
đóng architecture-gap).** Mọi lever đúng tầng (data/ingest, không sysprompt), gated D13,
HALLU=0 sacred. Đó là đường "có hết + hơn" thật sự.
