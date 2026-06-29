"""Shared primitive type aliases / NewTypes.

Ref: docs/application/PLAN_02_CONVENTIONS_BASE_CONTRACTS.md §types.py.

Using NewType gives static type safety without runtime cost — you cannot
accidentally pass a BotId where a TenantId is expected.
"""

from __future__ import annotations

from typing import Literal, NewType
from uuid import UUID

# --- Identity (opaque) -------------------------------------------------------
TenantId = NewType("TenantId", UUID)
BotId = NewType("BotId", UUID)
ConversationId = NewType("ConversationId", UUID)
DocumentId = NewType("DocumentId", UUID)
ChunkId = NewType("ChunkId", UUID)
MessageId = NewType("MessageId", UUID)
# v0.3.0 — `external_message_id` is the upstream service's INT; ragbot stores
# it verbatim (no FK, no transform) so metrics can be grouped per upstream message.
ExternalMessageId = NewType("ExternalMessageId", int)
JobId = NewType("JobId", UUID)
# v0.4.0 — External identifiers used by upstream bot registry.
# `bots` table stores `bot_id VARCHAR + channel_type VARCHAR + tenant_id INT NULL`.
ExternalBotId = NewType("ExternalBotId", str)
ChannelType = NewType("ChannelType", str)
ExternalTenantId = NewType("ExternalTenantId", int)
UserId = NewType("UserId", str)  # external: zalo peer_id, telegram user id, ...
TraceId = NewType("TraceId", str)
IdempotencyKey = NewType("IdempotencyKey", str)  # sha256 hex
# Tenant-scoped workspace slug — 4th identity key paired with
# (record_tenant_id, bot_id, channel_type). Pure pass-through string;
# format enforced by WorkspaceIdValidator at ingress, not the type system.
WorkspaceId = NewType("WorkspaceId", str)

# --- Versioning --------------------------------------------------------------
CorpusVersion = NewType("CorpusVersion", int)
BotVersion = NewType("BotVersion", int)
EmbeddingModelVersion = NewType("EmbeddingModelVersion", str)

# --- Enumerations (Literal preferred over Enum for JSON serializability) -----
Channel = Literal[
    "api",       # default — REST API caller
    "web",       # web widget
    "internal",  # internal services / cron
]
# NOTE: External channels (Telegram/Messenger/Zalo/...) live in separate
# adapter services that translate channel-specific shape → REST `/ragbot/chat`.
# This service is RAGbot-only — channel-agnostic.

Role = Literal["user", "assistant", "system", "tool"]

JobStatus = Literal[
    "queued",
    "running",
    "success",
    "failed",
    "cancelled",
    "dlq",
]

DocumentState = Literal[
    "DRAFT",
    "PUBLISHED",
    "UPDATED",
    "SUPERSEDED",
    "ARCHIVED",
    "PURGED",
    "INVALIDATED",
]

# Record-of-truth for the strategy a document was ACTUALLY chunked with.
# Two vocabularies coexist by design:
#   - AdapChunk taxonomy (uppercase): HDT / SEMANTIC / PROPOSITION / HYBRID —
#     the block-pipeline emit names carried onto every Chunk.
#   - Runtime selector names (lowercase): what ``select_strategy`` /
#     ``apply_cross_check`` and the special ingest branches actually pick
#     (recursive/semantic/hdt/hybrid/proposition + whole_document /
#     parent_child / parser_preserve / table_csv / table_dual_index).
# The ingest pipeline surfaces the runtime name verbatim so the
# ``DocumentIngested`` event is a faithful record-of-truth rather than a
# lossy bucket. This alias is a static type-hint only (no runtime
# validation, no DB CHECK constraint), so widening it is backward-compatible.
ChunkingStrategyName = Literal[
    # AdapChunk block-pipeline taxonomy
    "HDT",
    "SEMANTIC",
    "PROPOSITION",
    "HYBRID",
    # runtime selector / special-branch names
    "recursive",
    "semantic",
    "hdt",
    "hybrid",
    "proposition",
    "whole_document",
    "parent_child",
    "parser_preserve",
    "table_csv",
    "table_dual_index",
]

BlockType = Literal["HEADING", "TEXT", "TABLE", "FORMULA", "IMAGE", "CODE", "LIST"]

QueryIntent = Literal[
    "factoid",
    "multi_hop",
    "aggregation",
    "conversational",
    "out_of_scope",
    "realtime",
]

LLMIntent = Literal[
    "routing",
    "generation",
    "grading",
    "rewriting",
    "reflection",
    "narration",
    "contextualization",
    "proposition",
]

ModerationResultKind = Literal["safe", "blocked", "flagged"]

__all__ = [
    "BlockType",
    "BotId",
    "BotVersion",
    "Channel",
    "ChannelType",
    "ChunkId",
    "ChunkingStrategyName",
    "ConversationId",
    "CorpusVersion",
    "DocumentId",
    "DocumentState",
    "EmbeddingModelVersion",
    "ExternalBotId",
    "ExternalMessageId",
    "ExternalTenantId",
    "IdempotencyKey",
    "JobId",
    "JobStatus",
    "LLMIntent",
    "MessageId",
    "ModerationResultKind",
    "QueryIntent",
    "Role",
    "TenantId",
    "TraceId",
    "UserId",
    "WorkspaceId",
]
