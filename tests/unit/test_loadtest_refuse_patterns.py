"""DEFAULT_LOADTEST_REFUSE_PATTERNS — single source of truth for the 3
load-test harnesses (test_75q_load.py, test_universal_cases.py,
agent_d_loadtest.py).

Guards against:
- Silent regression of pattern coverage (each phrase must match).
- Re-introduction of harness-local DRY drift.
- False-negative on the new "chỗ này em chưa thấy" / "em chỉ có thông tin"
  cues added 2026-04-30 to catch system_prompt rule 5/6 refusals.
"""

from __future__ import annotations

import re

import pytest

from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS


@pytest.fixture(scope="module")
def refuse_regex() -> re.Pattern[str]:
    """Build the same regex shape harnesses compose at runtime."""
    return re.compile("(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")", re.IGNORECASE)


def test_constant_is_nonempty_tuple_of_str() -> None:
    assert isinstance(DEFAULT_LOADTEST_REFUSE_PATTERNS, tuple)
    assert len(DEFAULT_LOADTEST_REFUSE_PATTERNS) > 0
    for frag in DEFAULT_LOADTEST_REFUSE_PATTERNS:
        assert isinstance(frag, str)
        assert frag.strip() == frag, f"Whitespace in fragment: {frag!r}"
        # Each fragment must compile as a regex (catches typos like "??").
        re.compile(frag, re.IGNORECASE)


@pytest.mark.parametrize(
    "phrase",
    [
        # Existing canonical refusals (rule 4 of the bot system_prompt).
        "Dạ phần này em cần check lại với chuyên viên rồi báo lại sau ạ.",
        "Dạ chưa có thông tin về dịch vụ đó ạ.",
        "Em không có thông tin về phần đó.",
        "Chỗ này bên em chưa hỗ trợ ạ.",
        "Vui lòng liên hệ để được tư vấn cụ thể.",
        "Để em kiểm tra lại với chuyên viên ạ.",
        "Dạ em chưa rõ phần này lắm ạ.",
        # NEW patterns added 2026-04-30 (rule 5/6 refusal phrasing).
        "Dạ chỗ này em chưa thấy trong tài liệu hiện có ạ.",
        "Dạ em chưa thấy thông tin về phần đó.",
        "Trong tài liệu em chưa có thông tin chi tiết.",
        "Phần đó tài liệu không có đề cập ạ.",
        "Em chỉ có thông tin về địa chỉ, phần giờ mở cửa em chưa thấy ạ.",
    ],
)
def test_refuse_phrase_matches_canonical_regex(
    refuse_regex: re.Pattern[str], phrase: str
) -> None:
    assert refuse_regex.search(phrase), f"refuse phrase not detected: {phrase!r}"


@pytest.mark.parametrize(
    "phrase",
    [
        # PASS-class answers MUST NOT trip the refuse classifier.
        "Dạ địa chỉ của bên em là 123 Đường Lê Lợi ạ.",
        "Dr xin kính chào anh/chị ạ.",
        "Gói combo gồm 5 buổi với giá ưu đãi.",
        "Dạ bên em mở cửa từ 9h đến 21h hàng ngày ạ.",
    ],
)
def test_pass_phrase_does_not_match_refuse(
    refuse_regex: re.Pattern[str], phrase: str
) -> None:
    assert not refuse_regex.search(phrase), (
        f"PASS phrase tripped refuse classifier: {phrase!r}"
    )


def test_no_duplicate_fragments() -> None:
    """DRY: each fragment must be unique (case-sensitive)."""
    assert len(DEFAULT_LOADTEST_REFUSE_PATTERNS) == len(set(DEFAULT_LOADTEST_REFUSE_PATTERNS))


def test_constant_exported_in_all() -> None:
    """Must be in __all__ so downstream `from constants import *` picks it up."""
    from ragbot.shared import constants

    assert "DEFAULT_LOADTEST_REFUSE_PATTERNS" in constants.__all__
