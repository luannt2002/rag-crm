"""Pydantic request models for the test_chat route package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). Holds
the 11 request DTOs + the ``_slugify_bot_id`` helper that ``CreateBotRequest``
relies on. Imports nothing from sibling route modules (acyclic).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ragbot.shared.constants import (
    MAX_BOT_ID_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    MAX_CONNECT_ID_LENGTH,
)


def _slugify_bot_id(raw: str) -> str:
    """Convert raw bot_id string sang URL-safe slug.

    Rules (apply in order):
      1. Lowercase
      2. Strip Vietnamese diacritics (NFC → NFD → drop combining marks)
      3. Replace any non-alphanumeric → dash
      4. Collapse multiple dashes → single dash
      5. Strip leading/trailing dash

    Examples:
      "thông tư - 09/2020/TT-NHNN" → "thong-tu-09-2020-tt-nhnn"
      "Bot Name 2024!" → "bot-name-2024"
      "   spaces   " → "spaces"
    """
    import re as _re  # noqa: PLC0415
    import unicodedata as _ud  # noqa: PLC0415
    # 1+2: lowercase + drop diacritics
    s = _ud.normalize("NFD", raw.lower())
    s = "".join(c for c in s if _ud.category(c) != "Mn")
    # Special VN: ₫ → d, special chars → ""
    s = s.replace("đ", "d").replace("ə", "e")
    # 3: non-alphanumeric → dash
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    # 4+5: collapse + strip dashes
    s = _re.sub(r"-+", "-", s).strip("-")
    return s or raw  # fallback to raw if everything stripped


class CreateBotRequest(BaseModel):
    # Body carries the 2-key bot identity + optional workspace slug;
    # tenant is lifted from the JWT bearer.
    bot_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="External bot slug — auto-slugified to URL-safe form",
    )

    @field_validator("bot_id", mode="before")
    @classmethod
    def _auto_slugify(cls, v: Any) -> str:
        # Auto-convert any input to URL-safe slug.
        # "thông tư - 09/2020/TT-NHNN" → "thong-tu-09-2020-tt-nhnn"
        # Prevents 404 on URL routing when raw input contains spaces / slashes / diacritics.
        if not isinstance(v, str):
            return v
        slugged = _slugify_bot_id(v)
        if not slugged:
            raise ValueError("bot_id cannot be empty after slugification")
        return slugged

    channel_type: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHANNEL_TYPE_LENGTH,
        description="Channel — opaque string, RAG-agnostic",
    )
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Workspace slug; route resolver substitutes "
            "str(record_tenant_id) when omitted."
        ),
    )
    bot_name: str = Field(min_length=1, max_length=255)
    system_prompt: str = "Bạn là trợ lý AI. Trả lời dựa trên ngữ cảnh tài liệu được cung cấp. Trả lời bằng tiếng Việt."
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=450, ge=1, le=32000)
    callback_url: str | None = None
    # Setting bypass_token_limit at create-time requires tenant level (80);
    # caller below the threshold gets 403. Default False keeps existing
    # behaviour for non-test bots; UI test flow opts in by sending true.
    bypass_token_limit: bool = False


class UpdateBotRequest(BaseModel):
    bot_name: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    max_history: int | None = Field(default=None, ge=1)
    max_documents: int | None = Field(default=None, ge=1)
    prompt_max_tokens: int | None = Field(default=None, ge=1)
    rerank_top_n: int | None = Field(default=None, ge=1)
    plan_limits: dict | None = None
    callback_url: str | None = None
    bypass_token_limit: bool | None = None
    bypass_rate_limit: bool | None = None


class AddDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    content: str | None = None
    url: str | None = None
    source_type: str = "manual"
    # Per-bot workspace slug (4-key identity). Omitted/empty → tenant-default
    # (back-compat). Lets a tenant's bots live in distinct workspaces.
    workspace_id: str | None = Field(default=None, max_length=64)


class TestChatRequest(BaseModel):
    # Body carries the 2-key bot identity + optional workspace slug;
    # tenant lifted from JWT bearer.
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Workspace slug; route resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )
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
    question: str = Field(min_length=1, max_length=4000)
    # Optional caller-supplied conversation id. When set, the route uses it
    # to scope chat_histories so the harness can run N parallel/isolated
    # turns without history pollution. Falls back to DEFAULT_CONNECT_ID
    # so existing callers (the demo UI) keep working unchanged.
    connect_id: str | None = Field(
        default=None,
        max_length=MAX_CONNECT_ID_LENGTH,
        description=(
            "Optional conversation/session id. Defaults to the platform's "
            "shared test connect id when omitted; supply a unique value "
            "per harness room to keep histories isolated."
        ),
    )
    # HARN-3: opt-in debug payload. When `debug == "full"` (case-insensitive),
    # response includes `retrieved_chunks_content` so external harness+judge
    # can verify hallucination against actual chunk text, not just doc names.
    # Off by default — no regression for normal callers.
    debug: str = Field(default="", max_length=16)
    # Test-mode only: skip semantic cache lookup to force the full pipeline
    # to run. Useful when running many test turns against the same question
    # (cache TTL 24h + high cosine threshold would otherwise return stale
    # cached responses instead of exercising the live pipeline).
    # Production /chat endpoint does NOT expose this flag.
    bypass_cache: bool = Field(
        default=False,
        description=(
            "Test-mode only: skip semantic cache lookup to force pipeline run. "
            "Production /chat endpoint does NOT expose this flag."
        ),
    )


class TestChatClearRequest(BaseModel):
    # Body carries the 2-key bot identity + optional workspace slug;
    # tenant lifted from JWT bearer.
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
        description=(
            "Workspace slug; route resolver falls back to "
            "str(record_tenant_id) when omitted."
        ),
    )


class ValidateLinkRequest(BaseModel):
    url: str = Field(min_length=1)


class UpdateMaxHistoryRequest(BaseModel):
    max_history: int = Field(..., ge=1, description="Số tin nhắn tối đa mỗi room. Bắt buộc int >= 1.")


class UpdateVocabularyRequest(BaseModel):
    abbreviations: dict[str, str] = Field(default_factory=dict, max_length=500)
    diacritics: dict[str, str] = Field(default_factory=dict, max_length=500)


class UpdateConfigRequest(BaseModel):
    value: str = Field(min_length=0)


class UpsertApiKeyRequest(BaseModel):
    value: str
    label: str = "primary"


class CreateTokenRequest(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)
    description: str = ""
    role: str = Field(default="service", pattern=r"^(owner|service)$")
    rate_limit_value: int | None = Field(default=None, ge=0)
    rate_limit_window: int | None = Field(default=None, ge=1)


__all__ = [
    "_slugify_bot_id",
    "CreateBotRequest",
    "UpdateBotRequest",
    "AddDocumentRequest",
    "TestChatRequest",
    "TestChatClearRequest",
    "ValidateLinkRequest",
    "UpdateMaxHistoryRequest",
    "UpdateVocabularyRequest",
    "UpdateConfigRequest",
    "UpsertApiKeyRequest",
    "CreateTokenRequest",
]
