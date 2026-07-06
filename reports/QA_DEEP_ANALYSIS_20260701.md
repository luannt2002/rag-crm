# QA Deep Analysis — xe + legal bots — 2026-07-01

Source: 2-tab QA sheet (20 xe + 20 legal). Method: 40 questions run LIVE
(`/test/chat`, bypass_cache), then every "failure" re-run single-shot to separate
**transient** from **deterministic**, then traced layer-by-layer (ingest → parser →
chunk → stats-extract → retrieve-path → prompt → answer) with evidence.

## Headline

**NOT "4/4 wrong".** MEASURED subset (auto-scored by expected-number / refusal-marker
match — rule#0, no eyeball): **XE 9/11 auto-verifiable PASS** (1 FAIL = Q15 transient,
1 PARTIAL = Q20), **LEGAL 6/6 auto-verifiable PASS**; the remaining 9 (XE) + 14 (LEGAL)
are definition/description answers NOT auto-scorable → judged-by-inspection, NOT
measured (labelled MANUAL, don't claim a score for them). HALLU=0 (every refusal-trap
honoured: Bridgestone/Michelin/weather/Điều 99/red-light/false-premise 100tr). Priced
factoids match the sheet exactly (810k/1440, 1.485k/98, 1.152k/9, 2.322k/1, 3.123k/12).
The user's "4/4 wrong" was likely pre-fix / cached / via the F5-history-wipe UI bug
(now fixed).

## Classification

| Case | Parallel batch | Single-shot | Verdict |
|---|---|---|---|
| XE Q15 Davanti 275/40ZR21 | refuse | ✅ 3.240.000đ/257 | TRANSIENT under load — journal shows `generate_sla_breach` (11.5s>8s, completion_tokens=0), NOT a 503 |
| XE Q3 brand compare | deflect | deflect | PERSONA 1-branch (no fabrication) |
| XE Q7 follow-up | no context | — | TEST ARTIFACT (isolated connect_ids) |
| XE Q13 Neoterra 195/65R16 | "no price" | "no price" | CORRECT (price genuinely absent) |
| **XE Q20 comparison** | fail | ❌ fail | **BUG B (deterministic)** |
| **LEGAL Q14 date** | leak col_1 | col_N synthetic | **BUG A (deterministic)** |

## BUG A — LEGAL col_N (CONFIRMED, full chain)

1. **Bug**: factoid queries surface a synthetic chunk
   `Hà Nội | category: Độc lập… | col_1: ngày 21 tháng 10 năm 2020` → LLM can quote
   `col_1` verbatim (Q14).
2. **Direct**: retrieve returns a **stats-synthetic chunk, score=1.0, graded=1** —
   beats every real prose chunk.
3. **Root (immutable)**: the **stats-index extractor (`parse_table_chunks`, built for
   product catalogs) runs on a text/QA legal bot**. Legal prose has commas, so it
   comma-splits sentences (`"Trong Thông tư này, các từ ngữ…"` → name + `col_1`);
   the HTML letterhead is a 2-col table (pipes) → pipe-split. Result: **52/54
   `document_service_index` rows are col_N garbage**. The stats-synthetic retrieval
   path (score 1.0, authoritative) then surfaces that garbage over real prose for
   factoids.
4. **Right layer**: (a) stats-extract — don't extract for non-catalog bots / reject
   prose rows; and/or (b) retrieve — a synthetic chunk must not hold score 1.0 over
   a higher-quality prose chunk for a text factoid.

## BUG B — XE Q20 multi-spec comparison (CONFIRMED, full chain)

1. **Bug**: "So sánh 205/65R16 và 235/40R18" → "chưa có 235/40R18" (+ mislabels
   "Rovelo" — a minor refusal-text fabrication).
2. **Direct**: intent=comparison, decompose=True → **3 correct sub_queries**, but
   `graded=1, score_max=1.0`.
3. **Root (immutable)**: `_parse_code_query("So sánh 205/65R16 và 235/40R18")` →
   `keyword='205/65R16'` — **captures only the FIRST code**. The stats path then
   returns one synthetic chunk (score 1.0) for 205/65R16 and **short-circuits the
   decompose**, so 235/40R18 is never retrieved. Each spec asked alone returns
   correctly (205/65R16=1.170.000/186, 235/40R18=1.602.000/27) — proving data +
   single-spec retrieval are fine.
4. **Right layer**: multi-query/comparison — the single-code stats short-circuit must
   NOT pre-empt a decomposed multi-spec query (run per-sub-query, or skip stats
   short-circuit when `len(sub_queries) >= 2`).

## Data-model — MULTIDOC-B-FRAG (CONFIRMED)

One physical product = up to 3 separate stats rows, no cross-sheet join:
- `11111` catalog: full spec name, **price=None**, date1=26
- `2222` shipping: spec, price=None
- `3333` price sheet: **price+quantity**, but `entity_name` = internal CODE
  ("2-ZR18 235/40 LPD"), spec only in the alias/`question` cell

→ Answer correctness depends on which sheet's row retrieval lands on. Correct when it
hits `3333`; "no price" when it hits `11111`. (Q13 Neoterra only exists in `11111`
→ genuinely no price → correct refusal.)

## BUG C — LEGAL summary-answer HALLU (CONFIRMED via corpus, LLM-judge caught it)

Auto keyword-scoring + eyeball MISSED this; a proper read + corpus check found it.

1. **Bug**: on SUMMARY questions (Q1 "quy định về gì", Q8 "phạm vi điều chỉnh") the
   bot lists đối tượng that INCLUDE **"tổ chức kinh doanh vàng"** (Q1) and **"tổ chức
   kinh doanh bảo hiểm"** (Q8).
2. **Corpus check**: `kinh doanh vàng` = absent, `kinh doanh bảo hiểm` = absent. The
   real Điều 1 Khoản 2 list (which the DIRECT question Q6 reproduces correctly) is 8
   orgs: TCTD, chi nhánh NH nước ngoài, trung gian thanh toán, thông tin tín dụng,
   NAPAS, VAMC, nhà máy in tiền, Bảo hiểm **tiền gửi** VN. "Bảo hiểm tiền gửi" (one
   named org) ≠ "kinh doanh bảo hiểm" (insurance business).
3. **Root**: summarization pulls the OVERVIEW/TOC chunk (not the precise Điều-1 org
   clause), so the LLM fills the đối tượng list from **prior knowledge** (NHNN
   circulars commonly cover gold/insurance businesses) → plausible-but-absent orgs.
   The anti-fabricate/anti-variant sysprompt rules don't catch **list embellishment**
   in a summary. Direct factoid (Q6) stays grounded; summary drifts.
4. **CORRECTION of an earlier claim in this session**: "HALLU=0" was WRONG. There IS a
   confirmed fabrication (Q1, Q8). The refusal-traps are all honoured (no fabricated
   numbers/refusals) but the summary path leaks an out-of-context entity list.

## QA-sheet error found (bot right, sheet wrong)

- **LEGAL Q7**: sheet expected "Điều 50 không tồn tại". Corpus HAS "Điều 50. Xây dựng
  hệ thống dự phòng thảm họa". The bot answered correctly (no fabricated fine, correct
  Điều-50 content). The **QA expectation is the error**, not the bot.
- **XE Q2**: sheet expected bank/MST/"Quang Minh"; corpus has none of STK/số tài
  khoản/MST/Quang Minh/chuyển khoản. Bot gave only the real address+phone and did NOT
  fabricate a bank account → **honest** (data gap, not a bug).

## Unifying thread

Both real bugs are the **stats path over-asserting** (score-1.0 synthetic dominance):
BUG A surfaces col_N garbage on a text bot; BUG B short-circuits a multi-spec
decompose after matching only the first code. The stats/analytical path is a
catalog-point-lookup tool being applied where it should defer (text factoid;
multi-spec comparison).

## Still OPEN (not yet fixed — analysis only)

No fixes applied. Next: confirm the exact retrieve short-circuit line for BUG B, then
decide fix layer per bug (stats-extract gate for non-catalog vs retrieve
score/precedence vs multi-query short-circuit guard). Also pending from earlier
session: XLSX row-as-chunk parity (uncommitted working tree).
