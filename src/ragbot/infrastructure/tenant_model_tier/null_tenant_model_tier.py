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

# """NullTenantModelTier — Null Object: every tenant gets the full tier set.

# Selecting this strategy is the explicit operator-OFF baseline: the
# per-tenant filter is a no-op, so every tenant is allowed to consume
# ``cheap``, ``mid`` and ``premium`` bindings. The registry defaults to
# this strategy so a missing/empty ``tenant_model_tier_provider`` config
# key never silently downgrades any tenant.
# """

# from __future__ import annotations

# from uuid import UUID

# from ragbot.shared.constants import DEFAULT_MODEL_TIERS


# class NullTenantModelTier:
#     """No-op tier filter — returns the full tier set for any tenant."""

#     def allowed_tiers(self, record_tenant_id: UUID) -> frozenset[str]:
        # Argument intentionally ignored — Null Object semantics.
#         del record_tenant_id
#         return DEFAULT_MODEL_TIERS


# __all__ = ["NullTenantModelTier"]
