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

# """Tenant-model-tier strategy registry — DI factory by provider key.

# Pattern mirrors ``infrastructure.reranker.registry``: caller (DI
# container) reads ``tenant_model_tier_provider`` from ``system_config``
# and asks the registry for the matching ``TenantModelTierPort``
# implementation. Adding a new provider = drop a file in this package
# and register it here; no orchestration code changes.

# Unknown / empty / missing provider keys raise ``ValueError`` at build
# time. The Null Object is the explicit ``"null"`` selection and is also
# the configured default (``DEFAULT_TENANT_MODEL_TIER_PROVIDER``) — there
# is no silent fallback because a wrong tier filter is a billing-relevant
# misconfig that ops should see immediately.
# """

# from __future__ import annotations

# from typing import Any

# from ragbot.application.ports.tenant_model_tier_port import TenantModelTierPort
# from ragbot.infrastructure.tenant_model_tier.null_tenant_model_tier import (
#     NullTenantModelTier,
# )
# from ragbot.infrastructure.tenant_model_tier.static_tenant_model_tier import (
#     StaticTenantModelTier,
# )

# Registry holds CLASSES (not instances) so each ``build_*`` call returns
# a fresh object — DI container may wrap one in a Singleton if a single
# process-wide instance is desired.
# _REGISTRY: dict[str, type[TenantModelTierPort]] = {
#     "null": NullTenantModelTier,
#     "static": StaticTenantModelTier,
# }


# def build_tenant_model_tier(
#     provider: str,
#     **kwargs: Any,
# ) -> TenantModelTierPort:
#     """Construct the tier filter matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"static"``).
#     @param kwargs: forwarded to the strategy constructor (e.g. ``tier_map=``).
#     @return: ``TenantModelTierPort`` instance.
#     @raise ValueError: provider key is empty or not registered.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"Unknown tenant_model_tier provider {provider!r};"
#             f" registered={sorted(_REGISTRY.keys())}",
#         )
#     return cls(**kwargs)


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = ["build_tenant_model_tier", "list_providers"]
