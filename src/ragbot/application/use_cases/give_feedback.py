"""GiveFeedbackUseCase.

Migration 0010: writes feedback directly to `request_logs`
(feedback_score / is_correct / feedback_comment) via the
RequestLogRepository. The `feedback` table was dropped — there is no
longer a separate store. The outbox event is still emitted so
downstream analytics can consume the signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.application.commands.chat_commands import GiveFeedbackCommand
from ragbot.domain.events.chat_events import FeedbackGiven

if TYPE_CHECKING:
    from ragbot.infrastructure.repositories.request_log_repository import (
        RequestLogRepository,
    )
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)


class GiveFeedbackUseCase:
    def __init__(
        self,
        *,
        uow_factory: object,
        clock: Clock,
        request_log_repo: RequestLogRepository | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._request_log_repo = request_log_repo

    async def execute(self, cmd: GiveFeedbackCommand) -> None:
        # Persist on request_logs first (primary store).
        if self._request_log_repo is not None:
            score = 1 if cmd.rating == "up" else -1
            try:
                updated = await self._request_log_repo.attach_feedback_by_message(
                    tenant_id=cmd.record_tenant_id,
                    message_id=int(cmd.message_id),
                    score=score,
                    is_correct=None,
                    comment=cmd.comment,
                )
                if updated == 0:
                    logger.warning(
                        "feedback.no_matching_request_log",
                        tenant_id=str(cmd.record_tenant_id),
                        message_id=int(cmd.message_id),
                    )
            except Exception as exc:  # noqa: BLE001 — best-effort write
                logger.warning("feedback.persist_failed", error=str(exc))

        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            await uow.add_outbox(
                FeedbackGiven(
                    occurred_at=self._clock.now(),
                    record_tenant_id=cmd.record_tenant_id,
                    trace_id=cmd.trace_id,
                    workspace_id=cmd.workspace_id,
                    record_bot_id=cmd.record_bot_id,
                    conversation_id=cmd.conversation_id,
                    message_id=cmd.message_id,
                    rating=cmd.rating,
                    comment=cmd.comment,
                ),
            )
            await uow.commit()
        logger.info("feedback.recorded", rating=cmd.rating)


__all__ = ["GiveFeedbackUseCase"]
