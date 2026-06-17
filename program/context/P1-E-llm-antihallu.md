# P1-E — LLM OPS & ANTI-HALLU (Phase 1 context absorption)

> READ-ONLY domain map. Every claim = `file:line` or commit SHA. STANCE = understand, not judge.
> Scope: every LLM call + guardrails. `query_graph.py` (8087 lines) + `infrastructure/llm/` + `infrastructure/guardrails/`.

---

## (a) PURPOSE × MODEL × TEMPERATURE × WHERE-CALLED matrix

Model is NOT chosen in code. `DynamicLiteLLMRouter.complete_runtime` is purpose-agnostic — it takes a resolved `cfg` (`ModelRuntimeConfig`) from `model_resolver.resolve_runtime(record_tenant_id, record_bot_id, purpose=<binding_purpose>)` and uses `cfg.litellm_name` + `cfg.params.temperature` + `cfg.params.max_tokens` (`dynamic_litellm_router.py:557-570`). The **purpose string** is the binding lookup key into `bot_model_bindings`; the **temperature** is decided in `query_graph._invoke_llm_node:1272-1279` (override) OR inherited from the binding row.

| Pipeline purpose | binding key | Model (production default) | Temp | Where called (`query_graph.py`) | Temp source |
|---|---|---|---|---|---|
| **answer (generate)** | `generation` → cost-routed to `llm_factoid`/`llm_chitchat`/`llm_oos`/`llm_primary` via `_resolve_purpose_for_intent` (`6352`) | **gpt-4.1-mini** (fleet policy, alembic 0184) | `generation_temperature`=**0.0** (`1273`, `DEFAULT_GENERATION_TEMPERATURE` `_10_rbac.py:190`) | `generate` node `6431` (free-form), `6377` (structured) | forced via `_invoke_llm_node:1272` |
| **CRAG grader** | `grading` | **gpt-4.1-mini** (alembic 0195, was gemma) | 0.0 (`grade`∈DETERMINISTIC set `_10_rbac.py:204`) | `grade` node `5331/5341/5448` | `_invoke_*` path only |
| **decompose** | `decompose` | gpt-4.1-nano | 0.0 (deterministic set `:202`) | `7858/7861` (direct `llm.complete`), `3031/3057` (`_invoke`) | mixed — see gap below |
| **HyDE** | `hyde` | gpt-4.1-nano | **NOT forced** (excluded on purpose, commit `c6c6df4` — "light variation aids recall") | `1707` | binding temp |
| **condense** | `condensing` | gpt-4.1-nano | 0.0 (`:203`) | `2048` (`_invoke`) | forced |
| **understand/intent** | `understand_query` | gpt-4.1-nano | 0.0 (`:203`) | `2206`, `7679/7690/7697` | forced |
| **rewrite** | `rewriting` | gpt-4.1-nano | 0.0 (`:202`) | `2709` | forced |
| **multi_query** | `multi_query` | **haiku** (`DEFAULT_MULTI_QUERY_MODEL` `_11_*.py:143`) | 0.0 in set (`:202`) BUT direct call `2797/4152` does **not** pass temp → binding temp | `2797`, `4152` (direct `llm.complete`) | **GAP — not forced** |
| **grounding judge** | `grounding` | **gpt-4.1-nano** (alembic 0195, was gemma — 30s timeout) | 0.0 in set (`:204`) BUT direct call `1041/6834` does not pass temp | `6834` (sync), `1041` (async B5) | **GAP — not forced** |
| **reflect (self-correct)** | `reflection` | per-bot binding | 0.0 in set (`:205`) | `7184/7214` | `_invoke` path |
| **slot extractor (action)** | `slot_extractor_model` | **anthropic/claude-haiku-4-5** (`_20_*.py:63`) | n/a (separate service) | `slot_extractor.py:165` | service |
| **narrate (CAG)** | `narrate` | **claude-haiku-4-5** (`_20_*.py:55`) | enrichment 0.0 | CAG path | — |

**Temp enforcement gap (factual, not yet judged):** the deterministic-temp override lives only inside `_invoke_llm_node:1274` / `_invoke_structured_llm_node`. Three purposes call `llm.complete(cfg, ...)` **directly**, bypassing it, so they run at the binding's resolved `cfg.params.temperature`: `multi_query` (`2797`, `4152`), `grounding` (`1041`, `6834`), `decompose` (`7858`). If those bindings carry temp 0 in DB they are fine; if not, determinism is not guaranteed for them. Commit `c6c6df4` fixed the `_invoke_*` path but did not touch the direct call sites.

---

## (b) SACRED INJECT / OVERRIDE AUDIT — verdict: CLEAN (with 1 opt-in caveat)

### App-inject (does app prepend/append to bot's system_prompt?) — **CLEAN**

Prompt assembly is `generate` node `query_graph.py:6258-6292`:
- `system_prompt = state.get("bot_system_prompt") or _lang(state).prompt_generator` (`6258-6260`) — bot owner's prompt is THE system message verbatim (`6279`). The only fallback is the LanguagePack default when the bot set none.
- **No instruction text is prepended/appended.** The retrieved context and the question are placed in the **USER message**, XML-wrapped: `<documents>…</documents>\n\n<question>…</question>` (`6287-6292`). Wrapping data in the user turn is structural framing, not a system-prompt injection of platform rules.
- The only `system_prompt.replace()` is `ACTION_CAPTURED_SLOTS_PLACEHOLDER` substitution (`6265-6269`) — pure placeholder fill of owner-declared `{captured_slots}`, absent placeholder ⇒ untouched (comment `6261-6264` cites sacred-rule 10).
- `trust="data_only"` / `type` attributes on `<context>`/`<chunk>` (`6248-6255`) are XML attributes on the data block, config-gated (`generate_context_trust_hint_enabled`, `_resolve_xml_wrap_enabled`), not natural-language rules injected into the prompt.

### App-override (regex/replace on LLM answer? math_lockdown? blocked fallback?) — **CLEAN, one opt-in exception**

- `guard_output` explicitly documents the boundary `6723-6728`: *"application does NOT regex-check + override … grounding ratio below is observability only; it never substitutes the answer."*
- Grounding judge (`local_guardrail.llm_grounding_check`) returns `severity="warn"`, `action="hitl"` (`local_guardrail.py:524-527`) → persisted/logged only; **only `severity=="block"` raises** GuardrailBlocked (`914`). Grounding never blocks.
- `system_prompt_leak` returns `severity="block"` (`local_guardrail.py:337`) → this **does** block the answer, but it is a security guard (answer is leaking the system prompt), not a content override. It subtracts doc-grounded shingles first (`query_graph.py:6880-6897`) to avoid false-blocking corpus relay.
- **NO math_lockdown / numeric regex-replace anywhere.** Numeric grounding (`grounding_check` Pass 3, `local_guardrail.py:391-399`) only *classifies* grounded/not — never edits the answer.
- Post-hoc citation attribution `6479-6497` adds a citation entry for verifiability but comment `6483` confirms it does NOT alter answer text.
- **Exception (opt-in, per-bot, default OFF):** `critique_parse` self-RAG (`6650-6717`). When `self_rag_critique_enabled` is True AND the **LLM's own** `[Unsupported]` critique-token ratio ≥ threshold, the answer is **replaced** by `bots.oos_answer_template` (`6708-6712`). This is gated on the LLM's self-emitted markers, uses the bot's own template (fallback `DEFAULT_OOS_ANSWER_TEMPLATE = ""` empty string, `_04_jwt_auth.py:30` — never i18n), and is default OFF (`DEFAULT_SELF_RAG_ENABLED`). It is technically an app-side answer substitution but driven by the model's own critique, not a platform rule. Flag for Phase-2 scrutiny.

**Verdict: sacred inject = CLEAN. sacred override = CLEAN for the always-on path; one opt-in (self-RAG critique) replaces answer with bot template when the LLM self-flags unsupported.**

---

## Grounding judge "≤5 sentences" — HALLU hole (factual)

`local_guardrail.py:413` `max_sentences: int = 5`; `:445` `sentences = sentences[:max_sentences]`. Only the first 5 sentences of an answer are sent to the grounding judge — sentences 6+ are **never checked**. Mitigant: `generate_max_tokens=250` (doc 15-O §6) keeps answers short, so most answers are ≤5 sentences. But a long multi-fact answer (aggregation/comparison can request the `sub_answers` schema, `query_graph.py:544-550`) could exceed 5 sentences, leaving the tail unverified. `max_sentences` is a hardcoded function default — not config-driven.

## Numeric / aggregation (factual)

App does NOT compute sums. Aggregation/comparison/multi_hop intents (`544-550`) get the reasoning-first `sub_answers` schema (`_resolve_generate_schema:554`) so the **LLM enumerates** each fact; a `stats_index_repo` route (`1161`, commit `6fcf899`) serves pre-indexed stats. There is **no app-side sum + no warning when the LLM mis-sums** — correctness rests on the LLM + grounding. No silent wrong-sum override (compliant with sacred), but also no app safety net for arithmetic.

## Haiku contradiction — RESOLVED (no violation)

CLAUDE.md's Haiku ban is for the **Claude Code dev-agent tier** (`cost_audit.py model-mix`, "quality risk for HALLU=0 sacred" in the *coding* session). The **product pipeline** legitimately uses `claude-haiku-4-5` for small-token partial tasks per memory `feedback_haiku_partial_only`: slot extraction (`slot_extractor.py:165`), CAG narrate (`_20_*.py:55`), multi_query (`_11_*.py:143` `DEFAULT_MULTI_QUERY_MODEL="haiku"`), ingest enrichment (`anthropic_haiku_batch.py`). The **answer LLM is gpt-4.1-mini**, NOT Haiku (`feedback_haiku_partial_only`: "Haiku CHỈ cho decomposer + HyDE + ingest enrich; LLM answer = gpt-4.1-mini"). These are two different "Haiku" governance scopes. No contradiction.

## Loop safety — PROVEN bounded

Two independent counters:
1. `_total_graph_iterations` incremented in `grade` (`5209`), capped at `max_total_graph_iterations` (`DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS=8`, `_10_rbac.py:182`). Exceed ⇒ `grade` short-circuits to `retrieval_adequate=True` with top-2 chunks (`5211-5217`), breaking the CRAG→rewrite_retry loop. Hard backstop at `8028-8031` logs `graph_iteration_cap_reached`.
2. `reflect_retries` capped at `max_reflect_retries` (`DEFAULT_MAX_REFLECT_RETRIES=1`, `_15_*.py:120`) in `reflect` node (`7232-7233`). Doc 15-O §4 lists default 2; constant is 1 (config override per-bot).
Both counters persist in graph state and gate edges → no infinite loop possible.

---

## (c) GIT MODEL-EVOLUTION TIMELINE

| Commit / alembic | Date | Change | WHY (evidence) |
|---|---|---|---|
| alembic 0184 | — | answer model → fleet gpt-4.1-mini | "nano-drift" — nano too weak for answer (commit `ccc9f57`: RAGAS 0.86→0.91) |
| `c29d95c` Bug#8 | — | understand: CLASSIFY-FIRST + preserve aggregation cues | aggregation intent miss |
| `f6eeb42` | 2026-06-04 | route dead Anthropic→OpenAI for slot extractor; action slot-machine alive | dead ANTHROPIC key → slot gate never fired (3 built-not-wired) |
| `c6c6df4` | 2026-06-09 | force temp=0 on transform/classification purposes (`DEFAULT_DETERMINISTIC_LLM_PURPOSES`) | spa Q7 intermittent refuse↔answer; decompose/rewrite/MQ inherited ~0.3 temp → different sub-queries → answer flips. **HyDE excluded** (variation aids recall) |
| `cb9c3b1` | 2026-06-09 | robust grading: DB-verify + LLM-judge (no string-match) | grading reliability |
| alembic 0193 | 2026-06-09 | sysprompt allow grounded compute | over-strict anti-fabricate refused grounded math |
| alembic 0194 | 2026-06-09 | rewrite all sysprompts best-practice | sysprompt quality pass |
| **alembic 0195** | 2026-06-09 | **purge LMStudio gemma**: grounding gemma→**gpt-4.1-nano**, grading gemma→**gpt-4.1-mini**, disable custom_openai provider | grounding=30.0s = `DEFAULT_LLM_TIMEOUT_S` exact → gemma-4-e2b-it on self-hosted llm.innocom.co TIMED OUT every multi-fact turn = **76% of p95** AND grounding wasn't protecting anything on 10 bots. p95 ~40s→~12s |
| `8f1f00b` | — | (same arc) purge LMStudio gemma timeout → grounding→nano, grading→mini, p95 40s→15s | duplicate-arc of 0195 |
| `4ed436d` | — | run #6 verified: Coverage 0.945, HALLU=0 (post temp=0 fix) | validation |

Net current state: **answer=gpt-4.1-mini, grading=gpt-4.1-mini, grounding=gpt-4.1-nano, transforms=gpt-4.1-nano, MQ/slot/narrate/enrich=claude-haiku-4-5**. LMStudio (gemma/qwen self-hosted) purged from live path.

---

## (d) PLANS / STATUS related

- `dcaf504` plan: GA-hardening — RLS P0 + retrieval determinism + silent-degrade. Touches grounding silent-degrade (relevant: grounding async/skip degrade paths `6757`, `6797-6805`).
- README "what changed & why" (`dcf63a4`) documents engine swaps with evidence.
- Self-RAG critique (`critique_parse`) = shipped but default OFF (per-bot opt-in) — Wave A-B.
- B5 Phase B async grounding (`_run_grounding_check_background:1005`) = shipped, opt-in via `grounding_check_async_enabled`.
- Speculative streaming (`1299`) + Phase 3 verifier = per-bot, default OFF, "HALLU=0 sacred until Phase 3 verifier ships".
- Doc `15-O-anti-hallu-tuning.md` = verified 2026-05-05 9-layer base (note: predates ZE→? and 0195 grounding-model swap; §15 evidence table is Jina-era).

---

## (e) 10 OPEN QUESTIONS

1. **Temp-0 coverage gap:** direct `llm.complete` calls for `multi_query` (`2797`,`4152`), `grounding` (`1041`,`6834`), `decompose` (`7858`) skip the `_invoke_llm_node` temp override. Do their DB bindings carry temp 0, or do they run nondeterministic? (`c6c6df4` only fixed the `_invoke_*` path.)
2. **Grounding ≤5-sentence hole:** can a `sub_answers`-schema multi-fact answer exceed 5 sentences and leave tail claims unverified? Should `max_sentences` be config-driven / scaled to answer length?
3. **Grounding model = nano (0195):** is gpt-4.1-nano accurate enough as a SUPPORTED/NOT_SUPPORTED judge, or does a weak judge create false PASS (silent HALLU)? Was the HALLU=0 A/B in 0195's docstring actually run?
4. **Self-RAG critique override:** is replacing the answer with `oos_answer_template` on the LLM's own `[Unsupported]` markers (`6708`) acceptable under sacred-rule 2, given it's the model self-flagging? Any bot has it enabled in prod?
5. **Aggregation arithmetic:** with no app-side sum + no warning, who verifies a multi-fact total is correctly summed? Is grounding numeric-overlap enough, or can the LLM fabricate a plausible-but-wrong sum that passes (each addend grounded, the sum not)?
6. **DEFAULT_OOS_ANSWER_TEMPLATE="" :** if a bot never set `oos_answer_template`, a self-RAG refuse returns empty string as the answer (`6698`). Is an empty answer the intended UX, or a silent blank?
7. **reflect default 1 vs doc 2:** `DEFAULT_MAX_REFLECT_RETRIES=1` (`_15_*.py:120`) contradicts doc 15-O §4 "default 2". Which is the production value?
8. **Grounding silent-degrade:** on judge timeout/error, `llm_grounding_check` returns `None` = treated as grounded (`local_guardrail.py:506-510`, `554-555`, `591`). Is a degraded judge silently passing potentially-ungrounded answers (transport-degrade-silent vs HALLU risk)?
9. **HyDE non-deterministic by design:** HyDE excluded from temp-0 set (`c6c6df4`). Does HyDE variation cause the same run-to-run retrieval flips that motivated the temp-0 fix for the other transforms?
10. **15-O staleness:** the anti-hallu tuning doc's §15 evidence is Jina-era and predates alembic 0195's grounding-model swap. Are the threshold defaults (`reranker_min_score_active=0.15`, `grounding_check_threshold=0.3`) still calibrated for the current ZE + nano-grounding stack?
