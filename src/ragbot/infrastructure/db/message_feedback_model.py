"""MessageFeedbackModel — thumbs up/down verdict log per assistant message.

Schema lives at table ``message_feedback`` (alembic 0074). Each row is
one user verdict on one assistant message; the row carries the full
4-key tenant/bot identity so a future analytics aggregator can group by
``(record_tenant_id, record_bot_id, verdict)`` without an extra join.

External keys (no ``record_`` prefix per project naming rule):

* ``message_id`` — BIGINT upstream id, nullable when the signal targets
  a locally-generated message that has no upstream wire id.
* ``connect_id`` — opaque external user identifier, nullable for
  anonymous web sessions.

Internal keys (``record_`` prefix = our UUID PKs): tenant, bot,
conversation. The conversation FK is intentionally a plain UUID without
a ``ForeignKey`` constraint so a deleted conversation does not cascade
into the feedback record (the verdict is a forensic signal, not
conversation content).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragbot.infrastructure.db.models import Base
from ragbot.shared.constants import (
    FEEDBACK_VERDICT_THUMBS_DOWN,
    FEEDBACK_VERDICT_THUMBS_UP,
)


class MessageFeedbackModel(Base):
    """One row = one thumbs verdict on one assistant message."""

    __tablename__ = "message_feedback"
    __table_args__ = (
        Index(
            "ix_message_feedback_tenant_bot_created",
            "record_tenant_id",
            "record_bot_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    record_bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bots.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    record_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    connect_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verdict: Mapped[str] = mapped_column(
        Enum(
            FEEDBACK_VERDICT_THUMBS_UP,
            FEEDBACK_VERDICT_THUMBS_DOWN,
            name="message_feedback_verdict",
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


__all__ = ["MessageFeedbackModel"]
