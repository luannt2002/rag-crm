from __future__ import annotations
from typing import Final  # noqa: F401
from ._07_llm_sampling_defaults import *  # noqa: F401,F403

# --- Sentry / OTel ----------------------------------------------------------
DEFAULT_SENTRY_SAMPLE_RATE: Final[float] = 0.1

# --- Policy defaults --------------------------------------------------------
DEFAULT_QUALITY_SCORE: Final[float] = 5.0
DEFAULT_PRIVATE_DOC_RATIO: Final[int] = 100

# --- Circuit breaker --------------------------------------------------------
CIRCUIT_BREAKER_FAIL_MAX: Final[int] = 5
CIRCUIT_BREAKER_RESET_TIMEOUT: Final[int] = 30

DEFAULT_CB_FAILURE_THRESHOLD: Final[int] = 5
DEFAULT_CB_COOLDOWN_S: Final[int] = 30
# Backwards-compat alias kept until callers migrate to *BASE_S*.
DEFAULT_CB_COOLDOWN_BASE_S: Final[int] = DEFAULT_CB_COOLDOWN_S
# adaptive cooldown step + ceiling. Each consecutive OPEN failure
# extends the cooldown by ``STEP_S`` so a flapping upstream stops storming
# retries; capped at ``MAX_S`` so a long outage does not freeze a provider
# forever. Reset to base when state returns to CLOSED.
DEFAULT_CB_COOLDOWN_STEP_S: Final[int] = 15
DEFAULT_CB_COOLDOWN_MAX_S: Final[int] = 120
DEFAULT_CB_HALF_OPEN_MAX_CALLS: Final[int] = 1

# --- Failover orchestrator (Phase D / D1) ----------------------------------
# Default ENABLED (defensive). Set ``circuit_breaker_enabled = false`` in
# ``system_config`` to revert all resources to ``NullCircuitBreaker``
# (always-closed pass-through) without code change.
DEFAULT_CIRCUIT_BREAKER_ENABLED: Final[bool] = True

# Canonical resource keys for the registry. Add a new resource = new key
# here + new adapter file in ``infrastructure/resilience/``; orchestration
# stays untouched (Open-Closed).
CB_RESOURCE_REDIS: Final[str] = "redis"
CB_RESOURCE_DB: Final[str] = "db"
CB_RESOURCE_LLM: Final[str] = "llm"

DEFAULT_CHAT_WORKER_CONCURRENCY: Final[int] = 4
# Match available vCPU count; update systemd unit alongside this value.
DEFAULT_UVICORN_WORKERS: Final[int] = 4

# --- Async chat queue (mega-sprint-G25 / G26) ------------------------------
# Redis Stream consumed by ``scripts/chat_async_worker.py`` (G25). The HTTP
# route (G26: ``interfaces/http/routes/chat_async.py``) enqueues a job +
# ``job_id`` here and returns immediately; the worker consumes the stream,
# invokes the existing LangGraph pipeline, then writes the result to
# ``CHAT_RESULT_HASH_PREFIX{job_id}`` with TTL ``DEFAULT_CHAT_RESULT_TTL_S``.
# Decouples LLM latency from the HTTP request lifecycle so the API can accept
# 50–100 RPS instead of 0.1 RPS (current uvicorn worker hold pattern).
CHAT_REQUEST_STREAM: Final[str] = "chat.requested"
CHAT_REQUEST_CONSUMER_GROUP: Final[str] = "chat-workers"
CHAT_RESULT_HASH_PREFIX: Final[str] = "chat:result:"
# 10 minutes — long enough for a slow client poll loop, short enough that
# abandoned job results do not linger forever in Redis.
DEFAULT_CHAT_RESULT_TTL_S: Final[int] = 600
# XREADGROUP block timeout (ms). Worker wakes every interval to allow graceful
# shutdown signals to be checked without sleeping forever on an empty stream.
DEFAULT_CHAT_STREAM_BLOCK_MS: Final[int] = 5_000
# Max error message length persisted to the result hash — bounded so a giant
# traceback string does not blow the Redis hash size budget.
DEFAULT_CHAT_ERROR_TRUNCATE_CHARS: Final[int] = 300

# --- Async callback delivery defaults --------------------------------------
# These values are the SSoT for the webhook POST path used by chat_worker
# and the CallbackDelivery infrastructure. Operators tune via system_config
# keys (callback_timeout_s, callback_max_retries, callback_verify_ssl).
# Inline literals are forbidden per the zero-hardcode rule in CLAUDE.md.
DEFAULT_CALLBACK_TIMEOUT_S: Final[int] = 10
DEFAULT_CALLBACK_MAX_RETRIES: Final[int] = 3
# Base delay (seconds) for exponential backoff: attempt 0→1s, 1→2s, 2→4s.
DEFAULT_CALLBACK_BACKOFF_BASE_S: Final[float] = 1.0
# Deliver-time SSRF guard: re-resolve the callback host and reject
# private/internal IPs (RFC1918, loopback, link-local, cloud metadata
# 169.254.169.254) right before the POST. Setup-time validation alone is
# insufficient — a DNS-rebinding attacker flips the record from a public
# IP at validation to an internal IP at delivery. Secure-by-default ON;
# operators opt out per deployment via system_config
# (callback_ssrf_guard_enabled) when delivering inside a trusted VPC.
DEFAULT_CALLBACK_SSRF_GUARD_ENABLED: Final[bool] = True

DEFAULT_CACHE_STAMPEDE_LOCK_TIMEOUT_S: Final[int] = 5

# Redis SET NX EX TTL on the semantic-cache single-flight lock. Cross-process
# (uvicorn worker A vs worker B) thundering-herd protection: lock holder
# computes + stores; waiters poll the cache. TTL chosen so a crashed holder
# does not freeze waiters forever — pgvector lookup p95 << 5s in steady state.
DEFAULT_SEMANTIC_CACHE_LOCK_TTL_S: Final[int] = 5

# Sleep interval between waiter retries when the cross-process lock is held by
# another worker. Bounded recursion in find_similar_with_text(): after the
# lock holder populates the cache, the waiter's recursive call hits the exact-
# hash fast path. Set short enough that p50 cache hits don't visibly slow.
DEFAULT_SEMANTIC_CACHE_WAIT_RETRY_S: Final[float] = 0.1

# --- Debounce / idempotency -------------------------------------------------
DEBOUNCE_WINDOW_MS: Final[int] = 800

# --- SLA target (seconds) ---------------------------------------------------
DEFAULT_P95_SLA_SECONDS: Final[float] = 5.0
DEFAULT_P99_SLA_SECONDS: Final[float] = 8.0

# --- SLA alert thresholds (Prometheus alert rule sources) ------------------
# Used by the SLA-monitoring layer to compute "breach / no-breach" verdicts
# for sampled metric snapshots. Each constant is the *default*; operators
# may override at runtime via the matching ``sla_*`` keys in
# ``system_config`` (e.g. ``sla_p95_warn_seconds``). The constants are the
# single source of truth shipped in code; the Prometheus alert rule YAML
# references the same numeric values so YAML and code never drift.
DEFAULT_SLA_P95_WARN_SECONDS: Final[float] = 10.0
DEFAULT_SLA_P95_CRITICAL_SECONDS: Final[float] = 15.0
DEFAULT_SLA_ERROR_RATE_WARN: Final[float] = 0.05  # 5%
DEFAULT_SLA_ERROR_RATE_CRITICAL: Final[float] = 0.10  # 10%
DEFAULT_SLA_CACHE_HIT_RATIO_WARN: Final[float] = 0.20  # 20%
DEFAULT_SLA_CIRCUIT_OPEN_DURATION_S: Final[float] = 600.0  # 10 min
# Sustained-breach evaluation window. The Prometheus rule fires only when
# the metric expression evaluates true for this many seconds — protects
# against single-spike false positives.
DEFAULT_SLA_BREACH_WINDOW_S: Final[float] = 300.0  # 5 min

# --- p99 outlier guard ----------------------------------------------
# Chat requests slower than this threshold (wall-clock seconds) are counted
# in the ``chat_p99_outlier_total`` Prometheus counter and emit a structured
# ``chat_latency_outlier`` warning event. Threshold is intentionally well
# above the SLA target — anything past 20s is a tail-of-tail incident a
# human should look at, not a transient blip.
DEFAULT_P99_OUTLIER_THRESHOLD_S: Final[float] = 20.0

# --- startup warmup --------------------------------------------------
# Best-effort embed + LLM probe at app boot so the first real request does
# not pay cold connect / DNS / model-load. Runs as a background asyncio task
# so a slow probe does not delay readiness. Never raises — failures only
# emit ``warmup_failed`` warnings.
DEFAULT_WARMUP_ENABLED: Final[bool] = True
DEFAULT_WARMUP_TIMEOUT_S: Final[float] = 10.0
# Probe text used by the warmup LLM call. Documented as a literal probe —
# NOT prompt content injected into any user-facing prompt.
DEFAULT_WARMUP_LLM_PROBE_TEXT: Final[str] = "ping"
DEFAULT_WARMUP_LLM_MAX_TOKENS: Final[int] = 5

# --- Invocation audit janitor ----------------------------------------------
# Above this status='running' age the row is rewritten to status='failed'.
DEFAULT_INVOCATION_STUCK_TIMEOUT_S: Final[int] = 300

# --- Validation bounds ------------------------------------------------------
MAX_CHAT_CONTENT_LENGTH: Final[int] = 2000
MAX_DOCUMENT_NAME_LENGTH: Final[int] = 255
MAX_FILE_SIZE_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MB

