"""[T2-CostPerf] Tests — grounding_check truly parallel (asyncio.create_task).

Verifies:
- _schedule_grounding_check_background returns a Task (not None) when a loop is running.
- The returned Task does not block the caller's continuation.
- Breach is logged via structlog warning (grounding_async_breach).
- A passing check emits grounding_async_pass at INFO.
- Background judge error emits grounding_async_judge_error (not propagated to caller).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from ragbot.orchestration.query_graph import (
    _run_grounding_check_background,
    _schedule_grounding_check_background,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_for_background_schedule(**overrides: Any) -> dict[str, Any]:
    """Minimal state dict for _schedule_grounding_check_background tests."""
    base: dict[str, Any] = {
        "answer": "Dịch vụ X có giá 100.000đ / tháng.",
        "graded_chunks": [{"content": "Dịch vụ X giá 100k", "score": 0.85}],
        "record_tenant_id": uuid4(),
        "record_bot_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
    }
    base.update(overrides)
    return base


class _FakeModelResolver:
    async def resolve_runtime(self, *, record_tenant_id, record_bot_id, purpose):
        cfg = MagicMock()
        cfg.litellm_name = "mock/model"
        return cfg


class _FakeLLM:
    """LLM stub that returns a response indicating 'relevant' (no grounding breach)."""

    def __init__(self, text: str = "The answer is supported by the chunks."):
        self.text = text
        self.called = False

    async def complete(self, cfg: Any, *, messages: Any) -> dict[str, Any]:
        self.called = True
        return {
            "text": self.text,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0.0001,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGroundingCheckDoesNotBlockResponse:
    """_schedule_grounding_check_background must return a Task immediately."""

    @pytest.mark.asyncio
    async def test_grounding_check_does_not_block_response(self):
        state = _make_state_for_background_schedule()
        model_resolver = _FakeModelResolver()
        llm = _FakeLLM()

        # _schedule returns a Task — not None — when running inside an event loop.
        task = _schedule_grounding_check_background(
            state=state,
            threshold=0.3,
            top_score=0.85,
            model_resolver=model_resolver,
            llm=llm,
        )

        # Must return a Task immediately — not await the judge.
        assert task is not None, "Expected a Task, got None (check asyncio loop availability)"
        assert isinstance(task, asyncio.Task)

        # Stash on state for cleanup
        # Caller can proceed to return the response NOW (not blocking on judge).
        # Await the task so the test loop can clean up gracefully.
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()

    @pytest.mark.asyncio
    async def test_schedule_returns_task_stashed_on_state(self):
        state = _make_state_for_background_schedule()
        model_resolver = _FakeModelResolver()
        llm = _FakeLLM()

        task = _schedule_grounding_check_background(
            state=state,
            threshold=0.3,
            top_score=0.85,
            model_resolver=model_resolver,
            llm=llm,
        )
        # The task should be stored on state so graceful-shutdown hooks can await it.
        assert state.get("grounding_async_task") is task
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


class TestGroundingBreachLoggedAsync:
    """When grounding judge detects a breach it logs grounding_async_breach."""

    @pytest.mark.asyncio
    async def test_grounding_breach_logged_async(self):
        """A HALLU breach in the background judge emits a WARNING log."""
        state = _make_state_for_background_schedule(
            answer="Giá dịch vụ X là 200.000đ.",  # mismatches chunk
            graded_chunks=[{"content": "Dịch vụ X giá 100k", "score": 0.85}],
        )

        # Stub OutputGuardrail.llm_grounding_check to return a hit (breach).
        mock_hit = MagicMock()
        mock_hit.rule_id = "grounding_fail"
        mock_hit.severity = "warn"
        mock_hit.action = "log"

        model_resolver = _FakeModelResolver()
        llm = _FakeLLM()

        with patch(
            "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
            new=AsyncMock(return_value=mock_hit),
        ), patch(
            "ragbot.orchestration.query_graph.logger",
        ) as mock_logger:
            await _run_grounding_check_background(
                answer=state["answer"],
                retrieved_chunks=state["graded_chunks"],
                record_tenant_id=state["record_tenant_id"],
                record_bot_id=state["record_bot_id"],
                request_id=state["request_id"],
                message_id=state["message_id"],
                threshold=0.3,
                top_score=0.85,
                model_resolver=model_resolver,
                llm=llm,
            )
            # Breach should have been logged at WARNING level.
            warning_calls = [
                call for call in mock_logger.warning.call_args_list
                if call.args and "grounding_async_breach" in str(call.args[0])
            ]
            assert len(warning_calls) >= 1, (
                "Expected grounding_async_breach WARNING log, got: "
                f"{mock_logger.warning.call_args_list}"
            )

    @pytest.mark.asyncio
    async def test_grounding_pass_logged_info(self):
        """When judge passes (no breach) it emits grounding_async_pass at INFO."""
        state = _make_state_for_background_schedule()
        model_resolver = _FakeModelResolver()
        llm = _FakeLLM()

        with patch(
            "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
            new=AsyncMock(return_value=None),  # None = no breach
        ), patch(
            "ragbot.orchestration.query_graph.logger",
        ) as mock_logger:
            await _run_grounding_check_background(
                answer=state["answer"],
                retrieved_chunks=state["graded_chunks"],
                record_tenant_id=state["record_tenant_id"],
                record_bot_id=state["record_bot_id"],
                request_id=state["request_id"],
                message_id=state["message_id"],
                threshold=0.3,
                top_score=0.85,
                model_resolver=model_resolver,
                llm=llm,
            )
            info_calls = [
                call for call in mock_logger.info.call_args_list
                if call.args and "grounding_async_pass" in str(call.args[0])
            ]
            assert len(info_calls) >= 1, (
                "Expected grounding_async_pass INFO log"
            )

    @pytest.mark.asyncio
    async def test_grounding_judge_error_logged_not_raised(self):
        """LLM error in background judge is swallowed — response already shipped."""
        state = _make_state_for_background_schedule()
        model_resolver = _FakeModelResolver()
        llm = _FakeLLM()

        with patch(
            "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
            new=AsyncMock(side_effect=RuntimeError("upstream 503")),
        ), patch(
            "ragbot.orchestration.query_graph.logger",
        ) as mock_logger:
            # Must NOT raise — background task must not crash the worker loop.
            await _run_grounding_check_background(
                answer=state["answer"],
                retrieved_chunks=state["graded_chunks"],
                record_tenant_id=state["record_tenant_id"],
                record_bot_id=state["record_bot_id"],
                request_id=state["request_id"],
                message_id=state["message_id"],
                threshold=0.3,
                top_score=0.85,
                model_resolver=model_resolver,
                llm=llm,
            )
            error_calls = [
                call for call in mock_logger.warning.call_args_list
                if call.args and "grounding_async_judge_error" in str(call.args[0])
            ]
            assert len(error_calls) >= 1
