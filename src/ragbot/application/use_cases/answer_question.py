"""AnswerQuestionUseCase — 202 Accepted + Outbox enqueue."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from ragbot.application.commands.chat_commands import AnswerQuestionCommand
from ragbot.application.dto.chat_dto import ChatAcceptedDTO
from ragbot.application.services.idempotency import IdempotencyService
from ragbot.application.services.tenant_guard import TenantGuardService
from ragbot.application.services.token_budget import TokenBudgetPolicy
from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.message import Message
from ragbot.domain.events.chat_events import ChatReceived
from ragbot.domain.value_objects.idempotency_key import for_chat_message
from ragbot.shared.constants import DEFAULT_UPFRONT_TOKEN_ESTIMATE
from ragbot.shared.types import ConversationId, JobId

if TYPE_CHECKING:
    from ragbot.application.ports.repository_ports import (
        ConversationRepositoryPort,
        JobRepositoryPort,
        UnitOfWorkPort,
    )
    from ragbot.shared.clock import Clock

logger = structlog.get_logger(__name__)


class AnswerQuestionUseCase:
    """Persist user message + outbox event ChatReceived. Returns job_id (202)."""

    def __init__(
        self,
        *,
        conv_repo: ConversationRepositoryPort,
        job_repo: JobRepositoryPort,
        uow_factory: type[UnitOfWorkPort] | object,  # callable[[], UoW]
        idempotency: IdempotencyService,
        budget: TokenBudgetPolicy,
        clock: Clock,
    ) -> None:
        self._conv = conv_repo
        self._jobs = job_repo
        self._uow_factory = uow_factory
        self._idem = idempotency
        self._budget = budget
        self._clock = clock

    async def execute(self, cmd: AnswerQuestionCommand) -> ChatAcceptedDTO:
        # JWT middleware already enforced this; defence in depth.
        TenantGuardService.assert_owns(cmd.record_tenant_id, cmd.record_tenant_id)

        await self._budget.ensure_affordable(
            record_tenant_id=cmd.record_tenant_id,
            estimated_tokens=DEFAULT_UPFRONT_TOKEN_ESTIMATE,
        )

        idem_key = for_chat_message(
            record_tenant_id=str(cmd.record_tenant_id),
            record_bot_id=str(cmd.record_bot_id),
            user_id=str(cmd.user_id),
            external_message_id=cmd.external_message_id,
        )
        if await self._idem.is_duplicate(idem_key):
            prior = await self._idem.get_prior_result_ref(idem_key)
            if prior:
                logger.info("answer_question.idempotency_hit", key=idem_key)
                return ChatAcceptedDTO(
                    job_id=JobId(__import__("uuid").UUID(prior)),
                    status="queued",
                    status_url=f"/ragbot/jobs/{prior}",
                    trace_id=cmd.trace_id,
                )

        job_id = JobId(uuid4())

        uow_call = self._uow_factory  # type: ignore[assignment]
        async with uow_call() as uow:  # type: ignore[operator]
            conversation: Conversation = await self._conv.get_or_create(
                cmd.record_bot_id, cmd.user_id,
                record_tenant_id=cmd.record_tenant_id,
                workspace_id=cmd.workspace_id,
            )
            user_msg = Message.new_user_message(
                conversation_id=conversation.id,
                record_tenant_id=cmd.record_tenant_id,
                record_bot_id=cmd.record_bot_id,
                content=cmd.content,
                channel=cmd.channel,
                created_at=self._clock.now(),
            )
            updated = conversation.add_message(user_msg)
            await self._conv.save(
                updated,
                record_tenant_id=cmd.record_tenant_id,
                workspace_id=cmd.workspace_id,
            )

            await self._jobs.create(
                job_id=job_id,
                record_tenant_id=cmd.record_tenant_id,
                kind="chat.answer",
                payload={
                    "bot_id": str(cmd.record_bot_id),
                    "user_id": cmd.user_id,
                    "conversation_id": str(updated.id),
                    "content": cmd.content,
                    "channel": cmd.channel,
                    "history_limit": cmd.history_limit,
                    "callback_url": cmd.callback_url,
                },
            )

            event = ChatReceived(
                occurred_at=self._clock.now(),
                record_tenant_id=cmd.record_tenant_id,
                trace_id=cmd.trace_id,
                workspace_id=cmd.workspace_id,
                bot_id=cmd.bot_id,
                channel_type=cmd.channel_type,
                job_id=job_id,
                record_bot_id=cmd.record_bot_id,
                user_id=cmd.user_id,
                conversation_id=updated.id,
                message_id=user_msg.id,
                content=cmd.content,
                channel=cmd.channel,
                idempotency_key=idem_key,
                history_limit=cmd.history_limit,
                callback_url=cmd.callback_url,
            )
            await uow.add_outbox(event)
            await uow.commit()

        await self._idem.register(idem_key, result_ref=str(job_id))

        return ChatAcceptedDTO(
            job_id=job_id,
            status="queued",
            status_url=f"/ragbot/jobs/{job_id}",
            trace_id=cmd.trace_id,
        )


__all__ = ["AnswerQuestionUseCase"]
