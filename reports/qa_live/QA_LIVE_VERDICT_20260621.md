# Live conversational QA — 3 bots, 2 rounds, adjudicated verdict (2026-06-21)

**Method:** 3 QA/QC agents (one per bot) held REAL multi-turn business conversations
via the test chat endpoint (`scripts/qa_chat.py`, cache bypassed), verifying every
answer against the corpus (DB SELECT). Two rounds: basic flows, then deep flows
(listing · comparison · booking/appointment · stability-repeat). Every HALLU claim
was re-adjudicated in the MAIN session against DB evidence (rule #0).

## The headline: conversational QA overturns the factoid-eval's "COVERAGE 1.00"

The B-2 rigor harness scored all 3 bots **COVERAGE 1.00, std 0, HALLU 0** — but it
used **exact entity-NAMES** that route to the stats index (the easy path). Real users
ask by **size / listing / comparison / threshold**, which hit the vector→chunk path.
That path is broken. The 42-q hand-set's 1.00 was the cherry-pick problem again —
now proven a THIRD way (after auto-qrels noise + the price-factoid saga).

## Per-bot adjudicated verdict

### 🔴 chinh-sach-xe (tire shop) — NOT production-ready: fabricates prices
- **Confirmed fabrication:** the price `1.150.000đ` appears in **0 corpus chunks**
  (verified `content LIKE '%1150000%'` = 0 AND `'%1.150.000%'` = 0), yet the bot quotes
  it repeatedly for 205/55R16, 185/65R15 (both brands), etc. — a single "default
  confabulation price" pasted onto ≥4 products whose real prices differ (900k/810k/1044k).
- **Non-deterministic:** identical question "Lốp 205/55R16 giá bao nhiêu?" returned,
  across runs/sessions: `1.500.000` · `972.000` · `1.150.000` · `"chưa có thông tin"`
  (refuse) · `0đ`. Same question, 5 different outcomes.
- **Comparison inverted:** said Landspider cheaper than Rovelo; DB truth is the reverse
  → a customer buys the wrong brand.
- **Root cause (retrieval layer):** the stats index has NULL price for these *size*
  entities; size-queries retrieve date-list / image-link narration chunks (no price
  field) → the LLM confabulates. HALLU rate ~50% on price turns.

### 🟡 test-spa-id (spa) — solid core, listing gaps
- **0 HALLU** across both rounds (5 out-of-scope traps correctly refused). **Perfect
  price stability** (3 services × 3 runs = 0 drift). **Booking flow PASS** (intent →
  price/duration → time → confirmation; did not fabricate slot availability or address).
- **Coverage misses (silent denial of bookable services):** listing/comparison queries
  retrieve ONE chunk and omit services in others — triệt lông (12-zone price table,
  129k–2,499k), Vikim trẻ hóa (1,500k), trị mụn chuyên sâu (700k), 2 CSD services all
  denied on listing but answerable on direct single-service queries. Cheaper (700k-tier)
  services systematically most omitted.
- Verdict: usable for single-service Q + booking; weak on "list everything" / compare.

### 🔴 thong-tu (banking circular) — NOT production-ready for legal: wrong thresholds
- **MFA threshold accuracy 0/4 runs.** Bot says "cấp độ 2 trở lên" (reproducible 3/3 on
  short phrasing) or "cấp độ 3" (longer phrasing); corpus truth (Điều 30 khoản 6,
  chunk 289) is **"từ cấp độ 4 trở lên phải áp dụng xác thực đa yếu tố khi truy cập quản
  trị"**. Chunk 289/288 is never retrieved; chunk 356 ("cấp độ 2 trở lên", a different
  clause) always outranks it → the LLM conflates the retrieved level with the MFA rule.
- **Citation leakage (6 instances):** the bot cites internal DB chunk indices ("theo
  đoạn 36", "đoạn 390", "đoạn 278") as if they were legal references instead of the real
  article ("Điều 29/30"). Dangerous — a compliance officer would cite a meaningless artifact.
- **Level-4 coverage 0%**, multi-turn context loss (a phantom "đoạn 150" invented on turn 2).
- Defensive traps held: fake Điều 99, invented penalties, unrelated decree all refused (0 fabrication there).

## Root cause is ONE layer: RETRIEVAL (not LLM, not sysprompt)
All three failure families are the retriever sending the WRONG chunk:
- xe: size-query → narration/date chunk instead of the priced row.
- spa: listing → one chunk, siblings not retrieved.
- legal: threshold-query → wrong-clause chunk outranks the right one.
The LLM faithfully synthesizes what it is given — so the fix belongs at retrieval
(ranking / chunk-selection / the stats-vs-vector routing), NOT at the prompt. Patching
the sysprompt would be the same wrong-layer mistake the CLAUDE.md case-study warns about.

## What this means for the program
- **`scripts/qa_chat.py` + this conversational QA is now the eval that matters** — it
  found ~50% price-HALLU on xe that the factoid eval rated 1.00. The D13 human-curated
  set should be built from REAL conversational queries (size/listing/comparison/threshold),
  not entity-name factoids.
- **Next work (Tier-1, retrieval) — gated, fresh session, with a plan:**
  1. xe: route size-queries to the priced chunk (the stats index is NULL for sizes → fix
     the index population OR the size→entity resolution); reject answers when no price
     chunk is retrieved instead of confabulating.
  2. legal: fix ranking so the specific-clause chunk (cấp độ 4 MFA) beats the generic
     "cấp độ 2" clause; strip "đoạn N" narration from citations (surface "Điều X").
  3. spa: listing queries must gather ALL sibling chunks (multi-chunk retrieval), not one.
- This is a measure-FIRST win: do NOT patch sysprompt; build a conversational-query
  eval set, then fix retrieval against it with an A/B gate (HALLU=0 + coverage↑).

## Evidence trail
Per-bot transcripts + DB ground-truth: `reports/qa_live/{chinh-sach-xe,test-spa-id,thong-tu}_qa_report.md`
(round 1) + `*_qa_deep_report.md` (round 2). Phantom-price + MFA-threshold + citation-leak
all re-verified in the main session against DB.
