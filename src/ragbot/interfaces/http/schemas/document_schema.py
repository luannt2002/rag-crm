"""Document + Admin schemas.

Body carries 2-key bot identity ``(bot_id, channel_type)``; tenant is
lifted from the JWT bearer in the route. Admin schemas (provider/model/
binding mutations) keep UUID PKs — those are internal-service-to-internal-
service calls, not tenant-facing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from ragbot.shared.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_PRIVATE_DOC_RATIO,
    DEFAULT_SCHEMA_VERSION,
    MAX_BOT_ID_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_DOCUMENT_NAME_LENGTH,
    SUPPORTED_INGEST_SCHEMA_VERSIONS,
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_PATTERN,
)


class IngestDocumentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; route resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )
    source_url: HttpUrl
    document_name: str = Field(min_length=1, max_length=MAX_DOCUMENT_NAME_LENGTH)
    mime_type: str | None = None
    language: str = "vi"
    authority_score: float = Field(default=0.5, ge=0.0, le=1.0)
    # Forward-compat: body-level mirror of the ``X-Schema-Version`` header.
    # Handler branches on this when the payload shape evolves; the header
    # remains the authoritative source (lifted onto ``request.state``).
    # Defaults to ``DEFAULT_SCHEMA_VERSION`` so deployed partners that
    # never send the field stay on the current shape.
    schema_version: int = Field(
        default=DEFAULT_SCHEMA_VERSION,
        description=(
            "Body-level mirror of the X-Schema-Version header. "
            "Must be in SUPPORTED_INGEST_SCHEMA_VERSIONS."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value not in SUPPORTED_INGEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema_version={value} not supported; "
                f"supported: {sorted(SUPPORTED_INGEST_SCHEMA_VERSIONS)}"
            )
        return value


class IngestDocumentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    job_id: str
    tool_name: str
    status: Literal["queued"] = "queued"
    trace_id: str


class DeleteDocumentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; route resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )
    tool_name: str = Field(min_length=1, max_length=MAX_DOCUMENT_NAME_LENGTH)


class DeleteDocumentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    deleted_chunks: int
    corpus_version: int


class RechunkDocumentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
        description=(
            "Workspace slug; route resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )
    source_url: HttpUrl


class RechunkByDocumentIdRequest(BaseModel):
    """Rechunk a single document addressed by its internal UUID.

    260525 Bug #2 fix — URL-keyed rechunk cannot disambiguate bots with
    multiple documents sharing the same ``source_url`` (Google Sheets
    workbook with several tab gids, etc.). This request keys the
    document by primary key so the use case never has to guess.
    """

    model_config = ConfigDict(frozen=True)

    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug",
    )
    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
    )
    workspace_id: str | None = Field(
        default=None,
        max_length=WORKSPACE_ID_MAX_LEN,
        pattern=WORKSPACE_ID_PATTERN,
    )
    document_id: UUID = Field(
        ...,
        description="Internal documents.id UUID primary key.",
    )


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


# ----- Admin AI config schemas -----------------------------------
# NOTE: Admin endpoints are internal-service-to-internal-service (control-plane).
# They keep UUID record_* identifiers because they target specific DB rows by PK,
# not tenant-facing slugs. Tenant-facing endpoints are above.
class AdminCreateProviderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: Literal["llm", "embedding", "reranker", "moderation"]
    base_url: str
    auth_type: Literal["api_key", "oauth", "mTLS", "none"] = "api_key"
    credentials_vault_path: str | None = None
    enabled: bool = True


class AdminCreateModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: UUID
    name: str
    kind: Literal["chat", "embedding", "reranker", "moderation"]
    context_window: int = 8192
    max_output_tokens: int = 4096
    input_price_per_1k_usd: float = 0.0
    output_price_per_1k_usd: float = 0.0
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    languages: list[str] = Field(default_factory=lambda: ["en"])


class AdminCreateBindingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: UUID  # ADMIN-INTERNAL: targets bots.id PK directly (not tenant-facing)
    purpose: Literal[
        "llm_primary",
        "llm_fallback",
        "embedding",
        "reranker",
        "moderation_input",
        "moderation_output",
    ]
    model_id: UUID
    rank: int = 0
    variant: str | None = None
    weight: int = Field(default=DEFAULT_PRIVATE_DOC_RATIO, ge=0, le=100)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=DEFAULT_LLM_MAX_TOKENS, ge=1, le=32_000)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


__all__ = [
    "AdminCreateBindingRequest",
    "AdminCreateModelRequest",
    "AdminCreateProviderRequest",
    "DeleteDocumentRequest",
    "DeleteDocumentResponse",
    "IngestDocumentRequest",
    "IngestDocumentResponse",
    "JobStatusResponse",
    "RechunkByDocumentIdRequest",
    "RechunkDocumentRequest",
]
