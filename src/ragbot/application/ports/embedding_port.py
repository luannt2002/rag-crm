"""Embedding Protocol (LiteLLM cloud API in infrastructure)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.shared.types import TenantId


@runtime_checkable
class EmbeddingPort(Protocol):
    async def health_check(self) -> bool: ...

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,
    ) -> list[list[float]]: ...

    async def embed_one(
        self,
        text: str,
        *,
        spec: EmbeddingSpec,
        record_tenant_id: TenantId,
    ) -> list[float]: ...

    async def close(self) -> None: ...


__all__ = ["EmbeddingPort"]
