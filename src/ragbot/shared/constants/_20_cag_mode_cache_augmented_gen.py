from __future__ import annotations
from typing import Final  # noqa: F401
from ._19_sprint3_ekimetrics_selector_ import *  # noqa: F401,F403

# --- CAG Mode (Cache-Augmented Generation) -----------
# Owner-opt-in alternative path to RAG retrieval. When the bot owner flips
# ``cag_mode_enabled`` AND the bot's total corpus fits below
# ``cag_max_corpus_tokens``, the platform skips the retrieve/rerank stages
# and injects the WHOLE corpus into the LLM prompt as a single cached
# system block (Anthropic ``cache_control: ephemeral``). Subsequent turns
# read the corpus from prompt cache, so per-question retrieval latency
# collapses to ~0 and the LLM sees the entire knowledge base each time.
# Citation: Chan et al. 2024 — "Don't Do RAG: When Cache-Augmented
# Generation is All You Need for Knowledge Tasks"
# (https://arxiv.org/abs/2412.15605, ACM Web 2025 peer-reviewed).
# Reported 10.9-40.5x latency reduction vs RAG on small KBs.
# Gating rules (HALLU=0 sacred — never extrapolate beyond corpus):
#   1. ``cag_mode_enabled`` (system_config OR per-bot plan_limits) MUST be True.
#   2. The bot's measured corpus token count MUST be <= ``cag_max_corpus_tokens``.
#   3. If either gate fails, the platform falls back to standard RAG retrieval.
#   4. The Null adapter (default) ALWAYS returns ``should_engage=False`` so
#      the hot path is identical to today until the operator opts in.
DEFAULT_CAG_MODE_ENABLED: Final[bool] = False

# Provider key for the CAG strategy registry. ``"null"`` is the default OFF
# Null Object; ``"anthropic"`` is the prompt-cache adapter that emits the
# ``cache_control: ephemeral`` breakpoint on the corpus block.
DEFAULT_CAG_PROVIDER: Final[str] = "null"

# --- CleanBase Tier-0 sanitize (ingest pre-chunk pipeline) ------------------
# Defensive scrub layer applied AFTER raw parse, BEFORE chunking + embedding.
# Chains HTML strip + NFC normalize + zero-width char remove + prompt-inject
# blacklist regex (PROMPT_INJECTION_PATTERNS). Default ON — Tier-0 is the
# T1-Safety baseline corpus safety net. Per-call enablement is resolved by
# the registry from ``system_config``; constants expose the platform default
# for the bootstrap layer.
# Proof: CleanBase arxiv 2605.00460 §3 (sanitization pipeline); Trojan
# Source arxiv 2111.00169 §4 (zero-width injection vector); Greshake et al.
# arxiv 2302.12173 (indirect prompt-injection lexicon).
DEFAULT_CLEANBASE_TIER0_ENABLED: Final[bool] = True

# DocumentProfileAnalyzer registry key — picks the analyzer implementation
# at DI-container time. ``"rule_based"`` = no-LLM heuristic refine (this
# stream). ``"null"`` = legacy dict-only path (behaviour).
DEFAULT_DOC_PROFILE_ANALYZER_PROVIDER: Final[str] = "rule_based"

# Narrations are 1-2 sentences — ~120 tokens is plenty of headroom and
# caps cost in the worst case. Uncapped output would blow the batch budget.
DEFAULT_NARRATE_MAX_TOKENS: Final[int] = 120

# gpt-4.1-mini is the cost-optimal narration model: small, fast, and
# auto-cached by OpenAI. Operators can override per system_config when a
# different small model is preferred (e.g. gpt-4.1-nano for max savings).
DEFAULT_NARRATE_MODEL: Final[str] = "gpt-4.1-mini"

DEFAULT_NARRATE_PROVIDER: Final[str] = "llm"

# Slot-extractor (conversational-action) fallback wires when the per-bot
# system_config binding is missing. Token-small JSON extraction — gpt-4.1-mini
# tier. LiteLLM wire names carry the provider prefix; the bare provider code
# feeds the router.
DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE: Final[str] = "openai/gpt-4.1-mini"
DEFAULT_SLOT_EXTRACTOR_SONNET_WIRE: Final[str] = "openai/gpt-4.1-mini"
DEFAULT_SLOT_EXTRACTOR_PROVIDER: Final[str] = "openai"
DEFAULT_SLOT_EXTRACTOR_MAX_TOKENS: Final[int] = 400

# Low temperature: the narration must stay strictly grounded in the
# input. Higher values invent facts (HALLU risk); 0.0 collapses to a
# verbatim echo of the input on some models.
DEFAULT_NARRATE_TEMPERATURE: Final[float] = 0.2

# Narrate-then-Embed (TABLE / FORMULA / IMAGE).
# Inspired by Anthropic Contextual Retrieval (Sep 2024,
# https://www.anthropic.com/news/contextual-retrieval — reports -49%
# retrieval failure rate when chunks are augmented with LLM-generated
# context before embedding) and RAG-Anything HKUDS table-linearisation
# pattern. Default OFF — the value now matches the docstring (was True, a
# contradiction). narrate is per-table-block nano (the spreadsheet storm); the
# deterministic csv_chunker key:value rendering + Jina late_chunking cover table
# retrievability with 0 LLM. Operator flips system_config to opt in. (2026-06-17)
DEFAULT_NARRATE_THEN_EMBED_ENABLED: Final[bool] = False

# --- Proposition LLM Atomic Decomposition (Chen et al. 2024) ---------------
# Dense X Retrieval (Chen, Kong, Lan, Zhu, Yu — EMNLP 2024,
# https://arxiv.org/abs/2312.06648) — use an LLM to rewrite a paragraph
# into atomic, self-contained "propositions": each one a single factual
# claim with pronouns / coreferents replaced by their full entity names so
# the proposition reads correctly in isolation. The paper reports +55%
# relative EM over Contriever on factoid QA when retrieval embeds
# propositions instead of paragraph or sentence chunks.
# Default OFF (Null Object) — the platform exposes Port + Registry but
# never enables decomposition automatically. Operators flip
# ``system_config.proposition_llm_decomp_enabled`` (tenant-wide) AND
# ``system_config.proposition_use_llm`` (force LLM path inside
# ``_chunk_proposition``) to opt in; otherwise the chunker keeps using
# the rule-based clause splitter.
DEFAULT_PROPOSITION_LLM_DECOMP_ENABLED: Final[bool] = False

# Output ceiling — propositions are short factual sentences; capping
# output bounds latency + cost on pathological inputs (one big run-on
# paragraph). Sized so a ~500-word block decomposes within budget.
DEFAULT_PROPOSITION_LLM_MAX_TOKENS: Final[int] = 800

# Proposition decomposition is a utility task (not user-facing answer); the
# platform default uses gpt-4.1-mini (the only catalog LLM tier besides nano).
DEFAULT_PROPOSITION_LLM_MODEL: Final[str] = "gpt-4.1-mini"

DEFAULT_PROPOSITION_LLM_PROVIDER: Final[str] = "null"

# Near-zero temperature: the task is deterministic re-writing
# (decontextualisation), not creative generation. Any randomness risks
# inventing facts that aren't in the source → HALLU.
DEFAULT_PROPOSITION_LLM_TEMPERATURE: Final[float] = 0.0

DEFAULT_PROPOSITION_USE_LLM: Final[bool] = False

DEFAULT_PROXIMITY_CACHE_TTL_S: Final[int] = 3600

# Default OFF: NullQueryRouter always returns ``semantic`` so the pipeline
# preserves byte-identical behaviour until operator flips this knob in
# ``system_config.query_router_provider``.
DEFAULT_QUERY_ROUTER_PROVIDER: Final[str] = "null"

# Speculative retrieve: fire embed(raw_query) + hybrid_search in parallel with
# understand_query+rewrite. When the rewritten query is close enough to raw
# (cosine_similarity >= threshold), keep speculative chunks and skip the second
# retrieve. Default OFF — opt-in via system_config flag; bot owners can also
# override per-bot through pipeline_config.speculative_retrieve_enabled.
DEFAULT_SPECULATIVE_RETRIEVE_ENABLED: Final[bool] = False

# Cap on the speculative task wait time; prevents deadlock if embed/search
# hangs. Falls back to normal retrieve flow on timeout.
DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S: Final[float] = 30.0

# Pipeline speculative MULTI-QUERY expansion — fan out the LLM
# paraphrase call IN PARALLEL with the understand-query router so the
# downstream retrieve stage already has variants ready when the router
# resolves to a multi-hop / synthesis intent. Default OFF (cost vs perf
# trade-off: the speculative LLM call burns tokens on every turn whose
# router lands on a non-fanout intent). Per-bot opt-in via
# ``pipeline_config.pipeline_multi_query_speculative_enabled`` for
# tenants whose traffic skews to multi-hop questions. Cancellation is
# cooperative — the task is awaited with ``suppress(CancelledError)``
# so the no-fanout intent path leaves no orphan task.
DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED: Final[bool] = False

# Cap on the speculative MQ task wall-clock; LLM tail latency must NOT
# stall the chat-graph beyond the cache+understand layer. On timeout
# the speculative result is dropped and the downstream retrieve falls
# back to its inline MQ path.
DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S: Final[float] = 6.0

# Cosine-similarity floor above which raw embedding is "close enough" to the
# rewritten embedding to reuse the speculative chunks. Tunable per-bot via
# pipeline_config.speculative_similarity_threshold. Stricter (>0.9) = fewer
# hits + safer; looser (<0.8) = more hits + risk of using stale chunks.
DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD: Final[float] = 0.85

# --- Speculative Streaming Phase 2 (Wave K1) — Draft model race -------------
# Kick off a cheap draft LLM in parallel with the main LLM; whichever finishes
# first streams to the client, the loser is cancelled. Paper claim TTFB p50
# 1.5s → 350ms.
# Default OFF until Phase 3 verifier (Wave K2) lands. Per-bot opt-in via
# ``plan_limits.speculative_streaming_enabled``. HALLU=0 sacred PRESERVED only
# when ``speculative_hallu_verify_enabled=True`` (Phase 3 verifier gates draft
# tokens against main first chunk via 3-gate verifier below).
DEFAULT_SPECULATIVE_STREAMING_ENABLED: Final[bool] = False

# Cap on the draft model's wall-clock; if the draft hasn't returned its
# first token within this budget the SpeculativeRouter cancels it and
# lets the main LLM run alone. Prevents a stalled draft from holding the
# turn open past the main LLM's normal latency budget.
DEFAULT_DRAFT_MODEL_TIMEOUT_S: Final[float] = 5.0

# --- Speculative Streaming Phase 3 — HALLU verifier (Wave K2) ---------------
# When the draft model wins the race and starts streaming optimistically, the
# verifier buffers the first N tokens, then compares against the main model's
# first chunk. Three gates fire in order; ANY failure → emit ``redo`` SSE,
# drop draft buffer, switch to main stream. Defaults tuned conservatively so
# HALLU=0 sacred stays preserved — verifier prefers ``redo`` over false-safe.
#
# 1) Shingle substring overlap floor — fraction of draft word-shingles that
#    must also appear in the main first chunk. Lower = more lenient.
DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD: Final[float] = 0.80
# 2) Sentence-embedding cosine floor — semantic similarity between draft text
#    and main first chunk; below floor → topic divergence → abort.
DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD: Final[float] = 0.70
# 3) Draft token buffer size — number of streamed tokens accumulated before
#    main first chunk arrives. ``len(buffer) < THIS`` → wait (verifier defers).
DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS: Final[int] = 50
# Shingle width (in words) for substring-overlap hashing. Mirrors
# ``DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE`` (24) by default but kept distinct
# so verifier shingle tuning does not move the leak-detection signal.
DEFAULT_HALLU_VERIFIER_SHINGLE_SIZE: Final[int] = 6

# --- Token quota monetization (per-bot monthly budget) ----------------------
# BOOT FALLBACK only — runtime reads system_config.max_tokens_total (DB SSoT,
# Redis 30s TTL cache). Admin API PATCH /admin/system-config can mutate at
# runtime without redeploy. DO NOT hard-code this value into hot path.
DEFAULT_MAX_TOKENS_TOTAL: Final[int] = 10_000

# Per-response output token cap (max tokens LLM generates per chat call).
# BOOT FALLBACK — runtime reads system_config.output_tokens_per_response_default.
DEFAULT_OUTPUT_TOKENS_PER_RESPONSE: Final[int] = 1_000

# Throttle window (seconds) for chat-channel quota-exhausted notify.
# Avoid spam when many bots exhaust simultaneously.
DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S: Final[int] = 3600

# Default timezone for monthly reset cron.
DEFAULT_TOKEN_QUOTA_RESET_TIMEZONE: Final[str] = "Asia/Ho_Chi_Minh"

# Chat completion hook concurrency / timeout — defensive against
# OOM + runaway hooks. See plans/260514-output-token-quota-monetization/.
DEFAULT_CHAT_HOOK_MAX_CONCURRENCY: Final[int] = 5
DEFAULT_CHAT_HOOK_TIMEOUT_S: Final[float] = 30.0

# Reconciliation cron interval — re-sync Redis L1 with DB SSoT.
# Drift safety net: any Redis-side miss recovered within this window.
DEFAULT_TOKEN_USAGE_RECONCILE_INTERVAL_S: Final[int] = 300  # 5 min

# Document recovery worker (Phase 2 case study — 2026-05-18).
# Sweep cadence — every 5 min is plenty given the stuck-threshold is 15 min.
# Operators that want tighter SLA flip via env override.
DEFAULT_RECOVERY_INTERVAL_S: Final[int] = 300
# A doc is "stuck" when state=DRAFT for longer than this. Phase 1 outbox
# XADD verify rules out most silent fails; recovery catches the rare
# worker-crash-after-XACK-before-state-update window. 15 min covers a
# full ingest cold start (parse + chunk + embed + DB) with headroom.
DEFAULT_RECOVERY_STUCK_THRESHOLD_S: Final[int] = 900
# Per-sweep cap — bound DB query + replay fan-out so a runaway recovery
# cannot self-DoS the worker pool. 100 docs/5min = 20 docs/min replay,
# well below DEFAULT_RL_UPLOAD_PER_MIN=30 ingest tier cap.
DEFAULT_RECOVERY_BATCH_SIZE: Final[int] = 100
# Replay-suppression cooldown. The anti-dup join hides a doc that already has a
# recent replay outbox row so a sweep does not re-emit while the worker is still
# processing. WITHOUT a time bound this becomes PERMANENT: once a replay is
# marked 'processed' (on publish-to-stream) the doc is hidden forever — so a
# replay whose downstream ingest then FAILED leaves the doc stuck in DRAFT with
# no further sweep (observed: xe-3). Bounding the suppression to this cooldown
# turns permanent-stuck into retry-with-backoff: after the window a still-stuck
# doc is swept again. 1 h >> a full ingest (parse+chunk+embed), so a healthy
# in-flight ingest is never double-replayed.
DEFAULT_RECOVERY_REPLAY_COOLDOWN_S: Final[int] = 3600

# ── Cascade Routing thresholds (T1-Smartness + T2-CostPerf) ─────────────
# Adaptive Router downstream of the query_complexity classifier. When a
# bot opts in via plan_limits.cascade_routing_enabled, the answer-LLM
# model is selected by the complexity score so cheap turns ride the
# low-tier model and complex turns ride the high-tier model. Mid-band
# (T_LOW ≤ score < T_HIGH) keeps the bot's current default. Threshold
# values are conservative: low band only triggers on clearly simple
# queries; high band only triggers on clearly multi-entity queries.
# Both thresholds resolve via system_config so bot owners can tune
# without a redeploy.
#
# Recalibration evidence (50-turn pilot on representative test tenant): the 0.3 floor
# was structurally unreachable — every probe scored ≥ 0.55 after the
# length-normaliser term, so the cheap tier never fired (0/25 Haiku
# hits). The new floor at 0.6 captures the greeting + simple-lookup
# band (Q1 / Q9 / Q21–Q25 trap turns clustered ~0.55–0.6 post-clamp);
# the new ceiling at 0.9 keeps the premium tier reachable but tightens
# it from the prior 14/25 hit rate down to clearly multi-entity /
# hypothetical queries only. Bands are bounded by the resolver's
# [0.0, 1.0] clamp so t_high MUST stay ≤ 1.0 or the premium tier
# collapses to dead code.
DEFAULT_CASCADE_T_LOW: Final[float] = 0.6
DEFAULT_CASCADE_T_HIGH: Final[float] = 0.9
# Default OFF — flipping cascade ON without per-bot validation would
# silently change the answer-LLM choice across the platform. Bot owners
# opt-in via plan_limits.cascade_routing_enabled after rehearsing the
# cost/quality trade-off on their corpus.
DEFAULT_CASCADE_ROUTING_ENABLED: Final[bool] = False

# --- Self-RAG critique tokens (Asai 2023) ----------------------------------
# Post-processor parses ``[Supported]`` / ``[Unsupported]`` markers emitted
# by the LLM at the end of each factual claim and computes the unsupported
# ratio.  When the ratio meets/exceeds the threshold the orchestrator
# substitutes the bot's ``oos_answer_template`` for the answer (Quality
# Gate #10 — refusal text comes from the per-bot DB column, never an
# i18n constant).  Operator opt-in: the rule lives in ``bots.system_prompt``
# (see ``docs/sysprompt/self_rag_critique_template.md``) and the parser
# is gated by ``plan_limits.self_rag_critique_enabled``.  Default OFF
# preserves byte-identical behaviour for bots that never opt in.
DEFAULT_SELF_RAG_ENABLED: Final[bool] = False
# 30 % unsupported ⇒ refuse.  Conservative middle-ground per the paper's
# ablation table; bot owners tune via ``plan_limits.self_rag_critique_threshold``
# (lower = stricter; higher = looser).
DEFAULT_SELF_RAG_THRESHOLD: Final[float] = 0.3


