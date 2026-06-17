"""RefuseSuggestionModel — Stream H V1 SCAFFOLD (not yet wired).

Schema for aggregated refuse patterns per bot. The Stream H V1 admin
endpoint (``admin_refuse_suggestions.py``) currently queries
``request_logs`` directly via SQL aggregate — no batch job yet writes
to this table.

This model + alembic 0064 stay as scaffold for a future V2 batch
aggregator (cron-driven, cost-neutral) that materialises the SQL
aggregate into this table for faster admin lookups at scale. V2 not
shipped yet; revisit when:

  - Admin endpoint p95 > 500ms on production-scale request_logs, OR
  - Owner needs historical refuse trend (table has time series support
    via ``last_seen`` column that the live aggregate can't preserve).

Until then this model is referenced only by the alembic migration —
no service writes to the table. Importing the class is safe (Base
metadata registration only).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragbot.infrastructure.db.models import Base
from ragbot.shared.types import TenantId


class RefuseSuggestionModel(Base):
    """Aggregated refuse patterns per bot — helps owners find corpus gaps."""

    __tablename__ = "refuse_suggestions"
    __table_args__ = (
        Index("ix_refuse_suggestions_tenant_bot", "record_tenant_id", "record_bot_id"),
        UniqueConstraint(
            "record_bot_id", "query_intent",
            name="uq_refuse_suggestions_bot_intent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    record_bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bots.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_intent: Mapped[str] = mapped_column(String(64), nullable=False)
    refuse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    sample_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
