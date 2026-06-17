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

# """BartphoAccentNormalizer — STUB Strategy for ML-based VN tone restoration.

# Production wiring requires:
#   pip install transformers torch
#   + bartpho-syllable model snapshot (~ 1.5 GB).

# Default OFF. Operators flip ``system_config.text_normalizer_provider`` to
# ``"bartpho"`` only after the deps land on the box. The constructor raises
# :class:`NotImplementedError` so the registry's fail-soft path falls back
# to NullNormalizer and the install hint surfaces in logs.
# """

# from __future__ import annotations


# class BartphoAccentNormalizer:
#     """ML accent-restore stub — raises until the dep is installed."""

#     def __init__(self, **_: object) -> None:
#         raise NotImplementedError(
#             "VN accent ML restore requires transformers + bartpho-syllable. "
#             "Default OFF. See plans/260429-VN-accent-ML-transformers/plan.md "
#             "for installation + wiring instructions."
#         )

#     @staticmethod
#     def get_provider_name() -> str:
#         return "bartpho"

#     async def normalize(self, text: str) -> str:  # pragma: no cover — unreachable
#         raise NotImplementedError


# __all__ = ["BartphoAccentNormalizer"]
