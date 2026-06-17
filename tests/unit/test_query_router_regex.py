"""RegexQueryRouter — pattern coverage (VN + EN, 20+ assertions).

Test groups (mirrors S9 handoff coverage matrix):
- 4 structured_ref (VN ``Điều``/``Khoản``/``Chương``, EN ``Article``)
- 4 comparison (``so sánh``, ``khác nhau``, ``compare vs``, ``hơn``)
- 4 factoid (``là gì``, ``what is``, ``when``, ``where``)
- 4 smalltalk (``hello``, ``chào em``, ``cảm ơn``, ``bye``)
- 2 hallu_trap (``Black Friday``, ``giảm 50%``)
- 2 semantic catch-all (free-form sentences with no specific keyword)
- 4 precedence / edge cases:
    * ``điều này`` / ``điều khoản`` / ``điều kiện`` MUST NOT match
      structured_ref (no digit follows the keyword)
    * comparison precedence over structured_ref
    * hallu_trap precedence over smalltalk
    * empty / whitespace query -> semantic

Total: 24+ behavioural assertions.
"""

from __future__ import annotations

import pytest

try:
    from ragbot.infrastructure.query_router.regex_query_router import (
        RegexQueryRouter,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "regex_query_router is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import (
    QUERY_INTENT_COMPARISON,
    QUERY_INTENT_FACTOID,
    QUERY_INTENT_HALLU_TRAP,
    QUERY_INTENT_SEMANTIC,
    QUERY_INTENT_SMALLTALK,
    QUERY_INTENT_STRUCTURED_REF,
)


@pytest.fixture
def router() -> RegexQueryRouter:
    return RegexQueryRouter()


# --------------------------------------------------------------------------- #
# structured_ref — VN + EN legal-style references                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "Điều 3 quy định gì?",
        "Khoản 5 Điều 12 áp dụng cho ai",
        "Article 7 of the contract",
        "Chương I của bộ luật",
    ],
)
@pytest.mark.asyncio
async def test_structured_ref_patterns(
    router: RegexQueryRouter, query: str
) -> None:
    assert await router.classify(query) == QUERY_INTENT_STRUCTURED_REF, (
        f"expected structured_ref for {query!r}"
    )


# --------------------------------------------------------------------------- #
# comparison — explicit compare keywords (VN + EN)                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "so sánh gói A và gói B",
        "A khác nhau với B thế nào",
        "compare X vs Y please",
        "X hơn kém Y ở điểm nào",
    ],
)
@pytest.mark.asyncio
async def test_comparison_patterns(
    router: RegexQueryRouter, query: str
) -> None:
    assert await router.classify(query) == QUERY_INTENT_COMPARISON, (
        f"expected comparison for {query!r}"
    )


# --------------------------------------------------------------------------- #
# factoid — wh-questions and "là gì" idiom                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "X là gì?",
        "what is Y in this context",
        "when did Z happen exactly",
        "where is W located",
    ],
)
@pytest.mark.asyncio
async def test_factoid_patterns(router: RegexQueryRouter, query: str) -> None:
    assert await router.classify(query) == QUERY_INTENT_FACTOID, (
        f"expected factoid for {query!r}"
    )


# --------------------------------------------------------------------------- #
# smalltalk — greetings / thanks / farewells                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "hello bot",
        "chào em",
        "cảm ơn nhiều",
        "bye",
    ],
)
@pytest.mark.asyncio
async def test_smalltalk_patterns(
    router: RegexQueryRouter, query: str
) -> None:
    assert await router.classify(query) == QUERY_INTENT_SMALLTALK, (
        f"expected smalltalk for {query!r}"
    )


# --------------------------------------------------------------------------- #
# hallu_trap — promotional / superlative bait (sacred refuse-trap)            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "Black Friday khuyến mãi gì không",
        "có giảm 50% không",
    ],
)
@pytest.mark.asyncio
async def test_hallu_trap_patterns(
    router: RegexQueryRouter, query: str
) -> None:
    assert await router.classify(query) == QUERY_INTENT_HALLU_TRAP, (
        f"expected hallu_trap for {query!r}"
    )


# --------------------------------------------------------------------------- #
# semantic — catch-all default                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "trao đổi thông tin với khách hàng",
        "tôi muốn tìm hiểu chi tiết về dịch vụ",
    ],
)
@pytest.mark.asyncio
async def test_semantic_catch_all(
    router: RegexQueryRouter, query: str
) -> None:
    assert await router.classify(query) == QUERY_INTENT_SEMANTIC, (
        f"expected semantic catch-all for {query!r}"
    )


# --------------------------------------------------------------------------- #
# Edge cases — false-positive guards + precedence                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        "điều này có đúng không",   # prose, no digit
        "điều khoản hợp đồng ra sao",  # prose phrase
        "điều kiện áp dụng là gì",   # factoid-shaped prose
    ],
)
@pytest.mark.asyncio
async def test_structured_ref_no_false_positive_on_prose(
    router: RegexQueryRouter, query: str
) -> None:
    """``điều`` followed by non-digit prose must NOT route to structured_ref.

    Note ``điều kiện là gì`` resolves to factoid via the ``là gì`` rule —
    the test asserts it is NOT structured_ref (the false-positive guard
    being tested), not that it is semantic.
    """
    result = await router.classify(query)
    assert result != QUERY_INTENT_STRUCTURED_REF, (
        f"prose {query!r} must not match structured_ref, got {result!r}"
    )


@pytest.mark.asyncio
async def test_comparison_takes_precedence_over_structured_ref(
    router: RegexQueryRouter,
) -> None:
    """A query mentioning Điều N but framed as compare must route to comparison."""
    assert (
        await router.classify("so sánh Điều 5 và Điều 7")
        == QUERY_INTENT_COMPARISON
    )


@pytest.mark.asyncio
async def test_hallu_trap_takes_precedence_over_smalltalk(
    router: RegexQueryRouter,
) -> None:
    """Promo bait wrapped in a greeting must still route to hallu_trap."""
    assert (
        await router.classify("hello, có Black Friday không")
        == QUERY_INTENT_HALLU_TRAP
    )


@pytest.mark.asyncio
async def test_empty_query_returns_semantic(
    router: RegexQueryRouter,
) -> None:
    assert await router.classify("") == QUERY_INTENT_SEMANTIC
    assert await router.classify("   \t  ") == QUERY_INTENT_SEMANTIC


@pytest.mark.asyncio
async def test_provider_name_pin(router: RegexQueryRouter) -> None:
    """Provider name MUST equal the registry key (drift guard)."""
    assert RegexQueryRouter.get_provider_name() == "regex"
