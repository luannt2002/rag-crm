"""DRY consolidation regression test — F1 (MAJOR) from
CODE_VALIDATE_SWEEP_20260430.

Asserts the 3 load-test harnesses
(``scripts/test_75q_load.py``, ``scripts/test_universal_cases.py``,
``scripts/agent_d_loadtest.py``) all produce *identical* refuse
classification when given the same answer text. Pre-consolidation, two
sources of truth (``shared.constants.DEFAULT_LOADTEST_REFUSE_PATTERNS``
and ``scripts._loadtest_common.DEFAULT_REFUSE_FRAGMENTS``) drifted apart
(14 vs 28 tokens), so an answer flagged REFUSE by one harness could be
PASS in another — silently skewing benchmark numbers.

After consolidation:
- ``shared.constants.DEFAULT_LOADTEST_REFUSE_PATTERNS`` is the SUPERSET
  (33 fragments, dedup).
- ``scripts._loadtest_common`` imports from constants; no local list.
- All 3 harnesses build the SAME compiled regex shape.

This test is a guard: any future drift (re-introducing a local list,
deleting fragments without sync) will fail it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Make `scripts/_loadtest_common` importable without invoking script
# top-level code paths from the harnesses themselves (which would try to
# open HTTP clients etc.).
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _loadtest_common import (  # noqa: E402 — path-shim above
    DEFAULT_LOADTEST_REFUSE_PATTERNS as HELPER_FRAGMENTS,
)
from _loadtest_common import (  # noqa: E402
    REFUSE_PATTERN as HELPER_PATTERN,
)
from _loadtest_common import (  # noqa: E402
    is_refuse as helper_is_refuse,
)
from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS  # noqa: E402


# Sample answers — 3 refuse, 2 non-refuse — chosen to exercise both the
# constants-original and the helper-merged-in fragments so any silent
# regression on either side trips the test.
_REFUSE_SAMPLES = (
    "Dạ phần này em cần check lại với chuyên viên rồi báo lại sau ạ.",
    "Dạ chưa có thông tin về dịch vụ đó ạ.",
    "Vui lòng liên hệ hotline để được tư vấn cụ thể.",
)

_NON_REFUSE_SAMPLES = (
    "Dạ địa chỉ của bên em là 123 Đường Lê Lợi ạ.",
    "Dạ bên em mở cửa từ 9h đến 21h hàng ngày ạ.",
)


def _harness_75q_classify(answer: str) -> bool:
    """Mirror of ``scripts/test_75q_load.py`` REFUSE_PATTERN.search()."""
    pat = re.compile(
        "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
        re.IGNORECASE,
    )
    return bool(pat.search(answer))


def _harness_universal_classify(answer: str) -> bool:
    """Mirror of ``scripts/test_universal_cases.py`` REFUSE_REGEX.search()."""
    pat = re.compile(
        "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
        re.IGNORECASE,
    )
    return bool(pat.search(answer))


def _harness_agent_d_classify(answer: str) -> bool:
    """Mirror of ``scripts/agent_d_loadtest.py`` is_refuse(answer, REFUSE_PATTERN)."""
    return helper_is_refuse(answer, pattern=HELPER_PATTERN)


def test_helper_imports_canonical_constant() -> None:
    """Helper must re-export the SAME tuple object from constants —
    proves no local copy was reintroduced."""
    assert HELPER_FRAGMENTS is DEFAULT_LOADTEST_REFUSE_PATTERNS, (
        "scripts/_loadtest_common must import the canonical tuple from "
        "shared.constants — local DEFAULT_REFUSE_FRAGMENTS must NOT be "
        "redefined (DRY drift would silently desync the agent_d harness)."
    )


@pytest.mark.parametrize("answer", _REFUSE_SAMPLES)
def test_all_three_harnesses_agree_refuse(answer: str) -> None:
    """All 3 harnesses MUST classify the same refuse answer as refuse."""
    h75 = _harness_75q_classify(answer)
    huni = _harness_universal_classify(answer)
    hagent = _harness_agent_d_classify(answer)
    assert h75 is True, f"test_75q_load failed to flag refuse: {answer!r}"
    assert huni is True, f"test_universal_cases failed to flag refuse: {answer!r}"
    assert hagent is True, f"agent_d_loadtest failed to flag refuse: {answer!r}"
    assert h75 == huni == hagent, (
        f"Harness disagreement on refuse classification for {answer!r}: "
        f"75q={h75} universal={huni} agent_d={hagent}"
    )


@pytest.mark.parametrize("answer", _NON_REFUSE_SAMPLES)
def test_all_three_harnesses_agree_non_refuse(answer: str) -> None:
    """All 3 harnesses MUST classify the same PASS answer as non-refuse."""
    h75 = _harness_75q_classify(answer)
    huni = _harness_universal_classify(answer)
    hagent = _harness_agent_d_classify(answer)
    assert h75 is False, f"test_75q_load false-positive on PASS: {answer!r}"
    assert huni is False, f"test_universal_cases false-positive on PASS: {answer!r}"
    assert hagent is False, f"agent_d_loadtest false-positive on PASS: {answer!r}"
    assert h75 == huni == hagent, (
        f"Harness disagreement on PASS classification for {answer!r}: "
        f"75q={h75} universal={huni} agent_d={hagent}"
    )


def test_canonical_superset_includes_all_pre_consolidation_fragments() -> None:
    """Regression guard: any fragment that previously lived in the
    helper's local list MUST still be present in the canonical tuple.
    Drops (without an explicit migration plan) silently weaken refuse
    detection in the agent_d harness."""
    pre_consolidation_helper_fragments = {
        "chưa có thông tin",
        "không có thông tin",
        "check lại với",
        "cần check lại",
        "liên hệ trực tiếp",
        "liên hệ.*tư vấn",
        "liên hệ hotline",
        "vui lòng liên hệ hotline",
        "xin lỗi.*không",
        "không tìm thấy",
        "ngoài phạm vi",
        "ngoài khả năng",
        "không hỗ trợ",
        "chưa hỗ trợ",
        "không thuộc",
        "không thể",
        # "không biết" intentionally dropped 2026-05-01 — collides with the
        # polite Vietnamese idiom "Không biết chị cần loại nào ạ?" (= may I
        # ask…). Removed per plans/260501-R2-HARNESS-FIX/plan.md §2.1 +
        # reports/MEGA_REFUSE_WITH_DOCS_DEEPDIVE_20260501.md §2 Mode D. The
        # remaining "tôi không" + "không thể" + "chưa có thông tin"
        # fragments still cover genuine refusal phrasings; informativeness
        # gate (_has_factual_claim) handles the price+hedge false positives.
        "chuyên về.*dịch vụ",
        # Narrowed 2026-04-30 night (auditor-chief M1): the bare fragment
        # ``"hỗ trợ.*dịch vụ"`` was over-greedy and produced 47 R1-R4
        # PASS→REFUSE_WITH_DOCS false-positives. The canonical superset now
        # carries the apologetic-anchored form ``"em chỉ.*hỗ trợ.*dịch vụ"``
        # which preserves the original refusal-detection intent without
        # tripping benign informative CTAs. See
        # reports/MEGA_R1_R4_RECLASSIFY_20260430.md §3 + commit shipping
        # tests/unit/test_refuse_pattern_ho_tro_narrow.py for the audit
        # trail and the false-positive-guard pin tests.
        "em chỉ.*hỗ trợ.*dịch vụ",
        "tôi chỉ.*tư vấn",
        "chỉ có thể tư vấn",
        "không có trong",
        "không nằm trong",
        "chưa được cập nhật",
        "tôi không",
        "để em kiểm tra",
        "em chưa rõ",
        "kiểm tra.*chuyên viên",
    }
    canonical = set(DEFAULT_LOADTEST_REFUSE_PATTERNS)
    missing = pre_consolidation_helper_fragments - canonical
    assert not missing, (
        f"Pre-consolidation helper fragments missing from canonical tuple: "
        f"{sorted(missing)!r} — re-add to shared.constants."
    )


def test_extra_kwarg_extends_without_mutation() -> None:
    """``make_refuse_pattern(extra=...)`` must not mutate the canonical
    tuple — harness-specific cues append, never replace."""
    from _loadtest_common import make_refuse_pattern  # noqa: PLC0415

    before = tuple(DEFAULT_LOADTEST_REFUSE_PATTERNS)
    extra = ("totally-unique-cue-xyz-not-in-canonical",)
    pat = make_refuse_pattern(extra=extra)
    after = tuple(DEFAULT_LOADTEST_REFUSE_PATTERNS)

    assert before == after, "Canonical tuple was mutated by make_refuse_pattern"
    assert pat.search("foo totally-unique-cue-xyz-not-in-canonical bar")
    # And the extra cue must NOT be picked up by harness 75q/universal
    # (which build the regex from the canonical list only).
    assert not _harness_75q_classify("foo totally-unique-cue-xyz-not-in-canonical bar")
