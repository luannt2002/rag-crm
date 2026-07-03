"""Idempotency key derivation.

Ref: PLAN_03 §idempotency_key.py / RAGBOT_MASTER §14.4.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from ragbot.shared.types import IdempotencyKey


def build_idempotency_key(*parts: str) -> IdempotencyKey:
    """Build a sha256(part1|part2|...) key."""
    if not parts:
        raise ValueError("at least one part required")
    joined = "|".join(part.strip() for part in parts if part is not None)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return IdempotencyKey(digest)


def for_chat_message(
    *,
    record_tenant_id: UUID,
    record_bot_id: UUID,
    user_id: str,
    external_message_id: str | None,
) -> IdempotencyKey:
    """Idempotency key for an inbound chat message."""
    return build_idempotency_key(
        "chat",
        str(record_tenant_id),
        str(record_bot_id),
        str(user_id),
        external_message_id or "no-ext-id",
    )


def for_ingest_document(
    *,
    record_tenant_id: UUID,
    record_bot_id: UUID,
    source_url: str,
    corpus_version: int,
    workspace_id: str = "",
) -> IdempotencyKey:
    """Idempotency key for a document ingestion job.

    ``record_bot_id`` (+ ``workspace_id``) are part of the key. Without the bot, a second bot in the same tenant ingesting the same
    ``source_url`` within the 24h TTL collided and was silently swallowed —
    ``for_chat_message`` already scoped by bot, this mirrors it. 4-key identity.
    """
    return build_idempotency_key(
        "ingest",
        str(record_tenant_id),
        workspace_id or "system",
        str(record_bot_id),
        source_url,
        str(corpus_version),
    )


__all__ = ["build_idempotency_key", "for_chat_message", "for_ingest_document"]
