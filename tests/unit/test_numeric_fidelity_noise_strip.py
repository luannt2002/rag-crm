"""002-H: numeric-fidelity must not false-flag non-value digits.

Measured (truth-audit observe pass, step7 luannt100b): 4 of 6 false-positives
on correct answers came from digits that are NOT per-row corpus values —
URL-path fragments, a contact/hotline number, and a number echoed from the
user's own question. Stripping these before tokenizing drops the false-flag
without weakening the fabrication signal (a real invented price survives every
strip). Domain-neutral: URL shape, leading-0 contact-run shape, and the query
text — never a corpus/brand literal.
"""
from __future__ import annotations

from ragbot.shared.numeric_fidelity import (
    classify_answer_numbers,
    detect_cross_row_misattribution,
)


def test_url_path_digits_not_unsupported() -> None:
    """B-046: a Drive/upload link's path digits must not be flagged."""
    answer = (
        "Dạ lốp 225/45ZR18 giá 1.440.000đ, còn 39 lốp ạ. "
        "Ảnh: https://vn1.co/uploads/5118b478c4777980417763 và "
        "https://vn1.co/97984b8a1047"
    )
    ctx = ["225/45ZR18 LPD: 1440000 | quantity: 39"]
    r = classify_answer_numbers(answer, ctx)
    assert r["n_unsupported"] == 0, r["unsupported_tokens"]


def test_question_echoed_number_not_unsupported() -> None:
    """B-076: an OOS refusal that echoes '2020' from the question is not a
    fabrication — the number came from the user, not invented by the bot."""
    answer = "Em chưa có thông tin về Thông tư 2020 trong tài liệu ạ."
    ctx = ["Chính sách bảo hành: lốp chính hãng, thời hạn theo quy định."]
    q = "Thông tư 2020 quy định gì về lốp xe?"
    r = classify_answer_numbers(answer, ctx, question=q)
    assert r["n_unsupported"] == 0, r["unsupported_tokens"]


def test_contact_number_not_misattributed() -> None:
    """B-071/B-074: a hotline number is a corpus-wide contact constant, not a
    per-row price — it must not trip the cross-row misattribution detector."""
    answer = "Hotline hỗ trợ của Nam Phát là số 0988 771 310 ạ."
    ctx = [
        "195/65R16 NEO: price: — | date1: 26",
        "Hỗ trợ: Hotline/Zalo 0988 771 310",
    ]
    r = detect_cross_row_misattribution(answer, ctx)
    assert r["n_misattributed"] == 0, r["misattributed"]


def test_real_fabricated_price_still_flagged() -> None:
    """The strips must NOT weaken the fabrication signal: an invented price
    that is neither a URL nor a contact nor in the question is still caught."""
    answer = "Dạ lốp Neoterra 195/65R16 giá 1.500.000đ ạ."
    ctx = ["195/65R16 NEO: price: — | date1: 26 | productname: NEOTERRA NEOTOUR"]
    r = classify_answer_numbers(answer, ctx, question="Neoterra 195/65R16 giá bao nhiêu?")
    assert r["n_unsupported"] == 1, r
    assert "1.500.000" in r["unsupported_tokens"]


def test_real_cross_row_grab_still_flagged() -> None:
    """The contact strip must NOT hide a genuine cross-row price grab."""
    answer = "Dạ lốp Neoterra 195/65R16 giá 1.350.000đ ạ."
    ctx = [
        "195/65R16 NEO: price: — | productname: NEOTERRA NEOTOUR",
        "195/75R16 RVL: 1350000 | productname: ROVELO",
    ]
    r = detect_cross_row_misattribution(answer, ctx)
    assert r["n_misattributed"] == 1, r
