# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: Not registered in embedding/registry.py (_REGISTRY = {litellm, jina, zeroentropy, bkai_vn}).
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """Null embedder — graceful degrade when no embedding strategy is configured.

# Used as the default in the registry when ``embedding_provider`` is unset or
# unknown, or when concrete strategies fail to construct (e.g. missing API
# keys at boot).

# The orchestrator's ``_embed_query`` falls through to the BM25-only retrieval
# path on empty-vector returns. ``NullEmbedder.embed_*`` raises
# ``EmbeddingError`` so failover wrappers can detect the dead state and route
# to a secondary; ``embed_one`` / ``embed_batch`` (the ``EmbeddingPort``
# surface) follow the same contract so the orchestrator's narrow-except in
# ``_embed_query`` translates the raise into ``[]`` — exactly what the
# BM25-only fallback path consumes.

# ``health_check`` returns ``False`` so any boot-time probe surfaces the
# disabled state without raising.
# """

# from __future__ import annotations

# from typing import Any

# import structlog

# from ragbot.application.dto.ai_specs import EmbeddingSpec
# from ragbot.application.ports.embedder_port import EmbedderPort
# from ragbot.application.ports.embedding_port import EmbeddingPort
# from ragbot.shared.errors import EmbeddingError
# from ragbot.shared.types import TenantId

# logger = structlog.get_logger(__name__)


# class NullEmbedder(EmbedderPort, EmbeddingPort):
#     """No-op embedder — every call raises ``EmbeddingError``.

#     Implements both ``EmbedderPort`` (minimal failover-friendly contract)
#     and ``EmbeddingPort`` (orchestrator surface) so the registry can return
#     a single instance regardless of which port the caller asked for.
#     """

#     _MODEL_ID = "null"
#     _DIMENSION = 0

#     def __init__(self, **_: Any) -> None:
        # Accept arbitrary kwargs so the registry's filtered-kwargs forward
        # cannot crash construction — keeps the fail-soft default stable.
#         pass

#     @property
#     def dimension(self) -> int:
#         return self._DIMENSION

#     @property
#     def model_id(self) -> str:
#         return self._MODEL_ID

#     async def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
#         raise EmbeddingError("embedder disabled (null strategy)")

#     async def embed_documents(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
#         raise EmbeddingError("embedder disabled (null strategy)")

#     async def health_check(self) -> bool:
#         return False

    # ``EmbeddingPort`` surface — orchestrator may call these.
#     async def embed_one(
#         self,
#         text: str,
#         *,
#         spec: EmbeddingSpec | None = None,  # noqa: ARG002
#         record_tenant_id: TenantId | None = None,  # noqa: ARG002
#     ) -> list[float]:
#         raise EmbeddingError("embedder disabled (null strategy)")

#     async def embed_batch(
#         self,
#         texts: list[str],
#         *,
#         spec: EmbeddingSpec | None = None,  # noqa: ARG002
#         record_tenant_id: TenantId | None = None,  # noqa: ARG002
#     ) -> list[list[float]]:
#         raise EmbeddingError("embedder disabled (null strategy)")

#     async def close(self) -> None:
#         return None


# __all__ = ["NullEmbedder"]
