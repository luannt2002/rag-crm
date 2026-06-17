"""Unit tests for AdapChunk Layer 7 — FORMULA narrator.

Mock-only (no live LLM). Verifies:

- Happy path: ``LLMFn`` returns narration → ``narrate_formula`` returns
  the stripped narration (whitespace from the model is trimmed).
- ``LLMFn`` raises → passthrough raw LaTeX (graceful degradation; HALLU=0
  sacred — never embed fabricated / empty text in place of real content).
- Empty / whitespace input → empty / whitespace returned unchanged
  WITHOUT invoking the LLM (cost guard — no round-trip for nothing).
- Empty LLM output → passthrough raw LaTeX.
- Caller-supplied ``model`` / ``max_tokens`` / ``batch`` are forwarded to
  the injected ``LLMFn`` verbatim (zero-hardcode + DI contract).
- Default ``max_tokens`` matches ``DEFAULT_FORMULA_NARRATE_MAX_TOKENS``
  (constant pinning — prevents drift).
- Prompt template substitutes the LaTeX without leaking other content
  (domain-neutral / no brand literals).
"""
from __future__ import annotations

from typing import Any

import pytest

from ragbot.application.services.narrate.formula_narrator import (
    LLMFn,
    narrate_formula,
)
from ragbot.shared.constants import (
    DEFAULT_FORMULA_NARRATE_MAX_TOKENS,
    DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE,
)


class _RecordingLLM:
    """Mock LLM that records every call and returns a scripted response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        batch: bool = False,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "max_tokens": max_tokens,
                "batch": batch,
            }
        )
        return self._response


class _RaisingLLM:
    """Mock LLM that always raises — exercises the fallback path."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.call_count = 0

    async def __call__(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        batch: bool = False,
    ) -> str:
        self.call_count += 1
        raise self._exc


_TEST_MODEL = "test-model-id"


@pytest.mark.asyncio
async def test_narrate_formula_happy_path_returns_stripped_narration() -> None:
    llm: LLMFn = _RecordingLLM(
        response="  Einstein mass-energy equivalence: energy equals mass times c squared.\n"
    )
    latex = "$$E = mc^2$$"

    out = await narrate_formula(latex, llm_fn=llm, model=_TEST_MODEL)

    assert out == (
        "Einstein mass-energy equivalence: energy equals mass times c squared."
    )
    # LLM was invoked exactly once with the rendered prompt.
    assert len(llm.calls) == 1  # type: ignore[attr-defined]
    call = llm.calls[0]  # type: ignore[attr-defined]
    assert latex in call["prompt"]
    assert call["model"] == _TEST_MODEL
    # Default max_tokens equals the FORMULA-specific constant (pinning).
    assert call["max_tokens"] == DEFAULT_FORMULA_NARRATE_MAX_TOKENS
    # Default batch=True (Anthropic Batch -50% discount per Phan 16.7).
    assert call["batch"] is True


@pytest.mark.asyncio
async def test_narrate_formula_llm_raises_passthrough_latex() -> None:
    llm = _RaisingLLM(RuntimeError("upstream 503"))
    latex = "$$\\int_0^1 x^2 \\, dx = \\frac{1}{3}$$"

    out = await narrate_formula(latex, llm_fn=llm, model=_TEST_MODEL)

    # Graceful degradation — raw LaTeX returned unchanged.
    assert out == latex
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_narrate_formula_empty_input_returns_empty_no_llm_call() -> None:
    llm = _RecordingLLM(response="should not be used")

    out_empty = await narrate_formula("", llm_fn=llm, model=_TEST_MODEL)
    out_whitespace = await narrate_formula("   \n\t ", llm_fn=llm, model=_TEST_MODEL)

    # Inputs are returned unchanged — caller short-circuit, no LLM round-trip.
    assert out_empty == ""
    assert out_whitespace == "   \n\t "
    # Cost guard: zero LLM calls for empty / whitespace input.
    assert llm.calls == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_narrate_formula_empty_llm_output_passthrough_latex() -> None:
    llm = _RecordingLLM(response="   \n  ")  # whitespace-only
    latex = "$$a^2 + b^2 = c^2$$"

    out = await narrate_formula(latex, llm_fn=llm, model=_TEST_MODEL)

    # Empty narration -> fallback to raw LaTeX (HALLU=0: never embed blank).
    assert out == latex
    assert len(llm.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_narrate_formula_forwards_custom_max_tokens_and_batch_flag() -> None:
    llm = _RecordingLLM(response="Pythagorean theorem.")
    custom_max = DEFAULT_FORMULA_NARRATE_MAX_TOKENS + 25

    out = await narrate_formula(
        "$$a^2 + b^2 = c^2$$",
        llm_fn=llm,
        model="custom-model",
        max_tokens=custom_max,
        batch=False,
    )

    assert out == "Pythagorean theorem."
    call = llm.calls[0]  # type: ignore[attr-defined]
    assert call["model"] == "custom-model"
    assert call["max_tokens"] == custom_max
    assert call["batch"] is False


@pytest.mark.asyncio
async def test_narrate_formula_prompt_substitutes_latex_verbatim() -> None:
    llm = _RecordingLLM(response="ok")
    latex = "$$\\nabla \\cdot \\mathbf{E} = \\rho / \\epsilon_0$$"

    await narrate_formula(latex, llm_fn=llm, model=_TEST_MODEL)

    sent_prompt = llm.calls[0]["prompt"]  # type: ignore[attr-defined]
    # Prompt template was rendered with the exact LaTeX (no escaping).
    expected = DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE.format(latex=latex)
    assert sent_prompt == expected


def test_default_formula_narrate_max_tokens_is_tight() -> None:
    # Constant pin: FORMULA narrations are short — cap is 100 tokens
    # (50% Batch discount makes this ~$0.00005 per formula per Phan 16.7).
    # Test guards against accidental bump that would balloon ingest cost.
    assert DEFAULT_FORMULA_NARRATE_MAX_TOKENS == 100


def test_default_formula_narrate_prompt_template_has_latex_slot() -> None:
    # Template MUST expose ``{latex}`` substitution; missing slot would
    # mean the LLM never sees the formula. Defence vs silent template
    # regression.
    assert "{latex}" in DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE
    # Domain-neutral: no brand / industry literal sneaked into the
    # template. Generic mathematical-formula instruction only.
    lowered = DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE.lower()
    for forbidden in ("brand", "company", "product"):
        assert forbidden not in lowered
