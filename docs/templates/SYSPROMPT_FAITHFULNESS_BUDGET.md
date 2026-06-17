# Sysprompt Pattern — Faithfulness Budget

> **Intent**: cho bot owner một **clause sẵn-dán** vào `bots.system_prompt` để cấp một "ngân sách" (budget) số claim được **suy luận** so với số claim phải **trích dẫn** — pattern owner-side, lift expected +2-3pp grounded score, không đụng code platform.
>
> **Sacred (CLAUDE.md Quality Gate #10)**: platform **KHÔNG inject** đoạn clause này vào prompt LLM, **KHÔNG override** answer. Bot owner là single source of truth — owner tự copy vào `bots.system_prompt` row của mình. Doc này chỉ để owner đọc.

---

## 1. Concept — Budget là gì

Một câu trả lời của bot có thể chứa nhiều claim (mỗi claim = 1 mệnh đề có thể đúng/sai). Budget pattern phân claim làm 2 loại và đặt **giới hạn cứng** trên loại "rủi ro hơn":

| Loại claim | Nguồn | Đánh dấu | Budget mặc định |
|---|---|---|---|
| **Cited** | trích trực tiếp từ `<documents>` retrieve được | `[1]`, `[2]` (citation marker) | tối thiểu **3** |
| **Inferred** | suy luận / nối ý / khái quát từ chunk | prefix `[Suy luận]` (VN) hoặc `[Inferred]` (EN) | tối đa **1** |
| **Else** | bịa, generalization vô căn | (cấm) | **0** |

Nếu chunk không đủ để đạt ≥3 cited claim → bot **refuse mềm** theo Section 5 sysprompt template thay vì cố lấp bằng inferred.

**Tại sao "budget" hữu ích**: rule "không suy luận" cứng 100% gây over-refuse; rule "suy luận thoải mái" gây HALLU drift. Budget là điểm giữa: cho phép 1 nối-ý có-đánh-dấu để LLM trả tự nhiên, đồng thời lock số claim trích-dẫn để answer luôn neo trên ground truth.

---

## 2. Vì sao expected +2-3pp grounded

Faithfulness literature (RAGAS, TruLens, RAG-Triad) cho thấy:

- Force LLM **đánh dấu rõ** claim nào trích / claim nào suy luận → faithfulness eval tăng vì marker giúp self-check + downstream grader bắt được sai sót.
- Cap số inferred claim ở 1 → giảm bề mặt fabrication ~50-70% so với baseline không cap.
- Floor cited ≥3 → bắt LLM thực sự đọc context thay vì paraphrase 1 chunk duy nhất.

Số "+2-3pp" là **expected lift** dựa trên rationale paper, **chưa verified** trên load test bot thực tế của owner — owner nên A/B trước-sau bằng golden test 30 câu.

---

## 3. Sysprompt clause — drop-in template

Owner copy block dưới đây vào `bots.system_prompt`, đặt ngay trước Section "RESPONSE GROUNDING" trong [SYSPROMPT_TEMPLATE.md](SYSPROMPT_TEMPLATE.md). Thay placeholder, **không** thêm tên brand / industry literal.

### 3.1 VN clause

```
QUY TẮC FAITHFULNESS BUDGET (bắt buộc tuân thủ):

Mỗi câu trả lời của mày PHẢI thoả 3 điều kiện:

1. Có ÍT NHẤT 3 claim trích-dẫn từ <documents>. Mỗi claim đặt
   citation marker `[n]` (n = số chunk trong context).
   Ví dụ: "<dịch vụ> giá <số tiền> [1]."

2. Có TỐI ĐA 1 claim suy luận. Claim suy luận PHẢI bắt đầu bằng
   prefix `[Suy luận]` để user biết đây là nối-ý của bot,
   không phải fact trực tiếp từ tài liệu.
   Ví dụ: "[Suy luận] <sản phẩm> này có thể phù hợp với
   <đối tượng> dựa trên <tiêu chí trong chunk>."

3. KHÔNG được bịa số / tên thương hiệu / cam kết / so sánh
   vượt trội ("tốt nhất", "rẻ nhất", "duy nhất") nếu không có
   chunk nào nói trực tiếp. HALLU = 0 sacred.

Nếu retrieval CHỈ trả ≤2 chunk relevant → mày KHÔNG cố đạt
3 cited claim bằng cách nhồi suy luận. Thay vào đó, refuse
mềm theo Section 5 + CTA hotline.

Nếu user hỏi câu mà budget không đủ thông tin → ưu tiên
trung thực ("Em chưa có đủ dữ liệu chính xác về <chủ đề>")
hơn là cố trả đầy đủ.
```

### 3.2 EN clause

```
FAITHFULNESS BUDGET RULE (mandatory):

Every answer you produce MUST satisfy 3 conditions:

1. Contain AT LEAST 3 cited claims from <documents>. Each
   cited claim ends with citation marker `[n]` (n = chunk
   index in context).
   Example: "<service> costs <amount> [1]."

2. Contain AT MOST 1 inferred claim. Inferred claims MUST
   start with prefix `[Inferred]` so the user knows this is
   your interpretation, not a direct fact from the docs.
   Example: "[Inferred] <product> may suit <audience> based
   on <criteria from chunk>."

3. NEVER fabricate numbers, brand names, commitments, or
   superlatives ("best", "cheapest", "only") unless a chunk
   directly states so. HALLU = 0 is sacred.

If retrieval returns ≤2 relevant chunks → you MUST NOT pad
to 3 cited claims with inferred content. Instead, soft-refuse
per Section 5 + redirect to hotline / human handover.

When the budget is insufficient for an honest answer, prefer
honesty ("I don't have accurate enough data on <topic>") over
forced completeness.
```

### 3.3 Citation marker convention

- `[n]` where `n` is the 1-indexed chunk position in the `<documents>` block. Platform's retrieval node already preserves chunk order; owner does NOT need to manage IDs.
- Multiple citations on one claim: `[1][3]`.
- DO NOT cite by source URL or filename inside the answer — that leaks internal index hygiene to end-user. Use `[n]` only.

---

## 4. Worked example (generic, no brand)

**User question** (VN): "<dịch vụ A> có phù hợp với <đối tượng X> không, giá bao nhiêu?"

**Chunks retrieved** (4 chunk, all relevant):

```
[1] <dịch vụ A> giá khởi điểm <số tiền>, đã bao gồm <hạng mục>.
[2] <dịch vụ A> phù hợp <đặc điểm Y> nhờ <cơ chế Z>.
[3] Buổi đầu kéo dài <thời lượng>, tư vấn miễn phí.
[4] <đặc điểm Y> thường gặp ở <đối tượng X>.
```

**Compliant answer** (theo budget rule):

```
<dịch vụ A> có giá khởi điểm <số tiền> [1], đã bao gồm
<hạng mục> [1]. Dịch vụ này phù hợp với người có <đặc điểm Y>
nhờ <cơ chế Z> [2]. Buổi đầu kéo dài <thời lượng> và miễn phí
tư vấn [3].

[Suy luận] Vì <đối tượng X> thường có <đặc điểm Y> [4], dịch vụ
có thể phù hợp với <đối tượng X>, tuy nhiên cần tư vấn cá nhân
để xác nhận.
```

Phân tích:
- 4 cited claim (≥3, đạt floor): giá [1], hạng mục [1], phù hợp [2], thời lượng [3].
- 1 inferred claim (≤1, đạt cap), prefix `[Suy luận]` rõ, neo trên fact [4].
- 0 fabricate / no superlative / no brand literal → HALLU = 0.

---

## 5. Adoption — owner-side only

1. Owner copy clause Section 3.1 (VN) hoặc 3.2 (EN) vào field `bots.system_prompt` của bot mình quản lý — vị trí **trước Section 4 RESPONSE GROUNDING** của template chung.
2. Owner thay placeholder `<dịch vụ>`, `<sản phẩm>`, `<đối tượng>`, `<chủ đề>` bằng từ generic phù hợp domain bot — **không** thay bằng tên thương hiệu thật trong sysprompt; brand name chỉ xuất hiện trong corpus + `bots.brand_name` field.
3. Owner smoke test 5-10 câu user thường hỏi qua dev chat UI; check answer có:
   - đúng ≥3 marker `[n]`,
   - tối đa 1 prefix `[Suy luận]` / `[Inferred]`,
   - không superlative, không số bịa.
4. A/B với golden test 30 câu (ground truth do owner viết): so sánh grounded score trước-sau khi thêm clause.

**Platform KHÔNG inject clause này** ở bất kỳ orchestrator / pipeline node nào — Quality Gate #10 sacred. Nếu owner không copy → bot vẫn chạy bình thường, chỉ là không có lift expected từ pattern.

---

## 6. Risk + mitigation

| Risk | Triệu chứng | Mitigation |
|---|---|---|
| **Budget quá rộng** (>1 inferred allowed) | Hallucination drift: bot suy diễn nhiều, cited claim ít, user thấy answer "trôi" khỏi context | Lower budget xuống **0** inferred — chỉ trả khi đủ ≥3 cited; câu nào không đủ → refuse mềm. Kèm yêu cầu citation marker stricter. |
| **Budget quá chặt** (cap inferred = 0, floor cited ≥5) | Over-refuse: bot từ chối câu thực ra trả được, user frustration tăng, refuse-rate vượt 30% | Raise budget lên **2** inferred + giữ floor cited ≥3, KÈM yêu cầu mỗi inferred phải có chunk reference (`[Suy luận, theo n]`) để ép neo. |
| **Owner thay marker** (đổi `[Suy luận]` → emoji / từ khác) | Downstream grader không bắt được, eval rớt | Convention `[Suy luận]` / `[Inferred]` đặt trong sysprompt là **hợp lệ**; nhưng nếu đổi, owner phải đồng bộ golden test grader regex. |
| **Bot ignore clause** | LLM trả không có marker dù sysprompt có rule | Đặt clause **gần cuối** sysprompt (recency bias của LLM) + repeat ngắn ở Section 7 jailbreak. |

---

## 7. Reference

- [SYSPROMPT_TEMPLATE.md](SYSPROMPT_TEMPLATE.md) — Section 4 grounding rule (clause này bổ sung cho Section 4, không thay thế).
- CLAUDE.md Quality Gate #10 — application không inject text / không override answer.
- `docs/master/15-O-anti-hallu-tuning.md` — anti-HALLU 4-loại-số sacred.
- RAGAS faithfulness metric — rationale cho cited/inferred split.
