"""TenantScope value object — aggregates the per-request multi-tenant context.

Ref: PLAN_03 §tenant_scope.py / RAGBOT_MASTER §12.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import (
    BotId,
    BotVersion,
    ConversationId,
    CorpusVersion,
    EmbeddingModelVersion,
    TenantId,
    UserId,
)


@dataclass(frozen=True, slots=True)
class TenantScope:
    """Per-request tenant context — required at every boundary.

    A bare TenantScope (only record_tenant_id) is acceptable in admin
    endpoints where record_bot_id is not yet known. For chat / document
    use cases, record_bot_id must be set and `require_bot()` enforces it.

    Naming note: the internal field is `record_tenant_id` (UUID, PK of
    tenants.id). The EXTERNAL upstream integer `tenant_id` from NestJS
    identity is stored on the `bots` row, NOT on this scope.
    """

    record_tenant_id: TenantId
    record_bot_id: BotId | None = None
    user_id: UserId | None = None
    conversation_id: ConversationId | None = None
    bot_version: BotVersion | None = None
    corpus_version: CorpusVersion | None = None
    embedding_model_version: EmbeddingModelVersion | None = None

    def __post_init__(self) -> None:
        if self.record_tenant_id is None:
            raise TenantIsolationViolation("record_tenant_id is required in TenantScope")

    def require_bot(self) -> BotId:
        if self.record_bot_id is None:
            raise TenantIsolationViolation("record_bot_id required for this operation")
        return self.record_bot_id

    def cache_key_prefix(self) -> str:
        """Build canonical cache prefix from ``record_tenant_id`` + optional bot/version."""
        parts: list[str] = [f"t:{self.record_tenant_id!s}"]
        if self.record_bot_id:
            parts.append(f"bot:{self.record_bot_id!s}")
        if self.bot_version is not None:
            parts.append(f"bv:{self.bot_version}")
        if self.corpus_version is not None:
            parts.append(f"cv:{self.corpus_version}")
        if self.embedding_model_version:
            parts.append(f"emv:{self.embedding_model_version}")
        return ":".join(parts)


__all__ = ["TenantScope"]
