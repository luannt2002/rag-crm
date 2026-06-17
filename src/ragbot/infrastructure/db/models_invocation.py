"""ModelInvocation audit ORM models (v0.3.0 Task 4 — INVARIANT #2).

Tables:
- prompt_versions  : versioned prompt templates (no-overwrite on update).
- model_invocations: one row per LLM/embed/rerank call — full chain audit.

Imported by `ragbot.infrastructure.db.models` to register with Base.metadata.
`message_id` is BIGINT (ID của khách, nullable=False). `record_tenant_id` nullable
(not every upstream service identifies tenant). record_request_id is SOFT
ref (no FK constraint) to decouple audit tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragbot.infrastructure.db.models import Base
from ragbot.shared.constants import FEATURE_NAME_MAX_LEN, WORKSPACE_ID_MAX_LEN


# ============================================================================
# prompt_versions — versioned prompts (no overwrite)
# ============================================================================
class PromptVersionModel(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "name", "version_no",
            name="uq_prompt_versions_tenant_name_ver",
        ),
        Index("ix_prompt_versions_purpose", "purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # generation | router | rewrite | grader | reflect | narrate
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


# ============================================================================
# model_invocations — per LLM/embed/rerank call
# ============================================================================
class ModelInvocationModel(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        Index("ix_model_inv_message", "message_id"),
        Index("ix_model_inv_request_attempt", "record_request_id", "attempt_no"),
        Index("ix_model_inv_tenant_started", "record_tenant_id", "started_at"),
        # Per-feature cost rollup query path (alembic 0094).
        Index("ix_model_inv_feature_started", "feature_name", "started_at"),
    )

    invocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # `message_id` = ID khách (upstream); không FK cross-service.
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # soft ref request_logs.request_id — no FK (audit tables decoupled).
    record_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    record_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # High-level feature / subsystem that issued this invocation
    # (``query.generation``, ``ingest.enrich``, ``router.classify``, …).
    # Nullable for legacy rows + callers not yet threading the kwarg —
    # those roll up under ``DEFAULT_FEATURE_NAME_UNSET`` in cost audit.
    feature_name: Mapped[str | None] = mapped_column(
        String(FEATURE_NAME_MAX_LEN), nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # anthropic | openai | ollama | local | cohere | bge
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_prompt_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    full_payload_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    # success | failed | timeout | cached
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# Migration 0010: PayloadBlobModel dropped — raw payloads never enabled in
# prod. Hashes on model_invocations are the system of record.
# Migration 0019: ContextSnapshotModel dropped — zero usage in prod.


__all__ = [
    "ModelInvocationModel",
    "PromptVersionModel",
]
