"""Merged condense+router 'understand_query' node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no logic / prompt / LLM call / state key / ordering /
log-event change).

Shared helper closures (``_audit``, ``_invoke_structured_llm_node``,
``_so_usage``) and the query_graph-local module helpers (``_pcfg``, ``_lang``)
are threaded in as kwargs (importing the latter here would create a circular
import). Domain-neutral module-level collaborators (heuristic classifier, boot
config getter, intent metric, schema, constants) are imported directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from ragbot.application.dto.llm_schemas import UnderstandOutput
from ragbot.application.services.heuristic_intent_classifier import (
    classify_heuristic as _classify_heuristic,
)
from ragbot.orchestration.state import GraphState
from ragbot.shared.condense_gate import has_meaningful_history
from ragbot.shared.bootstrap_config import get_boot_config as _get_boot_config
from ragbot.shared.constants import (
    DEFAULT_CONDENSE_HISTORY_LIMIT,
    DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
    DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
    DEFAULT_HEURISTIC_INTENT_ENABLED,
    DEFAULT_INTENT_CONFIDENCE_FALLBACK,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_LANGUAGE,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
    DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS,
    DEFAULT_UNDERSTAND_CONDENSED_QUERY_AUDIT_PREVIEW_LEN,
    DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S,
    DEFAULT_UNDERSTAND_USE_STRUCTURED_OUTPUT,
    HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
)
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.i18n import get_routing_signals

logger = structlog.get_logger(__name__)

try:
    from ragbot.infrastructure.observability.metrics import (
        intent_classifier_confidence,
    )
except ImportError:
    intent_classifier_confidence = None  # type: ignore[assignment]


async def understand_query(
    state: GraphState,
    *,
    llm: Any = None,
    model_resolver: Any = None,
    understand_query_cache: Any = None,
    _audit: Any,
    _invoke_structured_llm_node: Any,
    _so_usage: Any,
    _pcfg: Any,
    _lang: Any,
) -> dict:
    """Merged condense+router; returns query/original_query/intent/model_used/answer."""
    # Idempotency guard: ``cache_check_and_understand_parallel`` may have
    # already executed the body upstream and merged results into state.
    # Skip so the LLM call never fires twice per turn. ``force_re_understand``
    # is the CRAG-retry escape hatch — when an upstream node wants a fresh
    # intent pass after rewrite_retry, it sets the flag to bypass this gate.
    # The marker field MUST be declared on ``GraphState`` (state.py); a
    # missing declaration causes LangGraph to drop the key at reducer-merge
    # time which silently re-enables the duplicate call.
    if state.get("_understand_skipped_by_parallel") and not state.get("force_re_understand"):
        return {}
    # Stream S5 Pipeline-Opt: Redis-backed memo. Repeat queries within
    # ``understand_query.cache_ttl_s`` skip the LLM round-trip entirely.
    # Bot-scoped by ``record_bot_id`` (post-resolve unique PK) — see
    # CLAUDE.md 4-key resolve flow. Cache failure degrades silent.
    _uq_cache = understand_query_cache
    _uq_bot_id = state.get("record_bot_id")
    _uq_query = state.get("query") or ""
    if _uq_cache is not None and _uq_bot_id and _uq_query:
        cached = await _uq_cache.get(str(_uq_bot_id), _uq_query)
        if cached:
            state["_uq_cache_hit"] = True
            await _audit(
                state,
                "intent_extracted",
                {
                    "intent": cached.get("intent", DEFAULT_INTENT_FALLBACK),
                    "intent_confidence": cached.get(
                        "intent_confidence", DEFAULT_INTENT_CONFIDENCE_FALLBACK,
                    ),
                    "condensed": bool(cached.get("query")),
                    "condensed_query": (cached.get("query") or "")[
                        :DEFAULT_UNDERSTAND_CONDENSED_QUERY_AUDIT_PREVIEW_LEN
                    ],
                    "cache_hit": True,
                },
            )
            # Filter to recognised state-update keys only — defence
            # against a corrupt payload sneaking unexpected slots in.
            _allowed = {"intent", "intent_confidence", "query", "original_query"}
            return {k: v for k, v in cached.items() if k in _allowed}

    # Layer 1 heuristic intent classify — skip LLM for high-confidence
    # easy-signal turns (greeting, chitchat). Saves ~1.6s p50 for ~80%
    # of conversational traffic. HALLU=0 sacred: heuristic only fires on
    # anchored patterns; any ambiguous query falls through to LLM.
    _heuristic_enabled = bool(
        _pcfg(state, "heuristic_intent_enabled", DEFAULT_HEURISTIC_INTENT_ENABLED)
    )
    if _heuristic_enabled and not state.get("force_re_understand"):
        # Locale-scoped intent patterns: resolve the bot's language pack signals
        # so a non-vi bot classifies on ITS patterns instead of the vi seed.
        # A vi bot resolves the vi seed → byte-identical to the legacy call.
        _h_signals = get_routing_signals(
            str(state.get("language") or DEFAULT_LANGUAGE)
        )
        _h_result = _classify_heuristic(
            state.get("query") or "", signals=_h_signals
        )
        _h_threshold = float(
            _pcfg(
                state,
                "heuristic_intent_confidence_threshold",
                HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
            )
        )
        if _h_result.intent is not None and _h_result.confidence >= _h_threshold:
            async with state["step_tracker"].step("understand_query") as _h_ctx:
                _h_ctx.set_metadata(
                    source="heuristic",
                    intent=_h_result.intent,
                    confidence=round(_h_result.confidence, 3),
                    pattern=_h_result.matched_pattern or "",
                )
            logger.debug(
                "understand_query_heuristic_match",
                intent=_h_result.intent,
                confidence=round(_h_result.confidence, 3),
            )
            return {
                "intent": _h_result.intent,
                "intent_source": "heuristic",
                "intent_confidence": _h_result.confidence,
            }

    # router_select_model telemetry step relocated to pre_retrieval_parallel_node
    # (query_complexity_node) so it runs in parallel with query_complexity and
    # semantic_cache_preflight — ~42ms DB lookup moved off the critical path.

    # Wave M3.7-G1 — name the step context so the LLM call (structured
    # or text path) can be recorded onto request_steps via ctx.model_id
    # (set by _invoke_structured_llm_node) and the text-path payload.
    # WHY: see condense_question above — every LLM-bound step needs
    # to attribute its cost back to a dashboard row.
    async with state["step_tracker"].step("understand_query") as uq_ctx:
        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=understand_query")

        history = state.get("conversation_history", [])
        # Shared predicate (drift-proof): the first follow-up (history == 2
        # messages) MUST trigger condense — see shared/condense_gate.py; the
        # old hand-rolled strict `>` here silently skipped it in production
        # (truth-audit 002 cluster A).
        _history_meaningful = has_meaningful_history(
            history,
            min_turns=DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
            min_chars=DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
        )

        if _history_meaningful:
            _u_pack = _lang(state)
            history_text = "\n".join(
                f"{_u_pack.condense_user_role if m.get('role') == 'user' else _u_pack.condense_bot_role}: {m.get('content', '')}"
                for m in history[-_pcfg(state, "condense_history_limit", DEFAULT_CONDENSE_HISTORY_LIMIT):]
            )
            user_content = (
                f"<history>\n{history_text}\n</history>\n\n"
                f"<question>{state['query']}</question>"
            )
        else:
            user_content = f"<question>{state['query']}</question>"

        _bot_context = ""
        _bot_sysprompt = state.get("bot_system_prompt", "") or ""
        if _bot_sysprompt:
            _bot_context = (
                f"\n<bot_context>\n"
                f"{_bot_sysprompt[:DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS]}"
                f"\n</bot_context>\n"
            )
        messages = [
            {"role": "system", "content": _lang(state).prompt_understand + _bot_context},
            {"role": "user", "content": user_content},
        ]

        so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
        so_understand = _pcfg(
            state, "understand_use_structured_output",
            DEFAULT_UNDERSTAND_USE_STRUCTURED_OUTPUT,
        )
        use_structured = bool(so_master) and bool(so_understand)

        try:
            if use_structured:
                parsed, ctx = await _invoke_structured_llm_node(
                    state,
                    purpose="understand_query",
                    messages=messages,
                    user_prompt=state["query"],
                    schema=UnderstandOutput,
                )
                # Wave M3.7-G1 — record structured-LLM cost regardless of
                # parse success (model burned tokens on both paths).
                _uq_usage = _so_usage(ctx)
                uq_ctx.record_llm(
                    model_used=str(getattr(ctx, "model_id", "") or "") or None,
                    prompt_tokens=_uq_usage["prompt_tokens"],
                    completion_tokens=_uq_usage["completion_tokens"],
                    cost_usd=_uq_usage["cost_usd"],
                )
                if parsed is not None:
                    condensed = (parsed.condensed_query or "").strip()
                    intent = parsed.intent
                    # Confidence is part of the structured-output contract;
                    # LLM-emitted value flows straight through, callers
                    # without structured output see the Pydantic default
                    # DEFAULT_INTENT_CONFIDENCE_FALLBACK.
                    try:
                        confidence = float(getattr(parsed, "confidence",
                                                   DEFAULT_INTENT_CONFIDENCE_FALLBACK))
                    except (TypeError, ValueError):
                        confidence = DEFAULT_INTENT_CONFIDENCE_FALLBACK
                    # Clamp to documented [0, 1] range — defence against a
                    # provider returning a truthy out-of-range float.
                    confidence = max(0.0, min(1.0, confidence))
                    update: dict = {
                        "intent": intent,
                        "intent_confidence": confidence,
                        "intent_source": "llm",
                    }
                    if condensed and condensed != state["query"]:
                        update["query"] = condensed
                        update["original_query"] = state["query"]
                    if intent_classifier_confidence is not None:
                        try:
                            intent_classifier_confidence.labels(
                                intent=str(intent),
                            ).observe(confidence)
                        except (ValueError, KeyError, AttributeError):
                            # Metric backend hiccup — never break the pipeline.
                            pass
                    await _audit(
                        state,
                        "intent_extracted",
                        {
                            "intent": intent,
                            "intent_confidence": confidence,
                            "condensed": bool(condensed and condensed != state["query"]),
                            "condensed_query": condensed[:DEFAULT_UNDERSTAND_CONDENSED_QUERY_AUDIT_PREVIEW_LEN],
                            "had_history": _history_meaningful,
                        },
                    )
                    # Cache the successful classification for repeat
                    # queries within the TTL window. ``has_meaningful_history``
                    # changes the prompt body, so only memoise when no
                    # history (the common case) — otherwise the cache
                    # could return a payload computed against different
                    # context.
                    if (
                        _uq_cache is not None
                        and _uq_bot_id
                        and _uq_query
                        and not _history_meaningful
                    ):
                        try:
                            _ttl = int(
                                _get_boot_config(
                                    "understand_query.cache_ttl_s",
                                    DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S,
                                ),
                            )
                        except (TypeError, ValueError):
                            _ttl = DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S
                        await _uq_cache.set(
                            str(_uq_bot_id), _uq_query, update, ttl_s=_ttl,
                        )
                    return update

            await _audit(
                state,
                "intent_extracted",
                {
                    "intent": DEFAULT_INTENT_FALLBACK,
                    "intent_confidence": DEFAULT_INTENT_CONFIDENCE_FALLBACK,
                    "condensed": False,
                    "condensed_query": "",
                    "had_history": _history_meaningful,
                    "fallback": True,
                },
            )
            return {
                "intent": DEFAULT_INTENT_FALLBACK,
                "intent_confidence": DEFAULT_INTENT_CONFIDENCE_FALLBACK,
            }

        except (AttributeError, TypeError):
            logger.exception("understand_query_programmer_bug")
            raise
        except (InvariantViolation, asyncio.TimeoutError, OSError,
                RuntimeError, ValueError, KeyError):
            # Runtime fail: fall back so retrieve+grade can decide.
            logger.warning("understand_query_failed", exc_info=True)
            return {
                "intent": DEFAULT_INTENT_FALLBACK,
                "intent_confidence": DEFAULT_INTENT_CONFIDENCE_FALLBACK,
            }


__all__ = ["understand_query"]
