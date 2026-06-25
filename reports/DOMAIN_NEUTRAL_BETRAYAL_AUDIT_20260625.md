# Domain-neutral / multi-bot betrayal audit — full codebase (2026-06-25)

6 parallel read-only sweeps over `src/ragbot/**` (excl. tests/alembic), judged by Opus.
Question: where does the ENGINE betray "application không support riêng 1 bot / 1 ngành / 1 ngôn ngữ"?

## Headline verdict
- ✅ **NO `if bot_id == "..."` functional forking** — control flow never keys on a bot identity. The 4-key/RLS/tenant-isolation and the answer/refuse-text layers are genuinely domain-neutral (text is DB-driven).
- ❌ **Two SYSTEMIC betrayals**, each breaking whole classes of non-spa bots:
  1. **The numeric / structured-query layer is hardwired to VND-price commerce.**
  2. **Intent-routing + ingest-enrichment + slot-extraction are hardcoded Vietnamese in CODE (not `language_packs`).**
- ⚠️ Plus **domain-vocab leakage** (service/booking/legal) into universal LLM prompts & parsers, and **3 genuine bugs** + **customer-literal leaks** in comments.

---

## BETRAYAL 1 — Numeric/structured layer = VND-price commerce (breaks any non-price / non-VND bot)

| Component | Evidence | Breaks |
|---|---|---|
| Only number parser is `parse_money_vn` | `shared/number_format.py:46-168` suffix table `ty/trieu/tr/nghin/k` VND-only; name+logic | USD/EUR bot; non-money numbers |
| Stats schema = price columns | `stats_index_repository.py:20-21` `price_primary/secondary`; `query_by_price_range`, `top_by_price`, `count_by_price_range`; sort priced-first | phone(RAM)/realestate(m²)/legal(#khoản) — no generic numeric range/superlative |
| VND floor drops numbers at ingest | `document_stats.py:224` `min_value=DEFAULT_PRICE_MIN_VND=10_000` | small legit numbers (RAM 8, qty 3) lost from numeric index |
| RangeFilter is price-typed | `query_range_parser.py:85-99` `price_min/price_max/price_column` | any non-price range query |
| Price buckets VND | `document_stats.py:892-965` `under_500k/above_5M`; `DEFAULT_PRICE_BUCKETS_VND` (dup in `_09`,`_21`) | meaningless for non-price; written to every summary_json |
| Math guardrail tags all numbers "VND" | `math_lockdown.py:184-188` `claims.add((num,"VND"))` | a USD/EUR bot's numbers mis-grounded |
| Conversation drift = price extractor | `jsonb_conversation_state.py:272-296` `_extract_prices`+`_normalise_price`; **spa-specific key `price_buoi_le`** at :200 | runs on every bot's answer; leaks a spa field name |
| Superlative enricher = price/discount/gifting | `superlative_context_enricher.py` `max_price/min_price/max_discount`, `RankedItem.price`, `tặng buổi/voucher` regex unconditional | every bot gets commerce intent slots |
| Prompt-compression currency bonus | `prompt_compression.py:170` +0.15 for `triệu/tỷ/nghìn` | VN-currency sentences over-weighted globally |

**Root:** the structured/numeric subsystem is a **PRICE index**, not a generic **ATTRIBUTE index**. `attributes_json` (ADR-0006 T3) is the neutral container that already exists but is not the first-class path.

## BETRAYAL 2 — Vietnamese hardcoded as LOGIC (breaks any non-VN bot)

| Component | Evidence | Effect |
|---|---|---|
| All stats/summary routing signals VN | `query_range_parser.py:109-135,339-361,425-444` `_COUNT/_LIST/_PRICE_ASK_SIGNALS`, strip-phrases | EN/ES bot never routes to stats/summary/factoid |
| Heuristic intent skip = VN regex | `heuristic_intent_classifier.py:62-105` | non-VN query with a VN-looking substring wrongly skips the LLM understand call |
| Slot extractor prompt VN-only | `slot_extractor.py:39-47` `"Bạn là slot extractor..."` | EN bot booking/slots degrade silently |
| Ingest enrichment prompt VN-only | `contextual_enrichment.py:33-75` `"Tài liệu có {N} đoạn..."` | EN corpus enriched with VN instruction |
| KG stopwords VN-hardcoded | `knowledge_graph.py:361-368` | EN/other KG queries mis-extract |
| VN legal numerals unconditional | `condense_question.py:49`, `simple_text_parser.py:31` `Điều/Khoản/Chương` | runs on every bot regardless of language |
| Duration units VN | `math_lockdown.py:_DURATION_UNITS` incl `buổi` | spa session unit treated as universal |

(Mitigated/gated — still engine-embedded VN: `vi_tokenizer`, `vocabulary_expander`, `prompt_compression` stopwords — all `language=="vi"` gated, acceptable but should move to `language_packs`.)

## BETRAYAL 3 — Domain (service/booking/legal) vocab in universal prompts/parsers

- `i18n.py:184,318` "đặt lịch / booking is NOT out_of_scope" + `:214,217` few-shots "combo/gói cơ bản" → **baked into EVERY bot's understand/condense/rewrite LLM prompt** (medium — functional bias).
- `document_stats.py:136-167` `_NAME_COL_TOKENS` {dich vu, san pham, goi, combo}, `_HEADER_EXTRA` {buoi} — catalog vocab in universal parser (mitigated by ADR-0006 T2/T3).
- `conversation_state` `ACTION_STATE_ALLOWED_TOP_KEYS={...,"service_locked"}` — booking concept as universal action schema.
- Legal vocab defaults: `DEFAULT_ARTICLE_REF_PATTERNS`, `DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN`, `_PRICE_STRUCTURAL_ANCHORS` {dieu/khoan/thong tu} (config-overridable → medium).

## Genuine BUGS found (fix regardless of the big refactor)
1. **Production doc UUID + customer name leaked** in `document_recovery_worker.py:5` (`4d6c1e47-… Thông tư 09/2020`) — secret-scrub violation.
2. **OOS threshold value mismatch**: `null_guardrail.py:95` `0.85` vs constant `0.90` — divergent SSoT.
3. **Hardcoded VN answer string**: `chat_routes.py:300` `"⏳ Tài liệu đang chuẩn bị..."` emitted as `answer` (sacred-rule app-inject).
4. **Empty bot.system_prompt → platform injects its own 8-rule VN block** `generate.py:595` from `i18n.prompt_generator` (sacred-rule app-inject; should be DB-seeded default or explicit refuse).
5. **55 customer-literal leaks in comments** (spa/xe/Dr.Medispa/PAYOT/thong-tu/real prices+SKUs) — CLAUDE.md "no tenant literal in tracked files".
6. **34 zero-hardcode magic numbers** (separate concern; notable: RetryPolicy `initial_backoff=100` vs const 500).

---

## Fix architecture — make the engine work for EVERY bot (domain-neutral)

**Theme: the engine must know only STRUCTURE (entity, labelled field, number, locale-token), never MEANING (price, service, booking, Vietnamese).**

### Track A — Generalize numeric/structured layer: PRICE-index → ATTRIBUTE-index
- `attributes_json` (already neutral) becomes the first-class path; `price_primary/secondary` → generic `numeric_value`/labelled numeric attributes (backward-compat VIEW).
- Range/superlative/count query ANY numeric attribute by its corpus label, not "price".
- Replace `parse_money_vn` floor with locale/unit-agnostic number capture; currency is just one labelled unit.
- `RangeFilter.price_*` → `value_*`; `math_lockdown` unit = the corpus's own label, not hardcoded "VND".
- Rename `*_price_*` / `parse_price_of_entity` → `*_attribute_*`; drop `price_buckets` or make per-bot.

### Track B — Move ALL language literals from code → `language_packs` (per-locale, DB)
- Routing signal lists (count/list/price-ask/superlative/strip), heuristic-intent regex, slot-extractor prompt, ingest-enrichment prompt, KG stopwords, legal-numeral normalizer → per-locale packs, selected by `bots.language`. Engine reads tokens from DB, never hardcodes a language.

### Track C — Remove domain assumptions from universal prompts/schema
- `i18n` understand/condense/rewrite prompts: strip "booking/combo/service" specifics → neutral, or make domain hints a per-bot opt-in.
- `ACTION_STATE_ALLOWED_TOP_KEYS` "service_locked" → generic "entity_locked".
- Column-role frozensets: keep only as `locale='vi'` DB seed (ADR-0006), not code.

### Track D — Close the genuine bugs now (small, safe)
- Scrub the doc-UUID + customer literals (comments → generic placeholders).
- Fix OOS threshold SSoT; route the "docs preparing" string out of `answer`; DB-seed the empty-sysprompt default; lift the 34 magic numbers.

### Sequencing (EVOLVE, measured, each behind a flag)
1. **Now (safe):** Track D bug-scrub + the lossy-render fix (pin first).
2. **ADR-0007:** Track A (PRICE→ATTRIBUTE index) — schema, hard-to-reverse, measured A/B.
3. **ADR-0008:** Track B (language→packs) — multi-locale correctness.
4. **Track C** folds into A/B.

**Honest scope:** A+B+C is a multi-week generalization. The platform works TODAY because every live bot is VN + (spa/xe price-catalog OR legal). The betrayal is **latent** (a non-VN or non-price bot would degrade), not an active break for current tenants — except the genuine bugs (Track D), which are real now.
