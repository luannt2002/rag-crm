"""Prometheus metrics — RAG-specific.

Ref: PLAN_14 / RAGBOT_MASTER §13.3.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

# RAG stage latency histogram
rag_stage_latency_seconds = Histogram(
    "rag_stage_latency_seconds",
    "Latency per RAG stage",
    labelnames=["stage", "intent"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10),
    registry=REGISTRY,
)

rag_tokens_total = Counter(
    "rag_tokens_total",
    "LLM tokens consumed",
    labelnames=["provider", "model", "kind"],
    registry=REGISTRY,
)

rag_cost_usd_total = Counter(
    "rag_cost_usd_total",
    "Aggregated LLM cost in USD",
    labelnames=["tenant_id", "bot_id", "model"],
    registry=REGISTRY,
)

cache_hit_total = Counter(
    "cache_hit_total",
    "Cache hits per layer",
    labelnames=["layer"],
    registry=REGISTRY,
)

iteration_count = Histogram(
    "reasoning_iteration_count",
    "Number of iterations per chat answer",
    buckets=(1, 2, 3, 4, 5),
    registry=REGISTRY,
)

citation_validation_fail_total = Counter(
    "citation_validation_fail_total",
    "Citations failed validation (hallucination guard)",
    registry=REGISTRY,
)

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests",
    labelnames=["method", "route", "status"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Task 5 — standardized RAG metrics (≥ 8) for /metrics scrape.
# Low-cardinality labels only (NO message_id / request_id / user_id).
# ---------------------------------------------------------------------------
request_total = Counter(
    "request_total",
    "Chat requests processed",
    labelnames=["status", "channel_type"],
    registry=REGISTRY,
)

request_duration_seconds = Histogram(
    "request_duration_seconds",
    "End-to-end chat request duration",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    registry=REGISTRY,
)

step_duration_seconds = Histogram(
    "step_duration_seconds",
    "Per-pipeline-step duration",
    labelnames=["step_name"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
    registry=REGISTRY,
)

tokens_used_total = Counter(
    "tokens_used_total",
    "LLM tokens consumed",
    labelnames=["purpose", "model_id", "kind"],
    registry=REGISTRY,
)

cost_usd_total = Counter(
    "cost_usd_total",
    "LLM cost in USD",
    labelnames=["purpose", "model_id"],
    registry=REGISTRY,
)

guardrail_triggered_total = Counter(
    "guardrail_triggered_total",
    "Guardrail rule hits",
    labelnames=["rule_id", "severity", "action"],
    registry=REGISTRY,
)

grounding_fail_total = Counter(
    "grounding_fail_total",
    "Grounding / citation failures",
    registry=REGISTRY,
)

# Grounding judge DEGRADED — judge died (timeout/error) or returned nothing
# (no checkable sentences), so the answer was passed through unverified. This
# is distinct from grounding_fail_total (judge ran and flagged a claim): a
# rising degraded count means the HALLU observability net is silently OFF, not
# that answers are clean (P2-E 🐛-3). ``reason`` = error | empty.
grounding_degraded_total = Counter(
    "grounding_degraded_total",
    "Grounding judge degraded (answer passed unverified)",
    labelnames=["reason"],
    registry=REGISTRY,
)

model_invocation_total = Counter(
    "model_invocation_total",
    "Model invocations by purpose/provider/status",
    labelnames=["purpose", "provider", "status"],
    registry=REGISTRY,
)

document_ingest_total = Counter(
    "document_ingest_total",
    "Document ingestion outcomes",
    labelnames=["status"],
    registry=REGISTRY,
)

document_ingest_duration_seconds = Histogram(
    "document_ingest_duration_seconds",
    "Document ingestion duration",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)

outbox_published_total = Counter(
    "outbox_published_total",
    "Outbox records published",
    labelnames=["status"],
    registry=REGISTRY,
)

# Document recovery worker — Phase 2 case study 2026-05-18.
# Counts stuck-DRAFT documents replayed by the recovery sweep. ``status``
# is "success" when the outbox row was inserted; "failed" when the row
# could not be persisted (DB error). Operators alert on sustained
# replays — a healthy steady-state is near-zero.
document_recovery_replayed_total = Counter(
    "document_recovery_replayed_total",
    "Stuck documents replayed by recovery worker",
    labelnames=["status"],
    registry=REGISTRY,
)

# Rate-limit fail-closed counter. Feeds a Grafana alert so an operator
# notices when the limiter starts rejecting traffic on Redis outage
# (instead of silently fail-open).
rate_limit_backend_error_total = Counter(
    "ragbot_rate_limit_backend_error_total",
    "Redis errors encountered during rate-limit check",
    labelnames=["reason"],
    registry=REGISTRY,
)

rate_limit_fail_closed_total = Counter(
    "ragbot_rate_limit_fail_closed_total",
    "Requests rejected with 503 because rate-limit backend was unavailable",
    labelnames=["scope"],
    registry=REGISTRY,
)

# Query embedding model mismatch detector. Ingest model and query-time
# resolved binding must match; otherwise vector-space mismatch silently
# degrades retrieval.
embedding_model_mismatch_total = Counter(
    "ragbot_embedding_model_mismatch_total",
    "Query embedding model != ingest embedding model — silent retrieval degradation.",
    labelnames=["expected", "resolved"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Prompt-cache observability — provider-side caching (OpenAI auto-cache for
# prompts ≥1024 tokens; Anthropic ephemeral cache_control). The router
# already discounts cost via usage.prompt_tokens_details.cached_tokens; these
# counters expose hit ratio + tokens saved so we can validate the
# break-even (Anthropic ≥1.4 reads, OpenAI ≥1024-token prompts).
# Labels kept low-cardinality: provider code, purpose (generation/grading/...).
# ---------------------------------------------------------------------------
prompt_cache_hits_total = Counter(
    "ragbot_prompt_cache_hits_total",
    "LLM completions whose response reported any cached prompt tokens.",
    labelnames=["provider", "purpose"],
    registry=REGISTRY,
)

prompt_cache_tokens_saved_total = Counter(
    "ragbot_prompt_cache_tokens_saved_total",
    "Cumulative cached prompt tokens (provider-side cache hits).",
    labelnames=["provider", "purpose"],
    registry=REGISTRY,
)

# Per-tenant rate-limit + monthly token cap counters: Layer-1 429s,
# bypass short-circuits, monthly token warn (≥80%), block (≥100%).
tenant_rate_limit_blocked_total = Counter(
    "ragbot_tenant_rate_limit_blocked_total",
    "Requests rejected with 429 by Layer-1 per-tenant rate-limiter.",
    labelnames=["source"],  # "tenant" | "system" | "fallback"
    registry=REGISTRY,
)

tenant_rate_limit_bypass_total = Counter(
    "ragbot_tenant_rate_limit_bypass_total",
    "Requests that skipped Layer-1 due to tenant or bot bypass flag.",
    labelnames=["channel"],  # "tenant_bypass" | "bot_bypass"
    registry=REGISTRY,
)

# Observability-preserving bypass counter. Bumped every time a request
# bypasses Layer-1 enforcement (``tenants.bypass_rate_limit=TRUE`` OR
# ``bots.bypass_rate_limit=TRUE``) — the underlying Redis counter still
# INCRs so admin dashboards retain VIP / internal-partner traffic
# visibility. ``source`` mirrors the resolver verdict (``tenant_bypass``
# | ``bot_bypass``); ``tenant_id`` is the UUID — cardinality scales with
# the count of bypass-enabled tenants, which is intentionally tiny.
rate_limit_bypass_observed_total = Counter(
    "ragbot_rate_limit_bypass_observed_total",
    "Layer-1 bypass events (tenant or bot) where the counter was still "
    "incremented for observability.",
    labelnames=["tenant_id", "source"],  # source = tenant_bypass | bot_bypass
    registry=REGISTRY,
)

tenant_token_warn_total = Counter(
    "ragbot_tenant_token_warn_total",
    "Times a tenant crossed the monthly token soft-warn threshold.",
    labelnames=["tenant_id"],
    registry=REGISTRY,
)

tenant_token_blocked_total = Counter(
    "ragbot_tenant_token_blocked_total",
    "Requests rejected because the tenant hit its monthly token cap.",
    labelnames=["tenant_id"],
    registry=REGISTRY,
)

# High-traffic resilience gauges. Pool / queue / circuit pressure lets
# operators correlate spikes (DB exhaustion, Redis saturation, LLM flap)
# directly. Cache-stampede counter validates single-flight at miss boundaries.
# ---------------------------------------------------------------------------
redis_pool_active_connections = Gauge(
    "ragbot_redis_pool_active_connections",
    "Active connections held by the Redis client pool (snapshot).",
    registry=REGISTRY,
)

db_pool_active_connections = Gauge(
    "ragbot_db_pool_active_connections",
    "Active connections checked out of the SQLAlchemy async pool (snapshot).",
    registry=REGISTRY,
)

circuit_breaker_state = Gauge(
    "ragbot_circuit_breaker_state",
    "Per-provider circuit-breaker state. 0=CLOSED, 1=HALF_OPEN, 2=OPEN.",
    labelnames=["provider"],
    registry=REGISTRY,
)

chat_worker_queue_depth = Gauge(
    "ragbot_chat_worker_queue_depth",
    "In-flight chat pipeline runs held by the worker concurrency Semaphore.",
    registry=REGISTRY,
)

cache_stampede_avoided_total = Counter(
    "ragbot_cache_stampede_avoided_total",
    "Concurrent cache lookups that awaited an in-flight single-flight lock "
    "instead of issuing their own DB query.",
    labelnames=["cache_name"],
    registry=REGISTRY,
)

# Snapshot size of weak-ref backed single-flight lock pools. Bounds the
# unbounded-dict failure mode where every distinct key would otherwise
# leak an ``asyncio.Lock`` for the process lifetime. ``pool`` is a low-
# cardinality label naming the owning subsystem ("semantic_cache",
# "pipeline_audit"). Healthy steady-state ≈ in-flight requests; runaway
# growth signals a weakref-cycle leak.
inflight_locks_size = Gauge(
    "ragbot_inflight_locks_size",
    "Snapshot size of the in-process single-flight lock pool per subsystem.",
    labelnames=["pool"],
    registry=REGISTRY,
)

# p99 outlier guard. Increments when end-to-end chat duration crosses
# ``DEFAULT_P99_OUTLIER_THRESHOLD_S`` (seconds). ``latency_bucket`` is a
# coarse low-cardinality label ("20-30" / "30-60" / "60+") so Grafana can
# split a single tail incident from a sustained regression. ``intent`` is
# the resolved chat intent (or "unknown") — also low cardinality (≤ 12).
chat_p99_outlier_total = Counter(
    "ragbot_chat_p99_outlier_total",
    "Chat requests whose end-to-end duration exceeded the p99 outlier "
    "threshold (seconds).",
    labelnames=["intent", "latency_bucket"],
    registry=REGISTRY,
)

# Intent classifier confidence distribution. Histogram so a Grafana
# panel can show the 50th / 90th percentile of self-reported classifier
# confidence per intent. Buckets stay coarse (6 bins) to keep
# cardinality bounded across the 9-value intent enum.
intent_classifier_confidence = Histogram(
    "ragbot_intent_classifier_confidence",
    "LLM-reported intent classification confidence (range 0-1) per resolved intent.",
    labelnames=["intent"],
    buckets=(0.1, 0.3, 0.5, 0.7, 0.9, 1.0),
    registry=REGISTRY,
)

# Counter increments every time the decompose node is skipped because
# the classifier confidence dropped below
# DEFAULT_DECOMPOSE_CONFIDENCE_GATE. Operators correlate p95 trim vs.
# decompose-skip rate.
decompose_skipped_low_confidence_total = Counter(
    "ragbot_decompose_skipped_low_confidence_total",
    "Decompose node skipped because intent classifier confidence < gate.",
    labelnames=["intent"],
    registry=REGISTRY,
)

# Multi-query gating instrumentation. ``deduped`` counts the number of
# paraphrase variants dropped by cosine/jaccard near-duplicate
# detection; ``skipped_no_entities`` increments when the entity gate
# bypassed the LLM expansion entirely.
mq_variants_deduped_total = Counter(
    "ragbot_mq_variants_deduped_total",
    "Multi-query paraphrase variants dropped by similarity dedup.",
    registry=REGISTRY,
)

mq_skipped_no_entities_total = Counter(
    "ragbot_mq_skipped_no_entities_total",
    "Multi-query expansion skipped because entity extractor returned no entities.",
    registry=REGISTRY,
)

# LLM primary→fallback failover events. ``reason`` is the exception type
# that triggered the retry (CircuitBreakerOpen, LLMError, etc.). The
# router only emits this counter when a fallback hop is actually
# attempted; bindings without ``record_fallback_model_id`` re-raise the
# primary error and never bump the counter.
llm_provider_failover_total = Counter(
    "ragbot_llm_provider_failover_total",
    "LLM primary→fallback failover hops attempted by the router.",
    labelnames=["from_provider", "to_provider", "purpose", "reason"],
    registry=REGISTRY,
)

# Active API key swap events (HTTP 403 / 429 from upstream). Provider-agnostic:
# ``provider_code`` ("jina", "openai", ...) plus ``purpose`` ("embed" /
# "rerank") disambiguate pools; ``from_label``/``to_label`` are
# "primary"/"secondary".
api_key_failover_total = Counter(
    "ragbot_api_key_failover_total",
    "Active API key switch events triggered by HTTP 403/429 from upstream providers.",
    labelnames=["provider_code", "purpose", "from_label", "to_label", "reason"],
    registry=REGISTRY,
)

# Per-provider warmup probe duration + outcome for the cold-start
# guard. ``ok`` label is "true"/"false" (string, low cardinality).
warmup_provider_duration_ms = Histogram(
    "ragbot_warmup_provider_duration_ms",
    "Per-provider warmup probe duration in milliseconds.",
    labelnames=["provider", "ok"],
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=REGISTRY,
)

# Cliff-detect filter drop telemetry. Bumped once per filter_min_score step
# when the cliff strategy runs and removes at least one chunk. ``reason`` is
# the same low-cardinality enum surfaced in step metadata ("cliff",
# "below_floor_or_single", "empty_context_safety_keep_top1"). Operators
# correlate drop volume / reason mix against REFUSE_GAP rate after threshold
# recalibrations (e.g. S2 cliff floor 0.15 → 0.05 on 2026-05-11).
cliff_drop_total = Counter(
    "ragbot_cliff_drop_total",
    "Cliff-detect filter chunk drops at the filter_min_score step.",
    labelnames=["bot_id", "reason"],
    registry=REGISTRY,
)

# Notify channel — webhook-driven error alerting. ``component`` labels
# match the dispatch site ("chat.pipeline" / "ingest.pipeline" /
# "admin_test"); ``severity`` is "info" / "error" / "critical".
notify_sent_total = Counter(
    "ragbot_notify_sent_total",
    "Notifications successfully POSTed to the webhook target.",
    labelnames=["component", "severity"],
    registry=REGISTRY,
)
# ``reason`` ∈ {rate_limit, dedup, disabled, unconfigured}.
notify_dropped_total = Counter(
    "ragbot_notify_dropped_total",
    "Notifications dropped before dispatch.",
    labelnames=["reason"],
    registry=REGISTRY,
)
# ``status_class`` ∈ {4xx, 5xx, timeout, network}.
notify_dispatch_failed_total = Counter(
    "ragbot_notify_dispatch_failed_total",
    "Notifications where the upstream HTTP POST failed after retry.",
    labelnames=["status_class"],
    registry=REGISTRY,
)


class MetricsRegistry:
    """Thin facade exposing the module-level Prometheus collectors.

    Bootstrap/container singletons return an instance of this so call sites
    can depend on a single object instead of importing each metric.
    """

    registry = REGISTRY
    request_total = request_total
    request_duration_seconds = request_duration_seconds
    step_duration_seconds = step_duration_seconds
    tokens_used_total = tokens_used_total
    cost_usd_total = cost_usd_total
    guardrail_triggered_total = guardrail_triggered_total
    grounding_fail_total = grounding_fail_total
    grounding_degraded_total = grounding_degraded_total
    model_invocation_total = model_invocation_total
    document_ingest_total = document_ingest_total
    document_ingest_duration_seconds = document_ingest_duration_seconds
    outbox_published_total = outbox_published_total
    http_requests_total = http_requests_total
    rag_stage_latency_seconds = rag_stage_latency_seconds
    rag_tokens_total = rag_tokens_total
    rag_cost_usd_total = rag_cost_usd_total
    cache_hit_total = cache_hit_total
    citation_validation_fail_total = citation_validation_fail_total


def setup_metrics_app() -> tuple[bytes, str]:
    """Render Prometheus exposition format."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "REGISTRY",
    "MetricsRegistry",
    "cache_hit_total",
    "cache_stampede_avoided_total",
    "chat_p99_outlier_total",
    "chat_worker_queue_depth",
    "circuit_breaker_state",
    "cliff_drop_total",
    "decompose_skipped_low_confidence_total",
    "intent_classifier_confidence",
    "mq_skipped_no_entities_total",
    "mq_variants_deduped_total",
    "warmup_provider_duration_ms",
    "citation_validation_fail_total",
    "cost_usd_total",
    "db_pool_active_connections",
    "document_ingest_duration_seconds",
    "document_ingest_total",
    "document_recovery_replayed_total",
    "embedding_model_mismatch_total",
    "grounding_degraded_total",
    "grounding_fail_total",
    "guardrail_triggered_total",
    "http_requests_total",
    "iteration_count",
    "api_key_failover_total",
    "llm_provider_failover_total",
    "notify_sent_total",
    "notify_dropped_total",
    "notify_dispatch_failed_total",
    "model_invocation_total",
    "outbox_published_total",
    "prompt_cache_hits_total",
    "prompt_cache_tokens_saved_total",
    "rag_cost_usd_total",
    "rag_stage_latency_seconds",
    "rag_tokens_total",
    "rate_limit_backend_error_total",
    "rate_limit_bypass_observed_total",
    "rate_limit_fail_closed_total",
    "tenant_rate_limit_blocked_total",
    "tenant_rate_limit_bypass_total",
    "tenant_token_blocked_total",
    "tenant_token_warn_total",
    "redis_pool_active_connections",
    "request_duration_seconds",
    "request_total",
    "setup_metrics_app",
    "step_duration_seconds",
    "tokens_used_total",
]
