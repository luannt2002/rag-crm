# QA Live Test Report — chinh-sach-xe

**Bot:** chinh-sach-xe | workspace: xe | channel: web  
**Date:** 2026-06-21  
**Tester:** Automated QA harness (realistic customer persona)  
**Scenarios run:** 6 | **Total turns:** 9  
**Server:** http://localhost:3004 (live)

---

## Ground Truth Reference (from DB before tests)

Key prices verified from `document_service_index`:

| Size | Brand | DB Price | DB Qty |
|---|---|---|---|
| 205/55R16 G/P | Landspider | 1,044,000đ | 819 |
| 185/65R15 G/P | Landspider | 900,000đ | 527 |
| 185/65R15 A68 | Rovelo | 810,000đ | 324 |
| 255/70R16 H/T | Landspider | 1,755,000đ | 351 |
| 255/70R16 A/T | Landspider | 2,079,000đ | 192 |
| 235/60R17 H/T | Landspider | 1,611,000đ | 46 |

Michelin, Bridgestone → 0 rows in catalog (confirmed).

---

## Scenario S1 — Price by Size: 205/55R16

**Room:** xe-s1 | **Turns:** 1

**Q (customer):** "Cho tôi hỏi lốp 205/55R16 của Landspider giá bao nhiêu vậy? Còn hàng không?"

**A (bot):** "Dạ quy cách 205/55R16 bên em có loại Landspider 205/55R16 GP giá **972.000đ/lốp**, hiện còn **338 lốp** ạ."

**Ground truth (corpus chunk 214 + DB):**
- Price: **1,044,000đ** (corpus: `price: 1044000`)
- Quantity: **819** (corpus: `quantity: 819`)

**Diff:**
- Bot price: 972,000đ → Corpus: 1,044,000đ → **delta -72,000đ (~7%)**
- Bot stock: 338 → Corpus: 819 → **delta -481 units**
- Neither figure (972,000 or 338) appears anywhere in the corpus (`SELECT content ... WHERE content ILIKE '%972%' → 0 rows`; `338` not present in any 205/55 context)

### **VERDICT: HALLU — fabricated price (972,000đ) and stock count (338)**

Corpus has the exact answer (chunk_index 214, 220). The retriever retrieved a wrong chunk (`chunk_index 379` = an image-link chunk with no price data); the LLM then confabulated numbers not present in the context.

---

## Scenario S2 — Multi-Turn: 185/65R15 with follow-up (3 turns)

**Room:** xe-s2 | **Turns:** 3

### Turn 1
**Q:** "Cho tôi hỏi lốp 185/65R15 có những loại gì?"

**A:** "Dạ quy cách 185/65R15 bên em có các loại sau ạ:
- Lốp LANDSPIDER 185/65R15 88H CITYTRAXX G/P giá **chưa có thông tin**, hiện còn **26 lốp** ạ.
- Lốp Rovelo 185/65R15 A68 giá **chưa có thông tin**, hiện còn hàng (số lượng cụ thể không nêu trong tài liệu) ạ."

**Ground truth:** Both Landspider (qty 527) and Rovelo (qty 324) have price AND quantity in corpus. Bot said "chưa có thông tin" for price (retrieval miss) and wrong qty (26 vs 527).

**VERDICT: COVERAGE_MISS + partial HALLU on stock.** Bot identified both brands correctly but couldn't retrieve price. Stock count 26 is fabricated.

---

### Turn 2 (follow-up: "Loại nào rẻ hơn? Còn hàng không?")
**Q:** "Loại nào rẻ hơn vậy shop? Và còn hàng không?"

**A:** "Dạ hiện tại bên em có lốp LANDSPIDER 235/60R17 102H CITYTRAXX H/T giá **1.611.000đ/lốp**, còn **46 lốp** ạ. Đây là sản phẩm duy nhất trong quy cách 235/60R17 có thông tin giá và tồn kho..."

**Issue:** The bot completely **lost conversation context**. The customer asked about 185/65R15 but the bot answered with 235/60R17 H/T. It also claims this is "the only product with price+stock info" for 235/60R17, which is irrelevant to the question.

The 1,611,000đ / 46 units for 235/60R17 is technically correct corpus data for that size, but the bot applied it to the wrong conversation thread.

**VERDICT: COVERAGE_MISS (context loss) — bot forgot the topic was 185/65R15, drifted to irrelevant size.**

---

### Turn 3 (correction: "Ý tôi là hỏi lốp 185/65R15 thôi")
**Q:** "Ý tôi là hỏi lốp 185/65R15 thôi, chứ không phải 235/60R17 - giá hai loại thế nào?"

**A:** "Dạ quy cách 185/65R15 bên em có các loại sau ạ:
- Lốp LANDSPIDER 185/65R15 88H CITYTRAXX G/P giá **1.150.000đ/lốp**, hiện còn **26 lốp** ạ.
- Lốp Rovelo 185/65R15 A68 giá **1.250.000đ/lốp**, hiện còn **15 lốp** ạ."

**Ground truth:**
- Landspider 185/65R15: **900,000đ**, qty **527** (corpus chunk 125, 131)
- Rovelo 185/65R15 A68: **810,000đ**, qty **324** (corpus chunk 1159, 1164)

**Diff:**
- Landspider: 1,150,000đ vs 900,000đ → **+250,000đ over-price (+28%)**
- Rovelo: 1,250,000đ vs 810,000đ → **+440,000đ over-price (+54%)**
- Stock 26 and 15 vs corpus 527 and 324 → both fabricated

Neither 1,150,000 nor 1,250,000 nor qty 26 nor qty 15 appear anywhere in the corpus for 185/65R15. The correct chunk existed and was retrievable (confirmed above).

### **VERDICT: HALLU — both prices and both stock counts fully fabricated. Correct answer was in corpus.**

---

## Scenario S3 — Vague Customer (Toyota Vios) — 2 turns

**Room:** xe-s3 | **Turns:** 2

### Turn 1
**Q:** "Xe tôi đi Toyota Vios, cần thay lốp, shop tư vấn giúp tôi nên lấy loại nào?"

**A:** "Dạ em là trợ lý tư vấn lốp xe của Nam Phát, chuyên phân phối lốp Landspider và Rovelo ạ. Anh/chị cho em biết quy cách lốp hiện tại của xe Toyota Vios (vd: 185/65R15, 195/65R15...) để em tư vấn loại phù hợp và báo giá giúp anh/chị nhé."

**Assessment:** Correctly identifies brand scope (Landspider/Rovelo). Sensibly asks for the tire size rather than guessing. Even gives helpful example sizes (185/65R15, 195/65R15 — both real Vios sizes). Natural and professional tone.

**VERDICT: PASS — appropriate UX handling of vague request.**

### Turn 2
**Q:** "Xe tôi đang dùng lốp 185/65R15, giá lốp Landspider và Rovelo loại này bao nhiêu?"

**A:** "Lốp LANDSPIDER 185/65R15 giá **1.150.000đ/lốp**, còn **26 lốp**. Lốp Rovelo 185/65R15 A68 giá **1.250.000đ/lốp**, còn **15 lốp**."

**Ground truth (same as S2 Turn 3):** Landspider 900,000đ / 527 units. Rovelo 810,000đ / 324 units.

### **VERDICT: HALLU — same fabricated prices and stock as S2 Turn 3. Consistently wrong across sessions.**

This confirms the issue is systematic retrieval failure for 185/65R15, not a one-off.

---

## Scenario S4 — Brand Availability: Landspider

**Room:** xe-s4 | **Turns:** 1

**Q:** "Shop có bán lốp Landspider không? Thương hiệu này có tốt không?"

**A:** "Dạ, bên em có phân phối lốp Landspider ạ. Về chất lượng thương hiệu, em là trợ lý tư vấn lốp xe của Nam Phát chuyên hỗ trợ về lốp Landspider và Rovelo nên chưa thể đánh giá chi tiết về 'tốt' hay không được ạ. Anh/chị cho em biết quy cách lốp anh/chị cần để em tư vấn sản phẩm phù hợp và báo giá cụ thể nhé."

**Assessment:** Correctly confirms Landspider availability. Honest about not being able to make subjective quality comparisons (appropriate for a sales bot). Redirects to useful next step. Natural and professional.

**VERDICT: PASS.**

---

## Scenario S5 — HALLU TRAP: Michelin + Bridgestone (brands not carried)

**Room:** xe-s5 | **Turns:** 1 | **Room xe-s5b:** 1 additional turn

### Turn 1 — Michelin
**Q:** "Shop có bán lốp Michelin không? Giá bao nhiêu?"

**A:** "Dạ bên em hiện phân phối Landspider và Rovelo, chưa có hãng Michelin ạ. Anh/chị cho em quy cách lốp, em gợi ý loại tương đương đang có nhé."

**Ground truth:** Michelin → 0 rows in catalog. Bot correctly refused and offered alternative.

**VERDICT: PASS — HALLU trap correctly refused.**

### Turn 2 — Bridgestone
**Q:** "Shop có lốp Bridgestone size 195/65R15 không? Giá khoảng bao nhiêu?"

**A:** "Dạ bên em hiện phân phối Landspider và Rovelo, chưa có hãng Bridgestone ạ. Anh/chị cho em quy cách lốp khác hoặc thương hiệu Landspider, Rovelo để em tư vấn và báo giá nhé ạ."

**VERDICT: PASS — correctly refuses Bridgestone. No price fabricated.**

---

## Scenario S6 — Comparison/Recommendation: 255/70R16 H/T vs A/T for Fortuner

**Room:** xe-s6 | **Turns:** 1

**Q:** "Tôi đang cân nhắc giữa lốp Landspider 255/70R16 H/T và loại A/T cho xe Fortuner. Khác nhau điểm gì và nên chọn loại nào?"

**A:** "hiện tài liệu bên em có thông tin về lốp Landspider 255/70R16 loại A/T... nhưng **chưa có dữ liệu cụ thể về loại H/T** cho quy cách này. Loại A/T thường phù hợp cho cả đường trường và địa hình nhẹ, còn H/T thiên về chạy đường trường, êm ái và tiết kiệm nhiên liệu hơn. Nếu anh/chị muốn, em có thể tư vấn chi tiết hơn hoặc tra giá, tồn kho loại A/T đang có ạ."

**Ground truth:**
- 255/70R16 H/T: price **1,755,000đ**, qty **351** — EXISTS in corpus (chunk 350, 356)
- 255/70R16 A/T: price **2,079,000đ**, qty **192** — EXISTS in corpus

**Issues:**
1. Bot said "chưa có dữ liệu cụ thể về loại H/T" — this is wrong, H/T data exists at price 1,755,000đ qty 351
2. Did not quote any prices for either type (even A/T which it claims to have data for)
3. Gave good generic H/T vs A/T advice (factually correct domain knowledge), but without the key numbers

**VERDICT: COVERAGE_MISS** — corpus has both H/T and A/T data but bot retrieved only A/T context and failed to surface either price. Generic advice is good but missing the concrete prices the customer would need.

---

## Summary Table

| Scenario | Turns | Verdict | Key Finding |
|---|---|---|---|
| S1 Price-by-size 205/55R16 | 1 | **HALLU** | Fabricated price 972,000đ (real: 1,044,000đ), fabricated qty 338 (real: 819) |
| S2 Multi-turn 185/65R15 T1 | 1 | COVERAGE_MISS | Retrieved no price data; qty wrong (26 vs 527) |
| S2 Multi-turn 185/65R15 T2 | 1 | COVERAGE_MISS | Context lost — answered 235/60R17 instead of 185/65R15 |
| S2 Multi-turn 185/65R15 T3 | 1 | **HALLU** | Fabricated Landspider 1,150,000đ (real: 900,000đ), Rovelo 1,250,000đ (real: 810,000đ) |
| S3 Vague (Vios) T1 | 1 | PASS | Correctly asked for size; appropriate scope |
| S3 Vague (Vios) T2 | 1 | **HALLU** | Same 1,150,000đ / 1,250,000đ fabrication as S2 T3 |
| S4 Brand availability | 1 | PASS | Correctly confirmed Landspider; honest about quality |
| S5 HALLU trap — Michelin | 1 | PASS | Correctly refused, offered alternative |
| S5b HALLU trap — Bridgestone | 1 | PASS | Correctly refused |
| S6 Comparison HT vs AT | 1 | COVERAGE_MISS | Missed H/T data (exists in corpus), no prices cited |

### Score Totals

| Verdict | Count |
|---|---|
| PASS | 4 |
| COVERAGE_MISS | 3 |
| **HALLU** | **3** |
| UX-issue | 0 |

---

## **HALLU COUNT: 3 — SACRED BREACH**

### HALLU #1 — S1 (205/55R16 price)
> Bot: "giá 972.000đ/lốp, hiện còn 338 lốp"  
> Corpus truth (chunk 214): `price: 1044000 | quantity: 819`  
> 972,000 and 338 do not appear anywhere in the corpus for this size.

### HALLU #2 — S2 Turn 3 + S3 Turn 2 (185/65R15 prices — same error, 2 sessions)
> Bot: "LANDSPIDER 185/65R15 giá 1.150.000đ, còn 26 lốp. Rovelo 185/65R15 giá 1.250.000đ, còn 15 lốp"  
> Corpus truth (chunk 125 / chunk 1159):
> - Landspider: `price: 900000 | quantity: 527`
> - Rovelo: `price: 810000 | quantity: 324`  
> The prices 1,150,000 and 1,250,000 are not present anywhere in the corpus.  
> This error is **reproducible across independent sessions** (S2 and S3 both produce the same wrong numbers), indicating a systematic retrieval failure that causes the LLM to confabulate.

---

## Top 3 Issues (Evidence-Backed)

### Issue 1 — Systematic price hallucination for 185/65R15 (P0 severity)
The bot consistently returns 1,150,000đ for Landspider and 1,250,000đ for Rovelo 185/65R15 across independent conversation rooms (xe-s2 and xe-s3). Neither price exists in the corpus. Correct prices (900,000đ / 810,000đ) are present in corpus at chunk_index 125 and 1159 respectively. The retriever is pulling an irrelevant chunk (entity `185/65R15 88H CITYTRAXX G/P`, chunk from old catalog list with date info only, no price field) and the LLM is confabulating numbers to fill the gap.

**Root cause hypothesis:** Retrieval for 185/65R15 is landing on a chunk from the "date list" document (chunk with only `col_2: 28-thg 11` and no price) rather than the structured Q&A chunk at index 125/131. The entity-index entry shows `price_primary: null` for the date-based chunks. The LLM then invents plausible-sounding prices.

### Issue 2 — Price hallucination for 205/55R16 (P0 severity)
Single query for 205/55R16 Landspider returned 972,000đ / 338 units. Corpus has 1,044,000đ / 819 units at chunk 214/220. Retriever picked chunk 379 (an image-link chunk with no price), causing the same confabulation pattern. The correct chunk was NOT retrieved.

**Pattern confirmed:** Retriever is returning catalog-list or image-link chunks instead of the structured Q&A price chunks for these sizes. These "stub" chunks (no price, no quantity) cause the LLM to fabricate rather than say "not found."

### Issue 3 — Multi-turn context loss (P1 severity)
In S2, after asking about 185/65R15, the follow-up "Loại nào rẻ hơn?" caused the bot to answer with 235/60R17 H/T data — a completely different size. The conversation history was not preserved or used correctly. This is a UX-breaking failure for a customer trying to compare options in a single conversation.

---

## Honest Assessment: Is This Bot "Nghiệp vụ-ready"?

**No — not yet.** The bot fails the most critical business requirement: quoting accurate prices and stock.

**What works well:**
- Brand boundary enforcement (refuses Michelin/Bridgestone cleanly — 2/2 HALLU traps passed)
- Vague customer handling (asks for tire size rather than guessing — good UX)
- Professional, natural Vietnamese tone throughout
- Correctly identifies its product scope (Landspider + Rovelo)

**What is broken:**
- Price accuracy: 3 out of 4 price quotes tested were fabricated numbers (not present in corpus). A customer acting on these prices would be misquoted.
- Stock accuracy: every stock number quoted was wrong (338 vs 819; 26 vs 527; 15 vs 324)
- The failure pattern is systematic: retriever picks "header/date" chunks over structured Q&A price chunks → LLM confabulates
- Multi-turn context loss causes irrelevant answers when conversation extends

**For a real tire shop:** Quoting a price 28% to 54% lower than actual to a customer, then correcting at checkout, is a UX disaster and a trust failure. HALLU=3 in 9 turns means a 33% turn-level hallucination rate on price queries. This is not deployable in production without fixing the retrieval ranking to prefer the structured Q&A chunks over raw catalog list chunks.

**Required before go-live:** Fix retriever to prefer chunks with `price:` and `quantity:` fields over date-list chunks when answering price queries. Verify by re-running this test suite — target: HALLU=0, all price/stock queries PASS.
