from __future__ import annotations

import pytest

try:
    from ragbot.application.services.multi_agent_review.agent_port import (
        AgentRole,
        ReviewVerdict,
    )
    from ragbot.application.services.multi_agent_review.parser import (
        parse_agent_response,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "multi_agent_review subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


def test_parser_extracts_all_sections() -> None:
    raw = (
        "SUMMARY: Looks fine\n"
        "ISSUES:\n- Missing test\n- Comment is wrong\n"
        "SUGGESTIONS:\n- Add fixture\n"
        "RISKS:\n- Race condition\n"
        "VERDICT: approved_with_fix"
    )
    out = parse_agent_response(AgentRole.ARCHITECT, raw)
    assert out.role is AgentRole.ARCHITECT
    assert out.summary == "Looks fine"
    assert out.issues == ["Missing test", "Comment is wrong"]
    assert out.suggestions == ["Add fixture"]
    assert out.risks == ["Race condition"]
    assert out.verdict is ReviewVerdict.APPROVED_WITH_FIX


def test_parser_handles_none_marker() -> None:
    raw = (
        "SUMMARY: Clean\n"
        "ISSUES:\n- none\n"
        "SUGGESTIONS:\n- none\n"
        "RISKS:\n- none\n"
        "VERDICT: approved"
    )
    out = parse_agent_response(AgentRole.CRITIC, raw)
    assert out.issues == []
    assert out.suggestions == []
    assert out.risks == []
    assert out.verdict is ReviewVerdict.APPROVED


def test_parser_rejects_falls_through() -> None:
    raw = (
        "SUMMARY: Bad\n"
        "ISSUES:\n- HALLU risk\n"
        "SUGGESTIONS:\n- redo\n"
        "RISKS:\n- none\n"
        "VERDICT: rejected"
    )
    out = parse_agent_response(AgentRole.QUALITY_GUARDIAN, raw)
    assert out.verdict is ReviewVerdict.REJECTED
    assert out.issues == ["HALLU risk"]


def test_parser_unknown_verdict_defaults_to_with_fix() -> None:
    raw = (
        "SUMMARY: x\n"
        "ISSUES:\n- y\n"
        "SUGGESTIONS:\n- none\n"
        "RISKS:\n- none\n"
        "VERDICT: maybe"
    )
    out = parse_agent_response(AgentRole.EVALUATOR, raw)
    assert out.verdict is ReviewVerdict.APPROVED_WITH_FIX


def test_parser_handles_numbered_bullets() -> None:
    raw = (
        "SUMMARY: ok\n"
        "ISSUES:\n1. first\n2) second\n"
        "SUGGESTIONS:\n- none\n"
        "RISKS:\n- none\n"
        "VERDICT: approved"
    )
    out = parse_agent_response(AgentRole.RAG_SPECIALIST, raw)
    assert out.issues == ["first", "second"]
