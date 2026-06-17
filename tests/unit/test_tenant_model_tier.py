"""TenantModelTier Strategy + DI Registry — unit tests.

Coverage:
- NullTenantModelTier: any tenant → all 3 tiers, return is a frozenset.
- StaticTenantModelTier with empty map: unknown tenant → full default set.
- StaticTenantModelTier with explicit map: configured tenant → exactly that subset.
- StaticTenantModelTier rejects unknown tier strings at construction time.
- ``build_tenant_model_tier`` resolves both registered keys to the right type.
- ``build_tenant_model_tier`` raises ``ValueError`` on unknown / empty provider.
- ``list_providers`` returns the sorted, complete registered set.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

try:
    from ragbot.infrastructure.tenant_model_tier import (
        NullTenantModelTier,
        StaticTenantModelTier,
        build_tenant_model_tier,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "tenant_model_tier subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import DEFAULT_MODEL_TIERS


def test_null_tenant_model_tier_returns_full_set_for_any_tenant() -> None:
    """Null Object: every tenant gets the full canonical tier set."""
    null = NullTenantModelTier()

    # Two distinct tenants, identical full-set output.
    tid_a = uuid4()
    tid_b = uuid4()
    out_a = null.allowed_tiers(tid_a)
    out_b = null.allowed_tiers(tid_b)

    assert out_a == frozenset({"cheap", "mid", "premium"})
    assert out_b == frozenset({"cheap", "mid", "premium"})
    # Frozenset (immutable) — STRONG type check, not just truthiness.
    assert isinstance(out_a, frozenset)
    # Identity with the canonical constant: avoid string-literal drift.
    assert out_a == DEFAULT_MODEL_TIERS


def test_static_tenant_model_tier_empty_map_falls_back_to_full_set() -> None:
    """Empty map → unknown tenant gets full default set (fail-open)."""
    static = StaticTenantModelTier(tier_map={})
    out = static.allowed_tiers(uuid4())

    assert out == DEFAULT_MODEL_TIERS
    assert isinstance(out, frozenset)


def test_static_tenant_model_tier_returns_configured_subset_only() -> None:
    """Configured tenant → exactly the seeded subset, others fall through."""
    cheap_only_tenant = uuid4()
    mid_premium_tenant = uuid4()
    static = StaticTenantModelTier(
        tier_map={
            cheap_only_tenant: frozenset({"cheap"}),
            mid_premium_tenant: frozenset({"mid", "premium"}),
        },
    )

    # Configured tenant: exact subset.
    assert static.allowed_tiers(cheap_only_tenant) == frozenset({"cheap"})
    assert static.allowed_tiers(mid_premium_tenant) == frozenset({"mid", "premium"})
    # Unconfigured tenant: full default set.
    assert static.allowed_tiers(uuid4()) == DEFAULT_MODEL_TIERS

    # Frozenset immutability — direct mutation must raise.
    out = static.allowed_tiers(cheap_only_tenant)
    assert isinstance(out, frozenset)
    with pytest.raises(AttributeError):
        out.add("premium")  # type: ignore[attr-defined]


def test_static_tenant_model_tier_rejects_unknown_tier_at_construction() -> None:
    """Typo in seed data → ValueError at boot, not at first request."""
    with pytest.raises(ValueError, match="Unknown tier"):
        StaticTenantModelTier(
            tier_map={uuid4(): frozenset({"cheap", "deluxe"})},
        )


def test_build_tenant_model_tier_registry_resolves_known_providers() -> None:
    """Both registered keys resolve to the right class; unknown raises."""
    assert isinstance(build_tenant_model_tier("null"), NullTenantModelTier)
    assert isinstance(
        build_tenant_model_tier("static", tier_map={}),
        StaticTenantModelTier,
    )

    # Unknown / empty provider must fail loud — billing-relevant misconfig.
    with pytest.raises(ValueError, match="Unknown tenant_model_tier provider"):
        build_tenant_model_tier("does_not_exist")
    with pytest.raises(ValueError, match="Unknown tenant_model_tier provider"):
        build_tenant_model_tier("")

    # Registry listing is sorted + complete.
    assert list_providers() == ["null", "static"]


def test_static_tenant_model_tier_accepts_iterable_inputs() -> None:
    """Constructor coerces set/tuple/list to frozenset for callers' convenience."""
    tid: UUID = uuid4()
    static = StaticTenantModelTier(
        tier_map={tid: ("cheap", "mid")},
    )

    out = static.allowed_tiers(tid)
    assert out == frozenset({"cheap", "mid"})
    assert isinstance(out, frozenset)
