"""SQLAlchemy ORM models."""

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
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy import MetaData
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    registry,
)

from ragbot.shared.bot_limits import COLUMN_DEFAULTS
from ragbot.shared.constants import (
    DEFAULT_CONTENT_HASH_HEX_LEN,
    DEFAULT_FREQUENCY_PENALTY,
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_LANGUAGE,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_PRESENCE_PENALTY,
    DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
    DEFAULT_PROVIDER_TIMEOUT_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    WORKSPACE_ID_MAX_LEN,
)

# Schema constant kept for backward compat (migrations import it).
RAGBOT_SCHEMA = "public"
mapper_registry = registry()


class Base(DeclarativeBase):
    """Declarative base — tables live in the default `public` schema."""

    metadata = MetaData()


# ============================================================================
# v0 — Tenancy / Conversation / Document / Job / Outbox / Quota / Idempotency
# ============================================================================
class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    quota_monthly_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000_000)
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_hmac_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # P33 — Luồng A tenant-level rate-limit bypass (platform admin only).
    # OR-merged with bots.bypass_rate_limit (Luồng B, P18-5) at middleware time.
    bypass_rate_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL = inherit system_config.tenant_rate_limit_per_min; 0 = soft-unlimited.
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # C.5 — monthly prompt+completion token cap. NULL = no cap; 0 = block.
    monthly_token_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-tenant CORS strict whitelist. Each entry is either an exact
    # origin (``https://app.example.com``) or a wildcard pattern
    # (``https://*.example.com``). Empty list (default) = block all
    # browser cross-origin traffic for this tenant.
    allowed_origins: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceModel(Base):
    """A workspace entity under a tenant (ADR-W2-D2).

    Additive to the 4-key identity: ``bots.workspace_id`` stays the
    canonical slug; this row anchors RBAC / quota / lifecycle for that
    slug. ``slug`` equals the ``bots.workspace_id`` value it represents;
    ``(record_tenant_id, slug)`` is unique. RLS-scoped on
    ``record_tenant_id`` (the entity is tenant-scoped; the slug is its
    payload). Soft-delete via ``deleted_at`` — never hard-delete a
    workspace that still has bots referencing its slug.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "slug", name="uq_workspaces_tenant_slug",
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
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class BotModel(Base):
    """`bots` table — bot config keyed by 3-key external (bot_id, channel_type)
    plus internal ``record_tenant_id`` UUID FK to ``tenants(id)``.

    Slug uniqueness is per-tenant: same ``bot_id`` may exist under different
    tenants, but ``(record_tenant_id, bot_id, channel_type)`` must be unique.
    """

    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "workspace_id", "bot_id", "channel_type",
            name="uq_bots_record_tenant_workspace_bot_channel",
        ),
        Index("ix_bots_record_tenant_bot_channel", "record_tenant_id", "bot_id", "channel_type"),
        Index("ix_bots_model", "record_model_id"),
        CheckConstraint(
            "length(trim(bot_id)) > 0", name="ck_bot_id_not_empty",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    bot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Tenant-supplied slug (pass-through). Format enforced at ingress by
    # ``WorkspaceIdValidator``; the column-level CHECK regex is the DB-side
    # backstop.
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False)
    record_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    record_embedding_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    setting_options: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "frequency_penalty": DEFAULT_FREQUENCY_PENALTY,
            "max_tokens": DEFAULT_GENERATION_MAX_TOKENS,
            "response_format": "text",
            "presence_penalty": DEFAULT_PRESENCE_PENALTY,
            "temperature": DEFAULT_TEMPERATURE,
            "top_p": DEFAULT_TOP_P,
        },
    )
    custom_vocabulary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    max_history: Mapped[int | None] = mapped_column(Integer, nullable=True, default=COLUMN_DEFAULTS["max_history"])
    max_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=COLUMN_DEFAULTS["max_documents"])
    prompt_max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=COLUMN_DEFAULTS["prompt_max_tokens"])
    rerank_top_n: Mapped[int | None] = mapped_column(Integer, nullable=True, default=COLUMN_DEFAULTS["rerank_top_n"])
    plan_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bypass_token_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bypass_rate_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Token-quota feature (alembic 0100). ``tokens_used`` accumulates the
    # period total and is reset monthly by a scheduled job; the two
    # ``extra_*`` columns are paid add-ons layered on top of the system
    # default quotas; ``bypass_token_check`` is the ops-only kill-switch
    # so support can unblock a tenant without DB surgery.
    tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    extra_max_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    extra_output_tokens_per_response: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    bypass_token_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    # Per-bot UI/content language. ``DEFAULT_LANGUAGE`` is a deployment-wide
    # constant (env-overridable) — multi-industry tenants set per-bot value
    # at admin creation time. See CLAUDE.md domain-neutral rule.
    language: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_LANGUAGE)
    oos_answer_template: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Phase 14 — operator opt-in rerank gate by query intent. NULL preserves
    # legacy always-rerank semantics. JSONB shape:
    #   {"enabled": bool, "intents": list[str]}
    # Validated by ``RerankIntentWhitelist`` Pydantic model on load.
    rerank_intent_whitelist: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    # Per-bot threshold overrides (Stream V Phase 2). Keys: reranker_min_score_active,
    # grounding_check_threshold, guard_output_min_score, generate_context_chars_cap.
    # Resolve chain: this column > plan_limits > system_config > schema default.
    threshold_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    # Per-bot conversational-action config (slot-filling / lead-capture).
    # Owner-defined JSONB; drives the slot-capture pipeline (alembic 0150).
    action_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    # Per-bot metadata-extraction hint for the metadata filter layer (0162).
    metadata_extraction_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("record_bot_id", "connect_id", name="uq_conv_bot_connect"),
        Index("ix_conv_tenant", "record_tenant_id"),
        Index("ix_conv_last_message_at", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False,
    )
    connect_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, default="api")
    rolling_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_msg_conv_created", "record_conversation_id", "created_at"),
        Index("ix_msg_tenant_bot", "record_tenant_id", "record_bot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False,
    )
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, default="api")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("record_tenant_id", "record_bot_id", "tool_name", name="uq_doc_tool"),
        # ``record_bot_id`` is 1:1 with the external 3-key triple, so it alone is selective.
        Index("ix_doc_bot", "record_bot_id"),
        Index("ix_doc_state", "state"),
        Index("ix_doc_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # ``channel_type`` is not persisted here; ``record_bot_id`` is 1:1 with the
    # external 3-key triple. External callers still pass it to ingest.
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Per-document language tag. Inherits from caller-supplied value
    # (HTTP /sync request) which inherits from ``bots.language``. Default
    # is the deployment-wide ``DEFAULT_LANGUAGE`` for greenfield ingest.
    language: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_LANGUAGE)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    # Note: authority_score / valid_from / valid_until / superseded_by dropped
    # in migration 0010 (advanced features not yet wired end-to-end).
    acl: Mapped[list[str]] = mapped_column(ARRAY(String(255)), nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # S8 δ1: pre-chunk source text for BM25 / audit reconstruction. Nullable;
    # populated on new ingests only (legacy rows stay NULL until re-ingest).
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestIdempotencyKeyModel(Base):
    """Tracks BE-to-BE upload idempotency so partner retries do NOT
    double-ingest.

    Schema source-of-truth: alembic ``010j``. Scoped by 4-key
    isolation — ``(record_tenant_id, workspace_id, idempotency_key)``
    unique. ``request_hash`` is the SHA-256 of the canonical request
    body so the service can detect "same key, different payload"
    abuse vs honest retry. ``expires_at`` is set by the service layer
    (default 24h forward); a nightly sweep deletes expired rows.
    """

    __tablename__ = "ingest_idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_ingest_idemkey",
        ),
        Index("ix_ingest_idemkey_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(WORKSPACE_ID_MAX_LEN), nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class JobModel(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_status", "record_tenant_id", "status"),
        Index("ix_jobs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    channel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxModel(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_pending", "processed_at", "created_at"),
        Index("ix_outbox_subject", "subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[bytes] = mapped_column(nullable=False)  # JSON bytes
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    channel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redis_entry_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class QuotaModel(Base):
    """Per-tenant quota.

    Note (v0.4.1): `tenant_id` is UUID PK (internal). Quota enforcement
    only active when upstream supplies `tenant_uuid` in chat payload.
    External `tenant_id INT` alone does NOT trigger quota check.
    To enable enforcement for INT tenants, add a mapping layer in
    identity service or migrate quotas.tenant_id to INT.
    """

    __tablename__ = "quotas"

    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000_000)
    used_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Daily document ingest quota (alembic 010i — 2026-05-18). 0 = unlimited
    # (operator override for premium tenants). Counter rolls over when
    # ``documents_today_reset_at`` is in the past.
    documents_per_day_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000,
    )
    documents_today_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    documents_today_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


# Migration 0010: FeedbackModel dropped — comments merged into
# `request_logs.feedback_comment`; scores/is_correct already existed there.


# ============================================================================
# AI config tables
# ============================================================================
class AIProviderModel(Base):
    __tablename__ = "ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="api_key")
    # credentials_vault_path dropped in migration 0010 — use api_key_ref +
    # api_key_encrypted instead.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # v0.3.0 runtime columns (migration 0009)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_PROVIDER_TIMEOUT_MS,
    )
    connect_timeout_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RETRY_MAX_ATTEMPTS,
    )
    max_concurrent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_PROVIDER_MAX_CONCURRENT,
    )
    healthcheck_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Alembic 010e — controls LiteLLM wire-name prefixing. TRUE => emit
    # ``{provider.code}/{model_name}`` (Cohere / Jina / Voyage / …).
    # FALSE => bare ``{model_name}`` (OpenAI / Anthropic native). Replaces
    # a per-brand literal that previously lived inside model_resolver.
    requires_prefix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class AIModelModel(Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("record_provider_id", "name", name="uq_ai_model_provider_name"),
        Index("ix_ai_model_kind", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=8192)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    input_price_per_1k_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0,
    )
    output_price_per_1k_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0,
    )
    supports_streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_json_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(8)), nullable=False, default=lambda: ["en"],
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # v0.3.0 runtime columns (migration 0009)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # wire model id (vd claude-sonnet-4-5)
    input_price_per_1k_cached_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    default_temperature: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    default_top_p: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    default_max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports_caching: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deprecation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class BotModelBindingModel(Base):
    __tablename__ = "bot_model_bindings"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "record_bot_id", "purpose", "rank", "variant",
            name="uq_binding_unique",
        ),
        Index("ix_binding_bot_purpose", "record_bot_id", "purpose", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    record_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_models.id", ondelete="RESTRICT"), nullable=False,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variant: Mapped[str | None] = mapped_column(String(16), nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    temperature: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=0)
    max_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_LLM_MAX_TOKENS,
    )
    top_p: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=1)
    extra_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # v0.3.0 runtime columns (migration 0009)
    record_fallback_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    record_prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    record_prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class PromptTemplateModel(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "record_bot_id", "template_key", "version",
            name="uq_prompt_unique",
        ),
        Index("ix_prompt_active", "record_tenant_id", "record_bot_id", "template_key", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    record_bot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    jinja_source: Mapped[str] = mapped_column(Text, nullable=False)
    required_vars: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list,
    )
    model_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


# Migration 0010: IntentRouteModel + BotAIToolModel + AIConfigAuditLogModel
# dropped. Routing logic uses bindings/purpose only. Audit unified in
# `AuditLogModel` below.


class AuditLogModel(Base):
    """Unified audit log — replaces ai_config_audit_log + policy_audit_log.

    Migration 0010. `resource_id` is VARCHAR(128) so composite IDs (e.g.
    binding purpose keys) and UUIDs both fit.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index(
            "ix_audit_log_tenant_time",
            "record_tenant_id", "resource_type", "created_at",
        ),
        Index(
            "ix_audit_log_resource",
            "resource_type", "resource_id", "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(WORKSPACE_ID_MAX_LEN), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    # Tamper-detection hash chain — alembic 010g. Each row's value is
    # ``sha256(prev_row.row_hash || canonical_fields)``. Verifier scans
    # ``(created_at, id)`` order and reports broken chains. NULL only
    # during the alembic 010g backfill window; thereafter NOT NULL.
    row_hash: Mapped[str | None] = mapped_column(
        String(DEFAULT_CONTENT_HASH_HEX_LEN), nullable=True,
    )


class BotTokenUsageLogModel(Base):
    """Monthly token-usage roll-up per bot (alembic 0100).

    One row per bot identity. ``usage_by_month`` is a JSONB map keyed by
    ``YYYY-MM`` → integer tokens consumed in that month; the monthly
    reset job copies ``bots.tokens_used`` into this row before zeroing
    the live counter so historical usage survives the reset.
    """

    __tablename__ = "bot_token_usage_log"
    __table_args__ = (
        UniqueConstraint(
            "record_tenant_id", "workspace_id", "bot_id", "channel_type",
            name="uq_btul_record_tenant_workspace_bot_channel",
        ),
        Index("ix_btul_record_bot_id", "record_bot_id"),
        Index(
            "ix_btul_record_tenant_workspace",
            "record_tenant_id", "workspace_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    record_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(WORKSPACE_ID_MAX_LEN), nullable=False,
    )
    bot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    record_bot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    usage_by_month: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


# Register v0.2.0 monitoring + policy + capability tables.
# Import side-effect adds them to Base.metadata for Alembic discovery.
from ragbot.infrastructure.db.models_monitoring import (  # noqa: E402, F401
    ModelCapabilityModel,
    RequestChunkRefModel,
    RequestLogModel,
    RequestStepModel,
    TenantModelPolicyModel,
)
from ragbot.infrastructure.db.models_guardrail import (  # noqa: E402, F401
    GuardrailEventModel,
)
from ragbot.infrastructure.db.models_invocation import (  # noqa: E402, F401
    ModelInvocationModel,
    PromptVersionModel,
)


__all__ = [
    "AIModelModel",
    "AIProviderModel",
    "AuditLogModel",
    "Base",
    "BotModel",
    "BotModelBindingModel",
    "BotTokenUsageLogModel",
    "ConversationModel",
    "DocumentModel",
    "IngestIdempotencyKeyModel",
    "JobModel",
    "MessageModel",
    "ModelCapabilityModel",
    "ModelInvocationModel",
    "OutboxModel",
    "PromptTemplateModel",
    "PromptVersionModel",
    "QuotaModel",
    "RequestChunkRefModel",
    "RequestLogModel",
    "RequestStepModel",
    "TenantModel",
    "TenantModelPolicyModel",
    "mapper_registry",
]
