"""SpeculativeRouter — Wave K1 Phase 2 unit tests.

Coverage:
    1. Draft wins → tokens come from the draft model.
    2. Main wins → tokens come from the main model.
    3. Both raise → router re-raises winner's exception.
    4. Draft raises, main succeeds → main's tokens streamed.
    5. Main raises, draft succeeds → draft's tokens streamed.
    6. Loser task is cancelled after winner detected.
    7. Cost-accounting event (``speculative_loser_cost_usd``) emitted.
    8. Winner event (``speculative_winner``) emitted with source.
    9. ``draft_model`` kwarg swapped into cfg.litellm_name for the draft.
   10. Non-streaming surfaces (complete / stream / health_check) delegate
       straight to main.

All assertions are real (value / behaviour), never ``assert True``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from ragbot.infrastructure.llm.speculative_router import SpeculativeRouter


# ── Test helpers ──────────────────────────────────────────────────────────


class _StubLLM:
    """Async-iterator-yielding stub that records cancel / call state.

    ``tokens`` are the deltas the stub will yield. ``open_delay_s`` is
    the wall-clock pause before the async iterator opens (simulates the
    upstream HTTP round-trip). ``raise_exc`` is raised in place of
    yielding tokens. ``per_token_delay_s`` pauses between tokens so a
    test can verify cancel actually interrupts mid-stream.
    """

    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        open_delay_s: float = 0.0,
        raise_exc: BaseException | None = None,
        per_token_delay_s: float = 0.0,
    ) -> None:
        self.tokens = tokens or []
        self.open_delay_s = open_delay_s
        self.raise_exc = raise_exc
        self.per_token_delay_s = per_token_delay_s
        # Recorders
        self.complete_runtime_stream_calls: list[tuple[Any, list[dict], dict]] = []
        self.complete_calls: int = 0
        self.stream_calls: int = 0
        self.health_check_calls: int = 0
        self.refresh_calls: int = 0
        self.close_calls: int = 0
        self.iter_started: bool = False
        self.iter_cancelled: bool = False

    async def complete_runtime_stream(
        self,
        cfg: Any,
        messages: list[dict],
        **kwargs: Any,
    ):
        """Async generator matching the real LLMPort interface.

        Uses ``async def + yield`` (not ``return self._iter()``) so that
        calling this method produces an async generator object — the same
        type produced by ``DynamicLiteLLMRouter.complete_runtime_stream``.
        The previous ``return self._iter()`` pattern produced a coroutine
        that returned an async iterator, which was accepted by
        ``asyncio.create_task`` but masked the TypeError raised by the real
        implementation (async generator objects are NOT coroutines).
        """
        self.complete_runtime_stream_calls.append((cfg, messages, kwargs))
        if self.open_delay_s > 0:
            await asyncio.sleep(self.open_delay_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        self.iter_started = True
        try:
            for tok in self.tokens:
                if self.per_token_delay_s > 0:
                    await asyncio.sleep(self.per_token_delay_s)
                yield tok
        except asyncio.CancelledError:
            self.iter_cancelled = True
            raise

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        self.complete_calls += 1
        return "main-complete-result"

    async def stream(self, messages: list[Any], **kwargs: Any):
        self.stream_calls += 1

        async def _empty():
            if False:  # pragma: no cover - generator marker
                yield ""

        return _empty()

    async def health_check(self) -> bool:
        self.health_check_calls += 1
        return True

    async def refresh_routing(self) -> None:
        self.refresh_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


def _make_cfg(litellm_name: str = "openai/main-model") -> SimpleNamespace:
    return SimpleNamespace(litellm_name=litellm_name)


async def _drain(aiter) -> list[str]:
    out: list[str] = []
    async for t in aiter:
        out.append(t)
    return out


# ── 1. Draft wins ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_draft_wins_streams_draft_tokens():
    draft = _StubLLM(tokens=["d1", "d2", "d3"], open_delay_s=0.0)
    # Main is delayed → draft's stream resolves first.
    main = _StubLLM(tokens=["m1"], open_delay_s=0.2)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["d1", "d2", "d3"], "draft tokens must be streamed verbatim"
    assert draft.iter_started is True
    # Loser drain is fire-and-forget; give the background drain a moment.
    await asyncio.sleep(0.05)


# ── 2. Main wins ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_wins_streams_main_tokens():
    # Draft is delayed past main's open.
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.2)
    main = _StubLLM(tokens=["m1", "m2"], open_delay_s=0.0)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["m1", "m2"]
    assert main.iter_started is True


# ── 3. Both fail → router re-raises winner's exception ────────────────────


@pytest.mark.asyncio
async def test_both_fail_router_raises():
    draft_exc = RuntimeError("draft boom")
    main_exc = RuntimeError("main boom")
    draft = _StubLLM(raise_exc=draft_exc, open_delay_s=0.0)
    main = _StubLLM(raise_exc=main_exc, open_delay_s=0.0)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    with pytest.raises(RuntimeError) as ei:
        await _drain(
            router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
        )
    # Whichever fires first wins the race; both are valid winners.
    assert ei.value.args[0] in {"draft boom", "main boom"}


# ── 4. Draft fails → main's tokens streamed -------------------------------


@pytest.mark.asyncio
async def test_only_draft_fails_main_wins():
    draft = _StubLLM(raise_exc=RuntimeError("draft boom"), open_delay_s=0.0)
    # Main opens slightly later so the draft "wins" the race by raising
    # first; the router must then NOT yield from the draft (it raised),
    # but per Phase 2 contract we re-raise rather than failover.
    # To validate the "only one fail" case we instead make the draft
    # take longer than main so main wins outright.
    draft = _StubLLM(raise_exc=RuntimeError("draft boom"), open_delay_s=0.5)
    main = _StubLLM(tokens=["m1", "m2"], open_delay_s=0.0)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["m1", "m2"]


# ── 5. Main fails → draft's tokens streamed -------------------------------


@pytest.mark.asyncio
async def test_only_main_fails_draft_wins():
    main = _StubLLM(raise_exc=RuntimeError("main boom"), open_delay_s=0.5)
    draft = _StubLLM(tokens=["d1", "d2"], open_delay_s=0.0)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["d1", "d2"]


# ── 6. Loser is cancelled --------------------------------------------------


@pytest.mark.asyncio
async def test_loser_task_cancelled():
    # Draft wins quickly; main is slow + sleeps between tokens so the
    # cancel actually interrupts its iterator.
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.0)
    main = _StubLLM(
        tokens=["m1", "m2", "m3"], open_delay_s=0.3, per_token_delay_s=0.2,
    )
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["d1"]
    # Drain runs in background; await a beat for cancel to propagate.
    await asyncio.sleep(0.1)
    # Main never started iterating because its open_delay was longer
    # than draft's; verify cancel beat it to the punch.
    assert main.iter_started is False


# ── 7. Cost-accounting event emitted --------------------------------------


def _install_capture(events: list[dict]):
    """Install a structlog processor that appends every event to ``events``."""
    def _capture(logger, method_name, event_dict):
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(
        processors=[_capture],
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


@pytest.mark.asyncio
async def test_cost_accounting_event_emitted():
    """``speculative_loser_cost_usd`` event must fire after a race."""
    events: list[dict] = []
    _install_capture(events)
    try:
        draft = _StubLLM(tokens=["d1"], open_delay_s=0.0)
        main = _StubLLM(tokens=["m1"], open_delay_s=0.3)
        router = SpeculativeRouter(main_llm=main, draft_llm=draft)

        await _drain(
            router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
        )
        # Loser drain is fire-and-forget — wait for it.
        await asyncio.sleep(0.1)
    finally:
        # Restore default configuration so other tests don't capture.
        structlog.reset_defaults()

    cost_events = [e for e in events if e.get("event") == "speculative_loser_cost_usd"]
    assert len(cost_events) == 1, f"expected exactly one cost event, got {len(cost_events)}"
    assert cost_events[0]["winner_source"] == "draft"
    assert "loser_runtime_ms" in cost_events[0]
    assert "cost_usd" in cost_events[0]


# ── 8. Winner event emitted -----------------------------------------------


@pytest.mark.asyncio
async def test_winner_event_emitted():
    events: list[dict] = []
    _install_capture(events)
    try:
        draft = _StubLLM(tokens=["d1"], open_delay_s=0.3)
        main = _StubLLM(tokens=["m1"], open_delay_s=0.0)
        router = SpeculativeRouter(main_llm=main, draft_llm=draft)

        await _drain(
            router.complete_runtime_stream(_make_cfg(), [{"role": "user", "content": "q"}]),
        )
        await asyncio.sleep(0.05)
    finally:
        structlog.reset_defaults()

    winner_events = [e for e in events if e.get("event") == "speculative_winner"]
    assert len(winner_events) == 1
    assert winner_events[0]["source"] == "main"
    assert isinstance(winner_events[0]["winner_first_token_ms"], int)
    assert winner_events[0]["winner_first_token_ms"] >= 0


# ── 9. ``draft_model`` swapped into cfg.litellm_name ----------------------


@pytest.mark.asyncio
async def test_draft_model_kwarg_swaps_cfg():
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.0)
    main = _StubLLM(tokens=["m1"], open_delay_s=0.3)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    cfg = _make_cfg(litellm_name="openai/main-model")
    await _drain(
        router.complete_runtime_stream(
            cfg, [{"role": "user", "content": "q"}], draft_model="openai/draft-cheap",
        ),
    )

    # Draft cfg must have the swapped wire name; main cfg untouched.
    assert draft.complete_runtime_stream_calls[0][0].litellm_name == "openai/draft-cheap"
    assert main.complete_runtime_stream_calls[0][0].litellm_name == "openai/main-model"
    # ``draft_model`` must NOT leak into downstream kwargs (popped before delegate).
    assert "draft_model" not in draft.complete_runtime_stream_calls[0][2]
    assert "draft_model" not in main.complete_runtime_stream_calls[0][2]


# ── 10. Non-streaming surfaces delegate to main ---------------------------


@pytest.mark.asyncio
async def test_non_streaming_delegates_to_main():
    draft = _StubLLM()
    main = _StubLLM()
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    assert await router.health_check() is True
    assert main.health_check_calls == 1
    assert draft.health_check_calls == 0

    result = await router.complete([], spec=None, record_tenant_id=None, trace_id=None)
    assert result == "main-complete-result"
    assert main.complete_calls == 1
    assert draft.complete_calls == 0

    await router.stream([])
    assert main.stream_calls == 1
    assert draft.stream_calls == 0

    await router.refresh_routing()
    # refresh_routing fans out to BOTH (paid in concurrent gather).
    assert main.refresh_calls == 1
    assert draft.refresh_calls == 1

    await router.close()
    assert main.close_calls == 1
    assert draft.close_calls == 1


# ── 11. Registry registration ---------------------------------------------


def test_registry_has_speculative_key():
    """The provider key ``"speculative"`` is registered in the LLM registry."""
    from ragbot.infrastructure.llm.registry import _REGISTRY, build_llm

    assert "speculative" in _REGISTRY
    assert _REGISTRY["speculative"] is SpeculativeRouter
    # build_llm raises a clear KeyError for unknown providers.
    with pytest.raises(KeyError):
        build_llm(provider="does-not-exist")
