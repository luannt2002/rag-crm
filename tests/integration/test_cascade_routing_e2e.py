"""Cascade Routing — end-to-end integration through the ``generate`` node.

CT-2 ship (builds on WA-2 cascade_router_helper). Verifies the full
in-graph contract:

- Cascade routing OFF (default) → no state mutation, no resolver call.
- Cascade routing ON + score < T_LOW (0.4) → cheap-tier model chosen.
- Cascade routing ON + T_LOW ≤ score < T_HIGH (0.75) → mid-tier (default).
- Cascade routing ON + score ≥ T_HIGH (0.95) → premium-tier model chosen.
- Missing ``complexity_score`` field → graceful fallback (score = 0.0,
  cheap tier when configured).
- Missing ``system_config`` keys → NullObject (empty string from resolver),
  helper preserves the current model so the answer path stays alive.
- Wire never raises — resolver outage degrades silently.

The test drives the *real* compiled LangGraph ``generate`` closure with
stubbed ports (LLM, model_resolver, guardrail) so the wire site is
exercised inside the production code path. The cascade-tier model name
is asserted on ``state["resolved_answer_model"]`` after the node runs.
Application MUST NOT override the LLM answer text — only the MODEL
CHOICE changes here, which we also assert.
"""

from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog

from ragbot.shared.constants import (
    DEFAULT_CASCADE_T_HIGH,
    DEFAULT_CASCADE_T_LOW,
)


# ── Sanity guard — the band we test against is the band we ship.
def test_cascade_band_constants_match_test_inputs() -> None:
    """The test scores (0.4 / 0.75 / 0.95) MUST land in the three bands.

    If a future tier-policy change moves the thresholds, the integration
    inputs below must also move. This guard fails loudly so that drift is
    visible at the test-collection stage.
    """
    assert 0.4 < DEFAULT_CASCADE_T_LOW, (
        f"low-band test input 0.4 must stay below T_LOW={DEFAULT_CASCADE_T_LOW}"
    )
    assert DEFAULT_CASCADE_T_LOW <= 0.75 < DEFAULT_CASCADE_T_HIGH, (
        "mid-band test input 0.75 must satisfy "
        f"T_LOW={DEFAULT_CASCADE_T_LOW} ≤ 0.75 < T_HIGH={DEFAULT_CASCADE_T_HIGH}"
    )
    assert 0.95 >= DEFAULT_CASCADE_T_HIGH, (
        f"high-band test input 0.95 must reach T_HIGH={DEFAULT_CASCADE_T_HIGH}"
    )


# ── Minimal port doubles ──────────────────────────────────────────────────


class _FakeInvocationLogger:
    @asynccontextmanager
    async def invoke_model(self, **_kw: Any):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name: str, **_kw: Any):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        return []

    async def check_output(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        return []


class _RecordingCascadeResolver:
    """Resolver double that records cascade calls and routes by score band.

    ``resolve_runtime`` (used by the LLM invoke helper) returns a generic
    mock cfg; ``resolve_cascade_runtime`` (used by the wire) returns a
    band-keyed name so the test can pin the chosen tier.
    """

    def __init__(
        self,
        *,
        low_model: str = "cheap-tier-mock",
        mid_model: str = "mid-tier-mock",
        high_model: str = "premium-tier-mock",
        cascade_returns_empty: bool = False,
        cascade_raises: bool = False,
    ) -> None:
        self._low = low_model
        self._mid = mid_model
        self._high = high_model
        self._cascade_returns_empty = cascade_returns_empty
        self._cascade_raises = cascade_raises
        self.cascade_calls: list[tuple[float, dict[str, Any] | None]] = []

        cfg = MagicMock()
        cfg.litellm_name = "mock/model"
        cfg.model_name = "mock/model"
        cfg.embedding_dimension = 8
        cfg.provider = MagicMock(code="mock", name="mock", timeout_ms=10_000)
        cfg.params = MagicMock()
        cfg.params.max_tokens = None
        self._cfg = cfg
        self.resolve_runtime = AsyncMock(return_value=cfg)
        self.resolve_embedding = AsyncMock(return_value=cfg)

    def resolve_cascade_runtime(
        self,
        complexity_score: float,
        bot_config: dict[str, Any] | None = None,
        *,
        config_getter: Any | None = None,  # noqa: ARG002 — parity
    ) -> str:
        self.cascade_calls.append((complexity_score, bot_config))
        if self._cascade_raises:
            raise RuntimeError("cascade resolver outage")
        if self._cascade_returns_empty:
            return ""
        # Band math mirrors resolve_cascade_runtime so the integration
        # exercises real threshold semantics even with the double.
        score = float(complexity_score)
        if score != score:  # NaN guard
            score = 0.0
        score = max(0.0, min(1.0, score))
        if score < DEFAULT_CASCADE_T_LOW:
            return self._low
        if score < DEFAULT_CASCADE_T_HIGH:
            return self._mid
        return self._high


def _make_llm(answer_text: str = "stub answer") -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": answer_text,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    })
    llm.complete_runtime_stream = None
    return llm


def _bot(*, enabled: bool, plan_limits: dict[str, Any] | None = None) -> SimpleNamespace:
    pl: dict[str, Any] = dict(plan_limits or {})
    pl["cascade_routing_enabled"] = enabled
    return SimpleNamespace(
        bot_id="bot-cascade-e2e",
        plan_limits=pl,
        threshold_overrides={},
    )


# ── Fixture: build the real graph and pluck the generate closure ─────────


def _build_generate_closure(
    *,
    resolver: _RecordingCascadeResolver,
    answer_text: str = "stub answer",
):
    """Compile the real graph; return (afunc, llm, resolver)."""
    from ragbot.orchestration.query_graph import build_graph

    llm = _make_llm(answer_text=answer_text)
    compiled = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    node = compiled.nodes["generate"].bound
    return node.afunc, llm


def _make_state(
    *,
    cascade_bot: SimpleNamespace,
    complexity_score: float | None = 0.75,
    graded_chunks: list[dict] | None = None,
    model_used: str = "status-quo-model",
) -> dict[str, Any]:
    """GraphState that drives ``generate`` through the plain-text path."""
    chunks = graded_chunks if graded_chunks is not None else [
        {
            "chunk_id": "00000000-0000-0000-0000-000000000001",
            "text": "Reference content used to keep refuse short-circuit OFF.",
            "document_name": "doc.pdf",
            "chunk_index": 0,
        },
    ]
    state: dict[str, Any] = {
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": "bot-cascade-e2e",
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "language": "vi",
        "query": (
            "Vui lòng cho biết chi tiết bảng so sánh các phương án triển khai "
            "kèm theo phân tích ưu nhược điểm rõ ràng để ra quyết định."
        ),
        "rewritten_query": None,
        "graded_chunks": chunks,
        "conversation_history": [],
        "answer": "",
        "model_used": model_used,
        "intent": "factoid",
        "step_tracker": _FakeStepTracker(),
        "bot_system_prompt": "You are a helpful assistant.",
        "bot": cascade_bot,
        "kg_service": None,
        "session_factory": None,
        "pipeline_config": {
            "structured_output_enabled": False,
            "generate_use_structured_output": False,
            "prompt_compression_enabled": False,
            "lost_in_middle_reorder_enabled": False,
            "condense_history_limit": 6,
            "refuse_short_circuit_enabled": True,
        },
    }
    if complexity_score is not None:
        state["complexity_score"] = complexity_score
    return state


# ── 1. Default OFF — no state mutation ────────────────────────────────────


class TestCascadeOffDefault:
    """When the bot has not opted in, cascade routing MUST be a no-op."""

    def test_default_off_no_cascade_resolver_call(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=False), complexity_score=0.95)
        asyncio.run(afunc(state))
        assert llm.complete.await_count >= 1, "generate did not call llm.complete"
        assert resolver.cascade_calls == [], (
            "OFF default must short-circuit before resolve_cascade_runtime"
        )
        assert "resolved_answer_model" not in state, (
            "OFF default must NOT write resolved_answer_model"
        )

    def test_off_default_does_not_alter_answer_text(self) -> None:
        """Sacred: application changes MODEL not TEXT."""
        resolver = _RecordingCascadeResolver()
        afunc, llm = _build_generate_closure(
            resolver=resolver, answer_text="LLM-owned answer body",
        )
        state = _make_state(cascade_bot=_bot(enabled=False), complexity_score=0.75)
        out = asyncio.run(afunc(state))
        assert out["answer"] == "LLM-owned answer body"


# ── 2. Opt-in — three-band routing ────────────────────────────────────────


class TestCascadeOptInBands:
    """When opted in, score determines tier."""

    def test_low_score_routes_to_cheap_tier(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=True), complexity_score=0.4)
        asyncio.run(afunc(state))
        assert state.get("resolved_answer_model") == "cheap-tier-mock"
        # Resolver was called exactly once with the low-band score.
        assert len(resolver.cascade_calls) == 1
        assert resolver.cascade_calls[0][0] == pytest.approx(0.4)
        # LLM still gets called — cascade is a hint, not a bypass.
        assert llm.complete.await_count >= 1

    def test_mid_score_routes_to_default_tier(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=True), complexity_score=0.75)
        asyncio.run(afunc(state))
        assert state.get("resolved_answer_model") == "mid-tier-mock"

    def test_high_score_routes_to_premium_tier(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=True), complexity_score=0.95)
        asyncio.run(afunc(state))
        assert state.get("resolved_answer_model") == "premium-tier-mock"

    def test_resolver_bot_config_carries_plan_limits(self) -> None:
        """Per-bot ``plan_limits`` reach the resolver verbatim (override path)."""
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        bot = _bot(
            enabled=True,
            plan_limits={"cascade_low_model": "per-bot-cheap"},
        )
        state = _make_state(cascade_bot=bot, complexity_score=0.4)
        asyncio.run(afunc(state))
        assert len(resolver.cascade_calls) == 1
        _score, bot_cfg = resolver.cascade_calls[0]
        assert bot_cfg is not None
        assert bot_cfg.get("cascade_low_model") == "per-bot-cheap"


# ── 3. Graceful degradation ───────────────────────────────────────────────


class TestCascadeDegradation:
    """Aux gaps degrade silently — answer path stays alive."""

    def test_missing_complexity_score_does_not_crash(self) -> None:
        """No ``complexity_score`` → 0.0 → cheap tier; generate still completes."""
        resolver = _RecordingCascadeResolver()
        afunc, llm = _build_generate_closure(resolver=resolver)
        state = _make_state(
            cascade_bot=_bot(enabled=True), complexity_score=None,
        )
        out = asyncio.run(afunc(state))
        # Pipeline did not crash; answer was produced.
        assert llm.complete.await_count >= 1
        assert out.get("answer") == "stub answer"
        # Resolver was called with score 0.0 (helper coerces missing → 0.0).
        assert resolver.cascade_calls[0][0] == pytest.approx(0.0)

    def test_null_object_resolver_keeps_current_model(self) -> None:
        """Resolver returns "" (no system_config keys) → no state mutation."""
        resolver = _RecordingCascadeResolver(cascade_returns_empty=True)
        afunc, llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=True), complexity_score=0.75)
        asyncio.run(afunc(state))
        assert llm.complete.await_count >= 1
        # Helper short-circuits NullObject → current_model preserved → wire
        # does NOT set state["resolved_answer_model"] (no swap detected).
        assert "resolved_answer_model" not in state

    def test_resolver_exception_does_not_kill_answer(self) -> None:
        """Resolver raise → wire catches → answer path unaffected."""
        resolver = _RecordingCascadeResolver(cascade_raises=True)
        afunc, llm = _build_generate_closure(resolver=resolver)
        state = _make_state(cascade_bot=_bot(enabled=True), complexity_score=0.95)
        out = asyncio.run(afunc(state))
        assert llm.complete.await_count >= 1
        assert out.get("answer") == "stub answer"
        # No state mutation when resolver explodes.
        assert "resolved_answer_model" not in state

    def test_nan_score_clamps_to_cheap_band(self) -> None:
        """NaN score → 0.0 → cheap tier (clamp inside helper)."""
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        state = _make_state(
            cascade_bot=_bot(enabled=True), complexity_score=math.nan,
        )
        asyncio.run(afunc(state))
        assert state.get("resolved_answer_model") == "cheap-tier-mock"


# ── 4. structlog event surface ────────────────────────────────────────────


class TestCascadeObservability:
    """Wire MUST emit a ``cascade_routing_applied`` event when a swap happens."""

    def test_structlog_event_fires_on_swap(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        state = _make_state(
            cascade_bot=_bot(enabled=True),
            complexity_score=0.4,
            model_used="status-quo-model",
        )
        with structlog.testing.capture_logs() as captured:
            asyncio.run(afunc(state))
        events = [e for e in captured if e.get("event") == "cascade_routing_applied"]
        assert events, (
            f"cascade_routing_applied event not captured; captured={captured!r}"
        )
        evt = events[0]
        assert evt.get("resolved_model") == "cheap-tier-mock"
        assert evt.get("complexity_score") == pytest.approx(0.4)
        assert evt.get("bot_id") == "bot-cascade-e2e"
        assert evt.get("previous_model") == "status-quo-model"

    def test_no_event_fires_when_off(self) -> None:
        resolver = _RecordingCascadeResolver()
        afunc, _llm = _build_generate_closure(resolver=resolver)
        state = _make_state(
            cascade_bot=_bot(enabled=False), complexity_score=0.95,
        )
        with structlog.testing.capture_logs() as captured:
            asyncio.run(afunc(state))
        assert not any(
            e.get("event") == "cascade_routing_applied" for e in captured
        ), "OFF default must NOT emit cascade_routing_applied"
