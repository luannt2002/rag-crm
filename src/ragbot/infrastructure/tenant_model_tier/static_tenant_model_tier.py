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

# """StaticTenantModelTier — in-memory map ``record_tenant_id`` → tier subset.

# Used in tests and bootstrap-from-config scenarios where the allow-list
# is small enough to live entirely in process memory. Unknown tenants
# fall back to the full ``DEFAULT_MODEL_TIERS`` set so a missing entry
# never silently downgrades a tenant that simply has not been seeded yet.

# The map is normalised at construction time:
#   * each value is coerced to ``frozenset[str]`` (immutability + hashable).
#   * tier strings outside ``DEFAULT_MODEL_TIERS`` raise ``ValueError`` so
#     a typo in seed data is caught at boot, not at first request.
# """

# from __future__ import annotations

# from collections.abc import Iterable, Mapping
# from uuid import UUID

# from ragbot.shared.constants import DEFAULT_MODEL_TIERS

# Any iterable of tier strings is accepted; constructor coerces to frozenset.
# TierIterable = Iterable[str]


# class StaticTenantModelTier:
#     """In-memory tier filter backed by an immutable per-tenant map."""

#     def __init__(
#         self,
#         tier_map: Mapping[UUID, TierIterable] | None = None,
#     ) -> None:
#         normalised: dict[UUID, frozenset[str]] = {}
#         for tenant_id, tiers in (tier_map or {}).items():
#             tier_set = frozenset(tiers)
#             unknown = tier_set - DEFAULT_MODEL_TIERS
#             if unknown:
#                 raise ValueError(
#                     f"Unknown tier(s) {sorted(unknown)} for tenant {tenant_id};"
#                     f" allowed={sorted(DEFAULT_MODEL_TIERS)}",
#                 )
#             normalised[tenant_id] = tier_set
#         self._map: dict[UUID, frozenset[str]] = normalised

#     def allowed_tiers(self, record_tenant_id: UUID) -> frozenset[str]:
        # Unknown tenant → fall through to full set so a yet-to-be-seeded
        # tenant is never silently locked out.
#         return self._map.get(record_tenant_id, DEFAULT_MODEL_TIERS)


# __all__ = ["StaticTenantModelTier"]
