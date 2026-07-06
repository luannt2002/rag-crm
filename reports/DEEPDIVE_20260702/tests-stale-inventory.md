# Stale-test inventory — root cause of the 8 pytest collection errors (+9 more found)

- **Slug**: tests-stale-inventory
- **Date**: 2026-07-03 (run), mandate dated 2026-07-02
- **Scope**: READ-ONLY diagnosis of `tests/` vs `src/` symbol drift. No code was modified.
- **Method**: `pytest --collect-only` (per-file + full-suite + bisect pairs), `git log -S`, `git show <commit>:<path>`, AST sweep of all 3,108 `from ragbot|tests|scripts import …` statements in `tests/` (module-level AND function-level), live-run classification of every suspect.
- Every claim below is labeled **FACT** (evidence attached) or **HYPOTHESIS** (explicitly unverified).

---

## Executive summary

The 8 mandated collection errors have exactly **2 root causes**, and a full-suite sweep surfaced a **3rd root cause** producing 9 more errors (17 total under `pytest tests/`):

| RC | Cause | Files broken | Class |
|---|---|---|---|
| **RC1** | Commit `24f2451` (2026-06-26) deleted the intentional back-compat re-export block from `query_graph.py` | 5 collection errors + 1 hidden runtime-failing suite (`test_crag_three_states.py`) | **(b) symbol moved → update imports** (or restore shim). NOT a runtime regression — all functions alive and used by `src`. |
| **RC2** | `tests/unit/_helpers_routes.py` imports FastAPI **private** symbols (`_EffectiveRouteContext`, `_IncludedRouter`) that do not exist in the installed fastapi 0.135.3 | 3 collection errors + 4 hidden runtime-failing suites | **Test-infra defect**: helper written 2026-06-19 for "fastapi >= 0.137"; this venv has had 0.135.3 continuously since 2026-04-16 — the helper has **never** imported successfully here. Fix helper (feature-detect fallback verified trivial), don't delete tests. |
| **RC3** | (a) `tests/_archive_pre_squash_20260618/alembic/` is a collected package literally named `alembic` that **shadows the real alembic library**; (b) load-test **scripts** named `test_*.py` in `tests/integration/` run a naive `.env` parser at import time that poisons `PROVIDER_API_KEYS_JSON` process-wide | 9 order-dependent collection errors (only in full-suite runs) | **Test-infra defect** — collection cross-contamination. |

Net blocked inventory: **~114 test functions across 13 files** (71 behind RC1+RC2 collection errors, 9 in `test_crag_three_states.py` runtime, 34 in the 4 lazy-import suites runtime) + 9 order-dependent error files in full-suite runs.

**Zero (a)-verdicts**: no test in the mandate list is obsolete. **Zero (c)-verdicts on src behavior**: no product feature was removed — every "missing" `ragbot` symbol exists and is exercised by production code paths.

---

## Reproduction baseline (FACT)

```
$ cd /var/www/html/ragbot && set -a && source .env && set +a
$ python -m pytest tests/unit --collect-only -q
  → 6583 tests collected, 8 errors        # exactly the 8 mandated files
$ python -m pytest tests/ --collect-only -q
  → 7156 tests collected, 17 errors       # +9 order-dependent (RC3)
```

The user's "8 errors" corresponds to `tests/unit` scope; full `tests/` scope is 17.

---

## RC1 — query_graph back-compat re-exports deleted by `24f2451` (5 + 1 files)

### The 5 collection errors

| Test file | Failing import (evidence) | Symbol now lives at |
|---|---|---|
| `tests/unit/orchestration/test_crag_compound_query.py:28` | `from ragbot.orchestration.query_graph import CRAG_GRADE_AMBIGUOUS, CRAG_GRADE_IRRELEVANT, CRAG_GRADE_RELEVANT, _remap_grade_for_intent` | `src/ragbot/orchestration/retrieval_filter.py:24-26` (grades), and `_remap_grade_for_intent` in same module (`__all__` at `retrieval_filter.py:213-222`) |
| `tests/unit/test_cliff_detect_filter.py:5` | `from ragbot.orchestration.query_graph import _cliff_detect_filter` | `src/ragbot/orchestration/retrieval_filter.py:94` |
| `tests/unit/test_output_guardrail_tuning.py:45` | `from ragbot.orchestration.query_graph import _rerank_threshold_gate` | `src/ragbot/orchestration/retrieval_filter.py:165` |
| `tests/unit/test_reranker_threshold_gate.py:21` | same as above | same |
| `tests/unit/test_query_decompose.py:5` | `from ragbot.orchestration.query_graph import parse_decomposed_sub_queries` | `src/ragbot/orchestration/query_graph_helpers.py:25` |

### The hidden 6th victim (sweep finding — not in the mandate list)

`tests/unit/test_crag_three_states.py` imports the same grade constants **inside test bodies** (lines 19, 32, 52, 69, 86, 100, 114) → it *collects* fine but **7 of its tests FAIL at runtime** (verified live run: `7 failed, 5 passed`). Same fix as the other 5.

### git archaeology (FACT)

```
$ git log --oneline -S "from ragbot.orchestration.retrieval_filter import" -- src/ragbot/orchestration/query_graph.py
24f2451  2026-06-26  fix(phase0): integrate S0-A RLS-hardening + S0-C qwen3-capability + S0-D multi-turn
cd08119  2026-06-17  first commit: ragbot RAG platform          # (history squashed at cd08119)
```

- Parent state `24f2451~1:src/ragbot/orchestration/query_graph.py:408-418` contained the full shim:
  `from ragbot.orchestration.retrieval_filter import (CRAG_GRADE_AMBIGUOUS, CRAG_GRADE_IRRELEVANT, CRAG_GRADE_RELEVANT, _autocut, _cliff_detect_filter, _CRAG_VALID_GRADES, _is_retrieval_adequate, _remap_grade_for_intent, _rerank_threshold_gate)` — and `24f2451~1:...:106-115` re-imported `parse_decomposed_sub_queries` from `query_graph_helpers`.
- `git show 24f2451 -- src/ragbot/orchestration/query_graph.py` removes **both** (diff `-` lines 173 and 450-459).
- All 5 test files predate the removal (all exist since `cd08119`, 2026-06-17) → **the tests were green until 2026-06-26 and broke at `24f2451`.**

### The re-export was a documented contract, not an accident of style (FACT)

Three separate in-repo statements promise the shim:

1. `src/ragbot/orchestration/retrieval_filter.py:9-12` — *"`query_graph` re-imports every name below, so existing call sites and the `from ragbot.orchestration.query_graph import _cliff_detect_filter` test imports keep working unchanged."*
2. `src/ragbot/orchestration/query_graph_helpers.py:8-11` — same promise for helpers.
3. Commit `094f7e8` (2026-06-19, "extract decompose node Phase D.6") message: *"KEEP parse_decomposed_sub_queries (intentional re-export for test_query_decompose)."*

And the tombstone: **`src/ragbot/orchestration/query_graph.py:273-276` still carries the comment** *"Re-exported here so existing call sites + test imports (`from ragbot.orchestration.query_graph import _cliff_detect_filter`) are unchanged."* — with **no import statement under it**. `24f2451` deleted the code but left the comment.

### Is it a real regression? (FACT: no — test-only breakage)

The moved functions are alive and wired into the production graph:
- `src/ragbot/orchestration/nodes/rerank.py:24-25` imports `_cliff_detect_filter`, `_rerank_threshold_gate` from `retrieval_filter` (used at `rerank.py:273` and `rerank.py:362`).
- `src/ragbot/orchestration/nodes/grade.py:28` imports `CRAG_GRADE_AMBIGUOUS` (used throughout grading).
- `src/ragbot/orchestration/nodes/decompose.py:16` imports `parse_decomposed_sub_queries` (used at `decompose.py:89`).

**HYPOTHESIS** (labeled, unverified): `24f2451` was a large multi-agent integration commit (371 lines churned in `query_graph.py`); the re-import block looked "unused inside query_graph" to a lint/cleanup pass and was stripped without checking the documented test contract. The leftover comment at `query_graph.py:273-276` supports this reading.

### Verdict & fix — **(b) symbol renamed/moved → update imports**

Preferred (canonical, matches strangler-fig direction): point the 6 test files at the new homes —
- `ragbot.orchestration.retrieval_filter` → `CRAG_GRADE_*`, `_CRAG_VALID_GRADES`, `_remap_grade_for_intent`, `_cliff_detect_filter`, `_rerank_threshold_gate`, `_autocut`, `_is_retrieval_adequate`
- `ragbot.orchestration.query_graph_helpers` → `parse_decomposed_sub_queries`

Alternative (1-line-ish, zero test churn): restore the two re-import statements in `query_graph.py` under the existing comment. Either way, also delete or reconcile the stale comment at `query_graph.py:273-276` and the now-false docstrings at `retrieval_filter.py:9-12` / `query_graph_helpers.py:8-11`.

---

## RC2 — `_helpers_routes.py` imports FastAPI private API absent from installed fastapi 0.135.3 (3 + 4 files)

### Failing import (FACT)

`tests/unit/_helpers_routes.py:22-26`:

```python
from fastapi.routing import (
    APIRoute,
    _EffectiveRouteContext,
    _IncludedRouter,
)
```

```
$ .venv/bin/python -c "import fastapi; print(fastapi.__version__)"
0.135.3
$ .venv/bin/python -c "import fastapi.routing as r; print([n for n in dir(r) if 'Included' in n or 'Effective' in n])"
[]
```

Neither private symbol exists → `ImportError` at collection for the 3 module-level importers:

| File | Import site |
|---|---|
| `tests/unit/interfaces/test_feedback_loop_wire.py:29` | `from tests.unit._helpers_routes import leaf_paths` |
| `tests/unit/test_admin_documents_debug_route.py:27` | `from tests.unit._helpers_routes import leaf_paths` |
| `tests/unit/test_route_workspace_scope_pin.py:39` | `from tests.unit._helpers_routes import iter_leaf_routes` |

### 4 more files fail at RUNTIME, not collection (sweep finding)

These import the helper **inside test functions**, so they collect clean and fail red at run (verified: `tests/unit/test_effective_prompt_endpoint.py::test_route_registered_on_app_router` FAILED with the same ImportError at `_helpers_routes.py:22`):

- `tests/unit/test_effective_prompt_endpoint.py:27`
- `tests/unit/test_chat_routing_compat.py:121,146`
- `tests/unit/interfaces/test_feedback_route_wire.py:34,49`
- `tests/unit/test_streaming_upload.py:211`

### git + venv archaeology (FACT)

- `git log --follow -- tests/unit/_helpers_routes.py` → single creating commit `9d2fee9` (2026-06-19, "feat(expert-rag): squash migrations 240→1 …"). The 3 module-level importer tests were created in the same commit.
- Helper docstring (`_helpers_routes.py:3`) states it targets *"FastAPI (>=0.137)"* lazy `_IncludedRouter` composition.
- Venv evidence: `.venv/lib64/python3.12/site-packages/fastapi-0.135.3.dist-info` directory mtime = **2026-04-16 16:40**. A pip upgrade/downgrade recreates dist-info with a fresh mtime, so fastapi has not been (re)installed since 2026-04-16 — i.e. **at no point since the helper's birth (2026-06-19) did this venv contain fastapi ≥ 0.137. These 7 files' helper-dependent paths have never passed in this environment.**
- `pyproject.toml:12` pins only a floor: `"fastapi>=0.110.0"`. No lock file exists (`ls *.lock` → none), so nothing contradicts 0.135.3.

**HYPOTHESIS** (labeled): the helper was authored by an agent against a different environment (worktree venv or upstream docs) where fastapi ≥ 0.137 with lazy router composition existed, and was merged without running the suite in this venv. Cannot be verified locally; what IS fact is that it never worked here.

### The helper's premise is false for the installed version (FACT — measured)

```
$ .venv/bin/python  # fastapi 0.135.3
parent.include_router(sub, prefix='/pre'); app.include_router(parent, prefix='/api')
router.routes types: ['APIRoute']
app APIRoute paths: ['/api/pre/leaf']
```

On 0.135.3, `include_router` **eagerly copies leaf `APIRoute`s with fully composed paths** — the plain-iteration fallback (already coded as the `elif isinstance(r, APIRoute)` branch at `_helpers_routes.py:57+`) is sufficient by itself.

### Verdict & fix — test-infra defect; keep all 7 test files

Fix `_helpers_routes.py` once: wrap the private-symbol import in `try/except ImportError` and fall back to plain `APIRoute` iteration (the lazy-wrapper branch stays for fastapi ≥ 0.137 forward-compat). All 7 files then work unmodified on both fastapi generations. Deleting the tests would be wrong — they pin route registration, RBAC, 4-key identity on upload paths (e.g. `test_streaming_upload.py` has 15 tests incl. `test_xadd_payload_carries_4key_identity`).

---

## RC3 — 9 additional order-dependent collection errors (full-suite runs only)

All 9 extra error files **collect and/or run clean in isolation** (verified individually); they only fail when collected after `tests/_archive_pre_squash_20260618/` and/or `tests/integration/test_2_demo_bots.py`. Two independent contamination mechanisms:

### RC3a — archive dir shadows the real `alembic` package (2 victims)

- `pyproject.toml:157` `testpaths = ["tests"]` with **no `norecursedirs`/`collect_ignore` for `tests/_archive_pre_squash_20260618/`** → pytest collects the archive.
- The archive contains an importable package literally named `alembic` (`tests/_archive_pre_squash_20260618/alembic/__init__.py` exists) in a dir pytest inserts on `sys.path` → it **shadows the installed alembic library** for the rest of the session.
- Victim 1 (deterministic): `tests/_archive_pre_squash_20260618/alembic/test_alembic_010a_merge_heads.py` — `ModuleNotFoundError: No module named 'alembic.config'`.
- Victim 2 (cross-contamination, FACT — reproduced with the pair alone): `tests/unit/test_anti_fabricate_rule_seed.py` → `ImportError: cannot import name 'op' from 'alembic' (/var/www/html/ragbot/tests/_archive_pre_squash_20260618/alembic/__init__.py)` — a **live, passing** unit suite (8 tests pass in isolation) killed by a graveyard directory.
- Fix: add the archive to `norecursedirs` (or `collect_ignore` in `tests/conftest.py`), and/or rename the inner `alembic/` dir.

### RC3b — load-test scripts masquerading as test modules poison `os.environ` at import (7 victims)

- `tests/integration/test_all_bots_load_120q.py:36-39` runs **at module import** (= collection time):

  ```python
  for line in ENV.read_text().splitlines():
      if line and not line.startswith("#") and "=" in line:
          k, v = line.split("=", 1)
          os.environ[k.strip()] = v.strip().strip('"')   # strips " but NOT '
  ```

- `.env:95` is single-quoted: `PROVIDER_API_KEYS_JSON='{"zeroentropy":…}'`. The naive parser writes the value **including the leading `'`** into the process env. Measured: `json.loads` on that value → `Expecting value: line 1 column 1 (char 0)` — the exact error in all 7 tracebacks. (Same corruption hits `.env:104 NOTIFY_CHANNEL_CONFIG_JSON` and `.env:129 PROVIDER_KEY_CONCURRENCY_JSON`.)
- `tests/integration/test_2_demo_bots.py:17-20` imports that harness at module level (plus `sys.path.insert` hack at line 14), so collecting it detonates the poison early (alphabetically 3rd file in `tests/integration/`).
- Every module collected afterwards that constructs `Settings` **at import time** then dies in the validator `src/ragbot/config/settings.py:481-487` (`raw_json = os.environ.get("PROVIDER_API_KEYS_JSON", "")` → non-empty, invalid). Verified minimal repro: `pytest test_2_demo_bots.py test_admin_analytics_routes.py --collect-only` → 1 error; each victim alone → clean.
- The 7 poisoned victims: `tests/integration/test_admin_analytics_routes.py`, `tests/unit/interfaces/http/test_resource_ownership_record_tid.py`, `tests/unit/test_embedded_workers.py`, `tests/unit/test_rate_limit_loadtest_bypass.py` (module-level `get_settings()` at line 52), `tests/unit/test_runtime_db_role_check.py`, `tests/unit/test_source_rate_limit_middleware.py`, `tests/unit/test_streaming_upload.py`. (The 3 RC2 files also show this error in full-suite runs — double-broken.)
- Aggravators (FACT):
  - `tests/integration/test_all_bots_load_120q.py`, `test_2_demo_bots.py`, `test_multiturn_dialogue.py` contain **zero** `def test_` functions (grep count = 0) — they are load-test *scripts* with `main()` living under the `test_*.py` collection glob purely for side effects. `test_multiturn_dialogue.py:40-43` duplicates the same broken parser.
  - `src/ragbot/interfaces/http/router.py:37` — `BASE = get_settings().app.api_base_path` at **module import** makes the whole `ragbot.interfaces.http` package un-importable whenever settings validation fails; this converts one bad env var into mass collection failure (import-time side effect, T3 design note).
  - The conftest dotenv-defusal (`tests/conftest.py:49-82`) neutralizes `dotenv.load_dotenv` but cannot see this hand-rolled parser.
- Fix: move/rename the 3 harness scripts out of the `test_*` namespace (e.g. `scripts/loadtest/` — they already have `__main__` entrypoints), or at minimum guard the env-mutation under `if __name__ == "__main__":`. Fixing only the quote-stripping would leave silent whole-`.env` overwrite of the test process env (it also clobbers `DATABASE_URL_APP` etc.), so relocation is the correct layer.

---

## Sweep: other stale imports across tests/ (3,108 from-imports checked via AST + live import)

Raw result: 132 failing `(module, symbol)` pairs. Classified by actually running every implicated suite:

### A. Genuinely broken (covered above)
- RC1 family: 5 collection files + `test_crag_three_states.py` (7 runtime FAIL, incl. `_CRAG_VALID_GRADES` at line 32).
- RC2 family: `_helpers_routes` → 3 collection + 4 runtime files.

### B. Silently no-op'd cleanup in `tests/conftest.py` (needs attention, does not error)
- `tests/conftest.py:248` — `from ragbot.shared.embedding_cache import clear_embedding_cache` → **symbol does not exist** (module has only `get_cached_embedding`/`set_cached_embedding`, `src/ragbot/shared/embedding_cache.py:55,76`). Swallowed by `except Exception: pass` (fail-soft), so the autouse per-test embedding-cache reset **has never run** post-squash (`git log -S clear_embedding_cache` → only `cd08119`). Potential cross-test contamination vector.
- `tests/conftest.py:254` — `from ragbot.infrastructure.reranker.jina_reranker import _jina_cb` → symbol gone; the circuit breaker is now a per-instance attribute `self._cb` (`src/ragbot/infrastructure/reranker/jina_reranker.py:118`). Same silent no-op.

### C. Intentional self-skipping "dead-code graveyard" (no action needed for green, but big inventory)
19+ suites import Strategy/DI subpackages whose **bodies are commented out in src**; each suite catches the ImportError and self-skips with reason "…is dead-code (body commented out)" (verified live: 13 skipped in one run, e.g. `tests/unit/test_query_router_registry.py:29`, `test_self_rag_router.py:31`, `test_convo_summary.py:33`, `test_chunk_quality_scoring.py:43`, `test_bartpho_accent_normalizer.py:17`, plus `test_tenant_model_tier`, `test_text_normalizer_strategy`, `test_tokenizer_registry`, `test_tool_client_strategy`, `test_cag_mode`, `test_d4_security_pentest`, `test_diff_reingest`, `test_proximity_cache`, `test_proposition_llm`, `test_hyde_generator`, `test_multi_vector_embedder`, `test_embedding_semantic_chunk`, `tests/unit/multi_agent_review/*` (4 files), partially `test_domain_neutral_multitenant`). These are permanently-skipped pins for mothballed features — worth a separate revive-or-remove decision (see `block-integrity-quality-gate` skill precedent), but they do not affect collection.

---

## Per-file verdict table (mandate files first)

| # | File | Root cause | Broken since | Verdict |
|---|---|---|---|---|
| 1 | `tests/unit/orchestration/test_crag_compound_query.py` | RC1 | `24f2451` 2026-06-26 | **(b)** update import → `retrieval_filter` |
| 2 | `tests/unit/test_cliff_detect_filter.py` | RC1 | `24f2451` | **(b)** → `retrieval_filter` |
| 3 | `tests/unit/test_output_guardrail_tuning.py` | RC1 | `24f2451` | **(b)** → `retrieval_filter` |
| 4 | `tests/unit/test_reranker_threshold_gate.py` | RC1 | `24f2451` | **(b)** → `retrieval_filter` |
| 5 | `tests/unit/test_query_decompose.py` | RC1 | `24f2451` | **(b)** → `query_graph_helpers` |
| 6 | `tests/unit/interfaces/test_feedback_loop_wire.py` | RC2 | born broken `9d2fee9` 2026-06-19 | keep; fix `_helpers_routes.py` fallback |
| 7 | `tests/unit/test_admin_documents_debug_route.py` | RC2 | `9d2fee9` | keep; fix helper |
| 8 | `tests/unit/test_route_workspace_scope_pin.py` | RC2 | `9d2fee9` | keep; fix helper |
| +9 | `tests/unit/test_crag_three_states.py` (runtime) | RC1 | `24f2451` | **(b)** update imports |
| +10..13 | `test_effective_prompt_endpoint` / `test_chat_routing_compat` / `test_feedback_route_wire` / `test_streaming_upload` (runtime) | RC2 | `9d2fee9` | keep; fix helper |
| +14 | `tests/_archive_pre_squash_20260618/alembic/*` | RC3a | since squash | exclude archive from collection |
| +15 | `tests/unit/test_anti_fabricate_rule_seed.py` | RC3a contamination | since squash | fix via archive exclusion (suite itself is healthy) |
| +16..22 | 7 RC3b victims listed above | RC3b contamination | since squash | fix via harness relocation; suites healthy |

## Recommended fix order (smallest risk first)

1. **RC1** — update imports in 6 test files to `ragbot.orchestration.retrieval_filter` / `ragbot.orchestration.query_graph_helpers`; delete stale comment `query_graph.py:273-276`; reconcile docstrings `retrieval_filter.py:9-12`, `query_graph_helpers.py:8-11`. Unblocks 58 collected + 7 failing tests.
2. **RC2** — `try/except ImportError` feature-detect in `tests/unit/_helpers_routes.py` (fallback = plain `APIRoute` iteration, verified equivalent on 0.135.3). Unblocks 13 collected + ~34 runtime tests.
3. **RC3a** — `norecursedirs`/`collect_ignore` for `tests/_archive_pre_squash_20260618`.
4. **RC3b** — move the 3 zero-test load harnesses out of `test_*` namespace (or guard env mutation under `__main__`); optionally fix single-quote stripping.
5. **conftest hygiene** — repoint/remove `tests/conftest.py:248,254` stale cleanup imports (currently silent no-ops).
6. **T3 note** — `src/ragbot/interfaces/http/router.py:37` module-level `get_settings()` converts env problems into package-import failures; consider deferring to app factory (design-level, not urgent).

## Key evidence index

- Removal commit: `git show 24f2451` — `-from ragbot.orchestration.retrieval_filter import (…9 names…)` and `-    parse_decomposed_sub_queries,`; parent `24f2451~1:src/ragbot/orchestration/query_graph.py:408-418,106-115` shows shim intact.
- Contract statements: `retrieval_filter.py:9-12`, `query_graph_helpers.py:8-11`, commit msg `094f7e8`, orphan comment `query_graph.py:273-276`.
- fastapi: `fastapi==0.135.3`; `dir(fastapi.routing)` contains neither private name; dist-info mtime 2026-04-16; `pyproject.toml:12` floor pin; live include_router check → `['/api/pre/leaf']` plain `APIRoute`.
- Poison: `test_all_bots_load_120q.py:36-39`; `.env:95/104/129` single-quoted; `settings.py:481-487` validator; bisect pair `test_2_demo_bots.py + test_admin_analytics_routes.py` → 1 error, each alone → 0.
- Shadowing: `tests/_archive_pre_squash_20260618/alembic/__init__.py`; pair run error `cannot import name 'op' from 'alembic' (…/_archive…/alembic/__init__.py)`; `pyproject.toml:157` has `testpaths` only.
