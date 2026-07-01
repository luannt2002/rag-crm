"""Monitoring + capability + policy ORM models.

Adds tables required by audit checklist Phần 2/3/6/8/9:
- request_logs (Phần 2 — 17 field per request)
- request_steps (Phần 3 — per pipeline step)
- model_capabilities (Phần 8.1 — extends ai_models 1-1)
- tenant_model_policy (Phần 8.2 — ratio + fallback)
- policy_audit_log (Phần 8.3.6 — track policy changes)
- FeedbackModel NOTE: existing model kept; add is_correct via Alembic.
- golden_questions (Phần 6.4)

Imported by `ragbot.infrastructure.db.models` to register with Base.metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragbot.infrastructure.db.models import Base
from ragbot.shared.constants import WORKSPACE_ID_MAX_LEN

# document_chunks is managed by raw SQL in pgvector_store.py (no ORM model);
# register a minimal Table reference so ForeignKey("document_chunks.id") in
# RequestChunkRefModel resolves at mapper-configuration time. Without this,
# any Session.commit() touching RequestChunkRefModel fails with
# NoReferencedTableError → request_logs rows stay status='running' forever.
#
# Columns declared here are read by SQLAlchemy ORM builders (count/select/where)
# in pgvector_store.py. Raw-SQL writes (upsert_chunks) continue to own the
# canonical INSERT path; the shim only mirrors columns needed by the builder
# patterns. Embedding (pgvector) and content_hash deliberately omitted —
# raw INSERT remains the right tool for the high-throughput batch insert.
_document_chunks_table_ref = Table(
    "document_chunks",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("record_bot_id", UUID(as_uuid=True), index=True),
    Column("record_document_id", UUID(as_uuid=True), index=True),
    Column("chunk_index", Integer),
    Column("metadata_json", JSONB),
    # M10 — chunk_type lifted out of metadata_json into a first-class column
    # so modality-aware retrieval / rerank can filter without JSONB parsing.
    # Allowed values mirror ``CHUNK_TYPES_ALLOWED`` (constants.py).
    Column("chunk_type", String(32), nullable=False, server_default="text"),
    extend_existing=True,
)


# ============================================================================
# request_logs (Phần 2) — 17 fields per top-level request
# ============================================================================
class RequestLogModel(Base):
    """One row per chat request (final aggregate). Wraps multiple steps."""

    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_reqlog_tenant_started", "record_tenant_id", "started_at"),
        Index("ix_reqlog_model", "record_model_id"),
        Index("ix_reqlog_status", "status"),
        Index("ix_reqlog_conversation", "record_conversation_id"),
        Index("ix_reqlog_question_hash", "question_hash"),
        Index("ix_reqlog_tenant_message", "record_tenant_id", "message_id"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # ----- Multi-tenant identity (Phần 9) ----------------------------------
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    channel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connect_id: Mapped[str] = mapped_column(String(255), nullable=False)
    record_bot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    record_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # `message_id` là ID của khách (upstream service) — lưu thẳng kiểu BIGINT
    # (8 bytes) để không bao giờ overflow. Chỉ dùng để group metric theo tin nhắn
    # khách. KHÔNG FK, không transform, không join cross-service.
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_namespace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # ----- Hashes (Privacy 2.B — NO raw text here) ------------------------
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    answer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- Verify (OPT-IN plaintext) --------------------------------------
    # Raw question/answer, populated ONLY when the platform opts in via
    # ``settings.request_log_store_plaintext``. NULL by default — the repository
    # gates the write, so the Privacy-2.B "hash only" posture holds unless a
    # tenant explicitly enables the verify/QA review flow.
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- Routing / model ------------------------------------------------
    record_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # ----- Timing ---------------------------------------------------------
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ----- Token + cost ---------------------------------------------------
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)

    # ----- Status ---------------------------------------------------------
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    # success | failed | timeout | moderated | refused
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- RAG artifacts --------------------------------------------------
    # ``retrieved_chunks`` JSONB column dropped in alembic 0109 (G15) and
    # split into the relational ``request_chunk_refs`` child table -- see
    # ``RequestChunkRefModel`` below. Inline JSONB held no FK and bloated
    # request_logs by ~16 MB / 10k requests / day.
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    # ----- Quality / feedback --------------------------------------------
    feedback_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    quality_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    quality_evaluator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # human | llm_judge | golden_match
    # Migration 0010: merged from feedback.comment
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- Forensic / replay ---------------------------------------------
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ============================================================================
# request_steps (Phần 3) — per stage of pipeline
# ============================================================================
class RequestStepModel(Base):
    __tablename__ = "request_steps"
    __table_args__ = (
        Index("ix_reqstep_request_order", "record_request_id", "step_order"),
        Index("ix_reqstep_step_name", "step_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("request_logs.request_id", ondelete="CASCADE"),
        nullable=False,
    )
    record_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    channel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # router | rewrite | hyde | retrieve | rerank | grade | generate
    # | reflect | tool_call | narrate | embed | guardrail_input | guardrail_output
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    record_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ============================================================================
# request_chunk_refs (G15) — relational split of request_logs.retrieved_chunks
# ============================================================================
class RequestChunkRefModel(Base):
    """Per-request retrieved-chunk reference (one row per (request, chunk)).

    Replaces the ``request_logs.retrieved_chunks`` JSONB column dropped in
    alembic 0109. The split:

    * Adds FK CASCADE on both sides (request_logs, document_chunks). Hard
      deletes propagate -- no dangling refs.
    * Drops the PII surface (no inline preview / document_name).
    * Keeps the (rank, score) pair so analytics can replay the ordered
      retrieve-grade output.

    Indexed on both FKs so JOIN-back queries are O(log n).
    """

    __tablename__ = "request_chunk_refs"
    __table_args__ = (
        Index("ix_rcr_request", "record_request_id"),
        Index("ix_rcr_chunk", "record_chunk_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("request_logs.request_id", ondelete="CASCADE"),
        nullable=False,
    )
    record_chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


# ============================================================================
# model_capabilities (Phần 8.1) — extends ai_models 1-1
# ============================================================================
class ModelCapabilityModel(Base):
    __tablename__ = "model_capabilities"

    record_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    # premium | standard | basic
    can_web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_read_private_docs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    can_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_tool_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_score: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False, default=5.0)
    hallucination_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0,
    )
    suitable_for: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list,
    )
    # ["qa","research","summarize","code","legal","medical"]
    not_suitable_for: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # v0.3.0 runtime columns (migration 0009)
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrent_per_key: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ============================================================================
# tenant_model_policy (Phần 8.2) — knowledge source ratio + fallback
# ============================================================================
class TenantModelPolicyModel(Base):
    __tablename__ = "tenant_model_policy"
    __table_args__ = (
        UniqueConstraint("record_tenant_id", "record_bot_id", "record_model_id", name="uq_tenant_policy"),
        CheckConstraint(
            "private_doc_ratio + web_search_ratio + general_knowledge_ratio = 100",
            name="ck_policy_ratio_sum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    channel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_bot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bots.id", ondelete="CASCADE"),
        nullable=True,
    )
    record_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    private_doc_ratio: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    web_search_ratio: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    general_knowledge_ratio: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_fallback_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_for_task: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


# Migration 0010: policy_audit_log + golden_questions + golden_run_results
# dropped. Policy audits are now written to unified `audit_log` table
# (see AuditLogModel). Golden-eval tooling deferred.


__all__ = [
    "ModelCapabilityModel",
    "RequestChunkRefModel",
    "RequestLogModel",
    "RequestStepModel",
    "TenantModelPolicyModel",
]
