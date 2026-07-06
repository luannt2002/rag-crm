# FULL UNIT TEST SUITE — HEALTH REPORT (tests-full-run)

- **Date**: 2026-07-02/03 (runs started 2026-07-02 ~23:50 +0700)
- **HEAD during runs**: `6796cd9` "revert(ING-F1): restore pure-money fallback — owner decision" (2026-07-02 16:55:31 +0700)
- **Branch**: `fix-260623-ingest-expert`
- **Working tree during run 2**: only `z_luannt_deubg.txt` modified (`git diff --stat`); `src/`, `tests/`, `alembic/` clean vs HEAD. (Caveat: other agents run concurrently; run-1 tree state not independently snapshotted.)
- **Command**: `cd /var/www/html/ragbot && set -a && source .env && set +a && python -m pytest tests/unit/ -q -p no:cacheprovider [--continue-on-collection-errors]`
- **Env**: Python 3.12, venv `.venv`, FastAPI 0.135.3 installed (`.venv/lib64/python3.12/site-packages/fastapi-0.135.3.dist-info`).

---

## 1. Headline numbers

| Run | Result | Wall time |
|---|---|---|
| Run 0 (as instructed, no extra flags) | **Interrupted: 8 errors during collection** — 0 tests executed, 24 skipped | 25.04s |
| Run 1 (`--continue-on-collection-errors -rf`) | **67 failed, 6439 passed, 32 skipped, 36 xfailed, 33 xpassed, 8 errors** | 205.39s (3:25) |
| Run 2 (`--continue-on-collection-errors -rfX`, stability re-check) | see §7 (launched for flake confirmation; targeted re-runs of all 67 failures already reproduced 67/67 deterministically) | — |

**FACT**: the plain command from the task (`pytest tests/unit/ -q`) does NOT run the suite at all today — 8 collection errors abort collection. CI-style "run the suite" is broken at the front door.

**FACT (stability)**: every one of the 67 failures was re-run in 4 targeted batches (25 + 16 + 14 + 12 = 67) and **all 67 reproduced identically**. The canary file was additionally run twice back-to-back with identical results (`25 failed, 34 passed, 1 xfailed` both times). **Zero flaky failures found** — category (b) is empty; the "random domains" in the canary are seeded (`random.Random(seed)`, seeds 0–24, test file line 190) and fully deterministic.

---

## 2. The 8 collection errors (verbatim causes)

| # | File | Error |
|---|---|---|
| 1 | `tests/unit/interfaces/test_feedback_loop_wire.py:29` | via `tests/unit/_helpers_routes.py:22` → `ImportError: cannot import name '_EffectiveRouteContext' from 'fastapi.routing'` |
| 2 | `tests/unit/test_admin_documents_debug_route.py:27` | same `_helpers_routes.py:22` ImportError |
| 3 | `tests/unit/test_route_workspace_scope_pin.py:39` | same `_helpers_routes.py:22` ImportError |
| 4 | `tests/unit/orchestration/test_crag_compound_query.py:28` | `ImportError: cannot import name 'CRAG_GRADE_AMBIGUOUS' from 'ragbot.orchestration.query_graph'` |
| 5 | `tests/unit/test_cliff_detect_filter.py:5` | `ImportError: cannot import name '_cliff_detect_filter' from 'ragbot.orchestration.query_graph'` |
| 6 | `tests/unit/test_output_guardrail_tuning.py:45` | `ImportError: cannot import name '_rerank_threshold_gate' from 'ragbot.orchestration.query_graph'` |
| 7 | `tests/unit/test_reranker_threshold_gate.py:21` | same `_rerank_threshold_gate` ImportError |
| 8 | `tests/unit/test_query_decompose.py:5` | `ImportError: cannot import name 'parse_decomposed_sub_queries' from 'ragbot.orchestration.query_graph'` |

---

## 3. Failure categorization — all 67 failures + 8 collection errors

### Group A — commit `24f2451` deleted the promised back-compat re-exports from `query_graph` (15 tests + 5 collection errors) — category (a) stale-import, with a src-side documentation lie

**FACT**: commit `24f2451` ("fix(phase0): integrate S0-A RLS-hardening + S0-C qwen3-capability + S0-D multi-turn", 2026-06-26) removed this block from `src/ragbot/orchestration/query_graph.py` (visible in `git show 24f2451`):

```python
-from ragbot.orchestration.retrieval_filter import (  # noqa: E402
-    CRAG_GRADE_AMBIGUOUS, CRAG_GRADE_IRRELEVANT, CRAG_GRADE_RELEVANT,
-    _autocut, _cliff_detect_filter, _CRAG_VALID_GRADES,
-    _is_retrieval_adequate, _remap_grade_for_intent, _rerank_threshold_gate,
-)
```

and removed `parse_decomposed_sub_queries` from the `query_graph_helpers` re-import (same diff, hunk `@@ -111,21 +100,18 @@`).

**But the promise is still in the source**:
- `src/ragbot/orchestration/query_graph.py:273-276` — comment: *"Re-exported here so existing call sites + test imports (`from ragbot.orchestration.query_graph import _cliff_detect_filter`) are unchanged."* — followed by NO import.
- `src/ragbot/orchestration/retrieval_filter.py:9-12` — docstring: *"``query_graph`` re-imports every name below, so existing call sites and the ... test imports keep working unchanged."* — false.

**Affected** (5 collection errors + 10 failures):
- Collection: `test_crag_compound_query.py`, `test_cliff_detect_filter.py`, `test_output_guardrail_tuning.py`, `test_reranker_threshold_gate.py`, `test_query_decompose.py`
- Failures: `test_crag_three_states.py` (7 tests, ImportError inside test bodies at lines 19/32/52/69/86/100/114), `test_p28_beta_query_graph.py` (3 tests, `AttributeError: module ... has no attribute 'CRAG_GRADE_RELEVANT'` / `'_is_retrieval_adequate'` at lines 58/76/103)

**Runtime impact**: none — the real symbols live and are wired: `retrieval_filter.py:26/94/165` (`CRAG_GRADE_AMBIGUOUS`, `_cliff_detect_filter`, `_rerank_threshold_gate`), consumed by `orchestration/nodes/rerank.py:24-25,273,362` and `orchestration/nodes/grade.py:28`; `parse_decomposed_sub_queries` at `query_graph_helpers.py:25`, consumed by `nodes/decompose.py:16,89`. These 20 dead tests were the *pin tests* for cliff/threshold-gate/CRAG invariants — **the pins are currently OFF** (they can no longer fail on a real behavior regression because they die at import).

**Fix**: either restore the one-line re-import block in `query_graph.py` (matches the still-present comments) or point the 7 test files at `retrieval_filter` / `query_graph_helpers` and delete the stale comments.

### Group B — FastAPI version skew: test helper requires ≥0.137 internals, venv has 0.135.3 (6 tests + 3 collection errors) — category (c) env-dependent

**FACT**: `tests/unit/_helpers_routes.py:22-27` imports `_EffectiveRouteContext` and `_IncludedRouter` from `fastapi.routing`; its own docstring (lines 3-11) says these exist in *"FastAPI (>=0.137)"*. Installed FastAPI is **0.135.3** and `dir(fastapi.routing)` contains neither symbol (verified live). `pyproject.toml:12` pins only `fastapi>=0.110.0`, which does not guarantee the helper's requirement.

**Affected**: collection errors on `test_feedback_loop_wire.py`, `test_admin_documents_debug_route.py`, `test_route_workspace_scope_pin.py`; runtime failures (import inside test body) in `test_chat_routing_compat.py` (2), `interfaces/test_feedback_route_wire.py` (2), `test_effective_prompt_endpoint.py` (1), `test_streaming_upload.py::test_route_disabled_on_composed_app` (1).

**Note**: these are the route-registration pins (4-key workspace scope pin, feedback wire, admin debug route, streaming-upload-disabled pin) — **security/wiring pins currently dead**. The helper was committed 2026-06-19 (`9d2fee9`) targeting internals this venv never had (fastapi dist-info dated Apr 16), i.e. these tests have plausibly never passed in this venv — CHƯA verify against author's env.

**Fix**: pin `fastapi>=0.137` + upgrade venv, or make `_helpers_routes.py` fall back to classic `APIRoute` iteration when the lazy-router internals are absent (it already accepts plain `APIRoute`, see line ~40) — a `try/except ImportError` around the private-symbol import.

### Group C — deliver-time SSRF guard broke all 14 callback-dispatcher unit tests (commit `eafddaa`) — category (a) stale tests broken by src change (not env: the hostnames don't resolve anywhere)

**FACT**: commit `eafddaa` ("fix(phase2): FMT-3 vlm-caption→config + loadtest-bypass coverage + SSRF webhook guard") added a deliver-time SSRF guard to `src/ragbot/infrastructure/delivery/callback_delivery.py:93-101`: when `ssrf_guard_enabled` (default `True` per `shared/constants/_08_sentry_otel.py:82` `DEFAULT_CALLBACK_SSRF_GUARD_ENABLED`), `deliver()` DNS-resolves the callback host and returns `False` before any HTTP attempt if unresolvable.

All 4 callback test files (unchanged since first commit `cd08119`) inject fake clients but use fabricated hostnames (`ep.example.com`, `partner.example.com`). Every test now logs `callback_ssrf_blocked_at_deliver reason='Cannot resolve hostname: ...'` and fails with `call_count == 0` / `ok is False`:

- `test_callback_delivery_client_reuse.py` (4), `test_chat_worker_callback_dispatcher.py` (3), `test_chat_worker_callback_negative_paths.py` (3), `test_chat_worker_callback_retry.py` (4). None of them passes `ssrf_guard_enabled=False` (grep: 0 hits).

**Consequence**: retry/backoff/HMAC/client-reuse logic has **zero effective unit coverage** right now. Also a test-design smell: a *unit* test suite now performs live DNS resolution (slow, network-coupled).

**Fix**: construct dispatcher with `ssrf_guard_enabled=False` in these tests (guard behavior itself is separately covered by the SSRF tests added in `eafddaa`), or monkeypatch `_is_url_safe`.

### Group D — strangler-fig node split: "wire-pin" tests still point at `query_graph` module attributes (5 tests) — category (a) stale tests, features verified alive elsewhere

| Test | Asserts | Where the feature actually lives now (verified) |
|---|---|---|
| `orchestration/nodes/test_speculative_retrieve.py:261` `test_query_graph_imports_decide_keep_speculative` | `hasattr(query_graph, "_decide_keep_speculative")` | `nodes/retrieve.py:62` (import) + `:642` (call) |
| `orchestration/nodes/test_speculative_retrieve.py:276` `..._speculative_constants` | `query_graph.DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD` | `nodes/retrieve.py:119,636`; constant at `constants/_20_cag_mode_cache_augmented_gen.py:157` |
| `orchestration/test_grounding_check_factoid_enabled.py:85` | `hasattr(qg, "DEFAULT_GROUNDING_CHECK_ENABLED")` | `nodes/guard_output.py:34,70` |
| `test_query_graph_cascade_wire.py:79` | `apply_cascade_routing` imported at `query_graph` module scope | `nodes/generate.py:36` (module-scope import) + `:399` (call); helper at `nodes/cascade_router_helper.py:96` |
| `test_t2_perf_fixes.py::test_crag_grade_bounded_concurrency` | fake `_call_with_schema` signature | src added kwarg `supports_json_mode` (call at `query_graph.py:~1269-1281`); the test's `_fake_call_with_schema` doesn't accept it → `TypeError` — stale test double |

### Group E — REAL src findings surfaced by failing tests — category (d)

#### E1. `stats_route_skip_grounding`: HALLU-safety default silently reverted + per-bot knob now dead + lying comment — **REAL bug (highest severity in this run)**

Failing test: `test_guard_output_intent_gating.py:267` `test_guard_output_wires_stats_route_skip_grounding_flag` (asserts `'stats_route_skip_grounding' in` guard-output source).

Evidence chain (all FACT):
1. Commit `062d6fa` (2026-06-25, "fix(grounding): apply grounding judge to stats route by default (HALLU-safe)") — commit message documents an actual **HALLU breach** ("a stock number leaked from history … passed unchecked") and shipped: grounding runs on stats answers by default; skip gated behind per-bot flag `stats_route_skip_grounding` (default `False`), with `_pcfg(state, "stats_route_skip_grounding", DEFAULT_STATS_ROUTE_SKIP_GROUNDING)` in guard_output.
2. Commit `3097755` ("fix(phase1): integrate S1-A late-binding table + S1-B anti-fabricate + S1-C lifecycle purge") **removed the `_pcfg` read** and made the skip unconditional. Current code `src/ragbot/orchestration/nodes/guard_output.py:105-106`:
   ```python
   if str(state.get("retrieve_mode") or "").startswith("stats"):
       _grounding_eligible = False
   ```
3. The comment directly above (guard_output.py:98-104) still ends *"Per-bot overridable."* — **false**, nothing reads the flag.
4. The per-bot knob still exists and is resolvable: `src/ragbot/shared/bot_limits.py:63-70` (`"stats_route_skip_grounding": {"type": "bool", "default": DEFAULT_STATS_ROUTE_SKIP_GROUNDING}`) with a comment claiming *"Default False = grounding applies to stats answers too (HALLU-safe…)"* — **dead knob**: owners can set it; it changes nothing.
5. Constant `DEFAULT_STATS_ROUTE_SKIP_GROUNDING` (`constants/_15_m2_neighbor_window_expansion.py`) is now consumed only by `bot_limits.py` schema, not by any decision point.

Label: the code state is FACT. Whether `3097755` re-opens the exact `062d6fa` HALLU breach is **HYPOTHESIS** (needs the 062d6fa repro turn re-run) — but the breach scenario the 062d6fa message describes (answer citing a history-leaked number on the stats route) is again unchecked by construction. Given "HALLU=0 sacred", this deserves an explicit owner decision + either restore the `_pcfg` gate or delete the knob+comments and re-pin the new policy.

#### E2. `cross_doc_reconcile_enabled`: new per-bot knob unreachable through pipeline config + inline hardcoded default — **REAL config-wiring gap**

Failing test: `test_pipeline_cfg_keys_parity.py:136` — `Missing keys: ['cross_doc_reconcile_enabled']`.

**FACT**: `src/ragbot/orchestration/query_graph.py:2380` reads `if bool(_pcfg(state, "cross_doc_reconcile_enabled", True))` (added by `aa029ec`, Phase-4 MULTIDOC-B-FRAG), but `_build_pipeline_config` (`interfaces/http/routes/test_chat/_pipeline_config.py`) never populates the key → the toggle can never be changed via system_config/per-bot config; it silently always-True. Bonus violation: the default is an inline literal `True`, not a `DEFAULT_*` constant from `shared/constants` (zero-hardcode rule).

#### E3. `DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT`: per-intent output-cap feature dead (orphan constant) — **REAL T2 regression**

Failing test: `test_generate_intent_max_tokens.py:91` (asserts generate-node source references the constant).

**FACT**: the constant (a 10-entry dict, `greeting: 60 … multi_hop: 400`, `constants/_06_llm_defaults.py:22-33`, still exported via `_09_message_feedback_thumbs_verd.py:371`) has **zero consumers** in `src/` — usage was deleted by `24f2451` (`git log -S`). The generate node now computes `_intent_max_tokens` at `nodes/generate.py:741-744` purely as `compute_output_cap(system_output_default, bot_extra_output)` (`shared/token_budget.py:63-78`) — **no intent dimension despite the variable name**. Effect: greeting/chitchat/vu_vo answers are no longer capped at 60–80 tokens; every intent gets the full per-response cap → token cost regression on cheap intents, and a misleading variable name. (Whether the removal was deliberate is unstated in `24f2451`'s message — CHƯA verify with author.)

#### E4. Hygiene-guard regressions (guards doing their job) — 3 tests

- **`test_domain_neutral_guard.py:97` `test_no_new_price_domain_coupling`** — `133 > baseline 127`. Trend by commit (git grep of the guard's regex over `src/ragbot`): `555d086`=130 → `aa029ec`=133 → `4e83410`=135 → HEAD `6796cd9`=133. Top offenders now: `infrastructure/repositories/stats_index_repository.py` (53), `shared/document_stats.py` (38), `shared/query_range_parser.py` (11), `orchestration/query_graph.py` (10). The engine keeps deepening first-class `price_*` coupling (e.g. `aa029ec` cross-doc reconcile merges "price-LESS fragments INTO the priced anchor", `query_graph.py:297+`) instead of the generic labelled-attribute path the guard mandates — directly related to the canary root cause (§4).
- **`test_narrow_exception_hierarchy.py:211`** — total broad-except `250 > ceiling 249`. The +1 site: `555d086` added `except Exception:  # noqa: BLE001 — tokenizer is optional; no-estimate fallback` in `src/ragbot/shared/llm_usage.py` (~line 30, tiktoken lazy import). It is noqa-annotated (hard rule no-noqa=0 still passes) but the soft ceiling wasn't bumped with justification as the test's own policy requires (test file comment lines 114-169).
- **`test_no_version_ref_grep.py:108`** — `9 > ceiling 7`. All 9 hits are in `tests/` and enumerated in the assertion, including 3× the literal `ragbot_v2_dev:document.uploaded.v1` in `tests/unit/infrastructure/events/test_redis_streams_nogroup_recovery.py:43,49,79` — which is *also* an environment/DB-name literal (`ragbot_v2_dev`) in a tracked file, brushing the tenant-literal rule.

---

## 4. Root cause — `test_multibot_ingest_canary` fails on all 25 "random domains"

### What the test does
`tests/unit/test_multibot_ingest_canary.py:188-213` `test_invariant_random_domain_no_silent_row_drop[0..24]`: for 25 **fixed seeds** it generates a well-formed CSV with 3–6 never-seen header names (`Field876A, Field530B, …`) and 2–5 rows of short text values (`v0r0c0, …`), then asserts INV-1 (table must not vanish) and INV-2 (no labelled value lost). **Deterministic** — `random.Random(seed)`, identical failures across two runs; NOT flaky. The file's own docstring (lines 19-20) declares: *"Tests that currently FAIL document the engine gaps the multi-bot fix must close; they are the executable spec."* — i.e. these 25 are committed-failing spec tests (unlike Shape-S1 at line 68 which is properly `xfail`-marked, these are not marked, so they redden every full run).

### Traced failure chain (live-debugged, seed 0)

Input:
```
Field876A,Field530B,Field141C,Field365D,Field623E,Field597F
v0r0c0,v0r0c1,...  (5 rows)
```

1. **Header not detected.** `_is_header_row` (`src/ragbot/shared/document_stats.py:348-387`) returns True only via (i) the structural rule — row directly above a `| --- |` / `---,---` separator (line 384-385), or (ii) the vocab hint — a cell normalising into `_HEADER_EXACT_TOKENS` or owner-declared `custom_roles` labels (lines 381-382, 387). A raw CSV has **no separator line**, headers `Field876A…` match **no vocabulary**, and the test passes **no custom_roles** → returns `False` (verified live: printed `False`).
2. **No roles bound.** `parse_table_chunks` (`document_stats.py:1019-1026`) therefore never sets `header`/`roles`; every row (including the header line itself) is parsed as data with `header=[]`, `roles={}`.
3. **All cells fall to `col_N`.** `_extract_entity_from_row(cols, [], …)` produces `ParsedEntity(name='v0r0c0', attributes={'col_1': …, …'col_5': …})` (verified live).
4. **Noise filter nukes every row.** `_is_noise_entity` (`document_stats.py:245-266`) returns True for any entity with **no price** and **only `col_N` attribute keys**; `parse_table_chunks` drops it at line 1067. Text-only values → `parse_money_vn` finds no price → every row classified noise → **`parse_table_chunks` returns `[]`** (verified live), violating INV-1.

### Root cause statement (evidence-backed)

The engine keeps a table row only if it can bind **at least one of**: (a) a recognized header (markdown separator adjacency, VN/EN header vocabulary, or owner-declared `custom_roles`), or (b) a money-parseable cell. A well-formed unknown-domain **raw CSV with none of these** (arbitrary header words, text-only values, no separator, no declared roles) is **silently reduced to zero entities** — the header falls through as data (step 1-2), labels degrade to `col_N` (step 3), and the anti-prose noise filter then deletes everything (step 4). `_is_noise_entity` is the proximate dropper; the architectural gap is that **header detection has no shape-only fallback for headerless raw CSV** (e.g. "first row all-label-shaped + consistent column count below" — exactly what the `table-header-detect-structural` skill in this repo prescribes), so "unknown domain" degenerates into "prose noise".

Contrast that proves the mechanism: the sibling property test `test_invariant_small_numeric_attribute_not_floored[0..24]` (same file, lines 219-241) **passes 25/25** — its tables use the in-vocab headers `Tên`/`Giá` and contain a real price, so path (a)+(b) both fire.

### Blast radius (what this means in production, not just in tests)

`parse_table_chunks` feeds the **Stats Index** at ingest (`application/services/document_service/ingest_stages_final.py:446,488`) — the deterministic structured index behind the stats/aggregation route (exact price/quantity/count answers; the same route that `guard_output.py:98-107` trusts as "authoritative structured index"). For any bot whose tables have out-of-vocabulary headers and no owner-declared `column_roles` and no money column, the stats index gets **0 entities, silently** (the `ingest_data_quality` advisory at `ingest_stages_final.py:490-510` logs a warning only when a *header row was detected*; here `tables_seen` sees no table at all for raw CSV without separators — so even the advisory can stay quiet). Plain text-chunk retrieval still works (chunks are persisted before this stage), but structured/count/aggregation capability silently disappears for that bot.

**Direct answer to the owner's "code only supports happy case" concern**: for the stats/entity-extraction path, **CONFIRMED with evidence** — the happy path is {VN/EN header vocab ∪ owner-declared roles ∪ markdown-separator tables ∪ price-bearing rows}; outside it the engine doesn't degrade gracefully (keep rows with generic labels), it drops to zero. The canary's INV-1/INV-2 are the executable spec of the missing behavior, and the price-coupling guard failure (§3-E4) shows the same bias growing (engine privileges `price_*` as first-class while generic labelled attributes get dropped).

### Expert fix direction (per the repo's own skills/ADRs, short → mid)
1. Short: in `parse_table_chunks`, when no header was detected for a delimited block, treat the **first all-label-shaped row** (no money/value cells, value-contrast with next row, consistent column count — shape-only, `table-header-detect-structural`) as header → real labels instead of `col_N`; then `_is_noise_entity` no longer fires (it only kills `col_N`-only entities).
2. Mid: make `_is_noise_entity` scope-aware — drop only rows that came from *non-tabular* chunks (prose comma-splits), never rows from a block where ≥2 rows share a consistent column count.
3. Both changes are engine-generic (no vocab), consistent with ADR-0006 Tier-2 authority of `custom_roles` and the domain-neutral guard.

---

## 5. Category summary table (all 75 = 67 failures + 8 collection errors)

| Category | Count | Items |
|---|---|---|
| (a) stale-import / stale test vs moved-or-changed src | **39** | Group A: 15 tests + 5 collection errors (query_graph re-exports, `24f2451`); Group C: 14 callback tests (SSRF guard `eafddaa`); Group D: 5 wire-pin tests (node split; incl. 1 stale test-double kwarg) |
| (b) flaky/random | **0** | none — canary is seeded & deterministic (verified: 2 identical full-file runs; 67/67 targeted re-run reproduction) |
| (c) env-dependent | **9** | Group B: 6 tests + 3 collection errors — FastAPI 0.135.3 vs helper's ≥0.137 internals (`_helpers_routes.py:22`, `pyproject.toml:12` under-pins) |
| (d) REAL src findings | **6** | E1 `stats_route_skip_grounding` dead knob + unconditional grounding skip on stats (`guard_output.py:105-106`, `bot_limits.py:63-70`); E2 `cross_doc_reconcile_enabled` unwired + inline default (`query_graph.py:2380`); E3 `DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT` orphan (`_06_llm_defaults.py:22`, 0 consumers); E4 3 hygiene-guard regressions (price-coupling 133>127; broad-except 250>249 from `llm_usage.py` tiktoken fallback; version-ref 9>7) |
| (spec-gap tests, deliberately failing) | **25** | canary INV-1/INV-2 seeds 0–24 — engine gap real (§4), tests are committed-failing executable spec (docstring lines 19-20), not regressions of previously-working behavior |

(The 25 canary tests are "(d)-adjacent": the test failure is by design/spec, but the underlying engine gap they document is real and production-relevant — see §4 blast radius.)

---

## 6. Test-suite health observations beyond the failures

1. **33 xpassed** — 33 tests marked `xfail` now pass (non-strict marks): stale expectations that should be promoted to real tests; in non-strict mode they can silently regress again.
2. **32 skipped** — dominated by "dead-code (body commented out)" skips (proposition_llm, proximity_cache, query_router, self_rag_router, tenant_model_tier, text_normalizer, tokenizer registry, tools…): a documented layer of disabled subsystems with their tests parked.
3. **Pin-test rot is systemic**: three recent integrate commits (`24f2451`, `3097755`, `eafddaa`) each shipped src changes that broke their own guard/pin tests without updating them — 39 of the 67 failures trace to exactly these three commits. The suite was demonstrably not run (or not gated) on those merges: the front-door command has been collection-broken since `24f2451` (2026-06-26).
4. **Unit tests doing live DNS** (Group C) — isolation violation worth fixing independently of the failures.
5. Deprecation noise: `FastAPIDeprecationWarning: ORJSONResponse is deprecated` from `interfaces/http/errors.py:58,67` (2 warnings) — future-breakage signal for the installed-FastAPI upgrade that Group B requires anyway.

## 7. Run-2 stability confirmation

Second full-suite run (same command + `-rX`), completed 2026-07-03:

- **Result: `67 failed, 6439 passed, 32 skipped, 36 xfailed, 33 xpassed, 42 warnings, 8 errors in 180.98s`** — byte-for-byte the same headline as run 1.
- `diff` of the sorted `FAILED` lists of run 1 vs run 2: **IDENTICAL FAILURE SETS** (0 lines differ).
- **FACT: the suite is 100% deterministic across two full runs under concurrent-agent load; zero flakes.**
- xpassed (33) concentrates in: `test_t2_perf_fixes.py` (4), `test_structured_output_helper.py` (4), `test_webhook_dispatcher.py` (3), `test_reranker_resolver.py` (3), `test_viranker_local_reranker.py` (2), `test_vi_compound_segmentation.py` (2), `test_retrieval_fallback.py` (2), `test_rerank_intent_whitelist.py` (2), `test_provider_failover.py` (2), + 9 singletons — all non-strict `xfail` marks that now pass and should be un-marked.

---

## Appendix — raw tallies

- Run 1 short-summary tail preserved at `/tmp/claude-0/-var-www-html-ragbot/e9b02298-b28c-48b7-9bfe-11eef21508ba/tasks/b5coe0y27.output` (last 400 lines).
- Failures per file (run 1): canary 25; crag_three_states 7; callback_retry 4; callback_client_reuse 4; p28_beta 3; callback_negative 3; callback_dispatcher 3; chat_routing_compat 2; speculative_retrieve 2; feedback_route_wire 2; t2_perf 1; streaming_upload 1; cascade_wire 1; cfg_keys_parity 1; no_version_ref 1; narrow_exception 1; guard_output_parallel 1; guard_output_intent_gating 1; generate_intent_max_tokens 1; effective_prompt 1; domain_neutral_guard 1; grounding_check_factoid 1. Total 67.
- `test_guard_output_parallel.py:147` note: the constant `DEFAULT_GUARD_OUTPUT_PARALLEL_ENABLED` moved consumer — now read in `interfaces/http/routes/test_chat/_pipeline_config.py:18,838` and the node reads the pcfg key at `nodes/guard_output.py:307` — feature alive, test asserts the old import location → Group D stale (counted in the 5).
