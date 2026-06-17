"""Unit tests for persona_quality_gate audit-only checks."""

from __future__ import annotations

from ragbot.application.services.persona_quality_gate import (
    PersonaWarning,
    audit_system_prompt,
)


def test_empty_prompt_returns_no_warnings() -> None:
    assert audit_system_prompt("") == []
    assert audit_system_prompt(None) == []


def test_short_clean_prompt_no_warnings() -> None:
    prompt = (
        "Bạn là trợ lý tư vấn. Khi khách hỏi, tra context và trả lời "
        "lịch sự. Nếu không có thông tin, nói rõ."
    )
    warnings = audit_system_prompt(prompt)
    assert warnings == []


def test_oversized_prompt_warns() -> None:
    prompt = "x" * 30000
    warnings = audit_system_prompt(prompt, oversized_threshold=20000)
    codes = [w.code for w in warnings]
    assert "persona_oversized" in codes


def test_pollution_pricing_pattern_warns() -> None:
    prompt = "Bạn là tư vấn viên. Giá 1 buổi: 20.000.000 đ"
    warnings = audit_system_prompt(prompt, oversized_threshold=100000)
    codes = [w.code for w in warnings]
    assert "persona_pollution" in codes


def test_pollution_canned_refusal_warns() -> None:
    prompt = "Khi khách hỏi → trả: chưa có chương trình khuyến mãi"
    warnings = audit_system_prompt(prompt, oversized_threshold=100000)
    codes = [w.code for w in warnings]
    assert "persona_pollution" in codes


def test_directive_conflict_warns() -> None:
    prompt = "Section A: KHÔNG tra TL cho dịch vụ X. Section B: tra [TL-3]."
    warnings = audit_system_prompt(prompt, oversized_threshold=100000)
    codes = [w.code for w in warnings]
    assert "persona_directive_conflict" in codes
    high_warnings = [w for w in warnings if w.severity == "high"]
    assert high_warnings


def test_multi_finding_aggregates() -> None:
    prompt = (
        "x" * 25000
        + "\nGiá: 8 triệu/buổi"
        + "\nKHÔNG tra TL"
        + "\ntra [TL-3]"
    )
    warnings = audit_system_prompt(prompt)
    codes = {w.code for w in warnings}
    assert "persona_oversized" in codes
    assert "persona_pollution" in codes
    assert "persona_directive_conflict" in codes


def test_warnings_are_immutable() -> None:
    w = PersonaWarning(code="x", severity="warn", detail="d")
    try:
        w.code = "y"  # type: ignore[misc]
    except (AttributeError, Exception):
        pass
    assert w.code == "x"


def test_pollution_patterns_use_unicode_correctly() -> None:
    """Vietnamese diacritic content must match patterns reliably."""
    prompt = "giá kịch bản: 8 triệu/buổi"
    warnings = audit_system_prompt(prompt, oversized_threshold=100000)
    assert any(w.code == "persona_pollution" for w in warnings)


def test_operator_can_override_patterns() -> None:
    """Empty patterns tuple = no pollution checks (operator opt-out)."""
    prompt = "Giá: 20.000.000 đ chưa có chương trình"
    warnings = audit_system_prompt(
        prompt,
        pollution_patterns=(),
        oversized_threshold=100000,
    )
    codes = [w.code for w in warnings]
    assert "persona_pollution" not in codes
