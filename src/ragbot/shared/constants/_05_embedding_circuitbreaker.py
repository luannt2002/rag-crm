from __future__ import annotations
from typing import Final  # noqa: F401
from ._04_jwt_auth import *  # noqa: F401,F403

# --- Embedding CircuitBreaker -----------------------------------------------
# fail_max raised 5→10 once admission control (DEFAULT_EMBEDDER_MAX_CONCURRENT)
# was added: the CB exists to protect against a genuinely-down provider, but a
# self-inflicted request burst was tripping it at 5. With the semaphore capping
# in-flight calls, the old threshold is over-sensitive to transient blips —
# 10 consecutive failures now signals a real outage, not burst contention.
DEFAULT_EMBEDDER_CB_FAIL_MAX: Final[int] = 10
DEFAULT_EMBEDDER_CB_RESET_S: Final[int] = 60

# Bounded concurrency for embedder HTTP calls (Async Rule 6). Without this,
# a burst of concurrent chat requests (e.g. parallel load test, no cache)
# fires N simultaneous embed calls; the provider rate-limits, each call
# exhausts its retry budget, and N failures trip the shared circuit breaker
# (fail_max) → 503 for the whole burst. The LLM router already caps per-provider
# concurrency; the embedder adapter did not. Caps in-flight embed calls so the
# CB measures genuine provider health, not self-inflicted burst saturation.
DEFAULT_EMBEDDER_MAX_CONCURRENT: Final[int] = 4

# --- Generic CircuitBreaker policy defaults ---------------------------------
# Used by application/services/retry_policy.CircuitBreakerPolicy when caller
# supplies no explicit override.
DEFAULT_CB_POLICY_FAIL_MAX: Final[int] = 5
DEFAULT_CB_POLICY_RESET_TIMEOUT_S: Final[int] = 30

# --- AI spec DTO defaults (LLMSpec / RerankerSpec / EmbeddingSpec) ---------
DEFAULT_SPEC_LLM_TEMPERATURE: Final[float] = 0.0
DEFAULT_SPEC_LLM_MAX_TOKENS: Final[int] = 1000
DEFAULT_SPEC_LLM_TOP_P: Final[float] = 1.0
DEFAULT_SPEC_RERANK_TOP_N: Final[int] = 5
DEFAULT_SPEC_RERANK_BATCH_SIZE: Final[int] = 50
DEFAULT_SPEC_EMBEDDING_MAX_BATCH: Final[int] = 64

# --- ModelResolverService L1/L2 cache (in-process LRU + Redis) -------------
DEFAULT_MODEL_RESOLVER_L1_TTL_S: Final[int] = 60
DEFAULT_MODEL_RESOLVER_L2_TTL_S: Final[int] = 600
DEFAULT_MODEL_RESOLVER_L1_MAX_SIZE: Final[int] = 512

# --- Dynamic LiteLLM Router refresh -----------------------------------------
DEFAULT_DYNAMIC_ROUTER_REFRESH_INTERVAL_S: Final[int] = 60

# --- Settings.py fallback defaults (SSoT for pydantic AppSettings) ----------
# All of these were inline magic numbers in ``config/settings.py`` until
# the 2026-05-16 zero-hardcode audit lifted them here. The .env override
# pattern is preserved: pydantic ``Field(default=DEFAULT_X)`` still reads
# ``ENV_VAR_NAME`` first and falls back to the constant when missing.
#
# Database connection pool (settings.py DatabaseSettings).
DEFAULT_DB_POOL_SIZE: Final[int] = 20
DEFAULT_DB_MAX_OVERFLOW: Final[int] = 10
DEFAULT_DB_POOL_RECYCLE_S: Final[int] = 1800
DEFAULT_DB_POOL_TIMEOUT_S: Final[int] = 30
# Redis connection pool (settings.py RedisSettings).
DEFAULT_REDIS_POOL_SIZE: Final[int] = 50
# Embedding model spec (settings.py EmbeddingSettings — DB system_config is
# still the runtime source of truth; these are boot-time fallbacks used before
# system_config loads). MUST match the live document_chunks.embedding column
# dimension — a cold-start (fresh DB / Redis miss) embeds with these, so a stale
# dim writes wrong-width vectors into the column → pgvector dimension error.
# Pinned to Jina v3 (1024) to match alembic 0228; change both together on the
# next embedding migration.
DEFAULT_EMBEDDING_FALLBACK_MODEL: Final[str] = "jina-embeddings-v3"
DEFAULT_EMBEDDING_FALLBACK_DIMENSION: Final[int] = 1024
DEFAULT_EMBEDDING_FALLBACK_VERSION: Final[str] = "v1"
# Adaptive chunking thresholds (settings.py ChunkingSettings).
DEFAULT_CHUNKING_HEADING_THRESHOLD: Final[int] = 5
DEFAULT_CHUNKING_AVG_LEN_SHORT: Final[int] = 30
DEFAULT_CHUNKING_TABLE_THRESHOLD: Final[int] = 2
DEFAULT_CHUNKING_AVG_LEN_LONG: Final[int] = 200
DEFAULT_CHUNKING_HEADING_MAX_FOR_SEMANTIC: Final[int] = 3
DEFAULT_CHUNKING_MIXED_CONTENT_THRESHOLD: Final[float] = 0.3
# Enrichment LLM settings (settings.py EnrichmentSettings).
DEFAULT_ENRICHMENT_MODEL: Final[str] = "gpt-4.1-mini"
DEFAULT_ENRICHMENT_TEMPERATURE: Final[float] = 0.0
DEFAULT_ENRICHMENT_MAX_TOKENS: Final[int] = 100
DEFAULT_ENRICHMENT_TIMEOUT_S: Final[int] = 10
# Note: DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS / _CHUNK_PREVIEW_CHARS /
# _MAX_PREFIX_CHARS already exist further below — settings.py mirrors
# those names so the smaller 2_000 / 500 / 500 values in the pydantic
# field defaults stay in sync with the constants block (line ~1937).
# JWT (settings.py JwtSettings — strings, no magic but lifted for SSoT).
DEFAULT_JWT_ALGORITHM: Final[str] = "RS256"
DEFAULT_JWT_ISSUER: Final[str] = "ragbot"
DEFAULT_JWT_AUDIENCE: Final[str] = "ragbot-clients"
# RAG pipeline defaults (settings.py RagSettings).
DEFAULT_RAG_TOP_K: Final[int] = 50
DEFAULT_RAG_RERANK_TOP_N: Final[int] = 5
DEFAULT_SEMANTIC_CACHE_THRESHOLD: Final[float] = 0.97
# When True the cache emits a structlog ``semantic_cache_hit`` event with the
# observed similarity score + active threshold for every hit. Used by the
# WA-7 diagnostic harness (``scripts/diagnose_p95_bottleneck.py --cache-stats``)
# to measure baseline hit rate per-bot before A/B testing a lower threshold.
# Cheap (one info-level structlog per hit) — safe to leave on; flip False to
# silence in test environments that already aggregate cache events elsewhere.
DEFAULT_SEMANTIC_CACHE_HIT_LOG_ENABLED: Final[bool] = True
DEFAULT_MAX_ITERATION_CAP: Final[int] = 3
DEFAULT_DEBOUNCE_WINDOW_MS: Final[int] = 800
DEFAULT_CIRCUIT_BREAKER_FAIL_MAX: Final[int] = 5
DEFAULT_CIRCUIT_BREAKER_RESET_TIMEOUT_S: Final[int] = 30
# App bind defaults (settings.py AppSettings).
DEFAULT_APP_PORT: Final[int] = 8000

# --- Redis client connection ------------------------------------------------
DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_S: Final[int] = 30
# Short-op client (cache get/set, rate-limit INCR, semantic cache lookup).
# Sub-second budget — operator alarms if cache hot-path takes > 2s.
DEFAULT_REDIS_SOCKET_TIMEOUT_S: Final[float] = 2.0
DEFAULT_REDIS_SOCKET_CONNECT_TIMEOUT_S: Final[float] = 1.0
# Long-op client (Redis Streams XREADGROUP block, XCLAIM recovery).
# XREADGROUP `block` is REDIS_XREAD_BLOCK_MS=5_000 below — the socket
# timeout MUST be larger than block + headroom or every blocking read
# raises TimeoutError before the server responds. 30s gives the
# subscribe loop 25s headroom on top of the 5s block window and covers
# health-check pings (health_check_interval=30s) without false positives.
DEFAULT_REDIS_STREAMS_SOCKET_TIMEOUT_S: Final[float] = 30.0
DEFAULT_REDIS_STREAMS_SOCKET_CONNECT_TIMEOUT_S: Final[float] = 5.0
DEFAULT_REDIS_STREAMS_MAX_CONNECTIONS: Final[int] = 20

# --- HTTP client timeout for OCR / parser fetches ---------------------------
DEFAULT_PARSER_HTTP_TIMEOUT_S: Final[float] = 60.0

# --- Parsed Markdown dump (debug aid 2026-05-18) ----------------------------
# After Action 1 upload writes ``documents.raw_content``, the API ALSO
# writes a sibling ``.md`` file to ``{PARSED_MD_DIR}/{tenant}/{doc_id}.md``
# so operators can open the parsed Markdown in any text editor (VSCode etc)
# to debug chunking issues (table-with-footer, heading structure, line breaks).
# The DB column ``raw_content`` stays the source-of-truth — this file is a
# convenience artefact, NOT a primary store. Lost file = next upload regenerates.
#
# Operator override via env ``RAGBOT_PARSED_MD_DIR`` (empty disables dump).
DEFAULT_PARSED_MD_DIR: Final[str] = "var/parsed_md"
DEFAULT_PARSED_MD_SUFFIX: Final[str] = ".md"

# Retention window for parsed-MD dump files. After this many days the file
# is eligible for cleanup by ``scripts/cleanup_parsed_md_dumps.py``. The DB
# row stays — only the on-disk convenience artefact is deleted. Lost file =
# next re-upload regenerates. 30 days matches typical operator debug cadence
# for chunking issues (table-with-footer, heading structure).
DEFAULT_PARSED_MD_RETENTION_DAYS: Final[int] = 30

# --- Embedding evaluation ---------------------------------------------------
DEFAULT_EVAL_TOP_K: Final[int] = 10
DEFAULT_EVAL_RELEVANCE_THRESHOLD: Final[float] = 0.6

# --- Retrieval hit@k / nDCG eval framework ----------------------------------
# Depths reported by `scripts/eval_retrieval_hit_at_k.py` (T2-Eval).
# Order matters for report rendering — keep ascending so markdown columns stay
# left-to-right shallow→deep. Tuple is immutable so callers can't mutate the
# shared default in place.
DEFAULT_HIT_AT_K_DEPTHS: Final[tuple[int, ...]] = (1, 3, 5, 10)
DEFAULT_NDCG_AT_K_DEPTHS: Final[tuple[int, ...]] = (5, 10)
# Maximum retrieval depth fetched per query — must cover the deepest k so
# hit@k and nDCG@k are computable without re-running retrieval per depth.
DEFAULT_EVAL_RETRIEVAL_TOP_K: Final[int] = 10

