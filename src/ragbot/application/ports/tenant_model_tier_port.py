"""TenantModelTierPort — Protocol for per-tenant model-tier allow-listing.

Thin pre-filter that maps a tenant UUID onto the subset of cost-aware
quality tiers (``cheap`` / ``mid`` / ``premium``) that the tenant is
entitled to consume. The ``ModelResolverService`` flow itself is left
untouched; downstream bot-config code applies this subset against the
list of available bindings before any LLM/embedding/reranker call.

Default behaviour (Null Object) returns the full tier set so unknown
tenants are never silently downgraded.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class TenantModelTierPort(Protocol):
    """Resolve which model tiers a tenant is allowed to use.

    Implementations MUST be deterministic + side-effect-free for a given
    ``record_tenant_id``. Returned set is an immutable ``frozenset`` so
    callers can cache it without defensive copies.
    """

    def allowed_tiers(self, record_tenant_id: UUID) -> frozenset[str]:
        """Return the allowed cost-tier subset for the tenant.

        @param record_tenant_id: UUID PK of the ``tenants`` row.
        @return: Subset of ``{"cheap", "mid", "premium"}``. Empty result
                 means "no tier allowed"; callers decide whether that is
                 a hard fail or a NullObject fall-through.
        """
        ...


__all__ = ["TenantModelTierPort"]
