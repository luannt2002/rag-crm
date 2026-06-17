"""BotConfig DTO — runtime shape khớp bảng `bots` migration 0011.

Dùng cho BotRegistryService cache (DB → in-memory bot_map).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ragbot.shared.bot_limits import COLUMN_DEFAULTS
from ragbot.shared.constants import (
    DEFAULT_FREQUENCY_PENALTY,
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_LANGUAGE,
    DEFAULT_PRESENCE_PENALTY,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
)


class RerankIntentWhitelist(BaseModel):
    """Per-bot rerank gate by query intent.

    When ``None`` is stored on ``BotConfig.rerank_intent_whitelist``
    legacy always-rerank behaviour applies. When set:

    * ``enabled=False`` — whitelist explicitly disabled, always rerank.
      Operators use this to A/B-disable the gate without dropping the
      configured intent list.
    * ``enabled=True`` + ``intents`` set — rerank fires only when the
      live ``state["intent"]`` is in ``intents``. All other intents
      bypass the rerank API call (saves ~150ms + Jina cost).
    * ``enabled=True`` + empty ``intents`` — rerank skipped for ALL
      intents (operator misconfig but well-defined behaviour).

    Validation: extra fields rejected so a typo in the JSONB column
    payload surfaces at load time, not silently at the gate.
    """

    enabled: bool = True
    # tuple keeps the value hashable + immutable post-validation.
    intents: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid")

    @field_validator("intents", mode="before")
    @classmethod
    def _coerce_intents(cls, v: Any) -> Any:
        """Accept JSON list / tuple / str-set inputs for forward-compat.

        Postgres JSONB returns a Python ``list``; older operator scripts
        may have written a ``set`` literal. Normalise to a clean tuple
        of stripped non-empty strings, preserving order de-duplicated.
        """
        if v is None:
            return ()
        if isinstance(v, (list, tuple, set, frozenset)):
            seen: set[str] = set()
            cleaned: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    continue
                token = item.strip()
                if not token or token in seen:
                    continue
                seen.add(token)
                cleaned.append(token)
            return tuple(cleaned)
        return v


class BotSettingOptions(BaseModel):
    """6 trường cấu hình LLM sampling — strict bounds."""

    frequency_penalty: float = Field(default=DEFAULT_FREQUENCY_PENALTY, ge=-2.0, le=2.0)
    max_tokens: int = Field(default=DEFAULT_GENERATION_MAX_TOKENS, ge=1, le=32000)
    response_format: Literal["text", "json_object"] = "text"
    presence_penalty: float = Field(default=DEFAULT_PRESENCE_PENALTY, ge=-2.0, le=2.0)
    temperature: float = Field(default=DEFAULT_TEMPERATURE, ge=0.0, le=2.0)
    top_p: float = Field(default=DEFAULT_TOP_P, ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class BotConfig(BaseModel):
    """Runtime bot config loaded from DB → cache."""

    id: UUID
    bot_id: str
    channel_type: str
    # Cache keys + cross-tenant guards rely on the tenant UUID and the
    # workspace slug being present on every DTO; together with bot_id and
    # channel_type they form the canonical resolve key.
    record_tenant_id: UUID
    workspace_id: str
    bot_name: str
    model_id: UUID | None = None
    embedding_model_id: UUID | None = None
    system_prompt: str = ""
    setting_options: BotSettingOptions = Field(default_factory=BotSettingOptions)
    tokens_used: int = Field(
        default=0, ge=0,
        description="Cumulative tokens this period. Reset monthly via cron.",
    )
    extra_max_tokens: int = Field(
        default=0, ge=0,
        description="Paid extra monthly quota beyond system default.",
    )
    extra_output_tokens_per_response: int = Field(
        default=0, ge=0,
        description="Paid extra output cap per chat response.",
    )
    bypass_token_check: bool = Field(
        default=False,
        description="Ops-only: skip quota check.",
    )
    custom_vocabulary: dict[str, Any] = Field(default_factory=dict)
    max_history: int | None = COLUMN_DEFAULTS["max_history"]
    max_documents: int = COLUMN_DEFAULTS["max_documents"]
    prompt_max_tokens: int | None = COLUMN_DEFAULTS["prompt_max_tokens"]
    rerank_top_n: int | None = COLUMN_DEFAULTS["rerank_top_n"]
    plan_limits: dict[str, Any] = Field(default_factory=dict)
    # Per-bot conversational-action config (slot-filling / lead-capture /
    # booking). Owner-defined in ``bots.action_config`` JSONB:
    # ``{"enabled": bool, "slots_schema": {...}, "service_lock": ...}``.
    # Drives the slot-capture pipeline in the generate node.
    action_config: dict[str, Any] = Field(default_factory=dict)
    # Per-bot metadata-extraction hint (owner self-service for the metadata
    # filter layer). Owner-defined in ``bots.metadata_extraction_config``.
    metadata_extraction_config: dict[str, Any] | None = None
    # Per-bot threshold tuning (reranker / grounding / guard / semantic_cache).
    # Resolve chain: column > plan_limits > system_config > schema default.
    threshold_overrides: dict[str, Any] = Field(default_factory=dict)
    callback_url: str | None = None
    bypass_token_limit: bool = False
    bypass_rate_limit: bool = False
    language: str = DEFAULT_LANGUAGE
    oos_answer_template: str | None = None  # Per-bot OOS override; None = fall back to i18n default
    # Phase 14 — operator opt-in rerank intent gate. ``None`` = legacy
    # always-rerank. See ``RerankIntentWhitelist`` docstring for semantics.
    rerank_intent_whitelist: RerankIntentWhitelist | None = None
    # Bot creation timestamp lifted from ``bots.created_at``. Optional
    # (rolling-deploy tolerance: pre-migration rows may not have populated
    # the cache yet). M14 uses this to default ``xml_wrap_enabled`` ON for
    # bots created on/after ``XML_WRAP_DEFAULT_ON_FROM_DATE`` while leaving
    # legacy bots untouched (backward-compat).
    created_at: datetime | None = None

    @field_validator("bot_id")
    @classmethod
    def bot_id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("bot_id must not be empty")
        return v

    @field_validator("workspace_id")
    @classmethod
    def workspace_id_valid(cls, v: str) -> str:
        # Defer to the central validator so DB rows that bypassed ingress
        # checks still surface a typed error at config-load time.
        from ragbot.shared.workspace_id_validator import WorkspaceIdValidator
        return WorkspaceIdValidator.validate(v)
