"""CRAG Grader Protocol — Strategy Pattern for chunk-relevance grading.

The CRAG (Corrective RAG) grade node decides which retrieved chunks reach
the generator. Today the grading lives inline in
:mod:`ragbot.orchestration.query_graph` (``grade`` node) and runs one
structured LLM call **per chunk** with a bounded :class:`asyncio.Semaphore`.
For ``top_k=50`` bots this is up to 50 LLM calls per turn — both costly
and slow (master DeepDive Finding #19).

This port abstracts the grading step so the orchestrator can swap the
strategy via ``system_config.crag_grader_provider`` (``"per_chunk"`` /
``"batch"`` / ``"null"``):

* ``per_chunk`` — legacy N-call behaviour (default; backward-compatible).
* ``batch``     — single structured LLM call grades every chunk at once,
                  ~10×–50× cheaper for factoid/comparison intents.
* ``null``      — Null Object; assigns max score to every chunk so the
                  pipeline behaves as if grading is disabled (tests, ops
                  emergencies).

The contract is intentionally minimal — return a ``{chunk_id: score}``
mapping where scores are floats in ``[0.0, 1.0]``. The orchestrator
remains in charge of thresholding, audit emission, retry, and fallback
logic so the change is surgical (Quality Gate rule: keep the existing
fallback / threshold logic untouched).

Vertical-agnostic: strategies must NOT bake in any domain / brand /
industry literal. Prompt template + system message come from the
caller's language pack (``language_pack_service.prompt_grader``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CragGraderPort(Protocol):
    """Strategy contract for CRAG chunk-relevance grading.

    Implementations: :class:`NullCragGrader` (default OFF),
    :class:`PerChunkCragGrader` (legacy N-call),
    :class:`BatchCragGrader` (single batched call).

    Contract:
      - Empty ``chunks`` MUST return ``{}`` without invoking the LLM.
      - Returned dict MUST contain every input chunk's ``chunk_id`` (or
        ``id``) key. Missing scores would force callers to special-case
        partial output; instead strategies emit a fallback ``1.0`` when
        the LLM response cannot be parsed (graceful degradation — keep
        the chunk, defer the decision to downstream threshold gate).
      - Scores MUST be floats in ``[0.0, 1.0]``. The orchestrator maps
        ``>= relevance_threshold`` -> ``relevant``, ``> 0`` -> ``ambiguous``,
        ``0`` -> ``irrelevant`` (caller policy, not strategy concern).
      - MUST NOT raise on LLM failure — return all-``1.0`` so the
        pipeline degrades to "trust the reranker" rather than refuse.
      - MUST be vertical-agnostic — no industry / brand literals.
    """

    async def grade_batch(
        self,
        *,
        query: str,
        chunks: list[dict],
    ) -> dict[str, float]:
        """Score each chunk's relevance to ``query``.

        @param query: rewritten / condensed user query (already passed
            through ``understand`` / ``rewrite`` nodes upstream).
        @param chunks: list of chunk dicts. Each MUST carry either a
            ``chunk_id`` or ``id`` field (string) plus a ``content`` or
            ``text`` field.
        @return: mapping of ``chunk_id`` → relevance score in
            ``[0.0, 1.0]``. Empty mapping iff ``chunks`` is empty.
        """
        ...

    def get_provider_name(self) -> str:
        """Identifier for observability (e.g. ``"null"`` / ``"batch"``)."""
        ...


__all__ = ["CragGraderPort"]
