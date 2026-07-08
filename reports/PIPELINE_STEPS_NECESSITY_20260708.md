# Luồng gọi step — vì sao gọi, có cần không, bỏ có mất đúng không?

> **Nguồn**: `request_steps` (load-test 200q, 2026-07-08) — thứ tự + thời gian THẬT, không đoán.
> **Nguyên tắc phân loại**: một step là **CORE** nếu bỏ → câu trả lời SAI/THIẾU. **OPTIMIZE** nếu cần về chức năng nhưng đang dùng-sai-công-cụ (chậm mà vẫn được việc). **ASYNC-ABLE** nếu không quyết định nội dung user thấy, chỉ kiểm tra/log → chạy sau được.

---

## 1. Luồng THẬT (theo thứ tự, mỗi câu chạy)

```
1  guard_input          BE   8ms     chặn prompt injection
2  cache_check          BE   0ms     tra cache L1 (câu lặp)
3  understand_query     LLM  18.1s   ← gọi AI: câu này loại gì? (MỌI câu)
3  multi_query_fanout   LLM  5.0s    ← gọi AI: sinh biến thể câu (~1/2 câu)
4  query_complexity     BE   38ms    chấm độ phức tạp
5  router_select_model  BE   1ms     chọn model
6  semantic_cache_check BE   0ms     tra cache L2 (câu tương tự)
7  adaptive_decompose   LLM  28.6s   ← gọi AI: tách "A và B" (chỉ câu phức, 47/200)
8  retrieve             BE   88ms    ← QUERY DATABASE (nhanh!)
8  rewrite              LLM  70.4s   ← gọi AI: viết lại câu (hiếm, 3/200, retry nặng)
9  rerank               BE   1.5s    xếp hạng chunk
10 rrf_fuse/filter      BE   0ms     gộp + lọc điểm thấp
11 generate             LLM  25.3s   ← gọi AI: VIẾT TRẢ LỜI (MỌI câu)
12 grade (CRAG)         BE   368ms   lọc chunk không liên quan
13 prompt_build         BE   6ms     ráp prompt
14 litm_order           BE   0ms     sắp xếp chunk (lost-in-middle)
15 citations_extract    BE   0ms     trích nguồn
16 guard_output         BE   3.5s    kiểm tra output (leak/PII)
17 persist              BE   3ms     lưu
19 grounding_check      LLM  24.9s   ← gọi AI: check answer có bịa? (factoid, 32/200, bắt user CHỜ)
```

**Tổng gọi AI/câu**: đơn giản = **2 lần** (understand + generate ≈ 43s) · phức tạp = **4-5 lần** (+ decompose + multi_query + grounding).

---

## 2. Từng LLM-step: vì sao / có cần / bỏ có mất đúng không?

| Step | Vì sao gọi | Bỏ đi có mất ĐÚNG không? | Verdict |
|---|---|---|---|
| **understand_query** (18s, MỌI câu) | Phân loại câu → route đúng (giá/so-sánh/bẫy/follow-up) | ⚠️ Bỏ hẳn → route sai (bẫy không nhận, so-sánh không tách). NHƯNG **thay bằng AI-nhẹ / luật** → **KHÔNG mất đúng** (phân loại 1 nhãn không cần AI nặng) | 🟡 **OPTIMIZE** — sai công cụ (AI nặng cho việc nhẹ) |
| **multi_query_fanout** (5s, ~1/2 câu) | Sinh biến thể câu → tìm được nhiều chunk hơn (tăng coverage) | ⚠️ Bỏ → 1 số câu miss data (coverage-miss là fail-class thật). Thay bằng **expand đồng-nghĩa/embedding** (không cần AI) cho câu đơn → giữ coverage | 🟡 **OPTIMIZE** — có kỹ thuật rẻ hơn |
| **adaptive_decompose** (28.6s, câu phức) | Tách "so sánh A và B" để tìm A, B riêng | ❌ Bỏ → so-sánh hỏng. NHƯNG **hiện đang 0/4** (chậm mà VẪN SAI) → cần **sửa prompt**, không bỏ | 🟡 **CORE-nhưng-HỎNG** — redesign, không optimize tốc độ |
| **rewrite** (70s, 3/200 hiếm) | Viết lại câu khi retry thất bại | 🔴 Đi cùng đường comparison đang fail nặng, 70s. Value đáng ngờ | 🔴 **XEM LẠI** — chậm nhất, đi cùng chỗ sai nhất |
| **generate** (25s, MỌI câu) | **SINH câu trả lời** | ❌ Bỏ → **KHÔNG có câu trả lời**. Đây là chỗ tiền ($0.56) + chất lượng thật sự | 🟢 **CORE** — giữ nguyên, chấp nhận chậm |
| **grounding_check** (25s, factoid, BẮT CHỜ) | Check answer bịa không → lý do **HALLU ≈ 0** | ✅ **KHÔNG bỏ** — nhưng **chạy SAU khi trả lời** (async, đã có code) → user KHÔNG chờ, answer vẫn được check + log. **0 mất đúng** | 🔴 **ASYNC-ABLE** — bỏ khỏi critical-path, không bỏ chức năng |

---

## 3. Các BE-step (không gọi AI): đều CORE + NHANH → giữ hết

`retrieve` (88ms, query DB) · `rerank` (1.5s) · `grade`/CRAG (368ms, lọc chunk → lý do HALLU thấp) · `guard_output` (3.5s) · guard_input/cache/rrf/mmr/litm/citations/persist (<40ms mỗi cái).

→ **Không có step BE nào là vấn đề.** DB + retrieval + rerank tổng **~2 giây**, đã tối ưu. **KHÔNG đụng** (đây là chỗ giữ 93% đúng + HALLU 0.5%).

---

## 4. KẾT LUẬN: không step nào là "rác", nhưng 3 chỗ dùng sai cách

| Ưu tiên | Việc | Bỏ có mất đúng? | Cắt được |
|---|---|---|---|
| 1 | **grounding_check → chạy async** (đã có code, bật 1 flag) | ❌ KHÔNG (vẫn chạy, chỉ sau khi trả lời) | **8-30s/câu factoid** |
| 2 | **understand_query → AI nhẹ / luật** | ❌ KHÔNG (phân loại vẫn đúng, thậm chí ổn hơn) | **~7-15s/MỌI câu** |
| 3 | **Thêm "đường tắt" cho câu đơn** (skip decompose/multi_query khi câu giá đơn) | ❌ KHÔNG (câu đơn vốn không cần các bước đó) | vài giây |
| 4 | **Redesign decompose** cho so-sánh (0/4) | ✅ đây là sửa ĐÚNG-SAI, không phải tốc độ | fix correctness |

### Điểm mấu chốt:
- **CHỖ QUYẾT ĐỊNH 93% ĐÚNG + HALLU 0.5%** = retrieve + rerank + grade + generate → **KHÔNG đụng bất kỳ cái nào**.
- Mọi cắt giảm đều ở: **việc chạy-sync-mà-nên-async** (grounding) + **AI-nặng-cho-việc-nhẹ** (understand) + **câu-dễ-chạy-full**.
- → **Giữ 100% chất lượng, chỉ làm thông minh hơn.** Không bỏ bước nào về chức năng.

*Mọi số dẫn từ `request_steps` load-test 2026-07-08. "Bỏ có mất đúng không" đánh giá theo: step đó có nằm trong chuỗi quyết-định-nội-dung (retrieve→grade→generate) hay chỉ kiểm-tra-sau (grounding) / tiền-xử-lý-thay-thế-được (understand/multi_query).*
