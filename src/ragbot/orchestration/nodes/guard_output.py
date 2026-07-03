"""Output-guardrail node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no logic / grounding judge / regex guard / refuse text /
state key / ordering / log-event change). The inner ``_grounding_llm`` judge
closure stays nested (it captures node-locals ``_grounding_threshold`` +
``state`` + the LLM DI), exactly as before.

The background-grounding scheduler ``_schedule_grounding_check_background``
lives in ``query_graph`` (module-level); it is threaded in as a kwarg rather
than imported here, to avoid a circular import. Shared helper ``_pcfg`` /
``_resolved_oos_template`` (query_graph-local) are likewise passed in.
``OutputGuardrail`` / ``GuardrailBlocked`` come from their own modules (no
cycle).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import structlog

from ragbot.application.ports.guardrail_port import GuardrailBlocked
from ragbot.infrastructure.guardrails.local_guardrail import OutputGuardrail
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
    DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_GROUNDING_CHECK_THRESHOLD,
    DEFAULT_GROUNDING_CONFIRMED_ACTION,
    DEFAULT_GROUNDING_FAILURE_MODE,
    GROUNDING_CONFIRMED_ACTION_BLOCK,
    DEFAULT_GROUNDING_INTENTS,
    DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE,
    DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
    DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS,
    DEFAULT_SYSPROMPT_LEAK_SKIP_STATS_ROUTE,
    DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
    DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED,
    GROUNDING_FAILURE_MODE_FAIL_OPEN,
)
from ragbot.shared.constants import NUMERIC_FIDELITY_EVENT
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.numeric_fidelity import classify_answer_numbers

logger = structlog.get_logger(__name__)


async def guard_output(
    state: GraphState,
    *,
    llm: Any = None,
    model_resolver: Any = None,
    guardrail: Any = None,
    _schedule_grounding_check_background: Any,
    _pcfg: Any,
    _resolved_oos_template: Any,
) -> dict:
    async with state["step_tracker"].step("guard_output") as guard_ctx:
        flags = list(state.get("guardrail_flags", []))

        # ── Numeric-fidelity OBSERVE (truth-audit Phase 4) ─────────────────
        # Deterministic, model-independent: classify every significant number
        # in the ORIGINAL answer against the served context (graded chunks).
        # Computed ONCE, before any guard branch — so a later block/substitute
        # still records what the LLM actually emitted (catch-rate honesty).
        # OBSERVE-ONLY: the verdict never gates the answer (sacred #10);
        # blocking is a separate owner-gated step after false-positive/catch
        # rates are reviewed (spec FR-010).
        _nf_answer = str(state.get("answer") or "")
        _nf_ctx = [
            str(c.get("text") or c.get("content") or "")
            for c in (state.get("graded_chunks") or [])
        ]
        _nf = classify_answer_numbers(_nf_answer, _nf_ctx)
        state["numeric_fidelity"] = _nf
        if _nf["n_numbers"]:
            logger.info(
                NUMERIC_FIDELITY_EVENT,
                record_bot_id=str(state.get("record_bot_id") or ""),
                trace_id=str(state.get("trace_id") or ""),
                n_numbers=_nf["n_numbers"],
                n_grounded=_nf["n_grounded"],
                n_derived_valid=_nf["n_derived_valid"],
                n_unsupported=_nf["n_unsupported"],
                unsupported_tokens=_nf["unsupported_tokens"],
                context_source=str(state.get("retrieve_mode") or ""),
            )

        # Numeric / citation grounding is the bot owner's responsibility via
        # `system_prompt` (anti-fabricate rules) — the LLM self-checks. The
        # application does NOT regex-check + override the answer here
        # (CLAUDE.md MINDSET #2: "KHÔNG math_lockdown regex check + replace …
        # LLM trả gì = user thấy nấy"). Grounding ratio below is observability
        # only; it never substitutes the answer.
        _grounding_enabled = bool(_pcfg(state, "grounding_check_enabled", DEFAULT_GROUNDING_CHECK_ENABLED))
        # Wave M3.6-L4 2026-05-20: per-intent threshold override.
        # WHY: multi_entity / comparison queries have top_score 0.4-0.7
        # (chunks chứa 1 entity nhưng chưa cover full compare set).
        # Threshold cố định 0.5 gây grounding judge reject oan
        # (verified Q14/Q16/Q17 fail 3/3 runs M3.5-C). Per-intent
        # config nới 0.4 cho 2 intent comparison/multi_entity,
        # giữ 0.5 cho factoid/chitchat/hallu_trap. HALLU sacred 7
        # trap đều intent=hallu_trap → threshold KHÔNG đổi cho trap.
        _base_threshold = float(_pcfg(state, "grounding_check_threshold", DEFAULT_GROUNDING_CHECK_THRESHOLD))
        _threshold_by_intent = _pcfg(state, "grounding_check_threshold_by_intent", None)
        _intent_for_threshold = state.get("intent") or ""
        if isinstance(_threshold_by_intent, dict) and _intent_for_threshold in _threshold_by_intent:
            try:
                _grounding_threshold = float(_threshold_by_intent[_intent_for_threshold])
            except (TypeError, ValueError):
                _grounding_threshold = _base_threshold
        else:
            _grounding_threshold = _base_threshold

        # Intent-gated grounding judge skips non-retrieval intents to save tail latency.
        _grounding_intents_cfg = _pcfg(state, "grounding_intents", DEFAULT_GROUNDING_INTENTS)
        if isinstance(_grounding_intents_cfg, (list, tuple)) and _grounding_intents_cfg:
            _grounding_intents = tuple(str(x) for x in _grounding_intents_cfg)
        else:
            _grounding_intents = DEFAULT_GROUNDING_INTENTS
        _current_intent = state.get("intent") or ""
        _grounding_eligible = _current_intent in _grounding_intents
        # Stats/aggregation route answers come from the authoritative structured
        # index (exact price/quantity/date SQL). Skipping grounding on them
        # reformats-friendly BUT let a HALLU breach through once (a stock number
        # leaked from history cited on a stats answer). So grounding
        # STAYS ON for stats by default (DEFAULT_STATS_ROUTE_SKIP_GROUNDING=False,
        # HALLU-safe); a bot that hits the reformat false-block can opt out via
        # plan_limits.stats_route_skip_grounding.
        _stats_skip_grounding = bool(
            _pcfg(state, "stats_route_skip_grounding", DEFAULT_STATS_ROUTE_SKIP_GROUNDING)
        )
        if _stats_skip_grounding and str(state.get("retrieve_mode") or "").startswith("stats"):
            _grounding_eligible = False
        _grounding_check_skipped = bool(
            _grounding_enabled and not _grounding_eligible
        )

        # B5 Phase B: async grounding decision. Eligible iff
        #   (a) grounding sync gate would have fired (enabled + intent eligible)
        #   (b) bot owner opted in (plan_limits.grounding_check_async_enabled)
        #   (c) current intent is in the async-eligible subset (factoid only)
        #   (d) pass-1 top retrieval score >= async_top_score_threshold
        # When all four hold the sync LLM judge is suppressed (llm_fn=None)
        # and a background task is scheduled after the sync guardrails
        # complete. Breach (judge ratio > threshold) is logged + metric.
        _async_enabled_cfg = bool(_pcfg(
            state,
            "grounding_check_async_enabled",
            DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
        ))
        _async_intents_cfg = _pcfg(
            state,
            "grounding_check_async_intents",
            DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
        )
        if isinstance(_async_intents_cfg, (list, tuple)) and _async_intents_cfg:
            _async_intents = tuple(str(x) for x in _async_intents_cfg)
        else:
            _async_intents = DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS
        _async_top_score_floor = float(_pcfg(
            state,
            "grounding_check_async_top_score_threshold",
            DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
        ))
        _async_pool = state.get("graded_chunks") or state.get("reranked_chunks") or []
        _async_top_score = 0.0
        for _c in _async_pool:
            try:
                _s = float(_c.get("score", 0) or 0)
            except (TypeError, ValueError):
                _s = 0.0
            if _s > _async_top_score:
                _async_top_score = _s
        _grounding_async = bool(
            _grounding_enabled
            and _grounding_eligible
            and _async_enabled_cfg
            and _current_intent in _async_intents
            and _async_top_score >= _async_top_score_floor
            and model_resolver is not None
            and llm is not None
        )

        llm_fn = None
        if (
            _grounding_enabled
            and _grounding_eligible
            and not _grounding_async
            and model_resolver is not None
            and llm is not None
        ):
            async def _grounding_llm(messages: list[dict]) -> dict:
                async with state["step_tracker"].step("grounding_check") as gc_ctx:
                    try:
                        cfg = await model_resolver.resolve_runtime(
                            record_tenant_id=state.get("record_tenant_id"),
                            record_bot_id=state.get("record_bot_id"),
                            purpose="grounding",
                        )
                    except InvariantViolation as exc:
                        logger.warning(
                            "model_resolver_no_binding",
                            purpose="grounding",
                            record_bot_id=str(state.get("record_bot_id")),
                            node="grounding_check",
                            error=str(exc)[:200],
                        )
                        # asyncio.gather(..., return_exceptions=True) upstream
                        # catches this; grounding branch degrades to skip.
                        raise
                    out = await llm.complete(cfg, messages=messages)
                    gc_ctx.set_metadata(
                        threshold=_grounding_threshold,
                        model=getattr(cfg, "litellm_name", None) or getattr(cfg, "model_name", "") or "",
                        messages=len(messages),
                        finish_reason=(out.get("finish_reason") if isinstance(out, dict) else None) or "",
                    )
                    # Wave M3.7-P2 — record grounding LLM cost.
                    # WHY: grounding_check is HALLU sacred guard; its
                    # LLM call (~0.8s p50) was untracked in
                    # request_steps. Now attributed to its row so
                    # cost dashboard knows the guard's spend.
                    _gc_model = (
                        (out.get("model_name") if isinstance(out, dict) else None)
                        or getattr(cfg, "litellm_name", None)
                        or getattr(cfg, "model_name", "")
                        or ""
                    )
                    gc_ctx.record_llm(
                        model_used=str(_gc_model) or None,
                        prompt_tokens=int(
                            (out.get("prompt_tokens") if isinstance(out, dict) else 0) or 0
                        ),
                        completion_tokens=int(
                            (out.get("completion_tokens") if isinstance(out, dict) else 0) or 0
                        ),
                        cost_usd=float(
                            (out.get("cost_usd") if isinstance(out, dict) else 0.0) or 0.0
                        ),
                    )
                    return out
            llm_fn = _grounding_llm

        # AG-A2 — grounding-net fail-CLOSED. The HALLU net was silently OFF
        # whenever the grounding judge could NOT run: grounding is enabled and
        # the intent is grounding-eligible (so the answer SHOULD have been
        # verified), the per-bot async ship-then-check path is NOT in play, yet
        # ``llm_fn`` is None — the LLM runtime is unwired (model_resolver / llm
        # missing). Previously the node returned the answer UNVERIFIED. Default
        # ``fail_closed`` substitutes the bot's ``oos_answer_template`` (the
        # existing refuse branch) instead of shipping an ungrounded answer,
        # honouring HALLU=0 sacred. Bot owner opts back into the legacy
        # pass-through per-bot via ``plan_limits.grounding_failure_mode =
        # "fail_open"``. This is NOT an answer-override (sacred-rule 10): it is
        # the same refuse-with-template path the output guardrail already uses
        # on a block.
        _grounder_dead = bool(
            _grounding_enabled
            and _grounding_eligible
            and not _grounding_async
            and llm_fn is None
        )
        _grounding_failure_mode = str(
            _pcfg(state, "grounding_failure_mode", DEFAULT_GROUNDING_FAILURE_MODE)
            or DEFAULT_GROUNDING_FAILURE_MODE
        )
        _grounding_fail_closed = _grounder_dead and (
            _grounding_failure_mode != GROUNDING_FAILURE_MODE_FAIL_OPEN
        )

        _leak_shingle_size = int(_pcfg(state, "guardrail_leak_shingle_size", DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE))
        _leak_min_match = int(_pcfg(state, "guardrail_leak_min_match_count", DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT))
        _sys_prompt = state.get("system_prompt", "")
        # Persona intents (greeting/identity) may legitimately echo the
        # sysprompt persona — skip the leak shingle so the intro the owner
        # asked for is not false-blocked as a system_prompt leak.
        _leak_skip_intents = _pcfg(
            state, "sysprompt_leak_skip_intents", DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS,
        )
        _leak_skip = (
            isinstance(_leak_skip_intents, (list, tuple))
            and str(state.get("intent") or "") in _leak_skip_intents
        )
        # Stats/structured route: the synthetic chunk is pipe-delimited, so the
        # doc-shingle subtraction below cannot cover the LLM's reworded prose
        # relay — any 24-word overlap with the owner's answer-template sentences
        # in the system_prompt then false-blocks a correct factoid. Skip the leak
        # shingle on this authoritative route (mirrors the grounding skip above;
        # the route never serves a prompt-extraction attack). Per-bot overridable.
        if str(state.get("retrieve_mode") or "").startswith("stats") and bool(
            _pcfg(
                state,
                "sysprompt_leak_skip_stats_route",
                DEFAULT_SYSPROMPT_LEAK_SKIP_STATS_ROUTE,
            )
        ):
            _leak_skip = True
        _sys_prompt_hash: list[str] | None = None
        if _sys_prompt and not _leak_skip:
            _words = _sys_prompt.split()
            if len(_words) >= _leak_shingle_size:
                _sys_prompt_hash = [
                    hashlib.sha256(" ".join(_words[i:i + _leak_shingle_size]).encode()).hexdigest()
                    for i in range(len(_words) - _leak_shingle_size + 1)
                ]
            else:
                _sys_prompt_hash = [hashlib.sha256(_sys_prompt.encode()).hexdigest()]

        # Doc-grounded exclusion (default-ON, every bot): a shingle that ALSO
        # appears in the retrieved <documents> is a legitimate corpus relay,
        # not a system-prompt leak. Subtract document shingles so the leak
        # guard only fires on sysprompt content NOT grounded in the docs.
        # Generic replacement for per-bot literal scrubbing — a bot that
        # relays an owner fact present in both its system_prompt and its
        # corpus is no longer false-blocked (forensic 2026-06-05).
        if _sys_prompt_hash and len(_sys_prompt_hash) > 1:
            _doc_shingles: set[str] = set()
            for _ch in (state.get("graded_chunks") or state.get("reranked_chunks") or []):
                _ctext = (_ch.get("content") or _ch.get("text") or "") if isinstance(_ch, dict) else ""
                _cw = _ctext.split()
                for _i in range(len(_cw) - _leak_shingle_size + 1):
                    _doc_shingles.add(
                        hashlib.sha256(" ".join(_cw[_i:_i + _leak_shingle_size]).encode()).hexdigest()
                    )
            if _doc_shingles:
                _sys_prompt_hash = [h for h in _sys_prompt_hash if h not in _doc_shingles]

        # Parallel is enabled when EITHER legacy flag OR new flag is set;
        # a per-bot explicit False on the legacy flag overrides both since
        # the legacy flag is the existing ops knob operators use today.
        # When the new `guard_output_parallel_enabled` key is explicitly
        # set in pipeline_config it takes precedence as the canonical name;
        # fall back to the legacy key so existing bots keep working.
        _guard_output_parallel_cfg = _pcfg(state, "guard_output_parallel_enabled", None)
        if _guard_output_parallel_cfg is not None:
            # Explicit new key → canonical
            _parallel_enabled = bool(_guard_output_parallel_cfg)
        else:
            # No new key → legacy key with its default
            _parallel_enabled = bool(
                _pcfg(
                    state,
                    "pipeline_parallel_output_guards_enabled",
                    DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED,
                )
            )
        # Parallel path runs the LLM grounding judge as a sibling task
        # to the regex output guards (system_prompt_leak, secret_scanner,
        # citation marker check). Both branches write only guardrail_flags
        # (list), merged additively — no state-clobber risk. Falls back
        # to serial when grounding is disabled or ineligible (no judge
        # to parallelise with).
        _will_parallel = (
            _parallel_enabled
            and _grounding_enabled
            and _grounding_eligible
            and llm_fn is not None
        )

        guard_ctx.set_metadata(
            grounding_check_skipped=_grounding_check_skipped,
            grounding_eligible=_grounding_eligible,
            grounding_check_async=_grounding_async,
            grounding_check_async_top_score=round(_async_top_score, 4),
            intent=_current_intent,
            parallel_enabled=_will_parallel,
            grounder_dead=_grounder_dead,
            grounding_failure_mode=_grounding_failure_mode,
        )

        _oos_template = _resolved_oos_template(state)
        _oos_threshold = float(
            _pcfg(
                state,
                "guardrail_oos_similarity_threshold",
                DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
            )
        )
        _citation_marker_required = bool(_pcfg(state, "citation_marker_required", False))

        # AG-A2 fail-CLOSED action point. The grounding judge could not run for
        # a grounding-eligible answer; refuse rather than ship UNVERIFIED. Done
        # here (after the oos template resolves, before the regex/parallel
        # guards run) so the substitution reuses the node's existing refuse
        # contract — same shape the GuardrailBlocked path returns.
        if _grounding_fail_closed:
            logger.warning(
                "grounding_fail_closed",
                request_id=str(state.get("request_id") or ""),
                record_bot_id=str(state.get("record_bot_id") or ""),
                intent=_current_intent,
                reason="grounder_unwired",
            )
            flags.append(
                {
                    "stage": "output",
                    "rule_id": "grounding_fail_closed",
                    "severity": "block",
                    "action": "block",
                    "blocked": True,
                }
            )
            return {
                "guardrail_flags": flags,
                "numeric_fidelity": _nf,
                "answer": _oos_template,
                "answer_type": "blocked",
                "answer_reason": "Grounding unavailable (fail_closed)",
            }

        if _will_parallel:
            # Task A: regex-only check_output (grounding disabled so the
            # serial call returns instantly after the regex passes).
            regex_task = asyncio.create_task(
                guardrail.check_output(
                    state.get("answer", ""),
                    system_prompt_hash=_sys_prompt_hash,
                    shingle_size=_leak_shingle_size,
                    retrieved_chunks=state.get("graded_chunks"),
                    tenant_id=state.get("record_tenant_id"),
                    message_id=state["message_id"],
                    request_id=state.get("request_id"),
                    grounding_check_enabled=False,
                    grounding_check_threshold=_grounding_threshold,
                    citation_marker_required=_citation_marker_required,
                    llm_complete_fn=None,
                    oos_template=_oos_template,
                    oos_similarity_threshold=_oos_threshold,
                    leak_min_match_count=_leak_min_match,
                )
            )
            # Task B: standalone LLM grounding judge. Bypasses
            # guardrail.check_output entirely so it does not double-persist
            # the regex hits; we persist this hit separately below.
            grounding_task = asyncio.create_task(
                OutputGuardrail.llm_grounding_check(
                    state.get("answer", ""),
                    state.get("graded_chunks") or [],
                    llm_fn,
                    threshold=_grounding_threshold,
                )
            )

            regex_result, grounding_result = await asyncio.gather(
                regex_task, grounding_task, return_exceptions=True
            )

            grounding_hit = None
            if isinstance(grounding_result, BaseException):
                logger.warning(
                    "parallel_grounding_branch_failed", exc_info=grounding_result
                )
                flags.append(
                    {
                        "stage": "output",
                        "rule_id": "parallel_error",
                        "severity": "info",
                        "action": "log",
                        "branch": "grounding",
                        "error_type": type(grounding_result).__name__,
                    }
                )
            else:
                grounding_hit = grounding_result

            if isinstance(regex_result, GuardrailBlocked):
                # Regex side blocked (e.g. system_prompt_leak) — short-circuit.
                for h in regex_result.hits:
                    flags.append(
                        {
                            "stage": "output",
                            "rule_id": h.rule_id,
                            "severity": h.severity,
                            "action": h.action,
                            "blocked": True,
                        }
                    )
                # Persist grounding_hit too so the audit trail captures
                # both branches even when regex blocks.
                if grounding_hit is not None:
                    flags.append(
                        {
                            "stage": "output",
                            "rule_id": grounding_hit.rule_id,
                            "severity": grounding_hit.severity,
                            "action": grounding_hit.action,
                        }
                    )
                    await guardrail._persist(
                        [grounding_hit],
                        guardrail_type="output",
                        tenant_id=state.get("record_tenant_id"),
                        message_id=state["message_id"],
                        request_id=state.get("request_id"),
                    )
                return {
                    "guardrail_flags": flags,
                    "numeric_fidelity": _nf,
                    "answer": _oos_template,
                    "answer_type": "blocked",
                    "answer_reason": "Output guardrail blocked",
                }
            if isinstance(regex_result, BaseException):
                logger.warning(
                    "parallel_regex_branch_failed", exc_info=regex_result
                )
                flags.append(
                    {
                        "stage": "output",
                        "rule_id": "parallel_error",
                        "severity": "info",
                        "action": "log",
                        "branch": "regex",
                        "error_type": type(regex_result).__name__,
                    }
                )
            else:
                for h in regex_result:
                    flags.append(
                        {
                            "stage": "output",
                            "rule_id": h.rule_id,
                            "severity": h.severity,
                            "action": h.action,
                        }
                    )

            if grounding_hit is not None:
                flags.append(
                    {
                        "stage": "output",
                        "rule_id": grounding_hit.rule_id,
                        "severity": grounding_hit.severity,
                        "action": grounding_hit.action,
                    }
                )
                await guardrail._persist(
                    [grounding_hit],
                    guardrail_type="output",
                    tenant_id=state.get("record_tenant_id"),
                    message_id=state["message_id"],
                    request_id=state.get("request_id"),
                )
                # A1 symmetric gate: when the judge CONFIRMS the answer is
                # ungrounded and the bot opted into block, substitute the bot's
                # oos_answer_template — the SAME sacred-#10-safe path a regex
                # block takes (bot's own refusal text, never app-injected).
                # Default "observe" preserves the legacy flag-and-ship behaviour;
                # block is per-bot opt-in (the grounding threshold's deliberate
                # false-positive bias means an un-measured default block would
                # over-refuse grounded answers).
                _grounding_confirmed_action = str(
                    _pcfg(
                        state,
                        "grounding_confirmed_action",
                        DEFAULT_GROUNDING_CONFIRMED_ACTION,
                    )
                    or DEFAULT_GROUNDING_CONFIRMED_ACTION
                )
                if _grounding_confirmed_action == GROUNDING_CONFIRMED_ACTION_BLOCK:
                    flags[-1]["blocked"] = True
                    return {
                        "guardrail_flags": flags,
                        "numeric_fidelity": _nf,
                        "answer": _oos_template,
                        "answer_type": "blocked",
                        "answer_reason": "Grounding judge confirmed ungrounded answer",
                    }
            return {"guardrail_flags": flags, "numeric_fidelity": _nf}

        try:
            hits = await guardrail.check_output(
                state.get("answer", ""),
                system_prompt_hash=_sys_prompt_hash,
                shingle_size=_leak_shingle_size,
                retrieved_chunks=state.get("graded_chunks"),
                tenant_id=state.get("record_tenant_id"),
                message_id=state["message_id"],
                request_id=state.get("request_id"),
                grounding_check_enabled=_grounding_enabled,
                grounding_check_threshold=_grounding_threshold,
                citation_marker_required=_citation_marker_required,
                llm_complete_fn=llm_fn,
                oos_template=_oos_template,
                oos_similarity_threshold=_oos_threshold,
                leak_min_match_count=_leak_min_match,
            )
            for h in hits:
                flags.append(
                    {
                        "stage": "output",
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action": h.action,
                    }
                )

            # B5 Phase B: schedule background grounding judge AFTER the
            # sync guardrails finished. The judge runs detached — the
            # caller proceeds to persist + return the response. Breach
            # is logged (structlog) + emits ``grounding_fail_total`` so
            # alerting picks it up out-of-band.
            if _grounding_async:
                _schedule_grounding_check_background(
                    state=state,
                    threshold=_grounding_threshold,
                    top_score=_async_top_score,
                    model_resolver=model_resolver,
                    llm=llm,
                )


            return {"guardrail_flags": flags, "numeric_fidelity": _nf}
        except GuardrailBlocked as exc:
            for h in exc.hits:
                flags.append(
                    {
                        "stage": "output",
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action": h.action,
                        "blocked": True,
                    }
                )
            return {
                "guardrail_flags": flags,
                "numeric_fidelity": _nf,
                # Output guardrail blocked: substitute bot's oos_answer_template (regen would be unsafe).
                "answer": _oos_template,
                "answer_type": "blocked", "answer_reason": "Output guardrail blocked",
            }


__all__ = ["guard_output"]
