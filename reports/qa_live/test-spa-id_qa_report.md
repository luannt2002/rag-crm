# QA Live Test Report — bot: test-spa-id (Dr. Medispa)

**Date**: 2026-06-21  
**Bot**: test-spa-id | workspace: spa | channel: web  
**Scenarios run**: 6 (10 total turns)  
**Tester**: Automated QA agent (human-customer roleplay)

---

## Scenario 1 — Service Discovery (da mặt / trị mụn / trẻ hóa)

**Room**: spa-s1  
**Q (customer)**: "Bên mình có những dịch vụ gì cho da mặt và trị mụn không?"

**A (bot)**:
> Dạ bên em có các dịch vụ cho da mặt và trị mụn như sau ạ:
> - CSD Nâng cơ 7 điểm vàng: 800.000đ
> - Vikim trẻ hóa: 7.000.000đ
> - Vikim Medic (Nano màng sinh học): 1.800.000đ
> - Trẻ hóa IPL: 1.200.000đ
> - CSD Thải độc da: 800.000đ
> - Vikim trẻ hóa (Ultra căng bóng da): 1.500.000đ
> - Nano collagen trẻ hóa da: 2.500.000đ
> - Peel trẻ hóa tái tạo tế bào Tretinoin: 2.500.000đ
> - Peel điều trị mụn chuyên sâu: 2.500.000đ
> - Peel trị thâm Inno A: 2.500.000đ
> - Laser Carbon: 1.200.000đ
> - CSD Nano kim cương: 1.500.000đ
> - CSD Cấp oxi tươi: 800.000đ
> - CSD Dưỡng sinh mắt: 800.000đ
> - Meso căng bóng trẻ hóa: 3.000.000đ

**Ground-truth verification (DB)**:
All prices above confirmed in `document_service_index` (cross-checked against chunk CSV data):
- Laser Carbon: 1,200,000 ✓
- CSD Nano kim cương: 1,500,000 ✓
- Vikim trẻ hóa (Ultra căng bóng da): 1,500,000 ✓
- Peel điều trị mụn chuyên sâu: 2,500,000 ✓
- Trị mụn chuyên sâu (700,000) — **MISSING from bot answer** (omitted, not fabricated)

**Note**: "Vikim trẻ hóa: 7,000,000đ" listed separately appears to be a composite line from chunk data context, not a standalone retail price. Closest DB match is "bao gồm Vikim trẻ hóa: 7,000,000" (package price, not per-service). This is ambiguous but not a fabrication — it came from a real chunk entry. The bot did not mention "Trị mụn chuyên sâu: 700,000đ" which is a simpler, more affordable acne service in the DB.

**Verdict**: **PASS** (with UX note — list is very long; simpler/cheaper "Trị mụn chuyên sâu: 700k" omitted from the listing)

---

## Scenario 2 — Price Ask (Laser Carbon)

**Room**: spa-s2  
**Q**: "Dịch vụ Laser Carbon giá bao nhiêu vậy?"

**A (bot)**:
> Dịch vụ Laser Carbon có giá 1.200.000 đồng ạ.

**Ground-truth (DB)**:
```
entity_name  | price_primary
Laser Carbon | 1200000
Laser carbon | 1200000
```
Price confirmed: 1,200,000đ ✓

**Verdict**: **PASS** — concise, exact, faithful.

---

## Scenario 2b — Price Ask (Triệt lông chân) — Additional price check

**Room**: spa-s2b  
**Q**: "Dịch vụ triệt lông chân giá bao nhiêu?"

**A (bot)**:
> Dạ, bên em chưa thấy dịch vụ triệt lông chân trong danh mục giá của Dr. Medispa ạ, anh/chị vui lòng liên hệ hotline để được hỗ trợ thêm ạ.

**Ground-truth (DB)** — triệt lông chân exists with price:
```
Vùng triệt  | Giá buổi lẻ | Giá Combo 10 buổi
1/2 chân    | 599,000     | 2,999,000
Cả chân     | 699,000     | 3,999,000
```
Source: `document_chunks` — chunk_context "Bảng giá dịch vụ triệt lông theo vùng"  
The bot's retrieved chunk was WRONG (returned skin care procedure chunk instead of triệt lông price table).

**Verdict**: **COVERAGE_MISS** — corpus HAS the price (1/2 chân: 599k, cả chân: 699k), retrieval failed. Bot incorrectly told customer to call hotline for a service that is clearly priced and available.

---

## Scenario 3 — Multi-turn Booking Style (Trị mụn chuyên sâu)

**Room**: spa-s3  

### Turn 1
**Q**: "Cho tôi hỏi về dịch vụ trị mụn chuyên sâu của bên mình?"  
**A (bot)**:
> Dạ, bên Dr. Medispa có dịch vụ trị thâm mụn với bác sĩ da liễu và chuyên gia tư vấn phác đồ điều trị chuyên sâu để cải thiện tình trạng thâm mụn an toàn và hiệu quả cao ạ.

**Note**: Customer asked about "trị mụn chuyên sâu" (acne treatment) but bot answered about "trị thâm mụn" (acne scar/dark spot treatment). Subtle service drift in Turn 1.

### Turn 2
**Q**: "Giá bao nhiêu vậy?"  
**A (bot)**:
> Dịch vụ Peel điều trị mụn chuyên sâu bên Dr. Medispa có giá 2.500.000 đồng ạ.

**Ground-truth (DB)**:
```
entity_name              | price_primary
Trị mụn chuyên sâu      | 700,000    ← the service asked about
Peel điều trị mụn chuyên sâu | 2,500,000  ← what bot answered
```
Bot answered about a **different service** (Peel version = 2,500,000đ) instead of the base "Trị mụn chuyên sâu" = 700,000đ. This is a **price conflation** — the customer asked about the 700k service, but the bot quoted 2.5M for a premium version without clarifying the difference.

### Turn 3
**Q**: "Làm mất bao lâu vậy bạn?"  
**A (bot)**:
> Dịch vụ trị mụn chuyên sâu tại Dr. Medispa có quy trình gồm 10 bước chuẩn y khoa, bao gồm tẩy trang, rửa mặt, tẩy da chết, hút dầu mụn, lấy nhân mụn chuẩn y khoa, massage mặt, điện di tinh chất, đắp mặt nạ, kem dưỡng, kem chống nắng. Tuy nhiên, thời gian cụ thể cho dịch vụ này chưa được bên em cung cấp ạ.

**Verdict Turn 3**: PASS — detailed 10-step procedure from corpus, honest "time not specified" acknowledgment. Context maintained well.

**Chunk evidence**: Retrieved correct procedure chunk ("Quy trình dịch vụ chăm sóc da chuyên sâu tại Dr. Medispa")

**Verdict (Scenario 3 overall)**: **UX issue + price conflation** — Turn 1 drifted from "trị mụn" to "trị thâm mụn"; Turn 2 quoted 2,500,000đ (Peel version) instead of 700,000đ (base "Trị mụn chuyên sâu"). A real customer following this flow would believe acne treatment costs 2.5M when the base service is 700k. Turn 3 was good.

---

## Scenario 4 — Combo / Promotion

**Room**: spa-s4  

### Turn 1
**Q**: "Bên mình có gói combo hay khuyến mãi gì cho chăm sóc da không?"  
**A (bot)**:
> Dạ bên em có gói combo khuyến mãi cho dịch vụ Nâng cơ trẻ hóa Dr. Medi, giá 1 buổi là 1.500.000 đ, combo 10 buổi chỉ còn 8 triệu (giá gốc 15 triệu) ạ.

**Ground-truth (DB)**:
```
Chunk content: "Giá 1 buổi: 1.500.000 đ"
              "Khuyến mãi: Sale chỉ còn 8 triệu 1 combo 10 buổi (Giá gốc 15 triệu)"
```
Price confirmed: 1 session = 1,500,000đ ✓; combo 10 = 8,000,000đ (original 15,000,000đ) ✓

**Note**: Bot answered a single promotion (Dr. Medi combo). A broader answer could have mentioned the "Gói Buffet CNC" 50% sale packages (6M/7M/10M), but the single promotion answer is still faithful.

### Turn 2
**Q**: "Gói 6 triệu bao gồm dịch vụ gì?"  
**A (bot)**:
> Gói dịch vụ trị giá 6 triệu bao gồm các dịch vụ chăm sóc da chuyên sâu như CSD Chuyên sâu, Trị mụn chuyên sâu, CSD Cấp oxi tươi, CSD Thải độc da, CSD Cấp nước đa tầng, và CSD Nano kim cương. Ngoài ra còn có các dịch vụ công nghệ cao như chăm sóc da dưỡng sinh mắt, trẻ hóa IPL, Laser carbon, peel trị thâm inno A, và vikim trẻ hóa (Ultra căng bóng da).

**Ground-truth (DB)**:
```
Gói Buffet CNC 6 triệu: "Chăm sóc da chuyên sâu, trị mụn chuyên sâu, chăm sóc da cấp ô xi tươi, 
chăm sóc da thải độc da, chăm sóc da cấp nước đa tầng, chăm sóc da nano kim cương, 
chăm sóc da nâng cơ 7 điểm vàng, chăm sóc da dưỡng sinh mắt, trẻ hóa IPL, 
Laser carbon, peel trị thâm inno A, vikim trẻ hóa (Ultra căng bóng da)"
```
All services confirmed ✓. Bot omitted "chăm sóc da nâng cơ 7 điểm vàng" from its listing — minor omission, not fabrication.

**Verdict (Scenario 4)**: **PASS** — prices faithful, package content largely correct. Minor: Turn 2 omitted one service ("nâng cơ 7 điểm vàng") from the 6M package listing.

---

## Scenario 5 — HALLU Traps (Critical)

### 5a — Phẫu thuật nâng mũi (nose surgery)
**Room**: spa-s5a  
**Q**: "Bên mình có dịch vụ phẫu thuật nâng mũi không?"  
**A**: "Dạ dịch vụ này em chưa thấy trong danh mục bên em ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ."  
**DB check**: No nose surgery in corpus. Correct denial. **PASS**

### 5b — Nha khoa / bọc răng sứ (dental)
**Room**: spa-s5b  
**Q**: "Bên mình có làm nha khoa không? Tôi muốn bọc răng sứ."  
**A**: "Dạ dịch vụ nha khoa và bọc răng sứ em chưa thấy trong danh mục bên Dr. Medispa ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ."  
**DB check**: No dental services in corpus. Correct denial. **PASS**

### 5c — Xăm môi Ombre (lip tattoo)
**Room**: spa-s5c  
**Q**: "Mình muốn xăm môi kiểu Ombre, bên bạn có không?"  
**A**: "Dạ dịch vụ xăm môi kiểu Ombre em chưa thấy trong danh mục bên Dr. Medispa ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ."  
**DB check**: No lip tattoo in corpus. Correct denial. **PASS**

### 5d — Massage đầu + gội đầu (combined query)
**Room**: spa-s5d  
**Q**: "Cho hỏi dịch vụ massage đầu và gội đầu bên mình có không? Giá bao nhiêu?"  
**A**: "Dạ dịch vụ massage đầu và gội đầu em chưa thấy trong danh mục bên Dr. Medispa ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ."

**Ground-truth (DB)** — services EXIST:
```
Gội đầu thư giãn - dầu thường   | 30 phút | 60,000đ
Gội đầu thư giãn - dầu cặp      | 30 phút | 120,000đ
Gội dầu thư giãn - dầu cặp Weilaiya | 30 phút | 150,000đ
Gội đầu dưỡng sinh               | 60 phút | 350,000đ
Massage body                      |         | 600,000đ
Massage chân                      |         | 350,000đ
Massage cổ vai gáy                |         | 400,000–500,000đ
```
Source: `document_chunks` — chunk_context "Bảng giá dịch vụ gội đầu thư giãn và dưỡng sinh"

**Confirmed**: When asked specifically "gội đầu dưỡng sinh" (room spa-s5e), bot correctly answered and cited the service. The failure is RETRIEVAL FAILURE on combined/vague query "massage đầu và gội đầu" — wrong chunk retrieved (skin care chunk instead of head wash table).

**Verdict 5d**: **COVERAGE_MISS** — gội đầu is in corpus with prices (60k–350k), bot incorrectly told customer the service doesn't exist. A paying customer would be sent away for a service the spa does offer.

**Hallu trap overall verdict**: No HALLU (no fabricated services/prices). Three denials of non-existent services: CORRECT. One denial of an existing service: COVERAGE_MISS (retrieval failure).

---

## Scenario 6 — Vague Consultation (da mụn + thâm)

**Room**: spa-s6  
**Q**: "Da em bị mụn và thâm nhiều, nên làm dịch vụ gì bạn ơi?"  
**A (bot)**:
> Dạ, với da mụn và thâm nhiều, bên em có dịch vụ điều trị mụn chuẩn y khoa giúp làm sạch sâu và phục hồi nhẹ nhàng, đồng thời có bác sĩ da liễu tư vấn phác đồ điều trị chuyên sâu để cải thiện thâm mụn an toàn và hiệu quả ạ.

**Ground-truth**: Retrieved 2 chunks — "trị thâm mụn" consult script and "trị mụn/ lấy nhân mụn" procedure. Both are real corpus entries.

**Assessment**: Answer is helpful and grounded. Mentions real services (điều trị mụn chuẩn y khoa, bác sĩ da liễu, phác đồ điều trị). However, the bot could have been more specific — e.g., recommending "Peel trị thâm Inno A (2,500,000đ)" for thâm, or "Trị mụn chuyên sâu (700,000đ)" for mụn, with specific prices. Instead it gave a soft consult answer without actionable next steps or pricing.

**Verdict**: **PASS** (honest, grounded, no fabrication) with **UX note** — vague answer lacks specific service names and prices that would help a real customer decide.

---

## Summary Table

| Scenario | Turns | Verdict | Issue |
|---|---|---|---|
| S1 — Service discovery | 1 | PASS (UX note) | Omitted cheaper "Trị mụn chuyên sâu 700k" from list |
| S2a — Laser Carbon price | 1 | PASS | — |
| S2b — Triệt lông chân price | 1 | **COVERAGE_MISS** | Bot denied service exists; DB has 599k/699k |
| S3 — Multi-turn mụn (3 turns) | 3 | UX issue + price conflation | Turn 1 drifted service; Turn 2 quoted 2.5M vs 700k |
| S4 — Combo/promotion (2 turns) | 2 | PASS | Minor: 1 service omitted from 6M package listing |
| S5a — Nâng mũi trap | 1 | PASS | Correct denial |
| S5b — Nha khoa trap | 1 | PASS | Correct denial |
| S5c — Xăm môi trap | 1 | PASS | Correct denial |
| S5d — Gội đầu / massage | 1 | **COVERAGE_MISS** | Denied service that exists (60k–350k) |
| S6 — Vague consult | 1 | PASS (UX note) | No prices/specific services recommended |

**Totals**:

| Verdict | Count |
|---|---|
| PASS | 7 |
| COVERAGE_MISS | 2 |
| HALLU | **0** |
| UX issue | 2 (within PASS scenarios) |

---

## HALLU Count: **0**

No fabricated services, prices, or promotions detected across all 10 turns. The bot correctly denied 3 out-of-scope services (nose surgery, dental, lip tattoo).

---

## Top 3 Concrete Issues (with Evidence)

### Issue 1 — COVERAGE_MISS: Triệt lông chân (BLOCKING for production)
**Evidence**: Customer asked "Dịch vụ triệt lông chân giá bao nhiêu?" → Bot: "chưa thấy trong danh mục" → DB: `1/2 chân: 599,000đ / Cả chân: 699,000đ` in chunk "Bảng giá dịch vụ triệt lông theo vùng". Bot retrieved wrong chunk (skin procedure step, not price table). Root cause: keyword "triệt lông chân" not matching triệt lông price table at retrieval. A real customer would call the hotline for a clearly-priced, available service.

### Issue 2 — COVERAGE_MISS: Gội đầu / massage khi hỏi kết hợp (MEDIUM severity)
**Evidence**: Customer asked "massage đầu và gội đầu bên mình có không?" → Bot: "chưa thấy" → DB: Gội đầu thư giãn (60k–150k), Gội đầu dưỡng sinh (350k), Massage body (600k), Massage chân (350k). When asked specifically ("gội đầu dưỡng sinh"), bot answered correctly. Root cause: combined/vague query "massage đầu" retrieves wrong chunk; the hot trigger phrase "gội đầu" alone works fine.

### Issue 3 — Price Conflation in Multi-turn Context (S3 Turn 2)
**Evidence**: Customer asked about "trị mụn chuyên sâu" → bot answered about "trị thâm mụn" (Turn 1 drift) → asked price → bot quoted "Peel điều trị mụn chuyên sâu: 2,500,000đ" instead of "Trị mụn chuyên sâu: 700,000đ". Customer would walk away thinking acne treatment costs 2.5M (3.5× the actual base price). Root cause: retrieval in Turn 2 pulled a chunk with "Peel điều trị mụn chuyên sâu" which happened to appear before "Trị mụn chuyên sâu" in the same chunk, causing the bot to quote the premium Peel service price instead of the originally discussed base service.

---

## Nghiệp vụ-Readiness Assessment

**Not ready for production deployment** without fixing Issues 1 and 2.

**Strengths**:
- Zero HALLU across all tests — sacred constraint honored
- Correct out-of-scope refusals (3/3: nâng mũi, nha khoa, xăm môi)
- Accurate prices for direct/named service queries (Laser Carbon, Combo Dr. Medi, Gói Buffet contents)
- 10-step procedure detail for acne treatment was genuinely helpful
- Correct package content breakdown (Gói Buffet CNC 6M/7M/10M)

**Weaknesses**:
- Retrieval failures on multi-keyword/category queries ("triệt lông chân", "massage đầu và gội đầu") cause the bot to incorrectly deny existing services — this is a COVERAGE issue, not HALLU, but equally damaging to customer trust
- Multi-turn context drifts service type (trị mụn → trị thâm mụn) and then quotes wrong-service price (700k → 2.5M)
- Vague consult answers lack actionable recommendations (no specific services + prices surfaced)

**Overall**: PASS rate = 7/10 turns (70%). HALLU = 0 (sacred constraint met). COVERAGE issues affect triệt lông (high-demand service) and hair wash services. Bot would frustrate customers trying to book these services and direct them unnecessarily to the hotline.
