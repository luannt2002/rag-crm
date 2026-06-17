"""PerChunkCragGrader — legacy N-call CRAG grader (one LLM call per chunk).

Wraps the historical per-chunk grading loop from
:mod:`ragbot.orchestration.query_graph` (``grade`` node, structured-output
fallback path) as a :class:`CragGraderPort` implementation. Selected when
``system_config.crag_grader_provider == "per_chunk"`` — the **default**
on every existing deployment so flipping the new abstraction layer on
is a no-behaviour-change opt-in.

Concurrency is bounded by :data:`DEFAULT_CRAG_GRADE_CONCURRENCY` so we
do not flood the LLM provider with parallel small calls (the original
inline implementation used :class:`asyncio.Semaphore(5)`). The cap is
configurable via the constructor for tests / per-bot tuning.

Graceful degradation: any single chunk whose LLM call fails or returns
unparsable JSON scores ``1.0`` (kept; defers to threshold gate). The
batch-wide failure mode (zero parsed) returns all ``1.0`` matching the
contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import GradeOutput
from ragbot.shared.constants import DEFAULT_CRAG_GRADE_CONCURRENCY

logger = structlog.get_logger(__name__)


_GRADE_TO_SCORE: dict[str, float] = {
    "yes": 1.0,
    "partial": 0.5,
    "no": 0.0,
}
_FALLBACK_SCORE: float = 1.0


# Same caller signature as BatchCragGrader — schema swaps from
# GradeBatchOutput to GradeOutput per call.
StructuredLlmCaller = Callable[..., Awaitable[tuple[Any, Any]]]


class PerChunkCragGrader:
    """Legacy CRAG grader — one structured LLM call per chunk."""

    def __init__(
        self,
        *,
        structured_llm_caller: StructuredLlmCaller,
        system_prompt: str,
        concurrency: int = DEFAULT_CRAG_GRADE_CONCURRENCY,
        purpose: str = "grading",
    ) -> None:
        if structured_llm_caller is None:
            raise ValueError("structured_llm_caller is required")
        self._call = structured_llm_caller
        self._system_prompt = system_prompt or ""
        self._concurrency = (
            concurrency
            if isinstance(concurrency, int) and concurrency > 0
            else DEFAULT_CRAG_GRADE_CONCURRENCY
        )
        self._purpose = purpose or "grading"

    @staticmethod
    def get_provider_name() -> str:
        return "per_chunk"

    async def grade_batch(
        self,
        *,
        query: str,
        chunks: list[dict],
    ) -> dict[str, float]:
        if not chunks:
            return {}

        sem = asyncio.Semaphore(self._concurrency)

        async def _grade_one(chunk: dict) -> tuple[str, float]:
            cid = str(chunk.get("chunk_id") or chunk.get("id") or "")
            if not cid:
                return "", _FALLBACK_SCORE
            txt = chunk.get("content") or chunk.get("text") or ""
            messages = [
                {"role": "system", "content": self._system_prompt},
                {
                    "role": "user",
                    "content": f"<query>{query}</query>\n<chunk>{txt}</chunk>",
                },
            ]
            async with sem:
                try:
                    parsed, _ctx = await self._call(
                        purpose=self._purpose,
                        messages=messages,
                        user_prompt=query,
                        schema=GradeOutput,
                    )
                except Exception as exc:  # noqa: BLE001 — top-level grader entrypoint, MUST degrade
                    logger.warning(
                        "crag_per_chunk_grader_llm_failed_fallback_one",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        chunk_id=cid,
                    )
                    return cid, _FALLBACK_SCORE
            if parsed is None or not getattr(parsed, "grade", None):
                return cid, _FALLBACK_SCORE
            verdict = (parsed.grade or "").strip().lower()
            return cid, _GRADE_TO_SCORE.get(verdict, _FALLBACK_SCORE)

        # ``asyncio.gather`` preserves input order; semaphore bounds
        # concurrency. ``return_exceptions=False`` — _grade_one swallows
        # per-chunk failures so a single network blip does not nuke the
        # whole grade stage.
        results = await asyncio.gather(*[_grade_one(c) for c in chunks])
        scores: dict[str, float] = {}
        for cid, score in results:
            if cid:
                scores[cid] = score
        return scores


__all__ = ["PerChunkCragGrader"]
