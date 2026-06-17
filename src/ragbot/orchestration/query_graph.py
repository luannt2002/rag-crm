"""LangGraph StateGraph for the RAG chat pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import inspect
import json as _json_mod
import os
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

import structlog
from langgraph.graph import END, StateGraph
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.services.model_resolver import (
    resolve_purpose_for_intent as _resolve_purpose_for_intent,
    to_embedding_spec as _to_embedding_spec,
)
from ragbot.application.ports.audit_logger_port import AuditLoggerPort
from ragbot.application.ports.cache_port import CachedResponse
from ragbot.application.ports.guardrail_port import (
    GuardrailBlocked,
    GuardrailHit,
    GuardrailPort,
)
from ragbot.application.ports.vector_store_port import HybridQuery
# OutputGuardrail static-method utility (llm_grounding_check + helpers).
# Static-only — no Port wrap needed; orchestration imports the class
# directly the way it would import any other pure-function module.
from ragbot.infrastructure.guardrails.local_guardrail import OutputGuardrail
from ragbot.infrastructure.observability.invocation_logger import InvocationLogger
from ragbot.orchestration.nodes.critique_parser import (
    parse_critique_tokens as _parse_critique_tokens,
    should_refuse_critique as _should_refuse_critique,
)
from ragbot.orchestration.nodes.generate import generate as _generate_node
from ragbot.orchestration.nodes.grade import grade as _grade_node
from ragbot.orchestration.nodes.guard_output import (
    guard_output as _guard_output_node,
)
from ragbot.orchestration.nodes.persist import persist as _persist_node
from ragbot.orchestration.nodes.reflect import reflect as _reflect_node
from ragbot.orchestration.nodes.retrieve import retrieve as _retrieve_node
from ragbot.orchestration.nodes.rerank import rerank as _rerank_node
from ragbot.orchestration.nodes.understand import (
    understand_query as _understand_query_node,
)
from ragbot.orchestration.nodes.speculative_retrieve import (
    decide_keep_speculative as _decide_keep_speculative,
)
from ragbot.orchestration.state import GraphState
from ragbot.orchestration.nodes.query_complexity import (
    classify_query_complexity as _classify_query_complexity,
)
from ragbot.orchestration.nodes.query_decomposer import (
    decompose_query as _decompose_query,
)
from ragbot.orchestration.nodes.cascade_router_helper import apply_cascade_routing
from ragbot.infrastructure.graph.graph_retriever import graph_retrieve as _graph_retrieve
from ragbot.shared.errors import (
    AuditEmitError,
    EmbeddingError,
    InvariantViolation,
    RetrievalError,
)
from ragbot.shared.embedding_cache import get_cached_embedding, set_cached_embedding
from ragbot.shared.mmr import mmr_filter
from ragbot.shared.prompt_compression import compress_chunks
from ragbot.shared.prompt_token_opt import apply_token_opt
from ragbot.shared.token_budget import compute_output_cap
from ragbot.shared.vi_tokenizer import expand_abbreviations, restore_diacritics
from ragbot.shared.chunking import (
    build_vn_structural_like_clauses,
    detect_vn_structural_anchor,
    normalize_vn_section_numerals,
)
from ragbot.shared.query_range_parser import (
    parse_range_query as _parse_range_query,
    matches_summary_pattern as _matches_summary_pattern,
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
    DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL = "gpt-4.1-nano"  # type: ignore[assignment]

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

from ragbot.shared.constants import (
    ACTION_CAPTURED_SLOTS_PLACEHOLDER,
    DEFAULT_ADAPTIVE_ROUTER_L1_ENABLED,
    DEFAULT_ANSWER_AUTONOMY_PERCENT,
    DEFAULT_BOT_CACHE_VERSION_HASH_LEN,
    DEFAULT_CASCADE_ROUTING_ENABLED,
    DEFAULT_HYDE_ENABLED,
    DEFAULT_CHUNK_TYPE_TEXT,
    DEFAULT_CITATIONS_TOP_K,
    DEFAULT_XML_WRAP_ENABLED,
    XML_WRAP_DEFAULT_ON_FROM_DATE,
    LEGACY_CORPUS_VERSION_TAG,
    DEFAULT_CRAG_FALLBACK_COUNT,
    DEFAULT_CRAG_GRADE_CONCURRENCY,
    DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED,
    DEFAULT_CRAG_LENIENT_GRADE_INTENTS,
    DEFAULT_CRAG_MAX_GRADE_RETRIES,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT,
    DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
    DEFAULT_CR_ENHANCED_ENABLED,
    DEFAULT_DECOMPOSE_CONFIDENCE_GATE,
    DEFAULT_DECOMPOSE_MIN_TOKENS,
    DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY,
    DEFAULT_PARSE_DECOMPOSED_MAX_SUB,
    DEFAULT_DECOMPOSE_USE_STRUCTURED_OUTPUT,
    DEFAULT_INTENT_CONFIDENCE_FALLBACK,
    DEFAULT_GENERATE_CONTEXT_TRUST_HINT_ENABLED,
    DEFAULT_GENERATE_HISTORY_MAX_MSGS,
    DEFAULT_DETERMINISTIC_LLM_PURPOSES,
    DEFAULT_CONDENSE_MIN_HISTORY_CHARS,
    DEFAULT_CONDENSE_MIN_HISTORY_TURNS,
    DEFAULT_DETERMINISTIC_TEMPERATURE,
    DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT,
    DEFAULT_GENERATE_USE_STRUCTURED_OUTPUT,
    DEFAULT_GENERATION_TEMPERATURE,
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_REFLECTION_ENABLED,
    DEFAULT_SKIP_REFLECT_INTENTS,
    DEFAULT_SKIP_REWRITE_INTENTS,
    INTENT_CHITCHAT,
    INTENT_GREETING,
    INTENT_MULTI_HOP,
    INTENT_OUT_OF_SCOPE,
    INTENT_SYNTHESIS,
    DEFAULT_GRADE_TIMEOUT_S,
    DEFAULT_GRADE_USE_BATCH,
    DEFAULT_GRADE_USE_STRUCTURED_OUTPUT,
    DEFAULT_UNDERSTAND_CONDENSED_QUERY_AUDIT_PREVIEW_LEN,
    DEFAULT_UNDERSTAND_USE_STRUCTURED_OUTPUT,
    DEFAULT_LITM_REORDER_ENABLED,
    DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS,
    DEFAULT_OOS_ANSWER_TEMPLATE,
    DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED,
    DEFAULT_SELF_RAG_ENABLED,
    DEFAULT_SELF_RAG_THRESHOLD,
    MAX_HISTORY_MESSAGE_CHARS,
    DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED,
    DEFAULT_METADATA_LAYER3_LLM_ENABLED,
    DEFAULT_METADATA_FALLBACK_RELAX_ENABLED,
    DEFAULT_MULTI_QUERY_ENABLED,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MODEL,
    DEFAULT_MULTI_QUERY_MIN_TOKENS,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_MQ_ENTITY_CONFIDENCE_GATE,
    DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
    DEFAULT_GREETING_PATTERNS,
    DEFAULT_PIPELINE_MULTI_QUERY_EMBED_BATCH_ENABLED,
    DEFAULT_PIPELINE_PARALLEL_CACHE_UNDERSTAND_ENABLED,
    DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED,
    DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED,
    DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED,
    DEFAULT_REWRITE_ENABLED_BY_INTENT,
    DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT,
    DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
    DEFAULT_REFLECT_USE_STRUCTURED_OUTPUT,
    DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
    DEFAULT_RERANK_CLIFF_GAP_RATIO,
    DEFAULT_RERANK_CLIFF_MIN_KEEP,
    DEFAULT_RERANK_CLIFF_SKIP_INTENTS,
    DEFAULT_RERANK_MAX_CHUNKS_TO_LLM,
    DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
    DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
    DEFAULT_ADAPTIVE_CONTEXT_HIGH_SCORE,
    DEFAULT_ADAPTIVE_CONTEXT_MAX_N,
    DEFAULT_ADAPTIVE_CONTEXT_EXEMPT_INTENTS,
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
    DEFAULT_RERANKER_MIN_SCORE,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
    DEFAULT_RERANKER_MIN_SCORE_BYPASS,
    DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD,
    DEFAULT_RETRIEVAL_MULTISTAGE_ENABLED,
    DEFAULT_RETRIEVAL_STAGES,
    DEFAULT_RETRIEVE_FALLBACK_ENABLED,
    DEFAULT_RETRIEVE_FALLBACK_TOP_K,
    DEFAULT_RRF_K,
    DEFAULT_SSE_PRODUCER_TIMEOUT_S,
    DEFAULT_OUTPUT_TOKENS_PER_RESPONSE,
    DEFAULT_STRUCTURED_OUTPUT_ENABLED,
    _REFUSE_ANSWER_TYPES,
)
from ragbot.application.dto.llm_schemas import (
    DecomposeOutput,
    GenerateFlatOutput,
    GenerateOutput,
    GradeBatchOutput,
    GradeOutput,
    ReflectOutput,
    UnderstandOutput,
)
from ragbot.application.services.structured_output_helper import (
    call_with_schema as _call_with_schema,
)
from ragbot.infrastructure.llm.dynamic_litellm_router import (
    compute_cost_usd as _router_compute_cost,
)
from ragbot.application.services.query_intent_extractor import (
    extract_intent as _extract_query_intent,
)
from ragbot.application.services.heuristic_intent_classifier import (
    classify_heuristic as _classify_heuristic,
)
from ragbot.application.services.superlative_context_enricher import (
    SuperlativeContextEnricher as _SuperlativeContextEnricher,
    get_enricher_for_language as _get_superlative_enricher,
)

_SUPERLATIVE_ENRICHER = _SuperlativeContextEnricher()
from ragbot.application.services.adaptive_rerank_weight import (
    adaptive_weight_enabled as _adaptive_weight_enabled,
    resolve_intent_weights as _resolve_intent_weights,
)
from ragbot.application.services.multi_query_expansion import (
    dedup_variants as mq_dedup_variants,
    expand_query as mq_expand_query,
    expand_query_with_entities as mq_expand_query_with_entities,
    rrf_merge_chunks as mq_rrf_merge_chunks,
)
from ragbot.application.services.vocabulary_expander import (
    get_default_expander as _get_vocab_expander,
)
from ragbot.shared.constants import (
    DEFAULT_BM25_NORMALIZATION_FLAGS,
    DEFAULT_CACHE_SIMILARITY_THRESHOLD,
    DEFAULT_CHITCHAT_QUERY_MAX_TOKENS,
    DEFAULT_CONDENSE_HISTORY_LIMIT,
    DEFAULT_HALLU_TRAP_KEYWORDS,
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_ENTITY_GROUNDING_ENABLED,
    DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    DEFAULT_GENERIC_VOCAB_ENABLED,
    DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY,
    DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH,
    DEFAULT_GENERATE_P95_SLA_MS,
    DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
    DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_GROUNDING_CHECK_THRESHOLD,
    DEFAULT_GROUNDING_INTENTS,
    DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE,
    DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
    DEFAULT_LANGUAGE,
    DEFAULT_LEXICAL_RRF_K,
    DEFAULT_LEXICAL_TOP_K,
    DEFAULT_MAX_REFLECT_RETRIES,
    DEFAULT_REFLECT_SKIP_IF_GROUNDED,
    DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
    DEFAULT_MMR_LAMBDA,
    DEFAULT_MMR_SIMILARITY_THRESHOLD,
    DEFAULT_NEIGHBOR_EXPAND_ENABLED,
    DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
    DEFAULT_NEIGHBOR_TOKEN_BUDGET,
    DEFAULT_NEIGHBOR_WINDOW_SIZE,
    DEFAULT_PROMPT_COMPRESSION_ENABLED,
    DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
    DEFAULT_PROMPT_TOKEN_OPT_ENABLED,
    DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
    DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    DEFAULT_GENERATE_CONTEXT_CHARS_CAP,
    DEFAULT_QUERY_RECEIVED_AUDIT_PREVIEW_CHARS,
    DEFAULT_REFLECT_ANSWER_PREVIEW_CHARS,
    DEFAULT_REFLECT_CONTEXT_CHUNK_CAP,
    DEFAULT_REFLECT_CONTEXT_CHUNK_CHARS,
    DEFAULT_RERANK_TOP_N,
    DEFAULT_SEMANTIC_CACHE_TTL,
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_ENABLED,
    DEFAULT_PIPELINE_MULTI_QUERY_SPECULATIVE_TIMEOUT_S,
    DEFAULT_SPECULATIVE_RETRIEVE_ENABLED,
    DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S,
    DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD,
    DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
    DEFAULT_SEMANTIC_CACHE_SKIP_NUMERIC,
    DEFAULT_TOP_K,
    DEFAULT_RERANK_TOP_N_BY_INTENT,
    DEFAULT_RETRIEVE_TOP_K_BY_INTENT,
    DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS,
    DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S,
    DEFAULT_STATS_INDEX_LIMIT,
    DEFAULT_STATS_RACE_TIMEOUT_S,
    DEFAULT_STATS_INDEX_RACE_ENABLED,
    DEFAULT_STATS_SUPERLATIVE_LIMIT,
    DEFAULT_STATS_ATTR_MAX_CHARS,
    RANGE_QUERY_MIN_CONFIDENCE,
    DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
    INTENT_AGGREGATION,
    INTENT_COMPARISON,
    HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
    DEFAULT_HEURISTIC_INTENT_ENABLED,
    DEFAULT_GUARD_OUTPUT_PARALLEL_ENABLED,
)
from ragbot.shared.bootstrap_config import get_boot_config as _get_boot_config
from ragbot.shared.context_utils import reorder_for_lost_in_middle
from ragbot.shared.autonomy_resolver import autonomy_band, clamp_autonomy_percent

from ragbot.shared.i18n import LanguagePack, get_pack, language_pack_from_dict

# CRAG grade vocabulary + pure chunk/grade filters live in retrieval_filter
# (strangler Phase 2). Re-exported here so existing call sites + test imports
# (`from ragbot.orchestration.query_graph import _cliff_detect_filter`) are
# unchanged.
from ragbot.orchestration.retrieval_filter import (  # noqa: E402
    CRAG_GRADE_AMBIGUOUS,
    CRAG_GRADE_IRRELEVANT,
    CRAG_GRADE_RELEVANT,
    _autocut,
    _cliff_detect_filter,
    _CRAG_VALID_GRADES,
    _is_retrieval_adequate,
    _remap_grade_for_intent,
    _rerank_threshold_gate,
)

_CITATION_RE = re.compile(r"\[chunk:([0-9a-f\-]+)\]", re.IGNORECASE)


def parse_decomposed_sub_queries(
    raw_llm_text: str, *, max_sub: int = DEFAULT_PARSE_DECOMPOSED_MAX_SUB,
) -> list[str]:
    """Parse LLM JSON-array output. raw text → list[str] (max `max_sub`)."""
    if not raw_llm_text:
        return []
    text = raw_llm_text.strip()
    if not text.startswith("["):
        return []
    try:
        parsed = _json_mod.loads(text)
    except (ValueError, TypeError):
        # Malformed JSON / non-string input — defensive parse failure.
        return []
    if not isinstance(parsed, list) or len(parsed) < 2:
        return []
    return [str(q).strip() for q in parsed[:max_sub] if str(q).strip()]


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
    except (RetrievalError, asyncio.TimeoutError, OSError,
            RuntimeError, ValueError, TypeError) as _retr_exc:
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


def expand_parent_chunks(
    chunks: list[dict],
    parent_map: dict[str, dict],
) -> list[dict]:
    """Swap child chunks for their parent content (small-to-big retrieval); dedup by parent id."""
    seen_parents: set[str] = set()
    expanded: list[dict] = []
    for chunk in chunks:
        pcid = chunk.get("parent_chunk_id")
        if pcid and str(pcid) in parent_map:
            pcid_str = str(pcid)
            if pcid_str in seen_parents:
                continue  # dedup
            seen_parents.add(pcid_str)
            parent = parent_map[pcid_str]
            expanded.append({
                **chunk,
                "content": parent["content"],
                "text": parent.get("text", parent["content"]),
                "chunk_id": pcid_str,
                "is_parent_expanded": True,
            })
        else:
            expanded.append(chunk)
    return expanded


_VALID_INTENTS: list[str] = list(get_args(UnderstandOutput.model_fields["intent"].annotation))


def _lang(state: Any) -> LanguagePack:  # noqa: ANN401
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

def _uuid_or_none(value: Any) -> Any:  # noqa: ANN401
    """Coerce a state UUID-like value to UUID, or None on missing/invalid."""
    if value is None:
        return None
    from uuid import UUID
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _pcfg(state: Any, key: str, default: Any = None) -> Any:  # noqa: ANN401
    """Read pipeline config value from GraphState with fallback default.

    260525 Bug #12 — also treat ``None`` as "missing". The Bug #7c bulk
    closure populates 78 keys with ``raw.get(key, None)`` so the key is
    PRESENT in the dict but the value is ``None`` (no operator override
    in ``system_config``). Pre-fix this short-circuited the caller-side
    ``DEFAULT_*`` fallback and propagated ``None`` straight through to
    code like ``float(_pcfg(...))`` which then crashed.

    Semantically ``None`` means "no operator override → use caller
    default", which is what callers already pass as ``default``.
    """
    raw = (state.get("pipeline_config") or {}).get(key, default)
    return default if raw is None else raw


def _resolve_xml_wrap_enabled(state: Any) -> bool:  # noqa: ANN401
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


def _resolve_generate_schema(state: Any) -> type:  # noqa: ANN401
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


def _understand_greeting_short_circuit(state: Any) -> str | None:  # noqa: ANN401
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


def _required_channel_type(state: Any) -> str:  # noqa: ANN401
    """3-key REQUIRED — refuse silent default; caller must populate state."""
    ch = state.get("channel_type")
    if not ch:
        raise InvariantViolation(
            "channel_type missing from GraphState (3-key identity violation)",
        )
    return str(ch)


def _resolved_oos_template(state: Any) -> str:  # noqa: ANN401
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


def _oos_text(state: Any) -> str:  # noqa: ANN401
    """Return the per-bot OOS template with ``{bot_name}`` substituted.

    Thin wrapper around :func:`_resolved_oos_template` that applies the
    legacy ``{bot_name}`` placeholder substitution so existing callers
    keep working unchanged.
    """
    template = _resolved_oos_template(state)
    if not template:
        return ""
    return template.replace("{bot_name}", str(_pcfg(state, "bot_name", "") or ""))


def _parse_doc_type_vocabulary(raw: Any) -> frozenset[str]:  # noqa: ANN401
    """Parse comma-separated or JSON-list vocabulary string into a frozenset."""
    if not raw:
        return frozenset()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(v).strip().lower() for v in raw if str(v).strip())
    text = str(raw).strip()
    if not text:
        return frozenset()
    if text.startswith("["):
        try:
            parsed = _json_mod.loads(text)
            if isinstance(parsed, list):
                return frozenset(str(v).strip().lower() for v in parsed if str(v).strip())
        except (ValueError, TypeError):
            return frozenset()
    return frozenset(t.strip().lower() for t in text.split(",") if t.strip())


def _check_embed_model_consistency(state: Any, spec: Any, log: Any) -> bool:  # noqa: ANN401
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


def _render_captured_slots(action_state: dict, action_cfg: dict) -> str:
    """Render captured + still-missing slot DATA for owner placeholder binding.

    Sacred-rule 10: this emits structured DATA only (key="value" + a neutral
    ``missing:`` list of required-but-unfilled slot names) — NO behavioural
    text, NO instruction, NO brand/domain literal. The bot owner places
    ``{captured_slots}`` in their ``system_prompt`` and writes the surrounding
    instruction themselves; the platform merely substitutes the live values so
    the LLM can ask only for what is missing instead of re-asking captured info.

    Tokens ``missing``/``none`` are neutral technical markers (not Vietnamese
    behavioural copy), keeping the binding language- and domain-agnostic.
    """
    filled: dict = (action_state or {}).get("slots_filled", {}) or {}
    # Required slots come from the matching sub-schema (by current intent, else
    # the first declared sub-schema) — same selection slot_extractor uses.
    schema: dict = (action_cfg or {}).get("slots_schema", {}) or {}
    intent = (action_state or {}).get("intent") or ""
    sub_key = intent if intent in schema else next(iter(schema), None)
    required: list = list((schema.get(sub_key, {}) or {}).get("required", [])) if sub_key else []

    pairs = [f'{k}="{v}"' for k, v in filled.items() if v not in (None, "")]
    missing = [s for s in required if not filled.get(s)]
    filled_str = ", ".join(pairs) if pairs else "none"
    missing_str = ", ".join(missing) if missing else "none"
    return f"{filled_str}; missing: {missing_str}"


def _compute_bot_cache_version(system_prompt: str | None, oos_answer_template: str | None) -> str:
    """Derive cache-bust version; changes when system_prompt or oos_answer_template change."""
    payload = (system_prompt or "") + "|" + (oos_answer_template or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DEFAULT_BOT_CACHE_VERSION_HASH_LEN]


def _is_null_lexical(adapter: Any) -> bool:
    """Return True when ``adapter`` is the Null Object for lexical retrieval.

    Probes ``get_provider_name``/``mode`` instead of an ``isinstance`` check
    so test doubles + future replacement Null adapters work uniformly.
    A bare object that doesn't expose either marker is treated as a real
    adapter (conservative — better to attempt a search than silently skip).
    """
    if adapter is None:
        return True
    get_name = getattr(adapter, "get_provider_name", None)
    if callable(get_name):
        try:
            if get_name() == "null":
                return True
        except (AttributeError, TypeError, RuntimeError):
            # Best-effort probe; treat probe failure as real adapter
            # (conservative — better to attempt search than silently skip).
            return False
    mode_attr = getattr(adapter, "mode", None)
    if isinstance(mode_attr, str) and mode_attr == "null":
        return True
    return False


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
    except Exception as exc:  # noqa: BLE001 — background task; response already shipped, must not crash worker loop
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
                    except asyncio.TimeoutError as exc:
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
        except (EmbeddingError, asyncio.TimeoutError, OSError,
                RuntimeError, ValueError, AttributeError):
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
        except (EmbeddingError, asyncio.TimeoutError, OSError,
                RuntimeError, ValueError, AttributeError) as _emb_exc:
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

    async def guard_input(state: GraphState) -> dict:
        async with state["step_tracker"].step("guard_input"):
            # Pre-load DB-driven language pack rows so downstream nodes read from the same source.
            lpack_rows: dict[str, str] | None = None
            if language_pack_service is not None:
                try:
                    lpack_rows = await language_pack_service.get_pack(
                        state.get("language", DEFAULT_LANGUAGE),
                    )
                except (OSError, RuntimeError, AttributeError,
                        KeyError, ValueError):
                    # Defensive: language-pack lookup failure must never
                    # block the input guard pipeline.
                    lpack_rows = None
            flags = list(state.get("guardrail_flags", []))
            try:
                hits = await guardrail.check_input(
                    state["query"],
                    tenant_id=state.get("record_tenant_id"),
                    message_id=state["message_id"],
                    request_id=state.get("request_id"),
                )
                for h in hits:
                    flags.append(
                        {
                            "stage": "input",
                            "rule_id": h.rule_id,
                            "severity": h.severity,
                            "action": h.action,
                        }
                    )
                out: dict[str, Any] = {"guardrail_flags": flags}
                if lpack_rows is not None:
                    out["_language_pack_rows"] = lpack_rows
                return out
            except GuardrailBlocked as exc:
                # Per-rule response_message overrides bot-level oos_answer_template.
                blocked_answer = _resolved_oos_template(state)
                for h in exc.hits:
                    flags.append(
                        {
                            "stage": "input",
                            "rule_id": h.rule_id,
                            "severity": h.severity,
                            "action": h.action,
                            "blocked": True,
                        }
                    )
                    if h.severity == "block" and h.details.get("response_message"):
                        blocked_answer = h.details["response_message"]
                out_blocked: dict[str, Any] = {
                    "guardrail_flags": flags,
                    "answer": blocked_answer,
                    "answer_type": "blocked",
                    "answer_reason": "Input guardrail blocked",
                }
                if lpack_rows is not None:
                    out_blocked["_language_pack_rows"] = lpack_rows
                return out_blocked

    async def check_cache(state: GraphState) -> dict:
        """Lookup semantic cache; short-circuit on hit."""
        async with state["step_tracker"].step("cache_check") as cc_ctx:
            await _audit(
                state,
                "query_received",
                {
                    "question": (state.get("query") or "")[:DEFAULT_QUERY_RECEIVED_AUDIT_PREVIEW_CHARS],
                    "tenant_id": str(state.get("record_tenant_id") or ""),
                    "channel_type": state.get("channel_type"),
                    "message_id": state.get("message_id"),
                    "history_messages": len(state.get("conversation_history") or []),
                },
            )
            if state.get("bypass_cache"):
                await _audit(state, "cache_check", {"hit": False, "reason": "bypass_test_mode"})
                cc_ctx.set_metadata(hit=False, reason="bypass_test_mode", bypass=True)
                return {"cache_status": "bypassed", "cache_hit": False}
            # Multi-turn follow-ups are context-dependent: the lookup runs on
            # the RAW query (before condense), so a pronoun query like "nó làm
            # những bước nào" is identical text across conversations but refers
            # to a DIFFERENT subject each time. A cosine hit would return a
            # stale cross-context answer. Skip the cache entirely when there is
            # conversation history (correctness > hit-rate; single-turn caches).
            if _pcfg(
                state, "semantic_cache_skip_multi_turn", DEFAULT_SEMANTIC_CACHE_SKIP_MULTI_TURN,
            ) and (state.get("conversation_history") or []):
                await _audit(state, "cache_check", {"hit": False, "reason": "skip_multi_turn"})
                cc_ctx.set_metadata(hit=False, reason="skip_multi_turn", bypass=False)
                return {"cache_status": "skip_multi_turn", "cache_hit": False}
            if semantic_cache is None:
                await _audit(state, "cache_check", {"hit": False, "reason": "no_semantic_cache"})
                cc_ctx.set_metadata(hit=False, reason="no_semantic_cache", bypass=False)
                return {}
            bot_cache_version = _compute_bot_cache_version(
                state.get("bot_system_prompt", ""), _resolved_oos_template(state),
            )
            try:
                query_text = state["query"]
                # corpus_version (memoised per turn via state["_corpus_version"],
                # reused at cache_store so write+read see an identical key) and
                # query_embedding are independent — gather them. corpus_version
                # reads tenant/bot; embed reads query_text; neither feeds the
                # other, both feed the cache lookup below. _resolve_corpus_version
                # degrades internally (never raises) so it is safe inside the try.
                corpus_version, query_embedding = await asyncio.gather(
                    _resolve_corpus_version(state),
                    _embed_query(query_text, state),
                )
                if not query_embedding:
                    await _audit(state, "cache_check", {"hit": False, "reason": "no_embedding"})
                    cc_ctx.set_metadata(hit=False, reason="no_embedding", bypass=False)
                    return {
                        "_bot_cache_version": bot_cache_version,
                        "_corpus_version": corpus_version,
                    }
                cached = await semantic_cache.find_similar_with_text(
                    query_embedding=query_embedding,
                    query_text=query_text,
                    record_tenant_id=state.get("record_tenant_id"),
                    record_bot_id=state.get("record_bot_id"),
                    bot_version=bot_cache_version,
                    corpus_version=corpus_version,
                    threshold=_pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD),
                    step_tracker=state["step_tracker"],
                    # Routes the cache lookup to the column matching this bot's embedding dim.
                    embedding_column=state.get("embedding_column"),
                    # Cross-process single-flight: two uvicorn workers serving
                    # the same hot query must not both compute. Falls back to
                    # in-process asyncio.Lock when redis_client is None.
                    redis_client=redis_client,
                )
                if cached is not None:
                    await _audit(
                        state,
                        "cache_check",
                        {
                            "hit": True,
                            "threshold": _pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD),
                        },
                    )
                    cached_total = (getattr(cached, "prompt_tokens", 0) or 0) + (
                        getattr(cached, "completion_tokens", 0) or 0
                    )
                    # Wave M3.6-G3 — emit hit-rate event + capture cosine
                    # similarity for cache-effectiveness dashboard.
                    # WHY: pre-fix the cache hit/miss outcome was logged
                    # via _audit only (forensic) without a structured
                    # ``semantic_cache_outcome`` event that aggregates into
                    # hit-rate metrics. With this event, ops can compute
                    # hit-rate / day from log analytics without touching
                    # audit_log tables (kept lean per CLAUDE.md observability
                    # rule). Cosine similarity exposed so a future
                    # ``cache_similarity_threshold`` tune has empirical data.
                    _cache_sim = float(getattr(cached, "similarity", 0.0) or 0.0)
                    _cache_threshold = float(
                        _pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD)
                    )
                    logger.info(
                        "semantic_cache_outcome",
                        hit=True,
                        similarity=round(_cache_sim, 4),
                        threshold=_cache_threshold,
                        record_bot_id=str(state.get("record_bot_id") or ""),
                        intent=state.get("intent") or "",
                    )
                    cc_ctx.set_metadata(
                        hit=True,
                        reason="cosine_sim",
                        bypass=False,
                        similarity=round(_cache_sim, 4),
                        threshold=_cache_threshold,
                    )
                    # 2026-05-27 — restore graded_chunks from cache snapshot
                    # so /chat API ``_build_sources`` can rebuild sources on
                    # cache_hit (RAGAS judge, audit tools, evaluator). When
                    # snapshot empty (legacy row) sources stays [].
                    _restored_chunks = [dict(c) for c in (cached.chunks or ())]
                    return {
                        "answer": cached.answer,
                        "answer_type": "cache_hit",
                        "answer_reason": "Semantic cache hit",
                        "citations": list(cached.citations),
                        "graded_chunks": _restored_chunks,
                        "model_used": cached.model_name or "",
                        "cache_status": "hit",
                        "tokens": {"prompt": 0, "completion": 0, "cached": cached_total},
                        "cost_usd": 0.0,
                        "_bot_cache_version": bot_cache_version,
                        "_corpus_version": corpus_version,
                    }
            except (AttributeError, TypeError, KeyError):
                # Programmer errors (renamed column, missing DI, bad state key) must surface.
                raise
            except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
                # Cache lookup transport / shape failure → degrade silent.
                logger.warning("semantic_cache_check_failed", exc_info=True)
            await _audit(state, "cache_check", {"hit": False, "reason": "miss_or_error"})
            # Wave M3.6-G3 — miss path emits the same event shape so the
            # ``semantic_cache_outcome`` aggregator sees hit + miss in one
            # query (avoid forensic-only audit_log scan). Similarity 0.0
            # because no cached row was returned by ``find_similar_with_text``.
            logger.info(
                "semantic_cache_outcome",
                hit=False,
                similarity=0.0,
                threshold=float(
                    _pcfg(state, "cache_similarity_threshold", DEFAULT_CACHE_SIMILARITY_THRESHOLD)
                ),
                record_bot_id=str(state.get("record_bot_id") or ""),
                intent=state.get("intent") or "",
            )
            cc_ctx.set_metadata(hit=False, reason="miss_or_error", bypass=False)
            return {
                "_bot_cache_version": bot_cache_version,
                "_corpus_version": corpus_version,
            }

    async def condense_question(state: GraphState) -> dict:
        """Condense conversation history + new question into a standalone query.

        Threshold lowered 2026-05-27: ``len(history) <= 2`` skipped follow-up
        after the very first turn (history = [user_T1, bot_T1] = 2 messages),
        which is when condense matters most (T2 may reference T1 entity with
        pronoun). Now ``< 2`` so the first follow-up triggers condense.
        Eval root-cause: "có ưu đãi gì k em" after "triệt lông nửa chân"
        was reaching rewrite with raw query (no T1 context), routing to
        generic promo chunks. Condense now bridges that gap.
        """
        # Normalize Vietnamese legal section numerals (Roman → Arabic) so the
        # query matches chunks whose structural path is stored in arabic
        # canonical form. Pre-2026-05-27, "Chương III" query missed all
        # "Chương III" chunks because the chunker also stored Roman literally;
        # after ship-260527 the chunker stores arabic, this mirror keeps the
        # two sides aligned. No-op for queries without Chương|Mục|Phần.
        _raw_q = state.get("query", "") or ""
        _norm_q = normalize_vn_section_numerals(_raw_q) if _raw_q else _raw_q
        _query_patch: dict[str, str] = {}
        if _norm_q != _raw_q:
            _query_patch["query"] = _norm_q
            state["query"] = _norm_q
        history = state.get("conversation_history", [])
        if not history or len(history) < DEFAULT_CONDENSE_MIN_HISTORY_TURNS:
            return _query_patch
        total_chars = sum(len(m.get("content", "")) for m in history)
        if total_chars < DEFAULT_CONDENSE_MIN_HISTORY_CHARS:
            return _query_patch
        # Wave M3.7-G1 — name the step context so the post-LLM payload
        # (model + tokens + cost) can be recorded onto request_steps.
        # WHY: M3.6 admin audit found request_steps.model_used /
        # cost_usd were NULL on 9/10 LLM-bound steps; only ``generate``
        # was wired (M3.2 Phase 1). Without per-step capture the cost
        # dashboard can not attribute spend to condense / understand /
        # multi_query / grade / etc. Recording is best-effort: if the
        # payload is missing keys we just skip (helper returns ints).
        async with state["step_tracker"].step("condense_question") as condense_ctx:
            _pack = _lang(state)
            history_text = "\n".join(
                f"{_pack.condense_user_role if m.get('role') == 'user' else _pack.condense_bot_role}: {m.get('content', '')}"
                for m in history[-_pcfg(state, "condense_history_limit", DEFAULT_CONDENSE_HISTORY_LIMIT):]
            )
            messages = [
                {"role": "system", "content": _pack.prompt_condense},
                {"role": "user", "content": (
                    f"{_pack.condense_history_label}:\n{history_text}\n\n"
                    f"{_pack.condense_new_question_label}: {state['query']}\n\n"
                    f"{_pack.condense_standalone_label}:"
                )},
            ]
            try:
                payload, _ctx = await _invoke_llm_node(
                    state, purpose="condensing", messages=messages, user_prompt=state["query"],
                )
                # Wave M3.7 — record model + tokens + cost into request_steps.
                condense_ctx.record_llm(
                    model_used=str(payload.get("model_name") or "") or None,
                    prompt_tokens=int(payload.get("prompt_tokens") or 0),
                    completion_tokens=int(payload.get("completion_tokens") or 0),
                    cost_usd=float(payload.get("cost_usd") or 0.0),
                )
                condensed = (payload["text"] or "").strip()
                if condensed:
                    # Re-apply numeral normalize on the LLM-condensed output:
                    # an LLM rewrite may introduce roman numerals even if the
                    # incoming query was arabic (history could contain roman).
                    condensed = normalize_vn_section_numerals(condensed)
                    return {"query": condensed, "original_query": state["query"]}
            except (InvariantViolation, asyncio.TimeoutError, OSError,
                    RuntimeError, ValueError):
                # Condense is opportunistic; LLM/router/transport failure
                # falls through to the original query unchanged.
                logger.debug("condense_question_skipped")
            return _query_patch

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
                    "embedding_model_version": "v1",
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
        except Exception:  # noqa: BLE001 — best-effort background task wrapper
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
        except (InvariantViolation, asyncio.TimeoutError, OSError,
                RuntimeError, ValueError, KeyError) as exc:
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
            except asyncio.TimeoutError:
                logger.warning("speculative_retrieve_timeout")
                spec_embed, spec_chunks = [], []
            except asyncio.CancelledError:
                spec_embed, spec_chunks = [], []
            if spec_embed and spec_chunks:
                merged["_speculative_raw_embed"] = spec_embed
                merged["_speculative_chunks"] = spec_chunks
        if spec_mq_task is not None:
            # Decide whether the resolved intent benefits from the
            # speculative paraphrase set. Multi-hop / synthesis / docs-
            # only consume MQ; chitchat / OOS / factoid_single discard.
            # When the intent is incompatible we cancel + suppress so
            # the task leaves no orphan and the LLM cost is bounded by
            # ``pipeline_multi_query_speculative_timeout_s``.
            _resolved_intent = (merged.get("intent") or "").strip().lower()
            _mq_consumable_intents = {
                INTENT_MULTI_HOP, "synthesis", "compound", "docs_only",
            }
            if _resolved_intent in _mq_consumable_intents:
                try:
                    mq_variants = await spec_mq_task
                except asyncio.TimeoutError:
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

    async def router(state: GraphState) -> dict:
        async with state["step_tracker"].step("router"):
            if model_resolver is None or llm is None:
                raise InvariantViolation("LLM runtime not configured for node=routing")
            messages = [
                {"role": "system", "content": _lang(state).prompt_understand},
                {"role": "user", "content": f"<question>{state['query']}</question>"},
            ]
            payload, _ctx = await _invoke_llm_node(
                state,
                purpose="routing",
                messages=messages,
                user_prompt=state["query"],
            )
            raw = (payload["text"] or "").strip().lower()
            intent = DEFAULT_INTENT_FALLBACK
            for cand in _VALID_INTENTS:
                if cand in raw:
                    intent = cand
                    break
            update: dict = {"intent": intent}
            return update

    async def rewrite(state: GraphState) -> dict:
        # Wave M3.7-G1 — name step ctx so the rewrite LLM cost is
        # attributed to its row in request_steps (was NULL pre-fix).
        async with state["step_tracker"].step("rewrite") as rw_ctx:
            # Per-intent skip gate: lightweight intents (greeting/chitchat/
            # factoid/feedback/vu_vo/out_of_scope) do not benefit from query
            # reformulation — skip the LLM call and carry the original query
            # forward unchanged. Saves ~1.2s per turn on those intent classes
            # with zero T1 quality regression (HALLU=0 sacred unaffected;
            # grounding_check still validates the final answer).
            _rewrite_intent = str(state.get("intent") or "")
            _rewrite_enabled_map = _pcfg(
                state, "rewrite_enabled_by_intent", None,
            )
            if isinstance(_rewrite_enabled_map, dict) and _rewrite_intent in _rewrite_enabled_map:
                try:
                    _intent_rewrite_enabled = bool(_rewrite_enabled_map[_rewrite_intent])
                except (TypeError, ValueError):
                    _intent_rewrite_enabled = True
            else:
                # Unknown intent or no override → default constant lookup.
                _intent_rewrite_enabled = DEFAULT_REWRITE_ENABLED_BY_INTENT.get(
                    _rewrite_intent, True,
                )
            if not _intent_rewrite_enabled:
                rw_ctx.set_metadata(
                    skipped=True,
                    reason="per_intent_disabled",
                    intent=_rewrite_intent,
                )
                return {"rewritten_query": state["query"]}

            if model_resolver is None or llm is None:
                raise InvariantViolation("LLM runtime not configured for node=rewriting")
            query = state["query"]
            # Thread last 2 history pairs into rewrite so multi-turn pronouns
            # ("có ưu đãi không" after "triệt lông") resolve correctly.
            # Empty history → user_content stays the raw query, byte-identical
            # to pre-2026-05-27 behaviour for first-turn requests.
            _rw_history = state.get("conversation_history", []) or []
            if _rw_history:
                # Last 2 pairs = 4 messages (user/assistant × 2)
                _recent = _rw_history[-4:]
                _hist_lines: list[str] = []
                for m in _recent:
                    role_label = (
                        "User" if m.get("role") == "user" else "Assistant"
                    )
                    content = (m.get("content") or "")[:200]
                    _hist_lines.append(f"{role_label}: {content}")
                _user_content = (
                    "Conversation context (last turns):\n"
                    + "\n".join(_hist_lines)
                    + f"\n\nCurrent query to rewrite: {query}\n\n"
                    + "Rewrite the current query as a standalone search query, "
                    + "expanding any pronouns or implicit references from the "
                    + "conversation context (e.g. 'có ưu đãi không' after "
                    + "discussing service X should become 'có ưu đãi cho dịch "
                    + "vụ X không')."
                )
            else:
                _user_content = query
            messages = [
                {"role": "system", "content": _lang(state).prompt_rewriter},
                {"role": "user", "content": _user_content},
            ]
            payload, _ctx = await _invoke_llm_node(
                state,
                purpose="rewriting",
                messages=messages,
                user_prompt=state["query"],
            )
            rw_ctx.record_llm(
                model_used=str(payload.get("model_name") or "") or None,
                prompt_tokens=int(payload.get("prompt_tokens") or 0),
                completion_tokens=int(payload.get("completion_tokens") or 0),
                cost_usd=float(payload.get("cost_usd") or 0.0),
            )
            rewritten = payload["text"] or state["query"]
            return {"rewritten_query": rewritten}

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
            except (asyncio.TimeoutError, OSError, RuntimeError,
                    ValueError, KeyError, AttributeError):
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

    async def decompose(state: GraphState) -> dict:
        """LLM-decompose multi-hop query into 2-4 sub-questions."""
        async with state["step_tracker"].step("decompose"):
            query = state.get("rewritten_query") or state["query"]
            messages = [
                {"role": "system", "content": _lang(state).prompt_decompose},
                {"role": "user", "content": query},
            ]
            so_master = _pcfg(state, "structured_output_enabled", DEFAULT_STRUCTURED_OUTPUT_ENABLED)
            so_node = _pcfg(state, "decompose_use_structured_output", DEFAULT_DECOMPOSE_USE_STRUCTURED_OUTPUT)
            use_structured = bool(so_master) and bool(so_node)
            try:
                if use_structured:
                    parsed, ctx = await _invoke_structured_llm_node(
                        state,
                        purpose="decompose",
                        messages=messages,
                        user_prompt=query,
                        schema=DecomposeOutput,
                    )
                    if parsed is not None:
                        if ctx is not None:
                            _u = _so_usage(ctx)
                            ctx.record(
                                response=_json_mod.dumps(parsed.model_dump()),
                                prompt_tokens=_u["prompt_tokens"],
                                completion_tokens=_u["completion_tokens"],
                                cost_usd=_u["cost_usd"],
                                finish_reason=_u["finish_reason"],
                            )
                        sub_queries = [s.strip() for s in (parsed.sub_queries or []) if s and s.strip()]
                        if len(sub_queries) >= 2:
                            logger.info(
                                "query_decomposed",
                                original=query[:80],
                                sub_count=len(sub_queries),
                                source="structured_output",
                            )
                            return {"sub_queries": sub_queries, "original_query": query}
                        return {}
                payload, ctx = await _invoke_llm_node(
                    state, purpose="decompose", messages=messages, user_prompt=query,
                )
                ctx.record(
                    response=payload["text"],
                    prompt_tokens=payload["prompt_tokens"],
                    completion_tokens=payload["completion_tokens"],
                    cost_usd=payload["cost_usd"],
                    finish_reason=payload["finish_reason"],
                )
                sub_queries = parse_decomposed_sub_queries(payload["text"] or "")
                if sub_queries:
                    logger.info("query_decomposed", original=query[:80], sub_count=len(sub_queries))
                    return {"sub_queries": sub_queries, "original_query": query}
            except (InvariantViolation, asyncio.TimeoutError, OSError,
                    RuntimeError, ValueError, KeyError):
                # Decompose is opportunistic; failure leaves the original
                # query as a single-pass retrieve.
                logger.debug("decompose_skipped", query=query[:80])
            return {}

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
            if _operation in ("max", "min"):
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
            linked_chunks: list[dict] = []
            if chunk_ids and doc_repo is not None and hasattr(
                doc_repo, "find_chunks_by_ids"
            ):
                try:
                    linked_chunks = await doc_repo.find_chunks_by_ids(
                        chunk_ids,
                        record_bot_id=state["record_bot_id"],
                    )
                except (OSError, RuntimeError, ValueError, KeyError,
                        AttributeError):
                    logger.warning(
                        "stats_index_chunk_fetch_failed",
                        exc_info=True,
                    )
            # Doc-level fallback when no chunk_id linkage exists yet.
            if not linked_chunks and doc_ids and doc_repo is not None and hasattr(
                doc_repo, "find_chunks_by_document_ids"
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
            # Surface the filtered/ranked rows as a synthetic context chunk.
            # The stats rows carry no chunk FK, so linked_chunks are doc-level
            # (the whole price table) — the LLM then has to re-filter "< 500k"
            # itself and often misses the matching rows. Handing it the already
            # filtered/ranked name+price list makes the aggregation result
            # explicit. Grounded data only (values extracted from the corpus),
            # no instruction text → not an app-inject (QG #10). Deduped + capped.
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
                # Currency-neutral: emit the raw number only (the corpus may be
                # in any currency — appending "VND" would break a USD/EUR bot).
                _parts = [f"{_name}: {int(_price)}"] if _price is not None else [_name]
                # Surface the remaining structured columns (answer/quantity/date/
                # image/...) generically so a record-shaped bot (e.g. an n8n
                # results[] consumer) gets every field, not just the price. The
                # column names come from the corpus header — domain-neutral, no
                # hard-coded field list. Skip internal keys + mega-cells (the
                # huge synonym/variant column that would dilute the chunk).
                _cat = _e.get("entity_category")
                if _cat:
                    _parts.append(f"category: {_cat}")
                _attrs = _e.get("attributes_json")
                if isinstance(_attrs, dict):
                    for _k, _v in _attrs.items():
                        if _k in ("chunk_index", "question", "variants"):
                            continue
                        _vs = str(_v).strip()
                        if not _vs or len(_vs) > DEFAULT_STATS_ATTR_MAX_CHARS:
                            continue
                        _parts.append(f"{_k}: {_vs}")
                _rows.append(" | ".join(_parts))
                if len(_rows) >= DEFAULT_STATS_INDEX_LIMIT:
                    break
            synthetic_chunks: list[dict] = []
            if _rows:
                _body = "\n".join(_rows)
                synthetic_chunks.append({
                    "content": _body,
                    "text": _body,
                    "chunk_id": "",
                    "document_name": "",
                    "score": 1.0,
                    "source": "stats_index",
                })
            return {
                "entities": entities,
                "linked_chunks": synthetic_chunks + linked_chunks,
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

    async def mmr_dedup(state: GraphState) -> dict:
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
                    mmr_thresh = float(_pcfg(state, "mmr_similarity_threshold", DEFAULT_MMR_SIMILARITY_THRESHOLD))
            else:
                mmr_thresh = float(_pcfg(state, "mmr_similarity_threshold", DEFAULT_MMR_SIMILARITY_THRESHOLD))
            mmr_lambda = float(_pcfg(state, "mmr_lambda", DEFAULT_MMR_LAMBDA))
            filtered = mmr_filter(
                chunks,
                lambda_param=mmr_lambda,
                similarity_threshold=mmr_thresh,
                strip_embedding=True,
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

    async def neighbor_expand(state: GraphState) -> dict:
        """M2 — expand ±N chunk_index neighbours after MMR.

        Per-bot opt-in via ``plan_limits.neighbor_expand_enabled``. When
        the flag is False (default) the node returns ``{}`` — a no-op
        merge that LangGraph treats as identity, so production paths
        stay byte-identical until bot owners opt in.

        When enabled the node fetches adjacent chunks (window radius
        ``neighbor_window_size``) from the same documents as the MMR
        survivors, applies a token-budget cap (M22), and replaces
        ``reranked_chunks`` with the expanded set. ``grade`` then sees
        the broader context. HALLU=0 sacred is preserved — no
        fabrication, only existing chunks surface.
        """
        enabled = bool(_pcfg(state, "neighbor_expand_enabled",
                             DEFAULT_NEIGHBOR_EXPAND_ENABLED))
        if not enabled:
            return {}
        seeds = state.get("reranked_chunks") or []
        if not seeds:
            return {}
        session_factory = state.get("session_factory")
        record_tenant_id = state.get("record_tenant_id")
        if session_factory is None or record_tenant_id is None:
            return {}
        window = int(_pcfg(state, "neighbor_window_size",
                           DEFAULT_NEIGHBOR_WINDOW_SIZE))
        budget = int(_pcfg(state, "neighbor_token_budget",
                           DEFAULT_NEIGHBOR_TOKEN_BUDGET))
        max_conc = int(_pcfg(state, "neighbor_max_concurrency",
                             DEFAULT_NEIGHBOR_MAX_CONCURRENCY))
        from ragbot.orchestration.nodes.neighbor_expand import expand_neighbors
        async with state["step_tracker"].step("neighbor_expand") as ne_ctx:
            expanded = await expand_neighbors(
                seeds,
                session_factory=session_factory,
                record_tenant_id=record_tenant_id,
                window_size=window,
                token_budget=budget,
                max_concurrency=max_conc,
            )
            ne_ctx.set_metadata(
                seeds_in=len(seeds),
                chunks_out=len(expanded),
                window_size=window,
                token_budget=budget,
                expanded_count=max(0, len(expanded) - len(seeds)),
            )
            await _audit(
                state,
                "neighbor_expand",
                {
                    "before": len(seeds),
                    "after": len(expanded),
                    "window_size": window,
                    "token_budget": budget,
                },
            )
            return {"reranked_chunks": expanded}

    async def rewrite_retry(state: GraphState) -> dict:
        """CRAG retry path: rewrite query and increment retry counter."""
        async with state["step_tracker"].step("rewrite_retry") as rr_ctx:
            attempt = state.get("grade_retries", 0) + 1
            max_retries = int(
                _pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES)
            )
            graded_count = len(state.get("graded_chunks") or [])
            triggered_by = (
                "grade_low" if graded_count == 0 else "grade_ambiguous"
            )
            original_query = (state.get("query") or "")
            result = await rewrite(state)
            result["grade_retries"] = attempt
            rewritten_query = result.get("rewritten_query") or ""
            n_chunks_after = len(state.get("retrieved_chunks") or [])
            rr_ctx.set_metadata(
                attempt=attempt,
                max_retries=max_retries,
                triggered_by=triggered_by,
                original_query_preview=str(original_query)[:80],
                rewritten_query_preview=str(rewritten_query)[:80],
                n_chunks_after=n_chunks_after,
            )
            return result

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

    async def critique_parse(state: GraphState) -> dict:
        """Self-RAG critique-token post-processor — opt-in per-bot.

        Reads ``plan_limits.self_rag_critique_enabled``.  When OFF returns
        ``{}`` so LangGraph treats the step as identity (byte-identical to
        the legacy path).  When ON parses ``[Supported]`` / ``[Unsupported]``
        markers; markers are always stripped (cosmetic).  When the
        unsupported ratio meets/exceeds the bot's threshold the answer is
        replaced by ``bots.oos_answer_template`` (Quality Gate #10 — never
        i18n fallback).  Parse failure ⇒ fail-open: log warning, return
        the raw answer untouched (HALLU=0 sacred preserved).
        """
        if not bool(_pcfg(state, "self_rag_critique_enabled", DEFAULT_SELF_RAG_ENABLED)):
            return {}
        raw = state.get("answer") or ""
        if not raw:
            return {}
        async with state["step_tracker"].step("critique_parse") as cp_ctx:
            try:
                parsed = _parse_critique_tokens(raw)
            except Exception:  # noqa: BLE001 — fail-open: HALLU=0 sacred, never lose answer
                logger.warning("critique_parse_failed", exc_info=True)
                return {}

            total_claims = int(parsed.get("total_claims", 0) or 0)
            unsupported = int(parsed.get("unsupported_count", 0) or 0)
            ratio = float(parsed.get("unsupported_ratio", 0.0) or 0.0)
            threshold = float(_pcfg(
                state,
                "self_rag_critique_threshold",
                DEFAULT_SELF_RAG_THRESHOLD,
            ))
            clean_text = parsed.get("clean_text") or raw

            should_refuse = _should_refuse_critique(parsed, threshold)
            cp_ctx.set_metadata(
                total_claims=total_claims,
                unsupported_count=unsupported,
                unsupported_ratio=round(ratio, 4),
                threshold=threshold,
                refused=bool(should_refuse),
            )

            if should_refuse:
                # Refusal text origin = bots.oos_answer_template (per-bot DB
                # column).  Empty fallback when the operator has not set
                # one — never an i18n hardcoded string.  Quality Gate #10.
                bot_template = _oos_text(state)
                template = bot_template or DEFAULT_OOS_ANSWER_TEMPLATE
                logger.info(
                    "critique_parse_refused",
                    request_id=str(state.get("request_id") or ""),
                    record_bot_id=str(state.get("record_bot_id") or ""),
                    total_claims=total_claims,
                    unsupported_count=unsupported,
                    unsupported_ratio=round(ratio, 4),
                    threshold=threshold,
                )
                return {
                    "answer": template,
                    "answer_type": INTENT_OUT_OF_SCOPE,
                    "answer_reason": "self_rag_unsupported_ratio_exceeded",
                }
            # Strip markers from the user-visible answer; preserve every
            # other field (LLM owns the prose).
            if clean_text != raw:
                return {"answer": clean_text}
            return {}

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

    def _input_blocked(state: GraphState) -> str:
        for f in state.get("guardrail_flags", []):
            if f.get("stage") == "input" and f.get("blocked"):
                return "persist"
        return "check_cache"

    def _cache_route(state: GraphState) -> str:
        """If cache hit produced an answer, skip to persist."""
        if state.get("cache_status") == "hit" and state.get("answer"):
            return "persist"
        if _pcfg(state, "merge_condense_router", True):
            return "understand_query"
        return "condense_question"

    def _understand_query_route(state: GraphState) -> str:
        """Route after understand_query (merged condense+router).

        Adaptive Router L1: when ``adaptive_router_l1_enabled`` is True
        and the LLM-emitted intent is NOT already ``multi_hop`` (which
        already triggers the legacy decompose path with its own
        confidence gate), divert into the domain-neutral regex/heuristic
        classifier. The classifier is microseconds; on "complex" it
        seeds ``sub_queries`` via ``adaptive_decompose`` so the existing
        retrieve / fanout (S2 bypass) consumes them. On "simple" it
        falls back to the legacy router so the byte-identical path is
        preserved for all non-multi-entity questions.
        """
        if _pcfg(
            state, "adaptive_router_l1_enabled", DEFAULT_ADAPTIVE_ROUTER_L1_ENABLED,
        ):
            existing_subs = [
                s for s in (state.get("sub_queries") or [])
                if isinstance(s, str) and s.strip()
            ]
            if (
                state.get("intent") != INTENT_MULTI_HOP
                and len(existing_subs) < 2
            ):
                return "query_complexity"
        return _router_route(state)

    def _complexity_route(state: GraphState) -> str:
        """Route after Layer 1 classifier: complex → L3 decomposer, else legacy router."""
        if state.get("complexity_label") == "complex":
            return "adaptive_decompose"
        return _router_route(state)

    def _router_route(state: GraphState) -> str:
        """Adaptive query routing: skip nodes based on intent. All intents flow through retrieve → generate."""
        intent = state.get("intent", DEFAULT_INTENT_FALLBACK)
        if intent == INTENT_MULTI_HOP and _pcfg(state, "decompose_enabled", True):
            _query_text = (state.get("query") or "").strip()
            _decompose_min = int(_pcfg(
                state, "decompose_min_tokens", DEFAULT_DECOMPOSE_MIN_TOKENS,
            ))
            if len(_query_text.split()) >= _decompose_min:
                # Confidence gate. Skip decompose (5 sub-query fan-out is
                # wasteful) when the classifier reports low confidence. Bot
                # owner can override via per-bot
                # ``decompose_confidence_gate`` pipeline_config slot.
                _conf_gate = float(_pcfg(
                    state,
                    "decompose_confidence_gate",
                    DEFAULT_DECOMPOSE_CONFIDENCE_GATE,
                ))
                _conf = float(state.get(
                    "intent_confidence",
                    DEFAULT_INTENT_CONFIDENCE_FALLBACK,
                ))
                if _conf < _conf_gate:
                    if decompose_skipped_low_confidence_total is not None:
                        try:
                            decompose_skipped_low_confidence_total.labels(
                                intent=str(intent),
                            ).inc()
                        except (ValueError, KeyError, AttributeError):
                            pass
                    logger.info(
                        "decompose_skipped_low_confidence",
                        intent=intent,
                        confidence=_conf,
                        gate=_conf_gate,
                    )
                else:
                    return "decompose"
        skip_rewrite = _pcfg(state, "skip_rewrite_intents", DEFAULT_SKIP_REWRITE_INTENTS)
        if intent in skip_rewrite:
            return "retrieve"
        return "rewrite"

    def _grade_route(state: GraphState) -> str:
        """Route after grade: rewrite_retry while retries left, else generate.

        Smart-skip (S1 Pipeline-Opt, T1-Smartness): the grade node itself
        short-circuits when ``crag_skip_retry_above_score`` is exceeded by
        pass-1 top score (see early-exit block in ``grade``). That path
        sets ``crag_skip_retry=True`` and ``retrieval_adequate=True`` so
        this router falls through to ``generate`` without an LLM call.

        Legacy belt-and-suspenders gate retained: if pass-1 grading marked
        the result inadequate but the rerank score still clears the floor,
        bypass rewrite_retry. The grounding_check guardrail downstream
        enforces HALLU=0 regardless of which path was taken.
        """
        # Fast path: skip flag was set in the grade node's early-exit.
        if state.get("crag_skip_retry"):
            return "generate"
        if not state.get("retrieval_adequate", True):
            retries = state.get("grade_retries", 0)
            if retries < _pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES):
                skip_floor = float(_pcfg(
                    state,
                    "crag_skip_retry_above_score",
                    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
                ))
                if skip_floor > 0 and retries == 0:
                    # Inspect pass-1 reranked chunks (graded_chunks may already
                    # be filtered to relevant+ambiguous, so prefer the upstream
                    # pool which carries the rerank score distribution).
                    pool = state.get("reranked_chunks") or state.get("graded_chunks") or []
                    top_score = 0.0
                    for c in pool:
                        s = float(c.get("score", 0) or 0)
                        if s > top_score:
                            top_score = s
                    if top_score >= skip_floor:
                        logger.info(
                            "crag_retry_smart_skip",
                            top_score=round(top_score, 4),
                            skip_floor=skip_floor,
                        )
                        return "generate"
                return "rewrite_retry"
        return "generate"

    def _output_blocked(state: GraphState) -> str:
        """Route after guard_output: persist when blocked, when bot did not
        opt into reflect, or when intent is in skip_reflect; else reflect.

        Reflect-gate (added 2026-05-18): bot owners opt in via
        ``plan_limits.reflection_enabled``. The default is False — matching
        ``shared/bot_limits.PLAN_LIMIT_SCHEMA``. Production audit
        (req 9cf611b5) found reflect firing 2× per turn (3.57s wasted) on
        bots that never enabled it. Gating here saves the round-trip
        without touching the reflect node implementation.
        """
        for f in state.get("guardrail_flags", []):
            if f.get("stage") == "output" and f.get("blocked"):
                return "persist"
        if not _pcfg(state, "reflection_enabled", DEFAULT_REFLECTION_ENABLED):
            return "persist"
        skip_reflect = _pcfg(state, "skip_reflect_intents", DEFAULT_SKIP_REFLECT_INTENTS)
        if state.get("intent") in skip_reflect:
            return "persist"
        return "reflect"

    async def graph_retrieve_node(state: GraphState) -> dict:
        """Retrieve additional context via knowledge graph (GraphRAG); empty on any failure."""
        _kg = state.get("kg_service")
        _sf = state.get("session_factory")
        if _kg is None or _sf is None:
            return {"graph_context": []}

        async with state["step_tracker"].step("graph_retrieve"):
            return await _graph_retrieve(
                state,
                kg_service=_kg,
                session_factory=_sf,
            )

    def _retrieve_route(state: GraphState) -> str:
        """Route after retrieve: skip pipeline when 0 chunks, else check GraphRAG."""
        # Stream D (RAGO Pareto): early exit when retrieval returned 0 chunks.
        # Skipping rerank→mmr→grade→rewrite_retry saves 3-4 API calls per turn.
        chunks = state.get("retrieved_chunks") or []
        if not chunks:
            return "generate"
        # Stats/aggregation route: chunks are authoritative SQL results
        # (price-range / superlative) and graded_chunks is already seeded.
        # Skip rerank→mmr→grade — the fuzzy reranker/grader otherwise rescore
        # the synthetic price list low and drop it, making the bot refuse a
        # numeric question the SQL already answered. grounding_check at
        # guard_output still enforces HALLU=0.
        if str(state.get("retrieve_mode") or "").startswith("stats"):
            return "generate"
        graph_mode = _pcfg(state, "graph_rag_mode", "disabled")
        if graph_mode == "disabled":
            return "rerank"
        if graph_mode == "adaptive":
            intent = state.get("intent", DEFAULT_INTENT_FALLBACK)
            if intent not in INTENT_SYNTHESIS:
                return "rerank"
        return "graph_retrieve"

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

    async def query_complexity_node(state: GraphState) -> dict:
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

    async def adaptive_decompose(state: GraphState) -> dict:
        """Adaptive Router L3 — LLM-based query decomposer.

        Calls the domain-neutral decomposer with the bot-resolved LLM
        spec. On any failure (LLM down, JSON parse error, single-item
        return) the original query passes through unchanged so the
        retrieve path stays functional. Multi-item results seed
        ``sub_queries``; the S2 bypass in multi_query_fanout / retrieve
        consumes that contract.
        """
        # Wave M3.7-P2 — name step ctx so adaptive_decompose LLM cost
        # (typically 1.4s p50, 7% of turns) attributes to its row.
        # Decompose may call llm.complete once per L3 question split;
        # we accumulate per-call into _dc_agg and record at end-of-step.
        async with state["step_tracker"].step("adaptive_decompose") as dc_ctx:
            query_text = state.get("rewritten_query") or state.get("query") or ""
            if not query_text:
                return {}
            if model_resolver is None or llm is None:
                return {}

            _dc_agg: dict[str, Any] = {
                "model": "", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
            }

            async def _llm_invoker(
                *, system: str, user: str, model: str, max_tokens: int,
            ) -> str:
                # Bot owner picks the binding by purpose ("decompose"); the
                # decomposer model knob ("decomposer.model") tunes which
                # underlying provider model gets called via the binding.
                try:
                    cfg = await model_resolver.resolve_runtime(
                        record_tenant_id=state.get("record_tenant_id"),
                        record_bot_id=state.get("record_bot_id"),
                        purpose="decompose",
                    )
                except InvariantViolation as exc:
                    logger.warning(
                        "model_resolver_no_binding",
                        purpose="decompose",
                        record_bot_id=str(state.get("record_bot_id")),
                        node="adaptive_decompose",
                        error=str(exc)[:200],
                    )
                    # decompose_query() catches and falls back to [query].
                    raise
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                # ``model_override`` was previously forwarded here but the
                # underlying litellm call exposes it directly to providers
                # (OpenAI), which reject any unrecognised kwarg with a 400.
                # The runtime cfg already carries the correct model from
                # ``resolve_runtime`` (or its llm_primary fallback). The
                # ``decomposer.model`` config knob is consumed inside the
                # decompose_query module (selects which binding purpose to
                # request) and does NOT need to be re-passed to the LLM
                # call. See plans/260515-multi-query-audit-fix/issues/
                # issue-9-decompose-model-override-rejected.md.
                resp = await llm.complete(
                    cfg,
                    messages=messages,
                    purpose="decompose",
                    max_tokens=max_tokens,
                )
                # Wave M3.7-P2 — accumulate per-call cost.
                if isinstance(resp, dict):
                    _dc_agg["model"] = resp.get("model_name") or _dc_agg["model"]
                    _dc_agg["prompt_tokens"] += int(resp.get("prompt_tokens", 0) or 0)
                    _dc_agg["completion_tokens"] += int(resp.get("completion_tokens", 0) or 0)
                    _dc_agg["cost_usd"] += float(resp.get("cost_usd", 0.0) or 0.0)
                else:
                    _dc_agg["model"] = getattr(resp, "model_name", "") or _dc_agg["model"]
                    _dc_agg["prompt_tokens"] += int(getattr(resp, "prompt_tokens", 0) or 0)
                    _dc_agg["completion_tokens"] += int(getattr(resp, "completion_tokens", 0) or 0)
                    _dc_agg["cost_usd"] += float(getattr(resp, "cost_usd", 0.0) or 0.0)
                # ``LLMResponse`` and the legacy dict envelope both expose
                # the text under different attrs; coalesce so the
                # decomposer module stays envelope-agnostic.
                if isinstance(resp, dict):
                    return str(resp.get("text") or resp.get("content") or "")
                return str(
                    getattr(resp, "content", None)
                    or getattr(resp, "text", None)
                    or ""
                )

            sub_queries = await _decompose_query(
                query_text, llm_invoker=_llm_invoker,
            )
            # Wave M3.7-P2 — record aggregated decompose cost.
            if _dc_agg["prompt_tokens"] > 0:
                dc_ctx.record_llm(
                    model_used=str(_dc_agg["model"] or "") or None,
                    prompt_tokens=_dc_agg["prompt_tokens"],
                    completion_tokens=_dc_agg["completion_tokens"],
                    cost_usd=_dc_agg["cost_usd"],
                )
            cleaned = [s for s in sub_queries if isinstance(s, str) and s.strip()]
            if len(cleaned) >= 2:
                logger.info(
                    "adaptive_router_decomposed",
                    original=query_text[:80],
                    sub_count=len(cleaned),
                    source="adaptive_router_l3",
                )
                return {"sub_queries": cleaned, "original_query": query_text}
            return {}

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
    def _reflect_route(state: GraphState) -> str:
        total_iters = state.get("_total_graph_iterations", 0)
        max_iters = int(_pcfg(state, "max_total_graph_iterations", DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS))
        if total_iters >= max_iters:
            logger.warning("graph_iteration_cap_reached", iterations=total_iters)
            return "persist"
        if not state.get("answer"):
            return "generate"
        return "persist"

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
