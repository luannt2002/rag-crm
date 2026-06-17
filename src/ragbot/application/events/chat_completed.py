"""In-process hook system for chat completion side-effects.

Zero external dependency — just Protocol + dataclass + asyncio.

Pattern: Port + Registry (mirrors Ragbot's reranker_resolver, embedder).
Caller (chat_worker.py) gọi 4 dòng để fire side-effects. Adding new
hook = 1 file + 1 dòng bootstrap, KHÔNG sửa chat_worker.

Two-stage commit guarantee Redis+DB sync:
  Stage 1: DB hooks run inside open transaction (caller's session)
  Stage 2: post_commit hooks run AFTER session.commit() succeeds
  Order prevents Redis drift if DB commit fails.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Sequence, Any
from uuid import UUID

import structlog

from ragbot.shared.constants import (
    DEFAULT_CHAT_HOOK_MAX_CONCURRENCY,
    DEFAULT_CHAT_HOOK_TIMEOUT_S,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ChatCompletedEvent:
    """Immutable post-chat event payload."""
    record_tenant_id: UUID
    workspace_id: str
    bot_id: str
    channel_type: str
    record_bot_id: UUID
    request_id: UUID
    prompt_tokens: int
    completion_tokens: int
    tokens_used_delta: int
    refusal_reason: str | None
    intent: str | None
    timestamp_iso: str


@runtime_checkable
class ChatCompletionHookPort(Protocol):
    """One hook = one side-effect.

    Implementations:
      - infrastructure/chat_hooks/token_usage_db_hook.py    (stage='db')
      - infrastructure/chat_hooks/token_usage_redis_hook.py (stage='post_commit')
      - infrastructure/chat_hooks/quota_threshold_notify_hook.py (stage='post_commit')
    """

    @property
    def hook_name(self) -> str: ...

    @property
    def stage(self) -> str:
        """'db' (in transaction) or 'post_commit' (after caller commits)."""
        ...

    async def run(self, event: ChatCompletedEvent, *, session: Any) -> None:
        """Process event. MUST NOT raise — log + return on error."""
        ...


class ChatHookRegistry:
    """Ordered hooks, 2-stage dispatch, semaphore + timeout safety.

    Defensive against:
      - OOM under burst (Semaphore bounds concurrent hook count)
      - Runaway hook (per-hook timeout)
      - Cascade failure (per-hook try/except isolation)
    """

    def __init__(
        self,
        hooks: Sequence[ChatCompletionHookPort],
        *,
        max_concurrency: int = DEFAULT_CHAT_HOOK_MAX_CONCURRENCY,
        timeout_s: float = DEFAULT_CHAT_HOOK_TIMEOUT_S,
    ):
        self._db_hooks = [h for h in hooks if h.stage == "db"]
        self._post_hooks = [h for h in hooks if h.stage == "post_commit"]
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout_s = timeout_s

    async def fire_db_stage(
        self, event: ChatCompletedEvent, *, session: Any,
    ) -> dict[str, bool]:
        """Stage 1: DB hooks (in transaction). Caller commits after."""
        return {
            h.hook_name: await self._run_isolated(h, event, session)
            for h in self._db_hooks
        }

    async def fire_post_stage(
        self, event: ChatCompletedEvent, *, session: Any,
    ) -> dict[str, bool]:
        """Stage 2: post-commit hooks. Run after caller commits."""
        return {
            h.hook_name: await self._run_isolated(h, event, session)
            for h in self._post_hooks
        }

    async def _run_isolated(
        self, hook: ChatCompletionHookPort,
        event: ChatCompletedEvent,
        session: Any,
    ) -> bool:
        """Run 1 hook with semaphore + timeout + exception catch."""
        async with self._semaphore:
            try:
                await asyncio.wait_for(
                    hook.run(event, session=session),
                    timeout=self._timeout_s,
                )
                return True
            except asyncio.TimeoutError:
                logger.warning(
                    "chat_hook_timeout",
                    hook_name=hook.hook_name,
                    stage=hook.stage,
                    timeout_s=self._timeout_s,
                    record_bot_id=str(event.record_bot_id),
                    request_id=str(event.request_id),
                )
                return False
            except Exception as exc:  # noqa: BLE001 — hook isolation by design
                logger.warning(
                    "chat_hook_failed",
                    hook_name=hook.hook_name,
                    stage=hook.stage,
                    record_bot_id=str(event.record_bot_id),
                    request_id=str(event.request_id),
                    error=str(exc)[:200],
                    exc_info=True,
                )
                return False


__all__ = [
    "ChatCompletedEvent",
    "ChatCompletionHookPort",
    "ChatHookRegistry",
]
