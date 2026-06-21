# Root-cause: xe price fabrication on size-queries (5-step, evidence-driven)

Per CLAUDE.md BUG INVESTIGATION MANDATE. Diagnosis only — fix is a sacred-path
(price answer) change, gated + fresh session.

## 1. Bug gì — reproduce concrete
- Câu hỏi: "Lốp 205/55R16 giá bao nhiêu?" (verbatim, cache bypassed)
- Đáp án đúng (corpus): **1.044.000đ, quantity 819** (chunk 214 `price: 1044000 | quantity: 819`; stats row "205/55/16 GP, Land 205/55/16 G-P" price_primary=1044000)
- Bot trả: non-deterministic — `1.150.000đ` / `972.000đ` / `1.500.000đ` / `"chưa có thông tin"` / **`0đ/lốp, hết hàng`** across runs
- Diff: bot quotes a price that is fabricated (`1.150.000` appears in **0 corpus chunks**, verified) or 0đ, never the true 1.044.000.

## 2. Nguyên nhân trực tiếp — 1 layer up
- Layer fail: **retrieval** (stats-index routing).
- Số liệu (live debug): `intent='aggregation'`, `top_k=1`, `score_max=1.0`, `source='query_graph'`, `rewritten_query='giá lốp 205/55R16'`.
- The single retrieved chunk = the **symbol-variant row** `205/55R16 91V CITYTRAXX G/P | col_2: 28-thg 11 | col_4: RVL 205/55/16 …` — **no `price:` field**. The priced chunk 214 was NOT retrieved.

## 3. Gốc rễ — immutable cause (chain)
`bot 0đ/fabricate`
 ← `stats lookup returns a NULL-price entity row`
 ← `the query token "205/55R16" (R-notation) keyword-matches the WRONG index row`
 ← **the same physical product is split into ≥2 `document_service_index` rows with inconsistent notation, and the price is attached to only one:**
  - `205/55R16 91V CITYTRAXX G/P` (R-notation, from the `productname` field) → **price NULL** ×4 rows
  - `205/55/16 GP, Land 205/55/16 G-P` (slash-notation, from the `answer` field) → **price 1044000**
- The user's natural "205/55R16" matches the R-notation NULL-price row; the priced row uses "205/55/16" (slash). **Immutable cause = the stats-index EXTRACTION created notation-inconsistent duplicate entities and the price landed on the notation the user does NOT type.**
- Evidence: `SELECT entity_name, price FROM document_service_index WHERE entity_name ILIKE '%205/55R16%'` → 4 rows, all NULL price; `WHERE price=1044000` → name is "205/55/16 GP, Land…" (slash). Chunk 214 has BOTH the productname (R) and `price:1044000` in one block — so extraction split one block's fields into separate, inconsistently-priced rows.

## 4. Expert solution — đúng tầng (data/extraction + retrieval guard)
Right layer = stats-index population + a retrieval safety net. NOT sysprompt (the
CLAUDE.md case-study warns: retrieval miss must not be patched at sysprompt).
- **Short-term (matching):** when the stats lookup matches multiple rows for a query,
  PREFER a row that has a price over a NULL-price row (a price query should never
  return a NULL-price entity if a priced sibling matches). Low-risk, domain-neutral.
- **Mid-term (extraction):** dedup/merge entity rows of the same product so every
  notation variant carries the product's price (normalize size-token separators
  `/ R space -` to a canonical form at extraction + match time — separator
  normalization, not tire-specific vocabulary, so domain-neutral holds).
- **Safety net (anti-HALLU, all bots) — at RETRIEVAL, not answer-override:** do NOT
  surface a NULL-price / variant-symbol chunk AS price evidence. If no chunk with a
  real `price:` field matches, return EMPTY context → the LLM refuses on its own
  sysprompt (no app-forced refusal text, no answer override — compliant with sacred
  #2/#10). The bug today is the retriever handing the LLM a price-less chunk that it
  then confabulates around; starve that and the confabulation stops. Pattern:
  evidence-gated context (don't feed misleading rows), NOT code-level answer replacement.

## 5. CLAUDE.md compliance
- Sacred #5 (HALLU=0): currently VIOLATED on this path (fabricated 1.150.000) → the
  safety-net refusal restores it. Fix must be A/B gated with HALLU=0 verified.
- #10 (no app-inject / no app-override): the fix is at retrieval/extraction + a refusal
  gate driven by "is there evidence", NOT injecting text into the answer — compliant.
- Domain-neutral: separator normalization + prefer-priced-row are generic, no tire vocab.
- Tier: **T1-Smartness** (faithfulness). Right layer (retrieval/data), not sysprompt.
- Model: diagnosed in main session (Opus) per deepdive policy.

## Gate for the fix (next session)
Build a conversational-query eval (size/listing/threshold — the D13 set) FIRST, then
implement the matching + safety-net, measure with `eval_rigor.py --compare`:
DONE only if price-query COVERAGE↑ AND HALLU=0 AND existing 42-q COVERAGE 1.00 hold.
`scripts/qa_chat.py` is the reproduction harness.
