# DEEP-DIVE — 60 câu chinh-sach-xe (corpus mới, DB-anchored grading)

**Ngày**: 2026-07-03 · **Bot**: chinh-sach-xe (web/xe) · **Corpus**: 4 doc / 401 chunk / 242 stats-entity (172 có giá, 70 price=None) · **Grading**: Claude-judge, mọi giá đối chiếu `document_service_index` (KHÔNG chấm theo chunk-truncation).

## Kết quả tổng (60 câu)

| Nhóm | Số | % | Ghi chú |
|---|---|---|---|
| ✅ chuẩn (đúng + grounded, gồm refuse trap đúng) | 42 | 70% | 38 trả đúng + 4 refuse-đúng |
| 🔴 **sai — bịa số** | 1 | 1.7% | A-q20 Neoterra |
| 🔴 **lệch — conflation (số thật, sai brand)** | 5 | 8.3% | H-01, H-02, H-05, D-xe-multiturn-01, D-xe-price-lead-205-60-16 |
| ⚠️ deflect oan (né sang persona dù có data) | 5 | 8.3% | A-q10, A-q12, B-q06, B-q09, D-xe-list-02 |
| ⚠️ thiếu (retrieval miss đúng-size) | 3 | 5% | A-q09, A-q27, D-xe-compare-01 |
| ⚠️ chưa chuẩn (extrapolate kiến thức ngoài) | 3 | 5% | B-q03, B-q08, D-xe-fuzzy-01 |
| ⚠️ refuse oan (data có mà từ chối) | 1 | 1.7% | C-d07 |

**HALLU thật = 6/60 (10%)** — TẤT CẢ cùng 1 gốc: entity `price=None`. **Coverage mất = 9/60** (deflect 5 + thiếu 3 + refuse-oan 1).

> Sửa đính chính khi grading: H-01 (Rovelo 185/55R15→810k) và H-05 (Rovelo 205/65R15→999k/303) ban đầu bị chấm nhầm "chuẩn" (annotation bỏ sót flag); DB xác nhận `2-R15 185/55 RVL=None`, `2-R15 205/65 RVL=None` → cả 2 là conflation (copy giá Landspider).

## Định vị STEP lỗi (bug nằm ở đâu)

| Step | Số lỗi | Loại |
|---|---|---|
| **llm_generate** | **14** | 5 conflation + 1 bịa + 5 deflect + 3 extrapolate |
| **retrieve** | **4** | 3 thiếu + 1 refuse-oan (miss chunk đúng-size, kéo sibling-size) |

→ 2 điểm nghẽn: **generate** (bịa/conflate/né/thêm-ngoài) và **retrieve** (precision đúng-size).

## Gốc rễ #1 — price=None → bịa/conflate (6 HALLU)

**Chain:**
1. Stats index có 70/242 entity `price_primary=None` — chia 2 loại:
   - **7 pure-gap** (không có giá ở đâu cho size đó): Neoterra 195/65R16, LT235/75R15 WILDTRAXX, 245/75R16 LPD, 255/45ZR18 LPD, 235/65R16C, 155/65R13 RHP, 185/70R13 RHP → hỏi = **bịa** (A-q20 bịa 26.000.000đ; run trước bịa 1.250.000đ → non-deterministic).
   - **6 Rovelo** (RVL price=None nhưng Landspider cùng size CÓ giá): 185/55R15, 195/55R16, 205/60R16, 205/65R15, 195/70R14 → hỏi Rovelo = **copy giá Landspider** (H-01/H-02/H-05/multiturn/price-lead).
2. Code `query_graph.py` (~dòng 2465-2472): khi `_price is None` → render `_parts = [_name]` (chỉ TÊN, KHÔNG có nhãn "giá: chưa có"). LLM thấy 1 record authoritative (score cao) đủ field trừ giá → lấp khoảng trống (bịa) hoặc copy sibling (conflate).
3. Grounding-judge (A1) = safety net nhưng **default observe** → không chặn.

**Bằng chứng A-q20** (chunk LLM thấy):
```
Tài liệu xe-3 (177/187): | question | code | productname | ... | price | ...
| 195/65R16, 195 65 16, ... [cắt tại danh sách alias — KHÔNG có giá trị price]
→ answer: "Neoterra 195/65R16 giá 26.000.000đ, còn 26 lốp"   (1.250.000 → 999.000 → 26.000.000 mỗi run 1 số)
```
Corpus: `1.250.000`/`26.000.000` xuất hiện **0 lần**. → bịa tuyệt đối.

## Sysprompt CÓ nói "cấm bịa" chưa? — CÓ, verbatim

`bots.system_prompt` (8911 chars) ĐÃ có đúng luật user muốn:
- "CHỐNG BỊA — HALLU=0 (bất biến)" · "CHỈ xác nhận giá/tồn có LITERAL trong `<documents>`"
- "TUYỆT ĐỐI KHÔNG lấy giá/tồn của Landspider/Rovelo cùng quy cách để gán cho hãng khách hỏi" ← đúng luật chống conflation
- "Nếu quy cách CÓ trong tài liệu nhưng phần dữ liệu KHÔNG kèm con số → KHÔNG bịa số. Nói 'để em kiểm tra lại giá'" ← đúng luật cho case price=None

→ **Luật KHÔNG thiếu.** Nhưng LLM vẫn vi phạm 6/60 vì:
1. **Data render giấu chỗ thiếu**: row chỉ có tên, không có ô "price: (trống)" → LLM không nhận ra số đang thiếu để kích luật.
2. **Sysprompt là xác suất** (~90-99%), không đảm bảo 100% → cần lưới deterministic (A1) hoặc fix data-layer.

## Fix đề xuất (đúng tầng, chưa code)

1. **CODE (root, rẻ)** — `query_graph.py` stats-formatter: `price is None` → render nhãn tường minh `price: chưa cập nhật` (domain-neutral, từ language pack, zero-hardcode) → luật sysprompt "KHÔNG kèm số → KHÔNG bịa" kích deterministic. Sacred-#10-safe (format data, KHÔNG override answer).
2. **A1 net** — bật `grounding_confirmed_action=block` cho bot (đã code, đã proven chặn q20) — cần tune judge cho comparison khỏi over-refuse.
3. **INGEST (dài hạn)** — join xe-2(size,no-price) ↔ xe-3(price,code) để bỏ 51 CITYTRAXX None-price sibling; đánh dấu 6 Rovelo + 7 pure-gap là "no-price" ở nguồn.

## Gốc rễ #2 — retrieve precision (4 câu)

A-q09 (miss "Quang Minh" STK — corpus thiếu), A-q27 (235/40R18 không vào top, kéo 255/40R18), D-xe-compare-01 (kéo 195/65 thay 195/55), C-d07 (miss 185/55R15 dù score 1.0 tồn tại). → retrieval kéo sibling-size thay exact-size khi hỏi comparison/2-size.

## Gốc rễ #3 — deflect oan (5 câu) + extrapolate (3 câu)

- Deflect: A-q10/A-q12/B-q06/B-q09/D-xe-list-02 — score_max cao / có chunk nhưng LLM né sang câu persona "Em là trợ lý...". Non-deterministic (cùng câu run khác nhau: list vs né).
- Extrapolate: B-q03/B-q08/D-xe-fuzzy-01 — thêm kiến thức lốp ngoài corpus (decode H/P=High Performance, R=radial). Số grounded, chỉ "nói thêm".
