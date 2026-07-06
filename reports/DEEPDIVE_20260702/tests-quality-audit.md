# TEST SUITE QUALITY AUDIT — tests-quality-audit

- **Date**: 2026-07-03
- **Branch / HEAD during audit**: `fix-260623-ingest-expert` @ `6796cd9` (2026-07-02 16:55:31 +0700). HEAD moved mid-audit (conversation started at `949a3a4`) — other agents commit concurrently; all runtime numbers below are snapshots against `6796cd9` with only `z_luannt_deubg.txt` dirty (`git diff --stat`).
- **Method**: AST scan of every `tests/unit/**/test_*.py` (assert taxonomy), 2× full-suite runs (`pytest tests/unit -q -n 4`, env sourced), 1× targeted run of all 66 `tests/_xfail_list.txt` node IDs, module-to-test import mapping over 649 src modules, seeded 15-file random sample read for mock/drift judgment.
- **Cross-reference**: sibling report `reports/DEEPDIVE_20260702/tests-full-run.md` (failure root-cause per test). This report covers suite QUALITY; failure forensics are deliberately summarized, not duplicated.

---

## 0. Headline numbers (FACT — measured this session)

| Metric | Value | Evidence |
|---|---|---|
| Test files under `tests/unit` | 737 | `find tests/unit -name "test_*.py" \| grep -v __pycache__ \| wc -l` = 737 |
| Test functions (AST, pre-parametrize) | 6,241 | AST scan output `TOTAL_TEST_FUNCS=6241` |
| Full-suite result (run 1, `-n 4`) | **67 failed, 6439 passed, 32 skipped, 29 xfailed, 40 xpassed, 8 errors — 90.41s** | scratchpad `full_run.txt` last line |
| Full-suite result (run 2, `-n 4`) | **67 failed, 6439 passed, 32 skipped, 29 xfailed, 40 xpassed, 8 errors — 78.93s** | scratchpad `full_run2.txt` last line |
| Sibling serial run (no xdist) | 67 failed, 6439 passed, 32 skipped, 36 xfailed, 33 xpassed, 8 errors — 205s | `reports/DEEPDIVE_20260702/tests-full-run.md` §1 |
| Plain `pytest tests/unit -q` (no flags) | **aborts: 8 collection errors, 0 tests executed** | `tests-full-run.md` §1 Run 0; independently confirmed — passing bad node IDs yields pytest exit=4 usage-abort (my xfail-list run, §2 below) |

**FACT**: failure/pass counts are identical across 3 independent runs (2 mine with `-n 4`, 1 sibling serial) → the 67 failures are deterministic, not xdist artifacts. **FACT**: xfail/xpass split varies across runs (29/40 with `-n 4` vs 36/33 serial) → 7 tests flip xfail↔xpass with execution order (§2.3).

---

## 1. Weak assertions

### 1.1 `assert True` — 1 real occurrence; grep is misleading without AST

- `grep -rn "assert True" tests/unit` returns 20 hits — **19 of 20 are docstrings/comments advertising the opposite** ("real assertions, no ``assert True``"), e.g. `tests/unit/test_table_narrator.py:5`, `tests/unit/test_streaming_response.py:14`.
- **FACT — exactly 1 executable `assert True`**: `tests/unit/test_retrieve_empty_early_exit.py:47` — `assert True  # Graph wiring verified in query_graph.py line ~3653`.

### 1.2 The one genuinely vacuous file (FACT, read in full)

`tests/unit/test_retrieve_empty_early_exit.py` — all 3 tests are fake:

| Line | Test | What it actually does |
|---|---|---|
| 14–29 | `test_retrieve_route_returns_generate_when_chunks_empty` | builds a local dict, asserts `state.get("retrieved_chunks") or [] == []` on **its own literal** (line 27–28). Never calls `_retrieve_route`. |
| 32–39 | `test_retrieve_route_returns_rerank_when_chunks_present` | body is `pass  # Verified by code review: ...` (line 39) |
| 42–47 | `test_pipeline_graph_accepts_generate_edge_from_retrieve` | `assert True` (line 47) |

This file contributes 3 green ticks to "passed" while pinning nothing. It also violates CLAUDE.md Test rules ("WEAK (NOT acceptable): `assert True`") and the comment violates the no-version-ref/comment rule (references a line number `~3653` that drifts).

### 1.3 Zero-assert test functions: 42 (mostly legitimate no-raise contracts)

AST scan (excluding `@pytest.fixture`-decorated `test_*` names and functions using `pytest.raises`/mock `assert_called*`): **42 zero-assert test functions**. Spot-checks show the majority are legitimate "must not raise" contracts:

- `tests/unit/test_citation_policy.py:42–52` — `policy.validate([], frozenset())` where raising = failure. LEGIT.
- `tests/unit/test_rbac.py:93` — `require_min_level(_fake_request("superadmin"), 100)  # should not raise`. LEGIT.
- `tests/unit/test_tenant_guard.py:27–69` (5 funcs) — accept-side of guard contracts, raise-side covered by sibling `pytest.raises` tests in same file. LEGIT.
- Clusters worth a hardening pass (no-raise only, no output pinned): `tests/unit/test_app_imports.py:49,95,103,112,137` (5 preflight tests), `tests/unit/test_chat_hooks.py:124,166,184,201,218` (5 quota-notify negative paths — "no fire" is asserted only by absence of exception, not by inspecting the notify sink), `tests/unit/test_source_allowlist.py:321–419` (7 passthrough/degradation funcs).

**Verdict**: zero-assert ≈ 0.7% of functions, mostly deliberate. Not a systemic weakness.

### 1.4 `is not None`-only tests: 16 functions / 145 bare lines

- 145 single-line `assert X is not None` statements suite-wide (grep `^\s+assert \w+ is not None\s*$`), almost all as guards before stronger asserts — fine.
- **16 test functions whose ONLY asserts are `is not None` / `assert True`** (AST): e.g. `tests/unit/test_jina_reranker.py:92`, `tests/unit/test_zeroentropy_reranker.py:93` (`test_constructor_accepts_valid_key` — constructor smoke), `tests/unit/test_pii_universal_coverage.py:152,165,180,193` (4 "redacted across all surfaces" tests — **naming promises redaction verification, body only checks not-None**; HYPOTHESIS: these were stronger once and got weakened, unverified), `tests/unit/test_db_backed_api_key_pool.py:89`, `tests/unit/test_bot_registry_service.py:128`, `tests/unit/interfaces/test_feedback_route_wire.py:74`, `tests/unit/test_kg_service_transport_parity.py:26`, `tests/unit/test_a2_async_gather_wins.py:120`, `tests/unit/test_admin_ai_crud_contract.py:32`, `tests/unit/test_otel_tracing.py:14`, `tests/unit/test_refuse_pattern_clause_a.py:81`, `tests/unit/test_refuse_pattern_ho_tro_narrow.py:125`, `tests/unit/test_retrieve_empty_early_exit.py:42`.

The 4 PII ones are the only worrying cluster (security-adjacent name vs weak body).

### 1.5 The bigger weak-assertion class: source-text pin tests (~10% of files)

**FACT**: 73 of 737 test files (9.9%) assert on **source code as text** — `inspect.getsource` (65 files) or `Path(...).read_text()` of `src/` files (8 more). Example read in full: `tests/unit/test_m25_no_inline_embedding_version_model.py` — 5 tests, all regex over `retrieve.py`/`query_graph.py` file text (lines 36–38, 47–57); zero runtime execution of the pinned behavior. Similar: `tests/unit/orchestration/test_sysprompt_v7_template.py` (constant-string containment only, lines 37–104).

These are valid *governance* pins (zero-hardcode, wiring presence) but they (a) pass when behavior breaks as long as the string survives, and (b) break on harmless refactors (rename/move) — which is exactly what produced today's 8 collection errors + ImportError failure clusters (§6). They inflate "passed" as a quality signal for T1 behavior.

---

## 2. The xpassed tests — stale xfail marks

### 2.1 Mechanism (FACT)

`tests/conftest.py:100–183`: every node ID listed in `tests/_xfail_list.txt` (66 entries) is auto-marked `xfail(strict=False)` with reason "V17 legacy-drift … Fix scheduled in `plans/260507-V17-test-refactor`. Delete the corresponding line … once green" (`tests/conftest.py:157–165`). Additionally 14 in-file `pytest.mark.xfail` marks exist in 4 files (`test_domain_neutral_multitenant.py`, `test_semantic_cache_threshold.py:202`, `test_multibot_ingest_canary.py:68`, `test_perf_parallel_ship.py:95,143`).

### 2.2 31 of the 66 listed entries are stale (deterministic XPASS) — FACT

Targeted run of the 61 collectable listed node IDs: **`30 xfailed, 31 xpassed in 19.06s`** (scratchpad `xfail_run2.txt`). The same 31 XPASS also appear in both full-suite runs — deterministically green, marks stale since the plan date in the reason string (`plans/260507-V17-test-refactor` = 2026-05-07, ~8 weeks). The 31:

```
test_chat_pipeline_integration.py::test_pipeline_multi_hop_goes_through_rewrite
test_provider_failover.py::test_circuit_breaker_open_triggers_fallback
test_provider_failover.py::test_both_primary_and_fallback_fail_reraises_second
test_query_graph_resolver_integration.py::test_graph_with_none_resolver_raises_invariant_violation
test_query_graph_route_functions.py::test_reflect_route_persists_when_iteration_cap_hit
test_query_intent_extractor.py::test_llm_exception_returns_empty
test_rerank_intent_whitelist.py::test_row_to_config_malformed_payload_downgrades_to_none
test_rerank_intent_whitelist.py::test_rerank_node_empty_intents_skips_all
test_reranker_resolver.py::test_empty_api_key_falls_back_null
test_reranker_resolver.py::test_redis_write_failure_does_not_crash
test_reranker_resolver.py::test_build_failure_falls_back_null
test_reranker_strategy.py::test_registry_unknown_provider_falls_back_to_null
test_retrieval_fallback.py::TestRetryHybridWithOriginal::test_returns_empty_when_embedding_raises
test_retrieval_fallback.py::TestRetryHybridWithOriginal::test_returns_empty_when_hybrid_search_raises
test_structured_output_helper.py::test_invalid_json_returns_none_when_fallback_disabled
test_structured_output_helper.py::test_fenced_json_parsed_on_fallback
test_structured_output_helper.py::test_provider_call_exception_returns_none
test_structured_output_helper.py::test_anthropic_invalid_args_returns_none
test_t2_perf_fixes.py::test_generate_history_capped_at_max_msgs
test_t2_perf_fixes.py::test_generate_history_respects_condense_when_smaller
test_t2_perf_fixes.py::test_prompt_compression_step_emits_metadata
test_t2_perf_fixes.py::test_prompt_compression_step_skipped_when_disabled
test_tenant_rate_limiter.py::test_redis_error_fails_open
test_tenant_token_meter.py::test_redis_error_during_increment_returns_zero
test_vi_compound_segmentation.py::test_oversize_input_falls_back_to_original
test_vi_compound_segmentation.py::test_underthesea_failure_falls_back_to_original
test_viranker_local_reranker.py::test_registry_falls_back_to_null_when_stub_init_fails
test_viranker_local_reranker.py::test_registry_filter_does_not_pass_extra_kwargs
test_webhook_dispatcher.py::test_dispatch_4xx_drops_no_retry
test_webhook_dispatcher.py::test_dispatch_5xx_retries_then_gives_up
test_webhook_dispatcher.py::test_dispatch_swallows_self_failure
```

Note what these pin: **circuit-breaker fallback, Redis fail-open/fail-zero, reranker null-fallback, webhook retry semantics** — resilience contracts that are currently green but demoted to non-alerting `xfail(strict=False)`. A regression in any of them today would flip XPASS→XFAIL **silently** (strict=False never fails the suite, `tests/conftest.py:164`). The "delete the line once green" workflow (`tests/conftest.py:139-140`) has demonstrably not happened for 8 weeks.

### 2.3 7–9 more flip with execution order (test pollution) — FACT

XPASS in full run = 40; XPASS in targeted run = 31; sibling serial run = 33. Diff (full − targeted), all 9 extra:

- `test_query_graph_gaps_5_6_7.py` ×5 + `test_query_graph_resolver_integration.py` ×2 — xfail when run in the small batch, XPASS in the full sweep → order-dependent.
- `test_domain_neutral_multitenant.py::test_multi_query_expansion_loads_from_language_pack` — in-file mark reason "MultiQueryExpansionService not yet wired to LanguagePackService" **now XPASSES** → wiring landed, mark stale.
- `test_semantic_cache_threshold.py::test_cache_hit_emits_structlog_with_similarity_and_threshold` — mark reason (`test_semantic_cache_threshold.py:202`) documents "passes solo … fails in the full sweep because some other test re-initialises structlog"; it **XPASSED in my full sweep** → even the pollution description is stale/order-dependent. The reason text itself is written evidence that structlog-state pollution exists in the suite.

### 2.4 5 listed node IDs are dead AND poison targeted runs (FACT)

5 entries of `tests/_xfail_list.txt` point into files that module-level-skip as dead-code (`test_text_normalizer_strategy.py::…` ×2, `test_tokenizer_registry.py::…` ×1, `test_tool_client_strategy.py::…` ×2). Pytest 9 reports "found no collectors" for them and **aborts the whole invocation with exit 4** — reproduced: passing all 66 IDs runs 0 tests, "3 skipped in 6.17s", exit=4 (scratchpad `xfail_run.txt`), even with `--continue-on-collection-errors`. Anyone following the conftest's own instructions ("run the list, delete green lines") gets a broken run.

---

## 3. The skipped tests — 32 in-run, three distinct populations

### 3.1 24 module-level "dead-code" skips → 260 test functions parked (FACT)

Full-run `-rs` output lists 24 `SKIPPED [1] …: … dead-code (body commented out)` entries (scratchpad `full_run.txt`). Each is a module-level `pytest.skip(..., allow_module_level=True)` guarding a src subpackage whose body was commented out (verified: `src/ragbot/infrastructure/proximity_cache/lsh_proximity_cache.py:1–20` carries a "DEAD-CODE NOTICE — 2026-06-03 … kept INTACT (reversible)" header). Counted per file, **260 test functions** are parked:

| File | parked funcs |
|---|---|
| test_embedding_semantic_chunk.py | 36 |
| test_chunk_quality_scoring.py | 25 |
| test_diff_reingest.py / test_hyde_generator.py | 19 each |
| test_cag_mode.py / **test_d4_security_pentest.py** / test_proposition_llm.py | 17 each |
| test_multi_vector_embedder.py | 16 |
| query_router trio + self_rag_router | 39 |
| multi_agent_review (5 files) | 23 |
| others (bartpho, convo_summary, proximity, tenant_model_tier, text_normalizer, tokenizer, tools) | 32 |

**Assessment: LEGIT dead-code parking** consistent with the EVOLVE-not-REWRITE stance — with two exceptions:
1. **`tests/unit/test_d4_security_pentest.py:42`** — a 17-test **security pentest suite** for `prompt_injection_guard` is parked. Whatever replaced that guard on the answer path (guardrails engine), the pentest-style adversarial suite has no live successor under this name. HYPOTHESIS (unverified): injection coverage now lives in `tests/unit/infrastructure/guardrails/`; a mapping check is needed before calling this safe.
2. The `_xfail_list.txt` still references 5 node IDs inside these dead files (§2.4) — the two rot registries contradict each other.

### 3.2 7 spec-placeholder skips — rot, feature shipped but placeholders never converted (FACT)

`tests/unit/test_perf_parallel_spec.py:20–105` — 7 `@pytest.mark.skip("spec-only …")` placeholders dated to `reports/MEGA_PERF_PARALLEL_Q4_Q5_Q6_SPEC_20260501.md`. The file promises "Will move to tests/unit/test_rewrite_multiquery_parallel.py on land" — **none of the 3 promised files exist** (`ls` → No such file), while the Option-A feature itself DOES exist: `src/ragbot/orchestration/query_graph.py:2737` ("Parallel-wrapper for rewrite + multi_query expansion"). Two months of "living spec" that no longer lives; either convert (mq speculative parallel already has real tests, e.g. `tests/unit/test_mq_speculative_parallel.py`) or delete.

### 3.3 1 env-conditional skip — LEGIT

`tests/unit/test_structured_output_schemas.py:195` — real-API smoke behind `OPENAI_API_KEY`; conftest deliberately pops that key (`tests/conftest.py:69–71`), so it always skips in-repo. By design.

(Discrepancy note: the task premise said "24 skipped" — that matches a run where only module-level skips report (sibling Run 0 collected-abort state); an executed full run shows 32 = 24 dead-code + 7 spec + 1 env.)

---

## 4. Coverage blind spots (module→test import mapping — PROXY metric)

Method: a src module counts "referenced" if its dotted path appears in any test file import/string. This **overcounts blindness** for packages re-exported via `__init__` (e.g. `shared/constants/_NN_*.py` are consumed as `ragbot.shared.constants` — not truly untested) and **undercounts** behavioral coverage (importing ≠ asserting). Labeled findings below survive that caveat.

**FACT — headline**: 176/649 src modules (27%) never referenced by any test file by dotted path.

### 4.1 Whole subsystems with ZERO test reference

| Subsystem | Files | Live or dead? | Risk |
|---|---|---|---|
| `infrastructure/token_ledger/` | 0/5 | **LIVE** — wired in `src/ragbot/bootstrap.py:302` (`token_ledger = providers.Singleton(…)`, import at bootstrap.py:143); `async_db_token_ledger.py` is the money-path usage accounting sink with queue-drop semantics (docstring lines 1–9) | Billing/quota accounting has no unit pin on drop-counting, batching, or drain-failure behavior |
| `infrastructure/graph/` | 0/3 | `knowledge_graph.py` + `graph_retriever.py` — GraphRAG retrieval path | `graph_retrieve` node exists in orchestration; retrieval correctness untested |
| `application/queries/` | 0/2 | live CQRS read side | |
| `infrastructure/idempotency/` | 0/1 | package init | (service itself tested via `test_ingest_idempotency_service.py`) |

### 4.2 Live subsystems <50% referenced

- `infrastructure/resilience/` **1/8** — `failover_orchestrator.py`, `db_circuit_breaker.py`, `llm_circuit_breaker.py`, `redis_circuit_breaker.py`, `null_circuit_breaker.py`, `registry.py` never imported by tests by name. Circuit-breaker *behavior* is partially pinned indirectly (e.g. `test_provider_failover.py` — which is itself xfail-listed, §2.2). The platform's resilience layer is effectively guarded by tests that are demoted to non-alerting.
- `infrastructure/retrieval_fallback/` **1/7** — the multi-stage fallback strategies (`hybrid_stage1.py`, `bm25_only_stage2.py`, `keyword_stage3.py`, `parent_expand_stage4.py`, `registry.py`, `null_stage.py`) have no direct tests; `test_retrieval_fallback.py` targets the orchestration-level retry helper instead (and its 2 tests are xfail-listed, §2.2).
- `interfaces/workers/chat_worker/` **1/6** — `pipeline.py`, `payload.py`, `callbacks.py`, `config.py`, `pipeline_config.py` unreferenced; only the callback-dispatcher half has tests (which are currently FAILING, §6).
- `application/services/model_resolver/` **1/5** — the mixin split (`_binding_mixin.py`, `_cache_mixin.py`, `_helpers.py`, `service.py`) unreferenced by name; behavior covered via package import in `test_model_resolver_system_config_fallback.py` (read: real fakes, strong asserts) — acceptable but the cache mixin's Redis semantics have no dedicated pin.
- HTTP surface: **10+ admin routes unreferenced** — `admin_ai.py`, `admin_analytics.py`, `admin_gdpr.py`, `admin_metrics.py`, `admin_notify.py`, `admin_policy.py`, `admin_tenant_policy.py`, `admin_tenants.py`, `crm.py`, `health.py`, `jobs.py`, `honeypot.py`, plus middlewares `anti_abuse.py`, `body_size.py`, `logging_mw.py`, `trace_context.py`. For a HEADLESS BE PLATFORM whose only product surface is REST, admin/API contract coverage is the thinnest layer.
- `orchestration/nodes/` — **14 extracted node modules** (`grade.py`, `rerank.py`, `decompose.py`, `reflect.py`, `router.py`, `understand.py`, `guard_input.py`, `check_cache.py`, `rewrite_retry.py`, `routing.py`, `graph_retrieve.py`, `adaptive_decompose.py`, `query_complexity_node.py`) plus `retrieval_filter.py` never imported by tests directly. Tests still import their symbols from `ragbot.orchestration.query_graph` — the old location — which is exactly what broke: see §6.
- Ingest internals: `document_service/ingest_helpers.py`, `ingest_phases.py`, `ingest_stages_enrich.py`, `text_processing.py` unreferenced by name (covered indirectly via mixin-host tests like `tests/unit/application/test_stats_index_idempotent_reingest.py`).

### 4.3 Multi-format ingest coverage (positive finding)

Parser adapters ARE tested behaviorally: `tests/unit/test_parser_docx.py` builds a real in-memory `.docx` via python-docx and asserts structured-markdown output (read in full, lines 35–60+); `test_kreuzberg_parser.py`, `test_add_document_sheet_mime.py` etc. exist. The multi-format first-class mandate has real test backing at the adapter level.

---

## 5. Test-to-src drift risk — 15-file seeded random sample (shuf seed 42)

| # | File | Judgment |
|---|---|---|
| 1 | `test_sysprompt_assembler_pin.py` | invariant pin on assembler contract (ADR-W1-S10 lock) — governance pin, low mock |
| 2 | `orchestration/test_skip_understand_for_greeting.py` | behavioral, 0 mocks, 14 tests |
| 3 | `test_m25_no_inline_embedding_version_model.py` | **pure source-regex pin — zero runtime execution** (lines 36–96) |
| 4 | `test_query_graph_cascade_wire.py` | GOOD hybrid: real helper + recording stub resolver (lines 47–65) + 1 getsource wiring pin — but **1 of its tests FAILS in the current full run** |
| 5 | `test_cag_mode.py` | **never runs** — dead-code module skip (17 tests parked) |
| 6 | `orchestration/test_crag_compound_query.py` | **collection ERROR** — imports drifted symbols (§6) |
| 7 | `orchestration/test_sysprompt_v7_template.py` | constant-string containment only (lines 37–104) |
| 8 | `test_parser_docx.py` | STRONG behavioral — real .docx bytes → parser → asserts markdown structure |
| 9 | `orchestration/test_sysprompt_version_resolve.py` | behavioral on real resolver, SimpleNamespace stand-in — good |
| 10 | `application/test_sysprompt_assembler.py` | behavioral, 3 mock refs |
| 11 | `orchestration/test_sysprompt_partial_threshold.py` | constant/threshold pin |
| 12 | `test_text_normalizer_strategy.py` | **never runs** — dead-code module skip |
| 13 | `test_model_resolver_system_config_fallback.py` | STRONG — real service, full fake port rows, 5 documented invariants (docstring lines 16–25), 0 MagicMock |
| 14 | `test_ragas_metric_adapter.py` | behavioral on adapter + CLI module loaded from real script path — good |
| 15 | `application/test_stats_index_idempotent_reingest.py` | mock-heavy (18 refs) BUT exercises the real `_StageFinalizeMixin._stage_finalize` with behavioral asserts (`delete_by_document.await_count == 2`, lines 100–139) — mocks at boundaries only. GOOD |

**Sample verdict**: mock-overuse is NOT the drift vector — only 1/15 is mock-heavy and it still executes real src code. The suite's actual drift vectors, in order:
1. **Import-path drift** (4/15 sample impact: 1 collection error + 1 failing + 2 never-run): tests import symbols from where they used to live. Materialized suite-wide as 8 collection errors + 21 ImportError failures (§6).
2. **Source-text/constant pin style** (4/15): passes while behavior breaks, breaks while behavior holds.
3. **Dead-code parking** (2/15): silently zero coverage.

Extrapolating the sample honestly: roughly 25–30% of test files would NOT catch a behavioral break in the src they nominally cover (pins, parked, drifted) — HYPOTHESIS from a 15-file sample, not a census.

---

## 6. Current suite is RED — 67 failures + 8 collection errors (FACT)

Deterministic across 3 runs (§0). Exception-type census over failure tracebacks (`full_run2.txt`): **36 AssertionError, 21 ImportError, 4 AttributeError, 2 IndexError, 1 TypeError**. Clusters:

| Cluster | Count | Nature |
|---|---|---|
| `test_multibot_ingest_canary.py` | 25 | **Deliberate executable-spec reds**: docstring lines 20–21 "Tests that currently FAIL document the engine gaps the multi-bot fix must close; they are the executable spec." Property tests (`test_invariant_random_domain_no_silent_row_drop[0..24]`, seeded) fail with "rows silently dropped for an unseen domain" — a real T1 multi-bot/domain-neutral gap in `shared/document_stats.parse_table_chunks`. Only one shape is xfail-marked (line 68); the other 25 were committed UNMARKED → suite red by design. |
| symbol-move drift | ~21+8 errors | `CRAG_GRADE_*` moved to `src/ragbot/orchestration/nodes/grade.py:28`, `parse_decomposed_sub_queries` → `query_graph_helpers.py:25`, `_rerank_threshold_gate` → `retrieval_filter.py:165`; tests still import from `ragbot.orchestration.query_graph` (e.g. `tests/unit/test_crag_three_states.py:19` ImportError, `tests/unit/test_query_decompose.py:5`, `tests/unit/test_reranker_threshold_gate.py`). Collection errors also include a FastAPI-internal import (`tests/unit/_helpers_routes.py:22` — `_EffectiveRouteContext` from `fastapi.routing`, an import of a private third-party symbol that vanished in FastAPI 0.135). |
| chat_worker callback suites | 14 | AssertionError/IndexError in `test_callback_delivery_client_reuse.py`, `test_chat_worker_callback_*` — per sibling report, behavior drift after recent integrations. |
| misc singletons | ~15 | incl. guard/grep meta-tests (`test_no_version_ref_grep.py`, `test_narrow_exception_hierarchy.py`, `test_domain_neutral_guard.py` — the suite's own policy guards are failing). |

Full per-test root-cause: `reports/DEEPDIVE_20260702/tests-full-run.md`.

**Two test-health conclusions independent of who broke what**:
1. Committing intentionally-failing "executable spec" tests **without xfail(strict) marks** makes red the steady state, which trains everyone to ignore red — the exact failure mode that let 8 collection errors sit undetected at the suite's front door (`pytest tests/unit -q` aborts with 0 tests run).
2. The policy meta-guards (version-ref grep, broad-except counter, domain-neutral guard) failing means the pre-commit guard layer CLAUDE.md relies on is currently not green either.

---

## 7. VERDICT — is "6500 passing" trustworthy as a quality signal?

**No — with precision.** Decomposed:

| Claim component | Verdict | Evidence |
|---|---|---|
| The ~6,439 passing tests are real tests that ran | **TRUE** — 90s wall-clock (`-n 4`), deterministic across 3 runs | §0 |
| Assertion hygiene of passing tests | **Mostly strong** — 1 executable `assert True`, 42 zero-assert (mostly legit no-raise), 16 weak-only | §1 |
| "Suite is green" implication | **FALSE today** — 67 failed + 8 collection errors; plain `pytest tests/unit -q` runs **zero** tests | §6, tests-full-run.md §1 |
| "Passing = behavior covered" | **OVERSTATED** — 73 files (10%) pin source text not behavior; 27% of src modules never referenced; token_ledger/resilience/retrieval_fallback/admin-routes thin | §1.5, §4 |
| "xfail/skip bookkeeping is maintained" | **FALSE** — 31/66 xfail-list entries deterministically XPASS (8 weeks stale), 5 list entries point at dead files and abort targeted runs, 7 spec-skips outlived their shipped feature, 7–9 tests flip xfail↔xpass with ordering (pollution) | §2, §3 |
| Count stability | Header count ≈6,500 conflates 6,439 passed + 40 xpassed + 29 xfailed; 260 parked functions inside dead-skip files are invisible in it | §0, §3.1 |

**Bottom line**: the number is an honest *regression floor for the paths it covers*, and suite runtime (≤90s parallel) plus prevailing test style (real fakes at boundaries, behavioral asserts) are genuinely good. But as a *quality signal* it fails on three counts: the suite is not green right now; its escalation channels (xfail strict=False, dead-list, spec-skips) are rotted so regressions in resilience contracts would be silent; and ~10% of files certify text, not behavior. Treat "N passing" as necessary, not sufficient — the gate that matters (per CLAUDE.md) remains load-test Coverage/HALLU, and the unit gate needs the 5 repairs below first.

### Repair list (ranked, smallest-diff-first)

1. **Restore green as the invariant**: mark the 25 executable-spec canary reds `xfail(strict=True)` (they flip loudly when the fix lands) and fix the 8 collection-error imports (mechanical: update to `nodes/grade.py` / `query_graph_helpers.py` / `retrieval_filter.py` paths; replace the private-FastAPI import in `tests/unit/_helpers_routes.py:22`).
2. **Delete 31 stale lines from `tests/_xfail_list.txt`** (list in §2.2) + the 5 dead-file entries; consider flipping the conftest marker to `strict=True` so future fixes self-report.
3. Delete or implement `tests/unit/test_retrieve_empty_early_exit.py` (3 vacuous tests) and the 7 `test_perf_parallel_spec.py` placeholders.
4. Root-cause the structlog-pollution order dependence (documented in `test_semantic_cache_threshold.py:202` reason text) — one autouse structlog snapshot/restore fixture.
5. Coverage debt (T1-relevant first): behavioral tests for `infrastructure/retrieval_fallback` stages and `infrastructure/graph`; then token_ledger drop/drain semantics; then admin-route contract smokes. Verify `test_d4_security_pentest.py`'s 17 parked injection tests have a live successor in `tests/unit/infrastructure/guardrails/` before accepting the dead-code skip.
