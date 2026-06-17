"""Schema validator cho `chat.received.v1` event payload."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatReceivedPayload(BaseModel):
    """External payload from upstream service.

    Identity contract is the bot 2-key (``bot_id`` + ``channel_type``) plus
    the canonical ``record_tenant_id`` UUID. Legacy upstream ``tenant_id``
    INT and ``tenant_uuid`` string aliases are still accepted so the worker
    can drain old outbox rows during the migration window — both feed the
    same resolver in ``chat_worker._resolve_record_tenant_id``.
    """

    # External 2-key bot identity — REQUIRED for cross-tenant defense.
    bot_id: str = Field(min_length=1)
    channel_type: str = Field(min_length=1)
    # Optional on the wire — worker resolver substitutes ``str(record_tenant_id)``
    # so partially-claimed payloads stay routable.
    workspace_id: str | None = None

    # Internal IDs
    job_id: str  # string UUID
    message_id: int  # INT từ upstream
    conversation_id: str | None = None  # UUID optional
    user_id: str = Field(min_length=1)
    trace_id: str = ""

    # Tenant references — accept the new UUID claim or the legacy aliases.
    # At least one of these PHẢI có giá trị; resolver enforces.
    record_tenant_id: str | None = None
    tenant_uuid: str | None = None  # legacy alias for record_tenant_id
    tenant_id: int | None = None  # legacy upstream INT (backwards-compat)

    # Content
    content: str = Field(min_length=1)
    callback_url: str | None = None

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("https://") and not v.startswith("http://"):
                raise ValueError("callback_url must be a valid HTTP(S) URL")
        return v

    # Permission filtering — groups the user belongs to (optional)
    user_groups: list[str] = Field(default_factory=list)

    @field_validator("bot_id", "channel_type", "user_id")
    @classmethod
    def trim_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("job_id", "conversation_id", "tenant_uuid", "record_tenant_id")
    @classmethod
    def valid_uuid_or_none(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            UUID(v)
        except (TypeError, ValueError) as e:
            raise ValueError(f"must be valid UUID: {v}") from e
        return v
