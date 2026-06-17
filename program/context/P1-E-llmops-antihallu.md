# P1-E — LLM OPS & ANTI-HALLU (Phase 1 read-only report)

> Agent: P1-E llmops-antihallu · Date 2026-06-10 · Anchor: branch `fix-260604-action-slotmachine-dead-key`, alembic head 0195.
> Every claim carries `file:line`, commit SHA, or live-DB query result (read-only SELECT, `ragbot_v2_dev`).
> Companion: an earlier sibling file `P1-E-llm-antihallu.md` exists; this report INDEPENDENTLY re-verified its claims and **corrects two of them** (math_lockdown existence §c4; Haiku verdict §d).

---

## (a) PURPOSE × MODEL × TEMP × COST matrix

Model choice lives in DB, not code: `query_graph._invoke_llm_node` resolves
`model_resolver.resolve_runtime(record_tenant_id, record_bot_id, purpose=...)`
(`src/ragbot/application/services/model_resolver.py`), then
`DynamicLiteLLMRouter.complete_runtime` uses `cfg.litellm_name` + temp fallback
`temperature if temperature is not None else cfg.params.temperature`
(`src/ragbot/infrastructure/llm/dynamic_litellm_router.py:562-563`).
Temp forcing happens ONLY in `_invoke_llm_node` (`query_graph.py:1272-1279`):
`generation` → `generation_temperature` (default 0.0, `_10_rbac.py:190`);
purposes in `DEFAULT_DETERMINISTIC_LLM_PURPOSES` (`_10_rbac.py:201-207`: decompose,
rewrite/rewriting, multi_query, condense/condensing, routing, understand_query,
intent, grade/grading, grounding, guard, reflect/reflection) → 0.0; else binding temp.

### Live binding matrix (DB query 2026-06-10, `bot_model_bindings` JOIN `ai_models`, active AND not deleted)

| Purpose (binding key) | Model | n bots | binding temp min–max | max_tokens |
|---|---|---|---|---|
| generation / llm_primary | **gpt-4.1-mini** | 2 / 18 | 0.00–0.30 | 450–2048 |
| llm_factoid·chitchat·oos·greeting·feedback·comparison·aggregation·multi_hop·vu_vo·out_of_scope | gpt-4.1-mini | 2 each | 0.00–0.30 | 450–1000 |
| grading (CRAG grader) | **gpt-4.1-mini** (10 bots, alembic 0195) + gpt-4.1-nano (2) | 12 | 0.00–0.30 | 450–1024 |
| grounding (judge) | **gpt-4.1-nano** (alembic 0195) | 12 | **0.00–0.30** ⚠ | 450–1024 |
| decompose · condense/condensing · rewrite/rewriting · understand_query · intent · routing · multi_query · reflect/reflection · guard · chat · grade | gpt-4.1-nano | 2 each | 0.00–0.30 | 450–1000 |
| enrichment (ingest) | gpt-4.1-nano binding (12) — but `system_config.enrichment_model = "gpt-4.1-mini"`, temp 0.0 (live DB) | 12 | 0.00–0.30 | 450–1024 |
| embedding | zembed-1 (ZeroEntropy) | 15 | n/a | — |
| rerank | zerank-2 (ZeroEntropy) | 14 | n/a | — |
| slot_extractor (action) | `system_config.slot_extractor_model = "openai/gpt-4.1-mini"` (live DB, alembic 0169) | global | service-managed | — |
| multi_query (config layer) | `system_config.multi_query_model = "gpt-4.1-mini"` (live DB, alembic 0169) | global | binding temp ⚠ | — |
| cascade low/high (default-OFF) | gpt-4.1-nano / gpt-4.1-mini (live DB, alembic 0169) | global | — | — |
| narrate (CAG, default-OFF `DEFAULT_CAG_MODE_ENABLED=False` `_20:23`) | `DEFAULT_NARRATE_MODEL="claude-haiku-4-5"` `_20:55` — **dead constant, never imported** (grep: only definition line) | 0 | — | — |

### MEASURED cost per call (live `model_invocations`, last 7 days — read-only SELECT)

| purpose | model | n calls | avg in tok | avg out tok | **avg cost/call** |
|---|---|---|---|---|---|
| generation | gpt-4.1-mini | 1869 | 5883 | 288 | **$0.002302** |
| understand_query | gpt-4.1-mini | 755 | 1701 | 44 | $0.000563 |
| understand_query | gpt-4.1-nano | 76 | 1632 | 43 | $0.000271 |
| grading | gpt-4.1-mini | 331 | 262 | 16 | $0.000126 |
| rewriting | gpt-4.1-mini / nano | 272 / 111 | ~550 | ~12 | $0.000240 / $0.000096 |
| decompose | gpt-4.1-mini | 194 | 187 | 85 | $0.000211 |
| grading (zombie) | custom_openai/gemma-4-e2b-it | 9 | 0 | 0 | $0 (timeouts; last row 2026-06-09 16:45, pre-0195-purge) |

Price table (`ai_models` live): gpt-4.1-mini $0.0004/$0.0016 per 1k in/out · gpt-4.1-nano
$0.00016/$0.00064 · claude-haiku-4-5-20251001 $0.001/$0.005 (enabled=t) · gemma/qwen
custom_openai disabled (enabled=f, alembic 0195).

**Finding A1 — nano-policy not realized for most bots (measured)**: transform purposes have only
n=2 nano bindings; the other ~16 bots fall through `resolve_runtime`'s llm_primary fallback to
**mini** — measured 755 understand_query calls on mini vs 76 on nano (≈2.1× cost/call drift on the
highest-volume transform).
**Finding A2 — cost observability hole**: `model_invocations` contains ONLY 5 purposes all-time
(generation, understand_query, grading, rewriting, decompose). `grounding`, `multi_query`,
`condense`, `hyde` never appear — their call sites use `llm.complete(cfg, …)` directly
(`query_graph.py:1041,2797,4152,6834`) without the `invocation_logger.invoke_model` wrapper
(`query_graph.py:1262-1271`); grounding cost goes to `request_steps` only (comment `:6841-6845`).
Charter axis RẺ ("cost/query per-tenant") is incomplete for those purposes.
**Finding A3 — temp-0 enforcement gap is LIVE**: the 5 direct `llm.complete` sites pass no
`temperature=` → router uses binding temp (`dynamic_litellm_router.py:562-563`), and live bindings
for grounding/multi_query carry **temp up to 0.30** (DB: grounding n=12 tmin 0.00 tmax 0.30).
Commit `c6c6df4` (2026-06-09, temp-0 determinism fix) only covered the `_invoke_llm_node` path —
the grounding judge can be nondeterministic today on bots whose binding temp is 0.30.

Failover: 1-hop `record_fallback_model_id` (`dynamic_litellm_router.py:425-499`,
`DEFAULT_LLM_FAILOVER_ENABLED=True`, `MAX_HOPS=1` `_06_llm_defaults.py:65-66`). Live DB: exactly
**2 active generation bindings fall back to claude-haiku-4-5-20251001** (wired by alembic 0070).

---

## (b) GIT MODEL-HISTORY — 3 turning points + supporting commits

1. **Nano-drift answer-model fix — alembic 0184 + commit `ccc9f57`** (2026-06-08).
   Root cause in 0184 docstring (`alembic/versions/20260608_0184_realign_answer_model_mini_dedupe.py:7-16`):
   alembic 0161 had repointed every bot's answer binding mini→nano as a rate-limit workaround for a
   sequential load test; ~8 demo bots silently stayed on nano ("nano too weak for the answer node —
   citation + key-fact extraction… lich-su-vn 0.58"). 0184 realigns ALL llm_primary/generation
   to gpt-4.1-mini + dedupes 4× duplicate seed rows. `ccc9f57`: RAGAS 0.86→0.91.

2. **Temp-0 forcing on transforms — commit `c6c6df4`** (2026-06-09 19:04).
   Measured root cause (commit msg): spa Q7 SAME multi-fact question intermittently refused vs
   answered; generate was already temp 0 — variance came UPSTREAM: decompose/rewrite/multi_query/
   condense inherited ~0.3 → different sub-queries → different chunks → answer flips. Fix =
   `DEFAULT_DETERMINISTIC_LLM_PURPOSES` + 0.0 (`_10_rbac.py:192-207`). **HyDE excluded on purpose**
   ("light variation aids recall"). Verified by `4ed436d` "run #6: Coverage 0.945, HALLU=0".

3. **LMStudio/gemma purge — alembic 0195 + commit `8f1f00b`** (2026-06-09).
   Root cause in 0195 docstring (`20260609_0195_purge_lmstudio_grounding_grading_openai.py:5-13`):
   grounding_check = 30.0s = exactly `DEFAULT_LLM_TIMEOUT_S` → gemma-4-e2b-it on self-hosted
   LMStudio **timed out on every multi-fact turn = 76% of p95**, then degraded to skip → "grounding
   wasn't actually protecting anything on the 10 affected bots". Fix: grounding→gpt-4.1-nano,
   grading→gpt-4.1-mini, disable custom_openai provider + models. Expected p95 ~40s→~12s.
   DB confirms: gemma/qwen enabled=f; last gemma invocation 2026-06-09 16:45 (zombie rows = pre-purge).

Supporting arc: `f6eeb42` + alembic 0169 (2026-06-04) — **dead ANTHROPIC key → route Haiku config
keys to OpenAI** (`slot_extractor_model`→"openai/gpt-4.1-mini", `multi_query_model`→"gpt-4.1-mini",
`cascade_low/high`→nano/mini; downgrade() restores "haiku" — `0169:40-76`). LMStudio onboarding arc
(`de99ae0`, `7315a41`, `61e52ef`, `757f124` ~2026-05-21) is what 0195 later reversed. Sysprompt arc:
alembic 0193 (allow grounded compute — anti-fabricate was refusing grounded math), 0194 (rewrite all
sysprompts best-practice), `cb9c3b1` (robust grading: DB-verify + LLM-judge, no string-match).

---

## (c) ANTI-HALLU MECHANISMS MAP

**c1. guard_input** (`query_graph.py:1794-1853`): `guardrail.check_input` → flags; on
`GuardrailBlocked`, answer = `_resolved_oos_template(state)` (5-tier resolver:
`bots.oos_answer_template` → `plan_limits` → workspace → … → `system_config`,
`application/services/oos_template_resolver.py:8-12`) overridable per-rule by the rule's own
`response_message` (`:1843`). Platform fallback `DEFAULT_OOS_ANSWER_TEMPLATE = ""`
(`_04_jwt_auth.py:30`) — empty string, never i18n hardcode. **Compliant with sacred #3.**

**c2. Grounding judge** (`local_guardrail.py:408+` `llm_grounding_check`): per-sentence
SUPPORTED/NOT_SUPPORTED via structured judge (default `DEFAULT_GROUNDING_USE_STRUCTURED=True`
`_14:185`); hit when unsupported ratio > threshold 0.3 (`_15:105`), per-intent overrides live in
`system_config.grounding_check_threshold_by_intent` + intent-gated skip (`query_graph.py:6729-6760`).
Severity is **"warn"/action "hitl"** — observability only, never substitutes the answer (comment
`query_graph.py:6723-6728`). **Coverage limits**: (i) only first **5 sentences** judged —
`max_sentences=5` hardcoded fn default (`local_guardrail.py:413`, truncation `:445`); (ii) per-chunk
context preview 500 chars unless full-doc (`DEFAULT_GROUNDING_CONTEXT_PREVIEW_CHARS=500` `_16:200`,
bypass `local_guardrail.py:450-456`); (iii) silent-degrade — judge error/timeout ⇒ `None` ⇒ treated
as grounded; (iv) async/parallel modes (`DEFAULT_GROUNDING_CHECK_TRULY_PARALLEL=True` `_21:126-131`)
judge after the response is on the wire (`query_graph.py:1098`). Heuristic pre-pass `grounding_check`
3-pass: citation-marker / verbatim-substring / numeric-token-overlap (`local_guardrail.py:384-405`,
`DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED=True` `_14:183`).

**c3. Citation validation** (`query_graph.py:6341-6497`): LLM-claimed chunk_ids filtered against
actually-retrieved ids — invalid dropped + `citation_validation_fail_total` metric (`:6396-6406`,
free-form regex path `:6430-6469`). Post-hoc attribution when LLM didn't self-cite: top graded chunk
appended `citations_source="posthoc_top_chunk"` — comment `:6482` "does NOT alter answer text".

**c4. math_lockdown — CORRECTION vs sibling P1-E file** (it claimed "NO math_lockdown anywhere"):
module **EXISTS**: `infrastructure/guardrails/math_lockdown.py` (238 lines, pure functions —
VND/percent/duration/docref normalization, `extract_numeric_claims:166`,
`find_ungrounded_numbers:210`). Wiring audit (grep src/ + tests/): `extract_numeric_claims` is used
ONLY to **decide whether to skip caching** numeric answers (`query_graph.py:7361-7364`,
`semantic_cache_skip_numeric` — comment: "never to alter the answer"); `find_ungrounded_numbers` is
referenced only by tests (`tests/unit/test_math_lockdown_docref.py`) + a test_chat docstring
(`test_chat.py:3444`). **No answer override anywhere — sacred #2/#4 compliant.** Stale-comment flag:
`_21:134` says guard_output runs "three checks (PII, math_lockdown, leak)" in parallel — guard_output
actually runs grounding/leak/PII; math_lockdown is NOT one of them.

**c5. guard_output** (`query_graph.py:6719+`): grounding (warn-only, c2) + `system_prompt_leak`
(severity **"block"** `local_guardrail.py:337` — the one blocking output rule; security guard, not
content override; doc-grounded shingles subtracted first `query_graph.py:6880-6897`; OOS-similarity
carve-out Jaccard 0.90 `_06:84-87`). Only `severity=="block"` raises (`local_guardrail.py:914`).

**c6. Self-RAG critique (the one app-side answer swap, opt-in)**: `critique_parse`
(`query_graph.py:6650-6717`, edge `:8014-8021`) — when `self_rag_critique_enabled`
(default **OFF**, `DEFAULT_SELF_RAG_ENABLED=False` `_20:275`) AND the LLM's own `[Unsupported]`
marker ratio ≥ 0.3 (`_20:279`), the answer is replaced by the bot's `oos_answer_template`. Driven by
the model's self-critique + bot-owned template — borderline vs sacred #2; flagged for Phase 2.

**c7. Loop bounds**: `max_total_graph_iterations=8` (`_10_rbac.py:182`, short-circuit + hard backstop
`query_graph.py:8028-8031`); `DEFAULT_MAX_REFLECT_RETRIES=1` (`_15:120`) — **contradicts doc 15-O §4
"default 2"**. No unbounded CRAG→rewrite loop possible.

**c8. Sysprompt layer (the actual anti-fabricate)**: bot-owned `bots.system_prompt` rules
(15-O layer 9); used verbatim as THE system message (`query_graph.py:6258-6292`), only
`{captured_slots}` placeholder substitution (`:6265-6269`). Alembic 0193/0194 = latest sysprompt
tuning. Aggregation: no app-side sum, no warning — reasoning-first `sub_answers` schema + temp-0 +
grounding is the whole net.

**Plans cross-ref**: `dcaf504` GA-hardening plan covers grounding **silent-degrade** (open);
doc `docs/master/15-O-anti-hallu-tuning.md` is the 9-layer base but header/§3 evidence is partially
Jina-era + predates 0195 grounding-model swap.

---

## (d) HAIKU CONTRADICTION — VERDICT

`claude-haiku` occurrences (full grep, src/ + alembic/):

| Where | file:line | Live? |
|---|---|---|
| `DEFAULT_NARRATE_MODEL="claude-haiku-4-5"` | `constants/_20_cag…py:55` | **DEAD** — constant never imported anywhere (grep = definition only); CAG default OFF (`_20:23`) |
| `DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE="anthropic/claude-haiku-4-5"` | `constants/_20…py:63` | **SHADOWED-DEAD** — live `system_config.slot_extractor_model = "openai/gpt-4.1-mini"` (DB verified; set by alembic 0169 after dead-ANTHROPIC-key incident) |
| `DEFAULT_MULTI_QUERY_MODEL="haiku"` | `constants/_11…py:143` (read at `query_graph.py:2806,4162`) | **SHADOWED-DEAD** — live `system_config.multi_query_model = "gpt-4.1-mini"` (DB verified, 0169) |
| docstring examples | `model_resolver.py:742`, `bootstrap_config.py:92` | comments only |
| `ai_models` row `claude-haiku-4-5-20251001` enabled=t, $0.001/$0.005 per 1k | alembic 0070 seed | **SEMI-LIVE** — 0 primary bindings (live DB), but **2 active generation bindings keep it as `record_fallback_model_id`** → reachable on a CB-OPEN/retryable failover hop (`dynamic_litellm_router.py:463-499`) |

**Verdict: NO live-primary Haiku path; constants are dead/shadowed; ONE semi-live edge = the
2-binding generation failover hop.** No CLAUDE.md contradiction: the Haiku BAN governs the
**Claude Code dev-session tier** ("T-X BANNED … cost_audit.py model-mix"); the **product pipeline**
ban is narrower (memory `feedback_haiku_partial_only`: answer/judge must be gpt-4.1-mini) — and the
answer/judge ARE mini/nano today (live DB §a). Residual risk: the failover hop targets a model whose
provider key was declared DEAD in 0169's rationale, yet `.env` carries a 109-char `ANTHROPIC_API_KEY`
— key liveness **CHƯA verify** (needs 1 curl; if dead, the failover hop is a guaranteed
double-failure latency tax for those 2 bots).

### vs SOTA LLM-ops 2026 — HAS / LACKS

**HAS**: DB-driven purpose×model binding + 1-hop failover + per-provider CB & semaphore ·
temp-0 determinism for transforms with deliberate HyDE exception · LLM-as-judge grounding
(structured-output verdicts, locale-proof) · citation id validation vs retrieved set + post-hoc
attribution · prompt-cache (Anthropic ephemeral / OpenAI auto ≥1024 tok, `complete_runtime`
docstring) · per-call cost ledger with purpose label (`model_invocations`) · numeric-claim
normalization library (math_lockdown) · cost-aware intent→cheap-binding routing · bounded
self-correction loops.
**LACKS** (vs 2026 practice): full-coverage grounding (5-sentence cap = partial verification; SOTA =
claim-decomposition over the whole answer) · fail-closed-or-flagged judge degrade (today: silent
pass) · temp forcing at the ROUTER boundary (today: per-callsite, leaky — Finding A3) · complete
cost attribution for all purposes (Finding A2) · judge-the-judge calibration set (no measured
nano-judge FP/FN rate post-0195) · app-visible "unverified" annotation channel (sacred forbids
override, but a metadata flag to the client is allowed and absent) · canary/A-B harness for model
swaps (0195 says "A/B-gated" but gate is a manual load test).

---

## (e) 10 OPEN QUESTIONS for Phase 2

1. **Temp-0 leak (live)**: grounding/multi_query direct `llm.complete` sites omit `temperature=`
   while live bindings carry 0.30 (DB, 12 grounding bindings tmax 0.30). Force
   `DEFAULT_DETERMINISTIC_TEMPERATURE` at the router boundary (purpose∈set) instead of per-callsite?
2. **Nano-policy drift (measured)**: 755/831 understand_query calls ran on mini via llm_primary
   fallback (only 2 bots seed nano transform bindings). Seed fleet-wide nano bindings, or change
   resolver fallback for transform purposes to the platform-default nano?
3. **Cost ledger hole**: grounding/multi_query/condense/hyde bypass `invocation_logger` →
   `model_invocations` has only 5 purposes. Wrap the direct call sites, or per-tenant cost (charter
   RẺ) stays under-counted — by how much?
4. **Grounding 5-sentence cap**: `max_sentences=5` hardcoded (`local_guardrail.py:413`) — can a
   `sub_answers` multi-fact answer exceed 5 sentences leaving the tail unjudged? Make config-driven +
   measure tail-claim rate.
5. **Judge silent-degrade**: error/timeout ⇒ None ⇒ grounded. After the gemma era (timeout = degrade
   on EVERY multi-fact turn, 0195), should degrade emit a counted `grounding_degraded` flag /
   structured event (GA-hardening plan `dcaf504` silent-degrade item)?
6. **Haiku failover hop**: is the `.env` ANTHROPIC_API_KEY live? If dead, the 2 generation bindings'
   fallback to claude-haiku-4-5 is a guaranteed second failure — repoint fallback to nano or verify
   key.
7. **nano as grounding judge**: any measured FP/FN rate for gpt-4.1-nano SUPPORTED/NOT verdicts
   post-0195? (0195 claims "A/B-gated, load test must hold HALLU=0" — locate that run's artifact, or
   it's CHƯA verify.)
8. **Self-RAG answer-swap compliance**: `critique_parse` replacing the answer with
   `oos_answer_template` on the LLM's own `[Unsupported]` ratio — ruling under sacred #2? And when
   the bot never set the template, the swap yields `""` (empty answer UX). Any bot has it ON in prod?
9. **math_lockdown destiny**: `find_ungrounded_numbers` is built+tested but unwired; `_21:134`
   comment claims a parallel "math" output check that doesn't exist. Wire it as observability-only
   flag (sacred-compatible: no override) for the anti-HALLU 4-loại-số, or delete + fix the comment?
10. **15-O staleness**: doc says reflect default 2 (constant=1, `_15:120`), §3 thresholds calibrated
    Jina-era, header predates 0195 grounding swap. Recalibrate `grounding_check_threshold=0.3` +
    `reranker_min_score_active=0.15` against current ZE + nano-judge distributions?
