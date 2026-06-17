"""Issue 7 regression — decompose system prompts must include multi-entity examples.

Pre-fix, both prompt sources for decompose were thin instructions with
NO few-shot examples:

1. ``i18n._VI_PACK.prompt_decompose`` / ``_EN_PACK.prompt_decompose`` —
   used by ``query_graph.decompose`` node (Adaptive Router L2 path).
2. ``query_decomposer.DECOMPOSER_SYSTEM_PROMPT`` — used by the standalone
   ``decompose_query`` helper called from Adaptive Router L3.

Without examples, gpt-4.1-mini treats compound queries like "Điều 38 và 3"
as a single noun phrase + conjunction, returning 1 sub-query. Downstream:
``decompose_active = len(sub_queries) >= 2`` → False → fanout bypassed.

Post-fix all three prompts include structural multi-entity examples that
do NOT name any specific industry, customer, or product (domain-neutral
compliant). This test pins the contract by asserting key phrases the
examples must contain.

If the prompts are refactored or split, this test must be updated to
follow them — but the principle (examples teach the LLM to split) is
load-bearing for compound-entity correctness and must not silently
regress.
"""

from __future__ import annotations


def test_vn_decompose_prompt_has_multi_entity_example():
    """VN prompt must teach: "X và Y" → 2 sub-queries."""
    from ragbot.shared.i18n import _VI_PACK

    txt = _VI_PACK.prompt_decompose
    assert "X và Y" in txt, "VN prompt missing 2-entity example"
    assert "X, Y, Z" in txt, "VN prompt missing 3-entity list example"
    assert "So sánh" in txt or "so sánh" in txt, "VN prompt missing comparison example"
    assert "NHIỀU entity" in txt, "VN prompt missing the multi-entity rule line"


def test_en_decompose_prompt_has_multi_entity_example():
    """EN prompt must teach: "X and Y" → 2 sub-queries."""
    from ragbot.shared.i18n import _EN_PACK

    txt = _EN_PACK.prompt_decompose
    assert "X and Y" in txt, "EN prompt missing 2-entity example"
    assert "X, Y, Z" in txt, "EN prompt missing 3-entity list example"
    assert "Compare A and B" in txt, "EN prompt missing comparison example"
    assert "MULTIPLE entities" in txt, "EN prompt missing the multi-entity rule line"


def test_standalone_decomposer_prompt_has_multi_entity_example():
    """The Adaptive-L3 standalone prompt also teaches multi-entity split."""
    from ragbot.orchestration.nodes.query_decomposer import DECOMPOSER_SYSTEM_PROMPT

    txt = DECOMPOSER_SYSTEM_PROMPT
    assert "X and Y" in txt, "Standalone decomposer prompt missing 2-entity example"
    assert "X, Y, Z" in txt, "Standalone decomposer prompt missing 3-entity list"
    assert "Compare A and B" in txt, "Standalone decomposer prompt missing comparison example"
    assert "MULTIPLE entities" in txt, "Standalone decomposer prompt missing rule line"


def test_prompts_remain_domain_neutral_no_brand_or_industry_terms():
    """No brand/customer/industry literals in any decompose prompt.

    The platform serves multi-tenant (legal, medical, ecommerce, …).
    Examples must use abstract placeholders (X, Y, Z, document A) only,
    NOT real entity names like 'Vietcombank', 'Article 38', 'invoice', etc.
    """
    from ragbot.shared.i18n import _EN_PACK, _VI_PACK
    from ragbot.orchestration.nodes.query_decomposer import DECOMPOSER_SYSTEM_PROMPT

    # Sample list of substrings that would betray a specific tenant or industry.
    # NOT exhaustive — meant to catch the obvious slip.
    forbidden = [
        "Vietcombank", "VPBank", "Techcombank",   # banking customers
        "medispa", "Dr.",                          # medical brand
        "thông tư 09",                             # specific legal artifact
        "Điều 38",                                  # specific legal article
        "invoice", "purchase order",               # ecommerce literals
    ]
    for source_name, text in (
        ("_VI_PACK.prompt_decompose", _VI_PACK.prompt_decompose),
        ("_EN_PACK.prompt_decompose", _EN_PACK.prompt_decompose),
        ("DECOMPOSER_SYSTEM_PROMPT", DECOMPOSER_SYSTEM_PROMPT),
    ):
        for bad in forbidden:
            assert bad not in text, (
                f"{source_name} contains tenant-specific literal {bad!r}; "
                "decompose prompts must stay abstract per domain-neutral rule "
                "(CLAUDE.md)."
            )
