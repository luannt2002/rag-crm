"""Error notify hook — surface AI/API failures after retry exhaust.

The hook is a thin policy wrapper over ``WebhookNotifyDispatcher``: it
maps an exception type to a severity, picks the appropriate component
label, and schedules a fire-and-forget ``dispatch`` task. It is wired
into the **outermost** error catch sites of the chat + ingest workers
so retry, circuit-breaker, and provider-failover have already had a
chance to recover before an alert fires.

Design rules:

* Hook MUST not break business logic — every code path is wrapped so
  the originating ``raise`` continues to propagate even if the alert
  scheduling itself fails.
* Hook MUST NOT inject text into LLM prompts or override LLM answers
  (CLAUDE.md MINDSET — this is a side-effect-only observability hook).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from ragbot.shared.errors import CircuitBreakerOpen, InfrastructureError

logger = structlog.get_logger(__name__)


class ErrorNotifyHook:
    """Schedule webhook dispatch for unrecoverable AI / API errors."""

    def __init__(self, dispatcher: Any) -> None:
        # ``dispatcher`` is duck-typed as any object exposing
        # ``async dispatch(severity=..., component=..., message=..., ...)``;
        # production passes ``WebhookNotifyDispatcher``. The looser type
        # keeps unit tests free to inject a stub without inheriting.
        self._dispatcher = dispatcher
        # Strong-reference set per Python asyncio docs — the event loop
        # keeps only weak refs to ``create_task`` results, so a
        # fire-and-forget caller that drops the handle risks GC dropping
        # the task before the dispatch coroutine finishes its webhook
        # round-trip. The done-callback releases the entry so the set
        # stays bounded by inflight count.
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    async def on_ai_error(
        self,
        *,
        error: BaseException,
        component: str,
        record_tenant_id: UUID | None = None,
        record_bot_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> asyncio.Task[Any] | None:
        """Schedule a dispatch task for *error*; return the task handle.

        The task is intentionally not awaited — the alert path is
        fire-and-forget so the caller's ``raise`` is not blocked on a
        webhook round-trip. Returning the task lets unit tests assert
        scheduling without polling.

        Returns ``None`` when scheduling itself failed; never raises.
        """
        try:
            severity = self._severity_for(error)
            error_type = type(error).__name__
            message = self._format_message(error)
            coro = self._dispatcher.dispatch(
                severity=severity,
                component=component,
                message=message,
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                request_id=request_id,
                error_type=error_type,
            )
            task = asyncio.create_task(coro)
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            return task
        except Exception as schedule_exc:  # noqa: BLE001 — alert path must not break business logic; log + swallow.
            logger.warning(
                "error_notify_hook_schedule_failed",
                error_type=type(schedule_exc).__name__,
                err=str(schedule_exc),
                component=component,
            )
            return None

    @staticmethod
    def _severity_for(error: BaseException) -> str:
        """Map error class → alert severity.

        ``CircuitBreakerOpen`` indicates a sustained outage — the
        retry+CB layer has already given up — so it earns the higher
        severity. Other ``InfrastructureError`` subtypes (LLM /
        embedder / retrieval / ingest) are routine post-retry failures.
        """
        if isinstance(error, CircuitBreakerOpen):
            return "critical"
        if isinstance(error, InfrastructureError):
            return "error"
        return "error"

    @staticmethod
    def _format_message(error: BaseException) -> str:
        """Return ``str(error)`` with a trivial fallback for empty msgs."""
        text = str(error).strip()
        if text:
            return text
        return type(error).__name__


__all__ = ["ErrorNotifyHook"]
