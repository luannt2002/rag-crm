# [T1-Smartness] Domain-neutral / multi-bot fairness program

> Goal: the engine must be 100% FAIR to every bot — support no single bot, no single
> industry, no single language. Engine knows STRUCTURE (entity, label, number,
> locale-token), never MEANING (price, service, booking, a language's words, a bot name).
>
> Source: `reports/DOMAIN_NEUTRAL_BETRAYAL_AUDIT_20260625.md` (6-agent full sweep).
> ADRs: `docs/adr/0007-stats-price-index-to-attribute-index.md` (+ 0008 to write).
> Enforced by: `tests/unit/test_domain_neutral_guard.py` (ratchet, decreasing-only).

## Principle (binding contract)
Engine code MAY reference structure; MUST NOT reference a specific bot/brand, a single
industry's first-class concept (price/VND), or a hardcoded human language as LOGIC.
Owners declare meaning via `custom_vocabulary` / `system_prompt` / `language_packs`.

## Betrayal map (audit verdict)
- ✅ NO `if bot_id == "..."` forking; 4-key/RLS + answer/refuse text already neutral.
- ❌ Betrayal #1 — numeric/structured layer hardwired to VND-price commerce (9 files).
- ❌ Betrayal #2 — VN hardcoded as routing/intent/enrichment/slot LOGIC.
- ⚠️ Betrayal #3 — domain vocab (service/booking/legal) leaked into universal prompts.

## Tracks & status

### Track D — bugs + scrub + ENFORCEMENT  ✅ DONE (Phase 1+2, `314ad43`,`97286b9`)
- [x] Scrub production doc-UUID + customer name (secret-scrub) + all tenant/brand/bug-id
      literals across `src/ragbot` → generic placeholders. **bot/brand refs 17→0.**
- [x] `test_domain_neutral_guard.py` ratchet: bot/brand baseline **0** (new literal fails
      CI); price-coupling baseline **127** (shrinks as ADR-0007 lands).
- [ ] (deferred) 34 zero-hardcode magic numbers → constants (separate sweep).
- [ ] (deferred) `chat_routes.py` "docs preparing" string out of `answer`; empty-sysprompt
      DB-seed default; null_guardrail threshold SSoT.

### Track B — language literals → `language_packs` per-locale  ✅ DONE + ACTIVE (`97286b9`,`7576301`)
- [x] `RoutingSignals` on `LanguagePack` (count/list/strip/range/superlative/price-ask +
      measure-unit regex + intent regex); vi seed **byte-identical**, en seed.
- [x] `query_range_parser` + `heuristic_intent_classifier` read per-locale signals.
- [x] alembic `seed_routing_signals_260625` (vi+en).
- [x] WIRED: `retrieve.py` resolves `get_routing_signals(bots.language)` → non-vi bots
      route on their own signals; vi provably identical.
- [ ] (remaining B-tail) slot_extractor + contextual_enrichment VN-only prompts → packs
      → ADR-0008.

### Track A — PRICE-index → ATTRIBUTE-index  ⏳ ADR-0007 (Proposed), NOT started
Biggest betrayal; schema → staged strangler fig, each flag-gated + measured A/B.
- [ ] **S1** render-faithful synthetic chunk (surface ALL generic `attributes_json`,
      not just price_primary). Smallest, no schema change. Closes spa combo/HALLU as a
      side-effect of doing the generic-correct thing. **PRE-REQ: pin the lossy aggregate
      render path** ("Bên em có N nhóm" chunk drops attributes). ← NEXT.
- [ ] S2 numeric-attribute index table + ingest dual-write (price columns still authoritative).
- [ ] S3 generic range/superlative/count over the new index (flag; A/B vs price path).
- [ ] S4 flip `price_*` to derived VIEW; rename `price_*`→`value_*`, `parse_money_vn`→
      `parse_number`, drop VND floor/buckets; `math_lockdown` unit = corpus label.
- [ ] S5 language-neutral routing depends on Track B (done) reaching these routes.

### Track C — domain vocab out of universal prompts/schema  ⏳ NOT started
- [ ] i18n understand/condense/rewrite prompts: strip "booking/combo/service" or make
      domain hints per-bot opt-in.
- [ ] `ACTION_STATE_ALLOWED_TOP_KEYS` `service_locked` → `entity_locked`.
- [ ] `document_stats` `_NAME/_CATEGORY/_PRICE_COL_TOKENS` VN service frozensets → DB seed
      `locale='vi'` (ADR-0006 direction), not code.

## Fairness enforcement (the "không support riêng" mechanism)
- [x] CI ratchet guard (bot/brand=0, price=127 decreasing-only).
- [ ] Canary multi-bot eval: add an EN bot + a non-price (specs) bot to `eval/`; the gate
      requires every route (factoid/range/superlative/list/summary) to pass for them too,
      not just spa → "fair to all bots" becomes MEASURED.

## Sequencing
1. ✅ Track D + guard + Track B (+wiring) — Phase 1/2 shipped, safe, verified.
2. ⏳ Track A S1 (render-faithful) — NEXT; pin lossy path, TDD, measure 50Q.
3. ADR-0008 (B-tail prompts) + Track C.
4. Track A S2–S5 (schema) — measured A/B per stage.
5. Canary EN/non-price bot in eval.

## Honest scope
Track A schema is multi-week; strangler fig, never big-bang (would break live spa/xe/legal
bots = betray the fairness goal). Phase 1/2 (done) close 2 of 3 systemic betrayals + lock
enforcement; Betrayal #1 (price) remains, guarded against regression by the ratchet.
