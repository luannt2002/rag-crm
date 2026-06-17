"""BatchCragGrader — single structured-output LLM call grades every chunk.

Replaces the legacy per-chunk loop (one LLM call per chunk, bounded by
:class:`asyncio.Semaphore`) with a **single** structured-output call that
emits ``[{chunk_id, grade}, ...]`` for every candidate chunk in one round
trip. For ``top_k=50`` factoid bots this drops grade-stage cost ~10–50×
and trims grade-stage latency by ~30% (master DeepDive Finding #19).

The strategy is intentionally **stateless** — it accepts a
``structured_llm_caller`` callable in its constructor and forwards the
prompt + schema to whatever LLM transport the caller chose. The
:mod:`ragbot.orchestration.query_graph` wiring (Phase 2) supplies a
callable that wraps the existing ``_invoke_structured_llm_node`` so the
audit / invocation logging path is preserved.

Graceful degradation: if the LLM returns an unparseable response or
times out, every chunk is scored ``1.0`` and the orchestrator's
threshold gate decides the rest. This mirrors the inline grader's
"treat all ambiguous" fallback (query_graph.py:2826).

Cap: at most ``max_chunks`` chunks are batched in one call. Above that,
the strategy slices the input into bounded windows so a single rogue
``top_k=500`` request cannot exceed the LLM's context budget. Default
``max_chunks`` lives in :mod:`ragbot.shared.constants` and is
overridable from ``system_config.crag_batch_grader_max_chunks``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import GradeBatchOutput
from ragbot.shared.constants import DEFAULT_CRAG_BATCH_GRADER_MAX_CHUNKS

logger = structlog.get_logger(__name__)


# Mapping from LLM verdict words to numeric scores. The schema constrains
# the LLM to {"yes", "no", "partial"}; anything else collapses to the
# graceful-degradation default below.
_GRADE_TO_SCORE: dict[str, float] = {
    "yes": 1.0,
    "partial": 0.5,
    "no": 0.0,
}
_FALLBACK_SCORE: float = 1.0  # keep chunk, defer to downstream threshold


# Type alias for the caller-supplied LLM invocation. The orchestrator
# supplies a wrapper around its existing ``_invoke_structured_llm_node``;
# tests supply an AsyncMock. Returning ``(parsed, ctx)`` mirrors the
# orchestrator's signature so the wrapper is a thin pass-through.
StructuredLlmCaller = Callable[..., Awaitable[tuple[Any, Any]]]


class BatchCragGrader:
    """Single-call CRAG grader using structured-output JSON schema."""

    def __init__(
        self,
        *,
        structured_llm_caller: StructuredLlmCaller,
        system_prompt: str,
        max_chunks: int = DEFAULT_CRAG_BATCH_GRADER_MAX_CHUNKS,
        purpose: str = "grading",
    ) -> None:
        """@param structured_llm_caller: async callable returning
            ``(parsed_GradeBatchOutput_or_None, usage_ctx)``. Signature
            matches :func:`query_graph._invoke_structured_llm_node` so
            the orchestrator can pass it directly.
        @param system_prompt: grader system prompt sourced from the bot's
            language pack — application MUST NOT inject any extra
            instruction text (Quality Gate #10).
        @param max_chunks: ceiling on chunks per LLM call; oversize input
            is split into ``ceil(N/max_chunks)`` sequential batches.
            Default from ``system_config.crag_batch_grader_max_chunks``.
        @param purpose: observability label forwarded to the caller
            (LLM cost ledger groups by purpose).
        """
        if structured_llm_caller is None:
            raise ValueError("structured_llm_caller is required")
        self._call = structured_llm_caller
        self._system_prompt = system_prompt or ""
        # Guard against pathological config values without raising — fall
        # back to the canonical default so a typo in system_config cannot
        # break the grade node at runtime.
        self._max_chunks = (
            max_chunks
            if isinstance(max_chunks, int) and max_chunks > 0
            else DEFAULT_CRAG_BATCH_GRADER_MAX_CHUNKS
        )
        self._purpose = purpose or "grading"

    @staticmethod
    def get_provider_name() -> str:
        return "batch"

    async def grade_batch(
        self,
        *,
        query: str,
        chunks: list[dict],
    ) -> dict[str, float]:
        if not chunks:
            return {}

        # Split into batches to guard context budget on giant top_k.
        scores: dict[str, float] = {}
        for start in range(0, len(chunks), self._max_chunks):
            window = chunks[start : start + self._max_chunks]
            window_scores = await self._grade_single_window(query, window)
            scores.update(window_scores)
        return scores

    async def _grade_single_window(
        self,
        query: str,
        window: list[dict],
    ) -> dict[str, float]:
        """One LLM call grades ``window`` (already <= max_chunks)."""
        # Build the user message — chunk_id tagged so the LLM can echo
        # them in the structured response. Content trimmed to its natural
        # length; the caller's tokenizer is responsible for context-budget
        # checks (delegated, not duplicated here).
        chunk_lines: list[str] = []
        valid_ids: set[str] = set()
        for chunk in window:
            cid = str(chunk.get("chunk_id") or chunk.get("id") or "")
            if not cid:
                continue
            valid_ids.add(cid)
            txt = chunk.get("content") or chunk.get("text") or ""
            chunk_lines.append(f'<chunk id="{cid}">\n{txt}\n</chunk>')

        if not chunk_lines:
            # Nothing identifiable — degrade gracefully without LLM call.
            return {}

        user_msg = (
            f"<query>{query}</query>\n" + "\n".join(chunk_lines)
        )
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            parsed, _ctx = await self._call(
                purpose=self._purpose,
                messages=messages,
                user_prompt=query,
                schema=GradeBatchOutput,
            )
        except Exception as exc:  # noqa: BLE001 — top-level grader entrypoint, MUST degrade
            logger.warning(
                "crag_batch_grader_llm_failed_fallback_all_one",
                error=str(exc),
                error_type=type(exc).__name__,
                n_chunks=len(window),
            )
            return {cid: _FALLBACK_SCORE for cid in valid_ids}

        if parsed is None or not getattr(parsed, "grades", None):
            logger.warning(
                "crag_batch_grader_unparseable_fallback_all_one",
                n_chunks=len(window),
            )
            return {cid: _FALLBACK_SCORE for cid in valid_ids}

        # Build score dict from LLM verdicts; missing entries get fallback
        # so the contract guarantee (every chunk_id present) holds.
        by_id: dict[str, float] = {}
        for item in parsed.grades:
            if not getattr(item, "chunk_id", None):
                continue
            verdict = (item.grade or "").strip().lower()
            by_id[item.chunk_id] = _GRADE_TO_SCORE.get(verdict, _FALLBACK_SCORE)
        for cid in valid_ids:
            if cid not in by_id:
                by_id[cid] = _FALLBACK_SCORE
        return by_id


__all__ = ["BatchCragGrader", "StructuredLlmCaller"]
