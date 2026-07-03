"""Step-4 (T040/T041): deterministic numeric-fidelity gate — OBSERVE mode.

Contract: specs/001-rag-truth-audit/contracts/numeric-fidelity-event.md.
Every significant number in the answer is classified against the SERVED context
(graded_chunks text; parsed-value equality covers formatting drift):
grounded → derived_valid (|a−b| / a+b recompute over grounded values) →
unsupported. Model-independent, pure string/arith — no LLM, no DB call.
OBSERVE-ONLY: never modifies the answer (sacred #10); structlog + trace field.

Baseline evidence this must catch: A-q20 fabricated "26.000.000đ" (absent from
context and stats) at 53% rate; chuẩn controls must produce 0 unsupported
(false-positive rate measured in T042 before any blocking discussion).
"""
from __future__ import annotations

import inspect

from ragbot.shared.numeric_fidelity import classify_answer_numbers


def _ctx(*texts: str) -> list[str]:
    return list(texts)


# ---------------------------------------------------------------------------
# Pure classification (contract §Classification rules)
# ---------------------------------------------------------------------------

def test_grounded_literal() -> None:
    r = classify_answer_numbers(
        "Giá 1.242.000đ/lốp, còn 507 lốp ạ.",
        _ctx("2-ZR17 225/45 LPD: 1.242.000 | quantity: 507"),
    )
    assert r["n_unsupported"] == 0
    assert r["n_grounded"] >= 1


def test_grounded_parsed_value_formatting_drift() -> None:
    # Answer dotted, context bare int — value equality must ground it.
    r = classify_answer_numbers("Giá 1.242.000đ.", _ctx("price: 1242000"))
    assert r["n_unsupported"] == 0 and r["n_grounded"] == 1


def test_unsupported_the_q20_fabrication_verbatim() -> None:
    r = classify_answer_numbers(
        "Lốp giá 26.000.000đ/lốp ạ. Hiện còn 26 lốp.",
        _ctx("| 195/65R16, 195 65 16 ... | | | 26 | | https://drive"),
    )
    assert r["n_unsupported"] == 1
    assert "26.000.000" in r["unsupported_tokens"]


def test_derived_valid_difference_and_sum() -> None:
    r = classify_answer_numbers(
        "A giá 1.602.000đ, B giá 1.170.000đ — chênh lệch 432.000đ.",
        _ctx("1.602.000 ... 1.170.000"),
    )
    assert r["n_unsupported"] == 0
    assert r["n_derived_valid"] == 1


def test_min_digits_guard_ignores_small_tokens() -> None:
    # Sizes / ordinals / small stock counts stay out of the verdict set.
    r = classify_answer_numbers("Lốp 205/55R16 còn 9 lốp.", _ctx("205/55R16 quantity: 9"))
    assert r["n_numbers"] == 0


def test_unsupported_tokens_capped() -> None:
    ans = " ".join(f"{i}.111.000đ" for i in range(1, 15))
    r = classify_answer_numbers(ans, _ctx("no numbers"))
    from ragbot.shared.constants import NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP

    assert len(r["unsupported_tokens"]) <= NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP
    assert r["n_unsupported"] == 14  # counts stay exact; only the token list caps


def test_empty_answer_or_context_safe() -> None:
    assert classify_answer_numbers("", _ctx("x"))["n_numbers"] == 0
    r = classify_answer_numbers("Giá 1.242.000đ.", [])
    assert r["n_unsupported"] == 1  # no context = nothing grounds


# ---------------------------------------------------------------------------
# Constants + node wiring pins (mirrors test_grounding_confirmed_action.py)
# ---------------------------------------------------------------------------

def test_constants_exist() -> None:
    from ragbot.shared.constants import (
        NUMERIC_FIDELITY_EVENT,
        NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP,
    )

    assert isinstance(NUMERIC_FIDELITY_EVENT, str) and NUMERIC_FIDELITY_EVENT
    assert NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP > 0


def test_guard_output_wires_observe_only() -> None:
    """Pin: guard_output computes the observe verdict on the ORIGINAL answer
    and NEVER uses it to modify/substitute the answer (sacred #10). The state
    key 'numeric_fidelity' is written; no branch conditions answer content on
    the verdict."""
    from ragbot.orchestration.nodes import guard_output

    src = inspect.getsource(guard_output)
    assert "classify_answer_numbers" in src
    assert '"numeric_fidelity"' in src
    # No blocking on the verdict: the fidelity result must not gate the answer.
    assert "n_unsupported" not in src.split("classify_answer_numbers")[0], (
        "verdict must be computed once, observe-only"
    )
    for forbidden in (
        'if _nf["n_unsupported"]', "if _nf['n_unsupported']",
        'nf["n_unsupported"] >', "block_on_unsupported",
    ):
        assert forbidden not in src, f"observe-only violated: {forbidden}"


# ---------------------------------------------------------------------------
# Step-5: cross-row misattribution (lệch) — real number, wrong entity
# ---------------------------------------------------------------------------

from ragbot.shared.numeric_fidelity import detect_cross_row_misattribution


_TWO_ROW_CTX = [
    "2-R15 185/55 LPD: 810000 | price: 810000 | answer: LANDSPIDER 185/55R15 G/P "
    "| quantity: 779 | productname: Lốp xe LANDSPIDER 185/55R15 82V CITYTRAXX G/P\n"
    "2-R15 185/55 RVL | answer: ROVELO 185/55R15 A68 | productname: Lốp Rovelo 185/55R15 RHP-A68"
]


def test_misattribution_h01_verbatim_conflation_flagged() -> None:
    """H-01 class: answer attributes Landspider's 810.000 to Rovelo — the tokens
    'rovelo'/'a68' live ONLY in the row WITHOUT the number → cross-row mix."""
    r = detect_cross_row_misattribution(
        "Dạ, lốp Rovelo 185/55R15 A68 hiện còn hàng, giá 810.000đ/lốp ạ.",
        _TWO_ROW_CTX,
    )
    assert r["n_misattributed"] == 1
    assert any("810" in t["token"] for t in r["misattributed"])


def test_correct_brand_attribution_clean() -> None:
    r = detect_cross_row_misattribution(
        "Dạ, lốp Landspider 185/55R15 G/P giá 810.000đ/lốp, còn 779 lốp ạ.",
        _TWO_ROW_CTX,
    )
    assert r["n_misattributed"] == 0


def test_multi_line_listing_both_brands_clean() -> None:
    """C-d04 class: a correct LISTING answer names both brands on separate
    lines — line-scoping must keep it clean (whole-answer scoping would
    false-flag it)."""
    ctx = [
        "2-R16 205/55 LPD: 1044000 | price: 1044000 | answer: LANDSPIDER 205/55R16 G/P\n"
        "2-R16 205/55 RVL | price: 963000 | answer: ROVELO 205/55R16 A68"
    ]
    r = detect_cross_row_misattribution(
        "Dạ, quy cách 205/55R16 bên em có hai loại ạ:\n"
        "- Lốp LANDSPIDER 205/55R16 G/P giá 1.044.000đ/lốp\n"
        "- Lốp Rovelo 205/55R16 A68 giá 963.000đ/lốp",
        ctx,
    )
    assert r["n_misattributed"] == 0


def test_p07_wrong_row_pick_flagged() -> None:
    """P-07 class: asked-brand answer carries the OTHER row's price+stock."""
    ctx = [
        "2-R17 225/45 RVL: 1170000 | price: 1170000 | quantity: 4 | answer: ROVELO 225/45R17 AS01\n"
        "2-ZR17 225/45 LPD: 1242000 | price: 1242000 | quantity: 507 | answer: LANDSPIDER 225/45ZR17 H/P"
    ]
    r = detect_cross_row_misattribution(
        "Dạ, lốp Landspider 225/45R17 hiện đang có giá 1.170.000đ/lốp, còn 4 lốp ạ.",
        ctx,
    )
    assert r["n_misattributed"] >= 1


def test_generic_tokens_do_not_flag() -> None:
    """Tokens present in EVERY row ('lốp', 'price', 'answer') are not
    row-discriminative — they must never create a flag."""
    r = detect_cross_row_misattribution(
        "Giá lốp là 810.000đ ạ.",  # no entity tokens at all
        _TWO_ROW_CTX,
    )
    assert r["n_misattributed"] == 0


def test_guard_output_wires_misattribution_observe() -> None:
    import inspect
    from ragbot.orchestration.nodes import guard_output

    src = inspect.getsource(guard_output)
    assert "detect_cross_row_misattribution" in src
    assert "n_misattributed" in src
