"""Unit tests for ``ragbot.shared.vn_honorific.detect_honorific``.

Mock-only, deterministic. Validates first-occurrence semantics, accent
preservation, case-insensitivity, and word-boundary rejection of substrings.
"""
from __future__ import annotations

from ragbot.shared.constants import VN_HONORIFIC_LABELS
from ragbot.shared.vn_honorific import detect_honorific


def test_first_occurrence_addressee_wins() -> None:
    # "Chào anh, em hỏi tí" — addressee ``anh`` precedes self-reference ``em``.
    assert detect_honorific("Chào anh, em hỏi tí") == "anh"


def test_self_reference_em_when_alone() -> None:
    assert detect_honorific("em đang cần hỗ trợ") == "em"


def test_accent_preserved_for_co() -> None:
    # ``cô`` (with accent) precedes ``mình`` — first one wins, accent kept.
    assert detect_honorific("Cô ơi cho mình hỏi") == "cô"


def test_no_honorific_returns_none() -> None:
    assert detect_honorific("Cho hỏi giá sản phẩm bao nhiêu?") is None


def test_empty_string_returns_none() -> None:
    assert detect_honorific("") is None


def test_uppercase_input_normalised_to_lowercase_label() -> None:
    # ``ANH oi`` — case-insensitive match, label returned in canonical lowercase.
    assert detect_honorific("ANH oi") == "anh"


def test_substring_rejected_by_word_boundary() -> None:
    # ``thanh toán`` embeds ``anh`` inside ``thanh`` — must NOT match because
    # there's no word boundary between ``th`` and ``anh``. Same guard rejects
    # ``khánh`` / ``mạnh`` / ``danh`` style false positives.
    assert detect_honorific("thanh toán hóa đơn") is None


def test_multiple_honorifics_first_one_wins() -> None:
    # ``anh`` appears before ``chị`` — first detected.
    assert detect_honorific("anh và chị cùng đi") == "anh"


def test_returned_label_is_in_canonical_set() -> None:
    # Cross-check: every label this utility returns must come from the SSoT
    # frozenset. Guards against regex drift introducing tokens out of policy.
    expectations = {
        "Chào anh, em hỏi tí": "anh",
        "em đang cần hỗ trợ": "em",
        "Cô ơi cho mình hỏi": "cô",
        "ANH oi": "anh",
        "anh và chị cùng đi": "anh",
        "bác cho cháu hỏi": "bác",
        "chú giúp cháu với": "chú",
    }
    for text, expected in expectations.items():
        label = detect_honorific(text)
        assert label == expected, f"expected {expected!r} from {text!r}, got {label!r}"
        assert label in VN_HONORIFIC_LABELS
