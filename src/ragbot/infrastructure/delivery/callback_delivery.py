"""Callback delivery — POST answer to caller's API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import json as _json
import time
from typing import Any

import httpx
import structlog

from ragbot.shared.callback_validator import _is_url_safe
from ragbot.shared.constants import (
    DEFAULT_CALLBACK_BACKOFF_BASE_S,
    DEFAULT_CALLBACK_MAX_RETRIES,
    DEFAULT_CALLBACK_SSRF_GUARD_ENABLED,
    DEFAULT_CALLBACK_TIMEOUT_S,
)

logger = structlog.get_logger(__name__)


class CallbackDelivery:
    """POST result to callback_url with HMAC signing + retry.

    Finding #12 perf fix: an ``httpx.AsyncClient`` is lazily created
    once per dispatcher instance and reused across both retry attempts
    AND multiple ``deliver()`` calls. Building a fresh client per
    attempt forced a fresh TCP handshake (+ TLS for HTTPS) every time
    — costly when the caller retries a slow endpoint. ``aclose()``
    closes the underlying pool on shutdown.
    """

    def __init__(
        self,
        callback_url: str,
        hmac_secret: str = "",
        max_retries: int = DEFAULT_CALLBACK_MAX_RETRIES,
        timeout_s: int = DEFAULT_CALLBACK_TIMEOUT_S,
        verify_ssl: bool = True,
        backoff_base_s: float = DEFAULT_CALLBACK_BACKOFF_BASE_S,
        ssrf_guard_enabled: bool = DEFAULT_CALLBACK_SSRF_GUARD_ENABLED,
    ) -> None:
        self._callback_url = callback_url
        self._hmac_secret = hmac_secret
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._verify_ssl = verify_ssl
        self._backoff_base_s = backoff_base_s
        # Deliver-time SSRF guard — re-resolve the host and reject
        # private/internal IPs right before the POST. Closes the
        # DNS-rebinding window left open by setup-time-only validation.
        self._ssrf_guard_enabled = ssrf_guard_enabled
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-create the shared ``AsyncClient`` on first use.

        Locked so concurrent ``deliver()`` invocations race once at
        most. Subsequent calls fall through the fast (``self._client is
        not None``) path with zero contention.
        """
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self._timeout_s, verify=self._verify_ssl,
                )
        return self._client

    async def aclose(self) -> None:
        """Release the pooled connection on dispatcher shutdown."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def deliver(self, result: dict[str, Any]) -> bool:
        """POST answer to callback_url with exponential backoff retry.

        If hmac_secret is provided, signs the payload with HMAC-SHA256 and
        attaches X-Ragbot-Signature / X-Ragbot-Timestamp headers.
        """
        # Deliver-time SSRF guard — re-resolve the host NOW (not just at
        # setup) so a DNS-rebinding flip to an internal IP between
        # validation and delivery cannot reach RFC1918 / loopback /
        # link-local / cloud-metadata (169.254.169.254) targets.
        if self._ssrf_guard_enabled:
            safe, reason = await _is_url_safe(self._callback_url)
            if not safe:
                logger.warning(
                    "callback_ssrf_blocked_at_deliver",
                    url=self._callback_url[:80],
                    reason=reason,
                )
                return False

        body_bytes = _json.dumps(result, ensure_ascii=False).encode("utf-8")

        # Build headers — include HMAC signature if secret configured
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Ragbot-Webhook/1.0",
        }
        if self._hmac_secret:
            timestamp = str(int(time.time()))
            signing_input = f"{timestamp}.".encode("utf-8") + body_bytes
            signature = _hmac.new(
                self._hmac_secret.encode("utf-8"),
                signing_input,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Ragbot-Signature"] = f"sha256={signature}"
            headers["X-Ragbot-Timestamp"] = timestamp

        url = self._callback_url
        client = await self._get_client()
        for attempt in range(self._max_retries):
            try:
                resp = await client.post(url, content=body_bytes, headers=headers)
                if resp.status_code < 400:
                    logger.info(
                        "callback_delivered",
                        url=url[:80],
                        status=resp.status_code,
                        attempt=attempt + 1,
                    )
                    return True
                logger.warning(
                    "callback_failed_status",
                    url=url[:80],
                    status=resp.status_code,
                    attempt=attempt + 1,
                )
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "callback_failed",
                    url=url[:80],
                    error=str(exc)[:100],
                    attempt=attempt + 1,
                )
            if attempt < self._max_retries - 1:
                # Exponential backoff: base * 2^attempt (e.g. 1s, 2s, 4s).
                await asyncio.sleep(self._backoff_base_s * (2**attempt))
        logger.error("callback_exhausted", url=url[:80], max_retries=self._max_retries)
        return False

    @property
    def mode_name(self) -> str:
        return "callback"
