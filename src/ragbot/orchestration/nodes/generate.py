"""Answer-generation node (lifted from ``build_graph``).

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no logic / prompt assembly / LLM call / citation handling /
refuse short-circuit / drift detection / state key / ordering / log-event
change).

SACRED-RULE PRESERVATION: this node assembles the LLM prompt + reads the LLM
answer verbatim. Application MUST NOT inject text into the prompt nor override
the answer — the body here is identical to its former nested-closure form.

Domain-neutral module-level collaborators (cascade router, prompt compression,
token-opt, context reorder, output-cap, intent-purpose resolver, metrics,
constants) are imported directly. Shared helper closures (``_audit``,
``_invoke_llm_node``, ``_invoke_structured_llm_node``, ``_so_usage``) and the
query_graph-local helpers (``_pcfg``, ``_lang``, ``_oos_text``,
``_resolve_xml_wrap_enabled``, ``_resolve_generate_schema``,
``_render_captured_slots``, ``_CITATION_RE``) are threaded in as kwargs
(importing the latter here would create a circular import).
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog

from ragbot.application.ports.guardrail_port import GuardrailBlocked
from ragbot.application.services.model_resolver import (
    resolve_purpose_for_intent as _resolve_purpose_for_intent,
)
from ragbot.orchestration.nodes.cascade_router_helper import apply_cascade_routing
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import (
    ACTION_CAPTURED_SLOTS_PLACEHOLDER,
    NARRATE_METADATA_KEY_RAW_CHUNK,
    DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
    DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS,
    DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE,
    DEFAULT_ADAPTIVE_CONTEXT_MAX_N,
    DEFAULT_CASCADE_ROUTING_ENABLED,
    DEFAULT_CHUNK_TYPE_TEXT,
    DEFAULT_GENERATE_CONTEXT_CHARS_CAP,
    DEFAULT_GENERATE_CONTEXT_TRUST_HINT_ENABLED,
    DEFAULT_GENERATE_HISTORY_MAX_MSGS,
    DEFAULT_GENERATE_P95_SLA_MS,
    DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED,
    DEFAULT_GENERATE_USE_STRUCTURED_OUTPUT,
    DEFAULT_GENERATE_VERBATIM_TAG,
    DEFAULT_GROUNDING_INTENTS,
    DEFAULT_LANGUAGE,
    DEFAULT_LITM_REORDER_ENABLED,
    DEFAULT_OOS_ANSWER_TEMPLATE,
    DEFAULT_OUTPUT_TOKENS_PER_RESPONSE,
    DEFAULT_PROMPT_COMPRESSION_ENABLED,
    DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
    DEFAULT_PROMPT_TOKEN_OPT_ENABLED,
    DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
    DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
    INTENT_CHITCHAT,
    MAX_HISTORY_MESSAGE_CHARS,
)
from ragbot.shared.context_utils import (
    apply_context_char_cap,
    reorder_for_lost_in_middle,
)
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.prompt_compression import compress_chunks
from ragbot.shared.prompt_token_opt import apply_token_opt
from ragbot.shared.token_budget import compute_output_cap

logger = structlog.get_logger(__name__)

try:
    from ragbot.infrastructure.observability.metrics import (
        citation_validation_fail_total,
    )
except ImportError:
    citation_validation_fail_total = None  # type: ignore[assignment]

try:
    from ragbot.infrastructure.observability.metrics import (
        llm_resolved_purpose_total,
    )
except ImportError:
    llm_resolved_purpose_total = None  # type: ignore[assignment]


_PRICE_CELL_RE = re.compile(r"^[\d.,]{4,}$")


def _is_empty_answer(answer: str | None) -> bool:
    """True when a success-path answer is blank (OBS-1 silent-failure signal).

    The LLM completed (status=success) yet returned no usable content —
    ``None``, empty, or whitespace-only. Pure decision function so the
    observability branch in the node can be unit-tested without graph DI.
    Detection ONLY — never used to author or substitute answer text
    (sacred-rule #10).
    """
    return not (answer or "").strip()


def _resolve_verbatim_fence(
    chunk: dict,
    chunk_meta: dict,
    fenced_text: str,
    *,
    enabled: bool,
    tag: str,
) -> str:
    """Resolve the read-only VERBATIM segment to append inside a chunk's
    context fence (F5 dual-read close), or ``""`` when nothing should change.

    The verbatim original (exact table grid / formula source) is stored at
    ingest under ``Chunk.original_content`` and, for the narrate-then-embed
    path, in chunk metadata ``raw_chunk``. Resolution precedence — first
    non-empty wins:
      1. ``chunk["original_content"]`` (entity field / compression-preserved)
      2. ``metadata["original_content"]``
      3. ``metadata[NARRATE_METADATA_KEY_RAW_CHUNK]`` (narrate storage)

    Returns ``""`` (so the fence is byte-identical to its current form) when:
      - the feature flag is off,
      - no verbatim is present, or
      - the verbatim equals the already-fenced text (no duplication).

    Otherwise returns a leading-newline data-only segment
    ``"\n<{tag}>…</{tag}>"``. This is ingest data surfaced READ-ONLY into the
    data envelope — it carries no instruction text and never alters the LLM
    answer (sacred-rule 10). Domain-neutral: SHAPE only, no brand/format
    literal.
    """
    if not enabled:
        return ""
    verbatim = (
        chunk.get("original_content")
        or chunk_meta.get("original_content")
        or chunk_meta.get(NARRATE_METADATA_KEY_RAW_CHUNK)
        or ""
    )
    verbatim = verbatim if isinstance(verbatim, str) else ""
    if not verbatim or verbatim == fenced_text:
        return ""
    return f"\n<{tag}>{verbatim}</{tag}>"


def _extract_locked_prices(
    preview: str, service_lower: str,
) -> tuple[str | None, str | None]:
    """Extract (primary, secondary) price strings for a service from its source
    chunk, for the cross-turn price-lock. Delimiter-aware: splits each line on BOTH
    pipe and comma, so it works for happy-case markdown tables (``| name | price |``)
    AND prior CSV rows (``name,price``). Returns the first two price-shaped cells on
    the line that names the service; ``(None, None)`` when absent. Domain-neutral:
    SHAPE only — no service/brand literal."""
    for line in preview.splitlines():
        if service_lower not in line.lower():
            continue
        cells = [c.strip() for c in re.split(r"[,|]", line)]
        prices = [c for c in cells if _PRICE_CELL_RE.match(c)]
        if prices:
            return prices[0], (prices[-1] if len(prices) > 1 else None)
    return None, None


async def generate(
    state: GraphState,
    *,
    llm: Any = None,
    model_resolver: Any = None,
    conversation_state: Any = None,
    slot_extractor: Any = None,
    _audit: Any,
    _invoke_llm_node: Any,
    _invoke_structured_llm_node: Any,
    _so_usage: Any,
    _pcfg: Any,
    _lang: Any,
    _oos_text: Any,
    _resolve_xml_wrap_enabled: Any,
    _resolve_generate_schema: Any,
    _render_captured_slots: Any,
    _CITATION_RE: Any,
) -> dict:
    # Tracks elapsed for SLA-breach warning; observability-only.
    _generate_t0 = time.monotonic()
    async with state["step_tracker"].step("generate", model_used=state.get("model_used")) as _gen_ctx:
        if model_resolver is None or llm is None:
            raise InvariantViolation("LLM runtime not configured for node=generation")
        graded = state.get("graded_chunks") or []
        await _audit(
            state,
            "generate_started",
            {
                "context_chunks": len(graded),
                "context_chars": sum(
                    len(c.get("content") or c.get("text") or "") for c in graded
                ),
                "answer_already_set": bool(state.get("answer")),
            },
        )

        # Tier 2 conversation state (X2 BUNDLED, alembic 0150). When the
        # bot owner has opted in via ``bots.action_config.enabled=true``
        # (resolved into ``pipeline_config["action_config"]`` by the
        # entry-point builder), load the prior turn's state and extract
        # slots from the current user message. State is read by the LLM
        # via the existing sysprompt template pattern (rule 20+21+22 in
        # ``language_packs.sysprompt_default_rules``, alembic 0151) —
        # application code does NOT prepend any text or override the
        # answer (sacred-rule preservation).
        _action_cfg = _pcfg(state, "action_config", {}) or {}
        _action_enabled = bool(_action_cfg.get("enabled")) if isinstance(_action_cfg, dict) else False
        _action_state_prior: dict = state.get("action_state") or {}
        _action_state_new: dict = dict(_action_state_prior)

        if (
            _action_enabled
            and conversation_state is not None
            and slot_extractor is not None
        ):
            conv_id = state.get("conversation_id")
            # 1) Load prior state from DB (Tier 2 backend)
            try:
                _action_state_prior = await conversation_state.load_state(
                    conversation_id=conv_id,
                )
                _action_state_new = dict(_action_state_prior)
            except Exception as exc:  # noqa: BLE001 — graceful degrade
                logger.debug(
                    "action_state_load_failed",
                    error=str(exc), error_type=type(exc).__name__,
                )

            # 2) Extract slots from current user turn — use the RAW user
            #    message (``original_query``), NOT the rewritten/condensed
            #    ``query``. Root cause 2026-06-15: condense rewrote a bare
            #    slot turn "Tên Lan" into the question "Tên Lan là gì?", so
            #    the slot extractor saw a question and returned {} → empty
            #    {captured_slots} → the LLM treated it as an OOS query and
            #    refused (measured 5/5). Slots come from what the user
            #    literally typed, never from the search-rewritten query.
            try:
                _intent_for_slots = state.get("intent") or ""
                _slot_source_msg = (
                    state.get("raw_user_message")
                    or state.get("original_query")
                    or state.get("query", "")
                    or ""
                )
                _new_slots = await slot_extractor.extract(
                    user_message=_slot_source_msg,
                    slot_schema=_action_cfg.get("slots_schema", {}),
                    intent=_intent_for_slots,
                )
            except Exception as exc:  # noqa: BLE001 — graceful degrade
                _new_slots = {}
                logger.debug(
                    "slot_extraction_failed",
                    error=str(exc), error_type=type(exc).__name__,
                )

            # 3) Merge slots — first turn pins service_locked from chunks
            _prior_filled: dict = _action_state_prior.get("slots_filled", {}) or {}
            _merged_filled = {**_prior_filled, **(_new_slots or {})}
            _action_state_new["slots_filled"] = _merged_filled
            if _intent_for_slots:
                _action_state_new["intent"] = _intent_for_slots

            # 4) Service lock — first time a "service" slot is filled
            #    (and chunks confirm it), record literal name + price
            #    from the source chunk. Future turns MUST honour this.
            _locked = _action_state_new.get("service_locked") or {}
            _detected_service = (_new_slots or {}).get("service")
            if (
                _detected_service
                and not _locked.get("name")
                and graded
            ):
                _service_lower = str(_detected_service).strip().lower()
                for chunk in graded[:5]:
                    preview = (chunk.get("content") or chunk.get("preview") or "")
                    if _service_lower in preview.lower():
                        _locked_entry: dict = {
                            "name": str(_detected_service),
                            "source_chunk_id": chunk.get("chunk_id", ""),
                            "locked_at_turn": state.get("message_id"),
                        }
                        # Capture the service's price from its source chunk for the
                        # cross-turn price-lock. Delimiter-aware (happy-case markdown
                        # "| name | price | price |" OR prior CSV row) — generic
                        # primary/secondary keys, no domain literal.
                        _pp, _ps = _extract_locked_prices(preview, _service_lower)
                        if _pp:
                            _locked_entry["price_primary"] = _pp
                            if _ps:
                                _locked_entry["price_secondary"] = _ps
                        _action_state_new["service_locked"] = _locked_entry
                        break

            state["action_state"] = _action_state_new

            await _audit(state, "action_state_loaded", {
                "enabled": True,
                "has_service_locked": bool(_action_state_new.get("service_locked")),
                "slots_filled_count": len(_action_state_new.get("slots_filled") or {}),
            })

        # Refuse short-circuit when zero graded chunks: return bot's oos_answer_template, skip LLM.
        _refuse_sc_enabled = bool(_pcfg(
            state,
            "refuse_short_circuit_enabled",
            DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
        ))
        # Chitchat bypass: trust upstream intent classifier only. Pattern heuristic
        # (token-count + trap-keyword) was misclassifying short factoid queries
        # ("có gì cho mặt") as chitchat → drops <documents> block in generate.
        _intent = state.get("intent") or ""
        _is_chitchat = _intent in INTENT_CHITCHAT
        # Action/booking bypass: when action_config is ON, a 0-chunk turn must
        # NOT hard short-circuit to the oos template. Booking is conversational
        # — bare-slot turns ("Tên Lan", "0901234567") legitimately retrieve no
        # document chunk, yet the hard refuse fired on them (measured 5/5,
        # 2026-06-15). Delegate the 0-chunk decision to the LLM instead: the
        # owner's anti-fabricate sysprompt refuses genuine out-of-scope turns
        # itself, the booking rules continue the slot-fill dialog, and the
        # downstream grounding judge still guards HALLU=0. Generic for every
        # action bot; reads existing flag, no per-bot logic.
        _action_bypass_refuse = _action_enabled
        if (
            _refuse_sc_enabled
            and not graded
            and not _is_chitchat
            and not _action_bypass_refuse
        ):
            _bot_template = _oos_text(state)
            _template = _bot_template or DEFAULT_OOS_ANSWER_TEMPLATE
            _template_source = (
                "bot_oos_template" if _bot_template else "default_constant"
            )
            await _audit(
                state,
                "refuse_short_circuit_fired",
                {
                    "template_source": _template_source,
                    "template_chars": len(_template),
                },
            )
            return {
                "answer": _template,
                "answer_type": "no_context",
                "answer_reason": "no_chunks_short_circuit",
                "chunks_used": 0,
            }

        # Cascade Routing wire (CT-2, builds on WA-2 helper).
        # Gated on per-bot ``cascade_routing_enabled`` (default OFF).
        # The helper consults ``state["complexity_score"]`` (written by
        # query_complexity_node upstream) and asks the resolver for a
        # tier-matched answer model. Missing flag / missing score /
        # resolver gap all degrade silently to the unchanged current
        # model (graceful degradation contract). Application MUST NOT
        # override the LLM answer — only the model CHOICE changes here.
        #
        # Wire reads ``pipeline_config`` (= resolved per-bot plan_limits
        # snapshot loaded earlier in the pipeline) so the helper does
        # not need a heavyweight ``state["bot"]`` DTO that this graph
        # never stores (verified post-Wave-D pilot).
        try:
            _cascade_current_model = (
                state.get("model_used") or state.get("resolved_answer_model") or ""
            )
            _cascade_enabled = bool(_pcfg(
                state,
                "cascade_routing_enabled",
                DEFAULT_CASCADE_ROUTING_ENABLED,
            ))
            # Diagnostic INFO at every wire entry — Wave D pilot
            # showed the cascade_routing_applied event missing in
            # journal despite multiple fixes; this trace pinpoints
            # whether the wire is reached + the flag state + the
            # complexity_score available at that moment. Drop to
            # DEBUG after Wave E pilot when the chain is verified
            # observable end-to-end.
            logger.info(
                "cascade_routing_wire_entered",
                bot_id=str(state.get("bot_id") or ""),
                enabled=_cascade_enabled,
                current_model=_cascade_current_model or "<empty>",
                complexity_score_present=("complexity_score" in state),
                complexity_score=float(state.get("complexity_score") or 0.0),
            )
            if _cascade_enabled:
                _cascade_resolved = apply_cascade_routing(
                    state,
                    model_resolver,
                    current_model=_cascade_current_model,
                )
                if (
                    _cascade_resolved
                    and _cascade_resolved != _cascade_current_model
                ):
                    state["resolved_answer_model"] = _cascade_resolved
                    logger.info(
                        "cascade_routing_applied",
                        complexity_score=float(
                            state.get("complexity_score") or 0.0,
                        ),
                        resolved_model=_cascade_resolved,
                        bot_id=str(state.get("bot_id") or ""),
                        previous_model=_cascade_current_model or "",
                    )
        except Exception:  # noqa: BLE001 — cascade hint must not kill answer
            logger.warning(
                "cascade_routing_wire_failed",
                bot_id=str(state.get("bot_id") or ""),
                exc_info=True,
            )

        _prompt_compressed = False
        if _pcfg(
            state,
            "prompt_compression_enabled",
            DEFAULT_PROMPT_COMPRESSION_ENABLED,
        ) and graded:
            _comp_max = _pcfg(
                state,
                "prompt_compression_max_chars_per_chunk",
                DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
            )
            async with state["step_tracker"].step("prompt_compression") as pc_ctx:
                _pre_chars = sum(
                    len(c.get("content") or c.get("text") or "") for c in graded
                )
                try:
                    _pc_lang = str(
                        state.get("language", DEFAULT_LANGUAGE)
                        or DEFAULT_LANGUAGE,
                    )
                    graded = compress_chunks(
                        graded,
                        max_chars_per_chunk=int(_comp_max),
                        remove_boilerplate=True,
                        preserve_key_info=True,
                        language=_pc_lang,
                    )
                    _status = "applied"
                    _prompt_compressed = True
                except Exception:  # noqa: BLE001
                    logger.warning("prompt_compression_failed", exc_info=True)
                    _status = "failed"
                _post_chars = sum(
                    len(c.get("content") or c.get("text") or "") for c in graded
                )
                pc_ctx.set_metadata(
                    chunks=len(graded),
                    max_chars_per_chunk=int(_comp_max),
                    chars_before=_pre_chars,
                    chars_after=_post_chars,
                    status=_status,
                )

        # Adaptive context-sizing: when retrieval is clearly strong, fewer
        # chunks reduce summarisation pressure → less drop-fact on multi-part
        # answers. Gated high + keeps safety-injected chunks so a strong
        # retrieval is never turned into an answer gap. Default OFF (rule #0
        # A/B before default). graded is rerank-score-sorted descending.
        if (
            _pcfg(state, "adaptive_context_enabled", DEFAULT_ADAPTIVE_CONTEXT_ENABLED)
            and (state.get("intent") or "") not in DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS
            and len(graded) > int(_pcfg(state, "adaptive_context_max_n", DEFAULT_ADAPTIVE_CONTEXT_MAX_N))
        ):
            _ac_top = float(graded[0].get("score", 0) or 0) if graded else 0.0
            _ac_hi = float(_pcfg(state, "adaptive_context_high_score", DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE))
            if _ac_top >= _ac_hi:
                _ac_n = int(_pcfg(state, "adaptive_context_max_n", DEFAULT_ADAPTIVE_CONTEXT_MAX_N))
                _ac_keep = graded[:_ac_n] + [
                    c for c in graded[_ac_n:] if c.get("_safety_injected")
                ]
                if len(_ac_keep) < len(graded):
                    await _audit(state, "adaptive_context_pruned", {
                        "before": len(graded), "after": len(_ac_keep), "top_score": round(_ac_top, 4),
                    })
                    graded = _ac_keep

        # Lost-in-the-middle reorder is applied LATER — AFTER token-opt +
        # char-cap have trimmed the set on the score-DESCENDING order. Reordering
        # here (before the cap) would place high-relevance chunks at the tail, and
        # the char-cap drops from the tail → it would discard the MOST relevant
        # chunks and keep the low-relevance middle. Filter on score order first,
        # order the survivors last. See the litm_order step below.

        async with state["step_tracker"].step("prompt_build") as pb_ctx:
            # B2 Phase: prompt-token squeeze (min-score + dedupe + factoid skip-history).
            # Original block from commit b8557ef was dropped by wave-J reorg merge
            # (d3fb2cd, -X theirs). Re-introduced here so the `_pto_*` references
            # downstream in this block resolve to real values instead of NameError.
            _pto_enabled = bool(_pcfg(
                state,
                "prompt_token_opt_enabled",
                DEFAULT_PROMPT_TOKEN_OPT_ENABLED,
            ))
            try:
                _pto_min_score = float(_pcfg(
                    state,
                    "prompt_token_opt_min_chunk_score",
                    DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
                ))
            except (TypeError, ValueError):
                _pto_min_score = float(DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE)
            try:
                _pto_dedupe = float(_pcfg(
                    state,
                    "prompt_token_opt_dedupe_jaccard_threshold",
                    DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
                ))
            except (TypeError, ValueError):
                _pto_dedupe = float(DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD)
            _pto_factoid_skip = bool(_pcfg(
                state,
                "prompt_token_opt_factoid_skip_history",
                DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
            ))
            graded, _pto_skip_history, _pto_metrics = apply_token_opt(
                graded,
                intent=state.get("intent"),
                enabled=_pto_enabled,
                min_score=_pto_min_score,
                dedupe_threshold=_pto_dedupe,
                factoid_skip_history=_pto_factoid_skip,
            )

            # Cap assembled context chars; drops tail (lowest-graded) chunks first.
            # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 — per-intent override.
            # Aggregation queries set a wider cap so every matching row
            # survives the chunk-drop pass; default 2900 chars was too
            # tight (verified 2026-05-21: turn "1tr499 có mấy dịch vụ"
            # dropped 3 of 7 graded chunks → bot saw only 4 rows).
            _intent_for_cap = state.get("intent") or ""
            _cap_by_intent = _pcfg(
                state, "generate_context_chars_cap_by_intent", None,
            )
            if isinstance(_cap_by_intent, dict) and _intent_for_cap in _cap_by_intent:
                try:
                    _ctx_cap = int(_cap_by_intent[_intent_for_cap])
                except (TypeError, ValueError):
                    _ctx_cap = int(
                        _pcfg(
                            state,
                            "generate_context_chars_cap",
                            DEFAULT_GENERATE_CONTEXT_CHARS_CAP,
                        )
                    )
            else:
                _ctx_cap = int(
                    _pcfg(
                        state,
                        "generate_context_chars_cap",
                        DEFAULT_GENERATE_CONTEXT_CHARS_CAP,
                    )
                )
            # Char-cap on the score-DESCENDING order (B1): drops the lowest-
            # relevance tail, always keeps ≥1. Runs BEFORE the LITM reorder
            # below so a high-relevance chunk is never discarded from the
            # reordered tail. See apply_context_char_cap's ORDER CONTRACT.
            graded, _dropped_chunks, _dropped_chars = apply_context_char_cap(
                graded, _ctx_cap,
            )

            # Lost-in-the-middle reorder (Liu et al., 2023): NOW that the set is
            # trimmed on score order, place the top-ranked survivors at the start
            # AND end so the LLM does not lose them in the middle. Runs last so the
            # char-cap above never drops a high-relevance chunk from the tail.
            if _pcfg(state, "lost_in_middle_reorder_enabled", DEFAULT_LITM_REORDER_ENABLED) and graded:
                async with state["step_tracker"].step("litm_order") as litm_ctx:
                    _pre_ids = [
                        str(c.get("chunk_id") or c.get("id") or "")
                        for c in graded
                    ]
                    graded = reorder_for_lost_in_middle(graded)
                    _post_ids = [
                        str(c.get("chunk_id") or c.get("id") or "")
                        for c in graded
                    ]
                    _post_id_to_pos = {cid: i for i, cid in enumerate(_post_ids) if cid}
                    _kept_indices = [
                        _post_id_to_pos.get(cid, -1)
                        for cid in _pre_ids
                    ]
                    litm_ctx.set_metadata(
                        n=len(graded),
                        kept_indices=_kept_indices,
                    )

            chunk_ids_allowed = {
                str(c.get("chunk_id") or c.get("id") or "")
                for c in graded
                if c.get("chunk_id") or c.get("id")
            }
            _trust_hint = bool(_pcfg(
                state,
                "generate_context_trust_hint_enabled",
                DEFAULT_GENERATE_CONTEXT_TRUST_HINT_ENABLED,
            ))
            # F5 dual-read close: per-bot flag to surface the ingest-time
            # VERBATIM original (exact table grid / formula source, stored
            # read-only in chunk metadata) inside each context fence. Default
            # OFF → no-op when absent; byte-identical happy-path. A/B before
            # flipping default (rule #0). Read-only data, never an app rule.
            _surface_verbatim = bool(_pcfg(
                state,
                "generate_surface_verbatim_enabled",
                DEFAULT_GENERATE_SURFACE_VERBATIM_ENABLED,
            ))
            # M14 — per-bot XML chunk wrap. New bots (created on/after
            # ``XML_WRAP_DEFAULT_ON_FROM_DATE``) get the explicit
            # ``<chunk id type section><content>…</content></chunk>``
            # format by default so the LLM can attribute citations and
            # treat each chunk as an atomic unit. Legacy bots keep the
            # ``<context …>`` format until the operator opts in.
            _xml_wrap = _resolve_xml_wrap_enabled(state)
            context_blocks = []
            for c in graded:
                cid = c.get("chunk_id") or c.get("id")
                text = c.get("text") or c.get("content") or ""
                doc_name = c.get("document_name") or c.get("metadata", {}).get("document_title") or ""
                chunk_idx = c.get("chunk_index", "")
                source_label = doc_name or f"chunk:{cid}"
                chunk_meta = c.get("metadata") or {}
                is_full_doc = c.get("is_full_document") or chunk_meta.get("is_full_document", False)
                context_type = "whole_document" if is_full_doc else "excerpt"
                if not cid:
                    continue
                # F5: read-only verbatim segment ("" → byte-identical fence).
                _vfence = _resolve_verbatim_fence(
                    c, chunk_meta, text,
                    enabled=_surface_verbatim,
                    tag=DEFAULT_GENERATE_VERBATIM_TAG,
                )
                if _xml_wrap:
                    # M14 mindset — chunk-as-atomic-unit. ``chunk_type``
                    # falls back to TEXT when retrieval has not yet
                    # populated the modality (legacy rows pre-M10).
                    _ctype = (
                        c.get("chunk_type")
                        or chunk_meta.get("chunk_type")
                        or DEFAULT_CHUNK_TYPE_TEXT
                    )
                    _section = (
                        chunk_meta.get("structural_path")
                        or chunk_meta.get("section")
                        or source_label
                    )
                    context_blocks.append(
                        f'<chunk id="{cid}" type="{_ctype}" section="{_section}">\n'
                        f'<content>{text}{_vfence}</content>\n'
                        f'</chunk>'
                    )
                elif _trust_hint:
                    context_blocks.append(
                        f'<context source="{source_label}" chunk="{chunk_idx}" id="{cid}" trust="data_only" type="{context_type}">\n{text}{_vfence}\n</context>'
                    )
                else:
                    context_blocks.append(
                        f'<context source="{source_label}" chunk="{chunk_idx}" id="{cid}">\n{text}{_vfence}\n</context>'
                    )
            context_str = "\n\n".join(context_blocks) if context_blocks else ""

            system_prompt = state.get("bot_system_prompt", "") or ""
            if not system_prompt:
                system_prompt = _lang(state).prompt_generator
            # Bind captured slot DATA into the owner-declared placeholder so
            # the LLM asks only for missing slots (no re-asking). Sacred-rule
            # 10: substitution only — absent placeholder → untouched prompt;
            # the platform never injects instruction text of its own.
            if _action_enabled and ACTION_CAPTURED_SLOTS_PLACEHOLDER in system_prompt:
                system_prompt = system_prompt.replace(
                    ACTION_CAPTURED_SLOTS_PLACEHOLDER,
                    _render_captured_slots(_action_state_new, _action_cfg),
                )
            # Cap history at min(condense_limit, DEFAULT_GENERATE_HISTORY_MAX_MSGS).
            # B2 token-opt: when factoid intent + skip-history flag → drop all history.
            _condense_limit = int(_pcfg(state, "condense_history_limit", DEFAULT_GENERATE_HISTORY_MAX_MSGS))
            _history_cap = min(_condense_limit, DEFAULT_GENERATE_HISTORY_MAX_MSGS)
            if _pto_skip_history:
                _history_messages: list[dict[str, Any]] = []
            else:
                _history_messages = state.get("conversation_history", [])[-_history_cap:]
            _cite_marker_re = re.compile(r"\[chunk:[0-9a-f-]+\]", re.IGNORECASE)
            messages = [{"role": "system", "content": system_prompt}]
            for msg in _history_messages:
                _content = msg.get("content", "") or ""
                _content = _cite_marker_re.sub("", _content).strip()
                if len(_content) > MAX_HISTORY_MESSAGE_CHARS:
                    _content = _content[:MAX_HISTORY_MESSAGE_CHARS].rstrip() + " […]"
                messages.append({"role": msg.get("role", "user"), "content": _content})
            _q = state.get('rewritten_query') or state['query']
            _user_content = (
                f"<question>{_q}</question>"
                if _is_chitchat
                else f"<documents>\n{context_str}\n</documents>\n\n<question>{_q}</question>"
            )
            messages.append({"role": "user", "content": _user_content})
            pb_ctx.set_metadata(
                context_chars=len(context_str),
                history_msgs=len(_history_messages),
                context_chunks=len(chunk_ids_allowed),
                compressed=_prompt_compressed,
                context_cap=_ctx_cap,
                context_chunks_dropped=_dropped_chunks,
                context_chars_dropped=_dropped_chars,
                token_opt_enabled=_pto_enabled,
                token_opt_dropped_by_score=_pto_metrics["dropped_by_score"],
                token_opt_dropped_by_dedupe=_pto_metrics["dropped_by_dedupe"],
                token_opt_history_skipped=_pto_skip_history,
            )
        so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
        so_generate = _pcfg(
            state, "generate_use_structured_output",
            DEFAULT_GENERATE_USE_STRUCTURED_OUTPUT,
        )
        # Streaming wins over structured-output: when an SSE sink is wired,
        # we MUST take the free-form path so tokens flow to the client as
        # they arrive. Structured-output uses JSON-mode which buffers the
        # whole response server-side and defeats TTFT. Citations from the
        # streamed answer are recovered post-stream via _CITATION_RE.
        if state.get("_stream_sink") is not None:
            so_generate = False

        # Per-response output cap = system default + paid extra (compute_output_cap).
        # Sacred zero-default contract — both inputs int >= 0.
        _system_output_default = int(_pcfg(
            state,
            "output_tokens_per_response_default",
            DEFAULT_OUTPUT_TOKENS_PER_RESPONSE,
        ))
        _bot_extra_output = int(
            state.get("bot_extra_output_tokens_per_response", 0) or 0,
        )
        _intent_max_tokens = compute_output_cap(
            system_output_default=_system_output_default,
            bot_extra_output=_bot_extra_output,
        )

        answer: str = ""
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        cost_usd = 0.0
        finish_reason: str | None = None
        model_name = "unknown"
        valid_citations: list[dict] = []
        citations_source = "llm"
        structured_succeeded = False
        _n_invalid_citations = 0

        # Cost-aware routing: route cheap intents (factoid / chitchat /
        # OOS-style) to a per-bot cheap binding when seeded; falls back
        # to llm_primary inside resolve_runtime when the cheap-purpose
        # row is absent. The orchestration ``purpose`` label stays
        # "generation" so observability + streaming gate behave
        # identically — only the binding lookup key changes.
        _binding_purpose = _resolve_purpose_for_intent(state.get("intent"))
        if llm_resolved_purpose_total is not None:
            try:
                llm_resolved_purpose_total.labels(
                    intent=str(state.get("intent") or "unknown"),
                    purpose=_binding_purpose,
                ).inc()
            except Exception:  # noqa: BLE001 — metric write is non-critical
                pass
        await _audit(
            state,
            "llm_purpose_resolved",
            {
                "intent": state.get("intent") or "unknown",
                "purpose": _binding_purpose,
            },
        )

        if bool(so_master) and bool(so_generate):
            # Reasoning-first SHAPE: multi-fact intents request the
            # GenerateOutput schema (sub_answers array) so the model
            # enumerates each facet before composing the final answer;
            # factoid/other intents keep the lean flat schema. Default
            # OFF — no behaviour change until an A/B validates the flag.
            _generate_schema = _resolve_generate_schema(state)
            parsed, ctx_so = await _invoke_structured_llm_node(
                state,
                purpose="generation",
                binding_purpose=_binding_purpose,
                messages=messages,
                user_prompt=state.get("rewritten_query") or state["query"],
                schema=_generate_schema,
                max_tokens_override=_intent_max_tokens,
            )
            if parsed is not None:
                structured_succeeded = True
                answer = parsed.answer
                _u = _so_usage(ctx_so)
                prompt_tokens = int(_u["prompt_tokens"])
                completion_tokens = int(_u["completion_tokens"])
                cached_tokens = int(_u["cached_tokens"])
                cost_usd = float(_u["cost_usd"])
                finish_reason = _u["finish_reason"]
                model_name = getattr(ctx_so, "model_id", None) or "unknown"
                # Drop LLM-claimed citation IDs that are not in retrieved chunk_ids.
                chunk_ids_lower = {cid.lower() for cid in chunk_ids_allowed}
                seen: set[str] = set()
                for cit in parsed.citations:
                    cid_norm = (cit.chunk_id or "").lower()
                    if not cid_norm or cid_norm in seen:
                        continue
                    if cid_norm not in chunk_ids_lower:
                        if citation_validation_fail_total is not None:
                            try:
                                citation_validation_fail_total.inc(1)
                            except Exception:  # noqa: BLE001
                                pass
                        continue
                    seen.add(cid_norm)
                    score = 0.0
                    doc_name = ""
                    for c in graded:
                        if str(c.get("chunk_id") or c.get("id") or "").lower() == cid_norm:
                            score = float(c.get("score") or c.get("relevance_score") or 0.0)
                            doc_name = (
                                c.get("document_name")
                                or (c.get("metadata") or {}).get("document_title")
                                or ""
                            )
                            break
                    valid_citations.append({
                        "chunk_id": cit.chunk_id,
                        "score": round(score, 6),
                        "quote": cit.quote,
                        "document_name": doc_name,
                    })

        if not structured_succeeded:
            # Fallback: free-form generation + regex citation parser.
            payload, ctx = await _invoke_llm_node(
                state,
                purpose="generation",
                binding_purpose=_binding_purpose,
                messages=messages,
                user_prompt=state.get("rewritten_query") or state["query"],
                max_tokens_override=_intent_max_tokens,
            )
            answer = payload["text"]
            prompt_tokens = payload["prompt_tokens"]
            completion_tokens = payload["completion_tokens"]
            cached_tokens = int(payload.get("cached_tokens", 0) or 0)
            cost_usd = payload["cost_usd"]
            finish_reason = payload["finish_reason"]
            model_name = payload.get("model_name", "unknown")

            cited_ids = _CITATION_RE.findall(answer)
            invalid_seen: set[str] = set()
            for cid in cited_ids:
                cid_norm = cid.lower()
                if chunk_ids_allowed and any(
                    cid_norm == allowed.lower()
                    for allowed in chunk_ids_allowed
                ):
                    score = 0.0
                    for c in graded:
                        if str(c.get("chunk_id") or c.get("id") or "").lower() == cid_norm:
                            score = float(c.get("score") or c.get("relevance_score") or 0.0)
                            break
                    if not any(vc["chunk_id"].lower() == cid_norm for vc in valid_citations):
                        valid_citations.append({"chunk_id": cid, "score": score})
                else:
                    invalid_seen.add(cid)
            if invalid_seen and citation_validation_fail_total is not None:
                try:
                    citation_validation_fail_total.inc(len(invalid_seen))
                except Exception:  # noqa: BLE001
                    pass
            _n_invalid_citations = len(invalid_seen)

            ctx.record(
                response=answer,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                finish_reason=finish_reason,
            )

        # Post-hoc attribution — citation observability fix. When the LLM
        # did not self-cite (valid_citations empty) but grounded chunks
        # exist, attribute the top-scored retrieved chunk. This ONLY
        # populates the citations list for verifiability; it does NOT alter
        # the answer text (sacred-rule 10 preserved). Lets faithfulness be
        # audited even when a weak model omits the [chunk:id] marker.
        if not valid_citations and graded:
            _top_cit = max(
                graded,
                key=lambda c: float(c.get("score") or c.get("relevance_score") or 0.0),
            )
            _top_cit_id = str(_top_cit.get("chunk_id") or _top_cit.get("id") or "")
            if _top_cit_id:
                valid_citations.append({
                    "chunk_id": _top_cit_id,
                    "score": float(_top_cit.get("score") or _top_cit.get("relevance_score") or 0.0),
                    "document_name": _top_cit.get("document_name") or "",
                })
                citations_source = "posthoc_top_chunk"

        async with state["step_tracker"].step("citations_extract") as cit_ctx:
            if structured_succeeded:
                _cit_source = "llm_structured"
            elif citations_source == "llm":
                _cit_source = "regex_fallback"
            else:
                _cit_source = str(citations_source)
            cit_ctx.set_metadata(
                n_valid=len(valid_citations),
                extracted=len(valid_citations),
                source=_cit_source,
                structured_succeeded=structured_succeeded,
                n_invalid=int(_n_invalid_citations),
            )

        tokens = {"prompt": prompt_tokens, "completion": completion_tokens, "cached": cached_tokens}

        # SLA-breach event covers the full node body; observability-only.
        _generate_elapsed_ms = int((time.monotonic() - _generate_t0) * 1000)
        _generate_sla_ms = int(_pcfg(state, "generate_p95_sla_ms", DEFAULT_GENERATE_P95_SLA_MS))
        if _generate_sla_ms > 0 and _generate_elapsed_ms > _generate_sla_ms:
            logger.warning(
                "generate_sla_breach",
                request_id=str(state.get("request_id") or ""),
                record_bot_id=str(state.get("record_bot_id") or ""),
                intent=state.get("intent") or "",
                duration_ms=_generate_elapsed_ms,
                sla_ms=_generate_sla_ms,
                completion_tokens=int(completion_tokens),
                max_tokens_cap=int(_intent_max_tokens),
            )

        # OBS-1 — an EMPTY answer on the success path is a SILENT failure:
        # the LLM completed (status=success) yet produced no content, so
        # nothing distinguishes it from a real answer in the success metrics.
        # Observability-only: emit a WARN + flag state for downstream
        # status/telemetry. We NEVER substitute or author replacement text
        # here (sacred-rule #10 — the application does not override the LLM
        # answer); the empty answer is returned verbatim. ``chunks_used``
        # separates a retrieval miss (0 chunks) from a generation failure
        # (chunks present but answer empty).
        if _is_empty_answer(answer):
            state["_generate_empty_answer"] = True
            logger.warning(
                "generate_empty_answer",
                request_id=str(state.get("request_id") or ""),
                record_bot_id=str(state.get("record_bot_id") or ""),
                intent=state.get("intent") or "",
                completion_tokens=int(completion_tokens),
                chunks_used=len(graded),
            )

        # Wave H Phase 1 — TTFT lands in ``request_steps.metadata_json``
        # for SLA monitoring. Streaming path stashes the first-delta
        # wall-clock on state inside ``_invoke_llm_node``; absent key
        # (structured-output / no sink / refuse short-circuit) → skip.
        _ttft = state.get("_stream_first_token_ms")
        if _ttft is not None:
            _gen_ctx.set_metadata(first_token_ms=int(_ttft))

        # Wave M3.2 — populate request_steps.model_used + cost_usd +
        # token counts (pre-fix, ``step()`` got passed ``state["model_used"]``
        # which is still empty BEFORE generate resolves its model).
        _gen_ctx.record_llm(
            model_used=model_name,
            prompt_tokens=int(tokens.get("prompt", 0) or 0),
            completion_tokens=int(tokens.get("completion", 0) or 0),
            cost_usd=float(cost_usd or 0.0),
        )

        # Tier 2 (X2 BUNDLED) — post-generate drift detect + state save.
        # When the bot opted into action_config and conversation_state
        # Port is wired, run drift detection on the LLM answer vs the
        # state we built before generation. Drift returns
        # ``GuardrailHit`` (Phase 3 reused type); severity="warn" → add
        # to ``guardrail_flags`` for audit; severity="block" → raise
        # ``GuardrailBlocked`` so the existing OOS refuse flow handles
        # (no application-side override, sacred-rule preservation).
        _post_drift_flags: list[dict] = []
        if (
            _action_enabled
            and conversation_state is not None
            and _action_state_new
        ):
            try:
                # Inject per-bot drift severity map (rule_id → block/warn)
                # under ephemeral key so strategy can map without changing
                # Port signature. JsonbConversationState reads + strips it.
                _drift_cfg = (
                    _action_cfg.get("drift_detection", {})
                    if isinstance(_action_cfg, dict) else {}
                )
                _drift_state_in = dict(_action_state_new)
                _drift_state_in["__drift_severity"] = {
                    "conversation_state_service_drift": _drift_cfg.get(
                        "service_name", "warn",
                    ),
                    "conversation_state_price_drift": _drift_cfg.get(
                        "service_price", "warn",
                    ),
                    "default": _drift_cfg.get("severity_default", "warn"),
                }
                drift_hits = await conversation_state.detect_drift(
                    prior_state=_drift_state_in,
                    proposed_answer=answer,
                    chunks=graded,
                )
                for h in drift_hits:
                    _post_drift_flags.append({
                        "stage": "post_generate",
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action": h.action,
                        "details": dict(h.details) if h.details else {},
                    })
                # Only BLOCK on an actual booking/slot-filling turn. The slot
                # extractor pins a service whenever the question merely names
                # one, so ``service_locked`` is present even on a pure price /
                # comparison / aggregation question — and the drift detector
                # then false-positives (a combo total looks like a "price
                # drift"), refusing an answerable question. Info-retrieval
                # intents are NOT booking, so keep their drift hits as a warn
                # flag instead of blocking.
                _drift_intent = state.get("intent") or ""
                _drift_is_info = _drift_intent in DEFAULT_GROUNDING_INTENTS
                if (not _drift_is_info) and any(h.severity == "block" for h in drift_hits):
                    # Re-use Phase 3 exception type. Caller (test_chat /
                    # chat_worker) catches GuardrailBlocked and substitutes
                    # bot.oos_answer_template. App does NOT override answer.
                    raise GuardrailBlocked(drift_hits)
            except GuardrailBlocked:
                raise
            except Exception as exc:  # noqa: BLE001 — graceful degrade
                logger.debug(
                    "action_state_drift_detect_failed",
                    error=str(exc), error_type=type(exc).__name__,
                )

            # Save state (best-effort; no crash on DB hiccup)
            try:
                conv_id_for_save = state.get("conversation_id")
                await conversation_state.save_state(
                    conversation_id=conv_id_for_save,
                    state=_action_state_new,
                    record_tenant_id=state.get("record_tenant_id"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "action_state_save_failed",
                    error=str(exc), error_type=type(exc).__name__,
                )

        _return: dict = {
            "answer": answer,
            "answer_type": "answered",
            "answer_reason": "Generated from retrieved context",
            "model_used": model_name,
            "tokens": tokens,
            "cost_usd": cost_usd,
            "citations": valid_citations,
            "citations_source": citations_source,
            "system_prompt": system_prompt,
        }
        if _post_drift_flags:
            _return["guardrail_flags"] = (
                list(state.get("guardrail_flags", []))
                + _post_drift_flags
            )
        if _action_enabled:
            _return["action_state"] = _action_state_new
        return _return


__all__ = ["generate"]
