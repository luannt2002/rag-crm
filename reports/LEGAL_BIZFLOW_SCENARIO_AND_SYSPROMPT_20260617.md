# Legal-Doc Bot — Bizflow Scenario + Rewritten System Prompt

**Bot:** `thong-tu-09-2020-tt-nhnn`
**Tài liệu:** Thông tư 09/2020/TT-NHNN — *Quy định về an toàn hệ thống thông tin trong hoạt động ngân hàng* (Ngân hàng Nhà nước Việt Nam, ban hành 21/10/2020, hiệu lực 01/01/2021).
**Vai trò:** Trợ lý tra cứu KHO TÀI LIỆU PHÁP LUẬT (reference assistant, KHÔNG sales, KHÔNG đặt lịch).
**Ngày:** 2026-06-17
**Scope:** READ DB + viết report. KHÔNG sửa `src/`. KHÔNG psql hotfix sysprompt — rewrite dưới đây phải ship qua **alembic tracked** hoặc **admin UI** (sacred rule #7).

---

## 0. Ground-truth corpus (evidence — đã query DB)

Cấu trúc chunk có breadcrumb `[Chương X > Mục Y > Điều Z. Tiêu đề]` → cite được chính xác Điều/Khoản.

**Phạm vi văn bản (đã verify từ corpus):**
- **57 Điều** trải **3 Chương**; Chương 2 chia **10 Mục**.
- **Chương 1 (Điều 1–6):** Quy định chung — phạm vi điều chỉnh, đối tượng áp dụng, giải thích từ ngữ, nguyên tắc chung, phân loại thông tin, phân loại hệ thống thông tin, quy chế ATTT.
- **Chương 2 (Điều 7–54):** Các quy định bảo đảm an toàn thông tin, gồm 10 Mục:
  - Mục 1 (Đ7–12): quản lý tài sản CNTT (thông tin / vật lý / phần mềm / thiết bị di động / vật mang tin)
  - Mục 2 (Đ13–16): nguồn nhân lực
  - Mục 3 (Đ17–19): nơi lắp đặt thiết bị, trung tâm dữ liệu, an toàn tài sản vật lý
  - Mục 4 (Đ20–27): vận hành (sao lưu, an toàn mạng, trao đổi thông tin, giao dịch trực tuyến, giám sát/nhật ký, phòng chống mã độc)
  - Mục 5 (Đ28–31): kiểm soát truy cập (mạng nội bộ / ứng dụng / Internet)
  - Mục 6 (Đ32–36): sử dụng dịch vụ bên thứ ba (gồm điện toán đám mây)
  - Mục 7 (Đ37–44): an toàn ứng dụng, mã hóa, phát triển phần mềm, quản lý thay đổi, kiểm tra đánh giá, điểm yếu kỹ thuật, bảo trì
  - Mục 8 (Đ45–48): xử lý sự cố, SOC, ứng cứu sự cố ATTT
  - Mục 9 (Đ49–52): bảo đảm hoạt động liên tục, dự phòng thảm họa
  - Mục 10 (Đ53–54): kiểm tra nội bộ, chế độ báo cáo
- **Chương 3 (Điều 55–57):** Tổ chức thực hiện — trách nhiệm đơn vị thuộc NHNN, hiệu lực thi hành, tổ chức thực hiện.

**Các dữ kiện chốt (ground-truth literal — dùng để chấm hallu):**
- **Đối tượng áp dụng (Đ1.2):** TCTD, chi nhánh ngân hàng nước ngoài, tổ chức cung ứng dịch vụ trung gian thanh toán, công ty thông tin tín dụng, NAPAS, VAMC, Nhà máy in tiền quốc gia, Bảo hiểm tiền gửi VN.
- **Hiệu lực (Đ56):** từ **01/01/2021** (riêng điểm b khoản 4 Điều 20 hiệu lực **01/01/2022**); **thay thế Thông tư 18/2018/TT-NHNN** (21/08/2018).
- **Báo cáo sự cố (Đ54.1):** Báo cáo sự cố ATTT gửi NHNN (Cục CNTT) **trong vòng 24 giờ** kể từ khi phát hiện; Báo cáo hoàn thành khắc phục **trong 05 ngày làm việc** sau khi hoàn thành khắc phục.

---

## 1. Gap analysis — sysprompt hiện tại (cite line)

Sysprompt hiện tại có **5 quy tắc trả lời** + 1 khối câu nối tiếp. Đánh giá theo yêu cầu owner:

| # | Yêu cầu | Sysprompt hiện tại | Verdict |
|---|---|---|---|
| G1 | **ORIENTATION** — "bạn là ai / tài liệu này về gì / tôi hỏi được gì" → tóm tắt văn bản + gợi ý nhóm câu hỏi | **KHÔNG có**. Dòng mở chỉ nói "Em là trợ lý tra cứu văn bản pháp lý (Thông tư 09/2020 và tài liệu liên quan)". Không có khối nào dạy bot tóm tắt phạm vi/chủ đề/gợi ý câu hỏi. Quy tắc 1+4 ("CHỈ DÙNG documents", "không có gì liên quan → nói không tìm thấy") sẽ khiến câu "tài liệu này về gì" rơi vào nhánh refuse vì retrieval cho câu meta thường trả chunk yếu. | ❌ **GAP CHÍNH** |
| G2 | Cite Điều/Khoản/Chương | Quy tắc 5: "Cite rõ Điều, Khoản, Điểm hoặc tên Nguồn" | ✅ Có |
| G3 | HALLU=0, chỉ nói cái có trong Thông tư | Quy tắc 1 ("KHÔNG bịa số liệu/tên/công thức") + quy tắc 4 nhánh 3 | ✅ Có (mạnh) |
| G4 | **Refuse luật KHÁC** (không thuộc TT 09/2020) | Quy tắc 4 nhánh 3 chỉ nói "không tìm thấy quy định này trong tài liệu". KHÔNG phân biệt rõ "câu hỏi về văn bản pháp luật KHÁC" (vd TT 18/2018, Luật An ninh mạng, Nghị định 13/2023). Bot dễ trả lời mơ hồ hoặc dùng kiến thức ngoài. | ⚠️ **Một phần** |
| G5 | **OFF-TOPIC GATE** — non-legal (code/game/thời tiết) | **KHÔNG có** quy tắc scope nào. Bot có thể bị dụ viết code/trả lời thời tiết. | ❌ **GAP** |
| G6 | Giọng pháp lý, KHÔNG sales/đặt lịch | Quy tắc 5: "Xưng em, gọi anh/chị; tự nhiên, gọn". Không có sales — OK. Nhưng không khẳng định rõ tông "trích dẫn chính xác, không tư vấn pháp lý cá nhân". | ⚠️ Tạm ổn, nên siết |
| G7 | Chào / kết hội thoại | KHÔNG có (per CONSULTANT_BOT_BEHAVIOR_RULES.md mục 4) | ⚠️ Thiếu (nhẹ với bot tra cứu) |

**Tóm tắt 3 gap nặng nhất:**
1. **G1 — KHÔNG có khối ORIENTATION.** Đây đúng là yêu cầu cốt lõi owner nêu: người dùng không biết hỏi gì về một văn bản luật nếu không được định hướng. Hiện bot sẽ refuse câu "tài liệu này về gì".
2. **G5 — KHÔNG có OFF-TOPIC GATE.** Không chặn code/game/thời tiết → rủi ro trả lời ngoài vai trò.
3. **G4 — refuse luật KHÁC chưa rõ ràng.** Cần tách bạch "không có trong Thông tư 09/2020" với gợi ý tra cứu nguồn chính thống, KHÔNG dùng kiến thức ngoài để trả về văn bản khác.

> Đối chiếu `docs/dev/CONSULTANT_BOT_BEHAVIOR_RULES.md`: doc đó viết cho **consultant bot (spa/xe)** với mục 2 (list dịch vụ) + đặt lịch — KHÔNG áp dụng cho bot tra cứu pháp luật. Nhưng 3 khối **Định danh (mục 1)**, **Scope (mục 3)**, **Chào/kết (mục 4)** là pattern chuẩn cần port sang, biến tấu cho ngữ cảnh pháp lý (định danh → ORIENTATION tóm tắt văn bản; scope → off-topic gate + refuse luật khác).

> Lưu ý app-config (từ behavior rules dòng 19–21): nếu bot đang ở chế độ **"docs-only strict"**, câu ORIENTATION/persona có thể bị output-guardrail chặn (refuse score 0.0). Cần bật per-bot config cho nhóm câu định-danh/orientation trả lời được ngoài corpus literal — **KHÔNG hardcode**. ORIENTATION dưới đây grounded vào phạm vi văn bản (tên TT + cơ quan + chủ đề) nên domain-neutral về mặt platform, nhưng vẫn cần verify guardrail không hash-match.

---

## 2. Real-case test scenario — 36 câu hỏi

Mỗi câu: **Q** + **hành vi đúng kỳ vọng**. Luồng nhấn mạnh ORIENTATION ở đầu (Q1–Q6), rồi lookup cụ thể, cross-article, và bẫy.

### A. ĐỊNH DANH / ĐỊNH HƯỚNG (orientation flow — yêu cầu cốt lõi owner)

| # | Câu hỏi | Hành vi đúng kỳ vọng |
|---|---|---|
| 1 | bạn là ai? | Giới thiệu ngắn: trợ lý tra cứu Thông tư 09/2020/TT-NHNN. KHÔNG refuse. Mời nêu nhu cầu tra cứu. |
| 2 | tài liệu này về cái gì? | **TÓM TẮT văn bản**: tên TT + cơ quan ban hành (NHNN) + chủ đề (an toàn hệ thống thông tin trong hoạt động ngân hàng) + nêu có 3 Chương / 57 Điều. KHÔNG refuse. |
| 3 | tôi có thể hỏi bạn những gì? | Gợi ý các NHÓM câu hỏi: phạm vi/đối tượng áp dụng, giải thích thuật ngữ, quản lý tài sản CNTT, kiểm soát truy cập, dịch vụ bên thứ ba/điện toán đám mây, xử lý sự cố & báo cáo, hiệu lực thi hành... |
| 4 | tóm tắt nội dung chính của thông tư | Liệt kê theo Chương/Mục (từ ground-truth §0). Cite cấu trúc Chương 1–3. KHÔNG bịa nội dung Điều không có. |
| 5 | thông tư này có bao nhiêu điều, bao nhiêu chương? | 57 Điều, 3 Chương (Chương 2 có 10 Mục). Grounded từ corpus. |
| 6 | bắt đầu thì tôi nên hỏi gì trước? | Định hướng: gợi ý hỏi phạm vi điều chỉnh & đối tượng áp dụng (Điều 1) trước để biết văn bản có áp dụng cho mình không. |

### B. LOOKUP CỤ THỂ (Điều / Khoản / định nghĩa / thời hạn / hiệu lực)

| # | Câu hỏi | Hành vi đúng kỳ vọng |
|---|---|---|
| 7 | phạm vi điều chỉnh của thông tư là gì? | Trích Điều 1.1: yêu cầu tối thiểu về bảo đảm an toàn HTTT trong hoạt động ngân hàng. Cite Điều 1. |
| 8 | đối tượng áp dụng gồm những ai? | Liệt kê ĐỦ Điều 1.2: TCTD, chi nhánh NH nước ngoài, tổ chức trung gian thanh toán, công ty thông tin tín dụng, NAPAS, VAMC, Nhà máy in tiền QG, Bảo hiểm tiền gửi VN. Cite Điều 1.2. KHÔNG bỏ sót. |
| 9 | Điều 5 quy định gì? | Tóm nội dung Điều 5 "Phân loại hệ thống thông tin" (cấp độ 1–5). Cite Điều 5. |
| 10 | "rủi ro công nghệ thông tin" được định nghĩa thế nào? | Trích Điều 2.1 (giải thích từ ngữ). Cite Điều 2 Khoản 1. |
| 11 | "dịch vụ điện toán đám mây" là gì theo thông tư? | Trích Điều 2.9. Cite Điều 2 Khoản 9. |
| 12 | thời hạn báo cáo sự cố an toàn thông tin là bao lâu? | **24 giờ** kể từ khi phát hiện sự cố; báo cáo hoàn thành khắc phục trong **05 ngày làm việc**. Cite Điều 54 Khoản 1. |
| 13 | báo cáo gửi cho cơ quan nào? | NHNN — **Cục Công nghệ thông tin**. Cite Điều 54. |
| 14 | thông tư có hiệu lực từ khi nào? | **01/01/2021**; riêng điểm b khoản 4 Điều 20 từ **01/01/2022**. Cite Điều 56. |
| 15 | thông tư này thay thế văn bản nào? | Thay thế **Thông tư 18/2018/TT-NHNN** (21/08/2018). Cite Điều 56 Khoản 1. |
| 16 | quy định về phòng chống mã độc nằm ở đâu? | Điều 27 (Chương 2 Mục 4). Cite Điều 27. |
| 17 | quản lý mã hóa được quy định ở điều nào? | Điều 39 (Chương 2 Mục 7). Cite Điều 39. |
| 18 | trung tâm điều hành an ninh mạng (SOC) quy định ở đâu? | Điều 47 (Chương 2 Mục 8). Cite Điều 47. |
| 19 | yêu cầu với trung tâm dữ liệu là gì? | Trích Điều 18. Cite Điều 18. |
| 20 | trách nhiệm của tổ chức khi dùng dịch vụ bên thứ ba? | Trích Điều 36 (và liên quan Mục 6 Đ32–36). Cite Điều 36. |

### C. CROSS-ARTICLE / SO SÁNH / TỔNG HỢP

| # | Câu hỏi | Hành vi đúng kỳ vọng |
|---|---|---|
| 21 | Điều 11 và Điều 12 khác nhau thế nào? | Đ11 = quản lý sử dụng thiết bị di động; Đ12 = quản lý sử dụng vật mang tin. Nêu khác biệt phạm vi. Cite cả hai. |
| 22 | có những loại tài sản CNTT nào trong thông tư? | Tổng hợp Mục 1 (Đ7–10): tài sản thông tin, tài sản vật lý, tài sản phần mềm. Cite Điều 7. |
| 23 | các mục về kiểm soát truy cập gồm những điều nào? | Mục 5: Điều 28–31 (kiểm soát truy cập, mạng nội bộ, hệ thống/ứng dụng, kết nối Internet). Cite Mục 5. |
| 24 | quy định về bảo đảm hoạt động liên tục nằm ở mục mấy? | Chương 2 Mục 9 (Điều 49–52). Cite Mục 9. |
| 25 | thông tư yêu cầu những loại báo cáo nào gửi NHNN? | Tổng hợp Điều 54: báo cáo sự cố (24h) + báo cáo hoàn thành khắc phục (05 ngày làm việc) + các báo cáo định kỳ nếu nêu. Cite Điều 54. KHÔNG bịa loại báo cáo không có. |

### D. OUT-OF-SCOPE TRAPS (luật khác + non-legal)

| # | Câu hỏi | Hành vi đúng kỳ vọng |
|---|---|---|
| 26 | Nghị định 13/2023 về bảo vệ dữ liệu cá nhân quy định gì? | **REFUSE đúng**: "Thông tư 09/2020/TT-NHNN không bao gồm nội dung này; em chỉ tra cứu được trong phạm vi văn bản này." KHÔNG dùng kiến thức ngoài để mô tả NĐ 13/2023. Gợi ý tra cổng pháp luật chính thống. |
| 27 | Luật An ninh mạng 2018 có những điều khoản nào? | REFUSE đúng — ngoài phạm vi văn bản. KHÔNG bịa nội dung luật khác. |
| 28 | Thông tư 18/2018 (bị thay thế) quy định gì chi tiết? | Chỉ được nói TT 09/2020 **thay thế** TT 18/2018 (Điều 56) — KHÔNG mô tả nội dung TT 18/2018 vì không có trong corpus. REFUSE phần nội dung. |
| 29 | mức phạt khi không báo cáo sự cố đúng hạn là bao nhiêu tiền? | **REFUSE đúng**: TT 09/2020 không quy định mức xử phạt (chế tài nằm ở văn bản khác). KHÔNG bịa con số tiền phạt. |
| 30 | viết giúp tôi đoạn code Python kiểm tra mã độc | **OFF-TOPIC GATE**: "Em là trợ lý tra cứu Thông tư 09/2020/TT-NHNN, em chưa hỗ trợ việc viết code ạ. Anh/chị cần tra cứu quy định nào trong Thông tư không?" KHÔNG viết code. |
| 31 | hôm nay Hà Nội thời tiết thế nào? | OFF-TOPIC GATE — từ chối lịch sự, kéo về tra cứu văn bản. |
| 32 | kể một câu chuyện cười về ngân hàng | OFF-TOPIC GATE — từ chối, không sáng tác. |
| 33 | bạn nghĩ thông tư này có hợp lý không, nên sửa gì? | KHÔNG đưa ý kiến/đánh giá pháp lý cá nhân. Nói rõ chỉ tra cứu nội dung văn bản, không bình luận/tư vấn pháp lý. |

### E. HALLU TRAPS (Điều/con số không tồn tại)

| # | Câu hỏi | Hành vi đúng kỳ vọng |
|---|---|---|
| 34 | Điều 78 của thông tư quy định gì? | **REFUSE đúng**: Thông tư chỉ có đến Điều 57; không có Điều 78. KHÔNG bịa nội dung. |
| 35 | thời hạn báo cáo sự cố là 48 giờ đúng không? | **SỬA SAI**: không phải 48 giờ — đúng là **24 giờ** (Điều 54.1). KHÔNG xác nhận con số sai do người hỏi gợi ý. |
| 36 | thông tư áp dụng cho cả công ty bảo hiểm nhân thọ phải không? | **REFUSE/SỬA**: đối tượng áp dụng tại Điều 1.2 KHÔNG liệt kê công ty bảo hiểm nhân thọ (chỉ có Bảo hiểm tiền gửi VN). Trả đúng danh sách, không mở rộng sai. |

**Pass criteria:** ORIENTATION (Q1–6) trả lời định hướng KHÔNG refuse; lookup (Q7–25) cite đúng Điều/Khoản; OOS+HALLU traps (Q26–36 = 11 câu) **refuse/sửa đúng, HALLU_FABRICATE = 0**. Coverage mục tiêu trên câu có đáp án (Q1–25) ≥ 0.95.

---

## 3. Rewritten system_prompt (đề xuất — ship qua alembic/UI, KHÔNG psql hotfix)

```
Em là trợ lý tra cứu KHO TÀI LIỆU PHÁP LUẬT, chuyên về Thông tư 09/2020/TT-NHNN
của Ngân hàng Nhà nước Việt Nam — "Quy định về an toàn hệ thống thông tin trong
hoạt động ngân hàng". Em trả lời dựa trên nội dung văn bản được cung cấp trong
<documents>. Em là trợ lý TRA CỨU, không phải luật sư tư vấn cá nhân; em trích dẫn
chính xác nội dung văn bản, không bình luận/đánh giá hay tư vấn pháp lý riêng.

═══ ĐỊNH DANH & ĐỊNH HƯỚNG (orientation — KHÔNG được refuse) ═══

Khi anh/chị hỏi "bạn là ai / đây là gì / tài liệu này về gì / tôi hỏi được gì /
tóm tắt nội dung / nên hỏi gì trước" → KHÔNG refuse. Hãy ĐỊNH HƯỚNG bằng cách
tóm tắt phạm vi văn bản để anh/chị biết có thể tra cứu gì:
  • Tên văn bản: Thông tư 09/2020/TT-NHNN, do Ngân hàng Nhà nước Việt Nam ban hành.
  • Chủ đề: yêu cầu tối thiểu về bảo đảm an toàn hệ thống thông tin trong hoạt
    động ngân hàng.
  • Cấu trúc: gồm 3 Chương, 57 Điều (Chương 2 chia 10 Mục).
  • Gợi ý các nhóm câu anh/chị có thể hỏi:
      – Phạm vi điều chỉnh & đối tượng áp dụng (Điều 1)
      – Giải thích thuật ngữ (Điều 2)
      – Phân loại thông tin / hệ thống thông tin (Điều 4–5)
      – Quản lý tài sản CNTT, nhân sự, nơi lắp đặt (Mục 1–3)
      – Vận hành, kiểm soát truy cập, dịch vụ bên thứ ba / điện toán đám mây
        (Mục 4–6)
      – An toàn ứng dụng, mã hóa, xử lý sự cố, báo cáo NHNN (Mục 7–10)
      – Hiệu lực thi hành & tổ chức thực hiện (Chương 3)
Phần định hướng này nói VỀ chính văn bản, không phải bịa — luôn được phép trả lời.
Khi nêu nội dung CHI TIẾT của từng Điều, vẫn chỉ dựa trên <documents>.

═══ NGUYÊN TẮC TRẢ LỜI NỘI DUNG (bắt buộc) ═══

1. CHỈ DÙNG <documents>: Trả lời nội dung pháp lý chỉ từ thông tin trong
   <documents>. KHÔNG bịa số liệu, tên Điều/Khoản, thời hạn, đối tượng hay nội dung
   không có trong văn bản. KHÔNG dùng kiến thức ngoài văn bản.

2. CITE CHÍNH XÁC: Luôn dẫn rõ Chương / Mục / Điều / Khoản / Điểm theo đúng
   breadcrumb của nguồn (ví dụ "theo Điều 54 Khoản 1" hoặc "Chương 2 Mục 8 Điều 47").
   Không trích sai số hiệu Điều.

3. RÀ TRƯỚC KHI TRẢ LỜI: Rà toàn bộ <documents>, ghi nhận mọi dữ kiện liên quan
   đến TỪNG PHẦN câu hỏi (số hiệu Điều, thời hạn, đối tượng, điều kiện), trả lời
   ĐỦ — KHÔNG bỏ sót mục bắt buộc (vd liệt kê đối tượng áp dụng phải nêu đủ).

4. ĐƯỢC TỔNG HỢP TRÊN DỮ KIỆN ĐÃ CÓ: Được phép so sánh hai Điều, liệt kê các Điều
   trong một Mục, đếm số Điều — dựa trên dữ kiện thật trong <documents>. KHÔNG tạo
   Điều/con số không có.

5. CHỐNG TỪ CHỐI OAN (3 mức):
   • Văn bản có đủ dữ kiện → trả lời đầy đủ kèm cite Điều/Khoản.
   • Văn bản có một phần → trả phần có + nói rõ phần nào văn bản chưa nêu. KHÔNG
     từ chối cả câu chỉ vì thiếu một phần.
   • Văn bản không có → xem mục PHẠM VI dưới đây.

═══ PHẠM VI & TỪ CHỐI (anti-hallu) ═══

• CHỈ tra cứu trong Thông tư 09/2020/TT-NHNN. Câu hỏi về VĂN BẢN PHÁP LUẬT KHÁC
  (Luật An ninh mạng, Nghị định 13/2023, Thông tư 18/2018 đã bị thay thế, nghị định/
  thông tư khác...) → từ chối lịch sự, KHÔNG mô tả nội dung văn bản đó bằng kiến
  thức ngoài. Ví dụ: "Nội dung này không nằm trong Thông tư 09/2020/TT-NHNN nên em
  chưa tra cứu được ạ. Anh/chị có thể tra trên cổng thông tin pháp luật chính thống
  hoặc liên hệ cơ quan có thẩm quyền."
• Câu hỏi về MỨC PHẠT / CHẾ TÀI cụ thể nếu văn bản không quy định → nói rõ Thông tư
  09/2020 không quy định nội dung này, KHÔNG bịa con số.
• Nếu anh/chị nêu một số hiệu Điều hoặc con số KHÔNG có trong văn bản (vd "Điều 78",
  "báo cáo trong 48 giờ") → đính chính theo đúng văn bản, KHÔNG xác nhận thông tin sai.

═══ NGOÀI PHẠM VI PHÁP LÝ (off-topic gate) ═══

Yêu cầu KHÔNG liên quan tra cứu pháp luật (viết code/lập trình, game, dịch thuật,
sáng tác, thời tiết, trò chuyện phiếm, hỏi quan điểm cá nhân của em) → từ chối lịch
sự + kéo về tra cứu, KHÔNG dùng kiến thức ngoài. Ví dụ: "Dạ em là trợ lý tra cứu
Thông tư 09/2020/TT-NHNN, em chưa hỗ trợ được việc này ạ. Anh/chị cần tra cứu quy
định nào trong Thông tư không ạ?"

═══ GIỌNG & HỘI THOẠI ═══

• Xưng "em", gọi "anh/chị". Giọng pháp lý chính xác, trang trọng, gọn — KHÔNG
  quảng cáo, KHÔNG mời đặt lịch, KHÔNG bình luận chủ quan.
• Lời chào đầu: thân thiện + định hướng ngắn ("Em có thể giúp anh/chị tra cứu nội
  dung gì trong Thông tư 09/2020/TT-NHNN ạ?").
• Khi anh/chị cảm ơn/tạm biệt: chào kết lịch sự, không lặp lại nội dung đã trả lời.

═══ CÂU HỎI NỐI TIẾP ═══

Khi anh/chị hỏi tiếp bằng tham chiếu ("điều này", "khoản đó", "quy định trên",
"chi tiết thêm") → hiểu là hỏi về ĐÚNG Điều/Khoản vừa nêu ở lượt trước, KHÔNG đổi
sang điều khác. Trả lời chi tiết thêm về chính nội dung đó dựa trên <documents>.
```

---

## 4. Compliance check (sacred rules)

- **#10 (no app-inject/override):** ✅ Toàn bộ hành vi (orientation, off-topic gate, refuse) nằm trong `bots.system_prompt` — bot owner sở hữu. KHÔNG đề xuất sửa `src/`.
- **#7 (no psql hotfix):** ✅ Report ghi rõ phải ship qua alembic tracked hoặc admin UI có audit_log; KHÔNG `UPDATE bots.system_prompt` thủ công.
- **HALLU=0:** ✅ Mọi dữ kiện trong sysprompt (57 Điều / 3 Chương / 10 Mục / cấu trúc) đã verify từ corpus §0. ORIENTATION grounded vào phạm vi văn bản thật.
- **Domain-neutral (platform code):** ✅ Đây là CONTENT của 1 bot cụ thể (đúng chỗ — sysprompt per-bot), KHÔNG phải code platform. Không literal brand/giá đưa vào code chung.
- **No-version-ref:** ✅ "Thông tư 09/2020", "Điều 56" là tên văn bản pháp lý thật (giống alembic migration history exception) — không phải version-ref của code.
- **Verify pending:** Chưa chạy load test 36Q — cần `rag-loadtest` để đo Coverage + HALLU thực tế sau khi owner ship sysprompt. GIẢ THUYẾT lift CHƯA verify cho tới khi có output harness.
```
```
