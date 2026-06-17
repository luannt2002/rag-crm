"""MessageFeedbackRepository — persist + aggregate thumbs verdicts.

Two operations:

* :meth:`record` — insert one verdict row. Caller must pass the full
  4-key bot identity (record_tenant_id + record_bot_id + workspace_id +
  channel_type are validated upstream at the route layer; the repo
  itself is keyed on the two UUID PKs because RLS + the unique
  constraint already collapse the 4-tuple to a single
  ``record_bot_id``).
* :meth:`aggregate_per_bot` — count thumbs_up vs thumbs_down for one
  bot over a sliding window. Returned dict is shape-stable
  (``{"thumbs_up": int, "thumbs_down": int}``) so callers can render
  zero-state without branching on missing keys.

Sessions are opened via :func:`session_with_tenant` so RLS is honoured
on every read and write. Cross-tenant attempts return 0 rows
(read) / fail the WITH CHECK clause (write) — both are policy-level
guarantees, not application-level.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.infrastructure.db.message_feedback_model import MessageFeedbackModel
from ragbot.shared.constants import (
    DEFAULT_FEEDBACK_AGGREGATE_DAYS,
    FEEDBACK_VERDICT_THUMBS_DOWN,
    FEEDBACK_VERDICT_THUMBS_UP,
)


class MessageFeedbackRepository:
    """Insert + aggregate verdict rows. RLS-scoped via session_with_tenant."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sf = session_factory

    async def record(
        self,
        *,
        record_tenant_id: UUID,
        record_bot_id: UUID,
        verdict: str,
        message_id: int | None = None,
        record_conversation_id: UUID | None = None,
        connect_id: str | None = None,
        comment: str | None = None,
    ) -> UUID:
        """Insert one verdict row; return the new row id.

        ``verdict`` must be one of the two thumbs constants — caller
        already validated this at the schema layer; we re-check here so
        the repo can never silently insert garbage even when called
        directly (e.g. from a worker).
        """
        if verdict not in (
            FEEDBACK_VERDICT_THUMBS_UP, FEEDBACK_VERDICT_THUMBS_DOWN,
        ):
            raise ValueError(f"verdict must be thumbs_up/thumbs_down; got {verdict!r}")

        new_id = uuid4()
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            session.add(
                MessageFeedbackModel(
                    id=new_id,
                    record_tenant_id=record_tenant_id,
                    record_bot_id=record_bot_id,
                    message_id=message_id,
                    record_conversation_id=record_conversation_id,
                    connect_id=connect_id,
                    verdict=verdict,
                    comment=comment,
                ),
            )
            await session.commit()
        return new_id

    async def aggregate_per_bot(
        self,
        *,
        record_tenant_id: UUID,
        record_bot_id: UUID,
        since_days: int = DEFAULT_FEEDBACK_AGGREGATE_DAYS,
    ) -> dict[str, int]:
        """Count thumbs_up / thumbs_down rows over the last N days.

        Both verdicts come back as separate keys even when one is zero
        so dashboard renderers can plot side-by-side bars without a
        ``defaultdict`` dance.
        """
        if since_days <= 0:
            raise ValueError(f"since_days must be positive; got {since_days}")

        cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

        # SQL ``COUNT(*) FILTER (WHERE verdict = 'thumbs_up')`` — keeps it
        # one round-trip and the predicate fits the partial-index pattern
        # in case the analytics team adds one later.
        up_col = func.count().filter(
            MessageFeedbackModel.verdict == FEEDBACK_VERDICT_THUMBS_UP,
        )
        down_col = func.count().filter(
            MessageFeedbackModel.verdict == FEEDBACK_VERDICT_THUMBS_DOWN,
        )

        stmt = (
            select(up_col, down_col)
            .where(MessageFeedbackModel.record_tenant_id == record_tenant_id)
            .where(MessageFeedbackModel.record_bot_id == record_bot_id)
            .where(MessageFeedbackModel.created_at >= cutoff)
        )

        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            row = (await session.execute(stmt)).one()

        return {
            FEEDBACK_VERDICT_THUMBS_UP: int(row[0] or 0),
            FEEDBACK_VERDICT_THUMBS_DOWN: int(row[1] or 0),
        }


__all__ = ["MessageFeedbackRepository"]
