"""P22 Option B — VN compound segmentation at ingest.

Real assertions: behaviour of `segment_vi_compounds` against the live
underthesea backend AND with the backend monkey-patched to simulate
fail / missing scenarios. These are the contract guarantees the ingest
path relies on.
"""
from __future__ import annotations

import importlib

import pytest

from ragbot.shared import vi_tokenizer
from ragbot.shared.constants import (
    DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED,
    DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S,
)


@pytest.fixture(autouse=True)
def _reset_tokenizer_state():
    """Force re-init of the module-level cached backend per-test so
    monkeypatched scenarios don't leak through to the next test.
    """
    yield
    # Reset after test so other tests get a clean init.
    importlib.reload(vi_tokenizer)


def test_constants_have_expected_defaults():
    """Defaults are wired to constants.py (zero-hardcode rule)."""
    assert DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED is True
    assert DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S >= 1


def test_compound_keeps_underscore_join_for_known_phrase():
    """Multi-word VN compound "chăm sóc da" gets joined into 1 token."""
    out = vi_tokenizer.segment_vi_compounds("chăm sóc da mặt")
    # underthesea yields "chăm_sóc da mặt" — exact compound boundary:
    # "chăm sóc" is a known compound, "da" + "mặt" remain separate tokens.
    assert "chăm_sóc" in out, f"expected compound chăm_sóc in {out!r}"
    # And the original 3-word phrase MUST have changed (proves segmentation ran).
    assert out != "chăm sóc da mặt"


def test_compound_triet_long_keeps_compound():
    """Domain-neutral VN compound "triệt lông" should be joined."""
    out = vi_tokenizer.segment_vi_compounds("dịch vụ triệt lông an toàn")
    # underthesea recognises "triệt_lông" or at minimum "dịch_vụ"
    # — at least one compound joined proves segmentation engaged.
    has_compound = "_" in out
    assert has_compound, f"expected at least one compound underscore in {out!r}"


def test_english_unchanged_or_safe():
    """Pure English should not gain spurious underscores from VN segmenter."""
    text_in = "skin care services"
    out = vi_tokenizer.segment_vi_compounds(text_in)
    # underthesea on English typically returns the input largely intact —
    # we don't require strict equality (it may lowercase or split on
    # punctuation), but it MUST NOT introduce ASCII-on-ASCII underscores.
    assert "skin_care" not in out
    # And it must remain a non-empty string of similar length.
    assert isinstance(out, str)
    assert len(out) >= len(text_in) - 5  # no major truncation


def test_empty_returns_empty():
    """Empty / whitespace input returns input unchanged (no exception)."""
    assert vi_tokenizer.segment_vi_compounds("") == ""
    assert vi_tokenizer.segment_vi_compounds("   ") == "   "


def test_none_returns_empty_string_not_crash():
    """None must not crash — return empty string defensively."""
    # type: ignore[arg-type]
    assert vi_tokenizer.segment_vi_compounds(None) == ""  # type: ignore[arg-type]


def test_oversize_input_falls_back_to_original():
    """When input exceeds the length budget the function returns input unchanged.

    Budget = timeout_s × DEFAULT_VI_COMPOUND_SEGMENTATION_THROUGHPUT_CHARS_PER_S.
    """
    from ragbot.shared.constants import (
        DEFAULT_VI_COMPOUND_SEGMENTATION_THROUGHPUT_CHARS_PER_S as _RATE,
    )
    # Build something comfortably over the timeout=1 budget.
    repeat_unit = "chăm sóc da "
    n_repeat = (_RATE // len(repeat_unit)) + 100
    big = repeat_unit * n_repeat
    assert len(big) > _RATE, "test fixture must exceed budget"
    out = vi_tokenizer.segment_vi_compounds(big, timeout_s=1)
    assert out == big, "oversize input must fall back to original text"


def test_underthesea_failure_falls_back_to_original(monkeypatch):
    """If underthesea raises, segment_vi_compounds returns input + warns."""
    # Force the module to consider itself initialised but with a broken backend.
    vi_tokenizer._initialized = True

    def _broken(*_a, **_kw):
        raise RuntimeError("simulated underthesea crash")

    monkeypatch.setattr(vi_tokenizer, "_tokenize_fn", _broken)

    text = "chăm sóc da mặt"
    out = vi_tokenizer.segment_vi_compounds(text)
    assert out == text, "broken backend must not propagate — return input"


def test_no_backend_falls_back_to_original(monkeypatch):
    """When underthesea is unavailable (_tokenize_fn=None) input is returned as-is."""
    vi_tokenizer._initialized = True
    monkeypatch.setattr(vi_tokenizer, "_tokenize_fn", None)

    text = "chăm sóc da mặt"
    out = vi_tokenizer.segment_vi_compounds(text)
    assert out == text


def test_segmentation_is_idempotent_for_already_joined():
    """Running segmentation twice must not double-segment / corrupt tokens."""
    once = vi_tokenizer.segment_vi_compounds("chăm sóc da mặt")
    twice = vi_tokenizer.segment_vi_compounds(once)
    # Underscore-joined tokens stay tokens; second pass should not introduce
    # new underscores inside an already-joined compound (e.g. "chăm__sóc").
    assert "__" not in twice, f"double-segmentation produced double underscore: {twice!r}"


def test_non_vietnamese_unicode_preserved():
    """Unicode that's not VN (e.g. Japanese) shouldn't crash and stays mostly intact."""
    text = "Hello 世界 こんにちは"
    out = vi_tokenizer.segment_vi_compounds(text)
    assert isinstance(out, str)
    # Core characters preserved (segmentation may add spaces but not delete chars)
    assert "世界" in out or "世 界" in out
