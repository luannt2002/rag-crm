from __future__ import annotations
from typing import Final  # noqa: F401
from ._06_llm_defaults import *  # noqa: F401,F403

# --- LLM sampling defaults --------------------------------------------------
DEFAULT_TEMPERATURE: Final[float] = 0.3

# --- SSE streaming ----------------------------------------------------------
DEFAULT_STREAMING_USE_REAL_LLM: Final[bool] = True
DEFAULT_STREAMING_WORD_DELAY_MS: Final[int] = 30
DEFAULT_CHAT_STREAM_TIMEOUT_S: Final[int] = 60
# 0 disables — keeps idle proxies from terminating the connection.
DEFAULT_SSE_HEARTBEAT_MS: Final[int] = 15_000
# Backpressure cap on producer→consumer queue.
DEFAULT_SSE_SINK_MAXSIZE: Final[int] = 64
# Hard upper bound on a single producer put — guards silently disconnected SSE.
DEFAULT_SSE_PRODUCER_TIMEOUT_S: Final[float] = 30.0
DEFAULT_TOP_P: Final[float] = 0.4
DEFAULT_FREQUENCY_PENALTY: Final[float] = 0.0
DEFAULT_PRESENCE_PENALTY: Final[float] = 0.0

# --- Field length limits ----------------------------------------------------
MAX_BOT_ID_LENGTH: Final[int] = 64
MAX_CHANNEL_TYPE_LENGTH: Final[int] = 32
MAX_BOT_NAME_LENGTH: Final[int] = 255
MAX_TITLE_LENGTH: Final[int] = 512
# Optional connect_id slug supplied by test/harness callers to isolate
# chat_histories per room. 128 chars accommodates uuid4 (36) plus a
# room-name prefix; longer is rejected at the API boundary to keep DB
# index pages tight.
MAX_CONNECT_ID_LENGTH: Final[int] = 128

# Strict regex for the external 2-key bot identity. Mirrors the
# WORKSPACE_ID_PATTERN family: ASCII letters + digits + hyphen + underscore
# only. Prevents slug injection into Redis keys, SQL WHERE clauses, log
# labels, and URL paths that thread bot_id / channel_type through the
# 4-key resolve boundary.
BOT_ID_PATTERN: Final[str] = r"^[a-zA-Z0-9_-]+$"
CHANNEL_TYPE_PATTERN: Final[str] = r"^[a-zA-Z0-9_-]+$"

# JWT clock skew tolerance — accepts tokens issued up to N seconds in the
# future / past relative to local clock. Without it, NTP drift between
# the gateway and the auth service rejects legitimate tokens during clock
# correction events.
DEFAULT_JWT_CLOCK_SKEW_S: Final[int] = 30

# --- RAGAS metric thresholds (scripts/eval_ragas_metrics.py) ----------------
# Default minimum scores used by the RAGAS adapter to PASS/FAIL evaluation
# gate. Tuned for Vietnamese Q&A corpus baseline; per-bot override via
# plan_limits.threshold_overrides for stricter SLAs.
DEFAULT_RAGAS_MIN_FAITHFULNESS: Final[float] = 0.8
DEFAULT_RAGAS_MIN_ANSWER_RELEVANCY: Final[float] = 0.7
DEFAULT_RAGAS_MIN_CONTEXT_PRECISION: Final[float] = 0.7
DEFAULT_RAGAS_MIN_CONTEXT_RECALL: Final[float] = 0.7

# --- RAGAS CI gate thresholds (scripts/eval_ragas.py) -----------------------
# Tighter PR-gate thresholds used by the live-pipeline eval script that
# replays a golden dataset against a running ragbot via /api/ragbot/test/chat.
# Faithfulness gate (anti-fabrication) is the strictest because HALLU=0 is
# sacred (CLAUDE.md Quality Gate #10). Relevancy gate ensures the bot
# actually answers the question rather than dodging with off-topic text.
DEFAULT_RAGAS_FAITHFULNESS_GATE: Final[float] = 0.85
DEFAULT_RAGAS_RELEVANCY_GATE: Final[float] = 0.80

# --- Step latency analysis (scripts/analyze_step_latency.py) ---------------
# Default time window for latency aggregation queries. 24h is the standard
# operator dashboard window; the max cap keeps a single-query memory bounded
# (request_steps grows ~50k rows/day, so 168h ≈ 350k rows in-mem).
DEFAULT_STEP_LATENCY_WINDOW_HOURS: Final[int] = 24
MAX_STEP_LATENCY_WINDOW_HOURS: Final[int] = 168

# --- Outbox poll / publish defaults -----------------------------------------
# Outbox poll/publish batch size. Match Postgres default cursor batch so a
# single query round-trip returns one page without server-side spool.
DEFAULT_OUTBOX_POLL_LIMIT: Final[int] = 100
DEFAULT_OUTBOX_PUBLISH_BATCH_SIZE: Final[int] = 100

# --- Audit list pagination ---------------------------------------------------
# Default page size on admin audit_log/list endpoints. Higher than the
# chat-pagination cap because audit consumers are operator dashboards
# with longer page render budgets.
DEFAULT_AUDIT_LIST_LIMIT: Final[int] = 100

# --- LiteLLM direct adapter (multi-agent review) -----------------------------
# Per-call timeout for the LiteLLM direct adapter used by the multi-agent
# review pipeline (NOT the main LLM router). Generous because each agent
# is permitted a longer turn than user-facing chat.
DEFAULT_LITELLM_DIRECT_ADAPTER_TIMEOUT_S: Final[float] = 60.0

# --- Per-bot rerank config resolver ------------------------------------------
DEFAULT_RERANK_CONFIG_TTL_S: Final[int] = 60

# --- HTTP short-timeout (HEAD probes / link validation) ---------------------
DEFAULT_HTTP_SHORT_TIMEOUT_S: Final[int] = 15

# --- Chunking strategy ------------------------------------------------------
DEFAULT_PROPOSITION_THRESHOLD_WORDS: Final[int] = 300

# --- Redis Streams ----------------------------------------------------------
REDIS_XREAD_COUNT: Final[int] = 10
REDIS_XREAD_BLOCK_MS: Final[int] = 5_000
# Per-subscribe-loop handler concurrency (2026-05-18 worker concurrency upgrade).
# Document ingest is I/O-bound (HTTP fetch → embed API → DB INSERT — ~70% time
# is network wait). One subscribe loop processes up to N messages in parallel
# via ``asyncio.Semaphore(N)`` so a single worker process can drive 5x throughput
# without the operator spawning extra systemd instances. RAM headroom is small
# (~20-30 MB per concurrent task — coroutine frame + httpx pool slots) so the
# default fits a single host comfortably. Operators that want strict
# sequential processing (e.g. for ordering guarantees per partition) set the
# value to ``1`` via env override.
DEFAULT_BUS_HANDLER_CONCURRENCY: Final[int] = 5
# Per-tenant inner concurrency cap (ADR-W2-D8 ingest fairness). The global
# ``DEFAULT_BUS_HANDLER_CONCURRENCY`` stays the outer total budget; this caps
# how many of those slots ANY single tenant may hold at once, so one tenant
# flooding ``document.uploaded.v1`` cannot starve another's ingest. MUST be
# < the global budget for fairness to bite (2 of 5 ⇒ ≥3 slots always reachable
# by other tenants). The per-tenant semaphore is acquired OUTSIDE the global
# one, so blocked tasks of a noisy tenant never hold a global slot.
DEFAULT_BUS_CONCURRENCY_PER_TENANT: Final[int] = 2
# Ingest fairness keyed by (bot_id, channel_type) + workspace instead of tenant
# (2026-06-13, owner spec). A single bot+channel may hold up to
# ``DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL`` slots; a single workspace up to
# ``DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE`` (so its bots share a wider budget).
# Both are acquired (workspace outer, bot+channel inner) under the global
# ``DEFAULT_BUS_HANDLER_CONCURRENCY`` budget — a noisy bot can't starve sibling
# bots of the same workspace, and a noisy workspace can't starve others. Set the
# global handler concurrency high enough that these caps actually bite.
DEFAULT_BUS_CONCURRENCY_PER_BOT_CHANNEL: Final[int] = 5
DEFAULT_BUS_CONCURRENCY_PER_WORKSPACE: Final[int] = 10
# Upper bound on the in-process per-tenant semaphore registry. Beyond this many
# distinct active tenants in one worker, overflow tenants share a single
# fallback semaphore (still bounded, never an unbounded dict). Sized well above
# realistic concurrent-tenant counts on one host.
DEFAULT_BUS_TENANT_SEM_MAX: Final[int] = 256

# Consumer-side dedup TTL for outbox Msg-Id header (Redis fast-path hint).
DEFAULT_OUTBOX_DEDUP_TTL_S: Final[int] = 86_400

# Transactional-inbox retention — DELETE event_inbox rows older than this
# (the source of truth for exactly-once is the inbox PK, Redis NX is a hint).
DEFAULT_INBOX_RETENTION_DAYS: Final[int] = 1
# Consumer poison threshold: after this many redeliveries a message is
# XADDed to the {stream}:dlq parking-lot stream then XACKed (real DLQ).
DEFAULT_BUS_DLQ_MAX_DELIVERIES: Final[int] = 5

SUBJECT_SYSTEM_CONFIG_CHANGED: Final[str] = "system_config.changed.v1"
SUBJECT_TOKEN_REVOKED: Final[str] = "token.revoked.v1"
# Emitted by BotLifecycleService.purge_bot after the hard-delete cascade.
SUBJECT_BOT_PURGED: Final[str] = "bot.purged.v1"

# understand_query cache key prefix — scanned + unlinked by bot purge.
CACHE_KEY_UQ_PREFIX: Final[str] = "ragbot:uq:v"
# SCAN COUNT hint for the purge UQ-cache sweep (batch size, not a limit).
DEFAULT_PURGE_UQ_SCAN_COUNT: Final[int] = 500

# --- GZip -------------------------------------------------------------------
GZIP_MINIMUM_SIZE: Final[int] = 1_000

