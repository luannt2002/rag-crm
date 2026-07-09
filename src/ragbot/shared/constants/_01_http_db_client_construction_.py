from __future__ import annotations
from typing import Final  # noqa: F401
from ._00_app_env_taxonomy import *  # noqa: F401,F403

# --- HTTP / DB client construction defaults ---------------------------------
# Per CLAUDE.md zero-hardcode rule: every timeout literal lives here so
# operators can tune without redeploy. Used by embedder/reranker HTTP probes
# (5s default — short because these are smoke-call latency-probes, not
# production retrieval).  The DB bootstrap timeout guards `bootstrap_config`
# against a slow postgres at app start so a bad replica doesn't wedge boot.
DEFAULT_HTTP_CLIENT_PROBE_TIMEOUT_S: Final[float] = 5.0
DEFAULT_DB_BOOTSTRAP_CONNECT_TIMEOUT_S: Final[int] = 3

# Hex length of the API-key fingerprint (sha256 prefix) shown in admin
# list/upsert endpoints and persisted in api_keys.metadata_json['fingerprint']
# so the list path never needs the plaintext key (ADR-W1-KEY).
API_KEY_FINGERPRINT_HEX_LEN: Final[int] = 12

# Max chars of an external-call error body / exception message carried in the
# ``external_call_failed`` observability event (embed / LLM / rerank). Bounded
# so a giant upstream HTML error page or traceback cannot flood the log line,
# yet long enough to carry the provider's actual reason string. Operators tune
# via system_config when a provider's error bodies are routinely longer.
DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS: Final[int] = 300

# --- /health/models thresholds ----------------------------------------------
DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S: Final[int] = 10
# Above this latency the provider is reported "degraded" even on success.
DEFAULT_HEALTH_MODELS_DEGRADED_LATENCY_MS: Final[int] = 2000
DEFAULT_HEALTH_PROBE_QUERY: Final[str] = "health_check_probe"
DEFAULT_HEALTH_PROBE_DOC_A: Final[str] = "alpha"
DEFAULT_HEALTH_PROBE_DOC_B: Final[str] = "beta"
DEFAULT_HEALTH_PROBE_LLM_PROMPT: Final[str] = "OK"
DEFAULT_HEALTH_PROBE_LLM_MAX_TOKENS: Final[int] = 5
MS_PER_SECOND: Final[int] = 1000

# --- Jina retry + CircuitBreaker --------------------------------------------
DEFAULT_JINA_RERANKER_MAX_ATTEMPTS: Final[int] = 2
DEFAULT_JINA_RERANKER_CB_FAIL_MAX: Final[int] = 5
DEFAULT_JINA_RERANKER_CB_RESET_S: Final[int] = 30

# --- ZeroEntropy reranker ---------------------------------------------------
# Hosted multilingual instruction-following reranker. Endpoint, model name
# and latency knob are env-overridable so ops can pin a specific model
# revision or proxy without redeploying.
DEFAULT_ZEROENTROPY_RERANKER_MODEL: Final[str] = "zerank-2"
DEFAULT_ZEROENTROPY_RERANKER_ENDPOINT: Final[str] = (
    "https://api.zeroentropy.dev/v1/models/rerank"
)
# DEPRECATED 2026-05-14 AdapChunk-reorg: ZE reranker timeout hard cap 30s → 5s
# bounds tail latency. Per debug doc Phần 22.4 LF2.
# DEFAULT_ZEROENTROPY_RERANKER_TIMEOUT_S: Final[float] = 30.0
DEFAULT_ZEROENTROPY_RERANKER_TIMEOUT_S: Final[float] = 5.0
# Provider-documented per-request document cap. Mirrors the Cohere/Jina
# style cap so the adapter truncates before hitting upstream 4xx.
DEFAULT_ZEROENTROPY_RERANKER_MAX_DOCS: Final[int] = 64
DEFAULT_ZEROENTROPY_HEALTH_CHECK_TIMEOUT_S: Final[float] = 10.0
DEFAULT_ZEROENTROPY_RERANKER_SCORE_PRECISION: Final[int] = 6
# 3 attempts so a request that hits a 429 can round-robin through all three
# pooled keys (each on an independent BPM account) before degrading to RRF —
# the per-attempt key rotation is what lets a single throttled key self-heal.
DEFAULT_ZEROENTROPY_RERANKER_MAX_ATTEMPTS: Final[int] = 3
# fail_max raised 5→10 once reranker admission control was added (same
# rationale as the embedder: bound in-flight calls first, let the CB measure
# genuine provider health, not self-inflicted burst contention).
DEFAULT_ZEROENTROPY_RERANKER_CB_FAIL_MAX: Final[int] = 10
DEFAULT_ZEROENTROPY_RERANKER_CB_RESET_S: Final[int] = 30
# Bulkhead: separate bounded-concurrency pool for the reranker so a reranker
# burst cannot starve the embedder pool (and vice versa) — each external
# dependency gets its own semaphore (Nygard, Release It! — bulkhead pattern).
DEFAULT_RERANKER_MAX_CONCURRENT: Final[int] = 4
# ZeroEntropy free-tier throughput is tied to the ``latency`` mode:
#   fast = 500 KB/min  · slow = 5 MB/min (10× headroom)
# The "fast" quota trips an HTTP 503 ("Rate limit for `fast` could not be met,
# request `slow` or `None`") under concurrent load. We default to "slow" for
# the 10× ceiling — measured 2026-06-10 (zerank-2, 20 docs): fast=725ms,
# slow=1118ms (only +~400ms; the old ">10s" note was wrong), well inside the
# blocking-path budget. With 3 pooled keys (separate accounts) that is
# 3×5 MB/min = 15 MB/min aggregate → the rate ceiling is effectively
# unreachable for this workload. Empty string OMITS the knob (ZE default mode);
# ops can pin "fast" via env for latency-critical single-tenant runs.
DEFAULT_ZEROENTROPY_RERANKER_LATENCY_MODE: Final[str] = "slow"

# --- Voyage reranker (rerank-2) ---------------------------------------------
# Hosted multilingual cross-encoder reranker. Endpoint, model name and
# timeout are env-overridable so ops can pin a specific model revision or
# proxy without redeploying. Key sourced via the provider-agnostic
# ``ApiKeyPool`` (provider_code = ``"voyage"``).
DEFAULT_VOYAGE_RERANK_MODEL: Final[str] = "rerank-2"
DEFAULT_VOYAGE_RERANK_BASE_URL: Final[str] = "https://api.voyageai.com/v1"
DEFAULT_VOYAGE_RERANK_ENDPOINT: Final[str] = (
    "https://api.voyageai.com/v1/rerank"
)
DEFAULT_VOYAGE_RERANK_TIMEOUT_S: Final[int] = 10
# 0 = use model default; >0 = override truncation dimension when supported.
DEFAULT_VOYAGE_RERANK_DIMENSIONS: Final[int] = 0
DEFAULT_VOYAGE_RERANK_HEALTH_TIMEOUT_S: Final[float] = 5.0
DEFAULT_VOYAGE_RERANK_MAX_DOCS: Final[int] = 1000
DEFAULT_VOYAGE_RERANK_SCORE_PRECISION: Final[int] = 6
DEFAULT_VOYAGE_RERANK_MAX_ATTEMPTS: Final[int] = 2
DEFAULT_VOYAGE_RERANK_CB_FAIL_MAX: Final[int] = 5
DEFAULT_VOYAGE_RERANK_CB_RESET_S: Final[int] = 30

# --- API key pool (provider-agnostic, active-passive) ----------------------
# Cooldown window (seconds) applied to an API key after a 403 (out of
# balance / forbidden) — a real key problem, so park it for a while. When the
# TTL expires the pool naturally retries the key on the next call.
DEFAULT_API_KEY_COOLDOWN_S: Final[int] = 300
# Per-KEY max concurrent in-flight requests (provider enforces this per key, NOT
# per account). Default 2 = Jina free tier. Operators override PER KEY via
# ``PROVIDER_KEY_CONCURRENCY_JSON`` (e.g. {"jina":[2,50]} for a free + paid key)
# so a higher-tier key opens more lanes without a code change. The embedder's
# total in-flight cap = SUM of its pool's per-key values.
DEFAULT_API_KEY_MAX_CONCURRENT: Final[int] = 2
# Env var holding the per-provider, per-key concurrency list (index-aligned with
# PROVIDER_API_KEYS_JSON). Absent → every key uses DEFAULT_API_KEY_MAX_CONCURRENT.
PROVIDER_KEY_CONCURRENCY_ENV: Final[str] = "PROVIDER_KEY_CONCURRENCY_JSON"
# Shorter cooldown for a 429 (transient rate-limit). A per-minute (BPM) quota
# refills within ~60s, so parking the key for the full 300s would needlessly
# drop it out of the round-robin for 5 minutes and overload the remaining
# keys (cascade). One BPM window lets it rejoin the rotation promptly.
DEFAULT_API_KEY_RATELIMIT_COOLDOWN_S: Final[int] = 60
# Redis key prefix for the cooldown ledger; final key adds provider_code +
# purpose + sha256 hash of the credential so the ledger never holds a
# plaintext key and pools for different providers fail independently.
API_KEY_COOLDOWN_REDIS_PREFIX: Final[str] = "ragbot:api_key_cooldown:"
# Master switch — kept for parity with other features even though the pool
# falls through to its primary entry whenever a secondary is not configured.
API_KEY_FAILOVER_ENABLED: Final[bool] = True

DEFAULT_RERANKER_ENABLED: Final[bool] = True

# --- Reranker minimum-score gate (mode-aware) -------------------------------
# ACTIVE = cross-encoder 0..1 scale; BYPASS = RRF scores too small to threshold.
# Empirical: ZE rerank scores 0.3+ correlate with relevant retrievals; 0.05-0.15
# is too permissive and lets noise through, contributing to HALLU risk when the
# generate node receives weakly-grounded chunks. The post-rerank refuse gate
# (top_score < threshold → refuse via bots.oos_answer_template) uses this value
# unless the bot's plan_limits override (reranker_min_score_active) raises it.
DEFAULT_RERANKER_MIN_SCORE_ACTIVE: Final[float] = 0.30
DEFAULT_RERANKER_MIN_SCORE_BYPASS: Final[float] = 0.0
# Legacy single-key default (back-compat only).
DEFAULT_RERANKER_MIN_SCORE: Final[float] = 0.01

# Adaptive cliff-detect filter knobs. Default strategy is "cliff" because
# its force_min_keep=True guarantees at least one chunk reaches grade when
# the input was non-empty — the refuse short-circuit at the generate node
# no longer fires for every threshold-cut request. "threshold" remains
# selectable per-bot for tenants that prefer a hard cut.
DEFAULT_RERANK_FILTER_STRATEGY: Final[str] = "cliff"  # "threshold" | "cliff"
DEFAULT_RERANK_CLIFF_GAP_RATIO: Final[float] = 0.35
# Absolute floor for the adaptive cliff cut in _cliff_detect_filter: the
# "negative-relevance noise" gate below which a reranked chunk is dropped
# regardless of the gap-ratio cut (DEFAULT_RERANK_CLIFF_GAP_RATIO). The value
# is reranker-distribution dependent — the current zerank cross-encoder scores
# legitimate-but-weak chunks higher than the prior embedding-reranker did, so
# the operating floor is 0.2 (a chunk below it is noise, not a weak match).
# This constant is only the fallback default; the effective value is resolved
# from system_config.rerank_cliff_absolute_floor (seeded to 0.2 — clone parity
# with production) and overridable per-bot via plan_limits. Keep this in step
# with the seed so a fresh clone-without-data-dump behaves like production.
DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR: Final[float] = 0.2
# Default 3 (not 1): a single reranker mis-score must not collapse the kept set
# to one chunk. Step-level forensic (2026-06-05, a legal-clause lookup) showed the
# semantic reranker can under-rank an exact-answer legal/clause chunk that
# lexical (BM25) ranks #1; with min_keep=1 the cliff then drops it, so the LLM
# never sees the answer. Keeping >=3 makes the DEFAULT robust for every bot
# (incl. newly-created ones); bots needing more override UP via
# plan_limits.rerank_cliff_min_keep (expert tuning only adds, never rescues).
DEFAULT_RERANK_CLIFF_MIN_KEEP: Final[int] = 3
# Retrieval safety-net: after rerank+cliff, force-include the top-N chunks by
# the pre-rerank retrieval (RRF/BM25/vector) order so a strongly-retrieved
# exact-answer chunk cannot be silently dropped by a single reranker mis-score
# (forensic 2026-06-05: zerank-2 buried a legal clause that BM25 ranked #1 to
# rerank rank-8, beyond top_n+cliff). Bounded (+N chunks max, only when the
# reranker disagrees with retrieval) → smart DEFAULT for every bot. 0 = off.
DEFAULT_RERANK_RETRIEVAL_SAFETY_N: Final[int] = 2
# Adaptive context-sizing: when retrieval is CLEARLY strong (top graded score
# >= high_score), pass only the top-N chunks to generate. Fewer chunks = less
# summarisation pressure = less drop-fact on multi-part answers (forensic
# 2026-06-06: drop-fact rose 33->48 as better retrieval enlarged context).
# Default OFF — must be A/B-measured before becoming default (rule #0); the
# high-score gate + keeping safety-injected chunks guard against turning a
# strong retrieval into an answer gap.
DEFAULT_ADAPTIVE_CONTEXT_ENABLED: Final[bool] = False
DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE: Final[float] = 0.85
DEFAULT_ADAPTIVE_CONTEXT_MAX_N: Final[int] = 3
# Intents that need WIDE context (every row / both sides) must NOT be pruned —
# A/B 2026-06-08: pruning aggregation dropped combo-price rows (correct
# -16pp). Synthesis/multi_hop benefit from focus (+4..+6pp); aggregation/
# comparison do not.
DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS: Final[tuple[str, ...]] = ("aggregation", "comparison")
# When ``rerank_filter_strategy = "cliff"``, the cliff filter already cuts weak
# chunks via ``absolute_floor`` + ``gap_ratio`` and the ``force_min_keep=True``
# safety net guarantees at least one chunk survives. Running the static
# ``_rerank_threshold_gate`` on top double-gates and produces false-positive
# refuses at top_score 0.29-0.43 (Wave J2 load-test 15Q: 27% refused, 3/4 cliff
# strategy active). Default OFF — gate skipped under cliff strategy. Owners
# who want the legacy hard-cut behaviour back (e.g. audit-heavy compliance
# bots that prefer "refuse over weak-answer") flip this True per-bot via
# ``plan_limits.rerank_threshold_gate_after_cliff_enabled``.
DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED: Final[bool] = False

# --- Intent taxonomy — single source of truth (V4 Open-Closed lift) --------
# Adding a conversational/synthesis intent must only edit constants here +
# UnderstandOutput.intent Literal. Pipeline must never inline-tuple-compare.
INTENT_FACTOID: Final[str] = "factoid"
INTENT_COMPARISON: Final[str] = "comparison"
INTENT_AGGREGATION: Final[str] = "aggregation"
INTENT_MULTI_HOP: Final[str] = "multi_hop"
INTENT_OUT_OF_SCOPE: Final[str] = "out_of_scope"

# Intents whose answers need EVERY entity/clause chunk (multi-fact): the rerank
# cliff's gap-cut keeps only the top score-cluster and drops answer chunks
# (measured: a legal-corpus multi_hop → only 1 chunk survived). For these intents the
# filter keeps the full reranked set (min_keep = n_in) instead of cutting;
# factoid/others still cliff normally.
DEFAULT_RERANK_CLIFF_SKIP_INTENTS: Final[frozenset[str]] = frozenset(
    {INTENT_AGGREGATION, INTENT_COMPARISON, INTENT_MULTI_HOP}
)
# Hard cap on chunk COUNT fed to the LLM after score-filtering. The cliff/
# threshold filters cut by score but can still pass many near-duplicate chunks
# (e.g. fragmented price rows) — this bounds the prompt size. Multi-fact intents
# (DEFAULT_RERANK_CLIFF_SKIP_INTENTS) are exempt: they need every clause chunk.
# ``0`` disables the cap.
DEFAULT_RERANK_MAX_CHUNKS_TO_LLM: Final[int] = 5
INTENT_GREETING: Final[str] = "greeting"
# ``INTENT_CHITCHAT_LABEL`` is the single classifier-output label for
# conversational / acknowledgement turns.  ``INTENT_CHITCHAT`` (frozenset)
# is the broader bucket used for per-flow logic (skipping MQ, etc.).
# Heuristic Layer-1 emits this label directly so downstream pipeline nodes
# that compare against the constant work without an inline literal.
INTENT_CHITCHAT_LABEL: Final[str] = "chitchat"
# Bucket label (NOT a classifier output) — used as a dict key alias for
# synthesis-flavor rewrite templates (HyDE answer-template hypothesis).
# Real classifier values that fall into the synthesis bucket are listed
# in ``INTENT_SYNTHESIS`` frozenset below.
INTENT_SYNTHESIS_LABEL: Final[str] = "synthesis"
DEFAULT_INTENT_FALLBACK: Final[str] = INTENT_FACTOID
INTENT_CHITCHAT: Final[frozenset[str]] = frozenset(
    {"greeting", "feedback", "chitchat", "vu_vo"}
)
INTENT_SYNTHESIS: Final[frozenset[str]] = frozenset({"multi_hop", "aggregation"})
INTENT_RETRIEVAL_BEARING: Final[frozenset[str]] = frozenset(
    {"factoid", "comparison", "aggregation", "multi_hop"}
)
DEFAULT_SKIP_REWRITE_INTENTS: Final[tuple[str, ...]] = (
    "factoid",
    "greeting",
    "out_of_scope",
)
# V6 expand: reflect adds ~1s per turn; only synthesis intents truly need it.
# Conversational + simple-factoid + OOS skip safely (sysprompt v6/v7 govern).
DEFAULT_SKIP_REFLECT_INTENTS: Final[tuple[str, ...]] = (
    "factoid",
    "greeting",
    "feedback",
    "chitchat",
    "vu_vo",
    "out_of_scope",
)
# Master gate: 2026-05-18 audit (req 9cf611b5) showed reflect firing 2×
# per turn (3.57s wasted) even though zero bots set
# ``plan_limits.reflection_enabled = True``. Mirror the
# ``shared/bot_limits.PLAN_LIMIT_SCHEMA`` default (False) here so
# ``_output_blocked`` can short-circuit without a separate DB read.
DEFAULT_REFLECTION_ENABLED: Final[bool] = False

# --- Per-bot rerank intent whitelist ----------------------------------------
# Bot owner can OFF when system_prompt already governs context-handling
# semantics (avoid double-instructing the LLM via attribute hints).
DEFAULT_GENERATE_CONTEXT_TRUST_HINT_ENABLED: Final[bool] = True
# Wrapper tag names (envelope only — bot owner's system_prompt defines behavior).
DEFAULT_GENERATE_DOCS_TAG: Final[str] = "documents"
DEFAULT_GENERATE_QUESTION_TAG: Final[str] = "question"
# F5 dual-read close: when ON, the ingest-time VERBATIM original of a chunk
# (exact table grid / formula LaTeX, stored read-only in chunk metadata) is
# surfaced inside its context fence so the LLM sees exact numbers at answer
# time. This is ingest data placed read-only in the data envelope — NOT an
# app instruction and NOT an answer override (sacred#10). Default OFF: a
# no-op when no verbatim is present, and A/B-gated before any default flip
# (rule #0). Envelope tag is data-only; bot owner's system_prompt governs use.
DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED: Final[bool] = False
DEFAULT_GENERATE_VERBATIM_TAG: Final[str] = "verbatim"

# PostgreSQL wire-protocol ceiling: a single prepared statement accepts at
# most 32767 (int16) bind parameters. A multi-row chunk INSERT binds ~11-12
# params/row, so a >~2900-chunk document would overflow one VALUES(...) batch
# and abort the whole ingest. Bulk INSERT helpers derive a per-batch row cap
# from this so large documents split into multiple round trips instead.
POSTGRES_MAX_BIND_PARAMS: Final[int] = 32767

DEFAULT_RERANK_INTENT_WHITELIST_ENABLED: Final[bool] = True
# Owner-override opaque strings; superset of classifier outputs (booking/yesno
# emerge only when bot owner injects them via pipeline_config).
DEFAULT_RERANK_WHITELIST_INTENTS: Final[tuple[str, ...]] = (
    "factoid",
    "comparison",
    "aggregation",
    "booking",
    "yesno",
)

