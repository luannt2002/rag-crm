"""Clause A "docs hiện có" refuse-pattern coverage (R3 false-positive fix).

Background
----------
The MEGA load-test R3 NEW-room run (2026-04-30) surfaced a classifier
false-positive: bot answer "Dạ chỗ này em chưa thấy trong tài liệu hiện có
ạ. Để em check lại..." was content-correct REFUSE per Clause A of the
bot's system_prompt (rule 5/6: "không có trong tài liệu thì nói chưa thấy")
but the load-test classifier labelled it ``PASS`` because:

  - answer length > 30 chars (length heuristic),
  - none of the original ``DEFAULT_LOADTEST_REFUSE_PATTERNS`` regex anchors
    aligned with the "tài liệu hiện có" / "chưa có trong tài liệu" /
    "tài liệu không có" phrasings.

This module pins each new fragment with a dedicated test so a future
refactor cannot silently delete coverage. Each test is intentionally
narrow — one fragment per test — to give a precise failure signal.

The tests are infrastructure-light (regex only, no DB / network) so they
run inside the unit suite and gate any harness that imports
``DEFAULT_LOADTEST_REFUSE_PATTERNS``.
"""

from __future__ import annotations

import re

import pytest

from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS


@pytest.fixture(scope="module")
def refuse_regex() -> re.Pattern[str]:
    """Compose the canonical regex the harnesses build at runtime."""
    return re.compile(
        "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
        re.IGNORECASE,
    )


# Per-fragment sample sentences that mimic real Clause-A refusal phrasing
# observed in R3. Each sentence MUST match the canonical refuse regex —
# either via the verbatim fragment we added or via an existing broader
# anchor in the tuple. The point is end-to-end coverage, not which sub-
# pattern wins the match.
_CLAUSE_A_FRAGMENTS: tuple[tuple[str, str], ...] = (
    (
        "cho_nay_em_chua_thay",
        "Dạ chỗ này em chưa thấy trong tài liệu hiện có ạ.",
    ),
    (
        "em_chua_thay_thong_tin",
        "Em chưa thấy thông tin chi tiết về gói VIP đó trong tài liệu ạ.",
    ),
    (
        "trong_tai_lieu_hien_co",
        "Phần này em không tìm thấy trong tài liệu hiện có ạ.",
    ),
    (
        "tai_lieu_hien_co_em_chua",
        "Trong tài liệu hiện có em chưa tìm thấy gói khuyến mãi đó.",
    ),
    (
        "chua_co_trong_tai_lieu",
        "Phần ưu đãi VIP chưa có trong tài liệu nên em cần check lại ạ.",
    ),
    (
        "tai_lieu_khong_co",
        "Tài liệu không có đề cập đến gói free 5 buổi anh/chị ơi.",
    ),
)


@pytest.mark.parametrize(
    ("fragment_id", "phrase"),
    _CLAUSE_A_FRAGMENTS,
    ids=[fid for fid, _ in _CLAUSE_A_FRAGMENTS],
)
def test_clause_a_fragment_matches_canonical_regex(
    refuse_regex: re.Pattern[str], fragment_id: str, phrase: str
) -> None:
    """Each Clause-A refusal phrasing must trigger the canonical regex.

    Asserts the regression of the R3 classifier false-positive: had this
    test existed before R3, the misclassified ``Room 20 idx=3`` turn would
    have flagged the missing pattern at CI time instead of in production
    eval.
    """
    match = refuse_regex.search(phrase)
    assert match is not None, (
        f"Clause-A refusal not detected for fragment {fragment_id!r}: "
        f"phrase={phrase!r}"
    )
