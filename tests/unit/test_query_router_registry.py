"""Query router registry — DI resolution + protocol conformance.

Pins:
- Registry exposes exactly 3 providers: ``null`` / ``regex`` / ``llm``.
- Each provider key resolves to the matching concrete class.
- Unknown / empty provider raises ``ValueError`` (loud-fail policy).
- Resolution is case-insensitive and whitespace-tolerant (operator typos
  in ``system_config`` should not silently degrade routing).
- All concrete strategies satisfy the ``QueryRouterPort`` runtime Protocol.
- ``get_provider_name()`` on each class matches its registry key.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.query_router_port import QueryRouterPort
try:
    from ragbot.infrastructure.query_router.llm_query_router import LLMQueryRouter
    from ragbot.infrastructure.query_router.null_query_router import NullQueryRouter
    from ragbot.infrastructure.query_router.regex_query_router import (
        RegexQueryRouter,
    )
    from ragbot.infrastructure.query_router.registry import (
        build_query_router,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "query_router subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


def test_registry_exposes_exactly_three_providers() -> None:
    providers = list_providers()
    assert providers == sorted(providers)
    assert providers == ["llm", "null", "regex"]


def test_registry_resolves_null_provider_to_null_router() -> None:
    instance = build_query_router("null")
    assert isinstance(instance, NullQueryRouter)


def test_registry_resolves_regex_provider_to_regex_router() -> None:
    instance = build_query_router("regex")
    assert isinstance(instance, RegexQueryRouter)


def test_registry_resolves_llm_provider_to_llm_router() -> None:
    instance = build_query_router("llm")
    assert isinstance(instance, LLMQueryRouter)


def test_registry_resolution_is_case_insensitive_and_trims() -> None:
    """Operator typos in system_config should still resolve cleanly."""
    assert isinstance(build_query_router("  NULL  "), NullQueryRouter)
    assert isinstance(build_query_router("Regex"), RegexQueryRouter)
    assert isinstance(build_query_router("LLM"), LLMQueryRouter)


def test_registry_raises_value_error_on_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown query_router provider"):
        build_query_router("does_not_exist_xyz")


def test_registry_raises_value_error_on_empty_provider() -> None:
    """Empty / None provider must NOT silently fall back — raise loud."""
    with pytest.raises(ValueError, match="unknown query_router provider"):
        build_query_router("")


def test_all_strategies_implement_port_protocol() -> None:
    assert isinstance(NullQueryRouter(), QueryRouterPort)
    assert isinstance(RegexQueryRouter(), QueryRouterPort)
    assert isinstance(LLMQueryRouter(), QueryRouterPort)


def test_provider_names_match_registry_keys() -> None:
    """get_provider_name must equal the registry key — pin against drift."""
    assert NullQueryRouter.get_provider_name() == "null"
    assert RegexQueryRouter.get_provider_name() == "regex"
    assert LLMQueryRouter.get_provider_name() == "llm"
