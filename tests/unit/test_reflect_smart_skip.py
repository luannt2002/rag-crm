"""Reflect smart-skip retry — Stream P1 (T2 perf).

When (a) the grounding-check guardrail did not fire on pass-1 AND (b) the
pass-1 top retrieval score clears ``reflect_skip_top_score_floor``, the
reflect node honours the existing answer rather than wiping it to trigger
a second generate + guard_output cycle (~5-6s saved).

Default ``reflect_skip_if_grounded = False`` preserves the legacy retry
semantics.
"""

from __future__ import annotations

import asyncio

from tests.unit._node_test_helpers import (
    build_test_graph,
    make_resolver_and_llm,
    make_state,
    node_callable,
)


def _reflect(compiled):
    return node_callable(compiled, "reflect")


def test_reflect_smart_skip_default_off_preserves_retry():
    """Default flag OFF → retry path fires when judge says rewrite."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="ngắn quá",
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.85, "content": "12 tháng"},
        ],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            # reflect_skip_if_grounded NOT set → falls back to default False
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Legacy path: answer cleared so generate fires again.
    assert out["answer"] == ""
    assert out["reflect_retries"] == 1


def test_reflect_smart_skip_grounded_and_high_score_bypasses_retry():
    """Skip flag ON + no grounding_fail flag + top_score >= floor → skip retry."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="12 tháng từ ngày kích hoạt.",
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.78, "content": "12 tháng"},
        ],
        guardrail_flags=[
            # Output stage fired but NOT llm_grounding_fail → grounded.
            {"stage": "output", "rule_id": "soft_pii_redact", "severity": "info"},
        ],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            "reflect_skip_if_grounded": True,
            "reflect_skip_top_score_floor": 0.30,
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Smart-skip honours the answer — no wipe, no retry.
    assert out == {}


def test_reflect_smart_skip_grounding_failed_still_retries():
    """Skip flag ON but grounding-check FAILED → retry must still fire."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="ngắn quá",
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.78, "content": "12 tháng"},
        ],
        guardrail_flags=[
            {"stage": "output", "rule_id": "llm_grounding_fail", "severity": "warn"},
        ],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            "reflect_skip_if_grounded": True,
            "reflect_skip_top_score_floor": 0.30,
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Not grounded → legacy retry path.
    assert out["answer"] == ""
    assert out["reflect_retries"] == 1


def test_reflect_smart_skip_low_top_score_still_retries():
    """Skip flag ON + grounded but top_score below floor → retry."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="ngắn quá",
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.12, "content": "vague"},
        ],
        guardrail_flags=[],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            "reflect_skip_if_grounded": True,
            "reflect_skip_top_score_floor": 0.30,
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Thin retrieval (0.12 < 0.30) → smart-skip declines; retry fires.
    assert out["answer"] == ""
    assert out["reflect_retries"] == 1


def test_reflect_smart_skip_does_not_fire_on_second_pass():
    """retries already at 1 → cap path engages regardless of skip flag."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="rewrite please")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="thử lại lần 2",
        graded_chunks=[
            {"chunk_id": "c1", "score": 0.9, "content": "12 tháng"},
        ],
        guardrail_flags=[],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            "reflect_skip_if_grounded": True,
        },
        reflect_retries=1,
    )
    out = asyncio.run(_reflect(compiled)(state))
    # Cap reached → keep current answer.
    assert out == {}


def test_reflect_smart_skip_judge_keep_unchanged():
    """Judge says keep → skip flag is irrelevant; existing answer survives."""
    resolver, llm, _cfg = make_resolver_and_llm(text_response="keep")
    compiled, *_ = build_test_graph(
        resolver_override=resolver, llm_override=llm
    )
    state = make_state(
        query="bảo hành",
        answer="12 tháng từ kích hoạt.",
        graded_chunks=[{"chunk_id": "c1", "score": 0.7, "content": "12 tháng"}],
        pipeline_config={
            "structured_output_enabled": False,
            "reflect_use_structured_output": False,
            "max_reflect_retries": 1,
            "reflect_skip_if_grounded": True,
        },
        reflect_retries=0,
    )
    out = asyncio.run(_reflect(compiled)(state))
    assert out == {}


def test_reflect_constants_exported():
    """Defaults preserve legacy retry semantics."""
    from ragbot.shared.constants import (
        DEFAULT_REFLECT_SKIP_IF_GROUNDED,
        DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
    )

    assert DEFAULT_REFLECT_SKIP_IF_GROUNDED is False
    assert 0.0 <= DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR <= 1.0


def test_reflect_plan_limit_schema_entries():
    """Bot owner overrides via ``plan_limits.reflect_skip_if_grounded`` etc."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

    flag = PLAN_LIMIT_SCHEMA["reflect_skip_if_grounded"]
    assert flag["type"] == "bool"
    assert flag["default"] is False

    floor = PLAN_LIMIT_SCHEMA["reflect_skip_top_score_floor"]
    assert floor["type"] == "float"
    assert floor["min"] == 0.0
    assert floor["max"] == 1.0
