# Config-completeness gate — finding + gate shipped (2026-07-08)

> **Measured, not guessed.** Contract keys extracted from `_PIPELINE_CFG_KEYS`
> (`interfaces/http/routes/test_chat/_pipeline_config.py`); seeded keys from
> `SELECT key FROM system_config` on the live dev DB. Reproduce:
> `python scripts/check_config_completeness.py --strict`.

---

## 0. The finding (SỰ THẬT — evidence)

| | count |
|---|---|
| `_PIPELINE_CFG_KEYS` — keys the BE batch-loads from `system_config` | **175** |
| Seeded rows in `system_config` (dev DB) | 264 |
| **Contract keys NOT seeded → silently fall back to the code `DEFAULT_*` constant** | **71** |

**Why it matters.** `cfg_svc.get_many(_PIPELINE_CFG_KEYS)` batch-loads these 175 keys.
For the 71 with no `system_config` row, `get_many` returns nothing → every
`_pcfg(state, "<key>", DEFAULT)` call site falls through to `DEFAULT`. Prod therefore
runs 71 knobs on **values baked into code, not chosen in the DB** — the exact drift the
config-ownership split exists to kill: unreproducible on a fresh clone, invisible in the
DB, changed only by editing `src/`. This is hard evidence for the architecture decision,
not a hypothesis.

**Scope note (honest).** "Unseeded" ≠ "broken today" — behavior currently equals the
constant. The risk is *ownership + reproducibility*: the value lives in the wrong place.

---

## 1. The gate shipped

| Artifact | What it does |
|---|---|
| `scripts/check_config_completeness.py` | CI init-test: contract ⊆ seeded. `--strict` = every key must be seeded; default = baseline-aware; `--write-baseline` regenerates from DB. Exit 1 blocks build. |
| `scripts/config_constant_fallback_baseline.txt` | The 71 known constant-fallback keys (generated from DB, not hand-typed). Decreasing-only backlog. |
| `tests/unit/test_config_completeness_baseline.py` | DB-free guard: no stale baseline entry + baseline count never grows (ceiling 71). |

**Discipline** (mirrors `test_broad_except_count_decreases`): the gate is **green today**
(all 71 are known-baseline) but **fails the moment a new contract key is added without a
seed value**. As the DATABASE team seeds each key, regenerate the baseline and lower
`_BASELINE_MAX` — the backlog can only shrink.

**Where it runs** (`README_DEVOPS.md` §1): after `alembic upgrade head` on a freshly-seeded
DB, before `docker build`. Red gate → seed gap the DATABASE team fixes, never a backend
inline default.

---

## 2. The 71-key backlog — proposed triage (GIẢ THUYẾT — needs owner/DATABASE decision)

Each key needs a disposition: **(A) seed the value** (behavior/content → DB owns it) or
**(B) reclassify pure-technical** and drop from the contract tuple (constant is fine per
CLAUDE.md: timeout/retry/batch/concurrency). This split is a judgment call — proposed, not decided.

### 🔴 HIGH — behavior/content resolving from a constant (seed these first)
Model + prompt + vocab/pattern are the sharpest violations of "app owns content":
- **Model/prompt**: `draft_model`, `intent_extractor_model`, `intent_extractor_system_prompt`
- **Vocab/pattern**: `metadata_extraction_vocabulary`, `structural_ref_fallback_pattern`,
  `generic_vocab_enabled`, `generic_vocab_max_expansions`, `generic_vocab_max_matches`
- **Intent routing lists**: `grounding_intents`, `skip_rewrite_intents`, `skip_reflect_intents`,
  `rerank_skip_intents`, `rerank_cliff_skip_intents`, `multi_query_skip_chitchat_intent`
- **Feature toggles**: `decompose_enabled`, `reflection_enabled`, `neighbor_expand_enabled`,
  `self_rag_critique_enabled`, `entity_grounding_enabled`, `refuse_short_circuit_enabled`,
  `adaptive_router_l1_enabled`, `stats_index_race_enabled`, `retrieve_fallback_enabled`,
  `speculative_hallu_verify_enabled`, `metadata_layer3_llm_enabled`,
  `diacritic_restoration_enabled`, `diacritic_restoration_use_model`,
  `bm25_substring_fallback_enabled`, `rerank_threshold_gate_after_cliff_enabled`,
  `multi_query_entity_gate_enabled`, `understand_use_structured_output`,
  `generate_context_trust_hint_enabled`, `prompt_token_opt_enabled`,
  `prompt_token_opt_factoid_skip_history`, `batch_step_logging_enabled`,
  `crag_lenient_grade_for_compound_intents_enabled`, `semantic_cache_skip_multi_turn`,
  `semantic_cache_skip_numeric`
- **Thresholds / caps** (behavioural — change answers): `crag_min_relevant_count`,
  `crag_min_relevant_fraction`, `decompose_confidence_gate`, `decompose_min_tokens`,
  `decompose_top_k_per_subquery`, `multi_query_complexity_min`, `multi_query_min_tokens`,
  `multi_query_dedup_threshold`, `range_query_min_confidence`, `self_rag_critique_threshold`,
  `grounding_check_async_top_score_threshold`, `speculative_similarity_threshold`,
  `guardrail_oos_similarity_threshold`, `prompt_token_opt_dedupe_jaccard_threshold`,
  `prompt_token_opt_min_chunk_score`, `generate_context_chars_cap`, `prompt_max_tokens`,
  `rag_max_documents`, `rag_*`/`retrieve_fallback_top_k`, `rerank_retrieval_safety_n`,
  `entity_grounding_max_entities`, `neighbor_token_budget`, `neighbor_window_size`,
  `stats_index_limit`, `max_total_graph_iterations`, `structural_ref_fallback_pattern`

### 🟢 LOW — plausibly pure-technical (may stay a constant; reclassify out of the contract)
Concurrency/timeout/SLA knobs CLAUDE.md explicitly allows as constants:
`crag_grade_concurrency`, `grade_timeout_s`, `neighbor_max_concurrency`,
`speculative_retrieve_timeout_s`, `pipeline_multi_query_speculative_timeout_s`,
`stats_race_timeout_s`, `generate_p95_sla_ms`, `guardrail_leak_shingle_size`

> If a key is reclassified LOW, the correct fix is to **remove it from `_PIPELINE_CFG_KEYS`**
> (it stops being a system_config contract key) — then the baseline shrinks honestly, not by
> hiding it. If HIGH, the DATABASE team seeds it with its current constant value (zero behavior
> change) via one alembic UPSERT, and lowers `_BASELINE_MAX`.

---

## 3. Next

1. Owner + DATABASE team triage the 71 (A-seed vs B-reclassify) — proposal in §2.
2. Per HIGH key: alembic UPSERT seeding the current constant value (idempotent + reversible,
   sacred #7) → regenerate baseline → lower `_BASELINE_MAX`. **Zero behavior change** (value
   equals the constant already in effect); only ownership moves code → DB.
3. Wire `scripts/check_config_completeness.py` as a **required** CI step post-seed.
4. When the backlog hits 0, flip the gate default to `--strict` and delete the baseline.

*Contract owner: `README_DEV.md`. Value owner: `README_DATABASE.md`. Gate owner: `README_DEVOPS.md`.*
