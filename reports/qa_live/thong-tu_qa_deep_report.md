# QA Live Deep Report — Bot: thong-tu-09-2020-tt-nhnn
**Date**: 2026-06-21  
**Bot**: `thong-tu-09-2020-tt-nhnn` | workspace: `legal` | channel: `web`  
**Domain**: Thông tư 09/2020/TT-NHNN — An toàn hệ thống thông tin trong hoạt động ngân hàng  
**Corpus**: 549 chunks  
**This round**: 5 deeper flows, 14 total turns  
**Prior round reference**: `thong-tu_qa_report.md` (8 turns, confirmed 2 critical issues)

---

## STABILITY TABLE

Three substantive questions asked 3 times each with distinct `--connect-id` per run.

| Question | Run 1 Answer (key clause) | Run 2 Answer | Run 3 Answer | Stable? | Verdict |
|---|---|---|---|---|---|
| MFA threshold: "Hệ thống cấp độ mấy thì bắt buộc?" | "từ **cấp độ 2** trở lên" | "từ **cấp độ 2** trở lên" | "từ **cấp độ 2** trở lên" | YES (stably wrong) | INSTABILITY=0 / HALLU=YES (all 3) |
| Scope: "Thông tư áp dụng cho những tổ chức nào?" | Lists: tổ chức tín dụng, công ty tài chính, công ty cho thuê tài chính — omits NAPAS, VAMC, etc. | "không có nội dung chi tiết về các tổ chức cụ thể" (soft refuse) | "không có nội dung chi tiết" (redirects to Điều 1) | NO (run 1 fabricates entities; runs 2-3 deflect) | INSTABILITY (content flip) |
| Network article: "Điều nào quy định về quản lý truy cập mạng?" | "đoạn 278" | "đoạn 278" | "đoạn 278" | YES (stably wrong — chunk index, not article) | CITATION-LEAK=YES (all 3) |

### Stability Notes

**MFA question**: The error is now *stable* — the bot consistently says "cấp độ 2 trở lên". Root cause (from DB trace): retrieval pulls chunk 356 (Điều 37 area — "đối với hệ thống từ cấp độ 2 trở lên, tổ chức thực hiện…") and chunk 55 (Điều 5 level-3 classification). Neither chunk mentions MFA. The bot hallucinates MFA at "cấp độ 2" by conflating a general classification threshold with a specific MFA mandate. Critically, **chunk 288 (Điều 30, khoản 6: "cấp độ 4 trở lên phải áp dụng MFA")** is NEVER retrieved. This is a retrieval-layer failure: the correct chunk does not surface because the semantic embedding of the MFA question matches general security-measure chunks, not the specific MFA clause.

**Scope question**: Answer varies across runs — run 1 *fabricates* entities ("công ty cho thuê tài chính") not present in the circular; runs 2-3 correctly admit they lack the detail and redirect. This constitutes INSTABILITY: the answer content is non-deterministic (fabrication vs. deflection). The correct source is Điều 1, khoản 2 (chunks 10-11), which was not retrieved in any of the 3 runs.

**Network article citation**: Perfectly stable — always says "đoạn 278" instead of "Điều 29". This demonstrates the CITATION-LEAK is deterministic, not random. Chunk 277 (which contains the explicit header `[QUẢN LÝ TRUY CẬP > Điều 29. Quản lý truy cập mạng nội bộ]`) is retrieved but the bot surfaces the chunk index number instead of the article number.

---

## CITATION-FAITHFULNESS CHECK

All chunk-index citations emitted by the bot across both rounds, mapped to the correct legal article.

| Bot citation (verbatim) | Correct legal reference | Corpus evidence | Verdict |
|---|---|---|---|
| "đoạn 281" (prior round S4-T3) | Điều 29, khoản 6 (Quản lý truy cập mạng nội bộ) | Chunk 281 header: `[QUẢN LÝ TRUY CẬP > Điều 29. Quản lý truy cập mạng nội bộ]` | CITATION-LEAK |
| "đoạn 278" (stability runs ×3) | Điều 29 (Quản lý truy cập mạng) | Chunk 277 header: `[QUẢN LÝ TRUY CẬP > Điều 29. Quản lý truy cập mạng nội bộ]` | CITATION-LEAK |
| "đoạn 37" (checklist T1) | Điều 2 (Định nghĩa — yếu tố xác thực) | Chunk 36-37 belong to Điều 2 definitions section | CITATION-LEAK |
| "đoạn 252" (checklist T1) | Điều 27 or 28 (Quản lý truy cập người dùng) | Chunk 252: `[QUẢN LÝ TRUY CẬP > Điều 27...]` — needs confirm | CITATION-LEAK |
| "đoạn 352" (checklist T1) | Điều 36 or similar (third-party compliance) | Chunk 352 is in the compliance section | CITATION-LEAK |
| "đoạn 291" (checklist T3) | Điều 31 (Quản lý kết nối Internet) | Chunk 289 header: `[QUẢN LÝ TRUY CẬP > Điều 31. Quản lý kết nối Internet]` | CITATION-LEAK (but bot correctly also said "Điều 31" — mixed) |

**Total distinct chunk-index leak instances: 6 (across both rounds)**  
**Correct article citation instances: 1 (Điều 31 in checklist T3 — but only because the chunk header was verbatim in the retrieved content)**

**Pattern**: The bot cites chunk indices ("đoạn N") when asked for article citations. When the retrieved chunk's metadata happens to include a `[SECTION > Điều X...]` header verbatim, the bot *sometimes* copies the article number correctly — but reverts to citing the chunk index. This is a **systematic** problem, not edge-case behavior.

---

## FLOW 1 — LISTING: Level-4 Requirements

**Connect-id**: `deep-listing`

**Q**: "Liệt kê các yêu cầu và nghĩa vụ đối với hệ thống thông tin cấp độ 4 theo Thông tư 09/2020."

**A (verbatim)**:
> Thông tư 09/2020/TT-NHNN không cung cấp chi tiết các yêu cầu và nghĩa vụ cụ thể đối với hệ thống thông tin cấp độ 4 trong nội dung được cung cấp. Vì vậy, em chưa thể liệt kê các yêu cầu và nghĩa vụ này theo văn bản.

**Retrieved evidence**: 1 chunk — chunk from "Điều khoản thi hành" (end of document / administrative section). This is completely irrelevant.

**Corpus ground truth — what exists in the corpus for level-4 requirements** (verified via DB query):

| Requirement | Article | Chunk | Text |
|---|---|---|---|
| MFA at admin access | Điều 30, khoản 6 | 288 | "từ cấp độ 4 trở lên phải áp dụng xác thực đa yếu tố khi truy cập quản trị" |
| Secret-data change monitoring | Điều 26, khoản 3 | 242 | "hệ thống thông tin từ cấp độ 4 trở lên" — giám sát cảnh báo thay đổi thông tin bí mật |
| Annual security audit | Điều 42 | 392 | "một năm một lần đối với các hệ thống thông tin cấp độ 4" |
| BCO review annually | Điều 52 | 477 | "một năm một lần đối với hệ thống thông tin từ cấp độ 4 trở lên" |
| File NHN feedback before classification | Điều 5, khoản 8 | 71 | "đối với hồ sơ đề xuất các hệ thống thông tin cấp độ 4, 5, tổ chức gửi hồ sơ cho Ngân hàng Nhà nước" |

**Verdict**: **COVERAGE_MISS (critical)**

The bot answered "không có nội dung" — a complete false-negative. The corpus contains at least 5 distinct requirements scoped to level-4 systems. The retrieval failure was total: only 1 chunk retrieved, completely off-topic. For a compliance officer asking "what must I do for a level-4 system", this answer is dangerously misleading — it implies no level-4-specific requirements exist when in fact there are several mandatory controls including MFA and annual audits.

---

## FLOW 2 — COMPARISON: Level 3 vs Level 4

**Connect-id**: `deep-compare`

**Q**: "Hệ thống cấp độ 3 và cấp độ 4 khác nhau những yêu cầu gì theo Thông tư 09/2020?"

**A (verbatim)**:
> Theo Thông tư 09/2020/TT-NHNN, hệ thống thông tin cấp độ 3 là hệ thống thông tin có một trong các tiêu chí sau, trong đó có hệ thống xử lý thông tin bí mật nhà nước cấp độ Mật (theo Điều 5 Khoản 4a). Tuy nhiên, văn bản trong `<documents>` không cung cấp thông tin chi tiết về yêu cầu cụ thể đối với hệ thống cấp độ 4 hoặc so sánh yêu cầu giữa hệ thống cấp độ 3 và cấp độ 4.

**Retrieved evidence**: 2 chunks — chunk 55 (Điều 5, level-3 classification criteria) and chunk 356 (general "cấp độ 2 trở lên" build-out requirements).

**Corpus ground truth — actual distinctions between level 3 and level 4**:

| Requirement | Level 3 | Level 4 | Article |
|---|---|---|---|
| Physical security monitoring | 24/7 protection required (chunk 155) | 24/7 protection required (inherits) | Điều 19 |
| Power backup | Required (chunk 163, 166) | Required (inherits) | Điều 19 |
| MFA for admin access | NOT required | YES — explicit (chunk 288, Điều 30 khoản 6) | Điều 30 |
| Secret data change alerts | NOT explicitly scoped | YES — "từ cấp độ 4" (chunk 242) | Điều 26 |
| Security audit frequency | Annual for level 3 (chunk 392) | Annual (same — chunk 392) | Điều 42 |
| BCO review frequency | Every 2 years for level 3 and below (chunk 477) | Annual for level 4+ (chunk 477) | Điều 52 |
| NHN consultation before classification | Not required | Required for level 4-5 (chunk 71) | Điều 5, khoản 8 |

**Verdict**: **COVERAGE_MISS (critical)**

The bot again answered "không có nội dung" for level-4, and described level-3 only in terms of its *classification criteria* (not its compliance requirements). The comparison is completely missing. A compliance officer cannot use this answer to understand what additional controls are triggered at level 4. The bot failed to retrieve the key differentiating chunk (288 — MFA) or the BCO frequency distinction (477).

**Conflation risk**: The bot correctly avoided *conflating* level 3 and 4 (it didn't mix their requirements), but only because it refused to answer level 4 at all. The non-answer avoided conflation but is not a passing behavior.

---

## FLOW 3 — COMPLIANCE CHECKLIST (Multi-turn, 3 turns)

**Connect-id**: `deep-checklist`

### Turn 1
**Q**: "Tôi vận hành một hệ thống cấp độ 4, cần tuân thủ những gì về xác thực và truy cập?"

**A (verbatim)**:
> Theo Thông tư 09/2020/TT-NHNN, khi vận hành hệ thống thông tin cấp độ 4, anh/chị cần tuân thủ các quy định về xác thực và quản lý truy cập như sau: Về xác thực, phải sử dụng các yếu tố xác thực bao gồm thông tin người dùng biết (ví dụ số PIN, mã khoá bí mật), thiết bị người dùng sở hữu (thẻ thông minh, thiết bị token, điện thoại di động), và dấu hiệu sinh trắc học của người dùng **(theo đoạn 37)**. Về quản lý truy cập, tổ chức phải quy định quản lý truy cập đối với người sử dụng, nhóm người sử dụng, các thiết bị và công cụ truy cập hệ thống thông tin, đảm bảo đáp ứng yêu cầu nghiệp vụ và an toàn thông tin **(theo đoạn 252)**. Ngoài ra, với hệ thống cấp độ 3 trở lên, bao gồm cấp độ 4, nếu sử dụng dịch vụ điện toán đám mây hoặc có bên thứ ba tham gia, phải đánh giá sự tuân thủ các quy định về bảo đảm an toàn thông tin của bên thứ ba theo đúng thỏa thuận **(theo đoạn 352)**.

**Retrieved evidence**: chunks 37 (Điều 2 — MFA definition/factor types), 252 (access management general), 352 (third-party compliance cấp độ 3+).

**Corpus faithfulness check**:
- The answer describes MFA *factor types* (what MFA is) but **does NOT state MFA is mandatory for level-4 admin access** (the actual obligation from Điều 30 khoản 6, chunk 288). It answers "what are the components of authentication" not "what authentication is required".
- Citation "đoạn 37" = Điều 2 (definition section). Correct substance, wrong citation form.
- Citation "đoạn 252" = general access management. The answer is not wrong, but it doesn't specify the level-4 specific requirement (MFA).
- Citation "đoạn 352" = third-party compliance for level 3+. Relevant and correct.
- **Critical miss**: The answer NEVER mentions "xác thực đa yếu tố khi truy cập quản trị" as the specific level-4 obligation. It describes MFA factors but not when/where MFA is mandated at level 4.

**Verdict for Turn 1**: **COVERAGE_MISS + CITATION-LEAK (3 chunk indices)**  
The bot gives generally true information but misses the core level-4-specific MFA obligation. It cites 3 chunk indices instead of article numbers.

---

### Turn 2
**Q**: "Điều khoản nào quy định việc đó? Cho tôi biết số điều cụ thể."

**A (verbatim)**:
> Thông tư 09/2020/TT-NHNN không cung cấp nội dung cụ thể về điều khoản hoặc số điều liên quan đến đoạn 150 mà anh/chị hỏi. Vì đoạn 150 trong tài liệu chỉ ghi số "3" mà không có nội dung chi tiết nào khác nên không thể xác định được điều khoản hoặc số điều cụ thể quy định việc đó.

**Retrieved evidence**: 1 chunk — chunk 150 (content: just the number "3" — an artefact chunk from the document parse).

**Analysis**: This is a **severe multi-turn context degradation failure**. 
1. The user asked "điều khoản nào quy định việc đó?" — referring back to the previous turn about authentication/access requirements.
2. The bot misread this as a question about "đoạn 150" — which was never mentioned. This is a conversational context tracking failure (possibly hallucinating a prior message that didn't exist, or failing to resolve the pronoun "đó").
3. The retrieval surfaced chunk 150 which contains literally only the number "3" — a parsing artifact from the circular's page or section numbering. The bot then tried to answer based on this meaningless chunk.
4. The user explicitly asked for "số điều cụ thể" (specific article numbers) — the bot answered with confusion about a non-existent "đoạn 150" question.

**Verdict for Turn 2**: **MULTI-TURN FAILURE (severe) + phantom chunk artifact surfaced**  
The bot lost conversational context, invented a reference to "đoạn 150" (which no one mentioned), and retrieved a junk chunk. This is a new critical failure mode not seen in the prior round.

---

### Turn 3
**Q**: "Còn về quản lý truy cập thì sao? Điều nào quy định?"

**A (verbatim)**:
> Về quản lý truy cập, Thông tư 09/2020/TT-NHNN quy định tại **Điều 31** về Quản lý kết nối Internet trong tổ chức, trong đó tổ chức phải quy định và triển khai việc quản lý kết nối Internet đáp ứng các yêu cầu cụ thể (theo Điều 31).

**Retrieved evidence**: chunk 291 — header `[QUẢN LÝ TRUY CẬP > Điều 31. Quản lý kết nối Internet]`.

**Analysis**:
- The bot correctly cited "Điều 31" (article number, not chunk index) — because the retrieved chunk's header explicitly labels it "Điều 31".
- However, Điều 31 is about *internet connection management*, not the general access management framework. The primary access management article is Điều 27 (Quản lý truy cập người dùng) and Điều 29 (Quản lý truy cập mạng nội bộ). Retrieval surfaced the wrong article.
- **Narrow correct**: The bot correctly said "Điều 31" (article form, not chunk index). But Điều 31 is not the main access management article — it governs internet connections specifically.

**Verdict for Turn 3**: **PARTIAL — correct article form but wrong article for the question**

---

## FLOW 4 — HALLU RE-TRAP

### H1: Fake Điều 99

**Q**: "Điều 99 của Thông tư 09/2020/TT-NHNN quy định gì về mức phạt vi phạm?"

**A**: "Thông tư 09/2020/TT-NHNN không có Điều 99, do đó không quy định gì về mức phạt vi phạm tại Điều này."

**Verdict**: **PASS** — Clean refusal. Correctly identifies non-existent article.

### H2: Fabricated Penalty Amount

**Q**: "Thông tư 09/2020 quy định mức phạt tiền cụ thể là bao nhiêu khi vi phạm an toàn hệ thống thông tin?"

**A**: "Thông tư 09/2020/TT-NHNN không quy định mức phạt tiền cụ thể khi vi phạm an toàn hệ thống thông tin trong hoạt động ngân hàng."

**Corpus**: Confirmed — penalties are in separate governmental decrees, not in this circular.  
**Verdict**: **PASS** — Correct. No fabricated penalty.

### H3: Unrelated Decree (NĐ 13/2023/NĐ-CP)

**Q**: "Thông tư 09/2020 có tham chiếu đến Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân không? Nó quy định gì cụ thể?"

**A**: "Thông tư 09/2020/TT-NHNN không có nội dung tham chiếu đến Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân và không quy định gì cụ thể về Nghị định này."

**Note**: NĐ 13/2023 post-dates the circular (2020). Circular references only NĐ 85/2016 and similar instruments.  
**Verdict**: **PASS** — Correct refusal. No cross-reference fabricated.

---

## CORPUS EVIDENCE SUMMARY

Ground-truth chunks that the bot FAILED to retrieve across flows where they were relevant:

| Chunk | Article | Content (key clause) | Should retrieve for question |
|---|---|---|---|
| 288 | Điều 30, khoản 6 | "cấp độ 4 trở lên phải áp dụng MFA khi truy cập quản trị" | MFA threshold (stability ×3), listing, comparison, checklist T1 |
| 242 | Điều 26, khoản 3 | "cấp độ 4 trở lên — giám sát cảnh báo thay đổi thông tin bí mật" | Listing, comparison |
| 477 | Điều 52 | "một năm một lần đối với cấp độ 4 trở lên" | Listing, comparison |
| 10-11 | Điều 1, khoản 2 | Full list of 8+ named entities covered by the circular | Scope question ×3 |
| 275 | Điều 29 header | "Điều 29. Quản lý truy cập mạng nội bộ" | Network article question ×3 |

---

## FULL SCENARIO SUMMARY TABLE

| Scenario | Flow | Turns | Verdict | Severity |
|---|---|---|---|---|
| Stability: MFA threshold ×3 | Stability | 3 | **HALLU (stably wrong: "cấp độ 2")** | **CRITICAL** |
| Stability: Scope ×3 | Stability | 3 | INSTABILITY (run 1 fabricates; 2-3 deflect) | High |
| Stability: Network article ×3 | Stability | 3 | CITATION-LEAK ×3 ("đoạn 278" ≠ Điều 29) | High |
| Listing: Level-4 requirements | Listing | 1 | COVERAGE_MISS ("không có nội dung") | **CRITICAL** |
| Comparison: Level 3 vs 4 | Comparison | 1 | COVERAGE_MISS (level-4 side empty) | **CRITICAL** |
| Checklist T1: Auth + access for level 4 | Checklist | 1 | COVERAGE_MISS + CITATION-LEAK ×3 | High |
| Checklist T2: "Which article?" | Checklist | 1 | **MULTI-TURN FAILURE** (phantom "đoạn 150") | **CRITICAL** |
| Checklist T3: Access management article | Checklist | 1 | PARTIAL (correct form Điều 31, wrong article) | Medium |
| HALLU H1: Fake Điều 99 | HALLU trap | 1 | PASS | — |
| HALLU H2: Penalty amount | HALLU trap | 1 | PASS | — |
| HALLU H3: Unrelated decree | HALLU trap | 1 | PASS | — |

---

## SUMMARY COUNTS

| Category | Count | Notes |
|---|---|---|
| PASS | 3 | All 3 HALLU traps — defensive refusal works |
| COVERAGE_MISS | 3 | Listing + Comparison + Checklist T1 |
| **HALLU (fabricated threshold)** | **3** | MFA stability runs — "cấp độ 2" every time |
| INSTABILITY | 1 set | Scope: run 1 fabricates "công ty cho thuê tài chính" not in corpus |
| CITATION-LEAK | 6 total instances | "đoạn N" used instead of "Điều X" across both rounds |
| MULTI-TURN FAILURE | 1 | Checklist T2: phantom "đoạn 150" context loss |
| PARTIAL | 1 | Checklist T3: right form, wrong article |

**HALLU count (this round): 3** (all same question, same error — MFA threshold at "cấp độ 2")  
**HALLU count across both rounds: 4** (3 new + 1 prior — same root cause)

---

## ROOT CAUSE ANALYSIS

### Issue 1 — MFA threshold HALLU (retrieval miss + conflation)
**Evidence**: Every MFA query retrieves chunk 356 ("cấp độ 2 trở lên, tổ chức thực hiện…" — general build-out requirements) instead of chunk 288 ("Điều 30 khoản 6 — cấp độ 4 trở lên phải áp dụng MFA"). The semantic embedding of "xác thực đa yếu tố, cấp độ mấy" matches the "cấp độ 2" general-requirements chunk more closely than the specific Điều 30 MFA clause. The LLM then conflates the retrieved "cấp độ 2 trở lên" phrase with MFA applicability.

**Layer**: Retrieval-layer failure (wrong chunk ranked top). NOT an LLM or prompt problem — the LLM is faithfully synthesizing from wrong retrieved content.

### Issue 2 — Chunk-index citation (CITATION-LEAK)  
**Evidence**: 6 confirmed instances. Bot emits "đoạn N" (internal DB chunk index) as if it were a legal citation. The bot appears to use the narrated chunk header ("Đoạn 278 thuộc phần...") as a citation anchor. Article numbers appear in chunks *only when the chunk header includes `[SECTION > Điều X...]`*, and only then does the bot occasionally use the article form.

**Layer**: Answer-generation layer. The LLM is using the chunk's narrated index from the RAG context. Fix: strip "Đoạn N thuộc phần..." narrated headers from retrieved content before placing in prompt, or inject article number directly.

### Issue 3 — Multi-turn context loss (Checklist T2)
**Evidence**: Turn 2 of checklist ("Điều khoản nào quy định việc đó?") was answered as if the user asked about "đoạn 150" — a reference that existed nowhere in the conversation. The retrieval query apparently disregarded the conversational context and surfaced a junk chunk (content: "3"). The bot then "answered" based on that phantom context.

**Layer**: Orchestration/query-construction layer. The conversational history was either dropped or misinterpreted when forming the retrieval query for turn 2.

---

## TOP 3 ISSUES WITH EVIDENCE

### Issue 1 — MFA threshold HALLU: "cấp độ 2" fabricated, "cấp độ 4" never surfaced (CRITICAL)
- **Evidence**: Corpus chunk 288 (Điều 30, khoản 6): *"Đối với các hệ thống thông tin từ **cấp độ 4** trở lên phải áp dụng xác thực đa yếu tố khi truy cập quản trị…"* Bot said "**cấp độ 2** trở lên" in 4 out of 4 test runs (1 prior round + 3 this round).
- **Root cause**: Retrieval pulls chunk 356 ("cấp độ 2 trở lên") — a general compliance initiation clause — and the LLM conflates that level with the MFA requirement. Chunk 288 (the correct chunk) is never retrieved.
- **Risk**: A banking IT officer implementing this guidance would apply MFA to level-2 admin systems (over-compliance) AND believe their level-3 admin MFA is covered. They would NOT know MFA is a specific level-4+ obligation under Điều 30 — the actual regulatory hook auditors check. Regulatory audit failure exposure.

### Issue 2 — Chunk-index citation leak: "đoạn N" instead of "Điều X" (HIGH)
- **Evidence**: 6 confirmed instances: "đoạn 278" (should be Điều 29), "đoạn 281" (Điều 29 khoản 6), "đoạn 37" (Điều 2), "đoạn 252", "đoạn 352", "đoạn 291" (correctly also said Điều 31 in one case).
- **Root cause**: The narrated chunk summaries begin with "Đoạn N thuộc phần…" — the LLM picks this as the citation anchor. The article number exists in the structured chunk header `[SECTION > Điều X.]` but the LLM prefers the narrated label.
- **Risk**: Compliance documentation citing "đoạn 278" instead of "Điều 29" is legally meaningless. These internal indices have no standing in regulatory filings or audit responses.

### Issue 3 — Coverage failure on level-4 specific queries: bot answers "không có nội dung" (CRITICAL)
- **Evidence**: Two separate level-4 queries (FLOW 1 listing, FLOW 2 comparison) both returned "không có nội dung cụ thể" for level-4 requirements. At least 5 level-4 specific requirements exist in the corpus: Điều 30 khoản 6 (MFA), Điều 26 khoản 3 (secret-data monitoring), Điều 42 (annual audit), Điều 52 (annual BCO review), Điều 5 khoản 8 (NHN consultation).
- **Root cause**: Retrieval does not surface level-4 specific chunks when the query is broad ("liệt kê yêu cầu cấp độ 4"). The query matches classification-criteria chunks (Điều 5) rather than the scattered level-4-specific obligation clauses.
- **Risk**: A compliance officer asking "what do I need to do for my level-4 system" gets "nothing" — the worst possible answer. They would incorrectly conclude no specific level-4 obligations exist.

---

## VERDICT: IS THIS BOT SAFE FOR BANKING COMPLIANCE USE?

**NO — not safe as-is. Downgraded from prior round: MORE dangerous than initially assessed.**

Prior round showed 1 HALLU (MFA threshold). This round shows:
- The HALLU is *stable* (not a fluke — systematic retrieval miss)
- Level-4 specific queries universally return false negatives ("không có nội dung")  
- Multi-turn conversations degrade: context is lost, junk chunks surface
- Citation leakage is systematic (6 instances, not edge-case)

**Defensive behavior (what works)**:
- Fake article refusal: 3/3 clean PASS
- Penalty fabrication refusal: PASS
- Unrelated decree cross-reference: PASS
The bot will not invent things when it has no retrieval signal. This is good but insufficient.

**Offensive behavior (what fails)**:
- When asked about real obligations for level-4 systems: either gives wrong threshold (MFA at level 2) or gives a false negative (no requirements found)
- Multi-turn context tracking: fails after turn 1
- Article citations: systematically uses internal chunk indices

A banking compliance officer using this bot to prepare for an SBV audit would:
1. Document MFA as required at level 2 (wrong — it's level 4)
2. Believe no specific level-4 obligations exist (wrong — several mandatory controls)
3. Cite "đoạn N" instead of "Điều X" in audit reports (legally meaningless)

**Overall score**: 3 PASS / 8 FAIL (counting each flow verdict). HALLU rate on real compliance questions: 4/4 on MFA question (100%). Coverage rate on level-4 queries: 0/2 (0%).
