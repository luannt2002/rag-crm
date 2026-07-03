"""Rerank node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no logic / score gate / prompt / state key / ordering /
log-event change).

The pure CRAG/cliff filters (``_cliff_detect_filter``, ``_rerank_threshold_gate``)
are imported directly from ``retrieval_filter`` (no cycle). Shared helper
closure ``_audit`` and the query_graph-local helpers (``_pcfg``,
``_uuid_or_none``) are threaded in as kwargs.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import structlog

from ragbot.orchestration.retrieval_filter import (
    _cliff_detect_filter,
    _rerank_threshold_gate,
)
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_CHUNK_SURVIVAL_TRACE_CAP,
    DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
    DEFAULT_RERANK_CLIFF_GAP_RATIO,
    DEFAULT_RERANK_CLIFF_MIN_KEEP,
    DEFAULT_RERANK_CLIFF_SKIP_INTENTS,
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_MAX_CHUNKS_TO_LLM,
    DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
    DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_RERANK_TOP_N_BY_INTENT,
    DEFAULT_RERANKER_MIN_SCORE,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
    DEFAULT_RERANKER_MIN_SCORE_BYPASS,
)
from ragbot.shared.errors import RetrievalError

logger = structlog.get_logger(__name__)

try:
    from ragbot.infrastructure.observability.metrics import (
        cliff_drop_total,
    )
except ImportError:
    cliff_drop_total = None  # type: ignore[assignment]


async def rerank(
    state: GraphState,
    *,
    reranker: Any = None,
    reranker_resolver: Any = None,
    error_notify_hook: Any = None,
    _audit: Any,
    _pcfg: Any,
    _uuid_or_none: Any,
) -> dict:
    async with state["step_tracker"].step("rerank") as step_ctx:
        inp = state.get("retrieved_chunks", [])
        # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 — per-intent top_n.
        # Aggregation queries need a wider rerank cap to retain every
        # matching row; default top_n=7 starved "có mấy X" queries
        # (verified 2026-05-21 UI test on a representative test tenant).
        _intent_for_topn = state.get("intent") or ""
        _topn_by_intent = _pcfg(state, "rerank_top_n_by_intent", DEFAULT_RERANK_TOP_N_BY_INTENT)
        _intent_override_topn = False
        if isinstance(_topn_by_intent, dict) and _intent_for_topn in _topn_by_intent:
            try:
                top_n = int(_topn_by_intent[_intent_for_topn])
                _intent_override_topn = True
            except (TypeError, ValueError):
                top_n = _pcfg(state, "rerank_top_n", DEFAULT_RERANK_TOP_N)
        else:
            top_n = _pcfg(state, "rerank_top_n", DEFAULT_RERANK_TOP_N)
        enabled = bool(_pcfg(state, "reranker_enabled", True))

        # Per-bot resolver wins; falls back to the process-global singleton.
        _active_reranker = reranker
        if reranker_resolver is not None and state.get("record_bot_id"):
            try:
                from uuid import UUID as _UUID
                _bot_uuid = _UUID(str(state["record_bot_id"]))
                _active_reranker = await reranker_resolver.resolve_for_bot(_bot_uuid)
            except (ValueError, TypeError) as _e:
                logger.warning(
                    "rerank_resolver_bad_bot_id",
                    record_bot_id=str(state.get("record_bot_id")),
                    error=str(_e)[:100],
                )

        # Local import avoids module-load circular dep with shared.constants.
        from ragbot.infrastructure.reranker.null_reranker import (
            NullReranker as _NullReranker,
        )

        # A real per-bot binding overrides the global reranker_enabled flag.
        _per_bot_reranker_active = (
            _active_reranker is not None
            and not isinstance(_active_reranker, _NullReranker)
            and reranker_resolver is not None  # only override when using resolver path
        )

        # Per-bot intent whitelist: when enabled, rerank only fires for listed intents.
        _whitelist = _pcfg(state, "rerank_intent_whitelist", None)
        _intent = state.get("intent")
        _intent_skip = False
        if (
            _whitelist is not None
            and getattr(_whitelist, "enabled", False)
            and _intent not in getattr(_whitelist, "intents", ())
        ):
            _intent_skip = True
            if not getattr(_whitelist, "intents", ()):
                logger.warning(
                    "rerank_intent_whitelist_empty_intents",
                    record_bot_id=str(state.get("record_bot_id") or ""),
                    intent=_intent,
                )

        # T2.S7 — per-intent skip gate with size safety.
        # Cheap intents (greeting / chitchat / single-fact lookup) skip rerank
        # ONLY when the candidate pool already fits inside rerank_top_n —
        # otherwise the disambiguation value of rerank still pays for itself
        # even on lightweight intents (e.g. factoid with 20 candidates fighting
        # for top 7 prices). Empty skip set disables the gate. Set membership
        # is lower-cased so classifier casing drift cannot defeat the skip.
        _skip_set_raw = _pcfg(state, "rerank_skip_intents", ()) or ()
        _skip_set = frozenset(
            str(s).strip().lower() for s in _skip_set_raw if str(s).strip()
        )
        _intent_lc = str(_intent or "").strip().lower()
        _size_safety = len(inp) <= int(top_n)
        _intent_skip_set = (
            bool(_skip_set)
            and _intent_lc in _skip_set
            and _size_safety
        )

        # Bypass taxonomy: empty_input | intent_skip_set | intent_skip | disabled | no_reranker | null_reranker | rerank.
        if not inp:
            mode = "empty_input"
        elif _intent_skip_set:
            mode = "intent_skip_set"
        elif _intent_skip:
            mode = "intent_skip"
        elif not enabled and not _per_bot_reranker_active:
            mode = "disabled"
        elif _active_reranker is None:
            logger.warning("rerank_no_adapter_configured")
            mode = "no_reranker"
        elif isinstance(_active_reranker, _NullReranker):
            mode = "null_reranker"
        else:
            mode = "rerank"

        if mode == "rerank":
            # Per-bot path uses DB-configured model; only the global path forwards reranker_model.
            _model_override = (
                None if _per_bot_reranker_active
                else _pcfg(state, "reranker_model", None)
            )
            # Fail-soft: rerank API errors fall back to retrieval order, keeping the pipeline alive.
            try:
                out = await _active_reranker.rerank(
                    query=state.get("rewritten_query") or state["query"],
                    chunks=inp,
                    top_n=top_n,
                    model=_model_override,
                )
            except RetrievalError as exc:
                logger.warning(
                    "rerank_api_failed_fallback_to_rrf",
                    provider=getattr(_active_reranker, "mode", "unknown"),
                    error=str(exc)[:200],
                )
                mode = "rerank_fallback"
                out = inp[:top_n]
                # Surface rerank API failure to webhook so ops sees
                # provider key 429/403/quota issue without tailing logs.
                if error_notify_hook is not None:
                    with contextlib.suppress(Exception):
                        await error_notify_hook.on_ai_error(
                            error=exc,
                            component="retrieval.rerank",
                            record_tenant_id=_uuid_or_none(state.get("record_tenant_id")),
                            record_bot_id=_uuid_or_none(state.get("record_bot_id")),
                            request_id=None,
                        )
        else:
            out = inp[:top_n]

        # WE-4 — write metadata_json.top_score so
        # scripts/diagnose_p95_bottleneck.py --rerank-score-histogram can
        # populate per-bot empirical distribution. Score semantics follow
        # ``mode``: rerank → cross-encoder 0..1, bypass → RRF 0.01-0.05.
        _top_score_scores = [float(c.get("score", 0) or 0) for c in out]
        _top_score = round(max(_top_score_scores), 6) if _top_score_scores else 0.0

        if mode == "intent_skip":
            step_ctx.set_metadata(
                mode=mode,
                input=len(inp),
                reranked=len(out),
                top_score=_top_score,
                intent=_intent or "",
                whitelist_intents=list(getattr(_whitelist, "intents", ())),
                rerank_top_n=int(top_n),
                rerank_top_n_intent_override=_intent_override_topn,
            )
        elif mode == "intent_skip_set":
            step_ctx.set_metadata(
                mode=mode,
                input=len(inp),
                reranked=len(out),
                top_score=_top_score,
                intent=_intent or "",
                skip_intents=sorted(_skip_set),
                rerank_top_n=int(top_n),
                rerank_top_n_intent_override=_intent_override_topn,
            )
        else:
            step_ctx.set_metadata(
                mode=mode,
                input=len(inp),
                reranked=len(out),
                top_score=_top_score,
                rerank_top_n=int(top_n),
                rerank_top_n_intent_override=_intent_override_topn,
            )

        # Mode-aware floor: cross-encoder 0..1 when rerank active, RRF 0.01-0.05 when bypassed.
        if mode == "rerank":
            _mode_default = DEFAULT_RERANKER_MIN_SCORE_ACTIVE
            _mode_key = "reranker_min_score_active"
        else:
            _mode_default = DEFAULT_RERANKER_MIN_SCORE_BYPASS
            _mode_key = "reranker_min_score_bypass"
        min_score = _pcfg(state, _mode_key, None)
        if min_score is None:
            # Legacy single-key fallback for un-migrated bots.
            min_score = _pcfg(state, "reranker_min_score", DEFAULT_RERANKER_MIN_SCORE)
            if min_score == DEFAULT_RERANKER_MIN_SCORE:
                # Legacy default is bypass-shaped; promote when live mode is rerank.
                min_score = _mode_default
        # Strategy dispatch: "threshold" (legacy static) | "cliff" (adaptive).
        # Cliff strategy ignores min_score; uses gap-ratio + absolute floor.
        _filter_strategy = _pcfg(state, "rerank_filter_strategy", DEFAULT_RERANK_FILTER_STRATEGY)
        if _filter_strategy == "cliff" and out:
            async with state["step_tracker"].step("filter_min_score") as fs_ctx:
                n_in = len(out)
                _scores_in = [float(c.get("score", 0) or 0) for c in out]
                top_in = max(_scores_in) if _scores_in else 0.0
                _floor = float(_pcfg(state, "rerank_cliff_absolute_floor", DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR))
                _gap = float(_pcfg(state, "rerank_cliff_gap_ratio", DEFAULT_RERANK_CLIFF_GAP_RATIO))
                _mink = int(_pcfg(state, "rerank_cliff_min_keep", DEFAULT_RERANK_CLIFF_MIN_KEEP))
                # Multi-fact intents need every entity/clause chunk — the gap-cut
                # drops answer chunks (e.g. a legal corpus multi_hop → 1 survived). Keep
                # the full reranked set for these intents instead of cliff-cutting.
                _cliff_skip = _pcfg(state, "rerank_cliff_skip_intents", None)
                _cliff_skip = (
                    set(_cliff_skip) if _cliff_skip is not None
                    else DEFAULT_RERANK_CLIFF_SKIP_INTENTS
                )
                if state.get("intent") in _cliff_skip:
                    _mink = len(out)
                _ids_before_cliff = [
                    str(c.get("chunk_id") or c.get("id") or "") for c in out
                ]
                out, _cliff_meta = _cliff_detect_filter(
                    out, absolute_floor=_floor, gap_ratio=_gap, min_keep=_mink,
                )
                n_kept = len(out)
                _scores_out = [float(c.get("score", 0) or 0) for c in out]
                top_out = max(_scores_out) if _scores_out else 0.0
                # C1 chunk-survival trace: which candidate chunk_ids this stage
                # dropped, so a "why did the answer chunk die" query on
                # request_steps can pin the exact stage (cliff floor/gap here).
                _ids_after_cliff = {
                    str(c.get("chunk_id") or c.get("id") or "") for c in out
                }
                _dropped_ids = [
                    cid for cid in _ids_before_cliff if cid and cid not in _ids_after_cliff
                ]
                fs_ctx.set_metadata(
                    n_in=n_in,
                    n_kept=n_kept,
                    n_dropped=n_in - n_kept,
                    dropped_chunk_ids=_dropped_ids[:DEFAULT_CHUNK_SURVIVAL_TRACE_CAP],
                    strategy="cliff",
                    absolute_floor=_floor,
                    gap_ratio=_gap,
                    cliff_max_gap=_cliff_meta["max_gap_ratio"],
                    cliff_triggered=_cliff_meta["triggered"],
                    cliff_reason=_cliff_meta["reason"],
                    top_score_in=round(top_in, 6),
                    top_score_out=round(top_out, 6),
                    mode=mode,
                )
                # Observability — bump only when cliff actually removed chunks.
                # Label cardinality bounded by _cliff_detect_filter reason enum
                # ("cliff" / "no_cliff_kept_all" / "below_floor_or_single" /
                # "empty_context_safety_keep_top1") and per-bot slug.
                if cliff_drop_total is not None and n_kept < n_in:
                    try:
                        cliff_drop_total.labels(
                            bot_id=str(state.get("bot_id") or "unknown"),
                            reason=str(_cliff_meta["reason"]),
                        ).inc(n_in - n_kept)
                    except Exception:  # noqa: BLE001 — metrics must not break pipeline
                        pass
        elif min_score and out:
            async with state["step_tracker"].step("filter_min_score") as fs_ctx:
                n_in = len(out)
                _scores_in = [float(c.get("score", 0) or 0) for c in out]
                top_in = max(_scores_in) if _scores_in else 0.0
                _ids_before_thr = [
                    str(c.get("chunk_id") or c.get("id") or "") for c in out
                ]
                out = [c for c in out if float(c.get("score", 0)) >= float(min_score)]
                n_kept = len(out)
                _scores_out = [float(c.get("score", 0) or 0) for c in out]
                top_out = max(_scores_out) if _scores_out else 0.0
                # C1 chunk-survival trace (threshold branch): dropped chunk_ids.
                _ids_after_thr = {
                    str(c.get("chunk_id") or c.get("id") or "") for c in out
                }
                _dropped_ids = [
                    cid for cid in _ids_before_thr if cid and cid not in _ids_after_thr
                ]
                fs_ctx.set_metadata(
                    n_in=n_in,
                    n_kept=n_kept,
                    n_dropped=n_in - n_kept,
                    dropped_chunk_ids=_dropped_ids[:DEFAULT_CHUNK_SURVIVAL_TRACE_CAP],
                    strategy="threshold",
                    min_score_threshold=float(min_score),
                    top_score_in=round(top_in, 6),
                    top_score_out=round(top_out, 6),
                    mode=mode,
                )
                if n_kept < n_in:
                    logger.info(
                        "rerank_min_score_filtered",
                        before=n_in,
                        after=n_kept,
                        threshold=min_score,
                        mode=mode,
                    )

        # Post-filter refuse gate: when a real reranker ran and the
        # surviving top-1 score still sits below ``min_score`` (resolved
        # per-bot via PLAN_LIMIT_SCHEMA → reranker_min_score_active),
        # drop every chunk so the existing refuse short-circuit at the
        # generate node emits ``bots.oos_answer_template``. Gate skips
        # bypass modes — their score scale is incomparable with the
        # cross-encoder 0..1 floor (Quality Gate #10: refuse text comes
        # from DB, never from this gate).
        #
        # Wave J2 fix (2026-05-20): honour the strategy contract documented
        # in ``PLAN_LIMIT_SCHEMA`` ("when strategy='cliff',
        # reranker_min_score_active is ignored"). The cliff filter already
        # cut weak chunks via ``absolute_floor`` + ``gap_ratio`` and its
        # ``force_min_keep=True`` safety net is specifically designed to
        # avoid empty context. Running the static threshold gate on top
        # double-gates and discards the cliff safety chunk — producing
        # false-positive refuses at top_score 0.29-0.43 (load-test 15Q:
        # 27% refused, 3/4 cliff strategy active). Threshold strategy still
        # runs the gate (legacy hard-cut bots opt-in via plan_limits).
        # Owner can force the legacy behaviour back via plan_limits flag
        # ``rerank_threshold_gate_after_cliff_enabled`` (default OFF).
        _gate_after_cliff = bool(_pcfg(
            state,
            "rerank_threshold_gate_after_cliff_enabled",
            DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
        ))
        _run_gate = _filter_strategy != "cliff" or _gate_after_cliff
        if _run_gate:
            _gate_threshold = float(min_score) if min_score else float(_mode_default)
            out, _gate_meta = _rerank_threshold_gate(
                out, threshold=_gate_threshold, mode=mode,
            )
            if _gate_meta["applicable"]:
                logger.info(
                    "rerank_threshold_gate",
                    top_score=_gate_meta["top_score"],
                    threshold=_gate_meta["threshold"],
                    refused=_gate_meta["refused"],
                    bot_id=str(state.get("bot_id") or ""),
                    record_bot_id=str(state.get("record_bot_id") or ""),
                    mode=mode,
                    strategy=_filter_strategy,
                )
        else:
            # Cliff strategy already filtered — skip the redundant static
            # threshold gate. Emit observability so ops can verify the new
            # behaviour landed without grep-walking the source.
            logger.info(
                "rerank_threshold_gate_skipped",
                strategy=_filter_strategy,
                reason="cliff_strategy_owns_filtering",
                bot_id=str(state.get("bot_id") or ""),
                record_bot_id=str(state.get("record_bot_id") or ""),
                mode=mode,
            )

        # Hard cap on chunk COUNT into the LLM prompt. Score filters above cut
        # weak chunks but can still pass many near-duplicate ones (fragmented
        # price rows) — this bounds the prompt. Multi-fact intents are exempt
        # (they need every clause/entity chunk); ``0`` disables the cap.
        _max_to_llm = int(_pcfg(state, "rerank_max_chunks_to_llm", DEFAULT_RERANK_MAX_CHUNKS_TO_LLM))
        _cap_skip = _pcfg(state, "rerank_cliff_skip_intents", None)
        _cap_skip = set(_cap_skip) if _cap_skip is not None else DEFAULT_RERANK_CLIFF_SKIP_INTENTS
        if _max_to_llm > 0 and len(out) > _max_to_llm and state.get("intent") not in _cap_skip:
            _n_before_cap = len(out)
            out = out[:_max_to_llm]
            logger.info(
                "rerank_max_chunks_cap",
                before=_n_before_cap, after=len(out), cap=_max_to_llm,
                intent=str(state.get("intent") or ""),
                bot_id=str(state.get("bot_id") or ""),
            )

        _r_scores = [float(c.get("score", 0) or 0) for c in out]
        _provider_name = (
            getattr(_active_reranker, "get_provider_name", lambda: type(_active_reranker).__name__)()
            if _active_reranker is not None
            else "none"
        )
        await _audit(
            state,
            "rerank_executed",
            {
                "mode": mode,
                "before": len(inp),
                "after": len(out),
                "top_score_active": round(max(_r_scores), 6) if _r_scores else 0,
                "min_score_filter": float(min_score) if min_score else 0,
                "provider": _provider_name,
            },
        )

        # Opt-in per-chunk debug; score semantics follow `mode`.
        if os.getenv("DEBUG_RETRIEVAL", "false").lower() == "true" or state.get("debug_full"):
            try:
                logger.info(
                    "retrieval_chunks_debug",
                    query=(state.get("rewritten_query") or state.get("query") or "")[:100],
                    top_k_scores=[float(c.get("score", 0) or 0) for c in out[:5]],
                    top_k_sources=[
                        (
                            c.get("document_name")
                            or (c.get("metadata") or {}).get("document_title")
                            or c.get("source")
                            or f"chunk:{c.get('chunk_id') or c.get('id')}"
                        )
                        for c in out[:5]
                    ],
                    chunk_count=len(out),
                    record_bot_id=str(state.get("record_bot_id") or ""),
                    mode=mode,
                    reranker_provider=_provider_name,
                )
            except Exception:  # noqa: BLE001 — observability must not break pipeline
                pass

        # Retrieval safety-net: a strongly-retrieved chunk (top of the
        # pre-rerank RRF/BM25/vector order) must not be silently dropped just
        # because the semantic reranker under-ranks it. Forensic 2026-06-05:
        # zerank-2 buried an exact-answer legal clause (BM25 rank #1) to
        # rerank rank-8, beyond top_n + cliff → hard miss. Union the top-N
        # retrieval-ordered candidates back in (bounded, only when the
        # reranker disagrees with retrieval). Default-ON robustness for every
        # bot; per-bot config tunes N up. Skip when no real rerank happened.
        if mode == "rerank" and inp:
            _safety_n = int(_pcfg(state, "rerank_retrieval_safety_n", DEFAULT_RERANK_RETRIEVAL_SAFETY_N))
            if _safety_n > 0:
                _kept_ids = {(c.get("chunk_id") or c.get("id")) for c in out}
                # Stamp safety-net chunks with a rerank-scale score so they
                # survive the downstream CRAG absolute-floor + context-cap
                # ordering — otherwise their raw RRF score (~0.01) is below
                # crag_min_fallback_score (0.3) and they get dropped, defeating
                # the safety-net. Lift each injected chunk UP to the lowest
                # surviving rerank score (these chunks ARE top-of-retrieval, just
                # under-ranked by zerank-2). When the min-score/cliff stage has
                # already emptied the surviving pool there is no floor to lift
                # to — keep the chunk's own real retrieval score instead, so a
                # genuinely-retrieved chunk reports its true score rather than a
                # collapsed 0.0 (M18: stamping 0.0 made the absolute-floor drop
                # the very chunk the safety-net re-injected).
                _kept_scores = [float(c.get("score", 0) or 0) for c in out]
                _stamp = min(_kept_scores) if _kept_scores else None
                _added = 0
                for _c in inp[:_safety_n]:
                    _cid = _c.get("chunk_id") or _c.get("id")
                    if _cid and _cid not in _kept_ids:
                        _c = dict(_c)
                        if _stamp is not None:
                            _c["score"] = _stamp
                        _c["_safety_injected"] = True
                        out.append(_c)
                        _kept_ids.add(_cid)
                        _added += 1
                if _added:
                    await _audit(state, "rerank_retrieval_safety_net", {
                        "added": _added,
                        "safety_n": _safety_n,
                        # None when the surviving pool was empty: the injected
                        # chunks kept their own real retrieval score (no lift).
                        "stamp_score": round(_stamp, 4) if _stamp is not None else None,
                    })

        # Propagate the score scale so the CRAG fallback gate can calibrate:
        # mode=="rerank" → cross-encoder scores (0..1, absolute floor valid);
        # any other mode → RRF/bypass scores (~0.01, needs relative gate).
        return {"reranked_chunks": out, "rerank_score_mode": mode}


__all__ = ["rerank"]
