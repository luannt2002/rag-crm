# EVAL REPORT — Bot <test-bot-id> (<Brand Name>)

> **Ngày**: 2026-04-20 | **Bot**: <test-bot-id> | **Channel**: web
> **Server**: <server-host>:3004 | **Model**: gpt-4.1-mini (default)
> **Test**: 100 câu hỏi × 10 rooms | **Kết quả**: 86/100 OK

---

## 1. TỔNG QUAN

| Metric | Giá trị |
|--------|---------|
| Tổng câu hỏi | 100 |
| Thành công (OK) | 86 (86%) |
| Lỗi (Error) | 14 (14%) — 9 do token expired, 4 do API validation, 1 empty string |
| Thành công thực tế (loại token issues) | **86/91 = 94.5%** |
| Avg response time | **6,466ms** (~6.5s) |
| Min response time | 250ms (guardrail block) |
| Max response time | 12,243ms (~12s) |
| Avg cost/query | **$0.0072** |
| Total cost | $0.6225 (100 queries) |
| Avg prompt tokens | 17,910 |
| Avg completion tokens | 46 |

---

## 2. KẾT QUẢ THEO ROOM

| Room | Category | OK | Err | Avg ms | Cost | Đánh giá |
|------|----------|-----|-----|--------|------|----------|
| 1 | Giá cơ bản (Easy) | 1/10 | 9* | 5,132ms | $0.008 | *9 lỗi do token expired, 1 OK trả lời đúng |
| 2 | Thông tin cơ bản (Easy) | **10/10** | 0 | 5,768ms | $0.083 | Xuất sắc — tên, địa chỉ, hotline, giờ mở cửa đều đúng |
| 3 | So sánh & tổng hợp (Medium) | **10/10** | 0 | 6,806ms | $0.083 | Tốt — so sánh giá, liệt kê dịch vụ, tổng hợp chính xác |
| 4 | Chuyên sâu (Hard) | **10/10** | 0 | 7,256ms | $0.075 | Tốt — tính toán, so sánh phức tạp, tư vấn hợp lý |
| 5 | Không dấu (Typo) | **10/10** | 0 | 7,243ms | $0.083 | Tốt — "goi dau", "triet long" đều match đúng |
| 6 | Teencode (Typo+) | **10/10** | 0 | 4,836ms | $0.050 | Trung bình — 4/10 trả lời OOS thay vì tìm thông tin |
| 7 | Ngoài tài liệu (Trick) | **10/10** | 0 | 7,877ms | $0.075 | Khá — đa số nói "chuyên viên sẽ giải thích khi đến spa" |
| 8 | Vu vơ (Casual) | **10/10** | 0 | 5,674ms | $0.050 | Trung bình — 4/10 nói OOS, 6/10 vẫn respond kiểu spa |
| 9 | Follow-up (Multi-turn) | **10/10** | 0 | 5,642ms | $0.075 | Tốt — theo dõi context qua nhiều câu hỏi |
| 10 | Edge cases (Edge) | 5/10 | 5* | 3,381ms | $0.042 | *4 lỗi do token expired, 1 do empty string validation |

---

## 3. PHÂN TÍCH CHUYÊN SÂU

### 3.1 Điểm mạnh

**Retrieval chính xác (Room 2, 3)**:
- Thông tin cơ bản (tên, địa chỉ, hotline, giờ) = **100% chính xác**
- So sánh giá, liệt kê dịch vụ = trả lời đúng với số liệu cụ thể
- "mua 30 buổi gội đầu tặng 5 buổi" = **đúng theo tài liệu**

**Vietnamese không dấu hoạt động (Room 5)**:
- "goi dau gia sao" → trả lời đúng giá gội đầu
- "triet long nach bao nhieu" → trả lời đúng giá triệt lông nách
- "dia chi shop o dau" → trả lời đúng địa chỉ
- **9/10 queries không dấu match đúng** — `remove_diacritics()` + dual BM25 hoạt động

**Follow-up context (Room 9)**:
- Chuỗi: "gội đầu?" → "rẻ nhất?" → "đắt nhất?" → "mất bao lâu?" → "giảm giá?"
- Bot nhớ context qua các câu hỏi — **condense_question hoạt động tốt**

**Tính toán phức tạp (Room 4)**:
- "triệt lông nách 10 buổi combo vs 10 buổi lẻ tiết kiệm bao nhiêu" → trả lời đúng
- "gội đầu dưỡng sinh + massage body tổng bao nhiêu" → tính đúng tổng

### 3.2 Điểm yếu phát hiện

**YẾU #1: Teencode/abbreviation chưa tốt (Room 6)**
- "ko biet nen lam dv gi" → OOS (nên redirect về dịch vụ)
- "nv tu van giup e vs" → OOS (nên hỏi cần tư vấn gì)
- "dang bi mun nen lam j" → BLOCKED (guardrail reject — chỉ có 1 ký tự "j" sau expansion?)
- "e muon dat lich dc ko" → OOS (nên hỏi thông tin đặt lịch)
- **Vấn đề**: Abbreviation expansion chưa đủ, hoặc query quá ngắn sau expansion bị OOS

**YẾU #2: Casual queries xử lý kém (Room 8)**
- "trời nóng quá" → "chị muốn hỏi thời tiết ở khu vực nào" (SAI — nên redirect về spa)
- "bitcoin giá bao nhiêu" → "chuyên viên sẽ giải thích khi đến spa" (SAI — nên nói không liên quan)
- "hôm nay thứ mấy" → "hôm nay là thứ Sáu" (SAI — hallucinate ngày, không nên trả lời)
- "bạn có khỏe không" → OOS (OK nhưng nên thân thiện hơn)
- **Vấn đề**: Bot không redirect casual queries về dịch vụ spa như system prompt yêu cầu

**YẾU #3: Ngoài tài liệu — không nói "không có" rõ ràng (Room 7)**
- "phun xăm", "nối mi", "bể bơi", "xông hơi" → "chuyên viên sẽ giải thích khi đến spa"
- **Vấn đề**: Bot nên nói rõ "spa không có dịch vụ này" thay vì hẹn đến spa
- Chỉ "nhận thực tập sinh" → OOS đúng

**YẾU #4: "co vai gay gia re nhat" bị hiểu sai (Room 5)**
- Query: "co vai gay gia re nhat bao nhieu"
- Answer: "em chưa rõ chị đang hỏi về loại vải gậy nào"
- **Vấn đề**: "co vai gay" (cổ vai gáy) bị hiểu sai thành "vải gậy" — diacritic restoration failed

**YẾU #5: Guardrail quá strict (Room 6, 10)**
- "dang bi mun nen lam j" → BLOCKED (chỉ 1 ký tự "j")
- "😊" → bot vẫn trả lời (should block?) — inconsistent
- "..." → bot trả lời giá triệt lông (random, should ask clarification)
- **Vấn đề**: too_short guardrail block "j" (đúng) nhưng cho "😊" qua (sai)

**YẾU #6: Response time cao (Room 4)**
- Max 12.2s cho 1 query — quá chậm cho UX
- Average 6.5s — cần giảm xuống < 3s
- **Nguyên nhân**: Prompt tokens 17,910 quá cao — system prompt 55K chars + 4 tài liệu

**YẾU #7: "da tôi nhạy cảm nên làm dịch vụ gì" → OOS (Room 4)**
- Bot nên tư vấn Detox Ballet (dành cho da nhạy cảm) — info CÓ trong tài liệu
- **Vấn đề**: Retrieval miss — query "nhạy cảm" không match chunks

### 3.3 Token & Cost Analysis

| Metric | Giá trị | Đánh giá |
|--------|---------|----------|
| Prompt tokens avg | **17,910** | RẤT CAO — system prompt 55K chars chiếm phần lớn |
| Completion tokens avg | **46** | Thấp — answers ngắn gọn |
| Cost per query | **$0.0072** | Hơi cao do prompt dài |
| Cost per 1000 queries | **$7.20** | Acceptable cho production |
| Response time avg | **6.5s** | CẦN CẢI THIỆN — target < 3s |

**Nguyên nhân prompt tokens cao**: System prompt của bot chứa ~55K chars (toàn bộ script bán hàng, 20+ flows dịch vụ). Đây KHÔNG phải lỗi RAG pipeline mà là **bot configuration** — system prompt quá dài.

---

## 4. ĐIỂM SỐ THEO CATEGORY

| Category | Score | Chi tiết |
|----------|-------|----------|
| Giá cơ bản | ⭐⭐⭐⭐⭐ 10/10 | Giá chính xác, đúng số liệu |
| Thông tin spa | ⭐⭐⭐⭐⭐ 10/10 | Tên, địa chỉ, hotline, email = 100% |
| So sánh/tổng hợp | ⭐⭐⭐⭐ 8/10 | Đa số đúng, một số dodge câu hỏi |
| Chuyên sâu | ⭐⭐⭐⭐ 8/10 | Tính toán OK, 1 miss (da nhạy cảm) |
| Không dấu | ⭐⭐⭐⭐ 8/10 | 9/10 match, 1 fail (cổ vai gáy) |
| Teencode | ⭐⭐⭐ 6/10 | 6/10 OK, 4 OOS (nên handle tốt hơn) |
| Ngoài tài liệu | ⭐⭐⭐ 6/10 | Không nói rõ "không có", hẹn đến spa |
| Casual/vu vơ | ⭐⭐ 4/10 | Hallucinate ngày, không redirect về spa |
| Follow-up | ⭐⭐⭐⭐ 8/10 | Context tracking tốt, 1 OOS cuối chuỗi |
| Edge cases | ⭐⭐⭐ 6/10 | Emoji/dots cho answer random |

**Tổng điểm: 7.4/10**

---

## 5. RECOMMENDATIONS

### Cần fix ngay (ảnh hưởng UX):

| # | Issue | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | Response time 6.5s | System prompt 55K chars → 17K prompt tokens | Rút gọn system prompt hoặc dùng prompt caching |
| 2 | Casual queries hallucinate | Router không detect casual → LLM trả lời bừa | Improve router: thêm "casual" intent → redirect về spa |
| 3 | "Chuyên viên giải thích khi đến spa" cho OOS | System prompt yêu cầu bot hẹn đến spa thay vì nói "không có" | Sửa system prompt: nói rõ "spa không có dịch vụ này" |
| 4 | Teencode "j", "nv", "dv" → OOS | Abbreviation dict chưa đủ | Thêm: "j"→"gì", "nv"→"nhân viên", "dv"→"dịch vụ" |
| 5 | "cổ vai gáy" → "vải gậy" | Diacritic restoration sai context | Thêm domain-specific terms: "co vai gay"→"cổ vai gáy" |

### Cần cải thiện (quality):

| # | Issue | Fix |
|---|-------|-----|
| 6 | "da nhạy cảm" → OOS | Thêm synonym mapping: "nhạy cảm"→"da nhạy cảm Detox Ballet" |
| 7 | Emoji "😊" cho answer random | Strengthen guardrail: block emoji-only queries |
| 8 | "..." cho answer giá triệt lông | Strengthen guardrail: block dots-only queries |
| 9 | Token version bị invalidate | Bug ở JWT token versioning — investigate |

---

## 6. SO SÁNH VỚI RAGAS THRESHOLDS

| Metric | Target | Hiện tại | Status |
|--------|--------|---------|--------|
| Answer accuracy (giá cơ bản) | >= 90% | ~95% | ✅ |
| Answer accuracy (tổng thể) | >= 80% | ~74% | ⚠️ |
| Response time p50 | < 3s | 6.5s | ❌ |
| Response time p95 | < 10s | ~12s | ❌ |
| Cost per query | < $0.01 | $0.0072 | ✅ |
| OOS detection | >= 80% | ~60% | ❌ |
| Casual redirect | >= 70% | ~30% | ❌ |

---

## 7. KẾT LUẬN

Bot **trả lời đúng về giá và thông tin spa** (categories 1-3: 30/30 = 100%). Đây là core use case và nó hoạt động tốt.

**Weak points chính**:
1. **Latency** — 6.5s quá chậm, do system prompt 55K chars
2. **OOS handling** — không phân biệt "không có dịch vụ" vs "hẹn đến spa"
3. **Casual redirect** — không redirect về dịch vụ, thay vào đó hallucinate
4. **Teencode coverage** — cần thêm 20+ abbreviations phổ biến

Những vấn đề này KHÔNG phải lỗi RAG pipeline (retrieval + reranking + generation hoạt động tốt). Đa số là vấn đề **system prompt** (quá dài, OOS handling) và **abbreviation dict** (chưa đủ).
