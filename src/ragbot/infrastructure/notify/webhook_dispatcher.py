"""Webhook dispatcher — POSTs error alerts to the configured channel.

Design constraints:

* Self-contained — never reraise to the caller; the alert path must
  not break the business logic that triggered it.
* Bounded — semaphore caps concurrency at one in-flight POST per
  process so an error storm cannot flood the upstream.
* Deduped — same (severity, component, message-prefix) within
  ``DEFAULT_NOTIFY_DEDUP_WINDOW_S`` collapses to a single send.
* Rate-limited — sliding minute counter caps total outbound dispatch
  rate to ``DEFAULT_NOTIFY_RATE_LIMIT_PER_MIN``.

The dispatcher does NOT decide *whether* an error is worth alerting on
— that lives in ``ErrorNotifyHook``. Here we only own the wire format
and the back-pressure controls.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import structlog
from redis.exceptions import RedisError  # audit O5: redis-py raises its OWN hierarchy

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.application.services.notify_channel_resolver import (
    NotifyChannelResolver,
)
from ragbot.infrastructure.observability.metrics import (
    notify_dispatch_failed_total,
    notify_dropped_total,
    notify_sent_total,
)
from ragbot.shared.constants import (
    DEFAULT_NOTIFY_CONCURRENCY,
    DEFAULT_NOTIFY_DEDUP_WINDOW_S,
    DEFAULT_NOTIFY_RATE_LIMIT_PER_MIN,
    NOTIFY_MESSAGE_TRUNCATE_CHARS,
    NOTIFY_REDIS_DEDUP_PREFIX,
    NOTIFY_REDIS_RATE_LIMIT_PREFIX,
)

logger = structlog.get_logger(__name__)

# Backoff envelope for in-band retry (5xx / timeout / network).
_RETRY_INITIAL_BACKOFF_S = 0.2
_RETRY_MAX_BACKOFF_S = 1.0
_RETRY_BACKOFF_MULTIPLIER = 2.0

# Sliding rate-limit bucket — one Redis key per minute. The TTL grace
# (``_BUCKET_TTL_GRACE_S``) prevents a key from expiring mid-tick during
# a clock skew between Redis and the worker.
_BUCKET_SIZE_S = 60
_BUCKET_TTL_GRACE_S = 5

# HTTP status class boundaries — semantic per RFC 7231. Lifted out of
# the inline ``response.status_code`` checks so the contract is visible.
_STATUS_2XX_FLOOR = 200
_STATUS_3XX_FLOOR = 300
_STATUS_4XX_FLOOR = 400
_STATUS_5XX_FLOOR = 500
_STATUS_6XX_FLOOR = 600


class WebhookNotifyDispatcher:
    """Send notifications to the resolved channel — fire and forget.

    Callers schedule via ``asyncio.create_task(dispatcher.dispatch(...))``
    so the originating thread is never held for the duration of the
    POST + retry envelope. Any exception inside ``dispatch`` is logged
    and counted; nothing propagates.
    """

    def __init__(
        self,
        httpx_client: httpx.AsyncClient | None,
        resolver: NotifyChannelResolver,
        redis_client,
        *,
        concurrency: int = DEFAULT_NOTIFY_CONCURRENCY,
        rate_limit_per_min: int = DEFAULT_NOTIFY_RATE_LIMIT_PER_MIN,
        dedup_window_s: int = DEFAULT_NOTIFY_DEDUP_WINDOW_S,
    ) -> None:
        # When the caller does not pre-build a client (production wiring
        # passes a long-lived shared client), we lazy-create one on first
        # send so unit tests can substitute via ``MockTransport``.
        self._client = httpx_client
        self._resolver = resolver
        self._redis = redis_client
        self._semaphore = asyncio.Semaphore(concurrency)
        self._rate_limit_per_min = rate_limit_per_min
        self._dedup_window_s = dedup_window_s

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, lazy-creating if not injected."""
        if self._client is None:
            # Default timeout is overridden per call from
            # ``NotifyChannelConfig.timeout_s``; this is just the
            # connection-pool baseline.
            self._client = httpx.AsyncClient()
        return self._client

    async def dispatch(
        self,
        *,
        severity: str,
        component: str,
        message: str,
        record_tenant_id: UUID | None = None,
        record_bot_id: UUID | None = None,
        request_id: UUID | None = None,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        """Send one alert. Returns a status dict; never raises.

        The dict is for callers that want to surface the upstream
        outcome (e.g. ``POST /admin/notify-channel/test``); production
        fire-and-forget callers ignore it.
        """
        outcome: dict[str, Any] = {
            "dispatched": False,
            "reason": None,
            "upstream_status": None,
        }

        cfg, source = await self._resolver.resolve()
        if cfg is None:
            notify_dropped_total.labels(reason="unconfigured").inc()
            outcome["reason"] = "unconfigured"
            return outcome
        if not cfg.enabled:
            notify_dropped_total.labels(reason="disabled").inc()
            outcome["reason"] = "disabled"
            return outcome

        # Dedup — same (severity, component, message-prefix) within the
        # window collapses to a single POST. Hash keeps the Redis key
        # short and side-steps any binary content concerns.
        dedup_key = self._build_dedup_key(severity, component, message)
        if await self._is_duplicate(dedup_key):
            notify_dropped_total.labels(reason="dedup").inc()
            outcome["reason"] = "dedup"
            return outcome

        if await self._is_rate_limited():
            notify_dropped_total.labels(reason="rate_limit").inc()
            outcome["reason"] = "rate_limit"
            return outcome

        body = self._build_body(
            severity=severity,
            component=component,
            message=message,
            record_bot_id=record_bot_id,
            error_type=error_type,
        )
        url = cfg.render_url()

        async with self._semaphore:
            status = await self._post_with_retry(cfg, url=url, body=body, source=source)

        if status is not None and _STATUS_2XX_FLOOR <= status < _STATUS_3XX_FLOOR:
            notify_sent_total.labels(
                component=component,
                severity=severity,
            ).inc()
            outcome["dispatched"] = True
            outcome["upstream_status"] = status
        else:
            outcome["upstream_status"] = status

        return outcome

    # ------------------------------------------------------------------
    # Wire format + transport
    # ------------------------------------------------------------------

    @staticmethod
    def _build_body(
        *,
        severity: str,
        component: str,
        message: str,
        record_bot_id: UUID | None,
        error_type: str | None,
    ) -> dict[str, Any]:
        """Compose the upstream JSON body — flat ``content`` string.

        Truncation prevents a single noisy stack trace from flooding the
        chat UI that consumes the webhook.
        """
        truncated = (message or "")[:NOTIFY_MESSAGE_TRUNCATE_CHARS]
        bot_label = str(record_bot_id) if record_bot_id is not None else "n/a"
        err_label = error_type or "Error"
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        content = (
            f"[RAGBOT-ALERT] {severity}: {err_label} @ {component}\n"
            f"msg: {truncated}\n"
            f"bot: {bot_label}\n"
            f"time: {timestamp}"
        )
        return {"content": content, "message_type": "text"}

    async def _post_with_retry(
        self,
        cfg: NotifyChannelConfig,
        *,
        url: str,
        body: dict[str, Any],
        source: str,
    ) -> int | None:
        """Run the bounded POST + retry envelope. Returns final HTTP
        status, or ``None`` when the request never produced a response
        (timeout / network / exhausted retries)."""
        client = await self._get_client()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Key": cfg.webhook_key,
        }
        attempts_total = max(0, cfg.max_retries) + 1  # initial + retries
        backoff = _RETRY_INITIAL_BACKOFF_S
        last_status: int | None = None

        for attempt in range(attempts_total):
            try:
                response = await client.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=cfg.timeout_s,
                )
            except httpx.TimeoutException as exc:
                notify_dispatch_failed_total.labels(status_class="timeout").inc()
                logger.warning(
                    "notify_dispatch_timeout",
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    source=source,
                )
                last_status = None
            except (httpx.NetworkError, httpx.HTTPError) as exc:
                # ``httpx.HTTPError`` is the base for transport + status
                # errors; ``HTTPStatusError`` does NOT escape from
                # ``client.post`` unless ``raise_for_status`` is called,
                # which we deliberately avoid (we read ``status_code``).
                notify_dispatch_failed_total.labels(status_class="network").inc()
                logger.warning(
                    "notify_dispatch_network_error",
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    err=str(exc),
                    source=source,
                )
                last_status = None
            else:
                last_status = response.status_code
                if _STATUS_2XX_FLOOR <= response.status_code < _STATUS_3XX_FLOOR:
                    return response.status_code
                if _STATUS_4XX_FLOOR <= response.status_code < _STATUS_5XX_FLOOR:
                    # 4xx is a config bug — retry would not help and
                    # would amplify the bad request load.
                    notify_dispatch_failed_total.labels(status_class="4xx").inc()
                    logger.warning(
                        "notify_dispatch_4xx",
                        status=response.status_code,
                        source=source,
                    )
                    return response.status_code
                if _STATUS_5XX_FLOOR <= response.status_code < _STATUS_6XX_FLOOR:
                    notify_dispatch_failed_total.labels(status_class="5xx").inc()
                    logger.warning(
                        "notify_dispatch_5xx",
                        attempt=attempt + 1,
                        status=response.status_code,
                        source=source,
                    )
                else:
                    # 1xx / 3xx are unexpected for this contract;
                    # treat as transient and retry.
                    notify_dispatch_failed_total.labels(status_class="5xx").inc()
                    logger.warning(
                        "notify_dispatch_unexpected_status",
                        attempt=attempt + 1,
                        status=response.status_code,
                        source=source,
                    )

            # Sleep + grow backoff only when another attempt remains.
            if attempt + 1 < attempts_total:
                await asyncio.sleep(backoff)
                backoff = min(
                    backoff * _RETRY_BACKOFF_MULTIPLIER,
                    _RETRY_MAX_BACKOFF_S,
                )

        return last_status

    # ------------------------------------------------------------------
    # Back-pressure helpers — Redis-backed for cross-process correctness
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dedup_key(severity: str, component: str, message: str) -> str:
        """Hash (severity, component, message-prefix) into a stable key.

        The slice on ``message`` keeps the dedup window tight enough to
        let semantically-different errors through while collapsing
        repeated identical traces. Prefix length is fixed (200 chars).
        """
        sample = (message or "")[:200]
        digest = hashlib.sha256(
            f"{severity}|{component}|{sample}".encode("utf-8"),
        ).hexdigest()
        return f"{NOTIFY_REDIS_DEDUP_PREFIX}{digest}"

    async def _is_duplicate(self, dedup_key: str) -> bool:
        """Set the Redis key with NX — failure means a duplicate."""
        try:
            stored = await self._redis.set(
                dedup_key,
                "1",
                ex=self._dedup_window_s,
                nx=True,
            )
        except (OSError, RedisError) as exc:
            # Cache outage — fail open (allow the alert through). The
            # rate limiter is the second layer that still bounds storms.
            logger.warning(
                "notify_dedup_check_failed",
                error_type=type(exc).__name__,
                err=str(exc),
            )
            return False
        # ``redis-py`` returns truthy on successful SET; ``None``/False
        # means the key was already present.
        return not stored

    async def _is_rate_limited(self) -> bool:
        """Sliding minute counter — drop when over the per-minute cap."""
        bucket = int(time.time()) // _BUCKET_SIZE_S
        key = f"{NOTIFY_REDIS_RATE_LIMIT_PREFIX}{bucket}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                # First write to a fresh bucket — set a TTL so it expires
                # naturally; the grace covers minor clock skew.
                await self._redis.expire(key, _BUCKET_SIZE_S + _BUCKET_TTL_GRACE_S)
        except (OSError, RedisError) as exc:
            logger.warning(
                "notify_rate_limit_check_failed",
                error_type=type(exc).__name__,
                err=str(exc),
            )
            return False
        return count > self._rate_limit_per_min


__all__ = ["WebhookNotifyDispatcher"]
