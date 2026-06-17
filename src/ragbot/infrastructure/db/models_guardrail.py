"""Guardrail events + rules ORM models.

- ``GuardrailEventModel`` (v0.3.0 Task 3): one row per guardrail rule hit.
  KHÔNG lưu raw text (privacy 2.B). Caller hashes content upstream when
  forensic correlation is needed.
- ``GuardrailRuleModel`` (Agent J, alembic 010f): one row per moderation
  rule. ``record_tenant_id IS NULL`` ⇒ platform default; non-NULL ⇒
  tenant override. Loader prefers the override.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragbot.infrastructure.db.models import Base
from ragbot.shared.constants import WORKSPACE_ID_MAX_LEN


class GuardrailEventModel(Base):
    __tablename__ = "guardrail_events"
    __table_args__ = (
        Index("ix_guardrail_events_message", "message_id"),
        Index("ix_guardrail_events_tenant_time", "record_tenant_id", "detected_at"),
        Index("ix_guardrail_events_rule_severity", "rule_id", "severity"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    guardrail_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # input | output | tool
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # info | warn | block
    action_taken: Mapped[str] = mapped_column(String(16), nullable=False)
    # allow | redact | block | hitl
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class GuardrailRuleModel(Base):
    """DB-driven moderation rule (Agent J).

    Loader fetches rows matching ``(record_tenant_id IS NULL OR
    record_tenant_id = :tenant)`` and merges so the tenant-override row
    wins on identical ``rule_id``. The unique partial indexes enforce
    one row per ``(tenant, rule_id)`` and one platform-default per
    ``rule_id``.
    """

    __tablename__ = "guardrail_rules"
    __table_args__ = (
        # Hot path: loader filters by tenant + scope when caching per scope.
        # The partial index in alembic 010f covers ``enabled = true``; the
        # ORM Index declaration here is informational so create_all in test
        # fixtures sees the same shape.
        Index(
            "ix_guardrail_rules_tenant_scope_enabled",
            "record_tenant_id",
            "scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(WORKSPACE_ID_MAX_LEN), nullable=False, default="system",
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_flags: Mapped[str] = mapped_column(
        String(32), nullable=False, default="",
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # info | warn | block
    action_taken: Mapped[str] = mapped_column(String(16), nullable=False)
    # allow | redact | block | hitl
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # input | output | both
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


__all__ = ["GuardrailEventModel", "GuardrailRuleModel"]
