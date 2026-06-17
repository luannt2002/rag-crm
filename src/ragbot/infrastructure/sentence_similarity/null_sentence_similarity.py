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
# Reason: sentence_similarity infra never wired.
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

# """NullSentenceSimilarity — hard-fail probe for misconfigured registry calls.

# Selected by tests / probes that want to assert the registry plumbing without
# exercising real similarity logic. Distinct from the *lexical* default which
# is the production fallback when the operator has not opted into embedding.
# """

# from __future__ import annotations

# import structlog

# logger = structlog.get_logger(__name__)


# class NullSentenceSimilarity:
#     """No-op adapter — always returns ``0.0`` so callers treat every pair as a boundary."""

#     def __init__(self) -> None:
#         self._calls = 0

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     @property
#     def provider_name(self) -> str:
#         return self.get_provider_name()

#     async def similarity(self, s1: str, s2: str) -> float:  # noqa: ARG002
#         self._calls += 1
#         logger.debug("null_sentence_similarity_zero", calls=self._calls)
#         return 0.0

#     def stats(self) -> dict[str, float | int]:
#         return {"calls": self._calls, "cache_hits": 0, "cache_misses": 0}


# __all__ = ["NullSentenceSimilarity"]
