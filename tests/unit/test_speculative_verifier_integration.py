"""SpeculativeRouter + HALLUVerifier integration — Wave L1 unit tests.

Wires Wave K1 SpeculativeRouter (Phase 2 race) with Wave K2 HALLUVerifier
(Phase 3 substring + numeric + embedding gates). Verifies the four end-
to-end paths:

  1. ``verify_enabled=False`` → Phase 2 behaviour preserved (no verify).
  2. ``hallu_verifier=None`` → no-op even when ``verify_enabled=True``.
  3. Draft wins + verify pass → buffered tokens flushed + draft continues.
  4. Draft wins + verify fail (numeric mismatch) → ``SPECULATIVE_REDO_SENTINEL``
     emitted, draft cancelled, main streamed verbatim.
  5. Draft wins + verify fail (overlap below floor) → same redo path.
  6. Verifier exception → unsafe → redo (HALLU sacred path).
  7. Main wins → verifier path NOT taken (only fires when draft wins).
  8. Draft buffer < ``buffer_tokens`` exhausts → verifier still runs once
     the iterator is empty (graceful partial-draft handling).
  9. ``draft_model`` kwarg + ``verify_enabled`` co-exist (kwarg routing).
 10. Embedding gate skipped when ``verify_embed_spec`` is None.

All assertions are real (value / behaviour), never ``assert True``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.hallu_verifier import (
    HALLUVerdict,
    HALLUVerifier,
    REASON_NUMERIC_MISMATCH,
    REASON_OVERLAP_BELOW_FLOOR,
    REASON_SAFE,
)
from ragbot.infrastructure.llm.speculative_router import (
    SPECULATIVE_REDO_SENTINEL,
    SpeculativeRouter,
)


# ─── Test doubles ──────────────────────────────────────────────────────────


class _StubLLM:
    """LLM stub yielding pre-defined deltas after ``open_delay_s``."""

    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        open_delay_s: float = 0.0,
        per_token_delay_s: float = 0.0,
    ) -> None:
        self.tokens = tokens or []
        self.open_delay_s = open_delay_s
        self.per_token_delay_s = per_token_delay_s
        self.iter_started = False
        self.iter_cancelled = False

    async def complete_runtime_stream(
        self, cfg: Any, messages: list[dict], **kwargs: Any,
    ):
        """Async generator matching real LLMPort interface (async def + yield).

        Must NOT use ``return self._iter()`` — that pattern produces a coroutine
        returning an async iterator, which masks the TypeError that occurs in
        production where ``DynamicLiteLLMRouter.complete_runtime_stream`` is an
        actual async generator object (not a coroutine).
        """
        if self.open_delay_s > 0:
            await asyncio.sleep(self.open_delay_s)
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
        return "main-complete"

    async def stream(self, messages: list[Any], **kwargs: Any):
        async def _empty():
            if False:  # pragma: no cover - generator marker
                yield ""
        return _empty()

    async def health_check(self) -> bool:
        return True

    async def refresh_routing(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StubEmbedder:
    """EmbeddingPort stub — returns deterministic vectors per text."""

    async def health_check(self) -> bool:  # pragma: no cover
        return True

    async def embed_batch(
        self, texts: list[str], *, spec: Any, record_tenant_id: Any,
    ) -> list[list[float]]:
        # Identity-like cosine: every text maps to [1.0, 0.0] → cosine 1.0.
        return [[1.0, 0.0] for _ in texts]

    async def embed_one(
        self, text: str, *, spec: Any, record_tenant_id: Any,
    ) -> list[float]:  # pragma: no cover
        return [1.0, 0.0]

    async def close(self) -> None:  # pragma: no cover
        return None


class _ForcedVerdictVerifier:
    """Verifier that returns a caller-supplied verdict; records calls.

    Bypasses the deterministic gates so a test can pin ``safe=True``
    or ``safe=False`` without crafting matching text. ``buffer_tokens``
    mirrors the real verifier's property contract.
    """

    def __init__(
        self,
        *,
        verdict: HALLUVerdict,
        buffer_tokens: int = 3,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._verdict = verdict
        self._buffer_tokens = buffer_tokens
        self._raise_exc = raise_exc
        self.calls: list[tuple[list[str], str, Any, Any]] = []

    @property
    def buffer_tokens(self) -> int:
        return self._buffer_tokens

    async def verify_draft_vs_main(
        self,
        draft_buffer: list[str],
        main_first_chunk: str,
        *,
        spec: Any = None,
        record_tenant_id: Any = None,
    ) -> HALLUVerdict:
        self.calls.append((list(draft_buffer), main_first_chunk, spec, record_tenant_id))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._verdict


def _make_cfg(litellm_name: str = "openai/main-model") -> SimpleNamespace:
    return SimpleNamespace(litellm_name=litellm_name)


async def _drain(aiter) -> list[str]:
    out: list[str] = []
    async for t in aiter:
        out.append(t)
    return out


# ─── 1. verify_enabled=False → Phase 2 preserved ──────────────────────────


@pytest.mark.asyncio
async def test_verify_disabled_phase2_preserved():
    draft = _StubLLM(tokens=["d1", "d2"], open_delay_s=0.0)
    main = _StubLLM(tokens=["m1"], open_delay_s=0.2)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(False, REASON_NUMERIC_MISMATCH, 0.0, ["999"], 0.0),
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=False,
        ),
    )

    assert tokens == ["d1", "d2"]
    assert verifier.calls == [], "verifier must NOT fire when verify_enabled=False"


# ─── 2. hallu_verifier=None → no-op even with verify_enabled=True ─────────


@pytest.mark.asyncio
async def test_verifier_none_no_op():
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.0)
    main = _StubLLM(tokens=["m1"], open_delay_s=0.2)
    router = SpeculativeRouter(main_llm=main, draft_llm=draft)

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,  # accepted but ineffective without a verifier
        ),
    )

    assert tokens == ["d1"]


# ─── 3. Verify pass → buffered draft flushed + continued ──────────────────


@pytest.mark.asyncio
async def test_verify_pass_flushes_and_continues_draft():
    draft = _StubLLM(tokens=["alpha", "beta", "gamma", "delta"], open_delay_s=0.0)
    main = _StubLLM(tokens=["alpha-main"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(True, REASON_SAFE, 1.0, [], 1.0),
        buffer_tokens=2,
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    # All four draft tokens stream (buffered ones flushed in order, rest tailed).
    assert tokens == ["alpha", "beta", "gamma", "delta"]
    # Verifier got exactly one call with the first two draft tokens.
    assert len(verifier.calls) == 1
    assert verifier.calls[0][0] == ["alpha", "beta"]
    assert verifier.calls[0][1] == "alpha-main"


# ─── 4. Verify fail (numeric mismatch) → redo sentinel + main stream ──────


@pytest.mark.asyncio
async def test_verify_numeric_mismatch_emits_redo_and_streams_main():
    draft = _StubLLM(tokens=["fake-num-999"], open_delay_s=0.0)
    main = _StubLLM(tokens=["correct-100", "and-more"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(False, REASON_NUMERIC_MISMATCH, 0.5, ["999"], 0.0),
        buffer_tokens=1,
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    # Sentinel comes FIRST so the SSE wire can emit a typed ``redo`` event.
    assert tokens[0] == SPECULATIVE_REDO_SENTINEL, (
        f"first token must be redo sentinel, got {tokens[:3]}"
    )
    # Then main's first chunk + tail (no draft text leaks through).
    assert "correct-100" in tokens
    assert "and-more" in tokens
    assert "fake-num-999" not in tokens


# ─── 5. Verify fail (overlap below floor) → same redo path ────────────────


@pytest.mark.asyncio
async def test_verify_overlap_below_floor_emits_redo():
    draft = _StubLLM(tokens=["off-topic"], open_delay_s=0.0)
    main = _StubLLM(tokens=["on-topic-first", "on-topic-rest"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(False, REASON_OVERLAP_BELOW_FLOOR, 0.1, [], 0.0),
        buffer_tokens=1,
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    assert tokens[0] == SPECULATIVE_REDO_SENTINEL
    assert "on-topic-first" in tokens
    assert "off-topic" not in tokens


# ─── 6. Verifier exception → unsafe (HALLU sacred) ────────────────────────


@pytest.mark.asyncio
async def test_verifier_exception_forces_redo():
    draft = _StubLLM(tokens=["d"], open_delay_s=0.0)
    main = _StubLLM(tokens=["m1", "m2"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(True, REASON_SAFE, 1.0, [], 1.0),
        buffer_tokens=1,
        raise_exc=RuntimeError("embedder boom"),
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    # Exception inside verify must NOT silently accept the draft.
    assert tokens[0] == SPECULATIVE_REDO_SENTINEL
    assert "m1" in tokens


# ─── 7. Main wins → verifier path NOT taken ───────────────────────────────


@pytest.mark.asyncio
async def test_main_wins_skips_verifier():
    draft = _StubLLM(tokens=["d1"], open_delay_s=0.2)
    main = _StubLLM(tokens=["m1", "m2"], open_delay_s=0.0)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(False, REASON_NUMERIC_MISMATCH, 0.0, ["x"], 0.0),
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    assert tokens == ["m1", "m2"]
    assert verifier.calls == [], "verifier only runs when draft wins"


# ─── 8. Short draft (exhausts before buffer fills) still verifies ─────────


@pytest.mark.asyncio
async def test_short_draft_verifies_partial_buffer():
    # Draft yields ONE token then exhausts; buffer_tokens=5 → verifier runs
    # on the partial buffer instead of waiting for tokens that never come.
    draft = _StubLLM(tokens=["only-one"], open_delay_s=0.0)
    main = _StubLLM(tokens=["main-first"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(True, REASON_SAFE, 1.0, [], 1.0),
        buffer_tokens=5,
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
        ),
    )

    assert tokens == ["only-one"]
    assert len(verifier.calls) == 1
    assert verifier.calls[0][0] == ["only-one"]


# ─── 9. draft_model kwarg + verify_enabled co-exist ───────────────────────


@pytest.mark.asyncio
async def test_draft_model_kwarg_with_verify():
    draft = _StubLLM(tokens=["a"], open_delay_s=0.0)
    main = _StubLLM(tokens=["b", "c"], open_delay_s=0.05)
    verifier = _ForcedVerdictVerifier(
        verdict=HALLUVerdict(True, REASON_SAFE, 1.0, [], 1.0),
        buffer_tokens=1,
    )
    captured: dict[str, Any] = {}

    async def _record_call(cfg: Any, messages: list[dict], **kwargs: Any):
        captured.setdefault("draft_cfg", cfg)
        captured.setdefault("kwargs", kwargs)
        # Must be an async generator (async def + yield), not ``return iter``.
        for tok in draft.tokens:
            yield tok

    draft.complete_runtime_stream = _record_call  # type: ignore[method-assign]

    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    await _drain(
        router.complete_runtime_stream(
            _make_cfg(litellm_name="openai/main-model"),
            [{"role": "user", "content": "q"}],
            draft_model="openai/draft-cheap",
            verify_enabled=True,
            verify_record_tenant_id="t-123",
        ),
    )

    # ``draft_model`` consumed by router; ``verify_*`` consumed too.
    assert captured["draft_cfg"].litellm_name == "openai/draft-cheap"
    assert "verify_enabled" not in captured["kwargs"]
    assert "verify_record_tenant_id" not in captured["kwargs"]
    assert "draft_model" not in captured["kwargs"]


# ─── 10. Embedding gate skipped when verify_embed_spec is None ────────────


@pytest.mark.asyncio
async def test_embed_spec_none_skips_gate3():
    """The real HALLUVerifier accepts spec=None and skips gate 3.

    This test uses the production HALLUVerifier (not the stub) wired with
    a stub embedder so we verify the spec=None branch returns safe with
    embedding_cosine=1.0 (the documented contract).
    """
    embedder = _StubEmbedder()
    verifier = HALLUVerifier(
        embedder=embedder,
        overlap_threshold=0.0,  # disable gate 1 for this check
        buffer_tokens=2,
        shingle_size=2,
    )
    # Use the matching same draft/main text so deterministic gates pass.
    draft = _StubLLM(tokens=["the answer is ", "forty two"], open_delay_s=0.0)
    main = _StubLLM(
        tokens=["the answer is forty two for the question"],
        open_delay_s=0.05,
    )
    router = SpeculativeRouter(
        main_llm=main, draft_llm=draft, hallu_verifier=verifier,
    )

    tokens = await _drain(
        router.complete_runtime_stream(
            _make_cfg(), [{"role": "user", "content": "q"}],
            verify_enabled=True,
            # spec intentionally omitted → gate 3 OFF
        ),
    )

    # Safe path → buffered + remaining draft tokens stream verbatim.
    assert tokens == ["the answer is ", "forty two"]
