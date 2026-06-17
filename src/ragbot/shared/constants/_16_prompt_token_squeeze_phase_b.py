from __future__ import annotations
from typing import Final  # noqa: F401
from ._15_m2_neighbor_window_expansion import *  # noqa: F401,F403

# --- Prompt-token squeeze (Phase B B2) — feature flag default OFF -----------
# Reduces LLM input tokens at the prompt-build boundary via min-score
# chunk filter + character-n-gram dedupe + intent-aware history skip.
# Default disabled; enable per-bot via pipeline_config or globally via
# system_config.prompt_token_opt_enabled.
DEFAULT_PROMPT_TOKEN_OPT_ENABLED: Final[bool] = False
# Drop chunks with grader score below this threshold. 0.0 = disabled
# (no chunks dropped). Typical ship value: 0.10–0.20 (already filtered
# upstream by CRAG absolute_floor, this is a finer cap at prompt layer).
DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE: Final[float] = 0.0
# Jaccard similarity over character 3-grams. Pairs >= threshold are
# treated as duplicates (first kept). 0.85 = aggressive (catches
# paraphrases); 0.95 = conservative (only exact-shape duplicates).
DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD: Final[float] = 0.85
# Factoid answers are self-contained; skipping conversation history
# for intent="factoid" saves ~500–1500 input tokens per turn.
DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY: Final[bool] = True

# Hard cap on assembled chunk-context — Chroma 2025 "context cliff" guard.
# V6 tighten 5000 → 2900: Chroma study finds prompts > 2900 tokens degrade
# generation accuracy; tighter cap also trims p95 LLM call time.
# Truncation drops chunks tail-first (lowest-rank) until under cap.
DEFAULT_GENERATE_CONTEXT_CHARS_CAP: Final[int] = 2900

# Per-intent overrides for chunk-context cap (260521-CHUNK-AGGREGATION-
# UNIVERSAL Phase 3). Aggregation queries need a wider window so the LLM
# sees every matching row when counting distinct entries. comparison /
# multi_hop also benefit from a moderate bump (multi-entity reasoning).
# out_of_scope / greeting / chitchat keep a smaller window since no
# retrieval payload is needed. Set value to None / dict for opt-out.
#
# Resolution order: ``plan_limits.generate_context_chars_cap_by_intent``
# (per-bot JSONB) > ``system_config.generate_context_chars_cap_by_intent``
# > this constant. Unknown intent falls back to
# ``DEFAULT_GENERATE_CONTEXT_CHARS_CAP``.
DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT: Final[dict[str, int]] = {
    "factoid": 2900,
    "comparison": 4200,
    "multi_hop": 4200,
    "aggregation": 5500,
    "out_of_scope": 1500,
    "greeting": 1500,
    "feedback": 1500,
    "chitchat": 1500,
    "vu_vo": 1500,
}

# Per-intent overrides for rerank top_n (260521-CHUNK-AGGREGATION-
# UNIVERSAL Phase 3). Default ``DEFAULT_RERANK_TOP_N=7`` is too tight
# for aggregation queries — UI evidence 2026-05-21 turn "1tr499 có mấy
# dịch vụ" showed only 1 of 4 ground-truth chunks reaching the LLM
# after rerank cap collapsed 20 candidates → 10 (then MMR 7, then
# prompt_build dropped 3 more → 4 final). Aggregation needs wider
# rerank so all matching rows survive the funnel.
#
# Resolution order: ``plan_limits.rerank_top_n_by_intent`` >
# ``system_config.rerank_top_n_by_intent`` > this constant. Unknown
# intent falls back to ``DEFAULT_RERANK_TOP_N``.
DEFAULT_RERANK_TOP_N_BY_INTENT: Final[dict[str, int]] = {
    "factoid": 7,
    "comparison": 12,
    "multi_hop": 12,
    "aggregation": 20,
    "out_of_scope": 5,
    "greeting": 5,
    "feedback": 5,
    "chitchat": 5,
    "vu_vo": 5,
}

# Per-intent override for the retrieve top_k cap applied at the RRF-fuse
# and lexical-fuse slicing points (query_graph retrieve node). Lightweight
# intents (greeting / chitchat / vu_vo / feedback / out_of_scope) need
# fewer candidates; heavy aggregation queries need more raw material so
# the rerank + MMR funnel has enough rows to find every matching chunk.
#
# Resolution order: ``pipeline_config.retrieve_top_k_by_intent`` (from
# ``system_config`` or operator override) > this constant. Unknown intent
# falls back to ``DEFAULT_TOP_K``.
DEFAULT_RETRIEVE_TOP_K_BY_INTENT: Final[dict[str, int]] = {
    "greeting": 5,
    "chitchat": 5,
    "vu_vo": 5,
    "feedback": 5,
    "out_of_scope": 5,
    "factoid": 15,
    "comparison": 25,
    "multi_hop": 30,
    "aggregation": 40,
}

DEFAULT_REFLECT_ANSWER_PREVIEW_CHARS: Final[int] = 500
# Reflect node grounding-context: top-N graded chunks × per-chunk char cap fed
# into the platform-internal reflector evaluation message.
DEFAULT_REFLECT_CONTEXT_CHUNK_CAP: Final[int] = 6
DEFAULT_REFLECT_CONTEXT_CHUNK_CHARS: Final[int] = 600
DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS: Final[int] = 500
DEFAULT_QUERY_RECEIVED_AUDIT_PREVIEW_CHARS: Final[int] = 500

# Per-intent skip flags for ``rewrite`` and ``multi_query`` LLM calls.
#
# Lightweight intents (greeting / chitchat / factoid / feedback / vu_vo /
# out_of_scope) never benefit from query rewriting or paraphrase fanout —
# they either answer from the raw query or refuse. Skipping 2 LLM calls
# on those intents saves ~3.5s of wall time on the critical path (1.2s
# rewrite + 2.3s multi-query) without any T1 quality regression (verified
# on V15/V16 HALLU=0 load tests where rewrite was already bypassed for
# greeting/OOS via ``skip_rewrite_intents``).
#
# Resolution order: ``plan_limits.rewrite_enabled_by_intent`` >
# ``system_config.rewrite_enabled_by_intent`` > this constant. Unknown
# intent falls back to ``True`` (safe default: run the LLM call).
DEFAULT_REWRITE_ENABLED_BY_INTENT: Final[dict[str, bool]] = {
    "greeting": False,
    "chitchat": False,
    "factoid": False,
    "feedback": False,
    "vu_vo": False,
    "out_of_scope": False,
    "aggregation": True,
    "comparison": True,
    "multi_hop": True,
}

# Per-intent skip flag for ``multi_query`` paraphrase fanout.
#
# Same intent grouping as ``DEFAULT_REWRITE_ENABLED_BY_INTENT``. Greeting /
# chitchat / factoid / feedback / vu_vo / out_of_scope queries retrieve
# fine with the original query; adding 3 paraphrases only burns tokens.
# Aggregation / comparison / multi_hop benefit from paraphrase diversity
# because their ground-truth answer may span multiple distinct chunks.
#
# Resolution order: ``plan_limits.multi_query_enabled_by_intent`` >
# ``system_config.multi_query_enabled_by_intent`` > this constant. Unknown
# intent falls back to ``True`` (safe default: allow fanout).
DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT: Final[dict[str, bool]] = {
    "greeting": False,
    "chitchat": False,
    "factoid": False,
    "feedback": False,
    "vu_vo": False,
    "out_of_scope": False,
    "aggregation": True,
    "comparison": True,
    "multi_hop": True,
}


# --- Load-test transport thresholds (offline harness only) ------------------
DEFAULT_LOADTEST_REQUEST_TIMEOUT_S: Final[float] = 90.0
DEFAULT_LOADTEST_RATE_LIMIT_RETRY_SLEEP_S: Final[float] = 60.0
DEFAULT_LOADTEST_MAX_TOKEN_REFRESH_RETRIES: Final[int] = 2
DEFAULT_LOADTEST_INTER_ROOM_SLEEP_S: Final[float] = 2.0
DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S: Final[float] = 0.0
# Below this answer length classify as FAIL (over-terse / stub answer).
DEFAULT_LOADTEST_MIN_PASS_ANSWER_CHARS: Final[int] = 30
DEFAULT_LOADTEST_ANSWER_TRUNCATE_CHARS: Final[int] = 2000
# Informativeness gate: answers longer than this skip refuse-pattern flip.
DEFAULT_LOADTEST_FACTUAL_LEN_THRESHOLD: Final[int] = 350

DEFAULT_PROVIDER_MAX_RETRIES: Final[int] = 2

# Cohere/Jina embedding output dimension (vs OpenAI 1536).
DEFAULT_RERANKER_EMBEDDING_DIM: Final[int] = 1024

# Upfront token estimate for budget guard pre-flight.
DEFAULT_UPFRONT_TOKEN_ESTIMATE: Final[int] = 5000

DEFAULT_SERVICE_CACHE_TTL_S: Final[int] = 300

# TTL jitter spread for cache entries written by SystemConfigService
# (and other cache writers). ``0.1`` = ±10% — caches set during the same
# burst expire at different times, avoiding thundering-herd at the
# upstream DB. Applied multiplicatively: actual_ttl = ttl + uniform(-r*ttl, +r*ttl).
DEFAULT_TTL_JITTER_RATIO: Final[float] = 0.1

# KG triple-extraction caps — SUBJ/OBJ entities, REL predicate.
DEFAULT_KG_TRIPLE_SUBJ_MAX_CHARS: Final[int] = 500
DEFAULT_KG_TRIPLE_OBJ_MAX_CHARS: Final[int] = 500
DEFAULT_KG_TRIPLE_REL_MAX_CHARS: Final[int] = 200
DEFAULT_KG_PREVIEW_CHARS: Final[int] = 3000

# Above this single-chunk char count, ingest is almost certainly a parser failure.
DEFAULT_INGEST_VALIDATOR_MAX_CHUNK_CHARS: Final[int] = 5000

# --- Per-tenant model tier resolver -----------------------------------------
# Cost-aware quality tiers exposed by ``model_resolver._quality_tier_from_model``
# (cheap < $1/1k out · mid $1–$10/1k out · premium ≥ $10/1k out). The
# ``TenantModelTierPort`` returns a subset of this set per tenant; bot config
# layer later filters bindings against that subset. Keeping the canonical
# tier vocabulary here avoids string-literal drift across the resolver and
# its strategies.
DEFAULT_MODEL_TIERS: Final[frozenset[str]] = frozenset({"cheap", "mid", "premium"})
DEFAULT_TENANT_MODEL_TIER_PROVIDER: Final[str] = "null"

DEFAULT_GROUNDING_CONTEXT_PREVIEW_CHARS: Final[int] = 500


# --- Per-tenant cost cap alerter --------------------------------------------
# Aggregation window for per-tenant token usage vs ``tenants.quota_monthly_tokens``.
# Mirrors a billing cycle of ~30 days; operators may pass `--since-days` to override.
DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS: Final[int] = 30
# Below this ratio of usage / quota → silent. At/above → ``cost_cap_warning``.
DEFAULT_COST_CAP_WARN_RATIO: Final[float] = 0.8
# At/above this ratio → ``cost_cap_exceeded`` (supersedes warn).
DEFAULT_COST_CAP_EXCEED_RATIO: Final[float] = 1.0
# How often the embedded cost-cap alerter sweeps tenants (D11 — closes
# P2-J "alerter correct but only an offline script calls it; no scheduler").
# Hourly is fine: token caps move slowly and the warn ratio gives headroom.
DEFAULT_COST_CAP_ALERT_INTERVAL_S: Final[int] = 3600


# === Wave J consolidated constants restore — post-K1 + cleanup-wave drift ===
# Comprehensive restore of constants imported by src/ragbot/ modules but
# dropped during the post-reorg ``-X theirs`` cleanup-wave merges. Each block
# is restored verbatim from the upstream branch that originally introduced
# the constant, with version-ref tokens scrubbed per CLAUDE.md no-version-ref
# rule. All values are SSoT defaults; bot owners override via system_config /
# pipeline_config.

