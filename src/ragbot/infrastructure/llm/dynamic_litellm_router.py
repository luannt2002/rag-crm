"""DynamicLiteLLMRouter — DB-driven LiteLLM routing.

Refreshes ``model_list`` from ``ai_providers`` + ``ai_models`` via
``AIConfigRepositoryPort`` on a TTL or on ``bot.config_updated.v1``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any

import litellm
import structlog
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.ai_config_port import AIConfigRepositoryPort
from ragbot.application.ports.llm_port import LLMMessage, LLMPort, LLMResponse
from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from uuid import UUID

from datetime import datetime, timedelta, timezone

from ragbot.application.ports.token_ledger_port import (
    TokenLedgerEntry,
    TokenLedgerPort,
)
from ragbot.application.services.tenant_token_meter import TenantTokenMeter
from ragbot.config.logging import (
    bot_id_ctx,
    channel_type_ctx,
    mode_ctx,
    record_bot_id_ctx,
    tenant_id_ctx,
    trace_id_ctx,
    workspace_id_ctx,
)
from ragbot.infrastructure.token_ledger.null_token_ledger import NullTokenLedger
from ragbot.shared.constants import (
    ANTHROPIC_PROVIDER_CODES,
    DEFAULT_CB_COOLDOWN_S,
    DEFAULT_CB_FAILURE_THRESHOLD,
    DEFAULT_DETERMINISTIC_LLM_PURPOSES,
    DEFAULT_DETERMINISTIC_TEMPERATURE,
    DEFAULT_DYNAMIC_ROUTER_REFRESH_INTERVAL_S,
    DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS,
    DEFAULT_LLM_FAILOVER_ENABLED,
    DEFAULT_BEST_EFFORT_LLM_PURPOSES,
    DEFAULT_BEST_EFFORT_RETRY_MAX_ATTEMPTS,
    DEFAULT_CRITICAL_LLM_PURPOSES,
    DEFAULT_CRITICAL_RETRY_MAX_ATTEMPTS,
    DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
    DEFAULT_RETRY_INITIAL_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_RETRY_MAX_MS,
    DEFAULT_TENANT_TOKEN_CAP_ENFORCE_PREFLIGHT,
)
from ragbot.infrastructure.llm.tpm_rate_limiter import (
    TpmRateLimiter,
    estimate_request_tokens,
)
from ragbot.shared.constants import (
    DEFAULT_LLM_TPM_LIMIT,
    DEFAULT_LLM_TPM_SAFETY_FRACTION,
)
from ragbot.shared.errors import CircuitBreakerOpen, LLMError
from ragbot.shared.types import TenantId, TraceId

# Process-wide per-model TPM limiter. Paces the LLM-gateway so ingest
# enrichment bursts queue instead of bursting → 429 → retry storm. Capped a
# safety margin BELOW the org ceiling so estimation error + window skew don't
# push us over and earn a 429 anyway.
_TPM_LIMITER = TpmRateLimiter(
    int(DEFAULT_LLM_TPM_LIMIT * DEFAULT_LLM_TPM_SAFETY_FRACTION)
)

try:  # pragma: no cover — optional metrics import (tests may not load app)
    from ragbot.infrastructure.observability.metrics import (
        circuit_breaker_state as _cb_state_gauge,
        llm_provider_failover_total,
        prompt_cache_hits_total,
        prompt_cache_tokens_saved_total,
    )
except Exception:  # noqa: BLE001
    prompt_cache_hits_total = None  # type: ignore[assignment]
    prompt_cache_tokens_saved_total = None  # type: ignore[assignment]
    _cb_state_gauge = None  # type: ignore[assignment]
    llm_provider_failover_total = None  # type: ignore[assignment]


# Exception types that can trigger a provider→fallback hop. Kept narrow on
# purpose: auth / bad-request errors stay non-retryable so they surface as
# programmer bugs rather than masking themselves behind a fallback hop.
_FAILOVER_TRIGGERS: tuple[type[BaseException], ...] = (
    CircuitBreakerOpen,
    LLMError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.APIConnectionError,
)


_CB_STATE_TO_INT: dict[CBState, int] = {
    CBState.CLOSED: 0,
    CBState.HALF_OPEN: 1,
    CBState.OPEN: 2,
}

logger = structlog.get_logger(__name__)


def _safe_uuid(val: str | None) -> UUID | None:
    """Parse a contextvar string as UUID for the token ledger; None on miss."""
    if not val or val == "UNSET":
        return None
    try:
        return UUID(str(val))
    except (ValueError, AttributeError, TypeError):
        return None

# Exceptions retryable across LLM providers. Auth errors / bad requests are
# NOT retried — those are programmer bugs, retrying hides them.
_RETRYABLE_LLM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OSError,
    ConnectionError,
    TimeoutError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.APIConnectionError,
    # Gateway 5xx (e.g. an OpenAI-compatible proxy hiccupping under load) is a
    # transient outage, not a client bug — retry the same provider before giving
    # up, otherwise one flaky 500 empties the whole turn.
    litellm.exceptions.InternalServerError,
)


def _disable_sdk_inner_retry(call_kwargs: dict[str, Any]) -> None:
    """Force the provider-SDK's OWN inner retry loop OFF so ONLY our
    ``retry_with_backoff`` layer retries.

    litellm otherwise constructs ``AsyncOpenAI(max_retries=2)`` (its default when
    no ``max_retries`` kwarg is passed), whose retry loop STACKS under our
    ``retry_with_backoff(max_attempts=3)`` — ~6x hammering of an already-struggling
    provider (the load-test's 244 uncoordinated ``Retrying request`` lines). A
    SINGLE controlled retry layer (ours, with backoff + jitter) is the AWS/Google-
    SRE best practice; nested retries amplify load on a failing upstream. Our
    ``_RETRYABLE_LLM_EXCEPTIONS`` already covers every transient the SDK would
    retry (connection / timeout / 429 / 5xx), so no coverage is lost.
    ``max_retries=0`` is load-bearing (zeroes the OpenAI client); ``num_retries=0``
    also keeps litellm's own retry wrapper off. ``setdefault`` never overrides an
    explicit caller value.
    """
    call_kwargs.setdefault("num_retries", 0)
    call_kwargs.setdefault("max_retries", 0)


def _retry_attempts_for_purpose(purpose: str) -> int:
    """Retry budget for a call, by its purpose.

    Best-effort calls (query understanding / expansion / grading — see
    ``DEFAULT_BEST_EFFORT_LLM_PURPOSES``) FAIL FAST: they degrade gracefully, so
    retrying a call that will be discarded anyway only pins a provider slot for
    attempts×timeout and hammers a struggling upstream (head-of-line blocking).
    The CRITICAL answer call (``generation``) gets a LARGER budget — its failure
    is the user-facing 503, so it retries harder (still a single coordinated
    layer, bounded by the pipeline wall-clock). Everything else — the safety check
    (``grounding``), routing, embedding — keeps the default budget. Default is the
    FULL default budget so a new/unclassified purpose is never accidentally made
    fail-fast.
    """
    if purpose in DEFAULT_BEST_EFFORT_LLM_PURPOSES:
        return DEFAULT_BEST_EFFORT_RETRY_MAX_ATTEMPTS
    if purpose in DEFAULT_CRITICAL_LLM_PURPOSES:
        return DEFAULT_CRITICAL_RETRY_MAX_ATTEMPTS
    return DEFAULT_RETRY_MAX_ATTEMPTS

# A 429 rate-limit is flow-control, NOT a provider outage: the provider is
# healthy and asking us to slow down. The TPM limiter + backoff already pace it.
# Counting it as a circuit-breaker failure is the Nygard/resilience4j
# anti-pattern of conflating throttle with outage — and because the breaker is
# keyed per-PROVIDER, a cheap-tier ingest burst (nano) would OPEN the shared
# "openai" breaker and fast-fail the live answer model (mini). So rate-limit
# errors are excluded from breaker accounting; only genuine outages (timeout /
# 5xx / connection drop) trip it.
_RATE_LIMIT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    litellm.exceptions.RateLimitError,
)


def _is_rate_limit(exc: BaseException) -> bool:
    """True iff ``exc`` is a provider rate-limit (429) — flow-control, not outage."""
    return isinstance(exc, _RATE_LIMIT_EXCEPTIONS)


def _external_error_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status code off a LiteLLM / httpx exception.

    LiteLLM provider exceptions (``RateLimitError``, ``AuthenticationError``,
    ``ServiceUnavailableError``, ``InternalServerError`` …) carry a
    ``status_code`` attribute; an underlying ``httpx`` error exposes it on its
    ``response``. ``None`` when neither is present (e.g. a raw connection
    error) — the event still logs, just without a numeric code.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _log_external_call_failed(
    *,
    integration: str,
    provider: str | None,
    model: str | None,
    exc: BaseException,
    duration_ms: int,
) -> None:
    """Emit the canonical ``external_call_failed`` observability event.

    Shared shape across the LLM / embed / rerank integrations so an operator
    can grep one event name and always see status + provider + model + a
    bounded error-body snippet + latency. Pure observability — the caller still
    owns the raise/return decision; this never swallows or alters control flow.
    """
    logger.warning(
        "external_call_failed",
        integration=integration,
        provider=provider or "unknown",
        model=model or "unknown",
        status_code=_external_error_status(exc),
        error=str(exc)[:DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS],
        error_type=type(exc).__name__,
        duration_ms=duration_ms,
    )


def _is_anthropic_model(litellm_name: str | None, provider_code: str | None) -> bool:
    """Return True iff the given model routes to Anthropic.

    Matches case-insensitive substring on either the LiteLLM model name
    (provider-prefixed wire name) or the provider code from the DB. Other
    providers all evaluate False so the cache_control wrapper can be
    applied unconditionally — no-op for non-Anthropic providers.
    """
    haystack = f"{(litellm_name or '').lower()}|{(provider_code or '').lower()}"
    return any(token in haystack for token in ANTHROPIC_PROVIDER_CODES)


def _apply_anthropic_cache_control(
    messages: list[dict[str, Any]],
    *,
    litellm_name: str | None = None,
    provider_code: str | None = None,
) -> list[dict[str, Any]]:
    """Wrap the FIRST system message in ``cache_control: {"type": "ephemeral"}``.

    Anthropic charges 25% of input price for cache writes and 10% for cache
    reads (5-min TTL). System prompt + few-shot examples are stable across
    turns within a session so they're the canonical breakpoint. Per
    Anthropic limits we only emit ONE breakpoint here — caller can extend
    later if it wants to mark few-shot/custom_vocabulary boundaries (max 4).

    Returns the input unchanged for non-Anthropic providers (OpenAI relies on
    automatic prompt caching for prompts ≥1024 tokens — no client flag).

    Idempotent: if the system content is already a list (multi-block), we
    leave it alone so callers retain control of multi-breakpoint payloads.
    """
    if not _is_anthropic_model(litellm_name, provider_code):
        return messages
    if not messages:
        return messages
    first = messages[0]
    if first.get("role") != "system":
        return messages
    content = first.get("content")
    if not isinstance(content, str) or not content:
        # Already a list-of-blocks payload, or empty — caller is in charge.
        return messages
    new_first = {
        **first,
        "content": [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    return [new_first, *messages[1:]]


# ---------------------------------------------------------------------------
# Usage / cost helpers (shared across sync, stream, and structured paths so
# every emit lands in `model_invocations` with identical math). Pricing comes
# from the per-call ``cfg.pricing`` (loaded by ``ModelResolver`` from the
# ``ai_models`` row) — never inlined.
# ---------------------------------------------------------------------------

# Usage extraction lives in ``ragbot.shared.llm_usage`` so the application
# layer can read token counts without importing this infra module
# (hexagonal boundary; see Issue #7 in deep-dive report). Re-exported
# here to preserve the historical public surface.
from ragbot.shared.llm_usage import (  # noqa: E402
    estimate_tokens_fallback,
    extract_usage_from_response,
)


def compute_cost_usd(
    pricing: Any,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Decimal:
    """Compute the per-call USD cost from the resolved ``Pricing`` row.

    ``pricing`` is a ``ragbot.application.dto.model_runtime.Pricing`` (or any
    object exposing ``input_per_1k_usd`` / ``output_per_1k_usd`` /
    ``cached_input_per_1k_usd``). When the model omits a cached price we
    fall back to half the input rate — same heuristic as the legacy inline
    code in ``complete_runtime``.
    """
    if pricing is None:
        return Decimal("0")
    input_rate = getattr(pricing, "input_per_1k_usd", None) or Decimal("0")
    output_rate = getattr(pricing, "output_per_1k_usd", None) or Decimal("0")
    cached_rate_attr = getattr(pricing, "cached_input_per_1k_usd", None)
    cached_rate = (
        cached_rate_attr if cached_rate_attr is not None else input_rate / Decimal(2)
    )
    if not isinstance(input_rate, Decimal):
        input_rate = Decimal(str(input_rate))
    if not isinstance(output_rate, Decimal):
        output_rate = Decimal(str(output_rate))
    if not isinstance(cached_rate, Decimal):
        cached_rate = Decimal(str(cached_rate))
    non_cached_input = max(int(prompt_tokens) - int(cached_tokens), 0)
    return (
        Decimal(non_cached_input) / Decimal(1000) * input_rate
        + Decimal(int(cached_tokens)) / Decimal(1000) * cached_rate
        + Decimal(int(completion_tokens)) / Decimal(1000) * output_rate
    )


# Provider codes for which LiteLLM accepts ``stream_options={"include_usage": True}``.
# Anthropic streams already include ``usage`` on the final ``message_stop``
# event so the flag is a no-op (and rejected by some LiteLLM versions). Lists
# are case-insensitive substring matches against ``cfg.provider.code``.
_STREAM_INCLUDE_USAGE_PROVIDER_CODES: tuple[str, ...] = (
    "openai",
    "azure",
)


def _supports_stream_include_usage(provider_code: str | None) -> bool:
    if not provider_code:
        return False
    code = provider_code.lower()
    return any(token in code for token in _STREAM_INCLUDE_USAGE_PROVIDER_CODES)


def _resolve_effective_temperature(
    temperature: float | None, purpose: str, cfg_temperature: float,
) -> float:
    """Decide the temperature actually sent to the provider (P2-E 🐛-2).

    An explicit ``temperature`` always wins. Otherwise, for a purpose in
    ``DEFAULT_DETERMINISTIC_LLM_PURPOSES`` (rewrite / multi_query / decompose
    / grounding / grade / …) we force ``DEFAULT_DETERMINISTIC_TEMPERATURE``
    (0.0) at the router — the single choke-point every callsite passes
    through. Previously only the ``_invoke`` node-helper forced 0.0, so the
    direct ``llm.complete(cfg, …)`` calls (multi_query / grounding /
    decompose) fell back to the binding's ``cfg.params.temperature`` (seeded
    0.3) and ran non-deterministically — the exact flip source c6c6df4 only
    half-fixed. Non-deterministic purposes keep the binding's configured
    temperature.
    """
    if temperature is not None:
        return temperature
    if purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES:
        return DEFAULT_DETERMINISTIC_TEMPERATURE
    return cfg_temperature


# Sink callback signature: receives ``(prompt_tokens, completion_tokens,
# cached_tokens, cost_usd, finish_reason)`` and may be awaited. Used by
# ``complete_runtime_stream`` so callers (query_graph) can capture token /
# cost totals after the iterator drains, then forward to invocation_logger.
UsageSink = Callable[[int, int, int, float, str | None], Awaitable[None] | None]


class DynamicLiteLLMRouter(LLMPort):
    """LiteLLM-backed router with DB refresh + Redis cache.

    Model list cached in Redis at `ragbot:models` so all workers share it.
    Refreshes from DB on TTL or on `bot.config_updated.v1` event.
    """

    REDIS_KEY = "ragbot:models"

    def __init__(
        self,
        ai_config_repo: AIConfigRepositoryPort,
        redis_client: Any | None = None,
        *,
        refresh_interval_s: int = DEFAULT_DYNAMIC_ROUTER_REFRESH_INTERVAL_S,
        token_meter: TenantTokenMeter | None = None,
        enforce_preflight_cap: bool = DEFAULT_TENANT_TOKEN_CAP_ENFORCE_PREFLIGHT,
        ledger: TokenLedgerPort | None = None,
    ) -> None:
        self._repo = ai_config_repo
        # Token-log-center sink (fire-and-forget). NullTokenLedger when unset.
        self._ledger: TokenLedgerPort = ledger or NullTokenLedger()
        self._redis = redis_client
        self._refresh_interval = refresh_interval_s
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()
        self._model_list: list[dict[str, Any]] = []
        # P33 / C.5 — per-tenant token meter for cap enforcement +
        # post-call accounting. ``None`` is tolerated so existing tests +
        # legacy code paths that build the router directly keep working.
        self._token_meter = token_meter
        self._enforce_preflight_cap = bool(enforce_preflight_cap)
        # P25-L4: one Semaphore per provider code, lazily created on first
        # call. Caps concurrent LLM calls per provider so one provider's
        # slow path can't starve the others.
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}
        # P25 Phase B: one CircuitBreaker per provider code. After
        # DEFAULT_CB_FAILURE_THRESHOLD consecutive failures the breaker OPENs
        # and rejects fast for DEFAULT_CB_COOLDOWN_S seconds, then probes
        # HALF_OPEN. Prevents cascade pile-up when a provider flaps.
        self._provider_circuit_breakers: dict[str, CircuitBreaker] = {}

    def _get_semaphore(self, provider_code: str, max_concurrent: int) -> asyncio.Semaphore:
        sem = self._provider_semaphores.get(provider_code)
        if sem is None:
            sem = asyncio.Semaphore(max_concurrent or DEFAULT_PROVIDER_MAX_CONCURRENT)
            self._provider_semaphores[provider_code] = sem
        return sem

    def _get_circuit_breaker(self, provider_code: str) -> CircuitBreaker:
        """Return the CircuitBreaker for ``provider_code`` (lazily created).

        Per-provider isolation: an OpenAI flap must not OPEN the Anthropic
        breaker — each upstream gets its own state machine.
        """
        cb = self._provider_circuit_breakers.get(provider_code)
        if cb is None:
            cb = CircuitBreaker(
                name=f"llm:{provider_code or 'unknown'}",
                policy=CircuitBreakerPolicy(
                    fail_max=DEFAULT_CB_FAILURE_THRESHOLD,
                    reset_timeout_s=DEFAULT_CB_COOLDOWN_S,
                ),
            )
            self._provider_circuit_breakers[provider_code] = cb
        return cb

    @staticmethod
    def _record_tenant_id_from_ctx() -> UUID | None:
        """Resolve record_tenant_id UUID from contextvars or return None.

        Reads ``tenant_id_ctx`` (the canonical UUID slot bound by
        ``bind_request_context``). System / background calls that never
        bound a tenant get ``None`` — meter calls become a no-op.
        """
        try:
            raw = tenant_id_ctx.get()
        except LookupError:
            return None
        if not raw or raw == "UNSET":
            return None
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            return None

    async def _meter_tokens(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Best-effort post-call increment into the per-tenant meter.

        Reads ``tenant_id_ctx`` (UUID slot) so the LLM call site doesn't
        have to thread the id explicitly. Silent on missing tenant (e.g.
        system background jobs) and on Redis errors — meter has its own
        fail-open. Single source of truth so streaming + non-streaming
        paths emit identical attribution.
        """
        if self._token_meter is None:
            return
        record_tenant_id = self._record_tenant_id_from_ctx()
        if record_tenant_id is None:
            return
        try:
            await self._token_meter.increment_tokens(
                record_tenant_id,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("tenant_token_meter_emit_skip", err=str(exc))

    async def _preflight_token_cap(self, record_tenant_id: UUID | None) -> None:
        """Pre-flight token-cap probe — raises ``LLMError`` when blocked.

        Only enforced when ``enforce_preflight_cap=True`` (default OFF) so
        admins can opt-in per environment. Soft mode (off) still logs a
        warn via the meter but never blocks the request — keeps the
        runtime resilient to DB lag.
        """
        if self._token_meter is None or record_tenant_id is None:
            return
        if not self._enforce_preflight_cap:
            return
        # Cap is loaded from TenantConfigCache in middleware; the router
        # doesn't have its own DB handle. We fetch the current usage and
        # only block if the meter has already crossed the cap, which is
        # observable via the post-call increment from the previous
        # request. Without preflight enforce_preflight_cap=False, this is
        # a no-op fast path.
        try:
            usage = await self._token_meter.get_monthly_usage(record_tenant_id)
        except Exception:  # noqa: BLE001
            return
        # NOTE: cap value lives on the tenant row but the router doesn't
        # see it directly. Operators wanting hard cuts should set
        # tenant_token_cap_enforce_preflight + load cap via middleware
        # then propagate via ``request.state``. Until then we only emit
        # warn-level metric on first crossing.
        logger.debug(
            "tenant_token_preflight_usage",
            record_tenant_id=str(record_tenant_id),
            used=usage.get("total", 0),
        )

    def _emit_cb_state(self, provider_code: str, cb: CircuitBreaker) -> None:
        """Push CB state to the Prometheus gauge (best-effort)."""
        if _cb_state_gauge is None:
            return
        try:
            _cb_state_gauge.labels(provider=provider_code or "unknown").set(
                _CB_STATE_TO_INT[cb.state],
            )
        except Exception:  # noqa: BLE001
            pass

    # --- Public API --------------------------------------------------------
    async def health_check(self) -> bool:
        """Check routing table has at least one model configured."""
        try:
            await self.refresh_routing()
            return len(self._model_list) > 0
        except Exception:  # noqa: BLE001
            logger.warning("llm_router_health_check_failed", exc_info=True)
            return False

    async def refresh_routing(self) -> None:
        async with self._lock:
            # CLAUDE.md Async Rule 1 — providers + models are independent
            # reads against the same repo; gather to halve refresh latency.
            providers_list, models = await asyncio.gather(
                self._repo.list_providers(enabled_only=True),
                self._repo.list_models(enabled_only=True),
            )
            providers = {p.id: p for p in providers_list}
            new_list: list[dict[str, Any]] = []
            for m in models:
                p = providers.get(m.provider_id)
                if p is None:
                    continue
                litellm_name = f"{p.name}/{m.name}"
                params: dict[str, Any] = {"model": litellm_name}
                if p.base_url:
                    params["api_base"] = p.base_url
                new_list.append(
                    {
                        "model_name": litellm_name,
                        "litellm_params": params,
                    },
                )
            self._model_list = new_list
            self._last_refresh = time.monotonic()

            # Cache to Redis
            if self._redis is not None:
                await self._redis.set(self.REDIS_KEY, json.dumps(new_list))

            logger.info("litellm_router_refreshed", model_count=len(new_list))

    async def _maybe_refresh(self) -> None:
        if time.monotonic() - self._last_refresh > self._refresh_interval:
            await self.refresh_routing()

    async def complete_runtime(
        self,
        cfg: Any,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        purpose: str = "unknown",
        background: bool = False,
        **kwargs: Any,
    ) -> dict:
        """Single-turn completion driven by ``ModelRuntimeConfig``.

        Returns ``{text, prompt_tokens, completion_tokens, cost_usd, finish_reason}``.

        P25-L4: wrapped in retry_with_backoff (for retryable LLM errors) +
        per-provider Semaphore (to cap concurrency per upstream). Non-retryable
        errors (auth, bad request) propagate on first attempt as LLMError.

        Prompt-cache: wraps the first system message in
        ``cache_control: ephemeral`` for Anthropic models (no-op for OpenAI,
        which auto-caches prompts ≥1024 tokens). ``purpose`` propagates to
        the cache observability counters as a low-cardinality label.

        When the binding ships a ``record_fallback_model_id`` the primary
        call is wrapped in a single-hop failover: a circuit-breaker open
        or retryable LiteLLM error switches to the fallback model and
        retries once. Failure on the fallback re-raises the second
        exception (no third hop, no provider cache_control transferred).
        """
        try:
            return await self._complete_runtime_one(
                cfg,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                purpose=purpose,
                background=background,
                **kwargs,
            )
        except _FAILOVER_TRIGGERS as exc:
            if not self._failover_eligible(cfg):
                raise
            logger.warning(
                "llm_failover_attempt",
                from_model=getattr(cfg, "wire_model_id", None),
                to_model=cfg.fallback_wire_model_id,
                from_provider=getattr(cfg.provider, "code", None),
                to_provider=getattr(cfg.fallback_provider, "code", None),
                purpose=purpose,
                reason=type(exc).__name__,
            )
            if llm_provider_failover_total is not None:
                try:
                    llm_provider_failover_total.labels(
                        from_provider=getattr(cfg.provider, "code", "unknown"),
                        to_provider=getattr(cfg.fallback_provider, "code", "unknown"),
                        purpose=purpose,
                        reason=type(exc).__name__,
                    ).inc()
                except Exception:  # noqa: BLE001
                    pass
            fallback_cfg = self._build_fallback_cfg(cfg)
            # ``apply_anthropic_cache=False`` — cache_control is per-provider;
            # transferring an Anthropic ephemeral breakpoint to a different
            # upstream corrupts the cache namespace. Fallback hop runs on a
            # plain prompt. One hop max — failure re-raises.
            return await self._complete_runtime_one(
                fallback_cfg,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                purpose=purpose,
                background=background,
                apply_anthropic_cache=False,
                **kwargs,
            )

    @staticmethod
    def _failover_eligible(cfg: Any) -> bool:
        """Return True iff a fallback hop should be attempted for ``cfg``.

        Honors ``DEFAULT_LLM_FAILOVER_ENABLED`` (global kill switch) and
        the per-binding opt-in (``record_fallback_model_id`` populated +
        a resolved fallback provider runtime + a wire model id).
        """
        if not DEFAULT_LLM_FAILOVER_ENABLED:
            return False
        return (
            getattr(cfg, "fallback_model_row_id", None) is not None
            and getattr(cfg, "fallback_provider", None) is not None
            and getattr(cfg, "fallback_wire_model_id", None)
        )

    @staticmethod
    def _build_fallback_cfg(cfg: Any) -> Any:
        """Project ``cfg`` onto its fallback hop (primary fields swapped)."""
        from dataclasses import replace

        return replace(
            cfg,
            provider=cfg.fallback_provider,
            wire_model_id=cfg.fallback_wire_model_id,
            litellm_name=cfg.fallback_wire_model_id,
            # Clear fallback fields on the swapped cfg so a second-hop
            # error cannot recursively chase another tier.
            fallback_model_row_id=None,
            fallback_wire_model_id=None,
            fallback_provider=None,
        )

    async def _complete_runtime_one(
        self,
        cfg: Any,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        purpose: str = "unknown",
        background: bool = False,
        apply_anthropic_cache: bool = True,
        **kwargs: Any,
    ) -> dict:
        """Inner single-hop completion. ``complete_runtime`` adds failover."""
        provider_code = getattr(cfg.provider, "code", None)
        cached_messages = (
            _apply_anthropic_cache_control(
                messages,
                litellm_name=cfg.litellm_name,
                provider_code=provider_code,
            )
            if apply_anthropic_cache
            else messages
        )

        async def _call() -> Any:
            _disable_sdk_inner_retry(kwargs)
            return await litellm.acompletion(
                model=cfg.litellm_name,
                messages=cached_messages,
                api_key=cfg.provider.api_key,
                api_base=cfg.provider.base_url,
                temperature=_resolve_effective_temperature(
                    temperature, purpose, cfg.params.temperature,
                ),
                max_tokens=(
                    max_tokens if max_tokens is not None else cfg.params.max_tokens
                ),
                timeout=cfg.provider.timeout_ms / 1000,
                **kwargs,
            )

        # Background (post-response) calls run on an isolated, smaller semaphore
        # lane so a backlog of them can NEVER queue ahead of a foreground
        # request-path call on the shared provider lane (root-cause 2026-06-13:
        # async grounding backlog starved foreground ``generate`` → p95 24-37s).
        # Selection is driven SOLELY by the explicit ``background`` flag, never by
        # ``purpose`` — the same purpose ("grounding") runs BOTH a foreground
        # (sync, response-blocking) and a background (fire-and-forget) path, so
        # only the caller knows which lane is correct. The async judge passes
        # ``background=True``; the sync grounding call keeps the full lane.
        if background:
            sem = self._get_semaphore(
                f"{cfg.provider.code}::background",
                DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT,
            )
        else:
            sem = self._get_semaphore(
                cfg.provider.code,
                getattr(cfg.provider, "max_concurrent", DEFAULT_PROVIDER_MAX_CONCURRENT),
            )
        breaker = self._get_circuit_breaker(cfg.provider.code)
        if not breaker.can_execute():
            self._emit_cb_state(cfg.provider.code, breaker)
            raise LLMError(
                f"LLM provider {cfg.provider.code} circuit breaker OPEN — fast-fail",
            )
        t0 = time.monotonic()
        try:
            async with sem:
                resp = await retry_with_backoff(
                    _call,
                    policy=RetryPolicy(
                        max_attempts=_retry_attempts_for_purpose(purpose),
                        initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS,
                        max_backoff_ms=DEFAULT_RETRY_MAX_MS,
                    ),
                    retryable_exceptions=_RETRYABLE_LLM_EXCEPTIONS,
                )
            breaker.record_success()
            self._emit_cb_state(cfg.provider.code, breaker)
        except _RETRYABLE_LLM_EXCEPTIONS as exc:
            # Observability first — surface the provider's actual reason (status +
            # body snippet + model) so an operator can see WHY the call failed
            # instead of only the mapped LLMError. Pure logging: no control-flow
            # change, the raise below is unchanged.
            _log_external_call_failed(
                integration="llm",
                provider=provider_code,
                model=cfg.litellm_name,
                exc=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            # 429 = throttle, not outage → leave the breaker untouched (pacing,
            # not provider health). Only real outages count toward the trip.
            if not _is_rate_limit(exc):
                breaker.record_failure()
                self._emit_cb_state(cfg.provider.code, breaker)
            # Retries exhausted — surface as LLMError so callers map to 503.
            raise LLMError(f"LLM provider {cfg.provider.code} failed after retries: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — circuit breaker must record ANY provider failure type then reraise unchanged; narrowing risks hiding new litellm/openai/anthropic exception classes.
            _log_external_call_failed(
                integration="llm",
                provider=provider_code,
                model=cfg.litellm_name,
                exc=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            breaker.record_failure()
            self._emit_cb_state(cfg.provider.code, breaker)
            raise
        text = resp.choices[0].message.content or ""

        # Single extraction path (sync + stream + structured share helper).
        prompt_tokens, completion_tokens, cached_tokens = extract_usage_from_response(resp)
        # Cost-metering fallback: some upstream gateways (and proxies) omit the
        # ``usage`` block, so both counts return 0 → cost logs $0 (unmeasurable).
        # Estimate the missing count locally (tiktoken) from the prompt + answer
        # text so the cost audit has a usable figure. Never overwrites a real
        # provider count (only fills a 0).
        if prompt_tokens == 0 or completion_tokens == 0:
            prompt_tokens, completion_tokens = estimate_tokens_fallback(
                messages, text, prompt_tokens, completion_tokens,
            )

        # Observability (all-flows audit 2026-07-10): surface the provider's
        # completion finish_reason on the answer path. A truncated/dropped
        # completion (finish_reason not "stop"/"tool_calls", or a stop with a
        # near-empty body) is otherwise accepted silently as a normal answer —
        # this log makes it visible before a completeness guard is wired.
        if purpose == "generation":
            logger.info(
                "llm_generation_finish",
                provider=provider_code,
                finish_reason=resp.choices[0].finish_reason,
                completion_tokens=completion_tokens,
                text_len=len(text),
            )

        # Observability — provider-side prompt-cache hit ratio.
        if cached_tokens > 0 and prompt_cache_hits_total is not None:
            try:
                prompt_cache_hits_total.labels(
                    provider=provider_code or "unknown",
                    purpose=purpose,
                ).inc()
                prompt_cache_tokens_saved_total.labels(
                    provider=provider_code or "unknown",
                    purpose=purpose,
                ).inc(cached_tokens)
            except Exception:  # noqa: BLE001
                pass

        cost = compute_cost_usd(
            cfg.pricing, prompt_tokens, completion_tokens, cached_tokens,
        )
        # P33 / C.5 — emit per-tenant token usage AFTER successful call.
        # Uses contextvar to avoid coupling LLM call site to tenant id.
        await self._meter_tokens(prompt_tokens, completion_tokens)

        # Include model_name in payload so generate() can record which
        # model actually answered (request_logs.model_name + UI display).
        # Without this, request_logs.model_name = "unknown" always —
        # cost analytics couldn't attribute spend per model.
        _model_name = (
            getattr(cfg, "litellm_name", None)
            or getattr(cfg, "model", None)
            or getattr(cfg, "name", None)
            or "unknown"
        )
        # Token-log-center: query-path (runtime-cfg) call. cfg.pricing carries
        # the per-model unit prices → SNAPSHOT them so historical cost is frozen
        # when ai_models prices later change. mode defaults to 'query' (this is
        # the answer path) unless the route set the contextvar.
        _finished = datetime.now(timezone.utc)
        try:
            _pr = getattr(cfg, "pricing", None)
            self._ledger.emit(TokenLedgerEntry(
                mode=(mode_ctx.get() or "query"),
                action="llm",
                provider=(provider_code or None),
                model=_model_name,
                record_tenant_id=_safe_uuid(tenant_id_ctx.get()),
                record_bot_id=_safe_uuid(record_bot_id_ctx.get()),
                bot_id=(bot_id_ctx.get() or None),
                workspace_id=(workspace_id_ctx.get() or None),
                channel_type=(channel_type_ctx.get() or None),
                trace_id=(trace_id_ctx.get() or None),
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cached_tokens=cached_tokens,
                started_at=_finished,
                finished_at=_finished,
                input_unit_price=getattr(_pr, "input_per_1k_usd", None),
                output_unit_price=getattr(_pr, "output_per_1k_usd", None),
                cached_unit_price=getattr(_pr, "cached_input_per_1k_usd", None),
                cost_usd=(float(cost) or None),
            ))
        except Exception:  # noqa: BLE001 — ledger must never break the LLM path
            pass
        return {
            "text": text,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": float(cost),
            "finish_reason": resp.choices[0].finish_reason,
            "model_name": _model_name,
        }

    async def complete_runtime_stream(
        self,
        cfg: Any,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        purpose: str = "unknown",
        usage_sink: UsageSink | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming variant of ``complete_runtime`` — yields token deltas.

        Wraps ``litellm.acompletion(stream=True)`` and surfaces only the
        ``choices[0].delta.content`` payload as text. Anthropic
        ``cache_control`` is preserved for parity with the non-streaming
        path. Token / cost accounting is NOT emitted here — streaming
        responses don't expose final ``usage`` deltas across all providers,
        so the caller is responsible for any post-stream accumulation if it
        needs invocation_logger metrics.

        Per-provider Semaphore + retry policy are intentionally skipped —
        streaming generators can't be safely retried (partial output already
        consumed by the client). On transient error mid-stream the
        AsyncIterator surfaces the exception to the caller.

        SOFT CircuitBreaker. Pre-flight ``can_execute()`` check rejects fast
        when the provider's CB is OPEN (no LiteLLM call, no yield). Once we
        enter the iteration loop we DO NOT re-check — partial
        output already on the wire can't be replayed. Outcome accounting
        happens post-stream:

        - clean finish with ≥1 token → ``record_success``
        - empty stream (0 tokens) → ``record_failure`` (treat as upstream bug)
        - exception (timeout, 429, 500 …) → ``record_failure`` + raise
        - ``asyncio.CancelledError`` (client disconnect) → re-raise WITHOUT
          touching CB state — that's a client issue, not a provider fault.
        """
        provider_code = getattr(cfg.provider, "code", None)
        breaker = self._get_circuit_breaker(provider_code or "unknown")
        if not breaker.can_execute():
            self._emit_cb_state(provider_code or "unknown", breaker)
            raise LLMError(
                f"LLM provider {provider_code} circuit breaker OPEN — "
                f"streaming rejected pre-flight",
            )

        cached_messages = _apply_anthropic_cache_control(
            messages,
            litellm_name=cfg.litellm_name,
            provider_code=provider_code,
        )
        # OpenAI / Azure require ``stream_options={"include_usage": True}``
        # to emit a final ``usage`` chunk; Anthropic streams already include
        # one on ``message_stop`` and the flag is rejected by some LiteLLM
        # versions, so we only set it when the provider supports it.
        if (
            _supports_stream_include_usage(provider_code)
            and "stream_options" not in kwargs
        ):
            kwargs["stream_options"] = {"include_usage": True}
        try:
            _disable_sdk_inner_retry(kwargs)
            stream = await litellm.acompletion(
                model=cfg.litellm_name,
                messages=cached_messages,
                api_key=cfg.provider.api_key,
                api_base=cfg.provider.base_url,
                temperature=_resolve_effective_temperature(
                    temperature, purpose, cfg.params.temperature,
                ),
                max_tokens=(
                    max_tokens if max_tokens is not None else cfg.params.max_tokens
                ),
                timeout=cfg.provider.timeout_ms / 1000,
                stream=True,
                **kwargs,
            )
        except asyncio.CancelledError:
            # Client gave up before LiteLLM could even open the stream — not
            # a provider fault, so the CB stays untouched.
            raise
        except Exception as exc:  # noqa: BLE001
            # 429 on stream-open = throttle, not outage → don't trip the shared
            # provider breaker (mirrors the non-stream paths).
            if not _is_rate_limit(exc):
                breaker.record_failure()
                self._emit_cb_state(provider_code or "unknown", breaker)
            raise LLMError(
                f"litellm stream failed for provider={provider_code}: {exc}",
            ) from exc

        tokens_yielded = 0
        # P33 / C.5 — accumulate token totals across the stream so we
        # emit ONE meter increment after the iterator drains (not per
        # chunk — Redis hash hammering on long completions).
        prompt_total = 0
        completion_total = 0
        cached_total = 0
        # OBS-F6 — accumulate the yielded answer text so the post-stream cost
        # fallback can tiktoken-estimate completion tokens when the provider omits
        # the ``usage`` chunk (some upstream gateways). Cheap list append; joined once
        # at the end ONLY when the estimate is actually needed (a real usage payload
        # skips it). Mirrors the sync path, which already holds the full answer text.
        answer_parts: list[str] = []
        finish_reason: str | None = None
        try:
            async for chunk in stream:
                try:
                    delta = chunk.choices[0].delta.content
                except (AttributeError, IndexError):
                    delta = None
                if delta:
                    tokens_yielded += 1
                    answer_parts.append(delta)
                    yield delta
                # ``finish_reason`` lives on the choice, not on usage. Capture
                # it whenever a chunk surfaces one so the post-stream sink can
                # log it alongside token totals.
                try:
                    fr = chunk.choices[0].finish_reason
                    if fr:
                        finish_reason = fr
                except (AttributeError, IndexError):
                    pass
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    # LiteLLM emits cumulative usage on the final chunk
                    # for most providers; overwrite (not add) so we don't
                    # double-count partial deltas upstream.
                    p, c, cached = extract_usage_from_response(chunk)
                    prompt_total = p
                    completion_total = c
                    cached_total = cached
        except asyncio.CancelledError:
            # Client disconnect mid-stream — penalising the provider here
            # would let a flaky client trip the breaker. Re-raise untouched.
            raise
        except Exception:  # noqa: BLE001 — streaming circuit breaker must record ANY provider failure type then reraise unchanged; narrowing risks hiding new litellm exception classes.
            breaker.record_failure()
            self._emit_cb_state(provider_code or "unknown", breaker)
            raise

        if tokens_yielded == 0:
            # Provider opened the stream but never emitted a token — treat as
            # an upstream failure (SLA violation) and let the breaker count it.
            breaker.record_failure()
            self._emit_cb_state(provider_code or "unknown", breaker)
            raise LLMError(
                f"litellm stream for provider={provider_code} yielded 0 tokens",
            )

        breaker.record_success()
        self._emit_cb_state(provider_code or "unknown", breaker)

        # Provider-side prompt-cache observability — same path as sync.
        if cached_total > 0 and prompt_cache_hits_total is not None:
            try:
                prompt_cache_hits_total.labels(
                    provider=provider_code or "unknown",
                    purpose=purpose,
                ).inc()
                prompt_cache_tokens_saved_total.labels(
                    provider=provider_code or "unknown",
                    purpose=purpose,
                ).inc(cached_total)
            except Exception:  # noqa: BLE001
                pass

        # OBS-F6 — cost-metering fallback (parity with the sync path): some
        # upstream gateways (and proxies) omit the streaming ``usage`` chunk,
        # so both totals stay 0 → streamed generation logs $0 (unmeasurable — the
        # HOTTEST call path). Estimate the missing count locally (tiktoken) from the
        # prompt messages + the accumulated answer text so the cost audit has a
        # usable figure. Never overwrites a REAL provider count (only fills a 0).
        if prompt_total == 0 or completion_total == 0:
            prompt_total, completion_total = estimate_tokens_fallback(
                messages, "".join(answer_parts), prompt_total, completion_total,
            )

        # P33 — single meter increment after stream completes. Tolerated
        # zero-totals (provider didn't expose usage) keeps month bucket
        # ticking rather than silently dropping the call.
        await self._meter_tokens(prompt_total, completion_total)

        # Compute cost from the per-call pricing row and forward to the
        # caller's sink so ``invocation_logger`` records non-zero values for
        # streamed generation. Sink is best-effort — exceptions never break
        # the iterator (the stream is already drained at this point).
        cost_decimal = compute_cost_usd(
            getattr(cfg, "pricing", None),
            prompt_total,
            completion_total,
            cached_total,
        )
        if usage_sink is not None:
            try:
                result = usage_sink(
                    prompt_total,
                    completion_total,
                    cached_total,
                    float(cost_decimal),
                    finish_reason or "stop",
                )
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.debug("stream_usage_sink_failed", err=str(exc))

        # Mark for low-cardinality observability — purpose label is logged here
        # (not emitted as metric since streaming has no final usage payload).
        logger.debug(
            "litellm_stream_finished",
            provider=provider_code,
            purpose=purpose,
            tokens_yielded=tokens_yielded,
            prompt_tokens=prompt_total,
            completion_tokens=completion_total,
            cached_tokens=cached_total,
            cost_usd=float(cost_decimal),
        )

    async def complete(  # type: ignore[override]
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Dual-mode entrypoint.

        - ``complete(cfg, messages, **kw)`` → runtime-config mode, returns dict.
        - ``complete(messages, *, spec, record_tenant_id, trace_id, ...)`` → LLMPort mode.
        """
        # Detect runtime-mode: first positional arg has `.litellm_name`.
        if args and hasattr(args[0], "litellm_name"):
            cfg = args[0]
            msgs = args[1] if len(args) > 1 else kwargs.pop("messages")
            return await self.complete_runtime(cfg, msgs, **kwargs)
        # Legacy LLMPort mode
        messages = args[0] if args else kwargs.pop("messages")
        return await self._complete_via_llmport(messages, **kwargs)

    async def _complete_via_llmport(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,  # noqa: ARG002 — used for trace tags
        trace_id: TraceId,  # noqa: ARG002
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        await self._maybe_refresh()
        litellm_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        kwargs = spec.to_litellm_kwargs()
        if response_schema is not None:
            try:
                kwargs["response_format"] = {"type": "json_object"}
            except Exception:  # noqa: BLE001
                pass

        # P25-L4: retry on retryable transient LLM errors. Non-retryable
        # exceptions (auth, bad request) propagate immediately as LLMError.
        async def _call() -> Any:
            _disable_sdk_inner_retry(kwargs)
            return await litellm.acompletion(
                messages=litellm_messages,
                **kwargs,
            )

        provider_code = getattr(spec, "provider", None) or "unknown"
        # TPM throttle (pace, don't storm): wait until this model's trailing-60s
        # token budget admits the call, THEN fire. A burst of ingest enrichment
        # calls queues here instead of 429-ing + retry-storming. Keyed by model
        # so cheap ingest (nano) paces on its own bucket and never throttles the
        # live answer model (mini).
        await _TPM_LIMITER.acquire(
            kwargs.get("model") or provider_code,
            estimate_request_tokens(litellm_messages, kwargs.get("max_tokens", 0)),
        )
        breaker = self._get_circuit_breaker(provider_code)
        if not breaker.can_execute():
            self._emit_cb_state(provider_code, breaker)
            raise LLMError(
                f"LLM provider {provider_code} circuit breaker OPEN — fast-fail",
            )
        try:
            t0 = time.monotonic()
            resp = await retry_with_backoff(
                _call,
                policy=RetryPolicy(
                    max_attempts=DEFAULT_RETRY_MAX_ATTEMPTS,
                    initial_backoff_ms=DEFAULT_RETRY_INITIAL_MS,
                    max_backoff_ms=DEFAULT_RETRY_MAX_MS,
                ),
                retryable_exceptions=_RETRYABLE_LLM_EXCEPTIONS,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            breaker.record_success()
            self._emit_cb_state(provider_code, breaker)
        except _RETRYABLE_LLM_EXCEPTIONS as exc:
            _log_external_call_failed(
                integration="llm",
                provider=provider_code,
                model=kwargs.get("model") or getattr(spec, "model_name", None),
                exc=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            # 429 = throttle, not outage → don't let an ingest enrichment burst
            # (nano) OPEN the shared provider breaker and starve the answer model.
            if not _is_rate_limit(exc):
                breaker.record_failure()
                self._emit_cb_state(provider_code, breaker)
            raise LLMError(f"litellm call failed after retries: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            _log_external_call_failed(
                integration="llm",
                provider=provider_code,
                model=kwargs.get("model") or getattr(spec, "model_name", None),
                exc=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            breaker.record_failure()
            self._emit_cb_state(provider_code, breaker)
            raise LLMError(f"litellm call failed: {exc}") from exc

        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = getattr(resp, "usage", None) or {}
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = float(getattr(resp, "_hidden_params", {}).get("response_cost", 0.0) or 0.0)

        # Token ledger — EVERY money-action (LLM call) emits the 5 tracking
        # numbers so BOTH the upload/ingest flow and the query flow are
        # auditable. This is the choke point for non-streaming calls (ingest
        # CR enrichment + narrate + grade/grounding); the streaming query path
        # additionally records to monitoring_log via UsageSink. ``mode`` comes
        # from the contextvar set by the worker/route entrypoint — NO guessing
        # flow from the model name. Identity is snapshot from contextvars so the
        # ledger row survives bot/document delete (no FK, no JOIN).
        _finished = datetime.now(timezone.utc)
        try:
            self._ledger.emit(TokenLedgerEntry(
                mode=mode_ctx.get() or "unknown",
                action="llm",
                provider=spec.provider,
                model=spec.model_name,
                record_tenant_id=_safe_uuid(tenant_id_ctx.get()),
                record_bot_id=_safe_uuid(record_bot_id_ctx.get()),
                bot_id=(bot_id_ctx.get() or None),
                workspace_id=(workspace_id_ctx.get() or None),
                channel_type=(channel_type_ctx.get() or None),
                trace_id=(trace_id_ctx.get() or None),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                total_tokens=tokens_in + tokens_out,
                started_at=_finished - timedelta(milliseconds=int(latency_ms)),
                finished_at=_finished,
                duration_ms=int(latency_ms),
                cost_usd=(cost or None),
            ))
        except Exception:  # noqa: BLE001 — ledger must never break the LLM path
            pass

        structured = None
        if response_schema is not None and content:
            try:
                structured = response_schema.model_validate(json.loads(content))
            except Exception:  # noqa: BLE001
                logger.warning("llm_structured_parse_failed", model=spec.model_name)

        return LLMResponse(
            content=content,
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency_ms,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
            structured=structured,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,  # noqa: ARG002
        trace_id: TraceId,  # noqa: ARG002
    ) -> AsyncIterator[str]:
        await self._maybe_refresh()
        litellm_messages = [{"role": m.role, "content": m.content} for m in messages]
        kwargs = spec.to_litellm_kwargs()
        kwargs["stream"] = True
        try:
            _disable_sdk_inner_retry(kwargs)
            stream = await litellm.acompletion(messages=litellm_messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"litellm stream failed: {exc}") from exc

        async def _aiter() -> AsyncIterator[str]:
            async for chunk in stream:
                try:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                except (AttributeError, IndexError):
                    continue

        return _aiter()

    async def close(self) -> None:
        # litellm doesn't expose a close; clients are http-based and managed internally
        pass


__all__ = [
    "DynamicLiteLLMRouter",
    "UsageSink",
    "compute_cost_usd",
    "extract_usage_from_response",
]
