"""B5 Phase B — async grounding check (background, non-blocking).

Invariants for the B5 cut over. The sync grounding judge tail-loads ~1.6s
onto p95. For high-confidence requests (factoid intent + top_score >=
``DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD``) the bot owner can
opt in to running the judge as a fire-and-forget asyncio.Task — response
ships immediately, breach is logged + emits ``grounding_fail_total``
out-of-band for alerting.

Rollback rule: if ``grounding_fail_total`` > 0 / week post-enable, flip
``grounding_check_async_enabled`` back to False and post-mortem before
re-enabling.

Tests are unit-only: helper-level (no LangGraph boot, no DB, no real
LLM). Stubs cover the four eligibility conditions + the breach paths.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
import structlog.testing

from ragbot.infrastructure.guardrails.local_guardrail import GuardrailHit
from ragbot.orchestration import query_graph as qg
from ragbot.orchestration.nodes import guard_output as _guard_output_module
from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA


def _qg_and_guard_src() -> str:
    """query_graph + guard_output node source concatenated.

    The guard_output node body was lifted out of ``build_graph`` into
    ``orchestration/nodes/guard_output.py`` (pure relocation); these
    source-level pins must scan both files.
    """
    return inspect.getsource(qg) + "\n" + inspect.getsource(_guard_output_module)
from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
    DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
)


def _install_log_capture() -> structlog.testing.LogCapture:
    """Install a process-wide structlog capture and return it. Caller
    inspects ``capture.entries`` for the recorded events."""
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[cap],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    # Replace the module-level logger so it picks up the new config.
    qg.logger = structlog.get_logger("ragbot.orchestration.query_graph")
    return cap


# ---------------------------------------------------------------------------
# 1. Default constants — HALLU=0 sacred preserved.
# ---------------------------------------------------------------------------
def test_default_async_enabled_is_false() -> None:
    """Default MUST be False. Opt-in only — bot owner must explicitly enable
    after acknowledging the HALLU rollback rule (grounding_fail_total > 0
    / week → revert to sync)."""
    assert DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED is False, (
        "Async grounding default must stay False to preserve HALLU=0 sacred "
        "guarantee. Flipping True silently bypasses sync block for all bots."
    )


def test_default_async_intents_factoid_only() -> None:
    """Narrowest cut: only ``factoid`` is async-eligible by default.
    Comparison / aggregation / multi_hop keep the sync path until factoid
    bakes clean — broadening the set without HALLU evidence is forbidden."""
    assert DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS == ("factoid",)


def test_default_async_top_score_threshold_high() -> None:
    """Top-score floor of 0.7 keeps gray-zone (0.18..0.30) and mid-quality
    (0.30..0.70) retrievals on the sync path. Async path only fires when
    retrieval signal is unambiguous."""
    assert DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD == 0.7


def test_plan_limit_schema_wires_async_keys() -> None:
    """``PLAN_LIMIT_SCHEMA`` must expose the three async knobs so the
    resolve chain (bot column > plan_limits > system_config > schema) can
    surface bot-owner overrides without code change."""
    for k in (
        "grounding_check_async_enabled",
        "grounding_check_async_top_score_threshold",
        "grounding_check_async_intents",
    ):
        assert k in PLAN_LIMIT_SCHEMA, f"PLAN_LIMIT_SCHEMA missing {k!r}"
    assert PLAN_LIMIT_SCHEMA["grounding_check_async_enabled"]["default"] is False
    assert (
        PLAN_LIMIT_SCHEMA["grounding_check_async_top_score_threshold"]["default"]
        == DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD
    )


# ---------------------------------------------------------------------------
# 2. _schedule_grounding_check_background — fire-and-forget contract.
# ---------------------------------------------------------------------------
@dataclass
class _StubLLMOut:
    text: str = ""


class _StubLLM:
    async def complete(self, _cfg: Any, *, messages: list[dict], **_kwargs: Any) -> dict:
        # Worst-case judge says everything supported — non-breach path.
        # Return enough lines to match the regex parse fallback.
        body = "1. SUPPORTED\n2. SUPPORTED\n3. SUPPORTED"
        return {"text": body, "finish_reason": "stop", "usage": {}}


class _StubLLMBreach:
    async def complete(self, _cfg: Any, *, messages: list[dict], **_kwargs: Any) -> dict:
        body = "1. NOT_SUPPORTED\n2. NOT_SUPPORTED\n3. NOT_SUPPORTED"
        return {"text": body, "finish_reason": "stop", "usage": {}}


class _StubResolver:
    async def resolve_runtime(self, **_: Any) -> Any:
        @dataclass
        class _Cfg:
            model_name: str = "stub"
            litellm_name: str = "stub/stub-v1"
        return _Cfg()


def _state_with_answer(answer: str = "An answer.", chunks: list[dict] | None = None) -> dict:
    chunks = chunks if chunks is not None else [
        {"chunk_id": "c1", "content": "Supporting fact 1.", "score": 0.85},
        {"chunk_id": "c2", "content": "Supporting fact 2.", "score": 0.81},
    ]
    return {
        "answer": answer,
        "graded_chunks": chunks,
        "record_tenant_id": "tenant-uuid",
        "record_bot_id": "bot-uuid",
        "request_id": "req-uuid",
        "message_id": 12345,
    }


@pytest.mark.asyncio
async def test_schedule_returns_task_and_does_not_block() -> None:
    """``_schedule_grounding_check_background`` MUST return immediately
    with an in-flight asyncio.Task. The caller path (response ship) cannot
    be blocked on the judge."""
    state = _state_with_answer()
    task = qg._schedule_grounding_check_background(
        state=state,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=_StubLLM(),
    )
    assert task is not None, "scheduler must return an asyncio.Task"
    assert isinstance(task, asyncio.Task)
    # Caller path must not have waited — task is still pending (or just
    # finishing). Either way: it's a Task, the caller didn't await.
    assert "grounding_async_task" in state
    # Drain the task so the test loop tears down cleanly.
    await task
    assert task.done()


@pytest.mark.asyncio
async def test_schedule_handles_no_running_loop_gracefully() -> None:
    """When there is no running loop (degenerate sync caller), the
    scheduler must NOT raise — it returns None and closes the coro to
    avoid the "coroutine was never awaited" warning."""
    # Simulate the no-loop branch by monkeypatching asyncio.create_task to
    # raise RuntimeError as it would from a sync context.
    real_create = asyncio.create_task

    def _raises(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("no running event loop")

    asyncio.create_task = _raises  # type: ignore[assignment]
    try:
        state = _state_with_answer()
        task = qg._schedule_grounding_check_background(
            state=state,
            threshold=0.3,
            top_score=0.85,
            model_resolver=_StubResolver(),
            llm=_StubLLM(),
        )
        assert task is None
        # State must not have a task entry on the degenerate path.
        assert "grounding_async_task" not in state
    finally:
        asyncio.create_task = real_create  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 3. _run_grounding_check_background — breach path emits warning + metric.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_breach_emits_warning_log() -> None:
    """When the LLM judge says >threshold of the answer sentences are
    NOT_SUPPORTED, the background task must emit ``grounding_async_breach``
    at WARNING — alerting picks it up out-of-band."""
    cap = _install_log_capture()

    await qg._run_grounding_check_background(
        answer="Sentence one. Sentence two. Sentence three.",
        retrieved_chunks=[
            {"chunk_id": "c1", "content": "Unrelated content.", "score": 0.85},
        ],
        record_tenant_id="tenant-uuid",
        record_bot_id="bot-uuid",
        request_id="req-uuid",
        message_id=12345,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=_StubLLMBreach(),
    )

    breach_events = [
        e for e in cap.entries
        if e.get("event") == "grounding_async_breach"
    ]
    assert breach_events, (
        "expected 'grounding_async_breach' warning after breach run; "
        f"got entries={[e.get('event') for e in cap.entries]!r}"
    )
    ev = breach_events[0]
    assert ev["log_level"] == "warning"
    # Identity payload MUST be present for forensic audit.
    assert ev["record_tenant_id"] == "tenant-uuid"
    assert ev["record_bot_id"] == "bot-uuid"
    assert ev["request_id"] == "req-uuid"
    assert ev["message_id"] == 12345
    assert ev["rule_id"] == "llm_grounding_fail"


@pytest.mark.asyncio
async def test_pass_emits_info_log() -> None:
    """When the judge passes (no breach) the background task logs
    ``grounding_async_pass`` at INFO — no alert raised."""
    cap = _install_log_capture()

    await qg._run_grounding_check_background(
        answer="Supported sentence one. Supported sentence two.",
        retrieved_chunks=[
            {"chunk_id": "c1", "content": "Supporting fact 1.", "score": 0.85},
            {"chunk_id": "c2", "content": "Supporting fact 2.", "score": 0.81},
        ],
        record_tenant_id="tenant-uuid",
        record_bot_id="bot-uuid",
        request_id="req-uuid",
        message_id=12345,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=_StubLLM(),
    )

    pass_events = [e for e in cap.entries if e.get("event") == "grounding_async_pass"]
    breach_events = [e for e in cap.entries if e.get("event") == "grounding_async_breach"]
    assert pass_events, (
        "expected 'grounding_async_pass' info record on non-breach; "
        f"got events={[e.get('event') for e in cap.entries]!r}"
    )
    assert not breach_events, "non-breach run must NOT emit grounding_async_breach"


@pytest.mark.asyncio
async def test_judge_failure_does_not_raise(monkeypatch: Any) -> None:
    """Errors bubbling out of ``OutputGuardrail.llm_grounding_check`` MUST
    be swallowed by the background task — the response has already shipped;
    bubbling exceptions up would crash the worker loop. The error is
    logged as ``grounding_async_judge_error``."""
    cap = _install_log_capture()

    async def _raising_judge(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("simulated judge crash")

    monkeypatch.setattr(qg.OutputGuardrail, "llm_grounding_check", _raising_judge)

    # Must NOT raise.
    await qg._run_grounding_check_background(
        answer="Some answer text.",
        retrieved_chunks=[
            {"chunk_id": "c1", "content": "Fact.", "score": 0.85},
        ],
        record_tenant_id="tenant-uuid",
        record_bot_id="bot-uuid",
        request_id="req-uuid",
        message_id=12345,
        threshold=0.3,
        top_score=0.85,
        model_resolver=_StubResolver(),
        llm=_StubLLM(),
    )

    err_events = [
        e for e in cap.entries if e.get("event") == "grounding_async_judge_error"
    ]
    assert err_events, (
        "Background task must catch judge crashes and emit "
        f"grounding_async_judge_error; got events={[e.get('event') for e in cap.entries]!r}"
    )
    breach_events = [
        e for e in cap.entries if e.get("event") == "grounding_async_breach"
    ]
    assert not breach_events, (
        "judge crash must NOT register as a breach (no signal — only an error)"
    )


# ---------------------------------------------------------------------------
# 4. guard_output source-level wiring — async gate logic.
# ---------------------------------------------------------------------------
def test_guard_output_uses_async_constants() -> None:
    """The orchestrator MUST reference the three new async constants in
    ``guard_output``. Source-level guard against accidental decoupling."""
    src = " ".join(_qg_and_guard_src().split())
    for needed in (
        '_pcfg( state, "grounding_check_async_enabled"',
        '_pcfg( state, "grounding_check_async_intents"',
        '_pcfg( state, "grounding_check_async_top_score_threshold"',
    ):
        assert needed in src, (
            f"guard_output must read {needed!r} from pipeline_config — "
            "missing wires async opt-in"
        )


def test_guard_output_suppresses_sync_llm_fn_when_async() -> None:
    """When ``_grounding_async`` is True, the sync ``llm_fn`` path is
    suppressed (``not _grounding_async`` predicate). Source-level guard:
    the predicate MUST be present, otherwise the sync judge would run
    twice (once inline, once in the background task)."""
    src = _qg_and_guard_src()
    assert "and not _grounding_async\n" in src, (
        "guard_output must gate the sync llm_fn assignment on "
        "``not _grounding_async`` — otherwise sync + async judges both fire."
    )


def test_guard_output_schedules_background_task_on_async() -> None:
    """When async is enabled, ``_schedule_grounding_check_background`` must
    be invoked after the sync guardrails — never before (response must
    not block on judge)."""
    src = inspect.getsource(qg)
    assert "_schedule_grounding_check_background(" in src, (
        "guard_output must call _schedule_grounding_check_background when "
        "async grounding is enabled."
    )


# ---------------------------------------------------------------------------
# 5. Eligibility gates — predicate covers all four AND-conditions.
# ---------------------------------------------------------------------------
def _evaluate_gate(
    *,
    grounding_enabled: bool,
    intent: str,
    async_enabled: bool,
    async_intents: tuple[str, ...],
    top_score: float,
    top_score_floor: float,
    grounding_intents: tuple[str, ...] = ("factoid", "comparison", "aggregation", "multi_hop"),
    model_resolver_present: bool = True,
    llm_present: bool = True,
) -> bool:
    """Mirror the predicate inside guard_output for unit testing."""
    grounding_eligible = intent in grounding_intents
    return bool(
        grounding_enabled
        and grounding_eligible
        and async_enabled
        and intent in async_intents
        and top_score >= top_score_floor
        and model_resolver_present
        and llm_present
    )


def test_gate_blocks_when_async_not_enabled() -> None:
    """Async path requires bot-owner explicit opt-in. Default False keeps
    every bot on the sync path."""
    assert _evaluate_gate(
        grounding_enabled=True, intent="factoid",
        async_enabled=False, async_intents=("factoid",),
        top_score=0.85, top_score_floor=0.7,
    ) is False


def test_gate_blocks_when_intent_not_async_eligible() -> None:
    """Comparison/aggregation/multi_hop are async-ineligible by default —
    even when async_enabled=True they take the sync path."""
    assert _evaluate_gate(
        grounding_enabled=True, intent="comparison",
        async_enabled=True, async_intents=("factoid",),
        top_score=0.85, top_score_floor=0.7,
    ) is False


def test_gate_blocks_when_top_score_below_floor() -> None:
    """Top-score floor is the confidence gate. 0.69 (just below 0.70) MUST
    take the sync path — even with bot opt-in and factoid intent."""
    assert _evaluate_gate(
        grounding_enabled=True, intent="factoid",
        async_enabled=True, async_intents=("factoid",),
        top_score=0.69, top_score_floor=0.7,
    ) is False


def test_gate_allows_when_all_four_conditions_pass() -> None:
    """All four AND-conditions met: factoid intent + opt-in + top_score
    0.85 + grounding enabled → async path."""
    assert _evaluate_gate(
        grounding_enabled=True, intent="factoid",
        async_enabled=True, async_intents=("factoid",),
        top_score=0.85, top_score_floor=0.7,
    ) is True


def test_gate_blocks_when_grounding_globally_disabled() -> None:
    """If the bot owner disabled grounding entirely
    (``grounding_check_enabled=False``) the async gate MUST also be
    closed — async cannot resurrect a disabled judge."""
    assert _evaluate_gate(
        grounding_enabled=False, intent="factoid",
        async_enabled=True, async_intents=("factoid",),
        top_score=0.85, top_score_floor=0.7,
    ) is False


# ---------------------------------------------------------------------------
# 6. HALLU=0 rollback rule documented in constants.
# ---------------------------------------------------------------------------
def test_async_constant_rollback_rule_documented() -> None:
    """The constant docstring (comment block above the declaration) MUST
    spell out the rollback rule: ``grounding_fail_total`` > 0 / week →
    flip back to False. Source-level reminder for future maintainers."""
    import ragbot.shared.constants as c
    from pathlib import Path
    # constants is now a package — concat its modules (the comment sits in the
    # same submodule as the declaration, so contiguity is preserved).
    _pkg_dir = Path(c.__file__).resolve().parent
    src = "\n".join(p.read_text() for p in sorted(_pkg_dir.glob("*.py")))
    assert "Rollback rule" in src, (
        "constants package must document the rollback rule for the async "
        "grounding flip near DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED."
    )
    # Anchor to the assignment site (not the __all__ entry, which is the
    # first occurrence in the file).
    anchor = "DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED: Final[bool]"
    idx = src.index(anchor)
    block = src[max(0, idx - 1000): idx]
    assert "Rollback rule" in block, (
        "Rollback rule comment must sit immediately above "
        "DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED to be discovered on flip."
    )
