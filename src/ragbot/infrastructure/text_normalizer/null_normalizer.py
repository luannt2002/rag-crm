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
# Reason: text_normalizer never wired in production path.
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

# """NullNormalizer — passthrough Strategy for text normalisation.

# Default selection when ``system_config.text_normalizer_provider`` is
# missing or set to ``"null"``. Returns the input string unchanged.
# """

# from __future__ import annotations


# class NullNormalizer:
#     """No-op normaliser — :meth:`normalize` returns input verbatim."""

#     def __init__(self, **_: object) -> None:
#         return

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def normalize(self, text: str) -> str:
#         return text


# __all__ = ["NullNormalizer"]
