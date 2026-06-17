"""Cache Protocol + key builders.

Ref: PLAN_06 §cache_port.py / RAGBOT_MASTER §19.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ragbot.shared.constants import (
    CACHE_KEY_CHUNKS,
    CACHE_KEY_EMBEDDING,
    CACHE_KEY_RESPONSE,
    CACHE_KEY_SEMANTIC,
)
from ragbot.shared.types import (
    BotId,
    BotVersion,
    CorpusVersion,
    EmbeddingModelVersion,
    TenantId,
)


@runtime_checkable
class CachePort(Protocol):
    async def health_check(self) -> bool: ...

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, *, ttl_s: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CachedResponse:
    answer: str
    citations: list[dict[str, Any]]
    model_name: str
    cached_at_ts: int
    # 2026-05-27 — graded chunks snapshot so cache_hit responses also expose
    # sources to /chat API consumers (RAGAS judge, audit tools, evaluator).
    # Empty tuple keeps backward-compat for existing callers that don't pass it.
    chunks: tuple[dict[str, Any], ...] = ()


@runtime_checkable
class SemanticCachePort(Protocol):
    async def find_similar(
        self,
        query_embedding: list[float],
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        threshold: float = 0.97,
    ) -> CachedResponse | None: ...

    async def store(
        self,
        *,
        query: str,
        query_embedding: list[float],
        response: CachedResponse,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        workspace_id: str,
        bot_version: BotVersion,
        corpus_version: CorpusVersion,
        ttl_s: int = 3600,
    ) -> None: ...


# --- Key builders (pure functions) ------------------------------------------
def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _scope(
    record_tenant_id: TenantId,
    record_bot_id: BotId,
    bot_version: BotVersion,
    corpus_version: CorpusVersion,
) -> str:
    return f"t:{record_tenant_id}:bot:{record_bot_id}:bv:{bot_version}:cv:{corpus_version}"


def build_semantic_cache_key(
    *,
    record_tenant_id: TenantId,
    record_bot_id: BotId,
    bot_version: BotVersion,
    corpus_version: CorpusVersion,
) -> str:
    return f"{CACHE_KEY_SEMANTIC}:{_scope(record_tenant_id, record_bot_id, bot_version, corpus_version)}"


def build_response_cache_key(
    *,
    record_tenant_id: TenantId,
    record_bot_id: BotId,
    bot_version: BotVersion,
    corpus_version: CorpusVersion,
    prompt_hash: str,
) -> str:
    return (
        f"{CACHE_KEY_RESPONSE}:"
        f"{_scope(record_tenant_id, record_bot_id, bot_version, corpus_version)}:p:{_h(prompt_hash)}"
    )


def build_chunks_cache_key(
    *,
    record_tenant_id: TenantId,
    record_bot_id: BotId,
    corpus_version: CorpusVersion,
    query_norm: str,
    filters_hash: str,
) -> str:
    return (
        f"{CACHE_KEY_CHUNKS}:t:{record_tenant_id}:bot:{record_bot_id}:cv:{corpus_version}:"
        f"q:{_h(query_norm)}:f:{_h(filters_hash)}"
    )


def build_embedding_cache_key(
    *,
    embedding_model_version: EmbeddingModelVersion,
    text: str,
) -> str:
    return f"{CACHE_KEY_EMBEDDING}:emv:{embedding_model_version}:h:{_h(text)}"


__all__ = [
    "CachePort",
    "CachedResponse",
    "SemanticCachePort",
    "build_chunks_cache_key",
    "build_embedding_cache_key",
    "build_response_cache_key",
    "build_semantic_cache_key",
]
