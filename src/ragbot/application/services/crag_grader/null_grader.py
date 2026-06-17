"""NullCragGrader — Null Object pattern for CRAG grading.

Returned by :func:`build_crag_grader` when ``system_config.crag_grader_provider``
is ``"null"`` (default OFF if operator explicitly disables CRAG) or unset.
Assigns the maximum score (1.0) to every chunk so the downstream
threshold gate keeps every chunk — effectively *disabling* CRAG without
forcing callers to add ``if grader is not None`` guards everywhere.

This is the safe rollback target: flipping ``crag_grader_provider`` to
``"null"`` via ``system_config`` UPDATE removes the LLM grade call from
the hot path entirely. Combined with ``crag_use_batch_grade = false``
it pins the pipeline to "reranker score + threshold" only (cheapest
mode, used during incident response when LLM grade is flapping).
"""

from __future__ import annotations


class NullCragGrader:
    """No-op grader — every chunk scored ``1.0``.

    The contract: returned dict carries every input chunk's id with
    score ``1.0``. Empty input → empty output (no LLM invocation).
    """

    def __init__(self, **_: object) -> None:
        # Accept (and ignore) any kwargs so the registry can build
        # NullCragGrader with the same signature as a real strategy.
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    async def grade_batch(
        self,
        *,
        query: str,  # noqa: ARG002 — interface contract, ignored by Null
        chunks: list[dict],
    ) -> dict[str, float]:
        if not chunks:
            return {}
        out: dict[str, float] = {}
        for chunk in chunks:
            cid = str(chunk.get("chunk_id") or chunk.get("id") or "")
            if cid:
                out[cid] = 1.0
        return out


__all__ = ["NullCragGrader"]
