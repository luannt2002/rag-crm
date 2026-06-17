"""RAGAS metric adapter — Port-style scaffold for offline eval.

Granular RAG quality measurement (faithfulness, answer_relevancy,
context_precision, context_recall) on top of binary HALLU + answer-rate
labels. Surfaces *where* a turn weakens (e.g. high faithfulness but low
context_precision = retrieval-side fix; the inverse = generation-side fix).

This module is a **dev-tool scaffold**, NOT a chat hot-path service. The
adapter exposes a deterministic stub today; admin wires the real `ragas`
package later via Strategy + Registry without touching call sites. No live
LLM call is made here — the stub keeps tests and CI hermetic.

Anti-patterns avoided:
- No application-side text/template injection into the LLM prompt.
- No override of LLM answer; metrics are read-only observations.
- Empty contexts deterministically score faithfulness = 0 (cannot ground a
  claim against nothing) — surfaces the missing-retrieval failure mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from ragbot.shared.constants import DEFAULT_RAGAS_STUB_SCORE

METRIC_FAITHFULNESS: Final[str] = "faithfulness"
METRIC_ANSWER_RELEVANCY: Final[str] = "answer_relevancy"
METRIC_CONTEXT_PRECISION: Final[str] = "context_precision"
METRIC_CONTEXT_RECALL: Final[str] = "context_recall"

EXPECTED_METRIC_KEYS: Final[tuple[str, ...]] = (
    METRIC_FAITHFULNESS,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
)


@runtime_checkable
class RagasMetricPort(Protocol):
    """Strategy port for RAGAS-style eval providers.

    Concrete strategies (stub today, real `ragas` provider tomorrow) MUST
    return a dict containing exactly ``EXPECTED_METRIC_KEYS`` with each
    value clamped to ``[0.0, 1.0]``. Empty contexts MUST yield a
    faithfulness of 0.0 — a claim has nothing to ground against.
    """

    def score(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> dict[str, float]:
        ...


@dataclass(frozen=True)
class RagasMetricAdapter:
    """Deterministic stub adapter (placeholder until real RAGAS wired).

    Returns the same score for every metric; empty contexts collapse the
    faithfulness score to 0.0 to honour the no-context, no-grounding
    invariant. The shape of the output is the contract real implementations
    must preserve.
    """

    stub_score: float = DEFAULT_RAGAS_STUB_SCORE

    def score(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> dict[str, float]:
        if not isinstance(question, str) or not isinstance(answer, str):
            raise TypeError("question and answer must be str")
        if not isinstance(contexts, list):
            raise TypeError("contexts must be a list[str]")

        clamped = max(0.0, min(1.0, float(self.stub_score)))
        faithfulness = clamped if contexts else 0.0
        return {
            METRIC_FAITHFULNESS: faithfulness,
            METRIC_ANSWER_RELEVANCY: clamped,
            METRIC_CONTEXT_PRECISION: clamped,
            METRIC_CONTEXT_RECALL: clamped,
        }


__all__ = [
    "EXPECTED_METRIC_KEYS",
    "METRIC_ANSWER_RELEVANCY",
    "METRIC_CONTEXT_PRECISION",
    "METRIC_CONTEXT_RECALL",
    "METRIC_FAITHFULNESS",
    "RagasMetricAdapter",
    "RagasMetricPort",
]
