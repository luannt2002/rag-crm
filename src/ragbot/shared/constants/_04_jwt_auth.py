from __future__ import annotations
from typing import Final  # noqa: F401
from ._03_language_packs_db_driven_pro import *  # noqa: F401,F403

# --- JWT / Auth -------------------------------------------------------------
DEFAULT_JWT_TTL_S: Final[int] = 86_400  # 24h
DEFAULT_DEV_TOKEN_ENABLED: Final[bool] = False
DEFAULT_DEV_TOKEN_ALLOW_NETWORK: Final[bool] = False
DEFAULT_DEV_TOKEN_SELF_TTL_REDIS_S: Final[int] = 300

# --- Cache TTL (seconds) ----------------------------------------------------
DEFAULT_CONTEXT_TTL: Final[int] = 600
DEFAULT_SEMANTIC_CACHE_TTL: Final[int] = 3600
# Tight enough to avoid wrong-intent hits while catching paraphrases.
DEFAULT_CACHE_SIMILARITY_THRESHOLD: Final[float] = 0.97
# Do NOT cache answers that contain numeric claims (price / percent / duration /
# article numbers). The cosine cache (0.97) can return a near-duplicate query
# that differs only in a number ("Điều 34" vs "Điều 35", "dưới 500K" vs "600K"),
# yielding a stale wrong-number answer — the staleness is dangerous precisely
# where the answer is numeric. Non-numeric answers still cache (hit-rate kept).
# Surgical alternative to disabling whole intents. Config-overridable.
DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC: Final[bool] = True
# Skip semantic cache (lookup + store) for MULTI-TURN turns (conversation
# history present). A follow-up like "nó làm những bước nào" is keyed on the
# raw query text — identical across conversations but meaning a DIFFERENT
# service depending on context. Caching it returns a stale cross-context
# answer (the coreference points elsewhere now). Correctness > hit-rate;
# single-turn queries still cache. Config-overridable.
DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN: Final[bool] = True
# Skip semantic-cache write for refuse answers (poison risk).
_REFUSE_ANSWER_TYPES: Final[frozenset[str]] = frozenset(
    {"out_of_scope", "no_context", "blocked"}
)

# OOS template fallback — empty by design per CLAUDE.md app-mindset rule.
# Bot owner sets per-bot bots.oos_answer_template; never inject app-level text.
DEFAULT_OOS_ANSWER_TEMPLATE: Final[str] = ""

# Context-aware refusal grounding floor — used by the reference sysprompt
# template the bot owner copies into ``bots.system_prompt`` (domain-neutral
# rule guidance). When the top retrieved chunk's score sits at-or-above this
# floor the owner's rule allows the LLM to answer with a grounding qualifier
# instead of the blanket refuse template; below the floor the owner's rule
# directs the LLM to declare uncertainty. The platform DOES NOT compute or
# enforce this threshold at runtime — it ships purely as a rule constant the
# owner's prompt references so the bot owner can keep one tuning knob in
# config alongside the rest of the retrieval thresholds.
DEFAULT_PARTIAL_GROUND_THRESHOLD: Final[float] = 0.20

# Sysprompt template version label — metadata only. The bot owner authors
# ``bots.system_prompt`` directly; this label records which reference
# template the owner used as a starting point so admin tooling can audit
# the rollout. Default ``BASELINE`` preserves prior behaviour. Switching
# the per-bot ``plan_limits.sysprompt_version`` to ``CONTEXT_AWARE`` does
# NOT change LLM input — the owner must also paste the new template into
# ``bots.system_prompt`` (CLAUDE.md: app never injects text into prompts).
SYSPROMPT_VERSION_BASELINE: Final[str] = "v6"
SYSPROMPT_VERSION_CONTEXT_AWARE: Final[str] = "v7"
DEFAULT_SYSPROMPT_VERSION: Final[str] = SYSPROMPT_VERSION_BASELINE
ALLOWED_SYSPROMPT_VERSIONS: Final[tuple[str, ...]] = (
    SYSPROMPT_VERSION_BASELINE,
    SYSPROMPT_VERSION_CONTEXT_AWARE,
)

DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED: Final[bool] = True
DEFAULT_CHITCHAT_QUERY_MAX_TOKENS: Final[int] = 6
# Domain-neutral default — bot owner provides keywords per-bot or via
# system_config key ``hallu_trap_keywords`` (per-language list).
DEFAULT_HALLU_TRAP_KEYWORDS: Final[tuple[str, ...]] = ()
# Document-level ingest batching for the embedding loop. Independent from
# the embedder-internal HTTP batch (DEFAULT_EMBEDDING_MAX_BATCH=64) which
# governs how many texts ride a single provider request. The doc-level
# batch governs how many chunks the orchestrator awaits before logging
# progress + yielding to the event loop. Large docs (e.g. 3851 chunks)
# would otherwise execute one giant await with no visibility while the
# embedder-internal loop iterates dozens of HTTP calls.
DEFAULT_EMBED_DOC_BATCH_SIZE: Final[int] = 100
# Cooperative pause between doc-level batches. Caps sustained QPS against
# the embedding provider so a single document doesn't burn through a
# per-minute quota and stall mid-ingest.
DEFAULT_EMBED_INTER_BATCH_SLEEP_S: Final[float] = 0.5
DEFAULT_EMBED_CACHE_TTL: Final[int] = 2_592_000  # 30 days
# Short-TTL caches for the hot LLM/embed path. ``understand_query`` and the
# narrow embed wrapper share Redis with 1h default TTL so repeat queries
# inside a typical user session reuse upstream work without staleness risk.
# Operator overrides via ``system_config.understand_query.cache_ttl_s`` and
# ``system_config.embed.cache_ttl_s`` — both whitelisted in bootstrap_config.
DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S: Final[int] = 3600
DEFAULT_EMBED_CACHE_TTL_S: Final[int] = 3600
# Bumped whenever ``i18n.py`` understand-query prompt template changes —
# acts as a cache namespace so a prompt revision instantly invalidates
# prior cached classifications without manual Redis flush.
PROMPT_VERSION_UQ: Final[int] = 1
DEFAULT_IDEMPOTENCY_TTL: Final[int] = 86_400  # 24 hours
DEFAULT_MODEL_RESOLVER_CACHE_TTL: Final[int] = 60
# In-process TTL for the resolved per-(bot, language) abbreviation map
# returned by ``ragbot.shared.vi_tokenizer.get_abbreviations``. Short
# enough that bot owners see vocabulary edits quickly; long enough that
# every chat turn does not re-query system_config + bots.
DEFAULT_ABBREVIATIONS_CACHE_TTL_S: Final[int] = 30
DEFAULT_BOT_CONFIG_TTL_S: Final[int] = 3600
DEFAULT_BOT_CACHE_VERSION_HASH_LEN: Final[int] = 12
# Bound on per-language singleton caches in the vocabulary / superlative
# factories. Realistic deployments use 1-3 languages; the cap protects
# against a streamed tag-attack (one instance per distinct tag forever).
DEFAULT_VOCAB_FACTORY_CACHE_SIZE: Final[int] = 64
# SHA-256 hex chunk fingerprint width persisted in document_chunks.content_hash.
# Full sha256 hex is 64 chars; column is sized to match.
DEFAULT_CONTENT_HASH_HEX_LEN: Final[int] = 64

# --- Diff-based re-ingest (chunk-level hash skip) ---------------------------
# T2-CostPerf. When a document is re-ingested the ingest path already SHA-256
# fingerprints every (post-enrichment) chunk; the diff layer surfaces that
# as a feature-flagged module so cost saving is measurable from structlog.
# Default OFF — bot owners flip via
# ``system_config.diff_based_reingest_enabled`` after they ship doc updates
# frequent enough that re-embedding identical chunks would be wasteful.
DEFAULT_DIFF_REINGEST_ENABLED: Final[bool] = False
# Rough OpenAI ``text-embedding-3-small`` list price (USD per 1M tokens) used
# only to estimate the *cost saved* metric on the skip path. Deployments on
# different embedders override via ``system_config.embed_cost_usd_per_1m_tokens``;
# the constant is the fallback so reports never silently drop the cost line.
DEFAULT_EMBED_COST_USD_PER_1M_TOKENS: Final[float] = 0.13
# Standard heuristic for transformer tokenizers on Latin / mixed scripts:
# ~4 characters per token. We do NOT load tiktoken just to estimate a USD
# headline; the resulting number is an order-of-magnitude reporting figure,
# never a billing input.
DEFAULT_CHARS_PER_TOKEN_ESTIMATE: Final[float] = 4.0
# Denominator for the "USD per 1M tokens" unit — the rate is quoted per
# million by every embedding vendor, so the conversion factor is part of
# the unit definition, not a tunable knob.
TOKENS_PER_MILLION: Final[int] = 1_000_000

# --- Corpus version (cache-key discriminator) -------------------------------
# Derived per-bot hash of MAX(documents.updated_at). Bumps automatically when
# bot owner uploads / replaces docs, so semantic_cache rows stale under the
# old corpus naturally fall out of the lookup. Redis-cached for a few minutes
# to keep the per-turn DB hit cheap on a hot bot.
DEFAULT_CORPUS_VERSION_CACHE_TTL_S: Final[int] = 300
# Sentinel returned when the bot has zero documents — keeps the cache key
# stable across "empty corpus" turns instead of churning on NULL.
DEFAULT_CORPUS_VERSION_EMPTY_SENTINEL: Final[str] = "empty"
# Legacy tag carried by historic semantic_cache rows (migration 0014 default).
# We keep it as a constant so any read-side code can still recognise old rows.
LEGACY_CORPUS_VERSION_TAG: Final[str] = "latest"
# Redis key prefix for the per-bot derived corpus_version cache.
CACHE_KEY_CORPUS_VERSION_PREFIX: Final[str] = "ragbot:corpus_version:"

# --- Fusion / scoring -------------------------------------------------------
RRF_K: Final[int] = 60
SEMANTIC_CACHE_THRESHOLD: Final[float] = 0.97
SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED: Final[float] = 0.95
DEFAULT_FRESHNESS_HALF_LIFE_DAYS: Final[int] = 90
DEFAULT_AUTHORITY_SCORE: Final[float] = 0.5

# --- Timeouts (seconds) -----------------------------------------------------
DEFAULT_LLM_TIMEOUT_S: Final[int] = 30
DEFAULT_EMBEDDING_TIMEOUT_S: Final[int] = 90
# Orchestrator-side hard ceiling on ONE doc-level embed batch await. The
# embedder's per-HTTP-call timeout (DEFAULT_EMBEDDING_TIMEOUT_S) bounds a
# single provider call, but a batch await that never returns (provider socket
# hang, infinite internal retry) would stall the worker on a document forever
# with no failed event (2026-06-13: a URL-heavy xe sheet hung at the embed
# step, 0 chunks persisted, doc stuck). This wraps the batch await so a hang
# raises TimeoutError → ExternalServiceError → doc marked failed → recovery
# worker re-queues. Generous (covers a full batch of retried calls) but finite.
DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S: Final[int] = 300
# Hard ceiling on the narrate-then-embed pass (table/formula → prose before
# embedding). Audit 2026-06-13 (CRIT): the ``_narrate_chunks_for_embed`` await
# had no timeout — a stalled per-chunk narrate LLM call hung the worker on the
# document forever, AFTER ``embedding_text_strategy_applied`` had already
# logged (a false "progressing" signal). On timeout the pipeline falls back to
# the raw embed-target text (identity passthrough = narrate_service-off path).
DEFAULT_NARRATE_TIMEOUT_S: Final[int] = 240
DEFAULT_RERANK_TIMEOUT_S: Final[int] = 15
DEFAULT_HTTP_TIMEOUT_S: Final[int] = 60
DEFAULT_STATEMENT_TIMEOUT_MS: Final[int] = 30_000

# --- Retry policy -----------------------------------------------------------
DEFAULT_RETRY_MAX_ATTEMPTS: Final[int] = 3
DEFAULT_RETRY_INITIAL_MS: Final[int] = 500
DEFAULT_RETRY_MAX_MS: Final[int] = 10_000
# Best-effort LLM purposes degrade gracefully when they fail — understand →
# raw query, decompose → no split, rewrite → raw query, multi_query → single
# query, grade → reranker order, reflect → keep answer, hyde → skip. So they
# FAIL FAST (1 attempt, no retry) instead of holding a provider slot for
# attempts × timeout and hammering a struggling upstream (SRE best practice:
# distinguish critical from best-effort calls; retrying a call that degrades
# anyway just amplifies load + head-of-line blocking under provider stress).
# Only the CRITICAL answer call (generation) and the safety check (grounding)
# keep the full retry budget. Purpose names are internal technical identifiers.
DEFAULT_BEST_EFFORT_RETRY_MAX_ATTEMPTS: Final[int] = 1
DEFAULT_BEST_EFFORT_LLM_PURPOSES: Final[frozenset[str]] = frozenset({
    "understand_query", "condensing", "decompose", "rewriting",
    "multi_query", "grading", "reflection", "hyde",
})
# The CRITICAL answer call gets a LARGER budget than the default — it is the one
# call whose failure the user sees as a 503, so it is worth retrying harder
# (with our coordinated backoff+jitter, still a SINGLE layer — the provider-SDK
# inner retry stays off). Measured 2026-07-13: with only 3 attempts the answer
# rate fell ~5pp vs the pre-fix 6 effective attempts (3 ours x 2 inner-SDK);
# 5 recovers most of it without re-introducing the uncoordinated inner storm.
# Bounded overall by the pipeline_timeout wall-clock, so it cannot hang.
DEFAULT_CRITICAL_RETRY_MAX_ATTEMPTS: Final[int] = 5
DEFAULT_CRITICAL_LLM_PURPOSES: Final[frozenset[str]] = frozenset({"generation"})

# --- Embedding batch --------------------------------------------------------
DEFAULT_EMBEDDING_MAX_BATCH: Final[int] = 64

