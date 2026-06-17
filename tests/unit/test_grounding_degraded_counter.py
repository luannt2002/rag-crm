"""Grounding judge degrade is observable, not silent (P2-E 🐛-3).

When the judge times out / errors, or returns no checkable claims, the
answer is passed through UNVERIFIED. Both paths returned ``None`` silently,
so a dashboard could not tell "judge PASSED" from "judge DIED" — the HALLU
observability net could be off without anyone knowing. The degrade now
increments ``grounding_degraded_total{reason}`` (sibling of the existing
``grounding_fail_total`` counter).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ragbot.infrastructure.guardrails.local_guardrail import OutputGuardrail
from ragbot.infrastructure.observability.metrics import grounding_degraded_total

_ANSWER = "Diện tích Việt Nam là 331 nghìn km vuông."
_CHUNKS = [{"content": "Việt Nam rộng 331 nghìn km vuông."}]


def _count(reason: str) -> float:
    return grounding_degraded_total.labels(reason=reason)._value.get()


def test_judge_error_increments_degraded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count("error")
    # An error that propagates OUT of the judge (not an internally-handled
    # timeout that degrades to empty) → the except-degrade path.
    monkeypatch.setattr(
        OutputGuardrail, "_run_text_parse_judge",
        AsyncMock(side_effect=RuntimeError("judge crashed")),
    )
    out = asyncio.run(
        OutputGuardrail.llm_grounding_check(
            _ANSWER, _CHUNKS, AsyncMock(), use_structured=False,
        ),
    )
    assert out is None  # degrade returns None (answer passes unverified)
    assert _count("error") == before + 1, (
        "a judge crash must bump grounding_degraded_total{reason=error}"
    )


def test_judge_empty_increments_degraded_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count("empty")
    # Judge ran but yielded zero checkable claims → empty degrade path.
    monkeypatch.setattr(
        OutputGuardrail, "_run_text_parse_judge",
        AsyncMock(return_value=(0, 0)),
    )
    out = asyncio.run(
        OutputGuardrail.llm_grounding_check(
            _ANSWER, _CHUNKS, AsyncMock(), use_structured=False,
        ),
    )
    assert out is None
    assert _count("empty") == before + 1, (
        "an empty judge result must bump grounding_degraded_total{reason=empty}"
    )
