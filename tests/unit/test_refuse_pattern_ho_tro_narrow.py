"""Regression test for the ``"hỗ trợ.*dịch vụ"`` over-greedy regex.

Background
----------
MEGA load-test rounds R1-R4 (2026-04-30) showed that the DRY-merged fragment
``"hỗ trợ.*dịch vụ"`` (introduced commit ``9c1adae``) silently flipped
**47 / 600** turns from PASS to REFUSE_WITH_DOCS because every benign trailing
CTA the bot uses to keep the conversation going matches it, e.g.:

    "Dạ giá dịch vụ gội đầu thư giãn là 60.000 đồng cho 30 phút ạ.
     Anh/chị cần em hỗ trợ đặt lịch hoặc tư vấn thêm dịch vụ khác không?"

Audit verdict in ``reports/MEGA_R1_R4_RECLASSIFY_20260430.md §3`` recommended
narrowing the fragment to ``"em chỉ.*hỗ trợ.*dịch vụ"`` so the apologetic
"I only support service X" refusal is still caught **without** flagging
content-positive informative answers that happen to end with a CTA.

This test pins both directions:

1. Each PASS-class CTA tail observed in R1-R4 raw data MUST NOT match the
   canonical refuse regex (false-positive guard).
2. Each apologetic "em chỉ hỗ trợ ... dịch vụ" phrasing MUST still match
   (false-negative guard — the original intent of the fragment).
3. Snapshot test ensures the over-greedy literal does not get re-introduced
   verbatim by a future copy-paste.
"""

from __future__ import annotations

import re

import pytest

from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS


@pytest.fixture(scope="module")
def refuse_regex() -> re.Pattern[str]:
    """Build the same regex shape harnesses compose at runtime.

    Mirrors ``scripts/test_75q_load.py`` line ~74 + ``test_universal_cases.py``
    line ~69 — case-insensitive alternation across the canonical tuple.
    """
    return re.compile(
        "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
        re.IGNORECASE,
    )


# --- 1. False-positive guard: benign CTAs MUST NOT trip the refuse classifier
#
# Each of these is a real PASS-class answer body (truncated) observed in
# /tmp/mega_round*.json that the over-greedy fragment formerly flagged.

_BENIGN_CTA_PHRASES: tuple[tuple[str, str], ...] = (
    (
        "cta_dat_lich_or_tu_van_them",
        "Dạ giá dịch vụ gội đầu thư giãn với dầu thường là 60.000 đồng cho "
        "30 phút ạ. Anh/chị cần em hỗ trợ đặt lịch hoặc tư vấn thêm dịch vụ "
        "khác không?",
    ),
    (
        "cta_ho_tro_anh_chi_chon",
        "Dạ bên em có nhiều gói chăm sóc da mặt chuẩn y khoa ạ. Em hỗ trợ "
        "anh/chị chọn dịch vụ phù hợp với nhu cầu nhé?",
    ),
    (
        "cta_can_em_ho_tro_tu_van",
        "Dạ chương trình combo 5 buổi đang có ưu đãi 20% ạ. Anh/chị cần em "
        "hỗ trợ tư vấn dịch vụ nào ạ?",
    ),
    (
        "cta_co_the_ho_tro_them_dich_vu_khac",
        "Dạ liệu trình điều trị mụn của bên em kéo dài 6 buổi ạ. Em có thể "
        "hỗ trợ thêm dịch vụ chăm sóc da kèm theo nếu anh/chị quan tâm.",
    ),
    (
        "cta_da_thong_tin_hien_tai_co_so",
        "Dạ, theo thông tin hiện tại, bên em có địa chỉ tại số 102 "
        "Vũ Trọng Phụng, Thanh Xuân, Hà Nội. Anh/chị cần em hỗ trợ gì thêm "
        "về dịch vụ không ạ?",
    ),
)


@pytest.mark.parametrize(
    ("phrase_id", "phrase"),
    _BENIGN_CTA_PHRASES,
    ids=[pid for pid, _ in _BENIGN_CTA_PHRASES],
)
def test_benign_cta_does_not_trip_refuse_classifier(
    refuse_regex: re.Pattern[str], phrase_id: str, phrase: str
) -> None:
    """Content-positive answers that end with an informative CTA must PASS."""
    assert refuse_regex.search(phrase) is None, (
        f"FALSE-POSITIVE regression on phrase {phrase_id!r}: refuse regex "
        f"matched a benign PASS-class CTA. The over-greedy "
        f"'hỗ trợ.*dịch vụ' fragment may have been re-introduced. "
        f"Phrase: {phrase!r}"
    )


# --- 2. False-negative guard: apologetic "em chỉ ... hỗ trợ ... dịch vụ" MUST match


_APOLOGETIC_REFUSE_PHRASES: tuple[tuple[str, str], ...] = (
    (
        "em_chi_ho_tro_dich_vu_lam_dep",
        "Dạ em chỉ hỗ trợ tư vấn dịch vụ làm đẹp tại spa thôi ạ, phần đó "
        "em chưa rõ.",
    ),
    (
        "em_chi_co_the_ho_tro_dich_vu_X",
        "Em chỉ có thể hỗ trợ các dịch vụ chăm sóc da và spa, ngoài phạm "
        "vi này em không tư vấn được ạ.",
    ),
)


@pytest.mark.parametrize(
    ("phrase_id", "phrase"),
    _APOLOGETIC_REFUSE_PHRASES,
    ids=[pid for pid, _ in _APOLOGETIC_REFUSE_PHRASES],
)
def test_apologetic_refusal_still_matches(
    refuse_regex: re.Pattern[str], phrase_id: str, phrase: str
) -> None:
    """The narrowed fragment must still catch the original refusal class."""
    assert refuse_regex.search(phrase) is not None, (
        f"FALSE-NEGATIVE regression on phrase {phrase_id!r}: "
        f"narrowed 'em chỉ.*hỗ trợ.*dịch vụ' fragment failed to match "
        f"a real apologetic refusal. Phrase: {phrase!r}"
    )


# --- 3. Snapshot test: the bare over-greedy literal must NOT be present


def test_overgreedy_fragment_not_present() -> None:
    """Fail loud if the bare 'hỗ trợ.*dịch vụ' fragment is re-added.

    The narrowed form requires the apologetic anchor 'em chỉ.*' as a prefix.
    A plain 'hỗ trợ.*dịch vụ' would resurrect the 47-turn false-positive
    class on the next load-test round.
    """
    forbidden = "hỗ trợ.*dịch vụ"
    narrow = "em chỉ.*hỗ trợ.*dịch vụ"
    fragments = set(DEFAULT_LOADTEST_REFUSE_PATTERNS)
    assert forbidden not in fragments, (
        "Over-greedy 'hỗ trợ.*dịch vụ' fragment re-introduced into "
        "DEFAULT_LOADTEST_REFUSE_PATTERNS. Use the narrowed form "
        f"{narrow!r} instead — see "
        "reports/MEGA_R1_R4_RECLASSIFY_20260430.md §3."
    )
    assert narrow in fragments, (
        f"Expected narrowed fragment {narrow!r} to be present in "
        "DEFAULT_LOADTEST_REFUSE_PATTERNS but it was not found."
    )
