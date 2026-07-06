# GAP RE-CHECK — LUỒNG 2 (QUERY) — post 9-commit audit fix

> **Slug:** gap-L2-query
> **Scope:** LUỒNG 2 (understand→retrieve→rerank→grade→generate→guard) + the S1/O4/O5/I-side spillover touched by the same 9 commits.
> **Anchor commit:** `6caeb9c` (HEAD, branch `fix-260623-ingest-expert`); fix range `da37778..6caeb9c`.
> **Method:** READ-ONLY re-read of CURRENT source + run the pinned tests. Every claim below carries `file:line` evidence. Labels: **FACT** = verified against source/test output this session; **HYPOTHESIS** = inferred, not runtime-proven.
> **rule#0:** no claim without evidence. No source edited.

---

## 0. Executive verdict

The 9 commits landed and are, for the most part, **correct at the code level** — I re-read each changed file and confirmed the fix matches the intent (not just the commit message). `pytest --co` collects **6678 tests with 0 collection errors** (FACT) so CI is un-broken (Q15/O1 closed). The S1 state-key declarations, Q2 stats-grounding restore, Q3/Q18 GraphRAG kwarg+chunk-id, Q5 ai_keys schema, Q13 rerank index-map, Q17 soft-delete filter, S-3 idempotency, O4 audit-guard, O5 webhook-except, I2 OCR-sync, I4 PII-wire, I5 shape-header all verify as present and shaped right. **BUT two material gaps survive inside the "handled" set:** (1) the 12 mirage-knobs were added ONLY to the `test_chat` builder — the **production WORKER builder** (`chat_worker/pipeline_config.py`) is missing **11 of the 12**, so bot-owner overrides for those knobs are silently ignored on the real B2B path (FACT); and the parity guard does NOT catch it (FACT — 3 passed with the gap present). (2) The **AST pin only guards node-RETURN dict literals, not in-place `state[k]=` writes** — 4 undeclared in-place keys exist today, one of which (`resolved_answer_model`) is the Q6 cascade key (FACT). The remaining ~12 register findings NOT in the commit set (Q4/Q6/Q7/Q8/Q9/Q10/Q11/Q12/Q14/Q16/O2/O3) are confirmed still open. HALLU-net is intact on the stats route by default (grounding stays ON). No new HALLU breach found.

---

## 1. HANDLED — re-read source to CONFIRM the fix landed

| ID | Finding | Verdict | Evidence (file:line) |
|---|---|---|---|
| **Q1/S1** | Cross-node state-keys declared | **CONFIRMED** | `state.py:211-225` declares 14 cross-node keys (`bot_extra_output_tokens_per_response`, `raw_user_message`, `rerank_score_mode`, `embedding_column`, `_total_graph_iterations`, `crag_skip_retry`, `_corpus_version`, `retrieval_degraded`, `embed_degraded`, `cache_hit`, `chunks_used`, `crag_skip_reason`, `grade_timeout_fallback`, `bot_created_at`). Handoff verified live: `raw_user_message` written `graph_assembly.py:177`, read `generate.py:251`, declared `state.py:213`. `embedding_column` written `query_graph.py:1378/1434`, read `:429-430`. **14 declared, tests green.** |
| **Q1 AST pin** | Structural guard | **CONFIRMED (partial coverage)** | `test_graphstate_key_pin.py` walks node-RETURN dict literals only (`_returned_keys`, line 58-70). `pytest` → **14 passed, 1 xfailed** (FACT). ⚠ Does NOT cover in-place `state[k]=` writes — see §3-UNCTRL-A. |
| **Q2** | Stats-route grounding gate restored (HALLU-net) | **CONFIRMED** | `guard_output.py:106-110` now gates on `_pcfg(state,"stats_route_skip_grounding", DEFAULT_STATS_ROUTE_SKIP_GROUNDING)`; default constant `= False` (`_15_m2_neighbor_window_expansion.py:124`) ⇒ grounding STAYS ON for stats. `143ff38` reverses the unconditional skip from `3097755`. Sacred #10 intact (judge re-enabled, no answer override). |
| **Q3** | GraphRAG kwarg `record_bot_id` | **CONFIRMED** | `graph_retriever.py:61` calls `query_graph(record_bot_id=record_bot_id,...)`; `ingest_core.py:795` calls `store_triples(record_bot_id=bot_uuid,...)`. Signatures: `knowledge_graph.py:130,182` both declare `record_bot_id`. |
| **Q18** | GraphRAG synthetic chunk_id | **CONFIRMED** | `graph_retriever.py:87` `"chunk_id": f"graph_synthetic_{_i}"` (non-falsy) so generate() no longer drops graph triples. |
| **Q5** | ai_keys schema prefix removed | **CONFIRMED** | `ai_config_repository.py:664,689,708,731,747` all use bare `ai_keys` (no `ragbot.` prefix). Test `TestSEC3AiKeysSchema` green. |
| **Q13** | Reranker index-map alignment | **CONFIRMED** | `litellm_reranker.py:74-80` builds `passage_chunk_idx`; `:107-108` maps `chunks[passage_chunk_idx[idx]]` — empty-content chunks no longer misalign scores. |
| **Q17** | Soft-delete fallback filter | **CONFIRMED (scoped)** | `bm25_only_stage2.py:80`, `keyword_stage3.py:105`, `parent_expand_stage4.py:90` each add `AND d.deleted_at IS NULL`. `hybrid_stage1` correctly delegates to `vector_store.hybrid_search` (filter lives in the pgvector store, not here) — consistent with the 3-stage scope. |
| **Q15/O1** | Re-exports restored, CI un-broken | **CONFIRMED** | `query_graph.py:281-314` re-exports `CRAG_GRADE_IRRELEVANT`, `_cliff_detect_filter`, `_rerank_threshold_gate` (+siblings). `pytest --co` = 6678 collected, **0 collection errors** (FACT). |
| **S-3** | Idempotency includes bot identity | **CONFIRMED** | `idempotency_key.py:for_ingest_document` now takes `record_bot_id` + `workspace_id` and folds them into the key. Mirrors `for_chat_message`. Test `TestSEC4` green. |
| **O4** | Audit-insert guard | **CONFIRMED** | `invocation_logger.py` wraps the finally-INSERT in `try/except Exception` (BLE001 noqa, aux-sink) + structured warning — DB blip no longer kills a successful LLM turn. |
| **O5** | Webhook dispatcher catches RedisError | **CONFIRMED** | `webhook_dispatcher.py:30` imports `RedisError`; `:332` `except (OSError, RedisError)`. redis-py's own hierarchy now honored. |
| **I2** | OCR fallback returns blocks | **CONFIRMED** | `kreuzberg_parser.py:245-251` prefers `extract_bytes_sync` (getattr) with defensive rename guard — no longer awaits a coroutine in a sync `def`. |
| **I4** | PII redactor wired | **CONFIRMED (caveat)** | `bootstrap.py:450-455` `providers.Singleton(build_pii_redactor, provider=providers.Callable(lambda: get_boot_config("pii_redactor_provider", DEFAULT_PII_REDACTOR_PROVIDER)))`. Provider now read from config vs frozen `"null"`. ⚠ It is `Singleton` (built once), while the comment says "PER-CALL" — provider is resolved at container build, not per request. Still a real fix (config now honored); the "per-call" language is inaccurate but behavior is correct for a process-lifetime provider. |
| **I5/S3** | Shape-only header fallback | **CONFIRMED** | `document_stats.py:_is_shape_header` (added `3edc50c`): promotes out-of-vocab tabular first-row by FORM (≥2 label cells, no money/number/bullet, ≥2 consistent grid rows). Wired into `parse_table_chunks` via `or (not header and _is_shape_header(...))`. Test `test_out_of_vocab_csv_still_extracts_entities` green. Domain-neutral (shape not vocab). |
| **Mirage-knobs (test_chat)** | 12 per-bot knobs populated | **CONFIRMED for test_chat ONLY** | `test_chat/_pipeline_config.py` (`6caeb9c`) adds `cross_doc_reconcile_enabled`, `xml_wrap_enabled`, `stats_route_skip_grounding`, `stats_code_lookup_enabled`, `stats_price_of_entity_enabled`, `stats_superlative_enabled`, `generate_surface_verbatim_enabled`, `grounding_failure_mode`, `guardrail_leak_min_match_count`, `sysprompt_leak_skip_intents`, `sysprompt_leak_skip_stats_route` (+`bot_custom_vocabulary`). **See §2-NH-MIRAGE — WORKER path missing 11/12.** |

---

## 2. NOT_HANDLED — register items NOT in the fixed set, confirmed still open

| ID | Finding | Severity | Why still open (evidence) |
|---|---|---|---|
| **NH-MIRAGE (NEW/critical spillover)** | Worker builder missing 11/12 mirage-knobs | **HIGH** | `chat_worker/pipeline_config.py` (production B2B path) contains ONLY `bot_custom_vocabulary` (:286); the other **11** are ABSENT (FACT — grep loop, all "ABSENT"). Because `_pcfg` returns the caller default when a key is missing (`query_graph_helpers.py:177-178`), the per-bot `resolve_bot_limit` override for these 11 knobs is **silently ignored on production**. Read sites confirm each is `_pcfg`-read: `stats_route_skip_grounding` guard_output.py:107, `stats_code_lookup_enabled` retrieve.py:227, `stats_price_of_entity_enabled` retrieve.py:238, `stats_superlative_enabled` retrieve.py:258, `generate_surface_verbatim_enabled` generate.py:613, `grounding_failure_mode` guard_output.py:241, `guardrail_leak_min_match_count` guard_output.py:249, `sysprompt_leak_skip_intents` guard_output.py:255, `sysprompt_leak_skip_stats_route` guard_output.py:270, `cross_doc_reconcile_enabled` query_graph.py:2421, `xml_wrap_enabled` query_graph.py:491. **Defaults are HALLU-safe** (stats grounding still ON via constant False), so no breach — but bot-owner configurability is dead on prod, i.e. the fix only reached the test harness. **Needs:** add the 11 `resolve_bot_limit(...)` entries to the worker dict + extend the parity test to compare the worker *dict body* (not just the `_CHAT_CONFIG_KEYS` tuple). |
| **Q4** | Grounding gate inverted (confirmed-ungrounded → warn/ship; judge-dead → refuse) | HIGH | `guard_output.py:503-519`: a `grounding_hit` from the sync judge only APPENDS a flag + persists — it does NOT substitute the OOS template (only `regex_result` GuardrailBlocked short-circuits with `answer_type:"blocked"` at :472-477). The `llm_fn is None` (judge dead) path DOES refuse via `fail_closed` (:234-246, default `GROUNDING_FAILURE_MODE_FAIL_CLOSED`). So a CONFIRMED-fabricated answer still ships while a judge-outage refuses — the register's "owner decision" item is untouched. |
| **Q6** | Cascade routing no-op | HIGH | `generate.py:408` writes `state["resolved_answer_model"]` but `_invoke_llm_node` (`query_graph.py:960-984`) resolves the model exclusively via `model_resolver.resolve_runtime(purpose=...)` — it never reads `resolved_answer_model`. The key is also UNDECLARED in state.py (in-place write, dropped across boundaries). Cascade still cosmetic (log event only). |
| **Q7** | parent_chunk_id never SELECTed | HIGH | Not in commit set. `parent_chunk_id` present in ingest/store + `parent_expand_stage4` but not projected into the primary retrieve SELECT — unchanged by the 9 commits. |
| **Q8** | count≠list + price-range OR/AND | HIGH | `retrieve.py:181-256` range/code/price/list parsing unchanged by the commits; the count-vs-list fold-set mismatch and range OR/AND bug persist. |
| **Q9** | heuristic `0.85 ≥ 0.85` + locale not wired | HIGH | Not in commit set — no change to understand_query confidence gate or signal threading. |
| **Q10** | per-bot embed dim ignored + vector(1280) locked | HIGH | Not in commit set — embedder dim validation at binding unchanged. |
| **Q11** | `SPECULATIVE_REDO_SENTINEL` leak | HIGH | `speculative_router.py:63,423,500` still `yield SPECULATIVE_REDO_SENTINEL`; redo protocol not implemented / not gated off. Not in commit set. |
| **Q12** | reranker construct per-turn (CB defeat) | HIGH | `rerank.py:90` `await reranker_resolver.resolve_for_bot(_bot_uuid)` per turn; no singleton-per-binding cache. Not in commit set. |
| **Q14** | streaming no failover + fallback price | MED | Not in commit set. |
| **Q16** | RLS dead superuser + fallback bare session | HIGH (posture) | Code sets `SET LOCAL app.tenant_id` (`engine.py:174`), but RLS enforcement depends on ops provisioning a NOBYPASSRLS role — an ops/DB item, not code, untouched by the 9 commits. |
| **O2** | Verification tier missing (numeric-fidelity / citation-validate / completeness) | HIGH | grep for `numeric_fidelity|citation_validate|completeness_check|verification_node` in orchestration → **0 hits** (FACT). The whole post-generate verification subsystem does not exist. Path ends `generate→guard_out(shingle)→END`. |
| **O3** | Redis-Streams recovery no re-dispatch | HIGH | Not in the 9-commit set (only O4/O5 observability items were touched). Still open. |
| **I9 (int-price)** | date-shaped 8-digit int accepted as price | MED | Deferred by design — `test_audit_pass2_repro.py:82-94` is `@pytest.mark.xfail(strict=True)` (the 1 xfail in the run), documenting the date/serial-as-price gap remains for a load-test-gated Phase-3 fix. |

---

## 3. UNCONTROLLED — silent-degrade / no-guard / no-fail-loud (incl. NEW)

| Path | Risk | Evidence |
|---|---|---|
| **UNCTRL-A · AST pin blind to in-place writes** | The S1 guard's docstring says it pins "every key a node RETURNS" — but a node that writes `state["newkey"]=…` in-place (not via return dict) with `newkey` undeclared is **NOT** caught. A future undeclared in-place cross-node key silently drops again. **4 undeclared in-place keys exist NOW** (FACT, AST walk this session): `resolved_answer_model` generate.py:408 (= Q6 cascade), `grounding_async_task` query_graph.py:871, `multi_query_skipped_simple` query_graph.py:1945, `_uq_cache_hit` understand.py:88. The latter 3 have no cross-node reader (within-node/observability) so no live bug — but the coverage hole is real. | `test_graphstate_key_pin.py:58-70` (`_returned_keys` only walks `ast.Return`→`ast.Dict`). |
| **UNCTRL-B · Worker builder silently drops 11 owner knobs** | Same root as NH-MIRAGE. A bot owner setting `plan_limits.stats_superlative_enabled=false` (or any of the 11) on the **production** path has NO effect — `_pcfg` falls back to the constant with zero warning. Silent config-degrade. | `chat_worker/pipeline_config.py` (11 keys absent) + `query_graph_helpers.py:177` (missing key → default, no log). |
| **UNCTRL-C · Parity test gives false assurance** | `test_pipeline_cfg_keys_parity.py` checks `_pcfg` keys against the **test_chat dict body** (`_extract_dict_keys(_TEST_CHAT, ...)`, line 127) and the `_CHAT_CONFIG_KEYS` **tuple** in `config.py` — it never inspects the **worker dict body**. So the worker can miss any builder key and the guard stays green (FACT: 3 passed with the 11-key gap live). | `test_pipeline_cfg_keys_parity.py:35-38,126-135` (test_chat dict + worker tuple, no worker dict). |
| **UNCTRL-D · Stats route bypasses rerank+grade (design)** | Even with Q2's grounding restored, the stats route still short-circuits rerank+grade (synthetic score=1.0). Grounding is the ONLY net; if a bot opts out via `stats_route_skip_grounding=true`, the stats answer has zero verification. Warn-only, per-bot foot-gun. | guard_output.py:106-110 (opt-out gate exists). |
| **UNCTRL-E · Grounding judge warn-only on confirmed breach** | (= Q4) A confirmed-ungrounded answer degrades to a persisted flag, not a refusal — silent to the end user. | guard_output.py:503-519. |
| **UNCTRL-F · GraphRAG `except Exception` still swallows** | `graph_retriever.py:111` `except Exception: # noqa: BLE001` → `warning` + return empty context. Q3 fixed the kwarg so the call no longer TypeErrors, but any real KG failure still degrades silently to 0 graph chunks with no fail-loud metric. | graph_retriever.py:111-113. |

---

## 4. Test evidence (FACT, this session)

- `pytest tests/unit/test_graphstate_key_pin.py tests/unit/test_audit_pass2_repro.py` → **14 passed, 1 xfailed** (the xfail = I9 date-int-price, deferred).
- `pytest tests/unit/test_pipeline_cfg_keys_parity.py` → **3 passed** — passes DESPITE the worker missing 11 knobs (proves the guard does not cover the worker dict body).
- `pytest tests/unit/ --co` → **6678 collected, 0 collection errors** (Q15/O1 closed).
- AST walk (custom, this session) → 4 undeclared in-place `state[k]=` writes across `orchestration/nodes` + `query_graph.py` + `graph_assembly.py`.
- grep `numeric_fidelity|citation_validate|completeness_check|verification_node` in `orchestration/` → 0 hits (O2 subsystem absent).
- grep loop over 12 mirage-knobs in `chat_worker/pipeline_config.py` → 1 PRESENT (`bot_custom_vocabulary`), 11 ABSENT.

---

## 5. Bottom line for the register

- **Genuinely closed (code correct + verified):** Q1/S1 (declarations), Q2, Q3, Q5, Q13, Q15/O1, Q17, Q18, S-3, O4, O5, I2, I5. (13)
- **Closed with caveat:** I4 (Singleton not per-call; behavior OK), Q1-AST-pin (return-only coverage), mirage-knobs (test_chat only).
- **Still open (unchanged by 9 commits):** Q4, Q6, Q7, Q8, Q9, Q10, Q11, Q12, Q14, Q16, O2, O3, I9. (13)
- **NEW gap surfaced by this re-check:** worker-builder missing 11/12 mirage-knobs + parity guard blind to it (NH-MIRAGE / UNCTRL-B / UNCTRL-C) — the single most consequential "handled-but-not-really" item, because it means the mirage-knob fix reached only the internal test endpoint, not the production B2B path.
