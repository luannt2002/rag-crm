# RAG Pipeline — Multi-Agent Deep-Debug (5 Opus agents, READ-ONLY) — 2026-07-02

Five parallel Opus agents audited the full pipeline (ingest · retrieval · query-understanding ·
generation/guardrail · observability/cost/tenant). Every finding carries `file:line` evidence
(rule#0). Already-fixed-this-session items excluded. Ranked by severity.

## CRITICAL

- **[TENANT] Retrieval SQL has NO `record_tenant_id` predicate; isolation is 100% RLS — and RLS
  is BYPASSED** (runtime on superuser DSN: `DATABASE_URL`=superuser, `DATABASE_URL_APP` unset,
  `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`). `pgvector_store.py:330-433` filters only `record_bot_id`.
  Not an active leak (bot-UUID is unique), but **defense-in-depth = 0**. Fix = provision
  `ragbot_app` NOBYPASSRLS role (ops) OR add a `record_tenant_id` belt to the retrieval SQL.
- **[GROUNDING] The grounding judge NEVER blocks — only appends a `warn` flag** →
  `local_guardrail.py:541-552` (`severity="warn"`), block-only raise at `:933`. The in-code
  comments "grounding still enforces HALLU=0" (grade.py:98,208; guard_output.py:104) are
  **factually false**. NOTE: not blocking is actually **correct** per sacred #10 (app must not
  override the answer) → HALLU prevention rests on the SYSPROMPT, not the judge. Fix = correct
  the misleading comments; do NOT wire a block (would violate sacred #10).

## HIGH — actionable code fixes

1. **[RETR-F1] 185-gap ROOT**: `query_by_name_keyword("185/60R15")` can't reach the price-sheet
   row when it keys the code differently → priced anchor never enters the entity set → cross-doc
   reconcile no-op → "chưa có giá". Fix = **digit-key parity** between the SQL match predicate
   (`stats_index_repository.py:566-603`) and `_reconcile_cross_doc._digkey` (query_graph.py:313).
2. **[ING-F1] numeric column → price** (Q13-class): an unlabeled numeric column (stock, date-as-int)
   is read as `price_primary` by the pure-money fallback — `document_stats.py:664-672`. Repro:
   `SL tồn=40400 → price_primary=40400`.
3. **[ING-F2] 8-digit date → price**: `20241224` (< 500M ceiling) registers as a price —
   `number_format.py:141-153`, ceiling `_21_streaming_upload.py:69`.
4. **[ING-F3] transposed/pivot tables destroyed**: a `property|value` table yields fake entities +
   drops the real name/price — `document_stats.parse_table_chunks`.
5. **[QU-F1] heuristic mislabels plain-name price factoid as `aggregation`** (conf 0.85 = threshold
   → LLM skipped) → rewrite + fanout + top_k=40 for a 1-fact query — `heuristic_intent_classifier.py:126-134`
   + `i18n.py:284-288`. The code-gate fix only rescued code-bearing queries; plain-name ("giá phòng
   deluxe") still misroutes. Fix = exclude retrieval-bearing intents from the heuristic fast-path
   (or bump the confidence below threshold) → also fixes QU-F2.
6. **[QU-F2] heuristic skips condense for multi-turn aggregation/comparison** → context loss —
   `understand.py:116-142` returns before the condense branch.
7. **[OBS-F2/F3] `provider="unknown"`**: text/streaming path reads `.name`; `ProviderRuntime` DTO
   only has `.code` — `query_graph.py:954` + `:2666`. Two 1-line fixes (`.name`→`.code`).
8. **[OBS-F6] tokenizer cost fallback only on the SYNC path, NOT streaming** (the hottest call):
   `dynamic_litellm_router.py:988-1035` streaming path skips `estimate_tokens_fallback` → streamed
   generation still logs $0. My cost fix is INCOMPLETE for the streaming path.
9. **[OBS-F4/F5] fanout / decompose / grounding write no `model_invocations` row** → their cost is
   invisible in the invocation ledger.

## MEDIUM

- [ING-F4] DOCX horizontally-merged (grid_span) header cells duplicate → column/value misalign — `docx_parser.py:114`.
- [ING-F5] `_is_pure_money` True for non-money (`2024`, dates) → breaks header detection — `tabular_markdown.py:60-72`.
- [ING-F6] English `m`/`M` suffix → millions (`15m`→15M VND) — `number_format.py:81-85`.
- [ING-F7] pipe rows without a leading `|` collapse to 1 cell — `document_stats.py:418` (affects external markdown/Kreuzberg).
- [ING-F8] parser failure → silent fallback to raw text (not fail-loud) — `ingest_core.py:315-344`.
- [RETR-F2] stats `limit=100` cap → list/count silently truncated for >100-row catalogs — `retrieve.py:309`, `query_graph.py:2448`.
- [RETR-F3] superlative drops the numeric bound ("rẻ nhất dưới 2tr" → global top-5) — `query_range_parser.py:270-284`.
- [RETR-F4] `expect_price` anti-fabricate gate only on `op==keyword`, not list/range — `query_graph.py:2316`.
- [RETR-F5] synthetic `chunk_id` sentinel + `score=1.0` → RRF dedup collision (latent) — `query_graph.py:2459`.
- [RETR-F6] RLS docstring false; stats reads use bare `self._sf()` → fail-closed silent-coverage if tenant_ctx unbound — `stats_index_repository.py:6-8,173+`.
- [QU-F3] `understand_query` cache key omits conversation history → stale no-condense reuse — `understand_query_cache.py:60-64`.
- [QU-F4] short word-only comparison (<8 tokens) bypasses decompose → single diluted embedding — `routing.py:114-118`.
- [QU-F5] legacy `router` intent via unordered substring scan → order-dependent mislabel — `router.py:43-49`.
- [GEN-F3] grounding 30% unsupported budget + `max_sentences=5` cap → late fabrication unchecked — `local_guardrail.py:539,422`.
- [GEN-F4] grounding fail-OPEN when judge errors mid-call (returns None=PASS) — `local_guardrail.py:514-520`.
- [GEN-F5] stats route + high-score CRAG skip both disable grounding → ambiguous-column (Q13) has zero check.
- [GEN-F6] `_strip_rules` regex needs `NN. ⭐ NAME`; ALL default rules use `# HEADER` → per-bot `sysprompt_rules_disabled` is a silent no-op — `sysprompt_assembler.py:66-69`.

## LOW
- [RETR-F7] reconcile drops absorbed fragments from STEP-5 attribution. [RETR-F8] routing (original_query)
  vs sizing (rewritten) query-source mismatch. [RETR-F9] rerank safety-net stamp vs CRAG floor.
  [QU-F6/F7] redundant complexity re-classify · factoid node hop. [OBS-F7] monitoring_log admin
  query no tenant filter. [ING minor] `DEFAULT_PRICE_MIN_VND` defined twice.

## Verified NON-issues (sacred #10 intact)
SysPromptAssembler append-only + owner-first + graceful-degrade; critique_parse / reflect /
captured-slots / speculative-router never author or override the answer; RequestLogRepository
enforces tenant; `session_with_tenant` fails loud. **App does not inject or override the answer.**

## Prioritized fix batch (highest leverage first)
1. OBS-F2/F3 provider `.name`→`.code` (2 one-liners) — trivial, unblocks cost attribution.
2. RETR-F1 digit-key parity in `query_by_name_keyword` — closes the known-open 185 gap.
3. ING-F1/F2 numeric-column/date → price guard — closes the Q13-class fabrication.
4. QU-F1/F2 heuristic exclude retrieval-bearing intents — fixes "giá X = aggregation" + condense loss.
5. OBS-F6 streaming tokenizer fallback — completes the cost fix.
6. GEN-F6 default-rule opt-out format — restores per-bot rule disable (incl. my new rules).

## FIX OUTCOMES — 2026-07-02 (evidence-verified, rule#0)

| # | Finding | Status | Evidence / commit |
|---|---|---|---|
| 1 | OBS-F2/F3 provider `.name`→`.code` | **SHIPPED** | `ProviderRuntime` is `slots=True` with only `.code` (model_runtime.py:24-26) → old `getattr(..,"name",..)` ALWAYS returned `"unknown"`. Commit `4e83410`. |
| 2 | RETR-F1 digit-key parity (185 gap) | **SKIPPED — verified FALSE** | Empirical: `query_by_name_keyword("185/60R15")` DOES return priced anchors (6 rows, 2 priced) via `_fold()` notation-fold + attr-text ILIKE (stats_index_repository.py:540-606). The agent's hypothesis was wrong; 185-combined is a decompose-interaction issue, not a keying gap. rule#0 caught it. |
| 3 | ING-F1 numeric-column → price (Q13) | **SHIPPED** | Repro confirmed `SL tồn=40400 → price_primary=40400`. Guard = pure-money fallback fires only when no price col / ragged row / header carries a price token. New helper `_header_has_price_token`. 52 tests pass. Commit `4e83410`. |
| 4 | QU-F1/F2 heuristic misroute (price factoid → aggregation) | **VERIFIED real, DEFERRED (needs load-test)** | Confirmed: `HEURISTIC_INTENT_CONFIDENCE_THRESHOLD=0.85` == the `0.85` conf assigned to aggregation/multi_hop/comparison, and the caller gate is `>=` → LLM skipped; the `aggregation` regex includes `bao nhiêu`. BUT the fix is a routing-policy change (blast radius across ALL aggregation queries), contradicts **9 existing pin tests** that encode current fast-path behaviour + a documented `test_price_inquiry_no_match` comment calling it "intentional". Proven harm is T2 (cost/latency); the T1 "dilutes retrieval" harm is a HYPOTHESIS not measured. Per no-guess-must-measure → **must load-test before shipping**, not a blind flip. |
| 5 | OBS-F6 streaming tokenizer cost fallback | **SHIPPED** | Streaming path logged $0 when provider omits the `usage` chunk. Now accumulates answer deltas + tiktoken-estimates before meter+cost. 2 new tests. Commit `cc5e1ea`. |
| 6 | GEN-F6 default-rule opt-out format | **SHIPPED** | Live seed uses `# HEADER` (alembic 20260627/20260701); `_RULE_BLOCK_RE` matched only `NN. ⭐ NAME` → `sysprompt_rules_disabled` was a SILENT no-op (breaks sacred-exception cond. (c)). Regex + addressing now match both shapes (by number OR folded name). Owner content provably untouched. 11 tests. Commit `86b9190`. |

### Still-open / not-in-this-batch (deferred, rationale)
- **185 combined-query** — decompose interaction (simple query returns 810.000 correctly 3/3; combined query decomposes wrong). Separate from RETR-F1. Needs decompose-path trace.
- **503 innocom gateway** — upstream provider omits `usage` + 503s under ~30-concurrent. Ops/provider issue (not code). OBS-F6 now at least attributes cost for the calls that DO return.
- **XE Q2** — corpus gap (owner must add STK/account data to the KB). Data layer, not code.
- **[TENANT] RLS bypass** — runtime on superuser DSN → provision `ragbot_app` NOBYPASSRLS role (ops) OR add `record_tenant_id` belt to retrieval SQL. Defense-in-depth, not an active leak.
- **8 stale unit-test collection errors** (test-hygiene debt, PRE-EXISTING, unrelated to this batch): import removed symbols `parse_decomposed_sub_queries` / `_cliff_detect_filter` / `_rerank_threshold_gate`, and a FastAPI `_EffectiveRouteContext` version drift. Worth a cleanup pass.
