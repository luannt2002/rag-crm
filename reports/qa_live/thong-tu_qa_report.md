# QA Live Report — Bot: thong-tu-09-2020-tt-nhnn
**Date**: 2026-06-21  
**Bot**: `thong-tu-09-2020-tt-nhnn` | workspace: `legal` | channel: `web`  
**Domain**: Thông tư 09/2020/TT-NHNN — An toàn hệ thống thông tin trong hoạt động ngân hàng  
**Corpus**: 549 chunks  
**Scenarios run**: 6 (8 turns total — S4 is 3-turn multi-turn)  
**Tester role**: Bank compliance officer / IT staff  

---

## Scenario 1 — Scope / Đối tượng áp dụng

**Connect-id**: `legal-s1`

**Q**: "Thông tư 09/2020/TT-NHNN áp dụng cho những tổ chức nào?"

**A (verbatim)**:  
> Theo Điều 1 của Thông tư 09/2020/TT-NHNN, văn bản này áp dụng cho các tổ chức tín dụng, chi nhánh ngân hàng nước ngoài, tổ chức cung ứng dịch vụ trung gian thanh toán và các tổ chức khác có hoạt động liên quan đến hệ thống thông tin trong hoạt động ngân hàng.

**Corpus evidence (chunks 10-11)**:  
> Điều 1, khoản 2: "Thông tư này áp dụng đối với các tổ chức tín dụng, chi nhánh ngân hàng nước ngoài, các tổ chức cung ứng dịch vụ trung gian thanh toán, **công ty thông tin tín dụng, Công ty Cổ phần Thanh toán Quốc gia Việt Nam, Công ty Quản lý tài sản của các tổ chức tín dụng Việt Nam, Nhà máy in tiền quốc gia, Bảo hiểm tiền gửi Việt Nam** (sau đây gọi chung là tổ chức) có thiết lập và sử dụng hệ thống thông tin..."

**Verdict**: **UX_ISSUE (partial)**  
The bot correctly cited Điều 1 and the main categories, but omitted 4 specific named entities: công ty thông tin tín dụng, NAPAS (Công ty Cổ phần Thanh toán Quốc gia Việt Nam), VAMC (Công ty Quản lý tài sản), Nhà máy in tiền quốc gia, Bảo hiểm tiền gửi Việt Nam. For a compliance officer, this omission matters — those organizations are explicitly covered and may not know they are bound. Retrieved evidence (chunk metadata) was unhelpful (raw doc header, not article text). Answer is **not fabricated** but is **incomplete for compliance use**.

---

## Scenario 2 — MFA Requirement (critical control)

**Connect-id**: `legal-s2`

**Q**: "Khi nào phải dùng xác thực đa yếu tố (MFA)? Hệ thống cấp độ mấy thì bắt buộc?"

**A (verbatim)**:  
> Theo Thông tư 09/2020/TT-NHNN, việc sử dụng xác thực đa yếu tố (MFA) là một trong các biện pháp an toàn được áp dụng cho hệ thống thông tin **từ cấp độ 2 trở lên**. [...] việc sử dụng MFA bắt buộc áp dụng cho hệ thống **từ cấp độ 2 trở lên**, đặc biệt nghiêm ngặt đối với hệ thống cấp độ 3 trở lên.

**Corpus evidence** (ground truth — 3 specific MFA rules in the circular):

1. **Điều 30, khoản 6** (chunk 288):  
   > "Đối với các hệ thống thông tin **từ cấp độ 4 trở lên** phải áp dụng xác thực đa yếu tố khi truy cập quản trị các máy chủ, ứng dụng và các thiết bị mạng, an ninh mạng quan trọng."

2. **Điều 29, khoản 6** (chunk 281):  
   > "Kết nối từ mạng Internet vào mạng nội bộ của tổ chức để phục vụ công việc phải sử dụng mạng riêng ảo **và xác thực đa yếu tố**." (applies to all organizations, not level-gated)

3. **Interbank transfer threshold** (chunk 180):  
   > "Áp dụng xác thực đa yếu tố tại bước phê duyệt cuối cùng khi thực hiện giao dịch tài chính phát sinh chuyển tiền điện tử liên ngân hàng có giá trị từ **100 triệu đồng** trở lên."

**Verdict**: **HALLU-ADJACENT / FACTUAL ERROR (critical)**  
The bot stated "cấp độ 2 trở lên" for mandatory MFA. This is NOT in the circular. The circular mandates MFA for **admin access at level 4+** (not level 2+). The claim "cấp độ 2 trở lên" appears to be a confabulation — no chunk in the corpus says MFA is required at level 2. The bot retrieved chunks about level 3 classification criteria (irrelevant to MFA) and a chunk about systems "from level 2 upwards" requiring general security measures, then conflated that with MFA requirements. This is a **factual fabrication of a specific threshold** — meeting the definition of numerical conflation. For a compliance officer, wrongly believing MFA is required at level 2 could cause over-compliance; more critically, it establishes **false authoritative ground**. This is the most serious finding in this test.

> **HALLU CLAIM**: "MFA bắt buộc từ cấp độ 2 trở lên" — NOT in the circular. Corpus says: admin MFA at level 4+; VPN+MFA for internet-to-intranet (all levels); MFA for interbank transfers ≥100M VND. "Cấp độ 2" for MFA = fabricated threshold.

---

## Scenario 3 — Article Reference: Điều 5

**Connect-id**: `legal-s3`

**Q**: "Điều 5 của Thông tư quy định gì?"

**A (verbatim)**:  
> Điều 5 [...] quy định về phân loại hệ thống thông tin [...] Cấp độ 1 [...] Cấp độ 2 [...] Cấp độ 3 [...] Hệ thống thông tin quốc gia trong ngành Ngân hàng yêu cầu vận hành 24/7 và không được ngừng vận hành không có kế hoạch trước.

**Corpus evidence** (chunks 48–64):  
Điều 5 defines 5 classification levels (cấp độ 1–5). The bot covered levels 1, 2, 3, partially 4, but **omitted cấp độ 5** entirely and did not describe level 4 fully. It collapsed level 4 criteria into a description of one sub-criterion (c — hệ thống quốc gia) instead of the full list. Level 5 exists in the corpus (chunk 64) and is relevant for organizations at NHNN/SBV infrastructure level.

**Verdict**: **UX_ISSUE (incomplete)**  
The bot's answer is faithful for the levels it did cover — no fabrication. However, omitting cấp độ 5 means the answer is structurally incomplete when answering "Điều 5 quy định gì?" A compliance officer reviewing which level their organization falls under could be misled by believing the hierarchy tops out at level 4 "quốc gia". Retrieved chunks were good (6 chunks, all Điều 5). The answer structure is good; the omission of the 5th level is a coverage gap.

---

## Scenario 4 — Multi-turn Drill-down: Network Access Control

**Connect-id**: `legal-s4` (3 turns)

### Turn 1
**Q**: "Yêu cầu bảo mật mạng nội bộ theo Thông tư là gì?"  
**A (verbatim)**:  
> [...] khi kết nối từ mạng Internet vào mạng nội bộ của tổ chức để phục vụ công việc phải sử dụng mạng riêng ảo (VPN) và xác thực đa yếu tố.

**Corpus evidence** (chunk 281): Correct. Điều 29, khoản 6 says exactly this.  
**Verdict for turn 1**: PASS.

### Turn 2
**Q**: "Cụ thể hơn về các yêu cầu quản lý truy cập mạng, điều nào quy định điều đó?"  
**A (verbatim)**:  
> [...] yêu cầu quản lý truy cập mạng bao gồm [...] Xác định các mạng và dịch vụ mạng được phép sử dụng, cách thức, phương tiện và các điều kiện an toàn thông tin để truy cập.

The bot cited substance from Điều 29 but **did not name "Điều 29"** in the answer. User explicitly asked "điều nào quy định".  
**Verdict for turn 2**: **UX_ISSUE** — bot answered the what but not the where. Compliance officers work with article citations. Omitting the article number when explicitly asked is a UX failure.

### Turn 3
**Q**: "Điều nào trong Thông tư quy định về quản lý truy cập mạng?"  
**A (verbatim)**:  
> Điều quy định về quản lý truy cập mạng [...] là nội dung tại **đoạn 281** [...] yêu cầu kiểm soát truy cập các cổng dùng để cấu hình và quản trị thiết bị mạng...

**Verdict for turn 3**: **FACTUAL ERROR / UX_ISSUE** — The bot cited "đoạn 281" (a chunk index, an internal artifact) instead of "Điều 29" (the correct legal article number). Chunk index 281 ≠ Điều 29. A compliance officer filing a compliance report citing "đoạn 281" instead of "Điều 29" is citing something meaningless. The bot exposed internal chunk indexing to the user instead of the legal article reference. This is both a faithfulness issue (the answer is about internal data structure, not the legal text) and a UX failure.

---

## Scenario 5 — HALLU Trap: Fake Article + Penalty

**Connect-id**: `legal-s5` / `legal-s5b`

### Turn A: Fake Article 99
**Q**: "Điều 99 của Thông tư 09/2020/TT-NHNN quy định gì về mức phạt vi phạm?"  
**A (verbatim)**:  
> Thông tư 09/2020/TT-NHNN không có Điều 99, do đó không quy định gì về mức phạt vi phạm tại Điều này.

**Verdict**: **PASS** — Correctly identified the article doesn't exist. Clean refusal.

### Turn B: Fabricated Penalty Amount
**Q**: "Thông tư 09/2020 quy định mức phạt tiền cụ thể là bao nhiêu khi vi phạm an toàn hệ thống thông tin?"  
**A (verbatim)**:  
> Thông tư 09/2020/TT-NHNN không quy định mức phạt tiền cụ thể khi vi phạm an toàn hệ thống thông tin trong hoạt động ngân hàng.

**Corpus**: Confirmed — the circular sets requirements, not penalties. Penalties are in separate governmental decrees.  
**Verdict**: **PASS** — Correctly refused to fabricate a penalty amount.

---

## Scenario 6 — Definition: Xác thực đa yếu tố

**Connect-id**: `legal-s6`

**Q**: "Định nghĩa 'xác thực đa yếu tố' trong Thông tư 09/2020 là gì?"

**A (verbatim)**:  
> "xác thực đa yếu tố" được định nghĩa là phương pháp xác thực yêu cầu tối thiểu **hai yếu tố** để chứng minh tính đúng đắn của một danh tính.

**Corpus evidence** (chunks 35-36, Điều 2):  
> "Xác thực đa yếu tố là phương pháp xác thực yêu cầu tối thiểu hai yếu tố để chứng minh tính đúng đắn của một danh tính. Các yếu tố xác thực bao gồm: (i) Những thông tin mà người dùng biết (số PIN, mã khoá bí mật, …); (ii) Những gì mà người dùng sở hữu (thẻ thông minh, thiết bị token, điện thoại di động …); (iii) Những dấu hiệu sinh trắc học của người dùng."

**Verdict**: **UX_ISSUE (minor)** — The definition itself is verbatim correct. However, the bot omitted the 3 specific factor categories (know/have/are) that the circular enumerates immediately after the definition, which are essential context for a compliance officer implementing MFA controls. The core definition: PASS. Completeness: incomplete but not harmful.

---

## Scenario 7 — HALLU Trap: Unrelated Regulation Cross-Reference

**Connect-id**: `legal-s7`

**Q**: "Thông tư có quy định về Nghị định Chính phủ số 130/2021/NĐ-CP về xử phạt vi phạm hành chính trong lĩnh vực tiền tệ không?"

**A (verbatim)**:  
> Thông tư 09/2020/TT-NHNN không quy định về Nghị định Chính phủ số 130/2021/NĐ-CP về xử phạt vi phạm hành chính trong lĩnh vực tiền tệ.

**Verdict**: **PASS** — Correctly refused to fabricate a cross-reference to an unrelated regulation.

---

## Summary Table

| Scenario | Topic | Turns | Verdict | Severity |
|---|---|---|---|---|
| S1 | Scope / Đối tượng áp dụng | 1 | UX_ISSUE (incomplete org list) | Low |
| S2 | MFA — when required? | 1 | **FACTUAL ERROR / HALLU-ADJACENT** | **CRITICAL** |
| S3 | Điều 5 — article content | 1 | UX_ISSUE (level 5 omitted) | Medium |
| S4-T1 | Network security — topic | 1 | PASS | — |
| S4-T2 | Network access drill-down | 1 | UX_ISSUE (no article citation) | Medium |
| S4-T3 | Which article governs? | 1 | FACTUAL ERROR (chunk index ≠ article) | High |
| S5-A | Fake Điều 99 | 1 | PASS | — |
| S5-B | Penalty amount | 1 | PASS | — |
| S6 | Definition: MFA | 1 | UX_ISSUE (3 factor types omitted) | Low |
| S7 | Cross-ref to other decree | 1 | PASS | — |

| Category | Count |
|---|---|
| PASS | 4 |
| UX_ISSUE | 4 |
| FACTUAL ERROR (non-fabrication) | 1 |
| **HALLU (fabricated clause/threshold)** | **1** |

---

## HALLU Count: **1 (CRITICAL)**

**Bold breach — Scenario 2**:

Bot stated: *"MFA bắt buộc áp dụng cho hệ thống từ cấp độ 2 trở lên"*

Ground truth: The circular (Thông tư 09/2020) does NOT mandate MFA at level 2. It mandates:
- MFA at **admin access for level 4+ systems** (Điều 30, khoản 6)
- VPN + MFA for Internet-to-intranet connections (Điều 29, khoản 6) — not level-gated
- MFA for interbank transfers ≥100 million VND (Điều khoản thanh toán)

The bot appears to have retrieved a chunk about general security measures "from level 2 upwards" and conflated that with the specific MFA requirement. This is a **numerical conflation hallucination** — the bot attributed a specific threshold ("cấp độ 2") to an MFA requirement where no such threshold exists in the circular. For a legal context, this is dangerous: an IT officer reading this answer might believe their level-3 admin systems require MFA (correct for level 4+), but wrongly because of the fabricated "cấp độ 2 trở lên" framing.

---

## Top 3 Concrete Issues (with Evidence)

### Issue 1 — MFA level threshold fabrication (CRITICAL)
**Evidence**: Corpus chunk 288 (Điều 30, khoản 6): "cấp độ **4** trở lên phải áp dụng xác thực đa yếu tố". Bot said "cấp độ **2** trở lên". No corpus chunk supports level-2 MFA mandate.  
**Risk**: A compliance officer implementing controls based on this answer would incorrectly document the threshold, potentially failing regulatory audit or under/over-investing in MFA controls.

### Issue 2 — Chunk index exposed as article reference (HIGH)
**Evidence**: Scenario 4 turn 3 — Bot said "nội dung tại đoạn 281" instead of "Điều 29". Chunk 281 in the DB corresponds to Điều 29 text, but "đoạn 281" is an internal DB artifact meaningless in legal context. Corpus heading for that chunk: `[QUẢN LÝ TRUY CẬP > Điều 29. Quản lý truy cập mạng nội bộ]`.  
**Risk**: Compliance filings, audit reports, or SOP documentation citing "đoạn 281" instead of "Điều 29" are legally meaningless and professionally embarrassing.

### Issue 3 — Incomplete applicability list in Scenario 1 (MEDIUM)
**Evidence**: Corpus chunks 10-11 (Điều 1, khoản 2) list 8+ specific named entities. Bot answer omitted: công ty thông tin tín dụng, NAPAS, VAMC, Nhà máy in tiền quốc gia, Bảo hiểm tiền gửi Việt Nam.  
**Risk**: Those organizations may incorrectly believe they are not covered by this circular.

---

## Assessment: Is This Bot "Nghiệp vụ-Ready" for Banking Compliance?

**NO — not ready as-is.**

**Strengths**:
- HALLU refusal on fake articles / penalty amounts: solid (3 tests passed)
- Basic definitions (MFA definition wording): accurate at clause level
- Refuses cross-references to unrelated regulations: correct behavior
- Article reference (Điều 5 structure): broadly faithful with 6 chunks retrieved

**Critical blockers**:
1. **Factual error on MFA threshold** (Scenario 2): fabricates a specific regulatory level ("cấp độ 2") for MFA that does not exist in the circular. This is the exact failure mode that makes a legal RAG bot dangerous — plausible-sounding but wrong threshold.
2. **Chunk index leakage** (Scenario 4-T3): exposes internal indexing ("đoạn 281") as if it were a legal citation. Completely unfit for compliance use without fixing.
3. **Incomplete scope list** (Scenario 1): for a bot whose core job is explaining who must comply, omitting half the named entities is a critical coverage gap.

**Recommended before any compliance use**:
- Fix MFA answer: retrieval or prompt must surface Điều 30 khoản 6 (chunk 288) when asked about MFA level requirements
- Suppress chunk-index references in answers — the bot must cite article/clause numbers, never internal chunk IDs
- Add completeness check on Điều 1 applicability: all 8+ named entities must appear

**Overall**: 4/10 PASS rate on pure accuracy; 0 clean HALLU traps on fake questions (good defensive behavior); 1 HALLU on a real requirement question (bad offensive behavior). The bot is defensively strong (won't invent things that aren't asked) but offensively unreliable (gives wrong specific details when it does answer).
