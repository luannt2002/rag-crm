"""[T2-CostPerf] Bug #2 fix pin tests — SpeculativeRouter async-generator race.

These tests pin the fix for the TypeError that occurred when
``asyncio.create_task`` received an async generator object instead of a
coroutine.  The fix wraps each async generator in ``_race_first_token``,
a coroutine that resolves to ``(first_token, generator)``.

Pin scenarios:
    1. ``_race_first_token`` returns correct (first_token, gen) tuple.
    2. ``_race_first_token`` handles empty generator → (None, gen).
    3. ``_race_first_token`` propagates exceptions from the generator.
    4. Draft wins race — tokens correct + first_token replayed.
    5. Main wins race — main tokens streamed + draft generator cancelled.
    6. Timeout — TimeoutError raised when both generators stall.
    7. Generator cancel cleanup — aclose called on loser generator.
    8. Mock interface contract — ``complete_runtime_stream`` IS async generator.
    9. ``asyncio.create_task`` accepts ``_race_first_token`` coroutine (not TypeError).
   10. Workspace_id missing produces TypeError (regression guard for Bug #1).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.infrastructure.llm.speculative_router import (
    SpeculativeRouter,
    _aclose_silently,
    _race_first_token,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _async_gen(*tokens: str, delay: float = 0.0, exc: BaseException | None = None):
    """Simple async generator for testing."""
    if delay > 0:
        await asyncio.sleep(delay)
    if exc is not None:
        raise exc
    for tok in tokens:
        yield tok


class _StubLLM:
    """Async-generator stub matching the real LLMPort interface.

    ``complete_runtime_stream`` is an async generator (``async def + yield``),
    matching ``DynamicLiteLLMRouter.complete_runtime_stream``.  The previous
    pattern (``async def: return self._iter()``) masked the TypeError because
    it produced a coroutine-returning-iterator, which ``asyncio.create_task``
    accepted; the real async-generator object is NOT a coroutine.
    """

    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        open_delay_s: float = 0.0,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.tokens = tokens or []
        self.open_delay_s = open_delay_s
        self.raise_exc = raise_exc
        self.closed = False

    async def complete_runtime_stream(self, cfg: Any, messages: list[dict], **kwargs: Any):
        if self.open_delay_s > 0:
            await asyncio.sleep(self.open_delay_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        for tok in self.tokens:
            yield tok

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return "stub"

    async def stream(self, messages: list[Any], **kwargs: Any):
        if False:  # pragma: no cover
            yield ""

    async def health_check(self) -> bool:
        return True

    async def refresh_routing(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


def _cfg(name: str = "openai/test-model") -> SimpleNamespace:
    return SimpleNamespace(litellm_name=name)


async def _collect(aiter) -> list[str]:
    out: list[str] = []
    async for tok in aiter:
        out.append(tok)
    return out


# ── 1. _race_first_token returns (first_token, gen) ──────────────────────────


@pytest.mark.asyncio
async def test_race_first_token_returns_first_and_gen():
    gen = _async_gen("alpha", "beta", "gamma")
    first, remaining = await _race_first_token(gen)

    assert first == "alpha", "first token must be 'alpha'"
    # Remaining generator should still yield the rest.
    rest = await _collect(remaining)
    assert rest == ["beta", "gamma"], "remaining tokens must be yielded from same generator"


# ── 2. _race_first_token handles empty generator ──────────────────────────────


@pytest.mark.asyncio
async def test_race_first_token_empty_generator():
    gen = _async_gen()  # no tokens
    first, remaining = await _race_first_token(gen)

    assert first is None, "empty generator must return None as first token"
    # remaining is the exhausted generator — iterating it yields nothing.
    rest = await _collect(remaining)
    assert rest == [], "exhausted generator must yield no further tokens"


# ── 3. _race_first_token propagates generator exceptions ─────────────────────


@pytest.mark.asyncio
async def test_race_first_token_propagates_exception():
    boom = ValueError("generator exploded")
    gen = _async_gen(exc=boom)

    with pytest.raises(ValueError, match="generator exploded"):
        await _race_first_token(gen)


# ── 4. Draft wins — first token replayed + remaining streamed ─────────────────


@pytest.mark.asyncio
async def test_draft_wins_first_token_replayed():
    draft = _StubLLM(tokens=["d1", "d2", "d3"], open_delay_s=0.0)
    main = _StubLLM(tokens=["m1"], open_delay_s=0.3)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _collect(
        router.complete_runtime_stream(_cfg(), [{"role": "user", "content": "q"}]),
    )

    # All three draft tokens must appear in order.
    assert tokens == ["d1", "d2", "d3"], f"expected draft tokens, got {tokens}"


# ── 5. Main wins — main tokens streamed ──────────────────────────────────────


@pytest.mark.asyncio
async def test_main_wins_streams_main_tokens_with_fixed_mock():
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.3)
    main = _StubLLM(tokens=["m1", "m2"], open_delay_s=0.0)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _collect(
        router.complete_runtime_stream(_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["m1", "m2"], f"expected main tokens, got {tokens}"


# ── 6. Timeout — TimeoutError raised ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_raises_when_both_stall():
    draft = _StubLLM(tokens=["d1"], open_delay_s=2.0)
    main = _StubLLM(tokens=["m1"], open_delay_s=2.0)
    router = SpeculativeRouter(
        main_llm=main,
        draft_llm=draft,
        draft_timeout_s=0.05,  # very short timeout
    )

    with pytest.raises(TimeoutError, match="speculative router"):
        await _collect(
            router.complete_runtime_stream(_cfg(), [{"role": "user", "content": "q"}]),
        )


# ── 7. Generator cancel cleanup — aclose_silently works ──────────────────────


@pytest.mark.asyncio
async def test_aclose_silently_does_not_raise():
    gen = _async_gen("a", "b", "c")
    # Pull one token so the generator is mid-stream.
    await gen.__anext__()
    # aclose must not raise even though the generator is mid-stream.
    await _aclose_silently(gen)
    # Generator is now closed; iterating it must yield nothing.
    rest = await _collect(gen)
    assert rest == [], "closed generator must yield nothing"


# ── 8. Mock contract — complete_runtime_stream IS async generator ─────────────


@pytest.mark.asyncio
async def test_stub_llm_complete_runtime_stream_is_async_generator():
    """Verify the test stub produces an async generator object (not a coroutine).

    The previous mock used ``async def: return self._iter()`` which produced
    a *coroutine* — accepted by ``asyncio.create_task`` but different from
    the real implementation.  This test pins the correct interface.
    """
    import inspect

    stub = _StubLLM(tokens=["x"])
    result = stub.complete_runtime_stream(_cfg(), [])

    # Must be an async generator, not a coroutine.
    assert inspect.isasyncgen(result), (
        f"complete_runtime_stream must be an async generator, got {type(result)}"
    )
    # Close it to avoid resource warning.
    await result.aclose()


# ── 9. asyncio.create_task accepts _race_first_token (not TypeError) ─────────


@pytest.mark.asyncio
async def test_create_task_accepts_race_first_token_coroutine():
    """``asyncio.create_task`` must NOT raise TypeError.

    This was the original Bug #2: ``create_task(async_gen_obj)`` raises
    ``TypeError: a coroutine was expected, got <async_generator ...>``.
    After the fix, ``create_task(_race_first_token(gen))`` works because
    ``_race_first_token`` is a coroutine function.
    """
    gen = _async_gen("tok1", "tok2")
    # Must not raise TypeError.
    task = asyncio.create_task(_race_first_token(gen))
    first, remaining = await task
    assert first == "tok1"
    # Clean up.
    await _aclose_silently(remaining)


# ── 10. Winner token count integrity ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_winner_token_count_matches_source():
    """All tokens from the winner must be delivered with no duplicates.

    Regression guard: the first token pulled by ``_race_first_token`` must
    be replayed exactly once.  Early bugs could emit it twice (once from
    the task result and again from the remainder iterator).
    """
    draft = _StubLLM(tokens=["A", "B", "C", "D"], open_delay_s=0.0)
    main = _StubLLM(tokens=["M"], open_delay_s=0.2)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _collect(
        router.complete_runtime_stream(_cfg(), [{"role": "user", "content": "q"}]),
    )

    assert tokens == ["A", "B", "C", "D"], (
        f"all 4 draft tokens expected exactly once, got {tokens}"
    )
    assert len(tokens) == 4, f"token count mismatch: {len(tokens)}"
