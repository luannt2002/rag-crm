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
# Reason: build_normalizer never called from production hot-path.
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

# WIRED: not yet — ingest_pipeline + answer_question integration.
# """Text normalizer strategy registry — DI factory based on provider key.

# Pattern mirrors :mod:`ragbot.infrastructure.reranker.registry`.

# NOTE: as of commit 74a4dfe this registry is shipped but **not called
# from any production hot-path**. Neither the ingest pipeline nor the
# answer_question use case currently asks for a ``TextNormalizerPort``
# — text normalisation is still inline. will wire
# ``build_normalizer`` into both boundaries.
# """

# from __future__ import annotations

# from typing import TYPE_CHECKING

# import structlog

# from ragbot.infrastructure.text_normalizer.bartpho_accent_normalizer import (
#     BartphoAccentNormalizer,
# )
# from ragbot.infrastructure.text_normalizer.null_normalizer import NullNormalizer

# if TYPE_CHECKING:
#     from ragbot.application.ports.text_normalizer_port import TextNormalizerPort

# logger = structlog.get_logger(__name__)


# _REGISTRY: dict[str, type] = {
#     "null": NullNormalizer,
#     "bartpho": BartphoAccentNormalizer,
# }


# def build_normalizer(
#     provider: str | None = None,
#     **kwargs,
# ) -> "TextNormalizerPort":
#     key = (provider or "").strip().lower() or "null"
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         logger.warning(
#             "text_normalizer_unknown_provider_fallback_null",
#             requested=provider,
#             registered=sorted(_REGISTRY.keys()),
#         )
#         cls = NullNormalizer
#     try:
#         return cls(**kwargs)  # type: ignore[return-value]
#     except (ImportError, NotImplementedError) as exc:
#         logger.error(
#             "text_normalizer_strategy_not_installed",
#             requested=key,
#             error=str(exc),
#         )
#         return NullNormalizer(**kwargs)


# def list_providers() -> list[str]:
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_normalizer", "list_providers"]
