"""MMR dedup node — diversity filter over reranked chunks before grade.

Extracted from ``query_graph.build_graph``. The infra-closure ``_audit`` and the
``_pcfg`` config reader are threaded in as kwargs (bound via ``functools.partial``
in the graph builder), matching the established node-extraction pattern.
"""
from __future__ import annotations

from typing import Any

from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_MMR_LAMBDA,
    DEFAULT_MMR_MIN_KEEP,
    DEFAULT_MMR_SIMILARITY_THRESHOLD,
)
from ragbot.shared.mmr import mmr_filter


async def mmr_dedup(
    state: GraphState,
    *,
    _pcfg: Any,
    _audit: Any,
) -> dict:
    """MMR dedup over reranked chunks before grade."""
    async with state["step_tracker"].step("mmr_dedup") as mmr_ctx:
        chunks = state.get("reranked_chunks", [])
        # 260525 Bug #10 — per-intent MMR similarity threshold.
        # aggregation queries collapse if row-shape CSV chunks (same
        # column structure, different data values) get dedup'd as
        # duplicates. Loosen the threshold for aggregation so distinct
        # data rows survive.
        _intent_for_mmr = state.get("intent") or ""
        _thresh_by_intent = _pcfg(state, "mmr_similarity_threshold_by_intent", None)
        _intent_override_mmr = False
        if isinstance(_thresh_by_intent, dict) and _intent_for_mmr in _thresh_by_intent:
            try:
                mmr_thresh = float(_thresh_by_intent[_intent_for_mmr])
                _intent_override_mmr = True
            except (TypeError, ValueError):
                mmr_thresh = float(
                    _pcfg(state, "mmr_similarity_threshold", DEFAULT_MMR_SIMILARITY_THRESHOLD)
                )
        else:
            mmr_thresh = float(
                _pcfg(state, "mmr_similarity_threshold", DEFAULT_MMR_SIMILARITY_THRESHOLD)
            )
        mmr_lambda = float(_pcfg(state, "mmr_lambda", DEFAULT_MMR_LAMBDA))
        _min_keep = int(_pcfg(state, "mmr_min_keep", DEFAULT_MMR_MIN_KEEP))
        filtered = mmr_filter(
            chunks,
            lambda_param=mmr_lambda,
            similarity_threshold=mmr_thresh,
            strip_embedding=True,
            min_keep=_min_keep,
        )
        mmr_ctx.set_metadata(
            before=len(chunks),
            after=len(filtered),
            similarity_threshold=mmr_thresh,
            intent_override=_intent_override_mmr,
            intent=_intent_for_mmr,
        )
        await _audit(
            state,
            "mmr_dedup",
            {
                "before": len(chunks),
                "after": len(filtered),
                "lambda": mmr_lambda,
                "similarity_threshold": mmr_thresh,
                "intent_override": _intent_override_mmr,
            },
        )
        return {"reranked_chunks": filtered}
