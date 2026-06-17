"""Self-RAG / Adaptive router strategy — unit tests.

Pins:
- ``NullSelfRagRouter.should_skip_retrieve`` always returns False
  (operator-OFF baseline preserves existing pipeline behaviour).
- ``IntentBasedSelfRagRouter`` returns True only for intents in
  ``DEFAULT_SELF_RAG_SKIP_INTENTS`` (greeting / chitchat / vu_vo).
- Registry resolves ``"null"`` -> NullSelfRagRouter and ``"intent"``
  -> IntentBasedSelfRagRouter.
- Unknown provider raises ValueError (loud-fail policy — no fallback).
- All strategies satisfy the ``SelfRagRouterPort`` runtime Protocol.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.self_rag_router_port import SelfRagRouterPort
try:
    from ragbot.infrastructure.self_rag_router.intent_based_self_rag_router import (
        IntentBasedSelfRagRouter,
    )
    from ragbot.infrastructure.self_rag_router.null_self_rag_router import (
        NullSelfRagRouter,
    )
    from ragbot.infrastructure.self_rag_router.registry import (
        build_self_rag_router,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "self_rag_router subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import DEFAULT_SELF_RAG_SKIP_INTENTS

# --------------------------------------------------------------------------- #
# (a) NullSelfRagRouter — always False                                        #
# --------------------------------------------------------------------------- #


def test_null_router_never_skips_for_any_intent() -> None:
    """Null Object contract — never skip, regardless of intent / query."""
    router = NullSelfRagRouter()
    # Mix of conversational, retrieval-bearing, and unknown intents.
    cases = [
        ("greeting", "xin chào"),
        ("chitchat", "how are you"),
        ("vu_vo", "..."),
        ("factoid", "what is the price"),
        ("multi_hop", "compare A vs B"),
        ("", ""),
        ("unknown_label", "abc"),
    ]
    for intent, query in cases:
        assert router.should_skip_retrieve(intent, query) is False, (
            f"NullSelfRagRouter must never skip — failed on intent={intent!r}"
        )


# --------------------------------------------------------------------------- #
# (b) IntentBasedSelfRagRouter — skip set = DEFAULT_SELF_RAG_SKIP_INTENTS     #
# --------------------------------------------------------------------------- #


def test_intent_based_router_skips_only_conversational_intents() -> None:
    """Skip set must equal DEFAULT_SELF_RAG_SKIP_INTENTS exactly.

    True for greeting / chitchat / vu_vo; False for retrieval-bearing
    intents (factoid, comparison, aggregation, multi_hop) and for OOS
    / unknown labels.
    """
    router = IntentBasedSelfRagRouter()
    # Skip set fully covered.
    for intent in DEFAULT_SELF_RAG_SKIP_INTENTS:
        assert router.should_skip_retrieve(intent, "any query text") is True, (
            f"intent={intent!r} should be in skip set"
        )
    # Pin the exact membership so a future drift in constants is loud.
    assert frozenset({"greeting", "chitchat", "vu_vo"}) == (
        DEFAULT_SELF_RAG_SKIP_INTENTS
    )
    # Retrieval-bearing intents must NOT be skipped.
    for intent in ("factoid", "comparison", "aggregation", "multi_hop"):
        assert router.should_skip_retrieve(intent, "any query text") is False, (
            f"intent={intent!r} must run retrieve"
        )
    # Unknown / empty intent labels default to running retrieve (safe).
    assert router.should_skip_retrieve("", "x") is False
    assert router.should_skip_retrieve("unknown_label_xyz", "x") is False


def test_intent_based_router_accepts_custom_skip_set() -> None:
    """Constructor override — operator can narrow / widen the skip set."""
    router = IntentBasedSelfRagRouter(skip_intents={"greeting"})
    assert router.skip_intents == frozenset({"greeting"})
    assert router.should_skip_retrieve("greeting", "hi") is True
    # chitchat is in default but excluded from this custom set.
    assert router.should_skip_retrieve("chitchat", "hi") is False


# --------------------------------------------------------------------------- #
# (c) + (d) Registry resolves known providers                                 #
# --------------------------------------------------------------------------- #


def test_registry_resolves_null_provider_to_null_router() -> None:
    instance = build_self_rag_router("null")
    assert isinstance(instance, NullSelfRagRouter)
    # Behavioural confirmation — not just type assertion.
    assert instance.should_skip_retrieve("greeting", "hi") is False


def test_registry_resolves_intent_provider_to_intent_router() -> None:
    instance = build_self_rag_router("intent")
    assert isinstance(instance, IntentBasedSelfRagRouter)
    # Behavioural confirmation — uses the default skip set.
    assert instance.should_skip_retrieve("greeting", "hi") is True
    assert instance.should_skip_retrieve("factoid", "hi") is False


def test_registry_resolution_is_case_insensitive_and_trims() -> None:
    """Whitespace / mixed-case provider strings from system_config must
    still resolve — defensive against typos in operator-edited config."""
    assert isinstance(build_self_rag_router("  NULL  "), NullSelfRagRouter)
    assert isinstance(
        build_self_rag_router("Intent"), IntentBasedSelfRagRouter
    )


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert providers == sorted(providers)
    assert "null" in providers
    assert "intent" in providers
    # Pin count so a drive-by addition is a deliberate test update.
    assert len(providers) == 2


# --------------------------------------------------------------------------- #
# (e) Unknown provider raises ValueError                                      #
# --------------------------------------------------------------------------- #


def test_registry_raises_value_error_on_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown self_rag_router provider"):
        build_self_rag_router("does_not_exist_xyz")


def test_registry_raises_value_error_on_empty_provider() -> None:
    """Empty / None provider must NOT silently fall back — raise loud."""
    with pytest.raises(ValueError, match="unknown self_rag_router provider"):
        build_self_rag_router("")


# --------------------------------------------------------------------------- #
# Port Protocol conformance                                                   #
# --------------------------------------------------------------------------- #


def test_all_strategies_implement_port_protocol() -> None:
    assert isinstance(NullSelfRagRouter(), SelfRagRouterPort)
    assert isinstance(IntentBasedSelfRagRouter(), SelfRagRouterPort)


def test_provider_names_match_registry_keys() -> None:
    """get_provider_name must equal the registry key — pin against drift."""
    assert NullSelfRagRouter.get_provider_name() == "null"
    assert IntentBasedSelfRagRouter.get_provider_name() == "intent"
