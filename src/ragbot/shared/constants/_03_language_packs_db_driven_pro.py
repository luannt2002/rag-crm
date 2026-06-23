from __future__ import annotations
from typing import Final  # noqa: F401
from ._02_per_intent_rerank_skip_gate_ import *  # noqa: F401,F403

# --- Language packs (DB-driven prompts, migration 0055/0056) ----------------
# Redis key prefix for cached language pack content (TTL = service cache TTL).
LANGUAGE_PACK_CACHE_PREFIX: Final[str] = "ragbot:lpack:"
# Canonical prompt-key registry. Adding a new prompt = (1) extend this tuple,
# (2) seed rows in language_packs, (3) reference key in orchestration. NO
# new prompt strings inline in code.
LANGUAGE_PACK_PROMPT_KEYS: Final[tuple[str, ...]] = (
    "generator",
    "grader",
    "understand",
    "condense",
    "rewriter",
    "reflector",
    "decompose",
    "greeting_answer",
    # Multi-query rewrite prompts (Multi-HyDE per-intent dispatch).
    # Seeded by alembic 0099 in the ``language_packs`` table. The runtime
    # caller resolves the template via LanguagePackService.get(lang, key).
    "multi_query_factoid_prompt",
    "multi_query_multi_hop_prompt",
    "multi_query_comparison_prompt",
    "multi_query_aggregation_prompt",
    # OOS / refuse fallback — tier 6 of OosTemplateResolver chain. Seeded by
    # alembic 0136. Empty default in i18n fallback pack means resolver returns
    # "" if neither owner column nor system_config carries a value.
    "refuse_message",
    # Platform-default sysprompt rules — appended to bot.system_prompt by
    # SysPromptAssembler service. Per-locale text seeded by alembic 0146.
    # Bot owners opt-out specific rules via
    # bots.plan_limits["sysprompt_rules_disabled"] = ["rule_17", ...].
    "sysprompt_default_rules",
)

# Intent → language_pack prompt_key for the multi-query rewriter.
# Synthesis intent reuses the aggregation template (HyDE hypothesis flavour
# — same semantics, different label upstream). Adding a new intent template
# = (1) new prompt_key row in language_packs, (2) new entry here.
MULTI_QUERY_INTENT_PROMPT_KEYS: Final[dict[str, str]] = {
    "factoid":     "multi_query_factoid_prompt",
    "multi_hop":   "multi_query_multi_hop_prompt",
    "comparison":  "multi_query_comparison_prompt",
    "aggregation": "multi_query_aggregation_prompt",
    "synthesis":   "multi_query_aggregation_prompt",
}
DEFAULT_MULTI_QUERY_PROMPT_KEY: Final[str] = "multi_query_factoid_prompt"

# --- Chat / History ----------------------------------------------------------
DEFAULT_MAX_HISTORY: Final[int] = 10
MAX_HISTORY_LIMIT_REQUEST: Final[int] = 20
# Per-message char cap when injecting history — bounds prompt growth.
MAX_HISTORY_MESSAGE_CHARS: Final[int] = 800
DEFAULT_GENERATE_HISTORY_MAX_MSGS: Final[int] = 10
# Per-chunk CRAG grading concurrency cap — guards provider 429.
DEFAULT_CRAG_GRADE_CONCURRENCY: Final[int] = 5
# Retrieve multi-query fan-out concurrency cap — each branch opens its own DB
# session from the pool, so a deep variant/sub-query fan-out can otherwise
# exhaust the connection pool under concurrent turns. Bound at roughly a
# quarter of the pool (DEFAULT_DB_POOL_SIZE // 4) so several turns can fan out
# at once without starving the pool. Per-bot override via pipeline_config.
DEFAULT_RETRIEVE_FANOUT_CONCURRENCY: Final[int] = 5
# Entity-fairness round-robin on the multi-query RRF merge (comparison /
# multi_hop). Default OFF so the merge is byte-identical to plain RRF until a
# bot owner opts in via pipeline_config. The quota is the minimum slots each
# distinct entity (doc-id) is guaranteed before the global RRF fill phase.
DEFAULT_ENTITY_FAIRNESS_ENABLED: Final[bool] = False
DEFAULT_ENTITY_FAIRNESS_PER_ENTITY_QUOTA: Final[int] = 2

# --- CRAG grader strategy (Port + Strategy + Registry) ----------------------
# Default provider for the CragGraderPort registry. ``"per_chunk"`` matches
# the legacy N-call grading behaviour so flipping the new abstraction layer
# on existing deployments introduces zero behaviour change until an operator
# updates ``system_config.crag_grader_provider`` to ``"batch"`` (cost ~10x
# cheaper for top_k=50) or ``"null"`` (emergency disable).
DEFAULT_CRAG_GRADER_PROVIDER: Final[str] = "per_chunk"
# Ceiling on chunks per batched LLM grade call. Above this, BatchCragGrader
# slices the input into bounded sequential windows so a rogue ``top_k=500``
# request cannot exceed the LLM's context budget. Override via
# ``system_config.crag_batch_grader_max_chunks``.
DEFAULT_CRAG_BATCH_GRADER_MAX_CHUNKS: Final[int] = 50
DEFAULT_BOT_ID: Final[str] = "1774946011723"
DEFAULT_CONNECT_ID: Final[str] = "test-user"

# --- Content Limits ----------------------------------------------------------
MAX_CONTENT_LENGTH: Final[int] = 10_000
MAX_DOWNLOAD_BYTES: Final[int] = 10_000_000
MAX_DOCUMENT_CONTENT_CHARS: Final[int] = 500_000

# --- Conversation ------------------------------------------------------------
ROLLING_SUMMARY_THRESHOLD: Final[int] = 20
# Tail window kept verbatim when history is compressed into a rolling summary.
# Boot fallback only — runtime reads rolling_summary_keep_last from system_config.
ROLLING_SUMMARY_KEEP_LAST: Final[int] = 6

# --- Rate Limiting -----------------------------------------------------------
FALLBACK_RATE_LIMIT_VALUE: Final[int] = 120
FALLBACK_RATE_LIMIT_WINDOW: Final[int] = 60

# --- Per-tenant rate limit + token cap --------------------------------------
DEFAULT_TENANT_RATE_LIMIT_PER_MIN: Final[int] = 600
DEFAULT_TENANT_RATE_LIMIT_WINDOW_S: Final[int] = 60
DEFAULT_TENANT_TOKEN_CAP_WARN_PERCENT: Final[int] = 80
DEFAULT_TENANT_TOKEN_CAP_BLOCK_PERCENT: Final[int] = 100
DEFAULT_TENANT_BYPASS_RATE_LIMIT: Final[bool] = False
DEFAULT_TENANT_CONFIG_TTL_S: Final[int] = 60
# OFF = soft warn only; True = block at LLM router boundary.
DEFAULT_TENANT_TOKEN_CAP_ENFORCE_PREFLIGHT: Final[bool] = False

# --- Sliding-window per-token rate limit (Layer 2) -------------------
# Per-endpoint policy table maps URL prefix → (limit, window_s, burst_factor).
# Caller key = JWT token jti / sub composite (NEVER tenant_id alone — that
# is Layer 1's job in tenant_rate_limiter). limits == 0 means soft-unlimited.
DEFAULT_RL_CHAT_PER_MIN: Final[int] = 60
# Per-4-key bot rate limit (2026-05-16 multi-tenant fairness).
# Key derivation: ``rl:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}``
# — scopes the cap so tenant A's "support" bot on "web" channel cannot
# starve tenant B's bot in the same workspace. The endpoint
# ``/api/ragbot/admin/rate-limits/inspect`` lets partners read both the
# policy + their current consumption (transparent SaaS contract).
DEFAULT_RL_BOT_PER_MIN: Final[int] = 120
DEFAULT_RL_UPLOAD_PER_MIN: Final[int] = 30  # bot-scoped doc ingest cap
# Per-tenant daily document quota (alembic 010i — 2026-05-18 vấn đề 6C
# multi-tenant fairness). 0 = unlimited (operator override for premium
# tenants seeded in quotas.documents_per_day_limit). 1000 default chosen
# to cover early-adopter B2B partners (~1 doc/min for 16h) without
# letting a runaway batch flood document_chunks and degrade global HNSW
# index quality.
DEFAULT_DOCUMENTS_PER_DAY_LIMIT: Final[int] = 1000
DEFAULT_RL_ADMIN_PER_MIN: Final[int] = 30
DEFAULT_RL_SYNC_PER_MIN: Final[int] = 30
DEFAULT_RL_DEFAULT_PER_MIN: Final[int] = 60
# Burst factor: first ``DEFAULT_RL_BURST_WINDOW_S`` seconds of the window
# may consume ``floor(limit * burst_factor)`` requests. After that the
# steady-state limit applies. Reset on window roll.
DEFAULT_RL_BURST_FACTOR: Final[float] = 2.0
DEFAULT_RL_BURST_WINDOW_S: Final[int] = 10
DEFAULT_RL_WINDOW_S: Final[int] = 60
# 'closed' = 503 on Redis outage (defence in depth, per-token layer);
# 'open' = pass-through (Layer 1 tenant_rate_limiter precedent).
DEFAULT_RL_FAIL_MODE: Final[str] = "closed"
# Whether to attach X-RateLimit-* headers on success responses (W3C draft).
DEFAULT_RL_EMIT_HEADERS: Final[bool] = True

# Per-source-tag rate limit case study (2026-05-18).
# Scoped to (record_tenant_id, source_tag) so KMS-A flooding inside one
# tenant cannot starve KMS-B in the same tenant. The per-token + per-IP
# + 4-key bot RL layers stay in front; this layer only caps source-
# tagged ingest traffic on the unified documents ingest path.
#
# The prefix is built at middleware install time as
# ``api_base_path + SOURCE_RL_INGEST_PATH_SUFFIX``; the suffix lives in
# this module so the URL routing stays free of an explicit version
# segment (no-version-ref rule, CLAUDE.md).
DEFAULT_SOURCE_RL_PER_MIN: Final[int] = 100
DEFAULT_SOURCE_RL_WINDOW_S: Final[int] = 60
SOURCE_RL_INGEST_PATH_SUFFIX: Final[str] = "/documents"
SOURCE_RL_TAG_MAX_LEN: Final[int] = 64

# --- Per-tenant CORS strict whitelist --------------------------------
# Browser-cached preflight TTL — 600s = 10min default per Mozilla guidance.
DEFAULT_CORS_PREFLIGHT_MAX_AGE_S: Final[int] = 600
# Methods + headers allowed across ALL tenant origins (uniform contract).
DEFAULT_CORS_ALLOW_METHODS: Final[tuple[str, ...]] = (
    "GET", "POST", "PATCH", "DELETE", "OPTIONS",
)
DEFAULT_CORS_ALLOW_HEADERS: Final[tuple[str, ...]] = (
    "Authorization", "Content-Type", "X-Trace-Id",
)
# Per-tenant cors_origins JSONB column default (empty = block all browser).
DEFAULT_TENANT_ALLOWED_ORIGINS: Final[tuple[str, ...]] = ()

# --- Audit -------------------------------------------------------------------
AUDIT_MAX_TEMP_TABLES: Final[int] = 2

