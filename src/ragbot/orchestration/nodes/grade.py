"""CRAG-lite grade node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no logic / grade verdict / prompt / state key / ordering /
log-event change). The inner ``_grade_one_chunk`` per-chunk gather closure
stays nested (it captures node-locals ``_grade_sem`` / ``query_text``), exactly
as before.

Pure CRAG vocabulary + filters (``CRAG_GRADE_*``, ``_remap_grade_for_intent``,
``_is_retrieval_adequate``) come from ``retrieval_filter`` (no cycle). Shared
helper closures (``_audit``, ``_invoke_structured_llm_node``, ``_so_usage``)
and the query_graph-local helpers (``_pcfg``, ``_lang``) are threaded in as
kwargs.
"""

from __future__ import annotations

import asyncio
import json as _json_mod
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import GradeBatchOutput, GradeOutput
from ragbot.orchestration.retrieval_filter import (
    CRAG_GRADE_AMBIGUOUS,
    CRAG_GRADE_IRRELEVANT,
    CRAG_GRADE_RELEVANT,
    _is_retrieval_adequate,
    _remap_grade_for_intent,
)
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_CRAG_FALLBACK_COUNT,
    DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO,
    DEFAULT_CRAG_GRADE_CONCURRENCY,
    DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
    DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
    DEFAULT_CRAG_MAX_GRADE_RETRIES,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
    DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_GRADE_TIMEOUT_S,
    DEFAULT_GRADE_USE_BATCH,
    DEFAULT_GRADE_USE_STRUCTURED_OUTPUT,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
    INTENT_OUT_OF_SCOPE,
)
from ragbot.shared.errors import InvariantViolation

logger = structlog.get_logger(__name__)


async def grade(
    state: GraphState,
    *,
    llm: Any = None,
    model_resolver: Any = None,
    _audit: Any,
    _invoke_structured_llm_node: Any,
    _so_usage: Any,
    _pcfg: Any,
    _lang: Any,
) -> dict:
    """CRAG-lite: LLM-based relevance grading on retrieved/reranked chunks."""
    async with state["step_tracker"].step("grade") as grade_ctx:
        # Tracks which CRAG path actually executed (batch vs per-chunk fallback).
        path_used = "batch"
        # Cap total CRAG + reflect iterations to prevent infinite loops.
        _total_iters = state.get("_total_graph_iterations", 0) + 1
        _max_iters = int(_pcfg(state, "max_total_graph_iterations", DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS))
        if _total_iters > _max_iters:
            logger.warning("crag_iteration_cap", iterations=_total_iters)
            return {
                "graded_chunks": state.get("reranked_chunks", [])[:2],
                "retrieval_adequate": True,
                "_total_graph_iterations": _total_iters,
            }

        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=grading")
        inp = state.get("reranked_chunks", [])
        if not inp:
            return {"graded_chunks": [], "retrieval_adequate": False, "answer_reason": "No chunks found for grading"}

        # ── Stats/aggregation route bypass ────────────────────────────
        # Chunks from the stats_index route are deterministic SQL results
        # (price-range / superlative), already precise. Running the fuzzy
        # CRAG relevance grader on them wrongly marks them "ambiguous" and
        # drops them → the bot then refuses a numeric question the SQL had
        # already answered ("dưới 500k", "rẻ nhất"). Pass them through; the
        # downstream grounding_check still enforces HALLU=0.
        _retrieve_mode = str(state.get("retrieve_mode") or "")
        if _retrieve_mode.startswith("stats"):
            grade_ctx.set_metadata(
                grade_path="skip_stats_route", retrieve_mode=_retrieve_mode,
                n_chunks=len(inp),
            )
            return {
                "graded_chunks": inp,
                "retrieval_adequate": True,
                "_total_graph_iterations": _total_iters,
                "crag_skip_retry": True,
                "crag_skip_reason": f"stats_route={_retrieve_mode}",
            }

        # ── Smart-skip CRAG (S1 Pipeline-Opt) ─────────────────────────
        # When pass-1 top score clears ``crag_skip_retry_above_score``
        # we skip the grade-LLM call AND the rewrite_retry loop. The
        # downstream grounding_check guardrail still enforces HALLU=0.
        # Set threshold > 1.0 to disable. Bot owner overrides via
        # ``plan_limits.crag_skip_retry_above_score`` (resolved upstream
        # by ``resolve_bot_limit`` into ``pipeline_config``).
        _skip_threshold = float(
            _pcfg(state, "crag_skip_retry_above_score", DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE)
        )
        if _skip_threshold > 0.0:
            _top_score = 0.0
            for _c in inp:
                _s_raw = _c.get("score")
                if _s_raw is None:
                    continue
                try:
                    _s = float(_s_raw)
                except (TypeError, ValueError):
                    continue
                if _s > _top_score:
                    _top_score = _s
            if _top_score >= _skip_threshold:
                logger.info(
                    "crag_grade_skip_high_score",
                    top_score=round(_top_score, 4),
                    skip_threshold=_skip_threshold,
                    n_chunks=len(inp),
                )
                grade_ctx.set_metadata(
                    grade_path="skip_high_score",
                    n_chunks_input=len(inp),
                    skip_top_score=round(_top_score, 4),
                    skip_threshold=_skip_threshold,
                )
                _graded = [
                    {**_c, "relevance": CRAG_GRADE_RELEVANT} for _c in inp
                ]
                _result: dict = {
                    "graded_chunks": _graded,
                    "retrieval_adequate": True,
                    "crag_skip_retry": True,
                    "crag_skip_reason": f"top_score={_top_score:.3f}>={_skip_threshold}",
                    "_total_graph_iterations": _total_iters,
                }
                # Intent self-correction also applies in the skip path.
                if state.get("intent") == INTENT_OUT_OF_SCOPE:
                    _result["intent"] = DEFAULT_INTENT_FALLBACK
                    _result["intent_corrected"] = True
                return _result

        query_text = state.get("rewritten_query") or state["query"]

        # Intent self-correction: reclassify mislabelled-OOS questions when retrieval recovered chunks.
        intent_corrected = False
        current_intent = state.get("intent")
        if current_intent == INTENT_OUT_OF_SCOPE and inp:
            intent_corrected = True

        so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
        so_grade = _pcfg(state, "grade_use_structured_output", DEFAULT_GRADE_USE_STRUCTURED_OUTPUT)
        so_grade_batch = _pcfg(state, "grade_use_batch", DEFAULT_GRADE_USE_BATCH)
        if not (bool(so_master) and bool(so_grade) and bool(so_grade_batch)):
            path_used = "per_chunk_fallback"
        graded: list[dict] = []
        grade_counts = {CRAG_GRADE_RELEVANT: 0, CRAG_GRADE_IRRELEVANT: 0, CRAG_GRADE_AMBIGUOUS: 0}
        structured_grade_succeeded = False
        _grade_word_map = {
            "yes": CRAG_GRADE_RELEVANT,
            "no": CRAG_GRADE_IRRELEVANT,
            "partial": CRAG_GRADE_AMBIGUOUS,
        }

        if bool(so_master) and bool(so_grade) and bool(so_grade_batch):
            # Single batched LLM call grades all chunks at once.
            batch_chunks_text = []
            for c in inp:
                cid = str(c.get("chunk_id") or c.get("id") or "")
                txt = c.get("content") or c.get("text") or ""
                batch_chunks_text.append(
                    f'<chunk id="{cid}">\n{txt}\n</chunk>'
                )
            batch_user = (
                f"<query>{query_text}</query>\n"
                + "\n".join(batch_chunks_text)
            )
            batch_messages = [
                {"role": "system", "content": _lang(state).prompt_grader},
                {"role": "user", "content": batch_user},
            ]
            # Wall-clock cap on the grade-LLM call. Distribution skew
            # (p50 0ms via high-score skip, p95 2.56s when invoked)
            # means the tail caller dominates chat-graph p95. The
            # cap acts as a safety net — on timeout the node falls
            # back to the reranker-supplied order, downstream
            # grounding_check still enforces HALLU=0 sacred. Per-bot
            # override via ``pipeline_config.grade_timeout_s``;
            # ``0`` disables the cap.
            _grade_timeout_s = float(
                _pcfg(state, "grade_timeout_s", DEFAULT_GRADE_TIMEOUT_S)
            )
            try:
                if _grade_timeout_s > 0:
                    parsed_batch, ctx_batch = await asyncio.wait_for(
                        _invoke_structured_llm_node(
                            state,
                            purpose="grading",
                            messages=batch_messages,
                            user_prompt=query_text,
                            schema=GradeBatchOutput,
                        ),
                        timeout=_grade_timeout_s,
                    )
                else:
                    parsed_batch, ctx_batch = await _invoke_structured_llm_node(
                        state,
                        purpose="grading",
                        messages=batch_messages,
                        user_prompt=query_text,
                        schema=GradeBatchOutput,
                    )
                # Wave M3.7-G1 — record CRAG batch grade LLM cost.
                # WHY: grade is the 2nd-most expensive LLM step (p50
                # ~2s when CRAG fires); pre-fix request_steps.model_used
                # was NULL so admin cost dashboards could not attribute
                # 28-43% of LLM spend. Only fires on success path
                # (timeout fallback already exits via early return).
                if ctx_batch is not None:
                    _gr_usage = _so_usage(ctx_batch)
                    grade_ctx.record_llm(
                        model_used=str(getattr(ctx_batch, "model_id", "") or "") or None,
                        prompt_tokens=_gr_usage["prompt_tokens"],
                        completion_tokens=_gr_usage["completion_tokens"],
                        cost_usd=_gr_usage["cost_usd"],
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "grade_timeout_fallback_to_rerank_order",
                    timeout_s=_grade_timeout_s,
                    n_chunks=len(inp),
                )
                grade_ctx.set_metadata(
                    grade_path="timeout_fallback",
                    n_chunks_input=len(inp),
                    grade_timeout_s=_grade_timeout_s,
                )
                # Reranker order preserved; downstream guardrail
                # still grades grounding.
                _fallback_graded = [
                    {**_c, "relevance": CRAG_GRADE_AMBIGUOUS} for _c in inp
                ]
                return {
                    "graded_chunks": _fallback_graded,
                    "retrieval_adequate": True,
                    "grade_timeout_fallback": True,
                    "_total_graph_iterations": _total_iters,
                }
            if parsed_batch is not None and parsed_batch.grades:
                by_id: dict[str, str] = {}
                for g in parsed_batch.grades:
                    if not g.chunk_id:
                        continue
                    by_id[g.chunk_id.lower()] = g.grade
                _so_graded: list[dict] = []
                _so_counts = {CRAG_GRADE_RELEVANT: 0, CRAG_GRADE_IRRELEVANT: 0, CRAG_GRADE_AMBIGUOUS: 0}
                _lenient_enabled = bool(_pcfg(
                    state,
                    "crag_lenient_grade_for_compound_intents_enabled",
                    DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
                ))
                _grade_intent = state.get("intent") or ""
                for chunk in inp:
                    cid_norm = str(chunk.get("chunk_id") or chunk.get("id") or "").lower()
                    verdict = by_id.get(cid_norm, "partial")
                    chunk_grade = _grade_word_map.get(verdict, CRAG_GRADE_AMBIGUOUS)
                    chunk_grade = _remap_grade_for_intent(
                        chunk_grade,
                        intent=_grade_intent,
                        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
                        lenient_enabled=_lenient_enabled,
                    )
                    _so_counts[chunk_grade] += 1
                    chunk_copy = {**chunk, "relevance": chunk_grade}
                    if chunk_grade in (CRAG_GRADE_RELEVANT, CRAG_GRADE_AMBIGUOUS):
                        _so_graded.append(chunk_copy)
                structured_grade_succeeded = True
                graded = _so_graded
                grade_counts = _so_counts
                logger.info(
                    "crag_grade_distribution",
                    relevant=grade_counts[CRAG_GRADE_RELEVANT],
                    irrelevant=grade_counts[CRAG_GRADE_IRRELEVANT],
                    ambiguous=grade_counts[CRAG_GRADE_AMBIGUOUS],
                    total=len(inp),
                    source="structured_output_batch",
                )
            else:
                logger.warning("grade_batch_failed_falling_back_to_per_chunk")
                path_used = "per_chunk_fallback"

        if not structured_grade_succeeded and bool(so_master) and bool(so_grade):
            any_parsed = False
            _so_graded: list[dict] = []
            _so_counts = {CRAG_GRADE_RELEVANT: 0, CRAG_GRADE_IRRELEVANT: 0, CRAG_GRADE_AMBIGUOUS: 0}

            # Bounded gather avoids flooding the LLM provider on deep top_K.
            _grade_concurrency = int(
                _pcfg(state, "crag_grade_concurrency", DEFAULT_CRAG_GRADE_CONCURRENCY)
            )
            _grade_sem = asyncio.Semaphore(max(1, _grade_concurrency))

            async def _grade_one_chunk(chunk: dict) -> tuple[dict, object | None, object | None]:
                """Grade one chunk via structured-output; returns (chunk, parsed, ctx)."""
                async with _grade_sem:
                    chunk_text = chunk.get("content") or chunk.get("text") or ""
                    per_messages = [
                        {"role": "system", "content": _lang(state).prompt_grader},
                        {"role": "user", "content": (
                            f"<query>{query_text}</query>\n<chunk>{chunk_text}</chunk>"
                        )},
                    ]
                    _parsed, _ctx = await _invoke_structured_llm_node(
                        state,
                        purpose="grading",
                        messages=per_messages,
                        user_prompt=query_text,
                        schema=GradeOutput,
                    )
                    return chunk, _parsed, _ctx

            # asyncio.gather preserves input order; semaphore bounds concurrency only.
            chunk_results = await asyncio.gather(
                *[_grade_one_chunk(c) for c in inp],
                return_exceptions=False,
            )

            for chunk, parsed, ctx in chunk_results:
                if parsed is None:
                    chunk_grade = CRAG_GRADE_AMBIGUOUS
                    if ctx is not None:
                        _u = _so_usage(ctx)
                        ctx.record(
                            response="",
                            prompt_tokens=_u["prompt_tokens"],
                            completion_tokens=_u["completion_tokens"],
                            cost_usd=_u["cost_usd"],
                            finish_reason="error",
                        )
                else:
                    any_parsed = True
                    chunk_grade = _grade_word_map.get(parsed.grade, CRAG_GRADE_AMBIGUOUS)
                    chunk_grade = _remap_grade_for_intent(
                        chunk_grade,
                        intent=(state.get("intent") or ""),
                        lenient_intents=DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
                        lenient_enabled=bool(_pcfg(
                            state,
                            "crag_lenient_grade_for_compound_intents_enabled",
                            DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
                        )),
                    )
                    if ctx is not None:
                        _u = _so_usage(ctx)
                        ctx.record(
                            response=_json_mod.dumps(parsed.model_dump()),
                            prompt_tokens=_u["prompt_tokens"],
                            completion_tokens=_u["completion_tokens"],
                            cost_usd=_u["cost_usd"],
                            finish_reason=_u["finish_reason"],
                        )
                _so_counts[chunk_grade] += 1
                chunk_copy = {**chunk, "relevance": chunk_grade}
                if chunk_grade in (CRAG_GRADE_RELEVANT, CRAG_GRADE_AMBIGUOUS):
                    _so_graded.append(chunk_copy)
            if any_parsed:
                structured_grade_succeeded = True
                graded = _so_graded
                grade_counts = _so_counts
                logger.info(
                    "crag_grade_distribution",
                    relevant=grade_counts[CRAG_GRADE_RELEVANT],
                    irrelevant=grade_counts[CRAG_GRADE_IRRELEVANT],
                    ambiguous=grade_counts[CRAG_GRADE_AMBIGUOUS],
                    total=len(inp),
                    source="structured_output",
                )
            else:
                logger.warning("grade_structured_output_all_failed_fallback")

        if not structured_grade_succeeded:
            logger.warning(
                "grade_no_structured_output_treat_all_ambiguous",
                total=len(inp),
            )
            for chunk in inp:
                grade_counts[CRAG_GRADE_AMBIGUOUS] += 1
                graded.append({**chunk, "relevance": CRAG_GRADE_AMBIGUOUS})
            result_meta = {
                "graded_chunks": graded,
                "retrieval_adequate": len(graded) > 0,
                "_total_graph_iterations": _total_iters,
            }
            grade_ctx.set_metadata(
                grade_path=path_used,
                n_chunks_input=len(inp),
                n_relevant=grade_counts[CRAG_GRADE_RELEVANT],
                n_irrelevant=grade_counts[CRAG_GRADE_IRRELEVANT],
                n_ambiguous=grade_counts[CRAG_GRADE_AMBIGUOUS],
                structured_output_used=structured_grade_succeeded,
            )
            await _audit(
                state,
                "grade_executed",
                {
                    "relevant": 0,
                    "irrelevant": 0,
                    "ambiguous": grade_counts[CRAG_GRADE_AMBIGUOUS],
                    "graded_kept": len(graded),
                    "retrieval_adequate": True,
                    "fallback_used": False,
                    "iterations": _total_iters,
                    "source": "structured_output_failed",
                },
            )
            return result_meta

        min_relevant = int(_pcfg(state, "crag_min_relevant_count", DEFAULT_CRAG_MIN_RELEVANT_COUNT))
        min_relevant_fraction = float(
            _pcfg(state, "crag_min_relevant_fraction", DEFAULT_CRAG_MIN_RELEVANT_FRACTION)
        )
        has_relevant = _is_retrieval_adequate(
            grade_counts,
            min_relevant_count=min_relevant,
            min_relevant_fraction=min_relevant_fraction,
        )
        has_ambiguous = grade_counts[CRAG_GRADE_AMBIGUOUS] > 0
        all_irrelevant = (
            grade_counts[CRAG_GRADE_RELEVANT] == 0
            and grade_counts[CRAG_GRADE_AMBIGUOUS] == 0
        )

        result: dict = {}

        if has_relevant:
            graded = [
                c for c in graded
                if c.get("relevance") in (CRAG_GRADE_RELEVANT, CRAG_GRADE_AMBIGUOUS)
            ]
            result["retrieval_adequate"] = True
        elif all_irrelevant:
            # Per-intent score threshold; synthesis-style intents admit broader candidates.
            if not graded and inp:
                intent_key = state.get("intent") or DEFAULT_INTENT_FALLBACK
                intent_thresholds = _pcfg(
                    state,
                    "crag_min_fallback_score_by_intent",
                    DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
                )
                if isinstance(intent_thresholds, dict) and intent_key in intent_thresholds:
                    min_score = float(intent_thresholds[intent_key])
                else:
                    min_score = float(_pcfg(
                        state,
                        "crag_min_fallback_score",
                        DEFAULT_CRAG_MIN_FALLBACK_SCORE,
                    ))
                _fb_pool = inp[:_pcfg(state, "crag_fallback_count", DEFAULT_CRAG_FALLBACK_COUNT)]
                # Calibrate the gate to the score scale. Cross-encoder scores
                # (mode=="rerank") are 0..1 → the absolute floor is valid.
                # RRF/bypass scores (~0.01) are NOT comparable to a 0.25
                # absolute floor (it would always reject → refuse oan), so we
                # switch to a scale-invariant relative gate: keep candidates
                # within RELATIVE_RATIO of the top score.
                if state.get("rerank_score_mode") == "rerank":
                    # Safety-net chunks (rerank node, _safety_injected) ARE
                    # top-of-retrieval re-added under the reranker; when the
                    # min-score/cliff stage emptied the surviving pool they keep
                    # their raw RRF score (~0.01), which the provenance-blind
                    # absolute floor would wrongly drop. Exempt them so the
                    # safety-net is not undone here.
                    fallback_candidates = [
                        c for c in _fb_pool
                        if c.get("_safety_injected")
                        or float(c.get("score", 0)) >= min_score
                    ]
                else:
                    _fb_top = max(
                        (float(c.get("score", 0) or 0) for c in _fb_pool), default=0.0,
                    )
                    if _fb_top > 0:
                        _rel_floor = _fb_top * DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO
                        fallback_candidates = [
                            c for c in _fb_pool
                            if float(c.get("score", 0) or 0) >= _rel_floor
                        ]
                    else:
                        fallback_candidates = list(_fb_pool)
                if fallback_candidates:
                    graded = [{**c, "relevance": "fallback"} for c in fallback_candidates]
                else:
                    result["retrieval_adequate"] = False
            if "retrieval_adequate" not in result:
                result["retrieval_adequate"] = len(graded) > 0
        else:
            retries = state.get("grade_retries", 0)
            max_retries = int(_pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES))
            # Compound-intent leniency (G10b · Issue 10): for compound queries
            # the surviving chunks each address ONE sub-entity. Re-running
            # retrieve+rerank+grade on a rewritten query rarely recovers a
            # "yes" verdict (the chunks are already the right ones; the
            # grader is being asked the WHOLE question). Skip the retry
            # and use the partial chunks - generate can still synthesize.
            _lenient_route_enabled = bool(_pcfg(
                state,
                "crag_lenient_grade_for_compound_intents_enabled",
                DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
            ))
            _route_intent = state.get("intent") or ""
            _lenient_route = (
                _lenient_route_enabled
                and _route_intent in DEFAULT_CRAG_LENIENT_GRADE_INTENTS
                and bool(graded)
            )
            if (retries >= max_retries and graded) or _lenient_route:
                # Retries exhausted (or compound-intent shortcut) with
                # ambiguous chunks: prefer partial answer over empty.
                result["retrieval_adequate"] = True
            else:
                result["retrieval_adequate"] = False

        result["graded_chunks"] = graded
        result["_total_graph_iterations"] = _total_iters
        if intent_corrected and bool(result.get("retrieval_adequate")):
            result["intent"] = DEFAULT_INTENT_FALLBACK
            result["intent_corrected"] = True
        grade_ctx.set_metadata(
            grade_path=path_used,
            n_chunks_input=len(inp),
            n_relevant=grade_counts[CRAG_GRADE_RELEVANT],
            n_irrelevant=grade_counts[CRAG_GRADE_IRRELEVANT],
            n_ambiguous=grade_counts[CRAG_GRADE_AMBIGUOUS],
            structured_output_used=structured_grade_succeeded,
        )
        await _audit(
            state,
            "grade_executed",
            {
                "relevant": grade_counts.get(CRAG_GRADE_RELEVANT, 0),
                "irrelevant": grade_counts.get(CRAG_GRADE_IRRELEVANT, 0),
                "ambiguous": grade_counts.get(CRAG_GRADE_AMBIGUOUS, 0),
                "graded_kept": len(graded),
                "retrieval_adequate": bool(result.get("retrieval_adequate")),
                "fallback_used": all_irrelevant
                and graded
                and graded[0].get("relevance") == "fallback",
                "iterations": _total_iters,
                "intent_corrected": bool(result.get("intent_corrected", False)),
            },
        )
        return result


__all__ = ["grade"]
