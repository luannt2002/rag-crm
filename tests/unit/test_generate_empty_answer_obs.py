"""OBS-1 — empty-answer silent-failure observability.

An answer that comes back blank on the SUCCESS path (the LLM completed but
produced no content) is indistinguishable from a real answer in the success
metrics. The generate node now flags + WARN-logs this condition. These tests
cover the pure decision helper ``_is_empty_answer`` (mirrors the
``_resolve_verbatim_fence`` pure-helper test pattern — no heavy graph DI).

Sacred-rule #10: detection ONLY. The helper never authors or substitutes
answer text; the empty answer is returned verbatim by the node.
"""
from __future__ import annotations

from ragbot.orchestration.nodes.generate import _is_empty_answer


def test_empty_string_is_flagged():
    assert _is_empty_answer("") is True


def test_none_is_flagged():
    assert _is_empty_answer(None) is True


def test_whitespace_only_is_flagged():
    assert _is_empty_answer("   \n\t  ") is True


def test_real_answer_is_not_flagged():
    assert _is_empty_answer("Dạ, giá là 499.000 VNĐ ạ.") is False


def test_refuse_template_is_not_flagged():
    # A non-empty refuse/oos answer is a real (intentional) answer — not the
    # silent-failure case OBS-1 targets.
    assert _is_empty_answer("Xin lỗi, em chưa có thông tin này.") is False


def test_single_nonspace_char_is_not_flagged():
    # Detection is purely "has any non-whitespace content" — no length/quality
    # judgement (that would be application authoring an answer opinion).
    assert _is_empty_answer("0") is False
