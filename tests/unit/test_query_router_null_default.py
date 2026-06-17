"""NullQueryRouter — backward-compat default contract.

Pins:
- Always returns the ``semantic`` label, regardless of input.
- ``DEFAULT_QUERY_ROUTER_PROVIDER`` constant is wired to ``"null"`` so
  bootstrap resolution without an explicit operator override lands on
  the Null Object (pipeline preserves pre-S9 behaviour).
- LLMQueryRouter without a wired ``classify_fn`` ALSO degrades to
  ``semantic`` so a partial bootstrap mis-wire cannot break traffic.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.query_router_port import QueryRouterPort
try:
    from ragbot.infrastructure.query_router.llm_query_router import LLMQueryRouter
    from ragbot.infrastructure.query_router.null_query_router import NullQueryRouter
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "query_router subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import (
    DEFAULT_QUERY_ROUTER_PROVIDER,
    QUERY_INTENT_SEMANTIC,
    QUERY_INTENT_TYPES,
)


@pytest.mark.asyncio
async def test_null_router_always_returns_semantic() -> None:
    """Null contract — every input shape lands on semantic."""
    router = NullQueryRouter()
    cases = [
        "",
        "   ",
        "hello",
        "Điều 5 quy định gì",
        "so sánh A vs B",
        "Black Friday khuyến mãi",
        "what is the price",
        "trao đổi thông tin",
    ]
    for query in cases:
        result = await router.classify(query)
        assert result == QUERY_INTENT_SEMANTIC, (
            f"NullQueryRouter must always return semantic; "
            f"failed on {query!r} -> {result!r}"
        )


def test_default_query_router_provider_constant_is_null() -> None:
    """Schema default MUST be ``null`` — backward-compat baseline.

    Flipping this constant is a behavioural change that breaks the
    "operator-OFF until opt-in" contract; that flip should go through
    a deliberate alembic migration + plan, not a constants edit.
    """
    assert DEFAULT_QUERY_ROUTER_PROVIDER == "null"


def test_semantic_label_is_member_of_intent_types() -> None:
    """The Null fallback label must be a valid QueryIntent vocabulary entry."""
    assert QUERY_INTENT_SEMANTIC in QUERY_INTENT_TYPES


@pytest.mark.asyncio
async def test_llm_router_without_classify_fn_degrades_to_semantic() -> None:
    """No callable wired -> degrade silent to semantic (graceful)."""
    router = LLMQueryRouter()
    assert await router.classify("any input") == QUERY_INTENT_SEMANTIC


@pytest.mark.asyncio
async def test_llm_router_classify_fn_validates_label_membership() -> None:
    """Wired callable returning a non-vocabulary string -> semantic fallback.

    A wobbly LLM that returns ``"something_else"`` must not corrupt the
    downstream graph state — the strategy collapses to the catch-all.
    """

    async def _bad_classify(q: str) -> str:
        del q
        return "not_a_real_intent_label"

    router = LLMQueryRouter(classify_fn=_bad_classify)
    assert await router.classify("hello") == QUERY_INTENT_SEMANTIC


@pytest.mark.asyncio
async def test_llm_router_respects_valid_classify_fn() -> None:
    """Wired callable returning a valid label MUST be honoured."""

    async def _good_classify(q: str) -> str:
        del q
        return "comparison"

    router = LLMQueryRouter(classify_fn=_good_classify)
    assert await router.classify("any") == "comparison"


def test_null_router_implements_port_protocol() -> None:
    assert isinstance(NullQueryRouter(), QueryRouterPort)
    assert isinstance(LLMQueryRouter(), QueryRouterPort)


def test_null_router_provider_name_pin() -> None:
    assert NullQueryRouter.get_provider_name() == "null"
    assert LLMQueryRouter.get_provider_name() == "llm"
