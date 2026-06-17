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
# Reason: Part of unused multi_agent_review subpackage.
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

# from ragbot.application.services.multi_agent_review.agents.auditor_agent import (
#     AuditorAgent,
# )
# from ragbot.application.services.multi_agent_review.agents.specialist_agent import (
#     SpecialistAgent,
# )

# __all__ = ["AuditorAgent", "SpecialistAgent"]
