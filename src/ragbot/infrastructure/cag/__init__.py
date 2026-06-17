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
# Reason: CAG infra never wired in bootstrap or graph (no cag port in build_graph).
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

# """CAG (Cache-Augmented Generation) strategy package.

# Re-exports kept intentionally narrow — callers import the Port from
# ``application.ports.cag_port`` and the factory from
# ``infrastructure.cag.registry``. The strategy classes are NOT re-exported
# here so a Sonnet-aided refactor cannot accidentally bypass the registry.
# """

# from __future__ import annotations
