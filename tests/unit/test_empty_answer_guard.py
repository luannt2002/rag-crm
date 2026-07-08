"""P0.1 — empty-answer guard.

Root cause (cross-bot fail-verify 2026-07-07, ``fail_verify_analysis_20260707``):
S-048 (single-turn) and B-050/B-052 (coref) returned an EMPTY answer — the LLM
completed but produced blank content (chunks present, generation failure). A
blank message is not an answer; ``generate.py`` only WARN-logs it (OBS-1) and
returns it verbatim, so the user sees an empty reply.

Fix. When the bot owner opts in (``empty_answer_guard_enabled``), ``guard_output``
detects a whitespace-only answer and substitutes the bot's OWN
``oos_answer_template`` — the same governed substitution numeric-fidelity /
brand-scope / grounding-fail-closed use (owner text, never app-injected; an
empty string is not an LLM answer to override → sacred #10 safe). Default OFF
preserves the legacy verbatim-empty behaviour.

These drive the ``guard_output`` node directly (mirrors
``test_grounding_fail_closed``) so no full LangGraph boot is needed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from ragbot.orchestration.nodes.guard_output import guard_output
from ragbot.shared.constants import DEFAULT_EMPTY_ANSWER_GUARD_ENABLED
from tests.unit._node_test_helpers import FakeGuardrail, RecordingStepTracker

_ANSWER = "Dạ, giá gói massage 60 phút là 300.000đ ạ."
_OOS = "Sorry, I do not have that information yet."


def _pcfg(state: dict[str, Any], key: str, default: Any) -> Any:
    pcfg = state.get("pipeline_config") or {}
    return pcfg.get(key, default)


def _resolved_oos_template(_state: dict[str, Any]) -> str:
    return _OOS


def _schedule_grounding_check_background(**_kw: Any) -> None:  # pragma: no cover
    raise AssertionError("background scheduler must not run in these tests")


def _make_state(answer: str, **pcfg: Any) -> dict[str, Any]:
    return {
        "step_tracker": RecordingStepTracker(),
        "record_tenant_id": uuid4(),
        "record_bot_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "intent": "factoid",
        "answer": answer,
        "graded_chunks": [{"text": "Some context."}],
        "guardrail_flags": [],
        "system_prompt": "",
        "retrieve_mode": "",
        # grounding OFF + serial guards so ONLY the empty-guard behaviour is under
        # test (a dead grounder would otherwise substitute OOS for factoid).
        "pipeline_config": {
            "grounding_check_enabled": False,
            "pipeline_parallel_output_guards_enabled": False,
            **pcfg,
        },
    }


async def _run(state: dict[str, Any]) -> dict:
    return await guard_output(
        state,
        llm=None,
        model_resolver=None,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )


def test_default_flag_is_off() -> None:
    """Strangler: default preserves legacy verbatim-empty behaviour."""
    assert DEFAULT_EMPTY_ANSWER_GUARD_ENABLED is False


@pytest.mark.asyncio
async def test_empty_answer_filled_with_oos_when_enabled() -> None:
    out = await _run(_make_state("", empty_answer_guard_enabled=True))
    assert out["answer"] == _OOS
    assert out["answer_type"] == "empty_guard"
    rule_ids = {f.get("rule_id") for f in out["guardrail_flags"]}
    assert "empty_answer_guard" in rule_ids


@pytest.mark.asyncio
async def test_whitespace_answer_treated_as_empty() -> None:
    out = await _run(_make_state("   \n\t ", empty_answer_guard_enabled=True))
    assert out["answer"] == _OOS
    assert out["answer_type"] == "empty_guard"


@pytest.mark.asyncio
async def test_empty_answer_verbatim_when_disabled() -> None:
    """Flag OFF (default) → empty-guard never fires; answer NOT the OOS template."""
    out = await _run(_make_state("", empty_answer_guard_enabled=False))
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "empty_answer_guard" not in rule_ids
    assert out.get("answer", "") != _OOS


@pytest.mark.asyncio
async def test_nonempty_answer_not_filled_when_enabled() -> None:
    """A real answer must pass through untouched even with the guard ON."""
    out = await _run(_make_state(_ANSWER, empty_answer_guard_enabled=True))
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "empty_answer_guard" not in rule_ids
    assert out.get("answer", _ANSWER) != _OOS
