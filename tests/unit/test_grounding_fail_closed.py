"""S1-B / AG-A2 — grounding-net fail-CLOSED.

Root cause. The output-guardrail node builds the LLM grounding judge
(``llm_fn``) only when ``model_resolver is not None and llm is not None``. When
the runtime is unwired (a dead/None grounder), ``llm_fn`` stays ``None`` and the
node previously returned the LLM answer UNVERIFIED — the HALLU net was silently
OFF for a grounding-eligible turn.

Fix. ``guard_output`` now detects the grounder-dead condition (grounding
enabled + intent eligible + NOT the async ship-then-check path + ``llm_fn is
None``) and, under the default ``grounding_failure_mode = "fail_closed"``,
substitutes the bot's ``oos_answer_template`` (the existing refuse contract)
instead of shipping the ungrounded answer. Bot owners opt back into the legacy
pass-through per-bot via ``plan_limits.grounding_failure_mode = "fail_open"``.

These tests drive the ``guard_output`` node directly with explicit DI kwargs so
``llm`` / ``model_resolver`` can be forced to ``None`` (the dead-grounder case)
without booting the full LangGraph.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.orchestration.nodes.guard_output import guard_output
from ragbot.shared.constants import (
    DEFAULT_GROUNDING_FAILURE_MODE,
    GROUNDING_FAILURE_MODE_FAIL_CLOSED,
    GROUNDING_FAILURE_MODE_FAIL_OPEN,
)
from tests.unit._node_test_helpers import (
    FakeGuardrail,
    RecordingStepTracker,
)

_ANSWER = "The hotline is 0900111222 and the price is 500000."
_OOS = "Sorry, I do not have that information yet."


def _pcfg(state: dict[str, Any], key: str, default: Any) -> Any:
    """Mirror the real ``_pcfg``: read from ``pipeline_config`` with default."""
    pcfg = state.get("pipeline_config") or {}
    return pcfg.get(key, default)


def _resolved_oos_template(_state: dict[str, Any]) -> str:
    return _OOS


def _schedule_grounding_check_background(**_kw: Any) -> None:  # pragma: no cover
    raise AssertionError("background scheduler must not run on the dead path")


def _make_state(**pcfg: Any) -> dict[str, Any]:
    return {
        "step_tracker": RecordingStepTracker(),
        "record_tenant_id": uuid4(),
        "record_bot_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "intent": "factoid",
        "answer": _ANSWER,
        "graded_chunks": [{"text": "Some unrelated context."}],
        "guardrail_flags": [],
        "system_prompt": "",
        "retrieve_mode": "",
        "pipeline_config": {
            "grounding_check_enabled": True,
            "grounding_intents": ("factoid",),
            **pcfg,
        },
    }


# --------------------------------------------------------------------------- #
# Constant contract                                                           #
# --------------------------------------------------------------------------- #
def test_default_grounding_failure_mode_is_fail_closed() -> None:
    """HALLU=0 sacred — the SAFE default refuses rather than ship unverified."""
    assert DEFAULT_GROUNDING_FAILURE_MODE == GROUNDING_FAILURE_MODE_FAIL_CLOSED
    assert GROUNDING_FAILURE_MODE_FAIL_CLOSED != GROUNDING_FAILURE_MODE_FAIL_OPEN


# --------------------------------------------------------------------------- #
# Behavioural — dead grounder                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dead_grounder_refuses_under_fail_closed() -> None:
    """model_resolver / llm None → llm_fn None → answer substituted by OOS."""
    state = _make_state()
    out = await guard_output(
        state,
        llm=None,
        model_resolver=None,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )
    # The ungrounded answer MUST NOT reach the user.
    assert out["answer"] == _OOS
    assert out["answer"] != _ANSWER
    assert out["answer_type"] == "blocked"
    rule_ids = {f.get("rule_id") for f in out["guardrail_flags"]}
    assert "grounding_fail_closed" in rule_ids


@pytest.mark.asyncio
async def test_dead_grounder_passes_under_fail_open_opt_out() -> None:
    """Per-bot opt-out restores the legacy pass-through (answer unchanged).

    With fail_open the node does NOT short-circuit; it proceeds to the regex
    guards (FakeGuardrail returns no hits) and returns the original answer.
    """
    state = _make_state(grounding_failure_mode=GROUNDING_FAILURE_MODE_FAIL_OPEN)
    out = await guard_output(
        state,
        llm=None,
        model_resolver=None,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )
    # Legacy behaviour: answer is NOT overwritten with the OOS template.
    assert out.get("answer", _ANSWER) != _OOS
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "grounding_fail_closed" not in rule_ids


@pytest.mark.asyncio
async def test_non_eligible_intent_not_failed_closed() -> None:
    """A non-grounding intent (greeting) never expected a judge → no refuse.

    The grounder-dead guard must only fire for grounding-ELIGIBLE intents,
    otherwise every greeting would be force-refused.
    """
    state = _make_state()
    state["intent"] = "greeting"
    out = await guard_output(
        state,
        llm=None,
        model_resolver=None,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )
    assert out.get("answer", _ANSWER) != _OOS
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "grounding_fail_closed" not in rule_ids


@pytest.mark.asyncio
async def test_grounding_disabled_not_failed_closed() -> None:
    """Grounding turned OFF entirely → no judge expected → no fail-closed."""
    state = _make_state(grounding_check_enabled=False)
    out = await guard_output(
        state,
        llm=None,
        model_resolver=None,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )
    assert out.get("answer", _ANSWER) != _OOS
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "grounding_fail_closed" not in rule_ids


@pytest.mark.asyncio
async def test_live_grounder_not_failed_closed() -> None:
    """When the runtime IS wired, llm_fn is built → NOT dead → no refuse.

    The grounding judge then runs through the guardrail; FakeGuardrail returns
    no hits so the answer passes verified (not via the fail-closed branch).
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.model_name = "mock/model"
    resolver.resolve_runtime = AsyncMock(return_value=cfg)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"finish_reason": "stop"})

    # Force the serial guard path so FakeGuardrail (no ``_persist``) is enough.
    state = _make_state(pipeline_parallel_output_guards_enabled=False)
    out = await guard_output(
        state,
        llm=llm,
        model_resolver=resolver,
        guardrail=FakeGuardrail(),
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )
    # Live grounder → did NOT take the fail-closed refuse branch.
    rule_ids = {f.get("rule_id") for f in out.get("guardrail_flags", [])}
    assert "grounding_fail_closed" not in rule_ids
    assert out.get("answer", _ANSWER) != _OOS
