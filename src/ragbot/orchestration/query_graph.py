"""LangGraph StateGraph for the RAG chat pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

import structlog
from langgraph.graph import END, StateGraph

from ragbot.application.ports.audit_logger_port import AuditLoggerPort
from ragbot.application.ports.guardrail_port import (
    GuardrailPort,
)
from ragbot.application.ports.vector_store_port import HybridQuery
from ragbot.application.services.model_resolver import (
    to_embedding_spec as _to_embedding_spec,
)

# OutputGuardrail static-method utility (llm_grounding_check + helpers).
# Static-only — no Port wrap needed; orchestration imports the class
# directly the way it would import any other pure-function module.
from ragbot.infrastructure.guardrails.local_guardrail import OutputGuardrail
from ragbot.infrastructure.observability.invocation_logger import InvocationLogger
from ragbot.orchestration.nodes.adaptive_decompose import (
    adaptive_decompose as _adaptive_decompose_node,
)
from ragbot.orchestration.nodes.check_cache import check_cache as _check_cache_node
from ragbot.orchestration.nodes.condense_question import (
    condense_question as _condense_question_node,
)
from ragbot.orchestration.nodes.critique_parser import (
    critique_parse as _critique_parse_node,
)
from ragbot.orchestration.nodes.decompose import decompose as _decompose_node
from ragbot.orchestration.nodes.generate import generate as _generate_node
from ragbot.orchestration.nodes.grade import grade as _grade_node
from ragbot.orchestration.nodes.graph_retrieve import (
    graph_retrieve_node as _graph_retrieve_node,
)
from ragbot.orchestration.nodes.guard_input import guard_input as _guard_input_node
from ragbot.orchestration.nodes.guard_output import (
    guard_output as _guard_output_node,
)
from ragbot.orchestration.nodes.mmr_dedup import mmr_dedup as _mmr_dedup_node
from ragbot.orchestration.nodes.neighbor_expand import (
    neighbor_expand as _neighbor_expand_node,
)
from ragbot.orchestration.nodes.persist import persist as _persist_node
from ragbot.orchestration.nodes.query_complexity import (
    classify_query_complexity as _classify_query_complexity,
)
from ragbot.orchestration.nodes.query_complexity_node import (
    query_complexity_node as _query_complexity_node,
)
from ragbot.orchestration.nodes.reflect import reflect as _reflect_node
from ragbot.orchestration.nodes.rerank import rerank as _rerank_node
from ragbot.orchestration.nodes.retrieve import retrieve as _retrieve_node
from ragbot.orchestration.nodes.rewrite import rewrite as _rewrite_node
from ragbot.orchestration.nodes.rewrite_retry import rewrite_retry as _rewrite_retry_node
from ragbot.orchestration.nodes.router import router as _router_node

# Conditional-edge routing deciders (pure state->str; capture no di_kwargs).
# Registered by reference in build_graph's add_conditional_edges calls; the
# route-function test fixture captures them by __name__ off the compiled graph.
from ragbot.orchestration.nodes.routing import (
    _cache_route,
    _complexity_route,
    _grade_route,
    _input_blocked,
    _output_blocked,
    _reflect_route,
    _retrieve_route,
    _router_route,
    _understand_query_route,
)
from ragbot.orchestration.nodes.speculative_retrieve import (
    intent_consumes_mq as _intent_consumes_mq,
)
from ragbot.orchestration.nodes.understand import (
    understand_query as _understand_query_node,
)

# Pure stateless helpers extracted to a sibling module (Phase 6 god-file
# split). Re-imported here so existing import paths
# (``from ragbot.orchestration.query_graph import _is_null_lexical`` etc.)
# and the di_kwargs threading into node functions keep working unchanged.
from ragbot.orchestration.query_graph_helpers import (
    _compute_bot_cache_version,
    _is_null_lexical,
    _parse_doc_type_vocabulary,
    _pcfg,
    _render_captured_slots,
    _uuid_or_none,
    expand_parent_chunks,
)
from ragbot.orchestration.state import GraphState
from ragbot.shared.chunking import (
    build_vn_structural_like_clauses,
    detect_vn_structural_anchor,
)
from ragbot.shared.embedding_cache import get_cached_embedding, set_cached_embedding
from ragbot.shared.errors import (
    AuditEmitError,
    EmbeddingError,
    InvariantViolation,
    RetrievalError,
)

logger = structlog.get_logger(__name__)

try:  # metrics optional in tests
    from ragbot.infrastructure.observability.metrics import (
        citation_validation_fail_total,
    )
except ImportError:
    citation_validation_fail_total = None  # type: ignore[assignment]

# Layer 3 LLM metadata extractor (Plan 260604-metadata-aware-v4)
# Soft import — Layer 3 disabled if litellm missing at startup
try:
    import litellm as _litellm_module

    from ragbot.infrastructure.metadata_filter.generic_llm_extractor import (
        GenericLLMMetadataExtractor as _L3Extractor,
    )
    from ragbot.shared.constants import (
        DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL,
    )
except ImportError:
    _litellm_module = None  # type: ignore[assignment]
    _L3Extractor = None  # type: ignore[assignment,misc]
    # litellm is the only optional dependency here — the constants module is
    # pure-data and never fails to import, so pull the fallback model name from
    # its single source of truth instead of re-pinning the literal.
    from ragbot.shared.constants import (  # noqa: F401 — fallback re-bind from SSoT
        DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL,
    )

try:  # metrics optional in tests
    from ragbot.infrastructure.observability.metrics import (
        embedding_model_mismatch_total,
    )
except ImportError:
    embedding_model_mismatch_total = None  # type: ignore[assignment]

try:
    from ragbot.infrastructure.observability.metrics import (
        decompose_skipped_low_confidence_total,
        intent_classifier_confidence,
    )
except ImportError:
    decompose_skipped_low_confidence_total = None  # type: ignore[assignment]
    intent_classifier_confidence = None  # type: ignore[assignment]

try:
    from ragbot.infrastructure.observability.metrics import (
        mq_skipped_no_entities_total,
        mq_variants_deduped_total,
    )
except ImportError:
    mq_skipped_no_entities_total = None  # type: ignore[assignment]
    mq_variants_deduped_total = None  # type: ignore[assignment]

try:
    from ragbot.infrastructure.observability.metrics import (
        llm_resolved_purpose_total,
    )
except ImportError:
    llm_resolved_purpose_total = None  # type: ignore[assignment]

try:
    from ragbot.infrastructure.observability.metrics import (
        cliff_drop_total,
    )
except ImportError:
    cliff_drop_total = None  # type: ignore[assignment]

try:  # B5 async grounding breach counter — optional in unit tests
    from ragbot.infrastructure.observability.metrics import (
        grounding_fail_total as _grounding_fail_total_metric,
    )
except ImportError:
    _grounding_fail_total_metric = None  # type: ignore[assignment]

from ragbot.application.dto.llm_schemas import (
    GenerateFlatOutput,
    GenerateOutput,
    UnderstandOutput,
)
from ragbot.application.services.structured_output_helper import (
    call_with_schema as _call_with_schema,
)
from ragbot.application.services.superlative_context_enricher import (
    SuperlativeContextEnricher as _SuperlativeContextEnricher,
)
from ragbot.infrastructure.llm.dynamic_litellm_router import (
    compute_cost_usd as _router_compute_cost,
)
from ragbot.shared.constants import (
    DEFAULT_DETERMINISTIC_LLM_PURPOSES,
    DEFAULT_DETERMINISTIC_TEMPERATURE,
    DEFAULT_GENERATION_TEMPERATURE,
    DEFAULT_GREETING_PATTERNS,
    DEFAULT_HYDE_ENABLED,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
    DEFAULT_MULTI_QUERY_COMPLEXITY_MIN,
    DEFAULT_MULTI_QUERY_ENABLED,
    DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MIN_TOKENS,
    DEFAULT_MULTI_QUERY_MODEL,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_PIPELINE_PARALLEL_CACHE_UNDERSTAND_ENABLED,
    DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED,
    DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    DEFAULT_SSE_PRODUCER_TIMEOUT_S,
    DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
    DEFAULT_XML_WRAP_ENABLED,
    INTENT_CHITCHAT,
    INTENT_GREETING,
    INTENT_MULTI_HOP,
    LEGACY_CORPUS_VERSION_TAG,
    XML_WRAP_DEFAULT_ON_FROM_DATE,
)

_SUPERLATIVE_ENRICHER = _SuperlativeContextEnricher()
from ragbot.application.services.multi_query_expansion import (
    dedup_variants as mq_dedup_variants,
)
from ragbot.application.services.multi_query_expansion import (
    expand_query as mq_expand_query,
)
from ragbot.application.services.multi_query_expansion import (
    expand_query_with_entities as mq_expand_query_with_entities,
)
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_FALLBACK_VERSION,
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_ENTITY_GROUNDING_ENABLED,
    DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    DEFAULT_LANGUAGE,
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED,
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S,
    DEFAULT_RETRIEVE_TOP_K_BY_INTENT,
    DEFAULT_SPECULATIVE_RETRIEVE_ENABLED,
    DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S,
    DEFAULT_STATS_ATTR_MAX_CHARS,
    DEFAULT_STATS_ATTR_MAX_WORDS,
    DEFAULT_STATS_INDEX_LIMIT,
    DEFAULT_STATS_SUPERLATIVE_LIMIT,
    DEFAULT_STATS_SYNTHETIC_CHUNK_ID,
    DEFAULT_TOP_K,
    INTENT_AGGREGATION,
    INTENT_COMPARISON,
)
from ragbot.shared.i18n import LanguagePack, get_pack, language_pack_from_dict

# CRAG grade vocabulary + pure chunk/grade filters live in retrieval_filter
# (strangler Phase 2). Re-exported here so existing call sites + test imports
# (`from ragbot.orchestration.query_graph import _cliff_detect_filter`) are
# unchanged.

_CITATION_RE = re.compile(r"\[chunk:([0-9a-f\-]+)\]", re.IGNORECASE)


async def retry_hybrid_with_original(
    vector_store: Any,
    original_query: str,
    state: Any,
    embed_fn: Any,
    top_k: int,
) -> list[dict]:
    """Retry hybrid_search with the original (un-rewritten) query. Returns [] on failure."""
    if vector_store is None or not hasattr(vector_store, "hybrid_search"):
        return []
    sig = inspect.signature(vector_store.hybrid_search)
    if "query_text" not in sig.parameters:
        return []
    try:
        embedding = await embed_fn(original_query, state)
        if not embedding:
            return []
        kwargs: dict[str, Any] = {
            "query_text": original_query,
            "query_embedding": embedding,
            "record_bot_id": state["record_bot_id"],
            "top_k": top_k,
        }
        if "channel_type" in sig.parameters:
            kwargs["channel_type"] = _required_channel_type(state)
        if "embedding_column" in sig.parameters and state.get("embedding_column"):
            kwargs["embedding_column"] = state["embedding_column"]
        # mega-sprint-G1: thread tenant so SET LOCAL app.tenant_id binds for RLS.
        if "record_tenant_id" in sig.parameters and state.get("record_tenant_id") is not None:
            kwargs["record_tenant_id"] = state["record_tenant_id"]
        raw = await vector_store.hybrid_search(**kwargs)
        return list(raw or [])
    except (TimeoutError, RetrievalError, OSError, RuntimeError, ValueError, TypeError) as _retr_exc:
        # Vector / hybrid retrieval FAILED (≠ "found nothing"). Fail LOUD:
        # mark the turn as degraded so the answer/grounding path can avoid
        # fabricating from an empty context, and log at ERROR (not warning)
        # with error_type so ops can alert. Still return [] so the graph
        # doesn't crash, but the degraded flag distinguishes error-empty from
        # genuine no-match (HALLU-safety — Agent-2 silent-fail finding).
        try:
            state["retrieval_degraded"] = True
        except (TypeError, KeyError):
            pass
        logger.error(
            "retrieve_error_degraded",
            error=str(_retr_exc),
            error_type=type(_retr_exc).__name__,
            exc_info=True,
        )
        return []


_VALID_INTENTS: list[str] = list(get_args(UnderstandOutput.model_fields["intent"].annotation))


def _lang(state: Any) -> LanguagePack:
    """Return active LanguagePack: state-injected DB rows take precedence over the static fallback.

    The language code resolves from ``state["language"]`` (set upstream from the
    per-bot ``language_code`` column or workspace default). Falls back to
    ``DEFAULT_LANGUAGE`` only when state has no value — preserves multi-tenant
    safety so an English/Khmer bot is never forced into the Vietnamese pack.
    """
    rows = state.get("_language_pack_rows") if isinstance(state, dict) else None
    language = (
        state.get("language", DEFAULT_LANGUAGE) if isinstance(state, dict)
        else DEFAULT_LANGUAGE
    )
    if rows:
        return language_pack_from_dict(language, rows)
    return get_pack(language)

def _resolve_xml_wrap_enabled(state: Any) -> bool:
    """Return the effective ``xml_wrap_enabled`` decision for this request.

    Resolution chain (highest wins):

    1. Explicit ``plan_limits.xml_wrap_enabled`` on the bot — True / False
       always wins (lets operators opt out even for new bots, or opt in
       legacy bots).
    2. ``bot_created_at >= XML_WRAP_DEFAULT_ON_FROM_DATE`` — bots created
       on/after the cutoff default to True when the key is absent.
    3. ``DEFAULT_XML_WRAP_ENABLED`` (False) — legacy bots untouched.

    Backwards-compat guarantee: an existing bot without ``xml_wrap_enabled``
    in its ``plan_limits`` keeps its current behaviour (no XML wrap).
    """
    explicit = _pcfg(state, "xml_wrap_enabled", None)
    if explicit is not None:
        return bool(explicit)
    bot_created_at = state.get("bot_created_at")
    if bot_created_at is None:
        return DEFAULT_XML_WRAP_ENABLED
    try:
        cutoff = datetime.fromisoformat(XML_WRAP_DEFAULT_ON_FROM_DATE)
    except ValueError:
        return DEFAULT_XML_WRAP_ENABLED
    # Naive cutoff compared against the bot's tz-aware ``created_at``: drop
    # the tzinfo so the comparison is consistent regardless of server TZ.
    created_naive = (
        bot_created_at.replace(tzinfo=None)
        if getattr(bot_created_at, "tzinfo", None) is not None
        else bot_created_at
    )
    return created_naive >= cutoff


# Per-intent gate for the structured sub-answer (reasoning-first) generation
# path. Multi-fact intents (aggregation / comparison / list / multi_hop) are
# the ones that drop facts on the flat single-string path; factoid + social
# intents keep the lean flat schema to avoid token bloat. Local literal set
# (will be centralized in shared/constants.py alongside the flag default once
# the A/B validates — that file is owned by another stream this cycle).
_STRUCTURED_SUBANSWER_INTENTS: frozenset[str] = frozenset(
    {INTENT_AGGREGATION, INTENT_COMPARISON, INTENT_MULTI_HOP},
)


def _resolve_generate_schema(state: Any) -> type:
    """Pick the generation structured-output schema for this turn.

    Returns :class:`GenerateOutput` (with the ``sub_answers`` reasoning-first
    array) only when the ``structured_subanswer_enabled`` flag is ON *and* the
    classified intent is a multi-fact intent. Every other case keeps
    :class:`GenerateFlatOutput` (no ``sub_answers`` → leaner JSON schema, no
    token bloat). SHAPE only — neither branch injects answer text nor mutates
    the LLM answer; the downstream code consumes ``.answer`` / ``.citations``
    from whichever schema is returned, identically.

    Flag read with a literal ``False`` fallback (rule #0: default OFF until an
    A/B validates). The ``DEFAULT_STRUCTURED_SUBANSWER_ENABLED`` constant will
    be centralized in ``shared/constants.py`` later (owned by another stream).
    """
    flag_on = bool(_pcfg(state, "structured_subanswer_enabled", False))
    if not flag_on:
        return GenerateFlatOutput
    intent = (state.get("intent") or "").strip()
    if intent in _STRUCTURED_SUBANSWER_INTENTS:
        return GenerateOutput
    return GenerateFlatOutput


def _understand_greeting_short_circuit(state: Any) -> str | None:
    """Return reason string ("short" / "greeting") if understand should bypass
    the LLM call for this turn, else ``None``.

    Gate hierarchy (all consulted from per-bot pipeline_config with fallback
    to ``DEFAULT_*`` constants — zero hardcode):

    1. ``skip_understand_for_greeting`` feature flag — default OFF.
    2. Short-token branch: ``len(query.split()) <= understand_skip_below_tokens``.
    3. Greeting-regex branch: any pattern in ``understand_greeting_patterns``
       matches the stripped query (case-insensitive).

    No exceptions raised on bad regex / config — best-effort returns ``None``
    so the caller falls back to the LLM understand path (graceful degrade).
    """
    if not bool(_pcfg(state, "skip_understand_for_greeting",
                      DEFAULT_SKIP_UNDERSTAND_FOR_GREETING)):
        return None
    query = (state.get("query") or "").strip()
    if not query:
        return None
    # Short-query branch — cheapest, no regex.
    try:
        min_tokens = int(
            _pcfg(state, "understand_skip_below_tokens",
                  DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS) or 0,
        )
    except (TypeError, ValueError):
        min_tokens = DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS
    if min_tokens > 0 and len(query.split()) <= min_tokens:
        return "short"
    # Greeting-regex branch — bot owner may override with empty list to disable.
    patterns = _pcfg(state, "understand_greeting_patterns", DEFAULT_GREETING_PATTERNS)
    if not patterns:
        return None
    for pat in patterns:
        if not isinstance(pat, str) or not pat:
            continue
        try:
            if re.match(pat, query, re.IGNORECASE):
                return INTENT_GREETING
        except re.error:
            # Bot-owner-authored bad regex — degrade silent, fall back to LLM.
            logger.warning("understand_greeting_pattern_invalid", pattern=pat)
            continue
    return None


def _required_channel_type(state: Any) -> str:
    """3-key REQUIRED — refuse silent default; caller must populate state."""
    ch = state.get("channel_type")
    if not ch:
        raise InvariantViolation(
            "channel_type missing from GraphState (3-key identity violation)",
        )
    return str(ch)


def _resolved_oos_template(state: Any) -> str:
    """Return the OOS template stashed by the upstream 7-tier resolver.

    Canonical resolution happens once per request, before the LangGraph
    pipeline starts, inside the chat entry points (test_chat.py /
    chat_stream.py / chat_worker.py). The resolved string is placed under
    ``state["oos_answer_template_resolved"]``; orchestration nodes read
    it via this helper to stay sync (no event-loop juggling at every
    OOS short-circuit) while still benefitting from the full chain
    (bot column → plan_limits → workspace_config → tenants →
    system_config → language_packs → constants).

    Backward-compat: if the upstream resolver did NOT run (legacy caller
    or older test fixture), fall through to the legacy ``pipeline_config``
    flatten (tier 1 only). This preserves prior behaviour for callers
    that haven't been migrated; emit nothing because the breadcrumb
    surfaces upstream wiring gaps as part of the chat_worker / route
    structured event already.
    """
    resolved = state.get("oos_answer_template_resolved") if isinstance(state, dict) else None
    # Treat resolver-empty as "no useful tier hit yet"; fall through to the
    # legacy pipeline_config flatten so the bot's column value (if any)
    # still wins. The 7-tier resolver returns ``""`` only when EVERY tier
    # (bot column → plan_limits → workspace_config → tenants →
    # system_config → language_packs → constants) was empty; in that case
    # the legacy flatten is also empty, so the caller emits "" — same
    # observable behaviour, no regression. When the resolver returns
    # non-empty text, that wins over the legacy flatten path.
    if resolved:
        return str(resolved)
    legacy = _pcfg(state, "oos_answer_template", None)
    return str(legacy) if legacy else ""


def _oos_text(state: Any) -> str:
    """Return the per-bot OOS template with ``{bot_name}`` substituted.

    Thin wrapper around :func:`_resolved_oos_template` that applies the
    legacy ``{bot_name}`` placeholder substitution so existing callers
    keep working unchanged.
    """
    template = _resolved_oos_template(state)
    if not template:
        return ""
    return template.replace("{bot_name}", str(_pcfg(state, "bot_name", "") or ""))


def _check_embed_model_consistency(state: Any, spec: Any, log: Any) -> bool:
    """Detect query vs ingest embedding model mismatch. Detection-only, never raises."""
    expected_model = str(_pcfg(state, "embedding_model", "") or "").strip()
    resolved_model = getattr(spec, "model_name", None) if spec is not None else None
    if not expected_model or not resolved_model:
        return False
    if resolved_model == expected_model:
        return False
    if embedding_model_mismatch_total is not None:
        try:
            embedding_model_mismatch_total.labels(
                expected=expected_model,
                resolved=resolved_model,
            ).inc()
        except (ValueError, AttributeError, TypeError):
            # Metric registry rejected the label tuple or counter is a stub.
            # Metrics failure must not break query.
            pass
    log.warning(
        "embedding_model_mismatch_query_vs_ingest",
        resolved_at_query=resolved_model,
        system_config_ingest_default=expected_model,
        record_bot_id=str(state.get("record_bot_id") or ""),
        action="using_resolved_model",
    )
    return True


async def _resolve_and_complete(
    *,
    model_resolver: Any,
    llm: Any,
    record_tenant_id: Any,
    record_bot_id: Any,
    purpose: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[dict, Any]:
    """Resolve runtime config and call LLM. Returns (payload_dict, cfg)."""
    if model_resolver is None or llm is None:
        raise InvariantViolation(
            f"LLM runtime not configured for node={purpose}",
        )
    cfg = await model_resolver.resolve_runtime(
        record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, purpose=purpose,
    )
    call_kwargs: dict[str, Any] = {"purpose": purpose}
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    result = await llm.complete(cfg, messages=messages, **call_kwargs)
    payload = {
        "text": result.get("text", "") or "",
        "prompt_tokens": int(result.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(result.get("completion_tokens", 0) or 0),
        "cost_usd": float(result.get("cost_usd", 0.0) or 0.0),
        "finish_reason": result.get("finish_reason", "stop"),
    }
    return payload, cfg


def _resolve_stats_keyword_synonyms(state: Any, keyword: str) -> list[str]:
    """Per-bot synonym variants for a stats-route keyword (domain-neutral).

    Reads ``bot_custom_vocabulary["synonyms"]`` — an owner-taught map like
    ``{"da": ["da chết", "chăm sóc da", "trẻ hóa da"]}`` — and returns the
    expansions for ``keyword`` (case-insensitive key match) so the structured
    LIST route OR-matches the full family rather than only exact substrings.
    Empty/missing map → ``[]`` (raw keyword only, behaviour unchanged). No
    hard-coded vocabulary here; the bot owner supplies the map via DB.
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    custom_vocab = _pcfg(state, "bot_custom_vocabulary", {})
    syn_map = custom_vocab.get("synonyms") if isinstance(custom_vocab, dict) else None
    if not isinstance(syn_map, dict):
        return []
    out: list[str] = []
    for key, vals in syn_map.items():
        if not isinstance(key, str) or key.strip().lower() != kw:
            continue
        if isinstance(vals, str):
            vals = [vals]
        if isinstance(vals, (list, tuple)):
            out.extend(str(v) for v in vals if isinstance(v, (str,)) and v.strip())
    return out


async def _run_grounding_check_background(
    *,
    answer: str,
    retrieved_chunks: list[dict],
    record_tenant_id: Any,
    record_bot_id: Any,
    request_id: Any,
    message_id: Any,
    threshold: float,
    top_score: float,
    model_resolver: Any,
    llm: Any,
) -> None:
    """Background grounding judge worker (B5 Phase B).

    Runs ``OutputGuardrail.llm_grounding_check`` AFTER the user response
    has already shipped. A breach is logged at WARNING with the full 4-key
    identity payload so out-of-band alerting can pick it up; a pass is
    logged at INFO. Judge failures (transient LLM error, parse error) are
    swallowed and logged as ``grounding_async_judge_error`` — the response
    has already left the worker, so bubbling exceptions up would crash the
    worker loop without recourse.

    Per Issue 11 (mega-sprint G11): the orchestrator at ``guard_output``
    schedules this when bot owner opted in to async grounding (factoid +
    high-confidence retrieval). Defining the helper now prevents the
    latent ``NameError`` that would otherwise fire the moment any bot
    flipped the ``plan_limits.grounding_check_async_enabled`` flag.
    """

    async def _llm_complete_fn(messages: list[dict]) -> dict:
        cfg = await model_resolver.resolve_runtime(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purpose="grounding",
        )
        # Fire-and-forget judge (response already shipped) → isolated background
        # semaphore lane so it cannot starve foreground generate under burst.
        return await llm.complete(cfg, messages=messages, background=True)

    try:
        hit = await OutputGuardrail.llm_grounding_check(
            answer,
            retrieved_chunks,
            _llm_complete_fn,
            threshold=threshold,
        )
    except Exception as exc:
        logger.warning(
            "grounding_async_judge_error",
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            record_tenant_id=str(record_tenant_id) if record_tenant_id is not None else "",
            record_bot_id=str(record_bot_id) if record_bot_id is not None else "",
            request_id=str(request_id) if request_id is not None else "",
            message_id=message_id,
        )
        return

    if hit is None:
        logger.info(
            "grounding_async_pass",
            record_tenant_id=str(record_tenant_id) if record_tenant_id is not None else "",
            record_bot_id=str(record_bot_id) if record_bot_id is not None else "",
            request_id=str(request_id) if request_id is not None else "",
            message_id=message_id,
            top_score=round(float(top_score), 4),
            threshold=float(threshold),
        )
        return

    logger.warning(
        "grounding_async_breach",
        record_tenant_id=str(record_tenant_id) if record_tenant_id is not None else "",
        record_bot_id=str(record_bot_id) if record_bot_id is not None else "",
        request_id=str(request_id) if request_id is not None else "",
        message_id=message_id,
        rule_id=hit.rule_id,
        severity=hit.severity,
        action=hit.action,
        top_score=round(float(top_score), 4),
        threshold=float(threshold),
    )


def _schedule_grounding_check_background(
    *,
    state: Any,
    threshold: float,
    top_score: float,
    model_resolver: Any,
    llm: Any,
) -> Any:
    """Fire-and-forget scheduler for the async grounding judge (B5 Phase B).

    The user response is already on the wire by the time guard_output
    returns; this schedules ``_run_grounding_check_background`` as an
    asyncio.Task so the judge does NOT contribute to p95. Returns the
    Task on success (also stored on ``state["grounding_async_task"]`` so
    a test or graceful-shutdown hook can await it). Returns ``None`` if
    no running event loop is available — caller has already shipped, so
    a degenerate sync caller must not see an exception.
    """
    coro = _run_grounding_check_background(
        answer=state.get("answer", "") or "",
        retrieved_chunks=state.get("graded_chunks") or state.get("reranked_chunks") or [],
        record_tenant_id=state.get("record_tenant_id"),
        record_bot_id=state.get("record_bot_id"),
        request_id=state.get("request_id"),
        message_id=state.get("message_id"),
        threshold=float(threshold),
        top_score=float(top_score),
        model_resolver=model_resolver,
        llm=llm,
    )
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        # No running loop (sync caller / test harness probing the gate).
        # Close the coroutine to avoid the "coroutine was never awaited"
        # warning, then degrade silent — response has already shipped.
        coro.close()
        logger.debug("grounding_async_no_loop")
        return None

    # Stash the task on state so callers (tests, graceful-shutdown hooks)
    # can await it. The orchestrator itself does NOT await — that is the
    # whole point of the async path.
    try:
        state["grounding_async_task"] = task
    except TypeError:
        # state is a frozen mapping in some test stubs — non-fatal.
        pass
    return task


def build_graph(
    *,
    invocation_logger: InvocationLogger,
    guardrail: GuardrailPort,
    vector_store: Any | None = None,
    lexical_retrieval: Any | None = None,
    reranker: Any | None = None,
    reranker_resolver: Any | None = None,
    embedder: Any | None = None,
    semantic_cache: Any | None = None,
    llm: Any,
    model_resolver: Any,
    redis_client: Any | None = None,
    audit_logger: AuditLoggerPort | None = None,
    entity_extractor: Any | None = None,
    metadata_filter_strategy: Any | None = None,
    language_pack_service: Any | None = None,
    corpus_version_service: Any | None = None,
    error_notify_hook: Any | None = None,
    understand_query_cache: Any | None = None,
    hyde_generator: Any | None = None,
    hallu_verifier: Any | None = None,
    stats_index_repo: Any | None = None,
    doc_repo: Any | None = None,
    conversation_state: Any | None = None,
    slot_extractor: Any | None = None,
) -> Any:
    """Build + compile the LangGraph StateGraph.

    Per-request data (``step_tracker``, ``bot_system_prompt``, ``kg_service``,
    ``session_factory``) is read from the ``GraphState`` dict at node-execution
    time rather than captured in build-time closures. That makes the compiled
    graph instance safe to cache and share across requests / tenants — no
    cross-request leak of tracker, prompt, or DB session.

    ``llm`` + ``model_resolver`` are required.
    """

    async def _audit(state: GraphState, event: str, data: dict) -> None:
        """Best-effort emit to pipeline audit logger; swallows exceptions."""
        if audit_logger is None:
            return
        try:
            await audit_logger.log(
                str(state.get("record_bot_id") or "unknown"),
                "query",
                event,
                {
                    "request_id": str(state.get("request_id") or ""),
                    **data,
                },
            )
        except (AuditEmitError, OSError, RuntimeError, ValueError):
            # Best-effort audit emit; transport / shape failures must
            # never block the pipeline (graceful-degradation rule).
            pass

    async def _resolve_corpus_version(state: GraphState) -> str:
        """Derive the per-bot corpus_version tag for this request.

        Memoised on state under ``_corpus_version`` so the three call
        sites (cache_check / hybrid_search port / cache_store) share a
        single Redis/DB lookup per turn. Falls back to the legacy literal
        whenever the service is missing or fails — keeps the pipeline
        correct under partial DI in tests.
        """
        cached = state.get("_corpus_version")
        if isinstance(cached, str) and cached:
            return cached
        if corpus_version_service is None:
            return LEGACY_CORPUS_VERSION_TAG
        try:
            version = await corpus_version_service.get_for_bot(
                state.get("record_tenant_id"),
                state.get("record_bot_id"),
            )
        except (OSError, RuntimeError, AttributeError, ValueError):
            # Best-effort: Redis/DB outage or service misconfig must
            # never block chat — fall back to the legacy tag.
            return LEGACY_CORPUS_VERSION_TAG
        return version or LEGACY_CORPUS_VERSION_TAG

    async def _invoke_llm_node(
        state: GraphState,
        *,
        purpose: str,
        messages: list[dict],
        user_prompt: str,
        max_tokens_override: int | None = None,
        binding_purpose: str | None = None,
    ) -> tuple[dict, Any]:
        """Run LLM call wrapped by invocation_logger; streams when sink + generation purpose.

        ``purpose`` = logical pipeline-stage tag (drives observability label,
        streaming gate, temperature override). ``binding_purpose`` = optional
        override for the resolver lookup key — defaults to ``purpose``.
        Cost-aware routing passes a different ``binding_purpose``
        (llm_factoid / llm_chitchat / llm_oos / llm_primary) so the binding
        lookup is intent-aware while the ``purpose`` label stays "generation".
        """
        lookup_purpose = binding_purpose or purpose
        try:
            cfg = await model_resolver.resolve_runtime(
                record_tenant_id=state.get("record_tenant_id"),
                record_bot_id=state.get("record_bot_id"),
                purpose=lookup_purpose,
            )
        except InvariantViolation as exc:
            logger.warning(
                "model_resolver_no_binding",
                purpose=lookup_purpose,
                record_bot_id=str(state.get("record_bot_id")),
                node="invoke_llm",
                error=str(exc)[:200],
            )
            # No LLM = cannot answer; fail-loud so upstream surfaces it.
            raise
        provider_code = getattr(getattr(cfg, "provider", None), "name", "unknown")
        model_id = getattr(cfg, "litellm_name", "unknown")
        # Per-feature cost-audit label (alembic 0094). Subsystem prefix
        # ``query.`` distinguishes orchestration LLM calls from ingest /
        # observe / router calls in the per-feature rollup.
        feature_name = f"query.{purpose}"
        async with invocation_logger.invoke_model(
            message_id=state["message_id"],
            record_tenant_id=state.get("record_tenant_id"),
            record_request_id=state.get("request_id"),
            purpose=purpose,
            provider=provider_code,
            model_id=model_id,
            user_prompt=user_prompt,
            feature_name=feature_name,
        ) as ctx:
            if purpose == "generation":
                gen_temp = _pcfg(state, "generation_temperature", DEFAULT_GENERATION_TEMPERATURE)
            elif purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES:
                # Mechanical reformulation / classification — force deterministic
                # so retrieval (and the final answer) is reproducible run to run.
                gen_temp = DEFAULT_DETERMINISTIC_TEMPERATURE
            else:
                gen_temp = None
            _max_tokens_raw = getattr(getattr(cfg, "params", None), "max_tokens", None)
            try:
                _max_tokens = int(_max_tokens_raw) if _max_tokens_raw else None
            except (TypeError, ValueError):
                _max_tokens = None
            # Override only narrows; never enlarges resolved budget.
            if max_tokens_override is not None and max_tokens_override > 0:
                if _max_tokens is None or max_tokens_override < _max_tokens:
                    _max_tokens = int(max_tokens_override)
            sink = state.get("_stream_sink")
            stream_fn = getattr(llm, "complete_runtime_stream", None)
            # Wave K1 Phase 2 — per-bot speculative streaming gate. When
            # ``plan_limits.speculative_streaming_enabled`` is True we
            # wrap ``llm`` in a SpeculativeRouter that races a cheap
            # draft model against the main model. Default OFF preserves
            # the single-model TTFB path (HALLU=0 sacred until Phase 3
            # verifier ships). The gate runs ONLY in the streaming
            # generation branch; non-streaming + structured paths stay
            # on the main LLM deterministically.
            _speculative_enabled = (
                purpose == "generation"
                and stream_fn is not None
                and bool(_pcfg(state, "speculative_streaming_enabled", False))
            )
            _draft_model = (
                str(_pcfg(state, "draft_model", "") or "")
                if _speculative_enabled
                else ""
            )
            # Wave L1 — per-bot Phase 3 verifier opt-in. Only valid when
            # Phase 2 (speculative streaming) is already on; default OFF
            # preserves Phase 2's HALLU-risk path for explicit per-bot
            # accept (and forces operators to bind the verifier before
            # enabling). The flag is read here once so the kwarg passed
            # into ``stream_fn`` below is deterministic per turn.
            _verify_enabled = (
                _speculative_enabled
                and hallu_verifier is not None
                and bool(_pcfg(state, "speculative_hallu_verify_enabled", False))
            )
            if _speculative_enabled:
                # Lazy import — only the opt-in path pays the import cost,
                # and tests that don't exercise the gate don't drag the
                # router into their unit boundary.
                from ragbot.infrastructure.llm.speculative_router import (
                    SpeculativeRouter,
                )

                _speculative_llm = SpeculativeRouter(
                    main_llm=llm,
                    draft_llm=llm,  # same instance; draft_model swap done per-call
                    hallu_verifier=hallu_verifier if _verify_enabled else None,
                )
                stream_fn = _speculative_llm.complete_runtime_stream
            if (
                purpose == "generation"
                and sink is not None
                and stream_fn is not None
            ):
                stream_kwargs: dict = {"purpose": purpose}
                if gen_temp is not None:
                    stream_kwargs["temperature"] = gen_temp
                if _max_tokens is not None and _max_tokens > 0:
                    stream_kwargs["max_tokens"] = _max_tokens
                if _speculative_enabled and _draft_model:
                    # SpeculativeRouter consumes ``draft_model`` and pops
                    # it before delegating downstream so the underlying
                    # router doesn't see an unknown kwarg.
                    stream_kwargs["draft_model"] = _draft_model
                if _verify_enabled:
                    # Phase 3 per-turn gate. SpeculativeRouter pops these
                    # kwargs before delegating; the underlying provider
                    # never sees them. Embedder spec is optional — when
                    # absent the verifier skips the topic-divergence
                    # gate (gates 1+2 still fire deterministically).
                    stream_kwargs["verify_enabled"] = True
                    _verify_record_tenant_id = state.get("record_tenant_id")
                    if _verify_record_tenant_id is not None:
                        stream_kwargs["verify_record_tenant_id"] = (
                            _verify_record_tenant_id
                        )
                _stream_usage: dict[str, float | int | str | None] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                    "finish_reason": "stop",
                }

                def _capture_usage(
                    p: int, c: int, cached: int, cost: float, fr: str | None,
                ) -> None:
                    _stream_usage["prompt_tokens"] = int(p)
                    _stream_usage["completion_tokens"] = int(c)
                    _stream_usage["cached_tokens"] = int(cached)
                    _stream_usage["cost_usd"] = float(cost)
                    _stream_usage["finish_reason"] = fr or "stop"

                stream_kwargs["usage_sink"] = _capture_usage
                buffer: list[str] = []
                # Wave H Phase 1 — capture LLM-side TTFT (the moment the
                # provider returns the first non-empty delta to us; this
                # is the wall-clock baseline the SSE helper measures
                # against the connection start). Stashed on ``state`` so
                # the surrounding generate() step can call
                # ``set_metadata(first_token_ms=…)`` on its step_tracker
                # context — landing in ``request_steps.metadata_json``
                # for SLA monitoring.
                _stream_t0 = time.monotonic()
                _first_token_ms: int | None = None
                async for delta in stream_fn(cfg, messages, **stream_kwargs):
                    if _first_token_ms is None and isinstance(delta, str) and delta:
                        _first_token_ms = int(
                            (time.monotonic() - _stream_t0) * 1000,
                        )
                        # TypedDict total=False — bare assignment is fine.
                        state["_stream_first_token_ms"] = _first_token_ms  # type: ignore[typeddict-unknown-key]
                    buffer.append(delta)
                    if sink is None:
                        continue
                    try:
                        # Guards against disconnected SSE consumer blocking forever.
                        await asyncio.wait_for(
                            sink.put(delta),
                            timeout=DEFAULT_SSE_PRODUCER_TIMEOUT_S,
                        )
                    except TimeoutError as exc:
                        logger.warning(
                            "sse_consumer_lagging_dropping_token",
                            request_id=str(state.get("request_id")),
                            timeout_s=DEFAULT_SSE_PRODUCER_TIMEOUT_S,
                        )
                        raise asyncio.CancelledError(
                            "SSE consumer lagging > timeout",
                        ) from exc
                    except (RuntimeError, ConnectionError, BrokenPipeError):
                        # SSE consumer disconnected; keep accumulating
                        # tokens locally, stop publishing to the dead sink.
                        sink = None  # type: ignore[assignment]
                answer_text = "".join(buffer)
                payload = {
                    "text": answer_text,
                    "prompt_tokens": int(_stream_usage["prompt_tokens"] or 0),
                    "completion_tokens": int(_stream_usage["completion_tokens"] or 0),
                    "cached_tokens": int(_stream_usage["cached_tokens"] or 0),
                    "cost_usd": float(_stream_usage["cost_usd"] or 0.0),
                    "finish_reason": _stream_usage["finish_reason"] or "stop",
                    "model_name": model_id,
                }
                # Record before async-with exits; caller-side ctx.record() is dropped.
                ctx.record(
                    response=answer_text,
                    prompt_tokens=payload["prompt_tokens"],
                    completion_tokens=payload["completion_tokens"],
                    cost_usd=payload["cost_usd"],
                    finish_reason=str(payload["finish_reason"]),
                )
                return payload, ctx
            call_kwargs: dict = {"messages": messages, "purpose": purpose}
            if gen_temp is not None:
                call_kwargs["temperature"] = gen_temp
            if _max_tokens is not None and _max_tokens > 0:
                call_kwargs["max_tokens"] = _max_tokens
            result = await llm.complete(cfg, **call_kwargs)
            payload = {
                "text": result.get("text", "") or "",
                "prompt_tokens": int(result.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(result.get("completion_tokens", 0) or 0),
                "cached_tokens": int(result.get("cached_tokens", 0) or 0),
                "cost_usd": float(result.get("cost_usd", 0.0) or 0.0),
                "finish_reason": result.get("finish_reason", "stop"),
                "model_name": model_id,
            }
            ctx.record(
                response=payload["text"],
                prompt_tokens=payload["prompt_tokens"],
                completion_tokens=payload["completion_tokens"],
                cost_usd=payload["cost_usd"],
                finish_reason=str(payload["finish_reason"]),
            )
            return payload, ctx

    async def _invoke_structured_llm_node(
        state: GraphState,
        *,
        purpose: str,
        messages: list[dict],
        user_prompt: str,
        schema: type,
        max_tokens_override: int | None = None,
        binding_purpose: str | None = None,
    ) -> tuple[Any, Any]:
        """Structured-output sibling of `_invoke_llm_node`. Returns (parsed_or_None, ctx).

        See ``_invoke_llm_node`` for the ``binding_purpose`` rationale —
        same split between observability label (``purpose``) and resolver
        lookup key (``binding_purpose``).
        """
        lookup_purpose = binding_purpose or purpose
        try:
            cfg = await model_resolver.resolve_runtime(
                record_tenant_id=state.get("record_tenant_id"),
                record_bot_id=state.get("record_bot_id"),
                purpose=lookup_purpose,
            )
        except InvariantViolation as exc:
            logger.warning(
                "model_resolver_no_binding",
                purpose=lookup_purpose,
                record_bot_id=str(state.get("record_bot_id")),
                node="invoke_structured",
                error=str(exc)[:200],
            )
            # No LLM = cannot run structured call; fail-loud.
            raise
        provider_code = getattr(getattr(cfg, "provider", None), "code", None) or getattr(
            getattr(cfg, "provider", None), "name", "unknown"
        )
        model_id = getattr(cfg, "litellm_name", "unknown")
        litellm_module = getattr(llm, "_litellm_module", None)
        if litellm_module is None:
            try:
                import litellm as _litellm_mod  # type: ignore
                litellm_module = _litellm_mod
            except ImportError:
                # Optional dependency: litellm absent → skip structured.
                logger.warning("structured_output_litellm_unavailable", purpose=purpose)
                return None, None
        # Structured-output sibling reuses the same ``query.<purpose>``
        # feature label so JSON-mode calls aggregate alongside their text
        # counterparts in cost audit.
        feature_name = f"query.{purpose}"
        async with invocation_logger.invoke_model(
            message_id=state["message_id"],
            record_tenant_id=state.get("record_tenant_id"),
            record_request_id=state.get("request_id"),
            purpose=purpose,
            provider=provider_code,
            model_id=model_id,
            user_prompt=user_prompt,
            feature_name=feature_name,
        ) as ctx:
            provider_obj = getattr(cfg, "provider", None)
            params_obj = getattr(cfg, "params", None)
            timeout_ms = getattr(provider_obj, "timeout_ms", None)
            timeout_s: float | None = None
            if timeout_ms is not None:
                try:
                    timeout_s = float(timeout_ms) / 1000.0
                except (TypeError, ValueError):
                    timeout_s = None
            # Decimal preserves DB Numeric(10,6) precision end-to-end.
            _so_usage: dict[str, Any] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "response_text": "",
                "finish_reason": None,
                "cost_usd": Decimal("0"),
            }

            def _capture_so_usage(
                p: int, c: int, cached: int, response_text: str, fr: str | None,
            ) -> None:
                _so_usage["prompt_tokens"] = int(p)
                _so_usage["completion_tokens"] = int(c)
                _so_usage["cached_tokens"] = int(cached)
                _so_usage["response_text"] = response_text or ""
                _so_usage["finish_reason"] = fr
                pricing = getattr(cfg, "pricing", None)
                _so_usage["cost_usd"] = _router_compute_cost(
                    pricing, p, c, cached,
                )

            # Override only narrows; never enlarges resolved budget.
            _so_max_raw = getattr(params_obj, "max_tokens", None)
            try:
                _so_max_tokens: int | None = (
                    int(_so_max_raw) if _so_max_raw else None
                )
            except (TypeError, ValueError):
                _so_max_tokens = None
            if max_tokens_override is not None and max_tokens_override > 0:
                if _so_max_tokens is None or max_tokens_override < _so_max_tokens:
                    _so_max_tokens = int(max_tokens_override)
            # Capability-driven structured-output transport: surface the
            # resolved model's declared capabilities so the helper picks
            # json_object (loose) vs json_schema (strict) vs tool mode —
            # NOT a name-substring guess. None when unresolved → helper
            # falls back to legacy name routing.
            caps_obj = getattr(cfg, "capabilities", None)
            parsed = await _call_with_schema(
                litellm_module=litellm_module,
                litellm_name=model_id,
                provider_code=provider_code,
                messages=messages,
                schema=schema,
                api_key=getattr(provider_obj, "api_key", None),
                api_base=getattr(provider_obj, "base_url", None),
                timeout=timeout_s,
                temperature=getattr(params_obj, "temperature", None),
                max_tokens=_so_max_tokens,
                usage_sink=_capture_so_usage,
                supports_json_mode=getattr(caps_obj, "supports_json_mode", None),
                supports_tools=getattr(caps_obj, "supports_tool_use", None),
            )
            # Record before async-with exits; caller-side ctx.record() is dropped.
            response_text = (
                _so_usage["response_text"]
                if isinstance(_so_usage["response_text"], str)
                else ""
            )
            finish_reason_val = _so_usage["finish_reason"]
            ctx.record(
                response=response_text,
                prompt_tokens=int(_so_usage["prompt_tokens"]),
                completion_tokens=int(_so_usage["completion_tokens"]),
                cost_usd=_so_usage["cost_usd"],
                finish_reason=(
                    str(finish_reason_val) if finish_reason_val
                    else ("stop" if parsed is not None else "error")
                ),
            )
            try:
                ctx._so_usage = _so_usage  # type: ignore[attr-defined]
                # Wave M3.2 — expose model_id so callers can lift it into
                # request_steps.model_used via ctx.record_llm(...). Without
                # this, model_name fell back to "unknown" because
                # InvocationContext doesn't have a model_id field.
                ctx.model_id = model_id  # type: ignore[attr-defined]
            except AttributeError:
                # ctx may be a frozen / read-only stub in test scenarios.
                pass
            return parsed, ctx

    def _so_usage(ctx: Any) -> dict[str, Any]:
        """Read structured-output usage payload from ctx; returns zero-payload when missing."""
        usage = getattr(ctx, "_so_usage", None) or {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "cached_tokens": int(usage.get("cached_tokens", 0) or 0),
            "cost_usd": float(usage.get("cost_usd", 0.0) or 0.0),
            "finish_reason": usage.get("finish_reason") or "stop",
        }

    async def _prewarm_embedding_cache(queries: list[str], state: GraphState) -> None:
        """Pre-batch embed multi-query variants and seed Redis embedding cache.

        Each subsequent ``_embed_query(q, state)`` call sees a cache hit and
        skips the HTTP round-trip. Best-effort — any failure logs a warning
        and falls through, leaving the per-branch embed path to compute on
        demand. Spec: J1 in plans/260501-R3-PERF-PARALLEL/draft.md.
        """
        if embedder is None or not queries:
            return
        # Set unconditionally so downstream readers never see None (silent
        # fallback to vector_store default risks dimension mismatch when new
        # providers are added).
        state["embedding_column"] = DEFAULT_EMBEDDING_COLUMN
        # Mirror _embed_query's prefix logic so cache keys collide on hit.
        query_prefix = str(_pcfg(state, "embedding_query_prefix", "") or "").strip('"')
        prefixed = [f"{query_prefix}{q}" if query_prefix else q for q in queries]
        emb_model = str(_pcfg(state, "embedding_model", "") or "") or "unknown"
        emb_dim = int(_pcfg(state, "embedding_dimension", DEFAULT_EMBEDDING_DIM) or DEFAULT_EMBEDDING_DIM)
        cold: list[tuple[int, str]] = []
        for idx, qp in enumerate(prefixed):
            cached = await get_cached_embedding(redis_client, qp, model=emb_model, dim=emb_dim)
            if not cached:
                cold.append((idx, qp))
        if not cold:
            return
        try:
            spec = None
            if model_resolver is not None:
                cfg = await model_resolver.resolve_runtime(
                    record_tenant_id=state.get("record_tenant_id"),
                    record_bot_id=state.get("record_bot_id"),
                    purpose="embedding",
                )
                spec = getattr(cfg, "embedding_spec", None)
                if spec is None and cfg is not None:
                    spec = _to_embedding_spec(cfg)
            if spec is None or not hasattr(embedder, "embed_batch"):
                return
            spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})
            cold_texts = [qp for _, qp in cold]
            sig = inspect.signature(embedder.embed_batch)
            params = set(sig.parameters.keys())
            kwargs: dict[str, Any] = {}
            if "spec" in params:
                kwargs["spec"] = spec
            if "record_tenant_id" in params:
                kwargs["record_tenant_id"] = state.get("record_tenant_id")
            results = await embedder.embed_batch(cold_texts, **kwargs)
        except (TimeoutError, EmbeddingError, OSError, RuntimeError, ValueError, AttributeError):
            # Best-effort prewarm; per-branch embed handles fallback.
            logger.warning("multi_query_embed_prewarm_failed", exc_info=True)
            return
        if not results:
            return
        for (idx, qp), emb in zip(cold, results):
            if emb:
                await set_cached_embedding(
                    redis_client, qp, emb, model=emb_model, dim=emb_dim or len(emb),
                )

    async def _embed_query(query_text: str, state: GraphState) -> list[float]:
        """Embed query text via embedder port; checks Redis cache first. Returns [] on failure."""
        if embedder is None:
            return []

        # Set up front so any later exception path still leaves
        # state["embedding_column"] populated for downstream vector-store calls.
        state["embedding_column"] = DEFAULT_EMBEDDING_COLUMN

        # HyDE (T1.4 Wave F production wire) — when the per-bot
        # ``hyde_enabled`` flag is True and a generator is wired into the
        # graph, ask the cheap LLM tier to draft a short hypothetical
        # answer and EMBED THAT in place of the raw query (Gao et al. 2022).
        # The hypothetical text reaches the embedder ONLY — it is never
        # injected into the answer LLM prompt (Quality Gate #10). All
        # failure paths inside ``generate_hypothetical_answer`` fall back
        # to the original query so retrieval keeps working when HyDE is
        # OFF or the upstream LLM stalls.
        if hyde_generator is not None and bool(
            _pcfg(state, "hyde_enabled", DEFAULT_HYDE_ENABLED)
        ):
            try:
                hyde_spec = None
                if model_resolver is not None:
                    cfg = await model_resolver.resolve_runtime(
                        record_tenant_id=state.get("record_tenant_id"),
                        record_bot_id=state.get("record_bot_id"),
                        purpose="hyde",
                    )
                    hyde_spec = getattr(cfg, "llm_spec", None)
                if hyde_spec is not None:
                    hypothetical = await hyde_generator.generate_hypothetical_answer(
                        query_text,
                        spec=hyde_spec,
                        record_tenant_id=state.get("record_tenant_id"),
                        trace_id=state.get("trace_id", ""),
                    )
                    if hypothetical and hypothetical.strip():
                        query_text = hypothetical
            except (InvariantViolation, RuntimeError, ValueError,
                    AttributeError, KeyError):
                # HyDE is best-effort — any spec-resolution or wire error
                # falls through to the raw query embed below.
                logger.warning("hyde_embed_swap_failed", exc_info=True)

        # Asymmetric embedding models require a query-side prefix.
        query_prefix = str(_pcfg(state, "embedding_query_prefix", "") or "").strip('"')
        if query_prefix:
            query_text = f"{query_prefix}{query_text}"

        emb_model = str(_pcfg(state, "embedding_model", "") or "") or "unknown"
        emb_dim = int(_pcfg(state, "embedding_dimension", DEFAULT_EMBEDDING_DIM) or DEFAULT_EMBEDDING_DIM)
        cached = await get_cached_embedding(redis_client, query_text, model=emb_model, dim=emb_dim)
        if cached:
            logger.debug("embedding_cache_hit", query=query_text[:80])
            return cached

        try:
            result: list[float] = []
            if hasattr(embedder, "embed_one"):
                spec = None
                if model_resolver is not None:
                    try:
                        cfg = await model_resolver.resolve_runtime(
                            record_tenant_id=state.get("record_tenant_id"),
                            record_bot_id=state.get("record_bot_id"),
                            purpose="embedding",
                        )
                        spec = getattr(cfg, "embedding_spec", None)
                        if spec is None and cfg is not None:
                            # Use the shared helper so provider prefix is preserved on the wire model name.
                            spec = _to_embedding_spec(cfg)
                    except (AttributeError, TypeError):
                        logger.exception("embedding_spec_build_programmer_bug")
                        raise
                    except (EmbeddingError, InvariantViolation, RuntimeError,
                            ValueError, KeyError):
                        # Runtime spec resolution failed; fall through to
                        # the legacy embedder.embed/embed_batch path.
                        logger.warning("embedding_spec_build_failed", exc_info=True)
                if spec is not None:
                    _check_embed_model_consistency(state, spec, logger)
                    # Spec is frozen; model_copy to switch task adapter to query side.
                    spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})
                    result = await embedder.embed_one(
                        query_text,
                        spec=spec,
                        record_tenant_id=state.get("record_tenant_id"),
                    )
                    if result:
                        await set_cached_embedding(redis_client, query_text, result, model=emb_model, dim=emb_dim or len(result))
                    return result
                if hasattr(embedder, "embed_batch"):
                    sig = inspect.signature(embedder.embed_batch)
                    params = set(sig.parameters.keys())
                    if "spec" not in params and "record_tenant_id" not in params:
                        batch_result = await embedder.embed_batch([query_text])
                        result = batch_result[0] if batch_result else []
                        if result:
                            await set_cached_embedding(redis_client, query_text, result, model=emb_model, dim=emb_dim or len(result))
                        return result
            if hasattr(embedder, "embed"):
                batch_result = await embedder.embed([query_text])
                result = batch_result[0] if batch_result else []
                if result:
                    await set_cached_embedding(redis_client, query_text, result, model=emb_model, dim=emb_dim or len(result))
                return result
        except (TimeoutError, EmbeddingError, OSError, RuntimeError, ValueError, AttributeError) as _emb_exc:
            # Embed FAILED (≠ "no vector by design"): fail LOUD — flag the turn
            # degraded so the answer path won't fabricate from a vector-less
            # context, and ERROR-log with error_type for alerting. Empty list
            # still lets retrieve fall back to lexical-only (HALLU-safety).
            try:
                state["embed_degraded"] = True
            except (TypeError, KeyError):
                pass
            logger.error(
                "embed_query_error_degraded",
                query=query_text[:80],
                error_type=type(_emb_exc).__name__,
                exc_info=True,
            )
        return []

    guard_input = functools.partial(
        _guard_input_node,
        guardrail=guardrail,
        language_pack_service=language_pack_service,
        _resolved_oos_template=_resolved_oos_template,
    )

    check_cache = functools.partial(
        _check_cache_node,
        semantic_cache=semantic_cache,
        redis_client=redis_client,
        _audit=_audit,
        _resolve_corpus_version=_resolve_corpus_version,
        _embed_query=_embed_query,
        _resolved_oos_template=_resolved_oos_template,
    )

    condense_question = functools.partial(
        _condense_question_node,
        _lang=_lang,
        _invoke_llm_node=_invoke_llm_node,
    )

    understand_query = functools.partial(
        _understand_query_node,
        llm=llm,
        model_resolver=model_resolver,
        understand_query_cache=understand_query_cache,
        _audit=_audit,
        _invoke_structured_llm_node=_invoke_structured_llm_node,
        _so_usage=_so_usage,
        _pcfg=_pcfg,
        _lang=_lang,
    )

    async def _run_speculative_retrieve(
        state: GraphState,
    ) -> tuple[list[float], list[dict]]:
        """Embed the raw user query and run hybrid_search in one shot.

        Helper for the speculative orchestrator. Returns the raw embedding
        plus the chunk list so the caller can later decide (via cosine
        similarity against the rewritten embed) whether to reuse them.

        Empty list of chunks on any failure — speculative is best-effort,
        the downstream retrieve node is the authoritative path. The single
        broad-except below is the "background task wrapper" pattern from
        CLAUDE.md — the orchestrator caller never raises this further.
        """
        raw_query = state.get("query") or ""
        if not raw_query or vector_store is None:
            return [], []
        # ``_embed_query`` already swallows its own exceptions and returns
        # ``[]`` on failure, so we don't need a separate try around it.
        raw_embed = await _embed_query(raw_query, state)
        if not raw_embed:
            return [], []
        try:
            # Per-intent retrieve top_k — speculative path mirrors main retrieve node.
            _spec_intent = state.get("intent") or ""
            _spec_topk_by_intent = _pcfg(state, "retrieve_top_k_by_intent", DEFAULT_RETRIEVE_TOP_K_BY_INTENT)
            if isinstance(_spec_topk_by_intent, dict) and _spec_intent in _spec_topk_by_intent:
                try:
                    _top_k = int(_spec_topk_by_intent[_spec_intent])
                except (TypeError, ValueError):
                    _top_k = int(_pcfg(state, "top_k", DEFAULT_TOP_K))
            else:
                _top_k = int(_pcfg(state, "top_k", DEFAULT_TOP_K))
            _is_port = (
                hasattr(vector_store, "hybrid_search")
                and "query_text" not in inspect.signature(
                    vector_store.hybrid_search
                ).parameters
            )
            if _is_port:
                hq = HybridQuery(dense_vector=raw_embed, query_text=raw_query)
                _hs_port_kwargs: dict[str, Any] = {
                    "record_bot_id": state["record_bot_id"],
                    "channel_type": _required_channel_type(state),
                    "corpus_version": await _resolve_corpus_version(state),
                    "embedding_model_version": DEFAULT_EMBEDDING_FALLBACK_VERSION,
                    "limit": _top_k,
                }
                # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                _hs_port_sig = inspect.signature(vector_store.hybrid_search)
                if (
                    "record_tenant_id" in _hs_port_sig.parameters
                    and state.get("record_tenant_id") is not None
                ):
                    _hs_port_kwargs["record_tenant_id"] = state["record_tenant_id"]
                candidates = await vector_store.hybrid_search(hq, **_hs_port_kwargs)
                chunks = [
                    {
                        "chunk_id": str(c.chunk_id),
                        "document_id": str(c.document_id),
                        "content": c.text,
                        "text": c.text,
                        "score": c.score,
                        "document_name": getattr(c, "document_name", "")
                        or (
                            c.payload.get("document_title", "")
                            if hasattr(c, "payload") else ""
                        ),
                        "chunk_index": getattr(c, "chunk_index", ""),
                        **(c.payload if hasattr(c, "payload") else {}),
                    }
                    for c in candidates
                ]
                return raw_embed, chunks
            if hasattr(vector_store, "hybrid_search"):
                _hs_kwargs: dict[str, Any] = {
                    "query_text": raw_query,
                    "query_embedding": raw_embed,
                    "record_bot_id": state["record_bot_id"],
                    "top_k": _top_k,
                }
                _hs_params = set(
                    inspect.signature(vector_store.hybrid_search).parameters.keys()
                )
                if "channel_type" in _hs_params:
                    _hs_kwargs["channel_type"] = _required_channel_type(state)
                # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                if (
                    "record_tenant_id" in _hs_params
                    and state.get("record_tenant_id") is not None
                ):
                    _hs_kwargs["record_tenant_id"] = state["record_tenant_id"]
                # 2026-05-27 — speculative retrieve must honor structural
                # pre-filter too. Without this, queries like "Chương 3
                # nói gì" hit speculative (zembed-1 has-cache-warm-path)
                # → bypass the prefilter in _run_hybrid_for_query →
                # chunks Chương 1/2 returned. Aligns spec with main path.
                if "structural_filter_patterns" in _hs_params:
                    _spec_anchor = detect_vn_structural_anchor(raw_query)
                    if _spec_anchor is not None:
                        _hs_kwargs["structural_filter_patterns"] = (
                            build_vn_structural_like_clauses(_spec_anchor)
                        )
                raw = await vector_store.hybrid_search(**_hs_kwargs)
                return raw_embed, list(raw or [])
        except Exception:
            logger.warning("speculative_hybrid_search_failed", exc_info=True)
        return raw_embed, []

    async def cache_check_and_understand_parallel(state: GraphState) -> dict:
        """Run ``check_cache``, ``understand_query``, and optionally a
        speculative ``retrieve(raw_query)`` concurrently.

        Gated by ``pipeline_parallel_cache_understand_enabled`` (default OFF).
        When OFF, falls through to plain ``check_cache`` so graph topology is
        byte-identical to the legacy path. On cache HIT, the understand task
        is cancelled (no token cost). On cache MISS, both results merge so
        the downstream understand_query node short-circuits via the
        ``_understand_skipped_by_parallel`` slot.

        Speculative retrieve sub-flag (``speculative_retrieve_enabled``,
        default OFF) further forks ``embed(raw_query) + hybrid_search``
        alongside understand_query. When the rewritten embedding is close
        enough to the raw embedding the speculative chunks are stashed in
        state for the downstream ``retrieve`` node to reuse.
        """
        flag = bool(
            _pcfg(state, "pipeline_parallel_cache_understand_enabled",
                  DEFAULT_PIPELINE_PARALLEL_CACHE_UNDERSTAND_ENABLED)
        )
        if not flag:
            return await check_cache(state)
        cache_task = asyncio.create_task(check_cache(state))
        und_task = asyncio.create_task(understand_query(state))
        spec_flag = bool(
            _pcfg(state, "speculative_retrieve_enabled",
                  DEFAULT_SPECULATIVE_RETRIEVE_ENABLED)
        )
        spec_task: asyncio.Task[tuple[list[float], list[dict]]] | None = None
        if spec_flag:
            spec_timeout = float(
                _pcfg(state, "speculative_retrieve_timeout_s",
                      DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S)
            )
            spec_task = asyncio.create_task(
                asyncio.wait_for(
                    _run_speculative_retrieve(state), timeout=spec_timeout,
                )
            )
        # Speculative multi-query expansion (4th parallel task) — fan
        # out the paraphrase LLM call alongside the understand router.
        # When the router lands on a multi-hop / synthesis / docs-only
        # intent the variants are already cached in state so the
        # downstream retrieve node skips its inline MQ call (saves
        # ~250-400ms p95). For chitchat / OOS intents the task is
        # cancelled with ``suppress(CancelledError)`` (no orphan).
        spec_mq_flag = bool(
            _pcfg(
                state,
                "pipeline_multi_query_speculative_enabled",
                DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED,
            )
        )
        spec_mq_task: asyncio.Task[list[str]] | None = None
        if spec_mq_flag:
            spec_mq_timeout = float(
                _pcfg(
                    state,
                    "pipeline_multi_query_speculative_timeout_s",
                    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S,
                )
            )
            spec_mq_task = asyncio.create_task(
                asyncio.wait_for(
                    _run_multi_query_expansion(state),
                    timeout=spec_mq_timeout,
                )
            )
        try:
            cache_out = await cache_task
        except BaseException:
            und_task.cancel()
            if spec_task is not None:
                spec_task.cancel()
            if spec_mq_task is not None:
                spec_mq_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await und_task
            if spec_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_task
            if spec_mq_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_mq_task
            raise
        if isinstance(cache_out, dict) and cache_out.get("cache_status") == "hit":
            und_task.cancel()
            if spec_task is not None:
                spec_task.cancel()
            if spec_mq_task is not None:
                spec_mq_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await und_task
            if spec_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_task
            if spec_mq_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_mq_task
            return cache_out
        try:
            und_out = await und_task
        except asyncio.CancelledError:
            if spec_task is not None:
                spec_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_task
            if spec_mq_task is not None:
                spec_mq_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await spec_mq_task
            raise
        except (TimeoutError, InvariantViolation, OSError, RuntimeError, ValueError, KeyError) as exc:
            # Mirror sequential understand_query fallback so parallel and
            # sequential paths are observably identical on LLM failure.
            logger.warning("understand_in_parallel_failed", exc_info=exc)
            und_out = {"intent": DEFAULT_INTENT_FALLBACK}
        merged: dict = {}
        if isinstance(cache_out, dict):
            merged.update(cache_out)
        if isinstance(und_out, dict):
            merged.update(und_out)
            merged["_understand_skipped_by_parallel"] = True
        if spec_task is not None:
            # ``_run_speculative_retrieve`` already swallows non-cancel
            # exceptions and returns ``([], [])`` on failure; only timeout
            # (wait_for) and explicit cancellation surface here.
            try:
                spec_embed, spec_chunks = await spec_task
            except TimeoutError:
                logger.warning("speculative_retrieve_timeout")
                spec_embed, spec_chunks = [], []
            except asyncio.CancelledError:
                spec_embed, spec_chunks = [], []
            if spec_embed and spec_chunks:
                merged["_speculative_raw_embed"] = spec_embed
                merged["_speculative_chunks"] = spec_chunks
        if spec_mq_task is not None:
            # Decide whether the resolved intent benefits from the
            # speculative paraphrase set, using the SAME per-intent gate the
            # producer (``_run_multi_query_expansion``) applies: the per-bot
            # ``multi_query_enabled_by_intent`` override, else the default
            # map. Keeping both gates on one source of truth (the canonical
            # intent taxonomy) means MQ-enabled intents (aggregation /
            # comparison / multi_hop) actually consume the already-paid-for
            # variants; chitchat / OOS / factoid discard. When the intent
            # does not consume MQ we cancel + suppress so the task leaves no
            # orphan and the LLM cost is bounded by
            # ``pipeline_multi_query_speculative_timeout_s``.
            _resolved_intent = (merged.get("intent") or "").strip().lower()
            _mq_intent_map = _pcfg(state, "multi_query_enabled_by_intent", None)
            if not isinstance(_mq_intent_map, dict):
                _mq_intent_map = DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT
            if _intent_consumes_mq(_resolved_intent, _mq_intent_map):
                try:
                    mq_variants = await spec_mq_task
                except TimeoutError:
                    logger.warning(
                        "pipeline_multi_query_speculative_timeout",
                        intent=_resolved_intent,
                    )
                    mq_variants = []
                except asyncio.CancelledError:
                    mq_variants = []
                except (
                    RuntimeError, ValueError, KeyError, TypeError,
                    OSError,
                ) as exc:
                    logger.warning(
                        "pipeline_multi_query_speculative_failed",
                        intent=_resolved_intent,
                        error_type=type(exc).__name__,
                    )
                    mq_variants = []
                if mq_variants:
                    merged["_mq_speculative_variants"] = mq_variants
                    logger.info(
                        "pipeline_multi_query_speculative_used",
                        intent=_resolved_intent,
                        n_variants=len(mq_variants),
                    )
            else:
                # Intent does not consume MQ — cancel + collect to
                # avoid an orphan running past graph completion.
                spec_mq_task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, asyncio.TimeoutError, Exception,
                ):
                    await spec_mq_task
                logger.info(
                    "pipeline_multi_query_speculative_cancelled",
                    intent=_resolved_intent,
                )
        return merged

    router = functools.partial(
        _router_node,
        model_resolver=model_resolver,
        llm=llm,
        _lang=_lang,
        _invoke_llm_node=_invoke_llm_node,
    )

    rewrite = functools.partial(
        _rewrite_node,
        model_resolver=model_resolver,
        llm=llm,
        _lang=_lang,
        _invoke_llm_node=_invoke_llm_node,
    )

    async def _run_multi_query_expansion(state: GraphState) -> list[str]:
        """Fire the multi_query LLM expansion as a stand-alone helper.

        Mirrors the inline block inside ``retrieve`` body. Returns the list of
        paraphrases (always includes original at variant 0); empty list means
        the gates declined to run (multi_query disabled, n_variants <= 1, no
        LLM runtime, etc.) and the caller should treat the absence of an
        ``_mq_queries`` slot as "fall through to retrieve's inline path".
        """
        # Per-intent skip gate: lightweight intents skip paraphrase fanout
        # to save ~2.3s/turn. Unknown intent falls back to True (safe default).
        _mq_intent = str(state.get("intent") or "")
        _mq_enabled_map = _pcfg(state, "multi_query_enabled_by_intent", None)
        if isinstance(_mq_enabled_map, dict) and _mq_intent in _mq_enabled_map:
            try:
                _intent_mq_enabled = bool(_mq_enabled_map[_mq_intent])
            except (TypeError, ValueError):
                _intent_mq_enabled = True
        else:
            _intent_mq_enabled = DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT.get(_mq_intent, True)
        if not _intent_mq_enabled:
            return []

        # Adaptive-RAG auto-mode gate (complexity routing). The query_complexity
        # node runs AFTER this on the graph, so ``state["complexity_score"]`` is
        # not populated yet — classify inline (the classifier is a sub-ms pure
        # function, no side effects). When the floor is 0.0 (default) the gate
        # is inert; a calibrated >0 floor lets simple single-fact queries skip
        # the paraphrase fanout (faster + cheaper) while complex queries expand.
        _mq_cx_min = float(_pcfg(state, "multi_query_complexity_min", DEFAULT_MULTI_QUERY_COMPLEXITY_MIN))
        if _mq_cx_min > 0.0:
            _mq_q = state.get("query") or ""
            try:
                _, _mq_cx_score = _classify_query_complexity(_mq_q)
            except (ValueError, TypeError):
                _mq_cx_score = _mq_cx_min  # classify failure → do not suppress
            if float(_mq_cx_score) < _mq_cx_min:
                state["multi_query_skipped_simple"] = True
                return []

        mq_enabled = bool(_pcfg(state, "multi_query_enabled", DEFAULT_MULTI_QUERY_ENABLED))
        mq_n_variants = int(_pcfg(state, "multi_query_n_variants", DEFAULT_MULTI_QUERY_N_VARIANTS))
        mq_max_variants = int(_pcfg(state, "multi_query_max_variants", DEFAULT_MULTI_QUERY_MAX_VARIANTS))
        mq_timeout_s = int(_pcfg(state, "multi_query_timeout_s", DEFAULT_MULTI_QUERY_TIMEOUT_S))
        if not mq_enabled or mq_n_variants <= 1 or model_resolver is None or llm is None:
            return []
        sub_queries_state = [
            s for s in (state.get("sub_queries") or []) if isinstance(s, str) and s.strip()
        ]
        # Decompose-precedence bypass: when the caller already supplied ≥2
        # sub-queries (decompose split the query upstream, or a previous
        # turn seeded them), paraphrase fanout is redundant — decompose's
        # sub-questions are the retrieval lever and adding paraphrases of
        # the original query on top would only add LLM cost without lifting
        # recall. Mirror the inline retrieve gate semantics: bypass when
        # sub-queries already exist; otherwise the LLM-paraphrase fanout
        # MUST run (it is the retrieval lever for the rewrite branch where
        # decompose lives on a sibling path and never produced sub-queries).
        if len(sub_queries_state) >= 2:
            state["fanout_bypassed"] = True
            return []
        query_text = state.get("query") or ""
        if not query_text:
            return []
        if bool(_pcfg(
            state, "multi_query_skip_chitchat_intent",
            DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT,
        )) and (state.get("intent") or "") in INTENT_CHITCHAT:
            return []
        _mq_min_tokens = int(_pcfg(
            state, "multi_query_min_tokens", DEFAULT_MULTI_QUERY_MIN_TOKENS,
        ))
        if len(query_text.split()) < _mq_min_tokens:
            return []

        # Wave M3.7-P2 — accumulate per-paraphrase LLM cost into one
        # request_steps row. WHY: ``expand`` helper invokes
        # _mq_llm_complete N times (1 per paraphrase variant, default
        # n_variants=3). Pre-fix each LLM call's tokens/cost were
        # discarded so request_steps.multi_query_fanout row had model
        # NULL + cost 0. Wrap accumulates per-call usage into a shared
        # dict; mq_ctx.record_llm fires at end-of-step with the sum.
        _mq_agg: dict[str, Any] = {
            "model": "", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        }

        async def _mq_llm_complete(*, model_id: str, messages: list[dict], timeout_s: int) -> dict:
            cfg = await model_resolver.resolve_runtime(
                record_tenant_id=state.get("record_tenant_id"),
                record_bot_id=state.get("record_bot_id"),
                purpose="multi_query",
            )
            result = await llm.complete(cfg, messages=messages, purpose="multi_query")
            # Wave M3.7-P2 accumulate.
            _mq_agg["model"] = result.get("model_name") or _mq_agg["model"]
            _mq_agg["prompt_tokens"] += int(result.get("prompt_tokens", 0) or 0)
            _mq_agg["completion_tokens"] += int(result.get("completion_tokens", 0) or 0)
            _mq_agg["cost_usd"] += float(result.get("cost_usd", 0.0) or 0.0)
            return result

        async with state["step_tracker"].step("multi_query_fanout") as mq_ctx:
            mq_model = str(_pcfg(state, "multi_query_model", "") or "") or DEFAULT_MULTI_QUERY_MODEL
            _entity_grounding_enabled = bool(
                _pcfg(state, "entity_grounding_enabled", DEFAULT_ENTITY_GROUNDING_ENABLED)
            )
            _entity_max = int(
                _pcfg(state, "entity_grounding_max_entities", DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES)
            )
            _bot_language = str(state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
            _use_entity_path = bool(entity_extractor is not None and _entity_grounding_enabled)

            # Entity-confidence gate. When entity grounding is
            # active and the extractor reports zero entities, paraphrase
            # fan-out rarely produces a matching BM25 token; skip MQ
            # entirely to save 1 LLM call (~250-400ms p95 trim) and
            # let the single-query branch retrieve. The Port contract
            # currently returns ``list[str]`` (no per-entity score),
            # so the gate is a binary "any entity present" check; the
            # constant ``DEFAULT_MQ_ENTITY_CONFIDENCE_GATE`` is
            # consumed when extractors later expose per-entity scores.
            _entity_gate_enabled = bool(_pcfg(
                state,
                "multi_query_entity_gate_enabled",
                _use_entity_path,
            ))
            if _use_entity_path and _entity_gate_enabled:
                try:
                    _probe_entities = await entity_extractor.extract(
                        query_text, language=_bot_language,
                    )
                except (
                    ValueError, KeyError, TypeError, AttributeError,
                    RuntimeError,
                ):
                    # Extractor failure → fall through to expansion
                    # (matches legacy fail-soft contract).
                    _probe_entities = []
                if not _probe_entities:
                    if mq_skipped_no_entities_total is not None:
                        try:
                            mq_skipped_no_entities_total.inc()
                        except (ValueError, AttributeError):
                            pass
                    logger.info(
                        "multi_query_skipped_no_entities",
                        provider=getattr(
                            entity_extractor, "get_provider_name", lambda: "?",
                        )(),
                        language=_bot_language,
                    )
                    mq_ctx.set_metadata(
                        n_variants=1,
                        requested=mq_n_variants,
                        model=mq_model,
                        entity_path=_use_entity_path,
                        entity_provider=(
                            getattr(
                                entity_extractor, "get_provider_name", lambda: "",
                            )()
                            if entity_extractor is not None
                            else ""
                        ),
                        language=_bot_language,
                        source="parallel_node",
                        skipped_no_entities=True,
                    )
                    return []

            _mq_intent = str(state.get("intent") or "") or None
            try:
                if _use_entity_path:
                    queries = await mq_expand_query_with_entities(
                        query_text,
                        n_variants=mq_n_variants,
                        max_variants=mq_max_variants,
                        model_id=mq_model,
                        timeout_s=mq_timeout_s,
                        llm_complete_fn=_mq_llm_complete,
                        entity_extractor=entity_extractor,
                        language=_bot_language,
                        entity_grounding_enabled=True,
                        max_entities=_entity_max,
                        intent=_mq_intent,
                        language_pack_service=language_pack_service,
                    )
                else:
                    queries = await mq_expand_query(
                        query_text,
                        n_variants=mq_n_variants,
                        max_variants=mq_max_variants,
                        model_id=mq_model,
                        timeout_s=mq_timeout_s,
                        llm_complete_fn=_mq_llm_complete,
                        intent=_mq_intent,
                        language=_bot_language,
                        language_pack_service=language_pack_service,
                    )
                if not queries:
                    queries = [query_text]
            except (TimeoutError, OSError, RuntimeError, ValueError, KeyError, AttributeError):
                # Multi-query expansion failure → fall back to single
                # query (contract: retrieval must always proceed).
                logger.warning("multi_query_parallel_failed", exc_info=True)
                queries = [query_text]

            # Variant similarity dedup. Drops near-duplicate
            # paraphrases that survived the rewriter's stochastic output.
            # Cosine path uses the same embedder as retrieval (cheap once
            # the upstream embed-batch already cached the variant text);
            # token Jaccard fallback is zero-cost for any caller that
            # disabled the embedder.
            _dedup_threshold = float(_pcfg(
                state,
                "multi_query_dedup_threshold",
                DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
            ))
            if len(queries) > 1:
                _embed_one_for_dedup = None
                if embedder is not None:
                    async def _embed_one_for_dedup(text: str) -> list[float]:
                        return await _embed_query(text, state)
                try:
                    deduped, dropped = await mq_dedup_variants(
                        list(queries),
                        embedder=embedder,
                        threshold=_dedup_threshold,
                        embed_one_fn=_embed_one_for_dedup,
                    )
                except (ValueError, KeyError, TypeError, AttributeError,
                        RuntimeError):
                    # Dedup must never break retrieval — fall through.
                    deduped, dropped = list(queries), 0
                if dropped > 0 and mq_variants_deduped_total is not None:
                    try:
                        mq_variants_deduped_total.inc(dropped)
                    except (ValueError, AttributeError):
                        pass
                queries = deduped or [query_text]

            mq_ctx.set_metadata(
                n_variants=len(queries),
                requested=mq_n_variants,
                model=mq_model,
                entity_path=_use_entity_path,
                entity_provider=(
                    getattr(entity_extractor, "get_provider_name", lambda: "")()
                    if entity_extractor is not None
                    else ""
                ),
                language=_bot_language,
                source="parallel_node",
            )
            # Wave M3.7-P2 — sum the per-variant cost into the step row.
            # WHY: see _mq_agg accumulator declaration above. Only fires
            # when the helper actually called the LLM (non-empty model
            # tag); short-circuit paths (chitchat skip, entity gate
            # bypass, dedup-only) leave the aggregator empty so we
            # avoid writing a misleading 0-token row.
            if _mq_agg["prompt_tokens"] > 0:
                mq_ctx.record_llm(
                    model_used=str(_mq_agg["model"] or "") or None,
                    prompt_tokens=_mq_agg["prompt_tokens"],
                    completion_tokens=_mq_agg["completion_tokens"],
                    cost_usd=_mq_agg["cost_usd"],
                )
        return list(queries)

    async def rewrite_and_mq_parallel(state: GraphState) -> dict:
        """Fire ``rewrite`` and the multi_query expansion concurrently.

        Gated by ``pipeline_parallel_rewrite_mq_enabled`` (default OFF). When
        OFF, falls back to plain ``rewrite`` so graph topology is byte-identical
        to the legacy path. The two LLM calls write disjoint state slots
        (``rewritten_query`` vs ``_mq_queries``); concurrency only — no LLM
        output is mutated.
        """
        flag = bool(
            _pcfg(state, "pipeline_parallel_rewrite_mq_enabled",
                  DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED)
        )
        if not flag:
            return await rewrite(state)
        # Decompose-precedence bypass — when decompose ALREADY produced ≥2
        # sub-queries upstream, paraphrase fanout is redundant (decompose's
        # sub-questions are the retrieval lever and adding paraphrases of
        # the original query on top adds LLM cost without lifting recall).
        # When sub_queries is empty (typical case from the rewrite branch —
        # decompose lives on a sibling path), the MQ paraphrase fanout MUST
        # run; it is the retrieval lever for compound single-query inputs.
        # Mirrors the inline retrieve gate semantics fixed in 8ec1eb9.
        sub_queries_state = [
            s for s in (state.get("sub_queries") or []) if isinstance(s, str) and s.strip()
        ]
        if len(sub_queries_state) >= 2:
            state["fanout_bypassed"] = True
            return await rewrite(state)
        rw_task = asyncio.create_task(rewrite(state))
        mq_task = asyncio.create_task(_run_multi_query_expansion(state))
        rw_out, mq_out = await asyncio.gather(rw_task, mq_task, return_exceptions=True)
        merged: dict = {}
        if isinstance(rw_out, dict):
            merged.update(rw_out)
        elif isinstance(rw_out, BaseException):
            logger.warning("rewrite_in_parallel_failed", exc_info=rw_out)
        if isinstance(mq_out, list) and len(mq_out) > 1:
            merged["_mq_queries"] = mq_out
        elif isinstance(mq_out, BaseException):
            logger.warning("mq_in_parallel_failed", exc_info=mq_out)
        return merged

    decompose = functools.partial(
        _decompose_node,
        _lang=_lang,
        _invoke_llm_node=_invoke_llm_node,
        _invoke_structured_llm_node=_invoke_structured_llm_node,
        _so_usage=_so_usage,
    )

    async def _do_stats_lookup(
        state: GraphState,
        *,
        range_filter: Any,
        stats_limit: int,
    ) -> dict | None:
        """Run stats-index SQL query + linked-chunk fetch.

        Returns the ready result dict on success (non-empty entities) or
        ``None`` when the index is empty / fails. The coroutine is designed
        to be safe to cancel at any await point — SQLAlchemy async sessions
        are rolled back automatically on cancellation.

        Source metadata tag is intentionally NOT set here; the caller sets it
        after the race resolves so the step context always reflects the actual
        path taken.
        """
        try:
            _operation = getattr(range_filter, "operation", "")
            if _operation == "keyword":
                # List/category ("liệt kê dịch vụ X" / "tư vấn về X"): return
                # EVERY record whose name/category matches the keyword, so the
                # LLM can list/count them ALL (vector/BM25 only surface top-k).
                _stats_kw = getattr(range_filter, "keyword", "") or ""
                entities = await stats_index_repo.query_by_name_keyword(
                    record_bot_id=state["record_bot_id"],
                    keyword=_stats_kw,
                    synonyms=_resolve_stats_keyword_synonyms(state, _stats_kw),
                    limit=stats_limit,
                )
                if not entities:
                    # "liệt kê tất cả / có những <generic category> nào" — the
                    # category word ("lốp", "dịch vụ") names the WHOLE corpus,
                    # not a value inside any entity NAME, so the keyword ILIKE
                    # returns 0. An enumerate-all query must list EVERY record
                    # (top-k vector retrieve gives an incomplete list), so fall
                    # back to the full table instead of collapsing to vector.
                    entities = await stats_index_repo.list_all_entities(
                        record_bot_id=state["record_bot_id"],
                        limit=stats_limit,
                    )
            elif _operation in ("max", "min"):
                # Superlative ("đắt nhất"/"rẻ nhất"): no bound → ORDER BY price.
                entities = await stats_index_repo.top_by_price(
                    record_bot_id=state["record_bot_id"],
                    direction=_operation,
                    limit=min(stats_limit, DEFAULT_STATS_SUPERLATIVE_LIMIT),
                    price_column=range_filter.price_column,
                )
            else:
                entities = await stats_index_repo.query_by_price_range(
                    record_bot_id=state["record_bot_id"],
                    price_min=range_filter.price_min,
                    price_max=range_filter.price_max,
                    price_column=range_filter.price_column,
                    limit=stats_limit,
                )
            if not entities:
                return None
            chunk_ids = [
                e["record_chunk_id"]
                for e in entities
                if e.get("record_chunk_id")
            ]
            # Fallback: when stats rows were ingested before the chunk_id
            # backfill (rows have ``record_chunk_id IS NULL``), fall back to
            # the parent document IDs so generate still has grounded text
            # context. Without this, the SQL path returns entities but no
            # chunks → no_chunks_short_circuit → OOS refuse even though the
            # data was found. Pre-2026-05-26 ingest never wrote chunk FKs
            # for stats rows, so this fallback is required for production
            # corpora until a backfill migration ships.
            doc_ids = [
                e["record_document_id"]
                for e in entities
                if e.get("record_document_id")
            ]
            # B-1 (STEP-5): ``record_chunk_id`` is now populated, but the precise
            # per-entity chunks are intentionally NOT fetched into the LLM
            # context — re-feeding raw rows changed answers (a tenant eval Q) and is a
            # separate quality change (W-I9, Phase C, its own A/B). Context stays
            # pre-B1: the synthetic record if built, else the doc-level fallback
            # below. STEP-5 attribution rides on the entities' ``record_chunk_id``
            # (the callback writes request_chunk_refs from them) — measurement
            # without touching the answer. ``chunk_ids`` retained for that.
            linked_chunks: list[dict] = []
            _ = chunk_ids  # entities carry record_chunk_id for attribution
            # Surface the filtered/ranked rows as a synthetic context chunk.
            # The stats rows carry no chunk FK, so the only alternative is the
            # doc-level dump of the WHOLE table — the LLM then has to re-filter
            # "< 500k" itself and often misses the matching rows, or (worse, on a
            # code lookup) reads the raw variant-blob rows and FABRICATES a
            # price/stock for a near-duplicate code. Handing it the already
            # filtered/ranked name+price+field list makes the result explicit
            # and is the clean source of truth. Grounded data only (values
            # extracted from the corpus), no instruction text → not an
            # app-inject (QG #10). Deduped + capped. Built BEFORE the doc-level
            # fallback so the fallback can be suppressed when this succeeds.
            # A structured FIELD value is short + atomic (price, date, code,
            # "30 phút"). Free-text extraction noise (a booking sentence
            # mis-captured as a column) is many words — surfacing it pollutes
            # the chunk and trips the grounding check on a correct answer. Keep
            # only field-like values: domain-neutral word-count + char cap,
            # skip generic placeholder columns.
            def _is_field_like(v: str) -> bool:
                return bool(v) and len(v) <= DEFAULT_STATS_ATTR_MAX_CHARS \
                    and len(v.split()) <= DEFAULT_STATS_ATTR_MAX_WORDS

            _seen: set[tuple[str, int]] = set()
            _rows: list[str] = []
            for _e in entities:
                _name = (str(_e.get("entity_name") or "")).strip().strip('"')
                if not _name:
                    continue
                _price = _e.get("price_primary")
                if _price is None:
                    _price = _e.get("price_secondary")
                _key = (_name, int(_price) if _price is not None else -1)
                if _key in _seen:
                    continue
                _seen.add(_key)
                # A name column that is actually a synonym/variant mega-cell
                # (a quoted "code, code, …" list parsed as col 0) is not a
                # usable display label — surfacing it dilutes the chunk and can
                # trip the grounding check. When the name is not field-like,
                # drop it from the line lead and let the labeled attributes
                # (productname / code / quantity / price / date …) carry the
                # record. Domain-neutral: keyed on value shape, not corpus.
                _name_field_like = _is_field_like(_name)
                # Currency-neutral: emit the raw number only (the corpus may be
                # in any currency — appending "VND" would break a USD/EUR bot).
                # When the name is field-like it labels the price (e.g.
                # "Triệt lông nách: 500000"). When it is NOT (a variant mega-cell
                # dropped from the lead), the bare number is ambiguous — the LLM
                # cannot tell 972000 is a price vs a quantity/id — so label it
                # generically "price:" (price_primary IS the schema's price
                # column, a structure concept, not a corpus/brand literal).
                if _name_field_like:
                    _parts = (
                        [f"{_name}: {int(_price)}"]
                        if _price is not None
                        else [_name]
                    )
                else:
                    _parts = (
                        [f"price: {int(_price)}"] if _price is not None else []
                    )
                # Surface the remaining structured columns (answer/quantity/date/
                # image/...) generically so a record-shaped bot (e.g. an n8n
                # results[] consumer) gets every field, not just the price. The
                # column names come from the corpus header — domain-neutral, no
                # hard-coded field list. Skip internal keys + the synonym/variant
                # mega-cell. A generic ``col_N`` name (header-less CSV) is NOT
                # skipped: the ``_is_field_like`` gate below already drops the
                # huge mega-cell (>120 chars / >12 words), so a short ``col_*``
                # value like a delivery date "28-thg 11" stays groundable instead
                # of being stripped — stripping every ``col_\d+`` left date/stock
                # queries unanswerable (the chunk became the bare entity name).
                _cat = str(_e.get("entity_category") or "").strip()
                if _is_field_like(_cat):
                    _parts.append(f"category: {_cat}")
                _attrs = _e.get("attributes_json")
                if isinstance(_attrs, dict):
                    for _k, _v in _attrs.items():
                        if _k in ("chunk_index", "question", "variants"):
                            continue
                        _vs = str(_v).strip()
                        if _is_field_like(_vs):
                            _parts.append(f"{_k}: {_vs}")
                if not _parts:
                    # No field-like name, price, nor attribute → nothing
                    # groundable to surface; skip rather than emit a blank line.
                    continue
                _rows.append(" | ".join(_parts))
                if len(_rows) >= DEFAULT_STATS_INDEX_LIMIT:
                    break
            synthetic_chunks: list[dict] = []
            if _rows:
                _body = "\n".join(_rows)
                synthetic_chunks.append({
                    "content": _body,
                    "text": _body,
                    # Non-empty sentinel: the generate node drops chunks with a
                    # falsy id from the <documents> block, which would make this
                    # authoritative stats answer invisible to the LLM.
                    "chunk_id": DEFAULT_STATS_SYNTHETIC_CHUNK_ID,
                    "document_name": "",
                    "score": 1.0,
                    "source": "stats_index",
                })
            # Doc-level fallback — ONLY when neither precise chunk-id linkage nor
            # a synthetic chunk is available. The whole-document dump exists to
            # avoid a no_chunks_short_circuit → OOS refuse when stats rows have
            # no chunk FK (pre-backfill corpora). But when a synthetic chunk was
            # built, that clean record IS the grounded context — adding the raw
            # whole-table dump on top reintroduces the variant-blob noise the
            # structured route exists to avoid, and on a code lookup the LLM then
            # fabricates a price/stock from a near-duplicate row. Suppress it.
            if (
                not linked_chunks
                and not synthetic_chunks
                and doc_ids
                and doc_repo is not None
                and hasattr(doc_repo, "find_chunks_by_document_ids")
            ):
                try:
                    linked_chunks = await doc_repo.find_chunks_by_document_ids(
                        list(set(doc_ids)),
                        record_bot_id=state["record_bot_id"],
                    )
                except (OSError, RuntimeError, ValueError, KeyError,
                        AttributeError):
                    logger.warning(
                        "stats_index_doc_chunk_fetch_failed",
                        exc_info=True,
                    )
            # LLM CONTEXT = the synthetic clean record only. Do NOT append the
            # raw per-entity source chunks (``linked_chunks``): re-feeding the raw
            # table rows changed answers (tenant COVERAGE 1.00->0.90 in the B-1 A/B)
            # and reintroduces the variant-blob noise the synthetic route exists
            # to avoid. STEP-5 attribution rides on ``entities`` (each carries
            # ``record_chunk_id``) — the callback writes ``request_chunk_refs``
            # from those WITHOUT polluting the context. Fall back to the raw
            # chunks only when no synthetic record could be built.
            return {
                "entities": entities,
                "linked_chunks": synthetic_chunks if synthetic_chunks
                else linked_chunks,
                "range_filter": range_filter,
            }
        except (OSError, RuntimeError, ValueError, KeyError,
                AttributeError, TypeError):
            logger.warning("stats_index_route_failed", exc_info=True)
            return None

    retrieve = functools.partial(
        _retrieve_node,
        vector_store=vector_store,
        lexical_retrieval=lexical_retrieval,
        embedder=embedder,
        llm=llm,
        model_resolver=model_resolver,
        redis_client=redis_client,
        entity_extractor=entity_extractor,
        metadata_filter_strategy=metadata_filter_strategy,
        language_pack_service=language_pack_service,
        stats_index_repo=stats_index_repo,
        doc_repo=doc_repo,
        _audit=_audit,
        _resolve_corpus_version=_resolve_corpus_version,
        _embed_query=_embed_query,
        _prewarm_embedding_cache=_prewarm_embedding_cache,
        _do_stats_lookup=_do_stats_lookup,
        _pcfg=_pcfg,
        _required_channel_type=_required_channel_type,
        _is_null_lexical=_is_null_lexical,
        expand_parent_chunks=expand_parent_chunks,
        retry_hybrid_with_original=retry_hybrid_with_original,
        _parse_doc_type_vocabulary=_parse_doc_type_vocabulary,
    )

    rerank = functools.partial(
        _rerank_node,
        reranker=reranker,
        reranker_resolver=reranker_resolver,
        error_notify_hook=error_notify_hook,
        _audit=_audit,
        _pcfg=_pcfg,
        _uuid_or_none=_uuid_or_none,
    )

    grade = functools.partial(
        _grade_node,
        llm=llm,
        model_resolver=model_resolver,
        _audit=_audit,
        _invoke_structured_llm_node=_invoke_structured_llm_node,
        _so_usage=_so_usage,
        _pcfg=_pcfg,
        _lang=_lang,
    )

    mmr_dedup = functools.partial(_mmr_dedup_node, _pcfg=_pcfg, _audit=_audit)

    neighbor_expand = functools.partial(
        _neighbor_expand_node, _pcfg=_pcfg, _audit=_audit,
    )

    rewrite_retry = functools.partial(_rewrite_retry_node, rewrite=rewrite)

    generate = functools.partial(
        _generate_node,
        llm=llm,
        model_resolver=model_resolver,
        conversation_state=conversation_state,
        slot_extractor=slot_extractor,
        _audit=_audit,
        _invoke_llm_node=_invoke_llm_node,
        _invoke_structured_llm_node=_invoke_structured_llm_node,
        _so_usage=_so_usage,
        _pcfg=_pcfg,
        _lang=_lang,
        _oos_text=_oos_text,
        _resolve_xml_wrap_enabled=_resolve_xml_wrap_enabled,
        _resolve_generate_schema=_resolve_generate_schema,
        _render_captured_slots=_render_captured_slots,
        _CITATION_RE=_CITATION_RE,
    )

    critique_parse = functools.partial(_critique_parse_node, _oos_text=_oos_text)

    guard_output = functools.partial(
        _guard_output_node,
        llm=llm,
        model_resolver=model_resolver,
        guardrail=guardrail,
        _schedule_grounding_check_background=_schedule_grounding_check_background,
        _pcfg=_pcfg,
        _resolved_oos_template=_resolved_oos_template,
    )

    reflect = functools.partial(
        _reflect_node,
        llm=llm,
        model_resolver=model_resolver,
        _pcfg=_pcfg,
        _lang=_lang,
        _invoke_llm_node=_invoke_llm_node,
        _invoke_structured_llm_node=_invoke_structured_llm_node,
        _so_usage=_so_usage,
    )

    persist = functools.partial(
        _persist_node,
        semantic_cache=semantic_cache,
        _audit=_audit,
        _resolve_corpus_version=_resolve_corpus_version,
        _embed_query=_embed_query,
        _pcfg=_pcfg,
        _compute_bot_cache_version=_compute_bot_cache_version,
        _resolved_oos_template=_resolved_oos_template,
    )

    graph_retrieve_node = _graph_retrieve_node

    async def _run_query_complexity(state: GraphState) -> dict:
        """Branch A of pre_retrieval_parallel: domain-neutral heuristic classifier.

        Writes ``complexity_label`` + ``complexity_score`` so the downstream
        conditional edge (_complexity_route) can route without re-running.
        Falls through to ("simple", 0.0) on any unexpected input — preserves
        legacy path on failure.
        """
        async with state["step_tracker"].step("query_complexity"):
            query_text = state.get("rewritten_query") or state.get("query") or ""
            try:
                label, score = _classify_query_complexity(query_text)
            except (ValueError, TypeError, AttributeError):
                label, score = ("simple", 0.0)
            logger.info(
                "adaptive_router_l1",
                label=label,
                score=round(float(score), 4),
                query_preview=str(query_text)[:80],
                bot_id=str(state.get("bot_id") or ""),
            )
            return {"complexity_label": label, "complexity_score": float(score)}

    async def _run_router_select_model(state: GraphState) -> dict:
        """Branch B of pre_retrieval_parallel: telemetry-only model resolver.

        Resolves the understand_query model binding for the current bot and
        records the model_id / provider onto a ``router_select_model``
        request_step row. No state keys are written — purely observability.
        Narrow-exception: (InvariantViolation, OSError, RuntimeError,
        ValueError, AttributeError) are caught and logged so a resolver
        outage does NOT block routing.
        """
        if model_resolver is None:
            return {}
        async with state["step_tracker"].step("router_select_model") as _ctx:
            try:
                _cfg = await model_resolver.resolve_runtime(
                    record_tenant_id=state.get("record_tenant_id"),
                    record_bot_id=state.get("record_bot_id"),
                    purpose="understand_query",
                )
                _model = (
                    getattr(_cfg, "litellm_name", None)
                    or getattr(_cfg, "model_name", None)
                    or "unknown"
                )
                _provider = getattr(getattr(_cfg, "provider", None), "name", "unknown")
                _ctx.set_metadata(
                    model_id=str(_model),
                    provider=str(_provider),
                    purpose="understand_query",
                )
            except (InvariantViolation, OSError, RuntimeError,
                    ValueError, AttributeError) as _err:
                _ctx.set_metadata(
                    model_id="unresolved",
                    provider="unresolved",
                    purpose="understand_query",
                    error=type(_err).__name__,
                )
                logger.warning(
                    "router_select_model_failed",
                    error_type=type(_err).__name__,
                    record_bot_id=str(state.get("record_bot_id") or ""),
                )
        return {}

    async def _run_semantic_cache_preflight(state: GraphState) -> dict:
        """Branch C of pre_retrieval_parallel: validate embedding column wired.

        Confirms the ``embedding_column`` key is set on state (resolved by
        check_cache upstream). Emits a warning when absent — this indicates
        the embedder DI is mis-configured and the semantic cache fast-path
        will be degraded for this turn. Does NOT re-query pgvector (that
        already ran in check_cache). Returns {} always — no state mutation.
        """
        async with state["step_tracker"].step("semantic_cache_check"):
            col = state.get("embedding_column")
            if not col:
                logger.warning(
                    "semantic_cache_preflight_no_embedding_column",
                    record_bot_id=str(state.get("record_bot_id") or ""),
                )
            else:
                logger.debug(
                    "semantic_cache_preflight_ok",
                    embedding_column=str(col),
                    record_bot_id=str(state.get("record_bot_id") or ""),
                )
        return {}

    query_complexity_node = functools.partial(
        _query_complexity_node,
        _run_query_complexity=_run_query_complexity,
        _run_router_select_model=_run_router_select_model,
        _run_semantic_cache_preflight=_run_semantic_cache_preflight,
    )

    adaptive_decompose = functools.partial(
        _adaptive_decompose_node,
        model_resolver=model_resolver,
        llm=llm,
    )

    graph = StateGraph(GraphState)
    graph.add_node("guard_input", guard_input)
    # Parallel-wrapper is the actual cache_check node; the ``check_cache``
    # closure is invoked inside it when the flag is OFF (byte-identical
    # fallback). No conditional edge targets a standalone ``check_cache``
    # node, so it is intentionally NOT registered.
    graph.add_node(
        "cache_check_and_understand_parallel", cache_check_and_understand_parallel
    )
    # Both merged and legacy paths are registered; _cache_route picks one via merge_condense_router.
    graph.add_node("understand_query", understand_query)
    graph.add_node("condense_question", condense_question)
    graph.add_node("router", router)
    # Parallel-wrapper for rewrite + multi_query expansion; falls back to
    # plain rewrite() when flag OFF so legacy path is byte-identical.
    # No conditional edge targets a standalone ``rewrite`` node — every
    # ``rewrite`` decision routes to ``rewrite_and_mq_parallel`` instead.
    graph.add_node("rewrite_and_mq_parallel", rewrite_and_mq_parallel)
    graph.add_node("decompose", decompose)
    # Adaptive Router L1 (domain-neutral classifier) + L3 (LLM decomposer).
    # L1 fires unconditionally after understand_query when the master
    # toggle ``adaptive_router_l1_enabled`` is on; L3 fires only when L1
    # flagged the query complex.
    graph.add_node("query_complexity", query_complexity_node)
    graph.add_node("adaptive_decompose", adaptive_decompose)
    graph.add_node("retrieve", retrieve)
    graph.add_node("graph_retrieve", graph_retrieve_node)
    graph.add_node("rerank", rerank)
    graph.add_node("mmr_dedup", mmr_dedup)
    # M2 neighbor expansion: opt-in via plan_limits.neighbor_expand_enabled.
    # Always registered; the node body short-circuits to ``{}`` (no-op)
    # when the flag is off so LangGraph's merge sees identity.
    graph.add_node("neighbor_expand", neighbor_expand)
    graph.add_node("grade", grade)
    graph.add_node("rewrite_retry", rewrite_retry)
    graph.add_node("generate", generate)
    # Self-RAG critique parser — opt-in per-bot. Node body short-circuits
    # to ``{}`` (identity) when the flag is off so LangGraph merges as a
    # no-op for bots that never opt in.
    graph.add_node("critique_parse", critique_parse)
    graph.add_node("guard_output", guard_output)
    graph.add_node("reflect", reflect)
    graph.add_node("persist", persist)

    graph.set_entry_point("guard_input")
    graph.add_conditional_edges(
        "guard_input",
        _input_blocked,
        {"persist": "persist", "check_cache": "cache_check_and_understand_parallel"},
    )
    graph.add_conditional_edges(
        "cache_check_and_understand_parallel",
        _cache_route,
        {"persist": "persist", "understand_query": "understand_query", "condense_question": "condense_question"},
    )
    graph.add_conditional_edges(
        "understand_query",
        _understand_query_route,
        {
            "rewrite": "rewrite_and_mq_parallel",
            "retrieve": "retrieve",
            "decompose": "decompose",
            "query_complexity": "query_complexity",
        },
    )
    # After Layer 1 classifier: complex → L3 decomposer; else fall back
    # to the legacy router decision (rewrite / retrieve / decompose).
    graph.add_conditional_edges(
        "query_complexity",
        _complexity_route,
        {
            "adaptive_decompose": "adaptive_decompose",
            "rewrite": "rewrite_and_mq_parallel",
            "retrieve": "retrieve",
            "decompose": "decompose",
        },
    )
    # L3 decomposer always falls through to retrieve. Sub-queries (if any)
    # are picked up by retrieve / multi_query_fanout via the S2 bypass.
    graph.add_edge("adaptive_decompose", "retrieve")
    graph.add_edge("condense_question", "router")
    graph.add_conditional_edges(
        "router",
        _router_route,
        {"rewrite": "rewrite_and_mq_parallel", "retrieve": "retrieve", "decompose": "decompose"},
    )
    graph.add_edge("rewrite_and_mq_parallel", "retrieve")
    graph.add_edge("decompose", "retrieve")
    graph.add_conditional_edges(
        "retrieve",
        _retrieve_route,
        {"rerank": "rerank", "graph_retrieve": "graph_retrieve", "generate": "generate"},
    )
    graph.add_edge("graph_retrieve", "rerank")
    graph.add_edge("rerank", "mmr_dedup")
    # M2: insert ``neighbor_expand`` between mmr_dedup and grade. The
    # node is opt-in per-bot (default OFF) — when the flag is False
    # the body returns ``{}`` and LangGraph treats the step as identity.
    graph.add_edge("mmr_dedup", "neighbor_expand")
    graph.add_edge("neighbor_expand", "grade")
    graph.add_conditional_edges(
        "grade",
        _grade_route,
        {"rewrite_retry": "rewrite_retry", "generate": "generate"},
    )
    graph.add_edge("rewrite_retry", "retrieve")
    # WA-4 Self-RAG critique gate sits between generate and guard_output.
    # When ``self_rag_critique_enabled`` is OFF the node returns ``{}`` so
    # the path remains byte-identical to the legacy ``generate→guard_output``
    # edge.  When ON the node may strip critique tokens from the answer
    # and, if the unsupported ratio exceeds the per-bot threshold, swap
    # the answer for the bot's ``oos_answer_template`` before guard_output.
    graph.add_edge("generate", "critique_parse")
    graph.add_edge("critique_parse", "guard_output")
    graph.add_conditional_edges(
        "guard_output",
        _output_blocked,
        {"persist": "persist", "reflect": "reflect"},
    )
    graph.add_conditional_edges(
        "reflect",
        _reflect_route,
        {"generate": "generate", "persist": "persist"},
    )
    graph.add_edge("persist", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Compiled-graph singleton                                                 #
# ---------------------------------------------------------------------------
# Compilation walks every ``add_node`` / ``add_conditional_edges`` call and
# materialises a ``Pregel`` instance — measurable but small per-request.
# Because per-request data (``step_tracker`` / ``bot_system_prompt`` / DB
# session factory / KG service) is now carried on ``GraphState`` rather than
# captured in node closures, a single compiled graph is safe to reuse across
# every request, every tenant, every bot. The singleton wrapper memoises the
# result so the second + n-th call to ``get_graph`` skip compilation entirely.

_GRAPH_SINGLETON: Any | None = None
_GRAPH_SINGLETON_LOCK = asyncio.Lock()


async def get_graph(**di_kwargs: Any) -> Any:
    """Return a process-wide cached compiled graph; build on first call.

    All keyword arguments are forwarded verbatim to ``build_graph``; once
    the singleton is populated, further calls return that same instance and
    *ignore* incoming kwargs. The DI singletons (``llm``, ``model_resolver``,
    ``vector_store``, ...) are themselves process-wide so reusing the
    compiled graph across calls is correct: it always closes over the same
    handles. Per-request data lives on state and is therefore unaffected.
    """
    global _GRAPH_SINGLETON
    if _GRAPH_SINGLETON is not None:
        return _GRAPH_SINGLETON
    async with _GRAPH_SINGLETON_LOCK:
        if _GRAPH_SINGLETON is None:
            _GRAPH_SINGLETON = build_graph(**di_kwargs)
    return _GRAPH_SINGLETON


def _reset_graph_singleton_for_test() -> None:
    """Drop the cached compiled graph — exposed for tests only."""
    global _GRAPH_SINGLETON
    _GRAPH_SINGLETON = None


__all__ = ["build_graph", "get_graph"]
