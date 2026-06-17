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
# Reason: tenant_model_tier never imported outside its own dir.
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

# """Per-tenant model-tier strategies (Null + Static + Registry)."""

# from ragbot.infrastructure.tenant_model_tier.null_tenant_model_tier import (
#     NullTenantModelTier,
# )
# from ragbot.infrastructure.tenant_model_tier.registry import (
#     build_tenant_model_tier,
#     list_providers,
# )
# from ragbot.infrastructure.tenant_model_tier.static_tenant_model_tier import (
#     StaticTenantModelTier,
# )

# __all__ = [
#     "NullTenantModelTier",
#     "StaticTenantModelTier",
#     "build_tenant_model_tier",
#     "list_providers",
# ]
