"""HTTP webhook notifier — emit ``bot_quota_exhausted`` to an operator-
configured upstream URL.

Domain-neutral: the upstream URL + auth token live in ``system_config`` /
env vars; the notifier does NOT bake in any specific tenant or platform.

Sacred invariants:

* **Throttle** — Redis SETNX 1h per bot. Prevents alert-storm when many
  bots exhaust their token quota simultaneously.
* **Empty URL = silently disabled** — dev/local without env vars must
  not error; ``send_quota_exhausted`` returns ``False``.
* **Failure soft** — ``httpx`` error logged, NOT raised. The chat path
  is independent of the notify path (CLAUDE.md graceful-degradation:
  aux dependency MUST NOT crash app).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import structlog

from ragbot.application.ports.notify_channel_port import NotifyChannelPort
from ragbot.shared.constants import DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S

logger = structlog.get_logger(__name__)

# Post timeout — keep tight; notify path is best-effort.
_POST_TIMEOUT_S = 10.0
# Successful POST status codes — generic 2xx for any webhook receiver.
_OK_STATUS = frozenset({200, 201, 202, 204})


class WebhookNotifier(NotifyChannelPort):
    """HTTP webhook → operator-configured URL. Throttled per bot via Redis SETNX."""

    def __init__(
        self,
        *,
        url: str,
        auth_token: str,
        redis_client: Any,
        throttle_s: int = DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S,
    ) -> None:
        self._url = (url or "").strip()
        self._auth = (auth_token or "").strip()
        self._redis = redis_client
        self._throttle = int(throttle_s)

    async def send_quota_exhausted(
        self,
        *,
        record_tenant_id: UUID,
        record_bot_id: UUID,
        bot_name: str,
        tokens_used: int,
        effective_limit: int,
    ) -> bool:
        if not self._url:
            # Silently disabled — no env config.
            return False

        # Throttle gate: SETNX with TTL = "lock" key for this bot.
        # If Redis lookup fails, we fall back to *sending* — safer to
        # over-alert (operator gets noise) than under-alert (operator
        # blind to billing event).
        throttle_key = f"ragbot:notify:quota:{record_bot_id}"
        try:
            nx_result = await self._redis.set(
                throttle_key, "1", nx=True, ex=self._throttle,
            )
        except Exception as exc:  # noqa: BLE001 — Redis fail = always send (safer than miss)
            logger.warning(
                "notify_throttle_redis_failed",
                error=str(exc)[:200],
                record_bot_id=str(record_bot_id),
            )
            nx_result = True  # allow send if throttle check unavailable

        # Redis ``set(..., nx=True)`` returns ``True`` on first write,
        # ``None`` (or ``False``) when the key already existed → throttled.
        if nx_result is None or nx_result is False:
            return False

        headers = {"Content-Type": "application/json"}
        if self._auth:
            headers["Authorization"] = f"Bearer {self._auth}"

        payload = {
            "event": "bot_quota_exhausted",
            "record_tenant_id": str(record_tenant_id),
            "record_bot_id": str(record_bot_id),
            "bot_name": bot_name,
            "tokens_used": int(tokens_used),
            "effective_limit": int(effective_limit),
            "timestamp": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z",
            ),
        }

        try:
            async with httpx.AsyncClient(timeout=_POST_TIMEOUT_S) as client:
                resp = await client.post(self._url, headers=headers, json=payload)
            ok = resp.status_code in _OK_STATUS
            logger.info(
                "notify_webhook_sent",
                record_bot_id=str(record_bot_id),
                status=resp.status_code,
                ok=ok,
            )
            return ok
        except (httpx.HTTPError, OSError) as exc:
            logger.warning(
                "notify_webhook_failed",
                error=str(exc)[:200],
                record_bot_id=str(record_bot_id),
            )
            return False


class NullNotifier(NotifyChannelPort):
    """No-op notifier — used when env vars empty (CLAUDE.md Null Object)."""

    async def send_quota_exhausted(
        self,
        *,
        record_tenant_id: UUID,
        record_bot_id: UUID,
        bot_name: str,
        tokens_used: int,
        effective_limit: int,
    ) -> bool:
        return False


__all__ = ["NullNotifier", "WebhookNotifier"]
