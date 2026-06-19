"""Adaptive Router L1 node — parallel wrapper for 3 pre-retrieval steps.

Extracted from ``query_graph.build_graph``. The three branch coroutines
(``_run_query_complexity``, ``_run_router_select_model``,
``_run_semantic_cache_preflight``) remain builder closures (they capture
di_kwargs) and are threaded in as callable kwargs, bound via
``functools.partial``. ``_pcfg`` is a pure helper imported directly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from ragbot.orchestration.query_graph_helpers import _pcfg
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED

logger = structlog.get_logger(__name__)


async def query_complexity_node(
    state: GraphState,
    *,
    _run_query_complexity: Any,
    _run_router_select_model: Any,
    _run_semantic_cache_preflight: Any,
) -> dict:
    """Adaptive Router L1 — parallel wrapper for 3 pre-retrieval steps.

    Runs ``query_complexity``, ``router_select_model``, and
    ``semantic_cache_check`` in parallel via ``asyncio.gather`` when
    ``pipeline_pre_retrieval_parallel_enabled`` is True (default).
    Falls back to sequential execution when the flag is False so
    byte-identical behaviour is preserved for bots that opt out.

    Exception handling (return_exceptions=True contract):
    - query_complexity exception → fallback ("simple", 0.0)
    - router_select_model exception → skip (telemetry only)
    - semantic_cache_preflight exception → skip (validation only)

    Emit at INFO so the cascade routing chain is observable in the
    production journal.
    """
    parallel_flag = bool(
        _pcfg(
            state,
            "pipeline_pre_retrieval_parallel_enabled",
            DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED,
        )
    )
    if not parallel_flag:
        # Sequential fallback — byte-identical to the pre-optimisation path.
        return await _run_query_complexity(state)

    complexity_result, router_result, sc_result = await asyncio.gather(
        _run_query_complexity(state),
        _run_router_select_model(state),
        _run_semantic_cache_preflight(state),
        return_exceptions=True,
    )

    # Branch A: query complexity — routing depends on this; fallback on exception.
    if isinstance(complexity_result, BaseException):
        logger.warning(
            "pre_retrieval_parallel_complexity_failed",
            error_type=type(complexity_result).__name__,
            record_bot_id=str(state.get("record_bot_id") or ""),
        )
        merged: dict = {"complexity_label": "simple", "complexity_score": 0.0}
    else:
        merged = dict(complexity_result) if isinstance(complexity_result, dict) else {
            "complexity_label": "simple", "complexity_score": 0.0,
        }

    # Branch B: router_select_model — telemetry only; log + skip on exception.
    if isinstance(router_result, BaseException):
        logger.warning(
            "pre_retrieval_parallel_router_failed",
            error_type=type(router_result).__name__,
            record_bot_id=str(state.get("record_bot_id") or ""),
        )

    # Branch C: semantic_cache_preflight — validation only; log + skip on exception.
    if isinstance(sc_result, BaseException):
        logger.warning(
            "pre_retrieval_parallel_sc_preflight_failed",
            error_type=type(sc_result).__name__,
            record_bot_id=str(state.get("record_bot_id") or ""),
        )

    return merged
