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
# Reason: HyDE infra never wired. Active HyDE path uses application/services/hyde_generator.py instead.
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

# """Hypothetical Document Embedding (HyDE) strategies — Null + LLM.

# Phase-C C1 stream. Owner-opt-in: the registry default (``"null"``) returns
# the raw query unchanged so the retrieve hot path is unaffected until an
# operator flips ``system_config.hyde_enabled`` (tenant-wide) or a bot owner
# flips ``bots.plan_limits.hyde_enabled`` (per-bot).
# """
