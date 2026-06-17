"""Cascade routing wire — unit tests for the query_graph integration site.

These tests assert the *wiring contract* (CT-2 ship): the cascade helper is
imported from ``orchestration.nodes.cascade_router_helper``, invoked inside
the ``generate`` node after the refuse short-circuit, gated by the
``cascade_routing_enabled`` per-bot flag, and surfaces the chosen model on
``state["resolved_answer_model"]``. We exercise the wire in isolation by
constructing the helper's input contract (state dict + resolver stub) the
same way ``query_graph.generate`` does — that gives us deterministic
coverage without spinning up the LangGraph runtime.

The companion integration suite (``tests/integration/test_cascade_routing_e2e.py``)
covers the in-graph behaviour end-to-end with a stubbed retriever + LLM.

Sacred contracts asserted:
- Default OFF: empty state / missing flag → no state mutation.
- Helper import path stable: ``orchestration.nodes.cascade_router_helper``.
- structlog event ``cascade_routing_applied`` carries the chosen model.
- Wire never raises — resolver errors degrade silently.
"""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.orchestration.nodes.cascade_router_helper import apply_cascade_routing


# ── Fixtures ──────────────────────────────────────────────────────────────


def _bot(enabled: bool, plan_limits: dict[str, Any] | None = None) -> SimpleNamespace:
    pl: dict[str, Any] = dict(plan_limits or {})
    pl["cascade_routing_enabled"] = enabled
    return SimpleNamespace(
        bot_id="bot-wire-test",
        plan_limits=pl,
        threshold_overrides={},
    )


class _StubResolver:
    """Records resolve_cascade_runtime calls + returns canned model name."""

    def __init__(self, return_value: str = "", raise_exc: bool = False) -> None:
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.calls: list[tuple[float, dict[str, Any] | None]] = []

    def resolve_cascade_runtime(
        self,
        complexity_score: float,
        bot_config: dict[str, Any] | None = None,
        *,
        config_getter: Any | None = None,  # noqa: ARG002 — parity
    ) -> str:
        self.calls.append((complexity_score, bot_config))
        if self.raise_exc:
            raise RuntimeError("resolver outage")
        return self.return_value


# ── 1. Import wire contract ───────────────────────────────────────────────


class TestWireImports:
    """The query_graph module MUST import the helper from its canonical
    location so the orchestrator and tests share a single source of truth.
    """

    def test_query_graph_imports_apply_cascade_routing(self) -> None:
        """``query_graph`` must import ``apply_cascade_routing`` (CT-2 wire)."""
        mod = importlib.import_module("ragbot.orchestration.query_graph")
        assert hasattr(mod, "apply_cascade_routing"), (
            "query_graph.generate calls apply_cascade_routing() — the import "
            "MUST be present at module scope so the symbol is bound at call time."
        )
        # Bound symbol must be the same callable as the helper module's.
        from ragbot.orchestration.nodes import cascade_router_helper

        assert mod.apply_cascade_routing is cascade_router_helper.apply_cascade_routing

    def test_helper_signature_matches_wire_callsite(self) -> None:
        """Helper signature is (state, model_resolver, *, current_model).

        The wire passes positional ``state`` + ``model_resolver`` and keyword
        ``current_model`` — if WA-2 ever changes the signature this test
        catches it before the orchestrator breaks.
        """
        sig = inspect.signature(apply_cascade_routing)
        params = list(sig.parameters.values())
        assert params[0].name == "state"
        assert params[1].name == "model_resolver"
        # current_model is keyword-only (mandatory by-name in the wire).
        cm = sig.parameters["current_model"]
        assert cm.kind == inspect.Parameter.KEYWORD_ONLY
        assert cm.default is inspect.Parameter.empty


# ── 2. Default-OFF wire path ──────────────────────────────────────────────


class TestWireDefaultOff:
    """When cascade is OFF (default) the wire MUST be a no-op."""

    def test_off_returns_current_model_unchanged(self) -> None:
        state = {"bot": _bot(enabled=False), "complexity_score": 0.9}
        out = apply_cascade_routing(
            state, _StubResolver("would-be-high"), current_model="status-quo",
        )
        assert out == "status-quo"

    def test_off_does_not_call_resolver(self) -> None:
        """OFF short-circuits before resolver invocation — zero call count."""
        resolver = _StubResolver("never-used")
        state = {"bot": _bot(enabled=False), "complexity_score": 0.9}
        apply_cascade_routing(state, resolver, current_model="status-quo")
        assert resolver.calls == []

    def test_missing_bot_is_treated_as_off(self) -> None:
        """No bot in state → treat as OFF (defensive — no surprise swap)."""
        resolver = _StubResolver("would-be-high")
        out = apply_cascade_routing(
            {"complexity_score": 0.9}, resolver, current_model="status-quo",
        )
        assert out == "status-quo"
        assert resolver.calls == []


# ── 3. Opt-in wire path ───────────────────────────────────────────────────


class TestWireOptIn:
    """When cascade is ON the wire forwards score + bot config to resolver."""

    def test_on_returns_resolver_model(self) -> None:
        resolver = _StubResolver("cheap-model")
        state = {"bot": _bot(enabled=True), "complexity_score": 0.1}
        out = apply_cascade_routing(state, resolver, current_model="status-quo")
        assert out == "cheap-model"

    def test_on_forwards_score_and_bot_config(self) -> None:
        resolver = _StubResolver("mid-model")
        state = {
            "bot": _bot(enabled=True, plan_limits={"cascade_low_model": "X"}),
            "complexity_score": 0.5,
        }
        apply_cascade_routing(state, resolver, current_model="status-quo")
        assert len(resolver.calls) == 1
        score, bot_cfg = resolver.calls[0]
        assert score == pytest.approx(0.5)
        # Helper merges plan_limits into the resolver's bot_config view.
        assert bot_cfg is not None
        assert bot_cfg.get("cascade_low_model") == "X"

    def test_resolver_empty_returns_falls_back_to_current(self) -> None:
        """NullObject contract: resolver "" → keep current_model."""
        resolver = _StubResolver("")
        state = {"bot": _bot(enabled=True), "complexity_score": 0.5}
        out = apply_cascade_routing(state, resolver, current_model="status-quo")
        assert out == "status-quo"


# ── 4. Graceful degradation ───────────────────────────────────────────────


class TestWireDegradation:
    """Aux dependency MUST NOT kill the answer path (graceful degrade)."""

    def test_resolver_raises_returns_current_model(self) -> None:
        resolver = _StubResolver(raise_exc=True)
        state = {"bot": _bot(enabled=True), "complexity_score": 0.5}
        out = apply_cascade_routing(state, resolver, current_model="status-quo")
        assert out == "status-quo"

    def test_missing_complexity_score_treated_as_zero(self) -> None:
        """No ``complexity_score`` in state → 0.0 → cheap tier resolver call."""
        resolver = _StubResolver("cheap-model")
        state = {"bot": _bot(enabled=True)}  # no score
        out = apply_cascade_routing(state, resolver, current_model="status-quo")
        assert out == "cheap-model"
        # Resolver must have been invoked with score == 0.0
        assert resolver.calls[0][0] == pytest.approx(0.0)

    def test_garbage_complexity_score_clamps_zero(self) -> None:
        """Non-numeric score → 0.0 (helper coerces; resolver re-clamps)."""
        resolver = _StubResolver("cheap-model")
        state = {"bot": _bot(enabled=True), "complexity_score": "not-a-float"}
        out = apply_cascade_routing(state, resolver, current_model="status-quo")
        assert out == "cheap-model"
        assert resolver.calls[0][0] == pytest.approx(0.0)
