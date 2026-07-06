# Independent Code Review — 9 audit commits (da37778..6caeb9c)

Branch: `fix-260623-ingest-expert`. READ-ONLY. rule#0: every claim below has `file:line` evidence and is labelled FACT (verified by re-reading source / running tests) vs HYPOTHESIS.

Commit order (oldest→newest):
```
da37778 P0 un-break CI + S1 state-keys + GraphRAG/ai_keys/idempotency
143ff38 P1 restore stats-route grounding gate (HALLU-net)
1485782 P1 soft-delete fallback filter + audit-insert guard + GraphRAG chunk id
8e14205 P1 LiteLLMReranker index alignment (empty content)
8b792b9 P2 OCR fallback blocks + PII provider wired + diff-reingest landmine
3edc50c P3 shape-only header fallback (S3)
a28d88b P2 webhook dispatcher catches redis-py exceptions
ee6ccb2 style comments to WHY-only
6caeb9c fix 7 pre-existing failing tests (wire-pins + 12 mirage knobs)
```

---

## PER-COMMIT VERDICT

### da37778 — VERDICT: CORRECT
- **S1 state-keys** — FACT: 14 keys declared at `state.py:211-225`, all present, 0 dups, 72 total keys (`python typing.get_type_hints` run: `dups: []`, `all14_present: True`). AST pin `test_graphstate_key_pin.py` + `test_audit_pass2_repro.py` = 14 passed / 1 xfailed. Types are plausible (`int/str/bool/Any`), each carries a WHY comment. Guard proves used-keys ⊆ declared-keys → the S1 drop class is closed.
- **GraphRAG kwarg** — FACT: `graph_retriever.py:61` + `ingest_core.py:800` pass `record_bot_id=`; receiving `knowledge_graph.py:130` (`store_triples`) + `:182` (`query_graph`) both declare `record_bot_id: UUID`. Rename is correct; old `bot_id=` raised TypeError (swallowed → 0 triples). CORRECT.
- **ai_keys schema** — FACT: `ai_config_repository.py:664/689/708/731/745` now `ai_keys` (no `ragbot.` prefix) on all 5 statements. CORRECT.
- **idempotency SEC-4** — FACT: `idempotency_key.py:for_ingest_document` now takes `record_bot_id` + `workspace_id`, key parts = `("ingest", tenant, ws|"system", bot, url, version)`. `cmd.record_bot_id`/`cmd.workspace_id` exist (used at `ingest_document.py:76/95/116`). Mirrors `for_chat_message`. CORRECT. (Minor style nit: line 60 assumes `cmd.record_bot_id`, line 63 uses defensive `getattr(cmd,"workspace_id","")` — inconsistent but both attrs exist, no runtime risk.)
- **F2 re-export** — FACT: `query_graph.py:277-296` restores the `retrieval_filter` re-exports; import smoke clean, no circular import.

### 143ff38 — VERDICT: CORRECT (sacred #10 intact)
- FACT: gate restored at `guard_output.py:106-110`: `_stats_skip_grounding = _pcfg(state,"stats_route_skip_grounding", DEFAULT_STATS_ROUTE_SKIP_GROUNDING)`; the skip only fires `if _stats_skip_grounding and retrieve_mode.startswith("stats")`.
- FACT: `DEFAULT_STATS_ROUTE_SKIP_GROUNDING = False` (`constants/_15_m2_neighbor_window_expansion.py:124`) → grounding **STAYS ON** for stats by default (HALLU-safe). This reverses the unconditional skip that `3097755` had introduced.
- FACT (sacred #10): this only re-enables the JUDGE running. `guard_output.py:65-67` states the app "does NOT regex-check + override the answer here". The fail path (`_grounding_fail_closed`, `:221-246`) substitutes the bot's `oos_answer_template` (the existing refuse branch), explicitly documented as NOT an answer-override. No grounding→mutate-answer path. Compliant.

### 1485782 — VERDICT: CORRECT
- **Q17 soft-delete** — FACT: `AND d.deleted_at IS NULL` added to all 3 rescue stages: `bm25_only_stage2.py:80`, `keyword_stage3.py:105`, `parent_expand_stage4.py:90`. `documents.deleted_at` is real (soft-delete write at `document_service/__init__.py:1070`; main store path already filters it). Closes a real soft-delete resurrection hole. No 500 risk.
- **O4 audit-insert guard** — FACT: `invocation_logger.py` wraps the audit INSERT in `try/except Exception` with `# noqa: BLE001` + reason + structured warning (aux sink, money path already succeeded). Compliant with broad-except policy (aux-sink graceful-degrade).
- **Q18 GraphRAG chunk id** — FACT: `graph_retriever.py:87` `chunk_id=f"graph_synthetic_{_i}"` (was None). `generate.py:596-599` gates `chunk_ids_allowed` on `c.get("chunk_id") or c.get("id")` being truthy → None was excluded; the synthetic string is truthy and citation matching is lowercased-string (`generate.py:809+`), no UUID parse → no crash. CORRECT.

### 8e14205 — VERDICT: CORRECT
- FACT: `litellm_reranker.py:74-80` builds `passage_chunk_idx` map; `:107-108` translates `idx` back via `chunks[passage_chunk_idx[idx]]` (was `chunks[idx]`). `:113` sets `"score": score` so the pin test's `c["score"]` assertion is valid. Empty-content chunk in the middle no longer shifts scores onto the wrong chunk. Test `test_litellm_reranker_index_align.py` passes. CORRECT.

### 8b792b9 — VERDICT: CORRECT (one minor cleanliness nit)
- **I2 OCR** — FACT: `kreuzberg_parser.py:245-248` prefers `extract_bytes_sync` (falls back to `extract_bytes`); `:333-355` content fallback builds blocks from `.content` when `.elements` is None. `active_heading` is defined at `:286` (before the fallback loop at `:333`) → no NameError. Consistent heading tracking. CORRECT.
- **I4 PII** — FACT: `bootstrap.py:450-454` `pii = providers.Singleton(build_pii_redactor, provider=providers.Callable(lambda: get_boot_config("pii_redactor_provider", DEFAULT_PII_REDACTOR_PROVIDER)))`. `get_boot_config` imported `:152`; pattern matches embedder (`:263`) / reranker (`:382`) / crag (`:402`). Frozen compile-time constant is gone → system_config now takes effect. CORRECT.
- **I17 diff-reingest** — FACT: `ingest_core.py:688-698` the dead `_diff_reingest_compute`/`_diff_reingest_log_event` calls are replaced with a single `logger.warning("diff_reingest_telemetry_not_implemented", …)`; flag still readable at `:682-688`, no NameError, doc not stranded. CORRECT.
- **NIT (low)**: I17 removed the only use of `DEFAULT_EMBED_COST_USD_PER_1M_TOKENS` but left the import at `ingest_core.py:71` → now an unused import (F401 confirmed by ruff; count 2→1 vs base `8b792b9~1`). Harmless at runtime (constant still exists); this file already carries a pre-existing F401 backlog (asyncio/re/Counter), so ruff-F401 is not CI-gating here.

### 3edc50c — VERDICT: CORRECT with a CONCERN on gate-tightness
- FACT: `_is_shape_header` added at `document_stats.py:390-427`; call site `:1063` fires only `(not header and _is_shape_header(...))` → happy path byte-identical. Gate: ≥2 label cells (no money via `parse_money_vn`, no pure-number, no bullet/`_is_discourse_opener`) AND ≥2 following rows same column-count and `not _is_prose_row`.
- FACT (measured): canary `test_multibot_ingest_canary.py` = 59 passed (matches commit). S3 repro `test_out_of_vocab_csv_still_extracts_entities` now green; the 8-digit date-vs-price case is honestly left `xfail(strict)` (`test_audit_pass2_repro.py`) → still open, correctly deferred.
- **CONCERN (medium)** — the anti-prose guard leans on `_is_prose_row` (`:865-882`) which only rejects a row when its LAST cell ends on a sentence terminator (`_STATS_SENTENCE_END`) and no cell is a price. A short comma-phrase list with a consistent column count and NO terminal punctuation slips through. FACT (executed): input `['quyền lợi, nghĩa vụ','trách nhiệm, bổn phận','nội dung, hình thức']` → `_is_shape_header(...) == True` (prose promoted to header). Impact is bounded: fires only when no vocab/separator header was found, and only feeds best-effort positional stats entities (answer-path grounding still applies on the vector route); it is NOT an answer-path HALLU. But the commit's claim "prose never over-promotes" is slightly overstated for the unterminated-consistent-grid case.

### a28d88b — VERDICT: CORRECT
- FACT: `webhook_dispatcher.py:31` imports `redis.exceptions.RedisError`; `:332` and `:355` change `except (OSError, ConnectionError, TimeoutError)` → `except (OSError, RedisError)`. Since redis-py `ConnectionError`/`TimeoutError` subclass `RedisError` and the builtins subclass `OSError`, the new set is a strict superset — nothing newly uncaught. Fail-open contract now honoured on a real Redis outage. Narrow-except, compliant.

### ee6ccb2 — VERDICT: CORRECT
- FACT: diff is comment/docstring-only; the flagged `+` lines are trailing-comment rewrites on unchanged code lines (`ingest_core.py`, `idempotency_key.py`, `graph_retriever.py`, `webhook_dispatcher.py`). No logic/behaviour change. Audit-batch comments (audit L2-4 / Q13 / SEC-4 / 2026-07-03 / commit-hash) are gone from current source; version-ref grep clean on all touched src files. Pre-existing 2026-05/06 temporal comments were intentionally left (surgical scope).

### 6caeb9c — VERDICT: CORRECT with a scoped CONCERN on `xml_wrap_enabled`
- **Wire-pins** — FACT: `query_graph.py:297-308` re-exports `_decide_keep_speculative`, `apply_cascade_routing`, `DEFAULT_GROUNDING_CHECK_ENABLED`, `DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD`; import smoke clean (no circular import); wire-pin tests pass.
- **12 mirage-knobs** — FACT: added to `test_chat/_pipeline_config.py:857-901` with `resolve_bot_limit(..., system_default=DEFAULT_*)`. For 11 of 12 the builder default equals the node's `_pcfg` fallback → NO behaviour change when a bot has no override:
  - `stats_route_skip_grounding` = `DEFAULT_STATS_ROUTE_SKIP_GROUNDING(False)` = node `guard_output.py:107`.
  - `stats_code_lookup_enabled`/`stats_price_of_entity_enabled`/`stats_superlative_enabled` = `DEFAULT_*(True/True/True)` = node `retrieve.py:227/238/258`.
  - `generate_surface_verbatim_enabled` = `DEFAULT_*(False)` = `generate.py:613`.
  - `grounding_failure_mode` = `'fail_closed'` = `guard_output.py:241`.
  - `guardrail_leak_min_match_count` = `10` = `guard_output.py:249`.
  - `sysprompt_leak_skip_intents` = `('greeting','chitchat')` = `guard_output.py:255`.
  - `sysprompt_leak_skip_stats_route` = `True` = `guard_output.py:270`.
  - `cross_doc_reconcile_enabled` = literal `True` = node `query_graph.py:2421` `_pcfg(...,True)` (both inline `True`, no named constant — pre-existing zero-hardcode nit in the node, mirrored not introduced).
  - `bot_custom_vocabulary` = `custom_vocabulary or {}` = node `_pcfg(...,{})`.
- **CONCERN (medium, test-harness-scoped)** — `xml_wrap_enabled`: builder now injects `resolve_bot_limit(...,system_default=DEFAULT_XML_WRAP_ENABLED=False)` → `False` when no override. The node resolver `query_graph.py:491-508` does `explicit=_pcfg(state,"xml_wrap_enabled",None); if explicit is not None: return bool(explicit)` — so a concrete `False` **bypasses the `bot_created_at` date-default-on branch** that previously (key-absent → None) could return True for post-cutoff bots. This IS a behaviour change vs the commit's "behaviour unchanged" claim, but it is SCOPED to `interfaces/http/routes/test_chat/_pipeline_config.py` (the internal QA harness; `xml_wrap_enabled` is populated in NO production builder — grep confirms only this file + the node). The register (§2) already classified the date-default-on path as effectively dead, so real impact is negligible; the explicit opt-in `plan_limits.xml_wrap_enabled=True` still works.
- **Stale tests** — FACT: `test_guard_output_parallel.py` re-pinned to `DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED` (the constant the node actually reads). `test_t2_perf_fixes::test_crag_grade_bounded_concurrency` skipped with a precise reason; skip is HONEST — the semaphore is alive at `grade.py:320-322` (`asyncio.Semaphore(max(1,_grade_concurrency))`, `async with _grade_sem`). Result 56 passed / 1 skipped / 0 failed.

---

## STILL-OPEN (register findings NOT in the fixed set — spot-verified open)
- I1 Path A/B split (worker flatten) — Phase 2, not in these 9 commits.
- Q11 SPECULATIVE_REDO_SENTINEL leak — `orchestration/nodes/speculative_retrieve.py` still present, Phase 1 residual not touched here.
- S-2/Q16 RLS dead runtime (superuser DSN) — ops cutover, Phase 4, untouched.
- Q4 grounding gate direction (owner decision on escalate-vs-observe) — the fail-closed knob exists (`guard_output.py:240-246`) but the broader warn-vs-block inventory (O6) is Phase 1 open.
- Register Phase 2/3/4 backlog (I3/.doc-xls, I6 coverage-repair, I8 AdapChunk, I9/I18/I19 currency+delimiter, Q6-Q12 retrieval, O2/O3/O7/O8) — all outside this batch.

## UNCONTROLLED / silent-degrade (audit's core concern)
- `_is_shape_header` over-promotes an unterminated consistent comma-grid to a header (FACT above) — degrades silently to best-effort entities; no fail-loud, no log when a shape-promote happens. (New, from 3edc50c.)
- `xml_wrap_enabled` date-default-on branch silently bypassed in the test-chat builder (FACT above) — no warning that the resolved `False` short-circuited the cutoff logic. (From 6caeb9c, harness-scoped.)
- I17 leftover unused import `DEFAULT_EMBED_COST_USD_PER_1M_TOKENS` (`ingest_core.py:71`) — cosmetic; no guard, but no runtime effect.

## COMPLIANCE (sacred rules on these 9 commits)
- #10 (no app-override / no app-inject): PASS — grounding restore re-enables JUDGE only; fail path is the existing `oos_answer_template` refuse branch (`guard_output.py:221-246`), not an answer mutation.
- #7 (no psql hotfix): PASS — all DB-shape fixes are code (SQL string / kwarg / schema-prefix); no `psql UPDATE` to protected content columns.
- no-version-ref: PASS — grep clean on touched src; audit-batch temporal comments scrubbed by ee6ccb2.
- domain-neutral: PASS — `_is_shape_header` is shape-only (no vocab/brand); no per-bot literal introduced.
- zero-hardcode: MOSTLY — knobs use named `DEFAULT_*` constants; two inline `True` literals for `cross_doc_reconcile_enabled` (node + builder) are pre-existing, mirrored not introduced.
- 4-key: PASS — SEC-4 idempotency now scopes `(tenant, workspace, bot, url, version)`.
- broad-except: PASS — O4 + O5 both narrow/annotated with `# noqa: BLE001` reason (aux sink).
