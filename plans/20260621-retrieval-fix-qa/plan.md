# [T1-Smartness] Retrieval fix — close the bugs live conversational QA exposed

**Origin:** the 2026-06-21 live QA (3 bots, 2 rounds) found production-blocking
RETRIEVAL bugs that the 42-q factoid eval (COVERAGE 1.00) hid. Evidence + root-cause:
`reports/qa_live/QA_LIVE_VERDICT_20260621.md` + `ROOTCAUSE_xe_price_20260621.md`.
Repro harness: `scripts/qa_chat.py`. ALL three failure families = retriever surfaces
the WRONG chunk (NOT an LLM/sysprompt bug → do not patch sysprompt).

**Tier:** T1 (faithfulness/coverage). **Sacred:** HALLU=0 must hold — these fixes
touch the price/legal answer path. **Discipline:** measure-FIRST, A/B gated, no
sysprompt patch, no app answer-override (sacred #2/#10).

---

## Phase 0 — the gate must exist FIRST (D13 conversational eval)
The 42-q hand-set is the EASY path (exact entity-name → stats index). It cannot gate
these fixes because it never exercises the broken paths. Build the eval that does.
- [ ] Author a conversational-query eval set per bot from REAL usage shapes:
      **size-query** ("Lốp 205/55R16 giá?"), **listing** ("liệt kê dịch vụ trị mụn"),
      **comparison** ("so sánh A vs B"), **threshold** ("MFA từ cấp độ mấy?").
      Ground-truth verified by hand against the corpus (NOT auto-gen — auto-qrels
      proved untrustworthy, commit a3dde09).
- [ ] ~15-20 queries/bot, stored in `tests/scenarios/` (curated, version-tracked).
- [ ] Wire into `eval_rigor.py --compare` so before/after is one command.
- **Gate output:** a baseline run showing the CURRENT failures numerically
      (xe price-COVERAGE low + HALLU>0; legal threshold 0%; spa listing-miss).

## Phase 1 — xe: price-notation matching (root-cause 01351a9)
Immutable cause: one product split into ≥2 `document_service_index` rows with
inconsistent size notation; price attaches to the slash-row ("205/55/16") but the
user types R-notation ("205/55R16") which matches a NULL-price row.
- [ ] **Match fix** (`stats_index_repository` lookup + `query_graph._do_stats_lookup`):
      on multi-match, PREFER a row with a non-NULL price over a NULL-price row.
- [ ] **Separator normalization** (domain-neutral): canonicalize size-token separators
      `/ R space -` at BOTH extraction (`document_service_index` populate) and match
      time, so "205/55R16" ≡ "205/55/16" ≡ "205 55 16". Put the normalizer in
      `shared/` (pure, tested), no tire vocabulary.
- [ ] **Evidence-gate** (`retrieve.py` stats routing ~line 170-185): never hand the
      generate node a chunk lacking a real `price:` field AS price evidence; if none
      matches → empty price context → LLM refuses on its own sysprompt (no override).
- [ ] Unit test: "205/55R16 giá?" → retrieves the priced chunk (1.044.000) deterministically.
- **DONE if:** xe price-COVERAGE↑, identical-query price is STABLE across 3 runs,
      HALLU=0 (no fabricated/0đ price), 42-q COVERAGE 1.00 no-regression.

## Phase 2 — legal: clause-ranking + citation faithfulness
Cause: generic-level chunk (356, "cấp độ 2") outranks the specific MFA chunk
(288/289, "cấp độ 4"); and "đoạn N" (DB chunk index) leaks into legal citations.
- [ ] **Ranking:** prefer the chunk whose narrated header names the SPECIFIC article
      asked about; verify chunk 289 (Điều 30.6) wins on the MFA query. Likely a
      rerank tie-break / scoring tweak in the reranker or retrieve node (reuse the
      F2 tie-break pattern, commit e175e0c).
- [ ] **Citation strip:** the answer must not surface "đoạn N" (internal chunk index)
      as a reference. Source of the leak = the narrated chunk lead "Đoạn N thuộc phần…"
      (same artifact the KG probe flagged, commit 52752cb). Strip/relabel the narration
      lead so only real "Điều X" reaches context — at the chunk-narrate / context-build
      layer, NOT by post-editing the LLM answer.
- [ ] Verify level-4 listing returns the real obligations (Điều 30.6, 26.3, 42, 52, 5.8).
- **DONE if:** MFA threshold answered "cấp độ 4" correctly + stably, 0 "đoạn N"
      citations, level-4 coverage>0, HALLU=0, defensive traps still refused.

## Phase 3 — spa: multi-chunk listing
Cause: listing/comparison retrieve ONE chunk → sibling services in other chunks are
silently denied (triệt lông, Vikim, trị mụn, CSD).
- [ ] For listing/enumeration intent, gather ALL sibling chunks of the category (raise
      top_k for listing intent and/or parent-expand), not one. Check
      `DEFAULT_RETRIEVE_TOP_K_BY_INTENT` + the listing/aggregation path in `retrieve.py`.
- [ ] Verify "liệt kê dịch vụ triệt lông" returns the full 12-zone price table.
- **DONE if:** listing COVERAGE↑ (no silent denial of corpus services), HALLU=0,
      spa price-stability + booking flow unchanged (they already PASS).

## Cross-cutting constraints (every phase)
- A/B via `eval_rigor.py --compare` on the Phase-0 set; Wilcoxon where N allows.
- HALLU=0 is a hard gate — any breach → revert.
- No sysprompt edit, no app answer-override, no psql to content/config tables.
- Domain-neutral (no tire/spa/legal literals in `src/`); zero-hardcode (thresholds via
  config/constants); Port+DI preserved.
- Each phase = its own commit, ship one bot at a time (don't batch all three).

## Sequencing
Phase 0 (gate) → Phase 1 (xe, most severe: active HALLU) → Phase 2 (legal, dangerous
domain) → Phase 3 (spa, lowest severity, no HALLU today). Each gated independently.
The D13 set built here is the durable asset — it becomes the standing conversational
regression eval the factoid set should have been.
