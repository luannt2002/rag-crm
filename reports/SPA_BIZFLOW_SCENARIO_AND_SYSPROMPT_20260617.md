# SPA Bot (`test-spa-id` — Dr. Medispa) — Business-Flow Scenario + Gap Analysis + Rewritten Sysprompt

**Date:** 2026-06-17
**Bot:** `test-spa-id` (Dr. Medispa thẩm mỹ viện)
**ROLE target:** nhân viên CSKH + tư vấn viên + thu thập thông tin → chốt đơn / đặt lịch.
**Scope:** READ DB + report only (no `src/` edit). Sysprompt below is a DRAFT to ship via alembic/admin-UI (not psql hotfix) — per CLAUDE.md sacred rule #7 and sacred rule #10 (behavior lives in `bots.system_prompt`, app never injects/overrides).

---

## 0. Corpus ground-truth (what the bot actually has)

Sampled from `document_chunks` for this bot. Service groups present in `<documents>`:

| Group | Sample services (literal names in corpus) | Price evidence |
|---|---|---|
| **Chăm sóc da (CSD)** | CSD Chuyên sâu, CSD Cấp oxi tươi, CSD Thải độc da, CSD Cấp nước đa tầng, CSD Nano kim cương, CSD Nâng cơ 7 điểm vàng, CSD Dưỡng sinh mắt | 700k / 800k / 1.500k (giá lẻ); ưu đãi 199k/299k |
| **Trị mụn / Peel** | Trị mụn chuyên sâu, Peel trị thâm Inno A, Peel điều trị mụn chuyên sâu, Detox Ballet | 700k / 2.500k; Detox Ballet 699k (gốc 2tr) |
| **Trẻ hóa** | Trẻ hóa IPL, Laser Carbon, Meso căng bóng trẻ hóa, Vikim trẻ hóa, Vikim Medic, Peel Tretinoin, Peel Ribo, Nano collagen trẻ hóa da, Hydra Ballet | 1.200k → 3.000k |
| **Triệt lông** | Triệt lông theo vùng (giá theo vùng cụ thể) | per-vùng |
| **Massage / dưỡng sinh** | Massage cổ vai gáy 60p (400k) / 90p (500k), Massage body 60p (600k), Massage chân 70p (350k), Gội đầu dưỡng sinh, Ấm nóng cổ tử cung, Chuyển hóa bụng 60p (800k) | as listed |
| **Tẩy da chết body (2 biến thể)** | **Tẩy đa chết body** 30p (450.000) · **Tẩy da chết & ủ trắng body** 60p (550.000) | both literal in corpus |
| **Info** | Giờ mở cửa 9-21h T2-CN, Google Maps link, ưu đãi khách mới, AI soi da 17 chỉ số | literal |

This corpus is the ONLY source-of-truth for the test below. Anything not in the table = HALLU trap.

---

## 1. GAP ANALYSIS — current sysprompt (line refs into `/tmp/spa_sysprompt.txt`, 55 logical lines)

The current sysprompt is a 7-block prompt. It is strong on anti-hallucination but has **two role-breaking internal contradictions** plus several smaller gaps.

### GAP A — "tư vấn về <nhóm>" (da / massage) → bot hỏi lại thay vì LIST nhóm (RULE CONFLICT)

Three blocks fight each other on the exact same trigger:

- **Line 19-20 "HỎI CHUNG CHUNG"**: `"chăm sóc da", "massage", "bên em có gì" → hỏi lại nhu cầu ĐÚNG 1 LẦN, KHÔNG liệt kê dịch vụ, KHÔNG báo giá.`
- **Line 49-54 "TƯ VẤN → HIỆN DANH SÁCH"**: `"tôi cần tư vấn", "có dịch vụ gì", "cho xem dịch vụ" → liệt kê các dịch vụ.`
- **Line 16-17 "NHIỀU BIẾN THỂ"**: only lists when the name matches **multiple variants of the same service** — does NOT cover a category umbrella.

**Conflict:** "tư vấn về da" / "tư vấn chăm sóc da" matches the **HỎI CHUNG CHUNG** trigger (`"chăm sóc da"`) which says *KHÔNG liệt kê* → bot hỏi lại. But the business intent (and `CONSULTANT_BOT_BEHAVIOR_RULES.md §2`) wants a **LIST of that group's services**. The corpus even has the owner's own answer for `"Tư vấn mình chăm sóc da / có những dịch vụ chăm sóc da nào?"` → which the corpus models as *"hỏi lại tình trạng da"*. So the prompt and corpus both push ask-back, but the task requirement (and good consult UX) wants list-all for a **named category**. Result: customer says "tư vấn về da", expects a menu of skin services, gets a generic "chị muốn cải thiện vấn đề gì?" — feels evasive.

There is no rule that distinguishes **"named category" ("tư vấn về DA")** — should LIST — from **"truly vague" ("bên em có gì")** — may ask back once. Both fall into the same HỎI CHUNG CHUNG bucket.

### GAP B — OUT-OF-SCOPE (code / game / math / weather / competitor) NOT refused — booking-push override

- **Line 40-41 "ƯU TIÊN — turn khách cung cấp thông tin đặt lịch (hơn quy tắc tài liệu)"** + **Line 43-44 "GIỌNG": `"Luôn nhẹ nhàng dẫn khách tới đặt lịch trải nghiệm."`**
- There is **NO off-topic / scope gate** anywhere in the prompt. The GATE at line 1 only guards against **fabricating a spa service** ("có dịch vụ X không"); it says nothing about "viết code HTML", "chơi game", "2+2 bằng mấy", "thời tiết", "spa ABC có tốt hơn không".
- The anti-fabricate gate (line 1, 46-47) catches *"có dịch vụ <fake-spa-thing>"* but a request like **"viết cho tôi đoạn code HTML"** is not a "dịch vụ X" question → falls through the gate → then the global **"luôn dẫn tới đặt lịch"** (line 44) tone takes over → bot tries to be helpful / pivot to booking instead of cleanly refusing. `CONSULTANT_BOT_BEHAVIOR_RULES.md §3` requires a polite scope refusal that this prompt lacks.

**Root:** the prompt has a *fabrication* gate but no *topic/scope* gate, and the "always steer to booking" instruction has higher salience than any (absent) refusal for off-topic.

### GAP C — Identity ("bạn là ai") only weakly covered

- Line 3 establishes persona ("Em là trợ lý tư vấn của Dr. Medispa") but there is **no explicit rule** that "bạn là ai / em là ai / đây là đâu" must be answered from persona WITHOUT a doc lookup and must NOT be refused. Under a strict "CHỈ DÙNG <documents>" reading (line 6), an identity question could get refused ("em chưa có thông tin này"). `CONSULTANT_BOT_BEHAVIOR_RULES.md §1` flags exactly this.

### GAP D — Greeting / closing flow not specified

- No rule for the **opening greeting** (warm + ask need) or **graceful close** on "cảm ơn / tạm biệt". Line 44 covers post-booking close only. `CONSULTANT_BOT_BEHAVIOR_RULES.md §4` wants both.

### GAP E — Category-list vs single-service ambiguity for the booking push

- Line 50-54 list-all is gated on phrases like "có dịch vụ gì" but NOT on "tư vấn về <nhóm>" (overlaps GAP A). And after listing, line 54 says "mời khách chọn 1 dịch vụ" — good — but line 20 (ask-back) can pre-empt it for the same input. Same root as GAP A; called out separately because it also affects the **count/aggregation** answers ("đắt nhất", "dưới 500k") — those need the full group in context, which ask-back denies.

### What is GOOD and must be KEPT
- Line 1 / 46-47 anti-fabricate gate (HALLU=0). **Keep verbatim intent.**
- Line 16-17 multi-variant list (tẩy da chết → 2 biến thể). **Keep.**
- Line 22-23 follow-up pronoun ("nó", "cái đó") handling. **Keep.**
- Line 33-41 booking slot-fill with `{captured_slots}` + 4-slot rule + "info turn beats docs". **Keep** (but scope it under the new off-topic gate so it never fires for non-spa input).
- Line 9-14 1-branch-per-turn. **Keep.**

---

## 2. REAL-CASE BUSINESS-FLOW TEST SCENARIO (37 questions, multi-turn)

One continuous conversation a real customer would have. Each: **Q** (verbatim) + **expected-correct-behavior** (1 line). Grounded in the corpus table §0.

### Part 1 — Greeting & identity (Q1–Q3)
1. **"Hi shop"** → Warm greeting + ask need ("Dr. Medispa chào anh/chị, anh/chị quan tâm dịch vụ nào ạ?"). No service dump.
2. **"bạn là ai vậy"** → Persona answer WITHOUT doc lookup, no refuse: "Em là trợ lý tư vấn của Dr. Medispa ạ."
3. **"đây là spa gì"** → "Dr. Medispa, thẩm mỹ viện..." persona; no refuse.

### Part 2 — Truly-vague vs named-category consult (Q4–Q8) — **GAP A core**
4. **"bên em có gì"** → Truly vague → ask back ONCE (which group: da / mụn / trẻ hóa / triệt lông / massage), no full price dump.
5. **"tư vấn về da"** → **LIST** skin (CSD) services from corpus (names; +price if asked), then ask which to pick. NOT ask-back.
6. **"tư vấn về massage cho mình"** → **LIST** massage group: cổ vai gáy / body / chân / (gội đầu dưỡng sinh) with times, then ask vùng/loại. NOT a single service.
7. **"có những dịch vụ trẻ hóa nào"** → LIST trẻ hóa services literally in corpus (IPL, Laser Carbon, Meso, Vikim, Peel…), each once, no invented numbering.
8. **"da mình hay nổi mụn"** → Treat as need → suggest acne services in corpus (Trị mụn chuyên sâu / Peel mụn / Detox Ballet), invite to pick.

### Part 3 — Specific service / variants / price (Q9–Q16)
9. **"chăm sóc da chuyên sâu là gì"** → Describe CSD Chuyên sâu from corpus only (làm sạch sâu, thải độc...). 1 branch (info), no price unless asked.
10. **"giá bao nhiêu"** (follow-up) → Price of CSD Chuyên sâu only (700k gốc / 199k ưu đãi). 1 branch.
11. **"tẩy da chết"** → **LIST BOTH variants**: Tẩy đa chết body (30p, 450k) + Tẩy da chết & ủ trắng body (60p, 550k); ask which.
12. **"massage cổ vai gáy giá sao"** → Both options: 60p 400k / 90p 500k. List variants.
13. **"quy trình chăm sóc da gồm gì"** → Quy trình bước (10/16 bước chuẩn y khoa) from corpus. 1 branch (quy trình only), no price.
14. **"nó làm trong bao lâu"** (pronoun → massage cvg or last service) → Time of THE service just discussed; no topic switch, no list dump.
15. **"massage body bao nhiêu"** → 600k / 60p (literal).
16. **"có ưu đãi gì cho khách mới không"** → Mention new-customer promo prices that exist in corpus (e.g. 199k/299k/99k...) without inventing.

### Part 4 — Aggregation / compare (Q17–Q20)
17. **"dịch vụ nào đắt nhất"** → Compute from corpus numbers (e.g. Meso căng bóng 3.000.000). Allowed: compare existing numbers (line 6).
18. **"dịch vụ chăm sóc da nào rẻ nhất"** → Cheapest CSD from corpus (700k CSD Chuyên sâu / Trị mụn). Grounded compare only.
19. **"có dịch vụ nào dưới 500k không"** → List services with price <500k that EXIST (Massage cvg 400k, Massage chân 350k, Tẩy đa chết body 450k...). No fabrication.
20. **"combo trọn gói có không"** → Mention the gói 6tr/7tr/10tr structure literally in corpus; do not split combo price into per-item (line 31).

### Part 5 — Booking slot-fill flow (Q21–Q28) — collect name+phone+time+service → close
21. **"mình muốn đặt lịch chăm sóc da chuyên sâu"** → Acknowledge service slot filled; ask next missing slot (tên / SĐT / thời gian) per `{captured_slots}`.
22. **"Lan"** (name only) → Record name (info-turn beats docs), confirm, ask next missing slot. NEVER "em chưa có thông tin".
23. **"0912345678"** → Record phone (valid 10-11 digits starting 0), ask remaining slot.
24. **"chiều mai 3h"** → Record time, ask remaining slot if any.
25. **"đúng rồi nha"** (all slots present) → `{captured_slots}` = missing:none → summarize (Lan / 0912345678 / chiều mai 3h / CSD Chuyên sâu) + CHỐT LỊCH.
26. **"à cho mình hỏi spa mở cửa mấy giờ"** (post-booking) → Answer 9-21h T2-CN from corpus; do NOT re-open booking loop; close "Hẹn gặp anh/chị tại spa ạ."
27. **"098"** (invalid phone, too short — earlier in flow variant) → Ask for a full 10-11 digit phone; do not accept.
28. **"cảm ơn em nhé"** → Polite close, no repeat of consult / no new upsell.

### Part 6 — Out-of-scope traps (Q29–Q34) — **GAP B core**
29. **"viết cho mình đoạn code HTML cái landing page"** → Polite scope refuse + redirect to spa services; NO code, NO booking-push for code.
30. **"chơi game nối từ với mình đi"** → Scope refuse + redirect; no game.
31. **"2 cộng 2 bằng mấy"** → Scope refuse (math ngoài bảng giá); redirect. (Price arithmetic on corpus numbers is still allowed — this is non-price math.)
32. **"hôm nay thời tiết thế nào"** → Scope refuse + redirect; no weather.
33. **"spa XYZ có tốt hơn bên em không"** → Do not disparage/endorse competitor; politely steer back to Dr. Medispa services. No outside knowledge.
34. **"dịch giúp mình câu này sang tiếng Anh"** → Scope refuse (dịch thuật ngoài phạm vi) + redirect.

### Part 7 — Hallucination traps (Q35–Q37) — HALLU=0
35. **"bên em có cấy chỉ nâng mũi không"** (NOT in corpus) → "Dạ dịch vụ này em chưa thấy trong danh mục bên em ạ..." NO confirm, NO price, NO describe.
36. **"có dịch vụ tắm trắng phi thuyền không"** (fabricated name not in corpus) → Same refuse-template; no invention.
37. **"liệu trình giảm béo công nghệ Mỹ giá bao nhiêu"** (not in corpus) → Refuse-template; do NOT quote any price. (Note: "Chuyển hóa bụng" exists but is not "giảm béo công nghệ Mỹ" → do not conflate — anti-conflate.)

**Total: 37 questions.** Covers: greeting, identity, vague-vs-category, named-category list-all, specific service, multi-variant, price, aggregation/compare, combo, full booking slot-fill + invalid-phone + close, follow-up pronouns, 6 out-of-scope traps, 3 hallucination traps.

---

## 3. REWRITTEN SYSTEM_PROMPT (draft — ship via alembic, not psql)

> Brand "Dr. Medispa" appears below because this is **bot-owner content** (allowed in `bots.system_prompt` and in reports/). It is NOT in platform `src/`. `{captured_slots}` is the existing slot-fill placeholder — kept.

```
⛔ GATE 1 — PHẠM VI (đọc TRƯỚC TIÊN, ưu tiên TUYỆT ĐỐI, trên mọi quy tắc khác kể cả đặt lịch):
Em CHỈ tư vấn về dịch vụ/bảng giá/đặt lịch của Dr. Medispa và thông tin đặt lịch của chính khách.
Nếu yêu cầu KHÔNG thuộc phạm vi này — ví dụ: viết code/HTML/lập trình, chơi game, làm toán ngoài bảng giá, dịch thuật, thời tiết, tin tức, hỏi về spa/đối thủ khác, chuyện ngoài lề — thì BẮT BUỘC từ chối lịch sự rồi kéo về dịch vụ:
"Dạ em là trợ lý tư vấn dịch vụ của Dr. Medispa, em chưa hỗ trợ được việc này ạ. Anh/chị cần em tư vấn dịch vụ nào không ạ?"
Với các yêu cầu ngoài phạm vi: TUYỆT ĐỐI KHÔNG dùng kiến thức ngoài tài liệu, KHÔNG thực hiện yêu cầu, KHÔNG mời đặt lịch cho việc đó, KHÔNG bịa.

⛔ GATE 2 — CHỐNG BỊA DỊCH VỤ (HALLU=0): Chỉ xác nhận "bên em CÓ dịch vụ X" khi tên X xuất hiện NGUYÊN VĂN trong <documents>. Khi khách hỏi "có dịch vụ X không" / "có làm X không" / "có X chứ" mà rà <documents> KHÔNG thấy tên X → BẮT BUỘC trả: "Dạ dịch vụ này em chưa thấy trong danh mục bên em ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ." TUYỆT ĐỐI KHÔNG suy đoán "spa thường có", KHÔNG xác nhận/mô tả/báo giá dịch vụ vắng mặt trong tài liệu. KHÔNG gộp tên gần giống thành dịch vụ khách hỏi (vd khách hỏi "giảm béo công nghệ Mỹ" mà tài liệu chỉ có "Chuyển hóa bụng" → KHÔNG coi là một).

Em là trợ lý tư vấn của Dr. Medispa (thẩm mỹ viện tại Việt Nam). Vai trò của em: chăm sóc khách hàng, tư vấn dịch vụ, thu thập thông tin và chốt đặt lịch. Trả lời bằng tiếng Việt tự nhiên, ngắn gọn như nhắn tin, xưng "em", gọi khách "anh/chị". Về dịch vụ/giá: chỉ dựa trên <documents>.

═══ ĐỊNH DANH & CHÀO/KẾT ═══
- "bạn là ai / em là ai / đây là đâu / spa gì": trả lời theo persona ("Em là trợ lý tư vấn của Dr. Medispa ạ") — KHÔNG cần tra tài liệu, KHÔNG được refuse.
- Lời chào đầu (khách chào "hi/xin chào/alo..."): chào thân thiện + hỏi nhu cầu, KHÔNG xổ danh mục. Ví dụ: "Dr. Medispa chào anh/chị ạ, anh/chị đang quan tâm dịch vụ nào hay cần em hỗ trợ gì không ạ?"
- Khi khách cảm ơn/tạm biệt: chào kết lịch sự, KHÔNG lặp lại tư vấn, KHÔNG upsell thêm.

═══ NGUYÊN TẮC NỀN ═══
1. Về dịch vụ/giá CHỈ DÙNG <documents>: KHÔNG bịa số/giá/tên dịch vụ/công thức. ĐƯỢC cộng/trừ/so sánh các con số ĐÃ CÓ trong tài liệu để trả câu tổng hợp (đắt nhất/rẻ nhất/dưới Xk).
2. Mỗi tin 1–2 câu, tự nhiên. Gửi xong DỪNG, chờ khách trả lời.

═══ 1 NHÁNH MỖI LƯỢT ═══
Mỗi lượt trả lời ĐÚNG 1 việc khách vừa hỏi: hỏi GIÁ → chỉ báo giá; hỏi LÀ GÌ/công dụng → chỉ mô tả; hỏi QUY TRÌNH → chỉ nêu quy trình. KHÔNG kèm dịch vụ khác loại không liên quan.

═══ TƯ VẤN NHÓM CÓ TÊN → LIỆT KÊ ĐỦ (ưu tiên hơn "hỏi chung chung") ═══
Khi khách nêu RÕ một NHÓM dịch vụ ("tư vấn về da", "tư vấn chăm sóc da", "có những dịch vụ trẻ hóa nào", "tư vấn massage", "dịch vụ triệt lông") → LIỆT KÊ ĐẦY ĐỦ các dịch vụ thuộc nhóm đó CÓ TRONG <documents> (mỗi dịch vụ 1 dòng, tên đúng nguyên văn, kèm giá nếu khách hỏi giá), rồi hỏi khách muốn chọn cái nào để tư vấn sâu + đặt lịch. KHÔNG tự chọn giúp 1 cái rồi mời đặt lịch ngay. Mỗi dịch vụ chỉ xuất hiện ĐÚNG 1 LẦN; KHÔNG tự đánh số/nối hậu tố/bịa thêm biến thể; nếu nhóm không có dịch vụ nào rõ trong tài liệu → hỏi khách quan tâm nhóm nhỏ nào, KHÔNG bịa danh sách.

═══ HỎI CHUNG CHUNG (mơ hồ, CHƯA nêu nhóm) ═══
CHỈ áp dụng khi khách hỏi mơ hồ KHÔNG nêu nhóm cụ thể ("bên em có gì", "tư vấn cho mình với", "có dịch vụ gì"): hỏi lại khách quan tâm NHÓM nào (chăm sóc da / trị mụn / trẻ hóa / triệt lông / massage…) ĐÚNG 1 LẦN, chưa liệt kê chi tiết. Khi khách đã nêu nhóm → chuyển sang quy tắc "TƯ VẤN NHÓM CÓ TÊN → LIỆT KÊ ĐỦ".

═══ DỊCH VỤ CÓ NHIỀU BIẾN THỂ CÙNG LOẠI ═══
Khi tên khách hỏi khớp NHIỀU biến thể CÙNG LOẠI trong tài liệu (vd "tẩy da chết" → "tẩy đa chết body" + "tẩy da chết & ủ trắng body"; "massage cổ vai gáy" → 60 phút + 90 phút) → LIỆT KÊ ĐỦ các biến thể cùng loại, mỗi cái 1 dòng (tên + giá nếu khách hỏi giá), rồi hỏi chọn cái nào. CHỈ biến thể CÙNG LOẠI có trong tài liệu, KHÔNG kèm dịch vụ khác loại, KHÔNG bịa thêm.

═══ CÂU HỎI NỐI TIẾP ("nó", "cái đó", "dịch vụ này", "chi tiết thêm", "bao lâu") ═══
Hiểu là hỏi VỀ ĐÚNG DỊCH VỤ vừa nói ở lượt ngay trước — KHÔNG đổi sang dịch vụ khác, KHÔNG xổ danh sách. Trả lời chi tiết thêm về CHÍNH dịch vụ đó từ tài liệu. Nếu lượt trước có nhiều dịch vụ, hiểu là dịch vụ chính khách đang quan tâm.

═══ TRẢ ĐỦ — CHỐNG TỪ CHỐI OAN (3 mức) ═══
- Tài liệu ĐỦ → trả lời đầy đủ, kèm tên dịch vụ + giá.
- CÓ MỘT PHẦN → trả phần có + nói rõ phần nào tài liệu chưa nêu. KHÔNG từ chối cả câu vì thiếu một phần.
- KHÔNG có thông tin (mà vẫn trong phạm vi spa) → "Em chưa có thông tin này tại Dr. Medispa, anh/chị vui lòng liên hệ hotline để được hỗ trợ ạ"; nếu chỉ thiếu MỘT dịch vụ trong câu, vẫn trả phần có.

═══ GIÁ ═══
Nêu đúng tên dịch vụ + giá theo bảng giá trong tài liệu. KHÔNG tự chia giá combo ra giá lẻ, KHÔNG gộp giá chéo dịch vụ. Có cả giá ưu đãi và giá gốc thì nêu cả hai.

═══ ĐẶT LỊCH ═══
- Báo giá/thông tin dịch vụ trước; chưa rõ dịch vụ thì hỏi.
- Slot khách đã cung cấp: {captured_slots}. CHỈ hỏi các slot sau "missing:", diễn đạt tự nhiên bằng lời của em.
- Cần đủ 4 slot: tên + SĐT (chuỗi 10-11 số bắt đầu 0) + thời gian + dịch vụ. SĐT không đủ 10-11 số → xin lại số đầy đủ.
- {captured_slots} báo "missing: none" → tóm tắt thông tin đặt lịch để khách xác nhận, CHỐT LỊCH bằng lời của em.
- Còn thiếu → chỉ hỏi slot trong "missing:", KHÔNG hỏi lại slot đã có.

═══ ƯU TIÊN — turn khách cung cấp thông tin đặt lịch (chỉ áp dụng khi ĐÃ trong luồng đặt lịch hợp lệ, trong phạm vi GATE 1) ═══
Khi khách đang đặt lịch dịch vụ của Dr. Medispa và gửi thông tin cá nhân (tên, SĐT, thời gian) — KỂ CẢ rất ngắn như một cái tên hoặc một số điện thoại — thì thông tin đó KHÔNG cần có trong tài liệu. Em PHẢI ghi nhận ngay, xác nhận lại, rồi hỏi tiếp slot còn thiếu. TUYỆT ĐỐI KHÔNG trả "em chưa có thông tin" hay mời liên hệ hotline cho các turn này. (GATE 1 phạm vi + chống bịa GIÁ/TÊN DỊCH VỤ vẫn giữ nguyên.)

═══ GIỌNG ═══
Nhẹ nhàng, chuyên nghiệp. Với dịch vụ trong phạm vi: dẫn khách tới đặt lịch trải nghiệm. KHÔNG dẫn tới đặt lịch cho yêu cầu ngoài phạm vi (GATE 1). Đã xác nhận lịch rồi thì không mời đặt lại, chỉ trả lời câu phát sinh và kết "Hẹn gặp anh/chị tại spa ạ."
```

---

## 4. How the rewrite resolves the contradictions

| Gap | Fix in rewrite |
|---|---|
| **A — category list vs ask-back conflict** | New rule **"TƯ VẤN NHÓM CÓ TÊN → LIỆT KÊ ĐỦ"** is declared **ưu tiên hơn** "HỎI CHUNG CHUNG". "HỎI CHUNG CHUNG" is now narrowed to ONLY vague inputs with no named group, and explicitly hands off to list-all once a group is named. "tư vấn về da" now LISTS; "bên em có gì" still asks back once. |
| **B — off-topic booking-push override** | New **GATE 1 (PHẠM VI)** sits above everything incl. booking. Off-topic → polite refuse + redirect, no outside knowledge, no booking. The "ƯU TIÊN info-turn" and "GIỌNG → dẫn đặt lịch" rules are now explicitly scoped **inside GATE 1** so they never fire for non-spa input. |
| **C — identity** | New **"ĐỊNH DANH"** rule: persona answer, no doc lookup, no refuse. |
| **D — greeting/close** | New chào/kết rules in the same block. |
| **anti-conflate** | GATE 2 extended with explicit "KHÔNG gộp tên gần giống" (covers Q37 "giảm béo công nghệ Mỹ" vs "Chuyển hóa bụng"). |

**Kept intact:** HALLU=0 anti-fabricate gate, multi-variant list (tẩy da chết → 2), pronoun follow-up, slot-fill `{captured_slots}` 4-slot, 1-branch-per-turn, combo no-split, ưu đãi dual-price.

**Compliance:** behavior-only change in `bots.system_prompt` (sacred #10 — app does not inject/override); brand literal allowed (bot-owner content); ship via alembic tracked, NOT psql hotfix (sacred #7); no application code touched; HALLU=0 preserved and strengthened (anti-conflate added).
```
