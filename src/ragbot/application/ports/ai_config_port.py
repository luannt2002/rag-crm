"""AI config repository port (DB-driven model config).

Ports for `ai_providers`, `ai_models`, `bot_model_bindings`, `prompt_templates`,
`intent_routes`, `bot_ai_tools`, `ai_config_audit_log`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ragbot.shared.types import BotId, TenantId


@dataclass(frozen=True, slots=True)
class ProviderRow:
    id: UUID
    name: str
    code: str  # machine slug for LiteLLM routing + cache_control match
    type: str  # llm | embedding | reranker | moderation
    base_url: str
    auth_type: str
    credentials_vault_path: str | None
    enabled: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    # Alembic 010e — controls LiteLLM wire-name prefixing. TRUE (default,
    # legacy / LiteLLM convention) => ``format_litellm_model`` returns
    # ``{code}/{name}``. FALSE (OpenAI / Anthropic native) => bare model
    # name. The DB column is the single source of truth — flipping a
    # provider takes effect on the next ModelResolver cache miss.
    requires_prefix: bool = True


@dataclass(frozen=True, slots=True)
class ModelRow:
    id: UUID
    provider_id: UUID
    name: str
    kind: str  # chat | embedding | reranker | moderation
    context_window: int
    max_output_tokens: int
    # Pricing kept as ``Decimal`` end-to-end — DB ``Numeric(10,6)`` precision
    # would otherwise be lost via float round-trip (penny leak at scale).
    # Conversion to ``float`` happens only at the JSON/UI boundary.
    input_price_per_1k_usd: Decimal
    output_price_per_1k_usd: Decimal
    supports_streaming: bool
    supports_tools: bool
    supports_vision: bool
    supports_json_mode: bool
    languages: tuple[str, ...]
    enabled: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    # Exposed so ``ModelResolverService`` can populate
    # ``ModelRuntimeConfig.embedding_dimension`` for bots whose binding
    # ``extra_params`` does not pin ``dimension`` (it lives on the model
    # row instead).
    embedding_dimension: int | None = None


@dataclass(frozen=True, slots=True)
class BindingRow:
    id: UUID
    record_tenant_id: UUID
    record_bot_id: UUID
    purpose: str  # llm_primary | llm_fallback | embedding | reranker | moderation_input | moderation_output
    model_id: UUID
    rank: int
    variant: str | None
    weight: int
    temperature: float
    max_tokens: int
    top_p: float
    extra_params: dict[str, Any]
    active: bool
    version: int
    # Same-tier alternate model UUID (FK ai_models.id) consulted when the
    # primary's circuit breaker OPENs or LiteLLM raises a retryable
    # LLMError. ``None`` = no failover configured (per-bot opt-out). One
    # hop max — failure on the fallback re-raises rather than chaining a
    # third try.
    record_fallback_model_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PromptTemplateRow:
    id: UUID
    record_tenant_id: UUID
    record_bot_id: UUID | None  # null = tenant-level default
    template_key: str
    version: int
    jinja_source: str
    required_vars: tuple[str, ...]
    model_hint: str | None
    active: bool


# Migration 0010: IntentRouteRow + BotToolRow removed (tables dropped).


@dataclass(frozen=True, slots=True)
class AuditEntry:
    record_tenant_id: UUID | None
    record_bot_id: UUID | None
    actor_user_id: str
    action: str  # create | update | delete | rollback | rotate_key
    resource_type: str
    resource_id: UUID
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    trace_id: str


@runtime_checkable
class AIConfigRepositoryPort(Protocol):
    # Providers (global) ----------------------------------------------------
    async def list_providers(self, *, enabled_only: bool = True) -> list[ProviderRow]: ...
    async def get_provider(self, provider_id: UUID) -> ProviderRow | None: ...

    # Models (global) -------------------------------------------------------
    async def list_models(
        self,
        *,
        provider_id: UUID | None = None,
        kind: str | None = None,
        enabled_only: bool = True,
    ) -> list[ModelRow]: ...

    async def get_model(self, model_id: UUID) -> ModelRow | None: ...

    # Bindings (per-tenant per-bot) ----------------------------------------
    async def list_bindings(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        purpose: str | None = None,
        active_only: bool = True,
    ) -> list[BindingRow]: ...

    async def list_bindings_multi_purpose(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        purposes: list[str],
        active_only: bool = True,
    ) -> dict[str, list[BindingRow]]:
        """Return ``{purpose: [BindingRow, ...]}`` in ONE SQL round-trip.

        Replaces N sequential ``list_bindings(... purpose=p)`` fan-out
        when the caller needs more than one purpose (typical chat-worker
        boot: ``llm`` + ``embedding`` + ``rerank``). Missing purposes
        appear as empty lists so the caller can use a single ``.get`` /
        ``[]`` lookup without a KeyError branch.
        """
        ...

    async def get_binding(self, binding_id: UUID, *, record_tenant_id: TenantId) -> BindingRow | None: ...

    async def create_binding(self, row: BindingRow) -> BindingRow: ...
    async def update_binding(self, binding_id: UUID, *, record_tenant_id: TenantId, **fields: Any) -> BindingRow: ...
    async def delete_binding(self, binding_id: UUID, *, record_tenant_id: TenantId) -> None: ...

    # Prompt templates -----------------------------------------------------
    async def get_prompt_template(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None,
        template_key: str,
        version: int | None = None,
        active_only: bool = True,
    ) -> PromptTemplateRow | None: ...

    async def list_prompt_template_versions(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None,
        template_key: str,
    ) -> list[int]: ...

    async def create_prompt_template(self, row: PromptTemplateRow) -> PromptTemplateRow: ...
    async def set_prompt_template_active(
        self,
        template_id: UUID,
        *,
        record_tenant_id: TenantId,
        active: bool,
    ) -> None: ...

    # Migration 0010: intent routes + bot tools methods removed.

    # Audit ----------------------------------------------------------------
    async def write_audit(self, entry: AuditEntry) -> None: ...

    async def list_audit(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None = None,
        limit: int = 100,
    ) -> Sequence[dict[str, Any]]: ...


__all__ = [
    "AIConfigRepositoryPort",
    "AuditEntry",
    "BindingRow",
    "ModelRow",
    "PromptTemplateRow",
    "ProviderRow",
]
