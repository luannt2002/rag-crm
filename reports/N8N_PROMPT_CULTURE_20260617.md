# N8N Prompt-Writing Culture — Analysis for RAG Conversion

**Source**: `z-luannt-prompt-n8n.txtx` (5392 lines, 11 distinct n8n/ChatGPT system-prompts concatenated, separated by `vs`).
**Date**: 2026-06-17. **Scope**: extract the "n8n prompt culture" to plan conversion of these bots onto the ragbot RAG platform.

Every claim below cites a line number or short quote from the file.

---

## 1. Inventory of distinct bots

| # | Bot name | Purpose (1-line) | Line range |
|---|---|---|---|
| 1 | **Beespace (v1)** — coworking/office/apartment CSKH | Tư vấn coworking + serviced office + homestay, thu thập nhu cầu, báo giá có điều kiện, special-date lockdown | 1–246 |
| 2 | **Reborn (Bác sĩ Hoàng Ngọc Hiếu)** — physiotherapy clinic | Tư vấn cơ-xương-khớp theo kịch bản cố định, gom SĐT, đẩy khách tới phòng khám | 251–384 |
| 3 | **UP Garden Hotel (v1)** — hotel booking | Đặt phòng, check phòng trống, booking + thanh toán, upsell | 389–592 |
| 4 | **UP Garden Hotel (v2)** — hotel booking, 2-doc | Bản sửa: đọc **2 tài liệu riêng** "Bảng giá phòng" + "Tình trạng phòng" | 597–775 |
| 5 | **KDExpress** — Canada↔Vietnam logistics, bilingual | Tư vấn cước/loại hàng/phụ phí, auto-detect VI/EN, dịch trung thực, heavy anti-misread | 781–1029 |
| 6 | **Gobe Việt Nam (v1)** — bathroom/kitchen equipment sales | Báo giá sỉ/lẻ, thu thập đơn hàng, xác nhận | 1033–1091 |
| 7 | **Beetech Academy** — IT Comtor course consulting (JP) | Bắt buộc thu thập 2 info trước khi trả lời; phân nhánh đã-biết/chưa-biết; xin SĐT theo thứ tự | 1097–1397 |
| 8 | **DAO Carton** — carton/foam/tape sales | 8 kịch bản; chuẩn hóa kích thước; bắt buộc hỏi số lượng trước khi báo giá | 1401–1562 |
| 9 | **Gobe Việt Nam (v2)** — equipment sales | Bản sửa: thêm danh mục sản phẩm, kiểm tra tồn kho, gửi ảnh khi yêu cầu | 1566–1645 |
| 10 | **CBL.VN** — IT/camera/projector retail, DB-tool | Gọi `get_product_info` tool, field-name mapping (`cf_1210`/`unit_price`), VAT compute, stock filter | 1648–1805 |
| 11 | **Quang Phúc** — HDPE tarp / geo-membrane pricing | Heavy computational: parse D×R(×S), 8m hard-gate, area formulas, table-lookup, shipping calc, VAT | 1809–3065 |
| 12 | **CENTREC (ĐH Cần Thơ)** — course/product/textbook consulting | Spreadsheet column-mapping (A/C/D/G/J/K/L/N), 2-case material disambiguation, lock-in active_course | 3068–3399 |
| 13 | **Beespace (v2)** — multilingual (VI/EN/JP) | Bản nâng cấp: language-lock, 3 data tables, Tết price hard-lock, special-date absolute block, locked phone template | 3402–3955 |
| 14 | **Dr. Medispa** — beauty/spa sales (largest) | ~24 hardcoded service scripts (info/price/process/ok branches), intent-gate, slot-filling booking, state vars | 3958–5021 |
| 15 | **Fine Mold Việt Nam** — recruitment assistant | Hardcoded position list, tool query for "đang tuyển", structured JSON output, interview scheduling | 5024–5392 |

Note: 15 prompts though some are v1/v2 of the same brand (Beespace ×2, UP Garden ×2, Gobe ×2). Distinct businesses ≈ 11.

---

## 2. Common patterns / culture

### 2.1 Structure — heavily step/branch driven

Near-universal `<step>` blocks with named Vietnamese sub-steps and explicit `BƯỚC N` / `NHÁNH A/B` branching:

- Beespace v1 `<step>` has 7 named sub-blocks: `<Chào hỏi khách hàng>`, `<Hỏi thông tin chi tiết>`, `<Cung cấp tư vấn dịch vụ>`, `<Chuyển tư vấn viên>` ... (lines 21–134).
- Greeting → qualify → answer/handoff is the canonical shape: KDExpress `<Chào hỏi và giới thiệu>` → `<Hỏi thông tin khách hàng cần>` → `<Tra cứu CHÍNH XÁC và tư vấn>` (lines 864–900).
- Beetech uses 9 numbered `BƯỚC` with hard branch `NHÁNH A: KHÁCH CHƯA BIẾT` vs `NHÁNH B: KHÁCH ĐÃ BIẾT` (lines 1178–1237).
- Fine Mold is a 9-step linear funnel BƯỚC 1→9 (lines 5113–5359).
- Dr. Medispa has a formal `4B) CHAIN-OF-THOUGHT` gate classifying every message into LOẠI A/B/C before responding (lines 4099–4182).

**Culture**: prompts are written like flowcharts/state-machines, not like persona descriptions. Many encode explicit state variables: Dr. Medispa `<6) BIẾN TRẠNG THÁI HỘI THOẠI>` declares `booking_confirmed`, `selected_service`, `origin_intent`, `asked_condition_once`, `booking_info_collected` (lines 4435–4465). CENTREC has `active_course`/`active_product`/`active_material` lock-in (lines 3099–3110).

### 2.2 DATA handling — split between INLINE-baked and external-reference

Two distinct habits coexist, sometimes in the same bot:

**(a) Data BAKED INLINE into the prompt** (anti-pattern for RAG):
- Beespace v1 hardcodes addresses: *"Cơ sở Nguyễn Trãi: tầng 4, toà H10, số 2, ngõ 475, Nguyễn Trãi..."* (lines 92–94), and a literal price table *"Standard: 900.000/phòng/đêm + Superior: 1.050.000/phòng/đêm (diện tích 23m2) + Deluxe: 1.200.000/phòng/đêm"* (lines 74–76).
- KDExpress bakes the whole KB: warehouse addresses + phone (lines 1004–1006), rate `$14.8 CAD/kg`, min 3kg, volumetric `W×L×H ÷ 6000` (lines 1008–1012), transit times `Hà Nội: 5-7 ngày` (lines 1014–1017).
- Beespace v2 Tết prices hardcoded `Standard 1.200.000 / Superior 1.400.000 / Deluxe 1.600.000` (line 3705).
- Dr. Medispa bakes ~24 full service scripts with prices inline e.g. *"giá chỉ 199k/buổi (giá gốc 700K/buổi)"* (line 4514), *"99K/buổi (giá gốc: 400k/buổi)"* (line 4721) — the prompt IS the price list.

**(b) External data REFERENCE** ("tra tài liệu", sheet/column names):
- Beespace v1: *"Mọi thông tin/giá phải TRÍCH XUẤT TỪ TÀI LIỆU; không suy diễn/khái quát"* (line 66); *"Đọc đúng dữ liệu từ cột A (Danh sách sản phẩm), xác định cơ sở ở cột B/C/D"* (line 116).
- UP Garden v2 explicitly reads **two separate documents**: *"Bảng giá phòng → để lấy giá, tiện ích, ảnh. Tình trạng phòng → để lấy số lượng phòng còn/hết theo ngày"* (lines 601–604; logic at 691–722).
- DAO Carton references sheet names: *"Hộp carton → Sheet 'Hop_carton' hoặc 'Hộp nắp gài' / Xốp → Sheet 'Xop-chong-soc'"* (lines 1447–1449).
- CBL.VN references a tool + DB columns: `get_product_info(productname, field_name)` and `<database_structure>` listing `cf_1210`, `unit_price`, `cf_852`... (lines 1666–1681).
- CENTREC `<columns_mapping>`: *"Lịch khóa học: A=Tên • C=Khai giảng ... K=Lịch buổi/khung giờ • L=Học phí • N=Hình thức"* (lines 3271–3274).

**Culture insight**: even the "reference external" bots assume a **Google-Sheet mental model** — they name sheets, columns (A/B/C, cf_xxxx), and rows. Several embed live Google Sheets/Drive/Docs URLs directly (lines 384, 503, 588, 590, 1644–1645, 3078). The retrieval model is "read cell at intersection of row=product, col=facility", not "retrieve semantically relevant chunk".

### 2.3 Hard-rule style — extremely heavy

`BẮT BUỘC` / `TUYỆT ĐỐI KHÔNG` / `CẤM TUYỆT ĐỐI` / `QUY TẮC VÀNG` / `NGHIÊM CẤM` appear constantly, often with emoji emphasis (🚫⚠️🔴⛔):

- Beespace v1 `<prohibited_actions>` is a wall of `Không...` rules (lines 171–184); `<pricing_guidelines>` repeats `TUYỆT ĐỐI KHÔNG` 5× (lines 137–143).
- Beetech `<response_rules>` has `🚫 CẤM TUYỆT ĐỐI` list (lines 1300–1307) and `QUY TẮC VÀNG` (line 1113).
- Quang Phúc has a `# RULE OVERRIDE — CỬA CHẶN 8M (ƯU TIÊN CAO NHẤT, CHẠY TRƯỚC MỌI THỨ)` with pseudo-code `IF (D>8 AND R>8) → STOP` (lines 1842–1869), plus a printed `CHECKLIST TRƯỚC KHI TRẢ LỜI` with checkboxes (lines 2979–3014).
- Dr. Medispa: `⛔ CẤM TUYỆT ĐỐI ... VI PHẠM NÀY LÀ LỖI NGHIÊM TRỌNG` with ❌SAI/✅ĐÚNG examples (lines 4006–4014).

The same rule is frequently **restated 3–6 times** across `<step>`, `<pricing_rules>`, `<prohibited_actions>`, `<note>`, `<special_rules>` (e.g. Beespace v1 special-date lockdown repeats in lines 41–48, 78–82, 96, 106–110, 120, 143, 167–168, 183, 201, 207, 228–245). This is defensive prompt-engineering against an unreliable single LLM call.

### 2.4 Conversation/sales flow vs pure Q&A — overwhelmingly SALES

Almost none are pure Q&A. The dominant goal is **lead-capture / booking / close**:

- Reborn: *"Mục tiêu: đưa khách đến phòng khám"* + *"Luôn yêu cầu số điện thoại"* (lines 377–381).
- Beetech: contact-collection ladder `📞 XIN SĐT → 💬 ZALO → 📧 EMAIL` (lines 1214–1225).
- Dr. Medispa: *"Luôn dẫn dắt khách tới bước đặt lịch hẹn tại spa"* (line 4019); full slot-filling booking BƯỚC 4–5 (lines 4923–4983).
- Hotel/Carton/Gobe: order capture + payment instructions (UP Garden bank info lines 465–469; Quang Phúc bank info lines 2446–2449).
- Fine Mold: recruitment funnel ending in interview booking + JSON record (lines 5341–5389).

Gating answers behind data-collection is explicit: Beetech *"TUYỆT ĐỐI KHÔNG trả lời về khóa học trước khi có đủ 2 thông tin cơ bản"* (line 1103); Beespace v1 Office: *"CHỈ KHI có SĐT/Zalo mới được phép báo GIÁ"* (line 18).

### 2.5 Response-format rules — short, no markdown, one question per turn

Consistent across nearly all:

- *"Mỗi lượt chỉ hỏi một câu"* / *"Mỗi lần trả lời chỉ hỏi một câu duy nhất"* (Beespace lines 191, 195; Beespace v2 line 3606; Dr. Medispa "1–2 câu ngắn" line 3983).
- *"không dùng ký tự hoặc định dạng đặc biệt"* / *"Không dùng emoji, markdown"* (lines 191, 564, 1084, 1793).
- Length caps: Quang Phúc *"TỐI ĐA 2-3 DÒNG mỗi câu trả lời"* (lines 1814, 1897); Beetech free-response *"Tối đa 100 từ"* (line 1264); CENTREC *"Ưu tiên tin <30 từ"* (line 3375).
- **Inconsistency**: a few DO use emoji (Beetech 🥰 line 1140; Fine Mold 😊 line 5121; Dr. Medispa uses 🚫⚠️ in rules but forbids them in output).

### 2.6 Multi-document handling — yes, several read "2+ tài liệu riêng"

- **UP Garden v2** is the clearest: ChatGPT meta-text *"mình đã chỉnh sửa lại system prompt để bot biết cách đọc dữ liệu từ hai tài liệu riêng: Bảng giá phòng ... Tình trạng phòng"* (lines 600–604), with `<room_availability_logic>` and `<price_info_logic>` each declaring a `Nguồn dữ liệu` (lines 691–722).
- **Beespace v2** reads 3 tables: "Thông tin sản phẩm" + "Trạng thái phòng" + "FAQ", each with language variants VN/ENG/JP (lines 3483–3511, 3530–3534).
- **Quang Phúc** routes across many: "Bảng giá bạt HDPE", "Bảng giá hàn bạt HDPE", "Bảng giá vận chuyển Viettel", "Bảng giá vải địa kỹ thuật", FAQ (lines 1816–1823) with `<knowledge_routing>` priority order (lines 1825–1836).
- **CENTREC** disambiguates 2 material cases: textbook in "Danh mục giáo trình" vs course-material in "Lịch khóa học" — *"TUYỆT ĐỐI KHÔNG được nhầm 2 case này"* (lines 3162–3188).
- **Dr. Medispa** has 3 docs [TL-1/2/3] but routes most queries to inline scripts, only tra-tài-liệu for non-scripted services (lines 4054–4096).

### 2.7 Anti-hallucination / "only from data" — strongly present everywhere

This is the single most consistent cultural value:

- *"Không bịa thông tin"* / *"KHÔNG TỰ BỊA"* recurs ~everywhere (lines 90, 198, 1090, 3100, 3610...).
- *"CHỈ báo giá khi có thông tin CHÍNH XÁC 100% trong tài liệu"* + ban on computing price (Beespace lines 138–141).
- KDExpress: *"SAO CHÉP NGUYÊN VĂN nếu cùng ngôn ngữ / DỊCH TRUNG THỰC nếu khác ngôn ngữ"*, *"KHÔNG CHẾ BIẾN nội dung"* (lines 858–859, 931–934), double-read to avoid misreading numbers (lines 974–979).
- Label-unverified convention: *"Mọi nội dung chưa được xác minh phải được dán nhãn [Suy luận], [Phỏng đoán], [Chưa xác minh]"* (Beespace line 214; KDExpress bilingual lines 963–965).
- Refusal-without-revealing-source: DAO Carton *"KHÔNG được nói 'cơ sở dữ liệu không có' ... Luôn nói: 'để em kiểm tra lại với chuyên viên'"* (lines 1427, 1547); Dr. Medispa *"TUYỆT ĐỐI không tiết lộ rằng thông tin lấy từ tài liệu"* (line 4024); Beespace v2 *"KHÔNG nói 'trong tài liệu không có'"* (line 3612).
- Fine Mold: *"Không được tự suy đoán trạng thái tuyển dụng"*, *"Có data ≠ đang tuyển"* (lines 5081, 5090).

**Counter-current**: the anti-hallu rules paradoxically force the model to do **exact arithmetic and table-cell lookup** (Quang Phúc area formulas, CBL VAT %, hotel night×price totals) — tasks LLMs are bad at — instead of forbidding them.

---

## 3. Anti-patterns that BREAK on a RAG platform

Given ragbot constraints: (a) KB lives in a corpus and is **retrieved, not baked**; (b) platform must stay **domain-neutral**; (c) **HALLU=0** enforced by grounding.

**A1. KB data baked inline in the prompt.**
Inline prices/addresses/tables (Beespace lines 74–76, 92–94; KDExpress lines 1004–1017; Dr. Medispa's 24 inline price scripts; Beespace v2 Tết prices line 3705) become **stale and unretrievable**. On RAG these belong in the corpus; baking them means the system_prompt grows huge, drifts from the corpus, and the grounding check can't verify them (they're not in retrieved chunks). Dr. Medispa's prompt is effectively the database — irreconcilable with retrieval.

**A2. Spreadsheet column/cell addressing as the retrieval model.**
"Đọc cột A, cơ sở ở cột B/C/D" (line 116), `cf_1210`/`unit_price` field names (lines 1666–1681), CENTREC `A=Tên • C=Khai giảng ... L=Học phí` (lines 3271–3274), "Row 1 = đang tuyển" (line 5087). RAG retrieves **chunks of text**, not cells at (row,col). Positional addressing has no meaning post-chunking; the LLM cannot "read column K". This must become either structured-data lookups or self-describing corpus text.

**A3. Application/prompt forcing arithmetic and exact-number transforms.**
Quang Phúc area math `A=(D+2×(S+P))×(R+2×(S+P))` + table lookup + shipping `K=A×T×0.96` + VAT (lines 2132–2317); CBL VAT `%VAT=((cf_1210-unit_price)/unit_price)×100` (line 1659); hotel `2 đêm × giá = tổng` (line 455). Asking the LLM to compute prices is a **HALLU vector** — exactly what ragbot forbids. These are structured-data/calculator jobs, not RAG.

**A4. Live external URLs (Google Sheets/Drive/Docs) as data source.**
Lines 384, 503, 588–590, 1644–1645, 3078, 3216. RAG corpora are ingested snapshots; embedding raw share-links means the bot points users at uncontrolled, un-grounded external docs and the platform can't enforce HALLU=0 over them.

**A5. Verbatim "copy this exact sentence" template libraries.**
Reborn's `KỊCH BẢN CHUẨN` (lines 309–375), Beetech `template FAQ ... nguyên văn không giới hạn độ dài` (lines 1281–1288), Beespace v2 `<locked_phone_request>` "không được đổi từ" (lines 3928–3950), Dr. Medispa "copy nguyên câu mẫu" ×24. This is **application-injected answer text** — directly violates ragbot Sacred Rule #10 (app must not inject/override LLM answer). Also: verbatim Vietnamese sentences in system_prompt trip ragbot's output-guardrail `system_leak` shingle match (known issue `feedback_sysprompt_verbatim_example`).

**A6. Per-customer / per-domain literals throughout.**
Brand names (Beespace, Reborn, KDExpress, Gobe, Dr. Medispa, Fine Mold), phone/bank/address literals (`<phone>` line 124; bank `<acct>` line 2448; `<address>` line 4978), hardcoded special dates (`7/11, 8/11...` line 41; `25/4, 26/4, 27/4` line 3498; Tết `16/02→23/02` line 3705). Violates **domain-neutral** rule — none of this can live in platform code; it is per-bot config/corpus.

**A7. Hardcoded business lists in the prompt.**
Fine Mold `# DANH SÁCH VỊ TRÍ HỢP LỆ (HARDCODE — KHÔNG ĐƯỢC TỰ THÊM/BỎ)` (lines 5037–5062); CENTREC `<course_inventory>` (lines 3288–3294). These are **inventory data** that change frequently → corpus or structured-data, not prompt.

**A8. Tool-call protocol embedded in prompt.**
CBL `get_product_info(...)` + critical_rules "chỉ gọi tool 1 lần" (lines 1680–1797); Fine Mold "gọi tool query", JSON output schema (lines 5363–5389). RAG retrieval replaces the bespoke tool; these protocols don't port and would confuse a retrieval-grounded model.

**A9. Refusal text hardcoded as fixed Vietnamese strings.**
"Hiện tại em sẽ kiểm tra lại..." (line 146), "Do lượng khách ngày này đông..." (line 45). ragbot rule: refusal text origin = `bots.oos_answer_template` or per-rule `response_message`, **not** inline i18n strings.

**A10. Stateful conversation variables expressed as prose.**
Dr. Medispa `booking_info_collected`, `origin_intent` (lines 4435–4465); CENTREC `active_course` lock-in. RAG answer-path is largely stateless per turn; complex slot-filling/booking state machines exceed what a system_prompt + retrieval can reliably hold and are an action-framework concern, not RAG.

---

## 4. Conversion guidance — where each pattern goes in ragbot

For each major pattern: what stays in `bots.system_prompt` (persona/tone/flow), what moves to **CORPUS** (retrieved data), what becomes **STRUCTURED-DATA** (price/qty/inventory lookup), what becomes a **CONFIG knob**.

| n8n pattern | ragbot target | How to re-express |
|---|---|---|
| Persona/identity (`<identity>`, `<tone>`: "xưng em, gọi anh/chị, thân thiện") (lines 186–188, 287–288) | **system_prompt** | Keep — this is exactly what owner-authored system_prompt is for. Domain-neutral platform never injects it. |
| Greeting→qualify→answer→handoff flow, "1 câu/lượt", branch logic (`<step>`, BƯỚC/NHÁNH) | **system_prompt** (behavioral instructions only) | Keep the *flow shape* as natural-language guidance. Drop the embedded data. Trim 3–6× rule repetition to one clear statement — ragbot's grounding does the enforcement the repetition was compensating for. |
| Inline price tables / addresses / KB facts (A1) | **CORPUS** | Move every baked fact into ingested documents. e.g. Dr. Medispa's 24 service descriptions + prices become corpus chunks ("Massage cổ vai gáy: ưu đãi 99k/buổi, gốc 400k, 60 phút, ..."). LLM retrieves + grounds, instead of copying a script. |
| Spreadsheet column/cell addressing (A2) | **CORPUS** (self-describing text) **or STRUCTURED-DATA** | Re-ingest sheets as row-per-record self-describing sentences ("Phòng Deluxe tại Ocean Park 3, giá 1.800.000đ/đêm, ban công + bồn tắm"). Drop "cột B/C/D" language entirely. |
| Price/quantity/inventory/availability that varies daily (hotel room count, stock `qtyinstock`, "đang tuyển" Row 1, course schedule) (A3, A7) | **STRUCTURED-DATA lookup** | These are live transactional data, NOT corpus and NOT prompt. Need a structured lookup (DB table or per-bot key-value) the bot can query deterministically. Retrieval can't keep "5 phòng còn" fresh and grounding can't verify computed totals. |
| Arithmetic (area formulas, VAT %, night×price, volumetric weight) (A3) | **STRUCTURED-DATA / calculator tool**, NOT LLM | Do not ask the LLM to compute. Out of MVP RAG scope; flag as deferred (matches the "no action-framework gold-plating" lesson). If kept, must be a deterministic calc node, never prompt arithmetic. |
| Verbatim "send this exact sentence" template libraries (A5) | **DELETE from prompt** | ragbot Sacred Rule #10: app cannot inject/override answer text. Let the LLM phrase from retrieved corpus + persona tone. Move the *content* (what to say) into corpus; keep only *tone* guidance in prompt. Avoids `system_leak` shingle false-positives. |
| Refusal / OOS / "check lại với chuyên viên" fixed strings (A9) | **CONFIG**: `bots.oos_answer_template` or guardrail `response_message` | Set per-bot refusal text in DB column, not inline. "Don't reveal source" becomes a system_prompt tone line, not a forbidden-phrase list. |
| Special-date / Tết lockdown, "Office never quote price", "phòng họp ≥2h", min-order rules (lines 41, 72, 3705, 5087) | **CONFIG** (per-bot `plan_limits`/custom config) + **system_prompt** business rule | Behavior toggles → per-bot config JSON, not platform code (domain-neutral). The *rule statement* can also live in system_prompt as owner instruction. |
| Anti-hallucination rules ("chỉ từ tài liệu, không bịa, dán nhãn [Chưa xác minh]") (2.7) | **PLATFORM grounding** (already enforced) + light system_prompt reinforcement | ragbot's faithfulness/grounding + HALLU=0 already does this. Owner may add 1–2 anti-fabricate lines (cf. sysprompt v6 4-rule pattern). Do NOT replicate the 6× repeated walls. |
| Multilingual VI/EN/JP language-lock (Beespace v2 lines 3415–3558, KDExpress) | **CONFIG knob** (language pack / per-bot locale) + corpus in each language | "Reply in customer's language" is a platform language-pack concern. Ingest VN/ENG/JP corpus variants; retrieval picks language-appropriate chunks. Keep "output = customer language" as one system_prompt line. |
| Brand/phone/bank/address literals (A6) | **CORPUS** (contact info) or **CONFIG** | Contact/address → corpus chunk or per-bot config. Never in platform code. |
| Multi-doc routing ("Bảng giá" vs "Tình trạng phòng", CENTREC 2-case material) (2.6) | **CORPUS** + retrieval (mostly automatic) | RAG retrieval naturally merges multiple docs; explicit "đọc tài liệu X then Y" routing becomes unnecessary. Where disambiguation matters (textbook vs course-material), encode as metadata/sections in corpus, not as prompt branches. |
| Tool-call protocols / JSON output schema (A8) | **OUT of system_prompt** | CBL `get_product_info`, Fine Mold JSON record → these are integration/action concerns. For pure RAG MVP, drop; the retrieval replaces the product-info tool. Structured capture (recruitment record) is an action-framework feature, deferred. |
| Stateful slot-filling / booking confirmation (A10) | **DEFER** (action-framework, not RAG) | Booking/scheduling state machines (Dr. Medispa BƯỚC 4–5, Fine Mold BƯỚC 5–9) are beyond RAG answer-path. Map to the existing action/slot feature if needed; do not force into system_prompt. |

### Net conversion principle

**system_prompt keeps ~persona + tone + conversational flow shape + a couple anti-fabricate lines.** Everything factual (prices, addresses, descriptions, FAQs, schedules, contact info) moves to the **CORPUS**. Anything that is live/transactional/numeric (stock counts, availability, totals, "currently hiring") becomes **STRUCTURED-DATA** lookups, not retrieval and not prompt. Refusal text and behavior toggles become **CONFIG**. The pervasive verbatim-template and exact-arithmetic habits must be **dropped** — they violate Sacred Rule #10 (no app-injected/overridden answers) and are HALLU vectors. The good news: the n8n culture's strongest shared value — "only answer from data, never fabricate" — is exactly ragbot's grounding/HALLU=0 contract; it just needs to be delivered by retrieval+grounding instead of by 6×-repeated prompt walls.
