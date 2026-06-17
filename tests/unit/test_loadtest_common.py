"""Unit coverage for `scripts/_loadtest_common.py`.

Closes finding F8 (LOW) of CODE_VALIDATE_SWEEP_20260430: the harness
helper module shipped without dedicated unit coverage of:
- `make_refuse_pattern(extra=...)` injection paths,
- `is_refuse(text=None)` early-return,
- explicit `pattern=` injection override,
- module-level constants / clean-import guarantees.

Per CLAUDE.md application-mindset rule, this regex is *test-side
classification only* and never feeds the production pipeline.
"""

from __future__ import annotations

import importlib
import re
from re import Pattern

import pytest

from scripts import _loadtest_common as lc


# ---------------------------------------------------------------------------
# make_refuse_pattern
# ---------------------------------------------------------------------------


def test_make_refuse_pattern_no_extra_returns_compiled_pattern() -> None:
    pat = lc.make_refuse_pattern()
    assert isinstance(pat, re.Pattern)
    # Base canonical fragments must trigger the compiled regex.
    assert pat.search("chưa có thông tin về dịch vụ này")
    assert pat.search("không có thông tin")


def test_make_refuse_pattern_with_extra_tuple_extends_base() -> None:
    extra = (r"unique-harness-cue-xyz",)
    pat = lc.make_refuse_pattern(extra=extra)
    # Base fragment still matches.
    assert pat.search("không tìm thấy")
    # Extra fragment now matches (case-insensitive).
    assert pat.search("UNIQUE-harness-cue-xyz appears here")


def test_make_refuse_pattern_with_empty_extra_is_noop() -> None:
    base = lc.make_refuse_pattern()
    same = lc.make_refuse_pattern(extra=())
    # Same alternation ⇒ same compiled source.
    assert base.pattern == same.pattern


def test_make_refuse_pattern_is_case_insensitive() -> None:
    pat = lc.make_refuse_pattern()
    assert pat.flags & re.IGNORECASE
    assert pat.search("CHƯA CÓ THÔNG TIN")


def test_make_refuse_pattern_does_not_mutate_canonical_fragments() -> None:
    """`extra` must not leak into the shared canonical fragment tuple."""
    snapshot = tuple(lc.DEFAULT_LOADTEST_REFUSE_PATTERNS)
    lc.make_refuse_pattern(extra=(r"throwaway-cue",))
    assert lc.DEFAULT_LOADTEST_REFUSE_PATTERNS == snapshot


# ---------------------------------------------------------------------------
# is_refuse
# ---------------------------------------------------------------------------


def test_is_refuse_none_returns_false() -> None:
    assert lc.is_refuse(None) is False


def test_is_refuse_empty_string_returns_false() -> None:
    assert lc.is_refuse("") is False


def test_is_refuse_detects_canonical_phrase() -> None:
    assert lc.is_refuse("Để em kiểm tra lại với chuyên viên ạ.") is True


def test_is_refuse_passes_for_non_refuse_text() -> None:
    assert lc.is_refuse("Dạ địa chỉ bên em là số 1 Đường ABC ạ.") is False


def test_is_refuse_uses_injected_pattern_when_provided() -> None:
    custom: Pattern[str] = re.compile(r"sentinel-token", re.IGNORECASE)
    # Phrase that would NOT trip the default pattern but DOES trip custom.
    assert lc.is_refuse("here is a SENTINEL-TOKEN inside", pattern=custom) is True
    # Phrase that trips default but not the custom (proves override took).
    assert lc.is_refuse("không có thông tin", pattern=custom) is False


def test_is_refuse_case_insensitive_default_pattern() -> None:
    assert lc.is_refuse("KHÔNG CÓ THÔNG TIN") is True


# ---------------------------------------------------------------------------
# Module-level surface
# ---------------------------------------------------------------------------


def test_module_level_constants_exposed() -> None:
    assert isinstance(lc.DEFAULT_LOADTEST_REFUSE_PATTERNS, tuple)
    assert len(lc.DEFAULT_LOADTEST_REFUSE_PATTERNS) > 0
    assert all(
        isinstance(f, str) and f for f in lc.DEFAULT_LOADTEST_REFUSE_PATTERNS
    )
    assert isinstance(lc.REFUSE_PATTERN, re.Pattern)
    expected = {
        "DEFAULT_LOADTEST_REFUSE_PATTERNS",
        "make_refuse_pattern",
        "REFUSE_PATTERN",
        "is_refuse",
    }
    assert expected.issubset(set(lc.__all__))


def test_every_canonical_fragment_compiles_individually() -> None:
    for frag in lc.DEFAULT_LOADTEST_REFUSE_PATTERNS:
        re.compile(frag, re.IGNORECASE)


def test_module_imports_cleanly_without_side_effects() -> None:
    """Reloading the module must not raise nor mutate global state."""
    reloaded = importlib.reload(lc)
    assert reloaded.REFUSE_PATTERN.pattern == lc.REFUSE_PATTERN.pattern
    assert reloaded.DEFAULT_LOADTEST_REFUSE_PATTERNS == lc.DEFAULT_LOADTEST_REFUSE_PATTERNS


@pytest.mark.parametrize(
    "phrase",
    [
        "chưa có thông tin",
        "không có thông tin",
        "vui lòng liên hệ hotline để được hỗ trợ",
        "ngoài phạm vi tư vấn của em",
        "tôi không thể trả lời",
        "để em kiểm tra lại với chuyên viên",
    ],
)
def test_default_pattern_matches_known_refuse_phrases(phrase: str) -> None:
    assert lc.is_refuse(phrase) is True
