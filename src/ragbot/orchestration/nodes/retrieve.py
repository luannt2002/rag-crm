"""Retrieve node (lifted from ``build_graph``) — the hybrid-search core.

Module-level node function wired into the LangGraph StateGraph via
``functools.partial`` in ``query_graph.build_graph``. Closure-captured DI
locals become explicit keyword params with the SAME names — pure relocation,
byte-identical body (no retrieval logic / embedding call / RRF fusion /
metadata filter / multi-query fan-out / stats-route / state key / ordering /
log-event change). The four inner closures (``_race_vector``,
``_embed_batch_queries``, ``_run_hybrid_for_query``, ``_mq_llm_complete``)
stay NESTED inside this node exactly as before — they capture node-local
state plus the DI kwargs.

Shared helper closures (``_audit``, ``_resolve_corpus_version``,
``_embed_query``, ``_prewarm_embedding_cache``, ``_do_stats_lookup``) and the
query_graph-local module helpers (``_pcfg``, ``_required_channel_type``,
``expand_parent_chunks``, ``retry_hybrid_with_original``,
``_parse_doc_type_vocabulary``) are threaded in as kwargs (importing the
query_graph-local helpers here would create a circular import). Domain-neutral
module-level collaborators (HybridQuery port type, range-query parser,
speculative-keep helper, embedding-spec coercer, constants) are imported
directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json as _json_mod
import re
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.ports.vector_store_port import HybridQuery
from ragbot.application.services.adaptive_rerank_weight import (
    adaptive_weight_enabled as _adaptive_weight_enabled,
    resolve_intent_weights as _resolve_intent_weights,
)
from ragbot.application.services.model_resolver import (
    resolve_purpose_for_intent as _resolve_purpose_for_intent,
    to_embedding_spec as _to_embedding_spec,
)
from ragbot.application.services.multi_query_expansion import (
    expand_query as mq_expand_query,
    expand_query_with_entities as mq_expand_query_with_entities,
    rrf_merge_chunks as mq_rrf_merge_chunks,
)
from ragbot.application.services.query_intent_extractor import (
    extract_intent as _extract_query_intent,
)
from ragbot.application.services.superlative_context_enricher import (
    get_enricher_for_language as _get_superlative_enricher,
)
from ragbot.application.services.vocabulary_expander import (
    get_default_expander as _get_vocab_expander,
)
from ragbot.orchestration.nodes.query_complexity import has_aggregation_keyword
from ragbot.orchestration.nodes.speculative_retrieve import (
    decide_keep_speculative as _decide_keep_speculative,
)
from ragbot.orchestration.retrieval_filter import _autocut
from ragbot.orchestration.state import GraphState
from ragbot.shared.chunking import (
    build_vn_structural_like_clauses,
    detect_vn_structural_anchor,
)
from ragbot.shared.embedding_cache import set_cached_embedding
from ragbot.shared.errors import InvariantViolation
from ragbot.shared.text_normalization import normalize_vn
from ragbot.shared.query_range_parser import (
    is_price_ask_query,
    parse_code_query as _parse_code_query,
    parse_list_query as _parse_list_query,
    parse_price_of_entity_query as _parse_price_of_entity_query,
    parse_range_query as _parse_range_query,
)
from ragbot.shared.i18n import get_routing_signals as _get_routing_signals
from ragbot.shared.vi_tokenizer import expand_abbreviations, restore_diacritics
from ragbot.shared.constants import (
    DEFAULT_BM25_NORMALIZATION_FLAGS,
    DEFAULT_CR_ENHANCED_ENABLED,
    DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY,
    DEFAULT_METADATA_LAYER3_LLM_ENABLED,
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_FALLBACK_VERSION,
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_ENTITY_GROUNDING_ENABLED,
    DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    DEFAULT_GENERIC_VOCAB_ENABLED,
    DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH,
    DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY,
    DEFAULT_LANGUAGE,
    DEFAULT_LEXICAL_RRF_K,
    DEFAULT_LEXICAL_TOP_K,
    DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED,
    DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL,
    DEFAULT_METADATA_FALLBACK_RELAX_ENABLED,
    DEFAULT_MULTI_QUERY_ENABLED,
    DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_MIN_TOKENS,
    DEFAULT_MULTI_QUERY_MODEL,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_PIPELINE_AUDIT_RETRIEVAL_PREVIEW,
    DEFAULT_PIPELINE_MULTI_QUERY_EMBED_BATCH_ENABLED,
    DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD,
    DEFAULT_RETRIEVAL_MULTISTAGE_ENABLED,
    DEFAULT_RETRIEVAL_STAGES,
    DEFAULT_RETRIEVE_FALLBACK_ENABLED,
    DEFAULT_RETRIEVE_FALLBACK_TOP_K,
    DEFAULT_RETRIEVE_TOP_K_BY_INTENT,
    DEFAULT_RRF_K,
    DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD,
    DEFAULT_STATS_CODE_LOOKUP_ENABLED,
    DEFAULT_STATS_INDEX_LIMIT,
    DEFAULT_DECOMPOSE_STATS_MAX_SUBS,
    DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED,
    DEFAULT_STATS_INDEX_RACE_ENABLED,
    DEFAULT_STATS_SUPERLATIVE_ENABLED,
    DEFAULT_STATS_RACE_TIMEOUT_S,
    DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
    DEFAULT_TOP_K,
    INTENT_AGGREGATION,
    INTENT_CHITCHAT,
    LEGACY_CORPUS_VERSION_TAG,
    RANGE_QUERY_MIN_CONFIDENCE,
)
from ragbot.shared.errors import EmbeddingError, RetrievalError

logger = structlog.get_logger(__name__)

# Layer 3 LLM metadata extractor (Plan 260604-metadata-aware-v4).
# Soft import — Layer 3 disabled if litellm missing at startup. Mirrors the
# query_graph soft-import so the byte-identical body's ``_L3Extractor`` /
# ``_litellm_module`` references resolve identically under both branches.
try:
    import litellm as _litellm_module
    from ragbot.infrastructure.metadata_filter.generic_llm_extractor import (
        GenericLLMMetadataExtractor as _L3Extractor,
    )
except ImportError:
    _litellm_module = None  # type: ignore[assignment]
    _L3Extractor = None  # type: ignore[assignment,misc]


async def _stats_chunks_for_sub_queries(
    *,
    state: dict,
    sub_queries: list,
    parse_fn,
    lookup_fn,
    min_confidence: float,
    stats_limit: int,
    expect_price: bool,
    max_subs: int,
) -> list[dict]:
    """Per-sub-query stats lookups for a DECOMPOSED question (002 cluster C).

    The old guard disabled the stats route entirely under decompose (symptom
    fix) — both legs of a comparison then fell to fuzzy vector retrieval and
    one leg routinely missed its priced row. This runs the authoritative
    point-lookup for EACH confident sub-query and returns the synthetic price
    chunks to JOIN the fan-out result set (never short-circuits it).

    Failure-isolated per sub (one bad lookup never kills the others);
    dedup by chunk_id; capped at *max_subs* to bound DB round-trips.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for q in [x for x in (sub_queries or []) if isinstance(x, str) and x.strip()][:max_subs]:
        try:
            rf = parse_fn(q)
            if rf is None or float(getattr(rf, "confidence", 0.0)) < min_confidence:
                continue
            payload = await lookup_fn(
                state, range_filter=rf, stats_limit=stats_limit,
                expect_price=expect_price,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            continue  # isolated: a failed leg must not kill the fan-out merge
        for ch in (payload or {}).get("linked_chunks") or []:
            cid = str(ch.get("chunk_id") or "")
            if cid and cid in seen:
                continue
            seen.add(cid)
            out.append(ch)
    return out


def _speculative_keep_allowed(*, sub_queries: list | None) -> bool:
    """Composition-aware speculative gate (truth-audit 002 cluster C).

    The speculative race pre-computes ONE hybrid_search on the RAW query. When
    the understand step decomposed the question into >=2 sub-queries, that
    single result set cannot serve the composition — keeping it would return
    from the node BEFORE the fan-out, so the sub-queries would never be
    retrieved (measured: comparison questions missing the second entity's
    row). Pure predicate, trivially testable.
    """
    subs = [q for q in (sub_queries or []) if isinstance(q, str) and q.strip()]
    return len(subs) < 2


async def retrieve(
    state: GraphState,
    *,
    vector_store: Any = None,
    lexical_retrieval: Any = None,
    embedder: Any = None,
    llm: Any = None,
    model_resolver: Any = None,
    redis_client: Any = None,
    entity_extractor: Any = None,
    metadata_filter_strategy: Any = None,
    language_pack_service: Any = None,
    stats_index_repo: Any = None,
    doc_repo: Any = None,
    _audit: Any,
    _resolve_corpus_version: Any,
    _embed_query: Any,
    _prewarm_embedding_cache: Any,
    _do_stats_lookup: Any,
    _pcfg: Any,
    _required_channel_type: Any,
    _is_null_lexical: Any,
    expand_parent_chunks: Any,
    retry_hybrid_with_original: Any,
    _parse_doc_type_vocabulary: Any,
) -> dict:
    async with state["step_tracker"].step("retrieve") as step_ctx:
        existing = state.get("retrieved_chunks") or []

        # ── Stats-index routing (B3 Self-Query Retrieval) ─────────────
        # When the intent is aggregation or comparison AND a price-range
        # filter is parsed from the query, route to the stats-index SQL
        # path instead of vector retrieve. The linked document chunks are
        # fetched and returned as evidence so the generate node has
        # grounded context — no app-side injection (CLAUDE.md QG #10).
        #
        # Race mode (``stats_index_race_enabled=true`` per-bot): stats and
        # vector retrieve run concurrently; the first non-empty winner
        # short-circuits. This improves recall when the stats index is
        # sparsely populated — vector retrieve can win and return chunks
        # the SQL path would have missed entirely.  Race is opt-in so
        # existing deployments keep sequential behaviour by default.
        _intent = str(state.get("intent") or "")
        # Stats route trigger: parser MUST extract a clear price range with
        # confidence >= threshold. Intent tag is an additional hint but not
        # required — many range queries ("dưới 1 triệu có dịch vụ gì",
        # "dịch vụ nào dưới 800 nghìn") miss the aggregation pattern in the
        # heuristic classifier yet have a clean numeric filter. Routing on
        # the filter (not the intent label) keeps the path domain-neutral
        # and avoids dragging intent-classifier maintenance into every
        # currency / language extension.
        if stats_index_repo is not None:
            # Parse the price bound from the ORIGINAL user text, not the
            # condensed query: condense_question rewrites state["query"] in
            # multi-turn and can drop the literal "dưới 500k", making the
            # stats route flaky (refuse run-to-run). original_query preserves
            # the raw text; fall back to query when condense did not run.
            _raw_query = state.get("original_query") or state.get("query") or ""
            # Per-locale routing signals (Track B): resolve the bot's language pack
            # signal lists so a non-vi bot routes on ITS signals instead of the vi
            # default. A vi bot resolves the vi seed → byte-identical to before.
            _routing_signals = _get_routing_signals(
                str(state.get("language") or DEFAULT_LANGUAGE)
            )
            _range_filter = _parse_range_query(_raw_query, signals=_routing_signals)
            # Code/spec lookup — tried BEFORE the fuzzy keyword list. A query
            # carrying a product/spec CODE ("lốp 195/65R15 còn hàng?",
            # "giá lốp 275/55R20 bao nhiêu") is a single exact-record lookup. The
            # keyword-list parser would otherwise capture a POLLUTED phrase
            # ("giá lốp 275/55R20") when the price-factoid words ("giá … bao
            # nhiêu") are split by the code — that phrase ILIKE-matches no row →
            # 0 stats → wrong vector fallback ("chưa tìm thấy") even though the
            # code exists. A code token is the most specific signal, so it wins
            # over the keyword list. Per-bot opt-out. Keyed on the code-token
            # SHAPE — domain-neutral, no bot/brand literal.
            if _range_filter is None and bool(_pcfg(
                state, "stats_code_lookup_enabled",
                DEFAULT_STATS_CODE_LOOKUP_ENABLED,
            )):
                _range_filter = _parse_code_query(_raw_query)
            # BUG-1 CONFLATE fix: a "<entity> giá bao nhiêu" price-of-entity
            # factoid must route to the structured name lookup (1 entity = 1
            # labelled price) so the LLM cannot attribute a co-occurring
            # entity's price from a multi-entity vector chunk. Sits BEFORE the
            # list route so "X giá bao nhiêu" is not treated as "list all X".
            # Keyed on the price-ask SHAPE — domain-neutral.
            if _range_filter is None and bool(_pcfg(
                state, "stats_price_of_entity_enabled",
                DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED,
            )):
                _range_filter = _parse_price_of_entity_query(
                    _raw_query, signals=_routing_signals
                )
            # Keyword/category list route: "liệt kê dịch vụ X" / "tư vấn về X" /
            # "có bao nhiêu X" need EVERY matching record (vector/BM25 only
            # surface top-k → incomplete list/count). Only when no price filter
            # AND no spec code applies, fall back to a name/category keyword
            # lookup.
            if _range_filter is None:
                _range_filter = _parse_list_query(_raw_query, signals=_routing_signals)
            # Superlative kill-switch: a "max"/"min" filter carries no numeric
            # bound and routes to ORDER BY price. Per-bot opt-out so the route
            # can be disabled without touching the range path.
            if (
                _range_filter is not None
                and getattr(_range_filter, "operation", "") in ("max", "min")
                and not bool(_pcfg(
                    state, "stats_superlative_enabled",
                    DEFAULT_STATS_SUPERLATIVE_ENABLED,
                ))
            ):
                _range_filter = None
            # Structural-reference guard: a query carrying an article/clause
            # anchor ("Điều 34", "Khoản 3") is a STRUCTURAL lookup, not a
            # stats/price-range query. The stats_index optimization links
            # entities by numeric range and returns early, bypassing the
            # article_aware metadata filter that fetches the exact article
            # chunk — the tt09 "Điều X quy định gì" miss. Skip stats when a
            # structural ref is present so hybrid + metadata_filter runs.
            # Domain-neutral: keys on the filter output, not bot/intent.
            _struct_ref: dict = {}
            if metadata_filter_strategy is not None and hasattr(
                metadata_filter_strategy, "extract",
            ):
                try:
                    _struct_ref = metadata_filter_strategy.extract(_raw_query) or {}
                except (re.error, ValueError, TypeError, AttributeError):
                    _struct_ref = {}
            # Always-on fallback: the strategy is None on the default path,
            # which would leave the guard a no-op. Detect a structural anchor
            # (Điều/Khoản/Article + number) with a domain-neutral regex so a
            # structural lookup never wrongly routes to stats_index.
            if not _struct_ref:
                _struct_pat = str(_pcfg(
                    state, "structural_ref_fallback_pattern",
                    DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN,
                ) or DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN)
                try:
                    if re.search(_struct_pat, _raw_query):
                        _struct_ref = {"_structural_anchor": True}
                except re.error:
                    pass
            # Multi-spec decompose guard: a comparison ("so sánh A và B") is
            # decomposed upstream into ≥2 sub_queries. The stats point-lookup
            # parses only the FIRST spec code (``_parse_code_query`` uses
            # re.search → first match), returns one score-1.0 synthetic chunk,
            # and short-circuits the whole retrieve — so the 2nd spec is never
            # fetched and the comparison answers "no info for B". When decompose
            # is active, skip the single-entity stats route and let the
            # multi-query fan-out retrieve every sub_query. Domain-neutral —
            # keys on sub_query count, not intent/bot.
            _decompose_active = len([
                s for s in (state.get("sub_queries") or [])
                if isinstance(s, str) and s.strip()
            ]) >= 2
            if _range_filter is not None and not _struct_ref and not _decompose_active and _range_filter.confidence >= float(
                _pcfg(state, "range_query_min_confidence", RANGE_QUERY_MIN_CONFIDENCE)
            ):
                _stats_limit = int(
                    _pcfg(state, "stats_index_limit", DEFAULT_STATS_INDEX_LIMIT)
                )
                # Anti-fabricate gate input: does the user ask for a price? A
                # price-ask that resolves only to price-LESS rows must fall
                # through to hybrid rather than answer authoritatively from a
                # row with no price (see _do_stats_lookup B-ROLEBLIND). Shape-
                # only via the locale price-ask signal — no vocab, no per-bot.
                _expect_price = is_price_ask_query(
                    _raw_query, signals=_routing_signals
                )
                _race_enabled = bool(
                    _pcfg(
                        state,
                        "stats_index_race_enabled",
                        DEFAULT_STATS_INDEX_RACE_ENABLED,
                    )
                )

                if _race_enabled and vector_store is not None:
                    # ── Race path ─────────────────────────────────────
                    # Fire both paths concurrently. The outer retrieve
                    # node cannot call _run_hybrid_for_query yet (it is
                    # defined below this closure), so we issue the
                    # hybrid_search call directly here to keep the race
                    # self-contained and dependency-free.
                    _race_timeout = float(
                        _pcfg(
                            state,
                            "stats_race_timeout_s",
                            DEFAULT_STATS_RACE_TIMEOUT_S,
                        )
                    )

                    async def _race_vector() -> list[dict] | None:
                        """Single-shot vector retrieve for the race arm.

                        Intentionally minimal — no multi-query, no RRF.
                        Returns the raw chunk list or ``None`` on failure /
                        embedding miss so the race resolver treats it as
                        "not ready" and waits for the other arm.
                        """
                        try:
                            _rv_q = (
                                state.get("rewritten_query") or state.get("query") or ""
                            )
                            _rv_top_k = int(
                                _pcfg(state, "top_k", DEFAULT_TOP_K)
                            )
                            if (
                                hasattr(vector_store, "hybrid_search")
                                and "query_text" not in inspect.signature(
                                    vector_store.hybrid_search
                                ).parameters
                            ):
                                _rv_emb = await _embed_query(_rv_q, state)
                                if not _rv_emb:
                                    return None
                                _rv_hq = HybridQuery(
                                    dense_vector=_rv_emb, query_text=_rv_q,
                                )
                                _rv_kwargs: dict[str, Any] = {
                                    "record_bot_id": state["record_bot_id"],
                                    "channel_type": _required_channel_type(state),
                                    "corpus_version": await _resolve_corpus_version(state),
                                    "embedding_model_version": DEFAULT_EMBEDDING_FALLBACK_VERSION,
                                    "limit": _rv_top_k,
                                }
                                _rv_sig = inspect.signature(
                                    vector_store.hybrid_search
                                )
                                if (
                                    "record_tenant_id" in _rv_sig.parameters
                                    and state.get("record_tenant_id") is not None
                                ):
                                    _rv_kwargs["record_tenant_id"] = state[
                                        "record_tenant_id"
                                    ]
                                _rv_raw = await vector_store.hybrid_search(
                                    _rv_hq, **_rv_kwargs
                                )
                                return [
                                    {
                                        "chunk_id": str(c.chunk_id),
                                        "document_id": str(c.document_id),
                                        "content": c.text,
                                        "text": c.text,
                                        "score": c.score,
                                        "document_name": getattr(
                                            c, "document_name", ""
                                        )
                                        or (
                                            c.payload.get("document_title", "")
                                            if hasattr(c, "payload")
                                            else ""
                                        ),
                                        "chunk_index": getattr(
                                            c, "chunk_index", ""
                                        ),
                                        **(
                                            c.payload
                                            if hasattr(c, "payload")
                                            else {}
                                        ),
                                    }
                                    for c in _rv_raw
                                ]
                            if hasattr(vector_store, "hybrid_search") and hasattr(
                                vector_store, "search"
                            ):
                                _rv_emb = await _embed_query(_rv_q, state)
                                if not _rv_emb:
                                    return None
                                _hs_kw: dict[str, Any] = {
                                    "query_text": _rv_q,
                                    "query_embedding": _rv_emb,
                                    "record_bot_id": state["record_bot_id"],
                                    "top_k": _rv_top_k,
                                }
                                _hs_s = inspect.signature(vector_store.hybrid_search)
                                if "channel_type" in _hs_s.parameters:
                                    _hs_kw["channel_type"] = _required_channel_type(state)
                                if (
                                    "record_tenant_id" in _hs_s.parameters
                                    and state.get("record_tenant_id") is not None
                                ):
                                    _hs_kw["record_tenant_id"] = state[
                                        "record_tenant_id"
                                    ]
                                return list(
                                    await vector_store.hybrid_search(**_hs_kw) or []
                                )
                            return None
                        except (RetrievalError, EmbeddingError, asyncio.TimeoutError,
                                OSError, RuntimeError, ValueError, KeyError,
                                AttributeError):
                            logger.warning(
                                "stats_race_vector_arm_failed", exc_info=True
                            )
                            return None

                    _stats_task: asyncio.Task[dict | None] = asyncio.create_task(
                        _do_stats_lookup(
                            state,
                            range_filter=_range_filter,
                            stats_limit=_stats_limit,
                            expect_price=_expect_price,
                        )
                    )
                    _vector_task: asyncio.Task[list[dict] | None] = (
                        asyncio.create_task(_race_vector())
                    )

                    _done: set[asyncio.Task[Any]]
                    _pending: set[asyncio.Task[Any]]
                    try:
                        _done, _pending = await asyncio.wait(
                            {_stats_task, _vector_task},
                            timeout=_race_timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except (asyncio.TimeoutError, RuntimeError):
                        _done, _pending = set(), {_stats_task, _vector_task}

                    # Determine winner — first completed task with a
                    # non-empty result.  We check all done tasks (there
                    # may be two if both completed within the event-loop
                    # tick).  Stats result is preferred when both are
                    # ready simultaneously (SQL result is more precise).
                    _race_winner: str | None = None
                    _race_stats_payload: dict | None = None
                    _race_vector_chunks: list[dict] | None = None

                    for _t in _done:
                        if _t is _stats_task and not _t.cancelled():
                            try:
                                _sp = _t.result()
                                if _sp:
                                    _race_stats_payload = _sp
                                    _race_winner = "stats_race_winner"
                                    break
                            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — task result unwrap
                                pass

                    if _race_winner is None:
                        for _t in _done:
                            if _t is _vector_task and not _t.cancelled():
                                try:
                                    _vp = _t.result()
                                    if _vp:
                                        _race_vector_chunks = _vp
                                        _race_winner = "vector_race_winner"
                                except (asyncio.CancelledError, Exception):  # noqa: BLE001 — task result unwrap
                                    pass

                    # Cancel the losing / timeout tasks.
                    for _pt in _pending:
                        _pt.cancel()
                    # Also cancel the loser if both completed.
                    if _race_winner == "stats_race_winner" and not _vector_task.done():
                        _vector_task.cancel()
                    if _race_winner == "vector_race_winner" and not _stats_task.done():
                        _stats_task.cancel()

                    if _race_winner == "stats_race_winner" and _race_stats_payload:
                        _sp = _race_stats_payload
                        _rf = _sp["range_filter"]
                        step_ctx.set_metadata(
                            source="stats_race_winner",
                            entity_count=len(_sp["entities"]),
                            range_min=_rf.price_min,
                            range_max=_rf.price_max,
                            operation=_rf.operation,
                        )
                        logger.info(
                            "stats_index_race_winner",
                            entity_count=len(_sp["entities"]),
                            linked_chunks=len(_sp["linked_chunks"]),
                            intent=_intent,
                        )
                        return {
                            "retrieved_chunks": _sp["linked_chunks"],
                            # Authoritative SQL result → also seed graded_chunks
                            # so the stats→generate route skips rerank/mmr/grade
                            # (which otherwise rescore the price list low + drop it).
                            "graded_chunks": _sp["linked_chunks"],
                            "retrieval_adequate": True,
                            "stats_entities": _sp["entities"],
                            "retrieve_mode": "stats_race_winner",
                        }

                    if _race_winner == "vector_race_winner" and _race_vector_chunks:
                        # Cancel the still-running stats task if not done.
                        if not _stats_task.done():
                            _stats_task.cancel()
                        step_ctx.set_metadata(
                            source="vector_race_winner",
                            candidates=len(_race_vector_chunks),
                            top_k=_pcfg(state, "top_k", DEFAULT_TOP_K),
                        )
                        logger.info(
                            "vector_race_winner",
                            candidates=len(_race_vector_chunks),
                            intent=_intent,
                        )
                        return {
                            "retrieved_chunks": _race_vector_chunks,
                            "retrieve_mode": "vector_race_winner",
                        }

                    # Both tasks empty / timeout → log fallback and
                    # continue to the full sequential vector path below.
                    logger.info(
                        "stats_race_fallback",
                        reason="both_empty_or_timeout",
                        intent=_intent,
                    )
                    step_ctx.set_metadata(source="fallback")

                else:
                    # ── Sequential path (default, race disabled) ───────
                    _seq_payload = await _do_stats_lookup(
                        state,
                        range_filter=_range_filter,
                        stats_limit=_stats_limit,
                        expect_price=_expect_price,
                    )
                    # 2026-05-28 — fall-through when stats_index returns 0
                    # linked_chunks. Production trace 7fed03f9 showed
                    # comparison query "So sánh Điều 40 vs 39" routed
                    # to stats_index with entity_count=1 linked_chunks=0
                    # → bot got empty retrieve → Faith failure mode.
                    # Stats-index is an OPTIMIZATION path — when it
                    # yields nothing, hybrid_search must run as fallback.
                    if (
                        _seq_payload
                        and _seq_payload.get("linked_chunks")
                    ):
                        _rf_s = _seq_payload["range_filter"]
                        step_ctx.set_metadata(
                            source="stats_index",
                            entity_count=len(_seq_payload["entities"]),
                            range_min=_rf_s.price_min,
                            range_max=_rf_s.price_max,
                            operation=_rf_s.operation,
                        )
                        logger.info(
                            "stats_index_route",
                            entity_count=len(_seq_payload["entities"]),
                            linked_chunks=len(_seq_payload["linked_chunks"]),
                            operation=_rf_s.operation,
                            intent=_intent,
                        )
                        return {
                            "retrieved_chunks": _seq_payload["linked_chunks"],
                            # Authoritative SQL result → also seed graded_chunks
                            # so the stats→generate route skips rerank/mmr/grade
                            # (which otherwise rescore the price list low + drop it).
                            "graded_chunks": _seq_payload["linked_chunks"],
                            "retrieval_adequate": True,
                            "stats_entities": _seq_payload["entities"],
                            "retrieve_mode": "stats_index",
                        }
                    # Empty stats_index → log + fall-through to hybrid.
                    if _seq_payload:
                        logger.info(
                            "stats_index_empty_fallback_hybrid",
                            entity_count=len(_seq_payload.get("entities", [])),
                            intent=_intent,
                        )

        if vector_store is None:
            step_ctx.set_metadata(candidates=len(existing), top_k=_pcfg(state, "top_k", DEFAULT_TOP_K), source="pre_seeded")
            return {"retrieved_chunks": list(existing)}

        query_text = state.get("rewritten_query") or state["query"]

        # Speculative retrieve gate (Stream B1): when the orchestrator
        # raced embed+hybrid_search(raw_query) against understand+rewrite,
        # decide whether the rewritten query is close enough to reuse
        # the pre-computed chunks. Cosine_sim >= threshold ⇒ skip the
        # second retrieve and save the hybrid_search round-trip.
        spec_chunks = state.get("_speculative_chunks")
        spec_raw_embed = state.get("_speculative_raw_embed")
        if spec_chunks and spec_raw_embed:
            spec_threshold = float(
                _pcfg(state, "speculative_similarity_threshold",
                      DEFAULT_SPECULATIVE_SIMILARITY_THRESHOLD)
            )
            # ``_embed_query`` swallows its own exceptions and returns
            # ``[]`` on failure — decide_keep_speculative treats [] as
            # "refuse keep" so the normal retrieve path runs below.
            rewritten_embed = await _embed_query(query_text, state)
            keep = _decide_keep_speculative(
                spec_raw_embed, rewritten_embed, spec_threshold,
            )
            # 002-C: decomposed questions MUST reach the fan-out below — a
            # single raw-query result set cannot serve >=2 sub-queries.
            if keep and not _speculative_keep_allowed(
                sub_queries=state.get("sub_queries"),
            ):
                keep = False
                logger.info("speculative_skipped_for_decompose",
                            n_sub_queries=len(state.get("sub_queries") or []))
            if keep:
                step_ctx.set_metadata(
                    candidates=len(spec_chunks),
                    top_k=_pcfg(state, "top_k", DEFAULT_TOP_K),
                    source="speculative_hit",
                )
                logger.info(
                    "speculative_retrieve_hit",
                    threshold=spec_threshold,
                    candidates=len(spec_chunks),
                )
                return {
                    "retrieved_chunks": list(spec_chunks),
                    "_speculative_hit": True,
                    "retrieve_mode": "speculative",
                }
            logger.info(
                "speculative_retrieve_miss",
                threshold=spec_threshold,
            )
            # Fall through to normal retrieve below; ``_speculative_hit``
            # stays unset so downstream telemetry can distinguish miss.

        _vi_preprocessing = _pcfg(state, "vietnamese_preprocessing_enabled", True)
        custom_vocab = _pcfg(state, "bot_custom_vocabulary", {})

        if _vi_preprocessing:
            abbrev_override: dict[str, str] | None = None
            raw_abbrev = _pcfg(state, "vietnamese_abbreviations", "{}")
            if raw_abbrev and raw_abbrev != "{}":
                try:
                    parsed = _json_mod.loads(raw_abbrev) if isinstance(raw_abbrev, str) else raw_abbrev
                    if isinstance(parsed, dict):
                        abbrev_override = parsed
                except (ValueError, TypeError):
                    # Malformed JSON / non-string config value.
                    logger.debug("abbreviation_dict_parse_failed", raw=str(raw_abbrev)[:100])

            custom_abbrevs = custom_vocab.get("abbreviations", {}) if isinstance(custom_vocab, dict) else {}
            if custom_abbrevs:
                if abbrev_override is None:
                    abbrev_override = {}
                abbrev_override.update(custom_abbrevs)

            _bot_language = str(state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
            expanded_query = expand_abbreviations(
                query_text, abbrev_override, language=_bot_language,
            )
            if expanded_query != query_text:
                logger.debug("query_expanded", original=query_text[:80], expanded=expanded_query[:80])
                query_text = expanded_query

        chunks: list[dict] = []

        # Per-intent retrieve top_k — mirrors rerank_top_n_by_intent pattern.
        # Lightweight intents (greeting/chitchat) need 5; aggregation needs 40.
        # Resolved once here; used at every RRF-fuse + lexical-fuse slice below.
        _intent_for_topk = state.get("intent") or ""
        _topk_by_intent = _pcfg(state, "retrieve_top_k_by_intent", DEFAULT_RETRIEVE_TOP_K_BY_INTENT)
        # Keyword-promote: superlative / list / extrema queries ("đắt nhất",
        # "rẻ nhất", "liệt kê tất cả") need the WHOLE set in-window for a
        # correct max/min/list. The LLM intent classifier often labels these
        # "factoid" → small top_k → the answer row randomly falls outside the
        # window (variance). Promote to the aggregation top_k so completeness
        # is deterministic. Only promotes (never shrinks) — guarded below.
        if (
            isinstance(_topk_by_intent, dict)
            and INTENT_AGGREGATION in _topk_by_intent
            and has_aggregation_keyword(query_text, lang=state.get("language") or DEFAULT_LANGUAGE)
            and int(_topk_by_intent.get(_intent_for_topk, 0) or 0)
            < int(_topk_by_intent.get(INTENT_AGGREGATION, 0) or 0)
        ):
            _intent_for_topk = INTENT_AGGREGATION
        _intent_override_topk = False
        if isinstance(_topk_by_intent, dict) and _intent_for_topk in _topk_by_intent:
            try:
                _retrieve_top_k = int(_topk_by_intent[_intent_for_topk])
                _intent_override_topk = True
            except (TypeError, ValueError):
                _retrieve_top_k = int(_pcfg(state, "top_k", DEFAULT_TOP_K))
        else:
            _retrieve_top_k = int(_pcfg(state, "top_k", DEFAULT_TOP_K))
        step_ctx.set_metadata(
            retrieve_top_k=_retrieve_top_k,
            intent_override_topk=_intent_override_topk,
        )

        _vocab_enabled = bool(
            _pcfg(state, "generic_vocab_enabled", DEFAULT_GENERIC_VOCAB_ENABLED)
        )
        if _vocab_enabled:
            _vocab_max_matches = int(
                _pcfg(state, "generic_vocab_max_matches", DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY)
            )
            _vocab_max_exp = int(
                _pcfg(state, "generic_vocab_max_expansions", DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH)
            )
            _bot_custom_vocab: dict | None = (
                custom_vocab.get("synonyms") if isinstance(custom_vocab, dict) else None
            )
            try:
                _vocab_lang = str(state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
                _vocab_expander = _get_vocab_expander(_vocab_lang)
                # Pass limits as call args; never mutate the shared singleton (race-safe).
                state = _vocab_expander.enrich_state(
                    state,
                    query_text,
                    _bot_custom_vocab,
                    max_matches=_vocab_max_matches,
                    max_expansions=_vocab_max_exp,
                )
            except (KeyError, ValueError, TypeError, AttributeError,
                    RuntimeError):
                # Vocab expander failure must not break retrieval.
                logger.warning("generic_vocab_enrich_failed", exc_info=True)

        # Read-side filter only useful when ingest stored metadata; gate both flags.
        metadata_aware_enabled = bool(
            _pcfg(state, "metadata_aware_retrieval_enabled", DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED),
        )
        metadata_extraction_enabled = bool(
            _pcfg(state, "metadata_extraction_enabled", False),
        )
        metadata_filter: dict[str, Any] = {}
        if metadata_aware_enabled and metadata_extraction_enabled:
            try:
                intent_model = str(_pcfg(state, "intent_extractor_model", "") or "") or None
                intent_prompt = str(_pcfg(state, "intent_extractor_system_prompt", "") or "")
                intent_vocab_raw = _pcfg(state, "metadata_extraction_vocabulary", "")
                allowed_doc_types = _parse_doc_type_vocabulary(intent_vocab_raw)
                metadata_filter = await _extract_query_intent(
                    query_text,
                    model_id=intent_model,
                    system_prompt=intent_prompt,
                    allowed_doc_types=allowed_doc_types,
                )
                if metadata_filter:
                    logger.debug(
                        "metadata_filter_extracted",
                        keys=list(metadata_filter.keys()),
                        doc_type=metadata_filter.get("document_type"),
                    )
            except (InvariantViolation, asyncio.TimeoutError, OSError,
                    RuntimeError, ValueError, KeyError, AttributeError):
                # Intent-extract LLM call failure must not break the
                # retrieve node; degrade to empty filter.
                logger.warning("metadata_filter_extract_failed", exc_info=True)
                metadata_filter = {}
        elif metadata_aware_enabled and not metadata_extraction_enabled:
            logger.debug(
                "metadata_aware_skipped_write_off",
                reason="metadata_extraction_enabled=false; corpus not labelled",
            )

        # C2 — Article-aware metadata pre-filter (regex, no LLM). Layered
        # on top of the LLM-intent filter above: regex keys augment the
        # filter dict; existing LLM keys take precedence on collision so
        # operator-supplied vocab (``document_type``) is never overridden
        # by a structural-anchor regex. Independent of the LLM gate so
        # bots that don't run the LLM extractor still get the cheap
        # regex pre-filter when ingest-side structured-ref metadata is
        # present. The strategy itself is DI-injected as the
        # ``metadata_filter_strategy`` kwarg on ``build_graph``
        # (NullFilter when operator config selects ``"null"``); the
        # orchestrator only talks to the Port.
        if metadata_filter_strategy is not None and hasattr(
            metadata_filter_strategy, "extract",
        ):
            try:
                _regex_filter = metadata_filter_strategy.extract(query_text)
                if _regex_filter:
                    # Existing LLM-extracted keys win on collision so
                    # operator vocab (``document_type``) is preserved.
                    _accepted: list[str] = []
                    for _k, _v in _regex_filter.items():
                        if _k not in metadata_filter:
                            metadata_filter[_k] = _v
                            _accepted.append(_k)
                    if _accepted:
                        logger.debug(
                            "article_aware_filter_extracted",
                            provider=getattr(
                                metadata_filter_strategy,
                                "get_provider_name",
                                lambda: "unknown",
                            )(),
                            keys=sorted(_accepted),
                        )
            except (KeyError, ValueError, TypeError, AttributeError,
                    re.error, RuntimeError):
                # Filter-strategy regex / shape failure must not
                # break retrieval; downstream uses empty filter.
                logger.warning(
                    "article_aware_filter_failed", exc_info=True,
                )

        # Layer 3 LLM entity extraction (Plan 260604-metadata-aware-v4).
        # OFF by default (DEFAULT_METADATA_LAYER3_LLM_ENABLED): query-time
        # LLM metadata extraction is the wrong tier (LLM call + latency per
        # query, can over-restrict). Ingest-side Contextual Retrieval is the
        # correct, already-on mechanism. Operator opt-in via system_config
        # after a per-bot A/B. Fires only when Layer 1/2 returned empty.
        _layer3_enabled = bool(
            _pcfg(state, "metadata_layer3_llm_enabled",
                  DEFAULT_METADATA_LAYER3_LLM_ENABLED),
        )
        if _layer3_enabled and not metadata_filter:
            try:
                # Cache per-graph singleton via function-attribute to
                # avoid re-construct cost on every query.
                _l3_singleton = getattr(_extract_query_intent, "_l3_extractor", None)
                _l3_locale = str(
                    state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE
                )
                if _l3_singleton is None and language_pack_service is not None:
                    _model_raw = _pcfg(
                        state,
                        "metadata_extraction_model",
                        DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL,
                    )
                    _model_id = str(_model_raw).strip().strip('"')
                    # Resolve prompt từ language_packs theo locale của bot
                    _prompt = await language_pack_service.get(
                        _l3_locale, "metadata_extract_default",
                    )
                    if _prompt:
                        _l3_singleton = _L3Extractor(
                            litellm_module=_litellm_module,
                            model_id=_model_id,
                            prompt_template=_prompt,
                            cache=None,  # cache wire deferred to bootstrap pass
                        )
                        _extract_query_intent._l3_extractor = _l3_singleton  # type: ignore[attr-defined]
                if _l3_singleton is not None:
                    _l3_filter = await _l3_singleton.extract(query_text, locale=_l3_locale)
                    if _l3_filter:
                        for _k, _v in _l3_filter.items():
                            if _k not in metadata_filter:
                                metadata_filter[_k] = _v
                        logger.debug(
                            "layer3_llm_filter_extracted",
                            keys=list(_l3_filter.keys()),
                        )
            except Exception as exc:  # noqa: BLE001 — Layer 3 soft fail
                logger.warning(
                    "layer3_llm_filter_failed",
                    error_type=type(exc).__name__,
                    err=str(exc)[:120],
                )

        async def _embed_batch_queries(
            batch_queries: list[str],
            st: GraphState,
        ) -> list[list[float]]:
            """Embed N queries in a single batch HTTP call and seed Redis cache.

            When the embedder supports ``embed_batch`` + ``embed_one`` (full
            EmbeddingPort contract), issues one HTTP request for all queries.
            After embedding, seeds the Redis embedding cache so subsequent
            ``_embed_query`` calls for the same texts (e.g. rewrite-retry
            fallback) see a cache hit.

            Fallback: parallel ``asyncio.gather`` of individual ``_embed_query``
            calls when the embedder lacks ``embed_batch`` or the batch call
            fails; each ``_embed_query`` seeds the cache independently.

            Returns a list indexed 1-to-1 with *batch_queries*; failed
            individual embeds produce ``[]`` at that position so callers can
            skip the branch without crashing the fan-out.
            """
            if not batch_queries:
                return []
            if embedder is None:
                return [[] for _ in batch_queries]
            # Set embedding_column unconditionally so downstream readers
            # never see None (same guard as _prewarm_embedding_cache).
            st["embedding_column"] = DEFAULT_EMBEDDING_COLUMN
            emb_provider = str(_pcfg(st, "embedding_provider", DEFAULT_EMBEDDING_PROVIDER) or DEFAULT_EMBEDDING_PROVIDER)
            emb_model = str(_pcfg(st, "embedding_model", "") or "") or "unknown"
            emb_dim = int(_pcfg(st, "embedding_dimension", DEFAULT_EMBEDDING_DIM) or DEFAULT_EMBEDDING_DIM)
            # Prefer single batch HTTP round-trip when the port supports it.
            if hasattr(embedder, "embed_batch") and hasattr(embedder, "embed_one"):
                try:
                    spec = None
                    if model_resolver is not None:
                        cfg = await model_resolver.resolve_runtime(
                            record_tenant_id=st.get("record_tenant_id"),
                            record_bot_id=st.get("record_bot_id"),
                            purpose="embedding",
                        )
                        spec = getattr(cfg, "embedding_spec", None)
                        if spec is None and cfg is not None:
                            spec = _to_embedding_spec(cfg)
                    if spec is not None:
                        # NFC-normalize before the prefix (mirror _embed_query /
                        # _prewarm) so this batch path embeds and cache-keys the
                        # same composed form as the corpus — an NFD variant would
                        # otherwise seed a key the per-branch embed never hits.
                        query_prefix = str(_pcfg(st, "embedding_query_prefix", "") or "").strip('"')
                        prefixed = [
                            f"{query_prefix}{nq}" if query_prefix else nq
                            for nq in (normalize_vn(q) for q in batch_queries)
                        ]
                        spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})
                        sig = inspect.signature(embedder.embed_batch)
                        params = set(sig.parameters.keys())
                        kwargs: dict[str, Any] = {}
                        if "spec" in params:
                            kwargs["spec"] = spec
                        if "record_tenant_id" in params:
                            kwargs["record_tenant_id"] = st.get("record_tenant_id")
                        batch_results = await embedder.embed_batch(prefixed, **kwargs)
                        if batch_results and len(batch_results) == len(batch_queries):
                            # Seed Redis cache so rewrite-retry / speculative
                            # paths hit cache on the same text this turn.
                            for prefixed_q, emb in zip(prefixed, batch_results):
                                if emb:
                                    await set_cached_embedding(
                                        redis_client, prefixed_q, emb,
                                        provider=emb_provider,
                                        model=emb_model,
                                        dim=emb_dim or len(emb),
                                    )
                            return batch_results
                except (EmbeddingError, asyncio.TimeoutError, OSError,
                        RuntimeError, ValueError, AttributeError):
                    logger.warning("embed_batch_queries_failed", exc_info=True)
            # Fallback: parallel individual embed calls (backward compat for
            # embedders without embed_batch, or when batch call failed).
            # Each _embed_query seeds the Redis cache independently.
            results = await asyncio.gather(
                *[_embed_query(q, st) for q in batch_queries],
                return_exceptions=False,
            )
            return list(results)

        async def _run_hybrid_for_query(
            q_text: str,
            meta_filter: dict[str, Any] | None = None,
            *,
            top_k_override: int | None = None,
            precomputed_embedding: list[float] | None = None,
        ) -> list[dict] | None:
            _branch_top_k = int(top_k_override) if top_k_override else int(_pcfg(state, "top_k", DEFAULT_TOP_K))
            _is_port = (
                hasattr(vector_store, "hybrid_search")
                and "query_text" not in inspect.signature(vector_store.hybrid_search).parameters
            )
            if _is_port:
                q_emb = precomputed_embedding if precomputed_embedding else await _embed_query(q_text, state)
                if not q_emb:
                    return None
                hq = HybridQuery(dense_vector=q_emb, query_text=q_text)
                # Same per-turn corpus tag as cache_check — keeps any
                # future port-side filter (e.g. partitioned vector index)
                # in sync with the cache discriminator.
                _port_kwargs: dict[str, Any] = {
                    "record_bot_id": state["record_bot_id"],
                    "channel_type": _required_channel_type(state),
                    "corpus_version": await _resolve_corpus_version(state),
                    "embedding_model_version": DEFAULT_EMBEDDING_FALLBACK_VERSION,
                    "limit": _branch_top_k,
                }
                # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                _port_sig = inspect.signature(vector_store.hybrid_search)
                if (
                    "record_tenant_id" in _port_sig.parameters
                    and state.get("record_tenant_id") is not None
                ):
                    _port_kwargs["record_tenant_id"] = state["record_tenant_id"]
                candidates = await vector_store.hybrid_search(hq, **_port_kwargs)
                return [
                    {
                        "chunk_id": str(c.chunk_id),
                        "document_id": str(c.document_id),
                        "content": c.text,
                        "text": c.text,
                        "score": c.score,
                        "document_name": getattr(c, "document_name", "") or (c.payload.get("document_title", "") if hasattr(c, "payload") else ""),
                        "chunk_index": getattr(c, "chunk_index", ""),
                        **(c.payload if hasattr(c, "payload") else {}),
                    }
                    for c in candidates
                ]
            if hasattr(vector_store, "search"):
                q_emb = precomputed_embedding if precomputed_embedding else await _embed_query(q_text, state)
                if (
                    q_emb
                    and q_text
                    and hasattr(vector_store, "hybrid_search")
                    and "query_text" in inspect.signature(vector_store.hybrid_search).parameters
                ):
                    _hs_kwargs: dict[str, Any] = {
                        "query_text": q_text,
                        "query_embedding": q_emb,
                        "record_bot_id": state["record_bot_id"],
                        "top_k": _branch_top_k,
                    }
                    _hs_sig = inspect.signature(vector_store.hybrid_search)
                    _hs_params = set(_hs_sig.parameters.keys())
                    if "channel_type" in _hs_params:
                        _hs_kwargs["channel_type"] = _required_channel_type(state)
                    if "bm25_use_cover_density" in _hs_params:
                        _hs_kwargs["bm25_use_cover_density"] = bool(
                            _pcfg(state, "bm25_use_cover_density", True),
                        )
                    if "bm25_normalization_flags" in _hs_params:
                        flags = int(_pcfg(state, "bm25_normalization_flags", DEFAULT_BM25_NORMALIZATION_FLAGS))
                        if not (0 <= flags <= 63):
                            logger.warning("invalid_bm25_flags", value=flags)
                            flags = 5
                        _hs_kwargs["bm25_normalization_flags"] = flags
                    if "bm25_substring_fallback_enabled" in _hs_params:
                        _hs_kwargs["bm25_substring_fallback_enabled"] = bool(
                            _pcfg(state, "bm25_substring_fallback_enabled", False),
                        )
                    # Phase-C C5: per-intent RRF weight blend. Gated behind
                    # ``adaptive_rerank_weight_enabled`` so the rollout flips
                    # via system_config without redeploy. The hybrid_search
                    # signature must accept ``bm25_weight`` / ``vector_weight``
                    # — adapters without those params silently keep the
                    # flat default behaviour.
                    if (
                        _adaptive_weight_enabled(state.get("pipeline_config"))
                        and "bm25_weight" in _hs_params
                        and "vector_weight" in _hs_params
                    ):
                        _iw = _resolve_intent_weights(
                            state.get("intent"),
                            pipeline_config=state.get("pipeline_config"),
                        )
                        _hs_kwargs["bm25_weight"] = _iw.bm25
                        _hs_kwargs["vector_weight"] = _iw.vector
                    if meta_filter and "metadata_filter" in _hs_params:
                        _hs_kwargs["metadata_filter"] = meta_filter
                    if (
                        "embedding_column" in _hs_params
                        and state.get("embedding_column")
                    ):
                        _hs_kwargs["embedding_column"] = state["embedding_column"]
                    # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                    if (
                        "record_tenant_id" in _hs_params
                        and state.get("record_tenant_id") is not None
                    ):
                        _hs_kwargs["record_tenant_id"] = state["record_tenant_id"]
                    # 2026-05-27 Fix 3 — VN structural pre-filter. When the
                    # query targets a single (Chương|Mục|Phần|Điều) N
                    # anchor, narrow the dense branch to chunks under that
                    # path. Bypasses zembed-1's weak grasp of structural
                    # identifiers. Adapter graceful-degrades when the
                    # filter excludes everything.
                    if "structural_filter_patterns" in _hs_params:
                        _anchor = detect_vn_structural_anchor(q_text)
                        if _anchor is not None:
                            _hs_kwargs["structural_filter_patterns"] = (
                                build_vn_structural_like_clauses(_anchor)
                            )
                    raw = await vector_store.hybrid_search(**_hs_kwargs)
                    return list(raw or [])
                if q_emb:
                    sig = inspect.signature(vector_store.search)
                    params = set(sig.parameters.keys())
                    search_kwargs: dict[str, Any] = {"top_k": _branch_top_k}
                    search_kwargs["query_embedding"] = q_emb
                    if "record_bot_id" in params:
                        search_kwargs["record_bot_id"] = state["record_bot_id"]
                    if "channel_type" in params:
                        search_kwargs["channel_type"] = _required_channel_type(state)
                    if (
                        "embedding_column" in params
                        and state.get("embedding_column")
                    ):
                        search_kwargs["embedding_column"] = state["embedding_column"]
                    # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                    if (
                        "record_tenant_id" in params
                        and state.get("record_tenant_id") is not None
                    ):
                        search_kwargs["record_tenant_id"] = state["record_tenant_id"]
                    raw = await vector_store.search(**search_kwargs)
                    return list(raw or [])
                return None
            return []

        # Decompose sub-queries take precedence over multi-query expansion.
        sub_queries_state: list[str] = [
            s for s in (state.get("sub_queries") or []) if isinstance(s, str) and s.strip()
        ]
        decompose_active = len(sub_queries_state) >= 2
        queries: list[str] = list(sub_queries_state) if decompose_active else [query_text]

        # Multi-query: N paraphrases -> parallel hybrid_search -> RRF merge. Skipped when decompose fired.
        # Per-intent gate: lightweight intents skip fanout to save ~2.3s/turn.
        _retrieve_mq_intent = str(state.get("intent") or "")
        _retrieve_mq_enabled_map = _pcfg(state, "multi_query_enabled_by_intent", None)
        if isinstance(_retrieve_mq_enabled_map, dict) and _retrieve_mq_intent in _retrieve_mq_enabled_map:
            try:
                _retrieve_intent_mq_enabled = bool(_retrieve_mq_enabled_map[_retrieve_mq_intent])
            except (TypeError, ValueError):
                _retrieve_intent_mq_enabled = True
        else:
            _retrieve_intent_mq_enabled = DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT.get(
                _retrieve_mq_intent, True,
            )
        mq_enabled = bool(_pcfg(state, "multi_query_enabled", DEFAULT_MULTI_QUERY_ENABLED))
        mq_n_variants = int(_pcfg(state, "multi_query_n_variants", DEFAULT_MULTI_QUERY_N_VARIANTS))
        mq_max_variants = int(_pcfg(state, "multi_query_max_variants", DEFAULT_MULTI_QUERY_MAX_VARIANTS))
        mq_timeout_s = int(_pcfg(state, "multi_query_timeout_s", DEFAULT_MULTI_QUERY_TIMEOUT_S))
        # Parallel-path hand-off: rewrite_and_mq_parallel may have pre-computed
        # paraphrases upstream so retrieve skips the inline LLM call. Only
        # honoured when decompose did NOT fire (sub_queries take precedence).
        # Honour speculative variants too (audit 2026-06-13): the parallel
        # rewrite path stores paraphrases under ``_mq_speculative_variants``;
        # reading only ``_mq_queries`` discarded that already-paid-for LLM
        # work and re-ran the inline expansion. Prefer the committed preset,
        # fall back to the speculative one.
        _preset_mq = (
            state.get("_mq_queries") or state.get("_mq_speculative_variants")
        )
        # S2 fanout bypass: skip the inline LLM expansion when decompose did
        # not produce ≥2 sub-queries and no pre-computed paraphrases are
        # already on state. Avoids ~3-5s of paraphrase LLM cost on a
        # single-query input where the original query alone retrieves fine.
        # When a pre-computed ``_mq_queries`` preset is present (parallel
        # rewrite path or injected by tests), honour it — the LLM work is
        # already paid for. The flag is the externally observable signal.
        _has_preset_mq = (
            isinstance(_preset_mq, list)
            and len([q for q in _preset_mq if isinstance(q, str) and q.strip()]) > 1
        )
        # Exact spec-code lookup ("giá 155/80R13 bao nhiêu", "khi nào về 2-R17"):
        # the product/spec CODE is the strongest possible signal, so paraphrase
        # fanout adds no recall — it only spends an extra LLM call per turn (and
        # under concurrency those calls make the upstream model service 503).
        # Measured A/B (5×): fanout OFF here is -1.1s/turn with the answer
        # UNCHANGED (5/5 correct both ways). Domain-neutral: keyed on the
        # universal code-token shape (``_parse_code_query`` requires a letter, so
        # a legal "Điều 34" digit-only anchor never matches).
        _exact_code_lookup = _parse_code_query(query_text) is not None
        # Bypass fanout when sub_queries already exist (decompose succeeded)
        # OR pre-computed paraphrases already exist (preset MQ from upstream)
        # OR the query is an exact spec-code lookup.
        # When none apply, the LLM-paraphrase fanout branch must run — this is the
        # path that produced 0 fires in Case B "Điều 38 và 3" before the gate was
        # inverted (decompose soft-failed → bypass blocked fanout → retrieve ran
        # once with raw query → missed Điều 3).
        _fanout_bypassed = decompose_active or _has_preset_mq or _exact_code_lookup
        if _fanout_bypassed:
            state["fanout_bypassed"] = True
        if (
            not decompose_active
            and isinstance(_preset_mq, list)
            and len(_preset_mq) > 1
        ):
            queries = [str(q) for q in _preset_mq if isinstance(q, str) and q.strip()]
            if not queries:
                queries = [query_text]
        elif (
            not _fanout_bypassed
            and not decompose_active
            and _retrieve_intent_mq_enabled
            and mq_enabled
            and mq_n_variants > 1
            and model_resolver is not None
            and llm is not None
            and not (
                bool(_pcfg(
                    state, "multi_query_skip_chitchat_intent",
                    DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT,
                )) and (state.get("intent") or "") in INTENT_CHITCHAT
            )
            and len(query_text.split()) >= int(_pcfg(
                state, "multi_query_min_tokens", DEFAULT_MULTI_QUERY_MIN_TOKENS,
            ))
        ):
            # Wave M3.7-P2 — accumulator for this call site (mirror
            # of the rewrite_and_mq_parallel path above). WHY:
            # multi_query expand fans out N variants; pre-fix the
            # tokens/cost discarded; now summed into mq_ctx row.
            _mq_agg2: dict[str, Any] = {
                "model": "", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
            }

            async def _mq_llm_complete(*, model_id: str, messages: list[dict], timeout_s: int) -> dict:
                try:
                    cfg = await model_resolver.resolve_runtime(
                        record_tenant_id=state.get("record_tenant_id"),
                        record_bot_id=state.get("record_bot_id"),
                        purpose="multi_query",
                    )
                except InvariantViolation as exc:
                    logger.warning(
                        "model_resolver_no_binding",
                        purpose="multi_query",
                        record_bot_id=str(state.get("record_bot_id")),
                        node="multi_query_fanout",
                        error=str(exc)[:200],
                    )
                    # Outer except-block catches and falls back to [query_text].
                    raise
                result = await llm.complete(cfg, messages=messages, purpose="multi_query")
                # Wave M3.7-P2 accumulate per-variant cost.
                _mq_agg2["model"] = result.get("model_name") or _mq_agg2["model"]
                _mq_agg2["prompt_tokens"] += int(result.get("prompt_tokens", 0) or 0)
                _mq_agg2["completion_tokens"] += int(result.get("completion_tokens", 0) or 0)
                _mq_agg2["cost_usd"] += float(result.get("cost_usd", 0.0) or 0.0)
                return result
            async with state["step_tracker"].step("multi_query_fanout") as mq_ctx:
                mq_model = str(
                    _pcfg(state, "multi_query_model", "") or ""
                ) or DEFAULT_MULTI_QUERY_MODEL
                # Entity-grounded expand requires extractor + per-bot opt-in.
                _entity_grounding_enabled = bool(
                    _pcfg(state, "entity_grounding_enabled", DEFAULT_ENTITY_GROUNDING_ENABLED)
                )
                _entity_max = int(
                    _pcfg(state, "entity_grounding_max_entities", DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES)
                )
                _bot_language = str(state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
                _use_entity_path = bool(
                    entity_extractor is not None
                    and _entity_grounding_enabled
                )
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
                    # Multi-query expansion failure → single-query
                    # fallback (retrieval contract: always proceed).
                    logger.warning("multi_query_node_failed", exc_info=True)
                    queries = [query_text]
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
                )
                # Wave M3.7-P2 — record aggregated MQ fanout cost (call
                # site #2 in the retrieve node). Same short-circuit
                # guard as the rewrite_and_mq_parallel path: only
                # record when the LLM actually ran (prompt_tokens>0).
                if _mq_agg2["prompt_tokens"] > 0:
                    mq_ctx.record_llm(
                        model_used=str(_mq_agg2["model"] or "") or None,
                        prompt_tokens=_mq_agg2["prompt_tokens"],
                        completion_tokens=_mq_agg2["completion_tokens"],
                        cost_usd=_mq_agg2["cost_usd"],
                    )

        # Per-sub-query cap keeps decompose branches focused; paraphrases use global top_k.
        decompose_branch_top_k: int | None = None
        if decompose_active:
            decompose_branch_top_k = int(
                _pcfg(state, "decompose_top_k_per_subquery", DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY),
            )

        # J1 — pre-batch embeddings: with N>1 queries, one embed_batch HTTP
        # round-trip returns all embeddings directly; fan-out branches
        # receive a precomputed_embedding and skip their own _embed_query
        # call entirely.  Fallback (_embed_batch_queries path):
        # embedder without embed_batch → parallel asyncio.gather of
        # individual _embed_query calls (same latency as before).
        _batch_embed_enabled = (
            len(queries) > 1
            and embedder is not None
            and bool(
                _pcfg(state, "pipeline_multi_query_embed_batch_enabled",
                      DEFAULT_PIPELINE_MULTI_QUERY_EMBED_BATCH_ENABLED)
            )
        )
        _precomputed_embeddings: list[list[float]] = []
        if _batch_embed_enabled:
            _precomputed_embeddings = await _embed_batch_queries(queries, state)

        try:
            if len(queries) > 1 and hasattr(vector_store, "hybrid_search"):
                results = await asyncio.gather(
                    *[
                        _run_hybrid_for_query(
                            q,
                            meta_filter=metadata_filter,
                            top_k_override=decompose_branch_top_k,
                            precomputed_embedding=_precomputed_embeddings[i] if _precomputed_embeddings and i < len(_precomputed_embeddings) else None,
                        )
                        for i, q in enumerate(queries)
                    ],
                    return_exceptions=True,
                )
                per_query_chunks: list[list[dict]] = []
                for q, res in zip(queries, results):
                    if isinstance(res, Exception):
                        logger.warning(
                            "retrieve_branch_failed",
                            query=q[:80],
                            error=str(res),
                            source="decompose" if decompose_active else "multi_query",
                        )
                        continue
                    if res is None:
                        continue
                    per_query_chunks.append(list(res))
                if per_query_chunks:
                    rrf_k = int(_pcfg(state, "rag_rrf_k", DEFAULT_RRF_K))
                    async with state["step_tracker"].step("rrf_fuse") as rrf_ctx:
                        chunks = mq_rrf_merge_chunks(per_query_chunks, rrf_k=rrf_k)
                        chunks = chunks[:_retrieve_top_k]
                        rrf_ctx.set_metadata(
                            branches=len(per_query_chunks),
                            merged=len(chunks),
                            rrf_k=rrf_k,
                        )
                    logger.info(
                        "retrieve_rrf_merged",
                        source="decompose" if decompose_active else "multi_query",
                        n_queries=len(queries),
                        successful_branches=len(per_query_chunks),
                        merged_unique=len(chunks),
                    )
                    # 002-C step-3: decomposed legs get their AUTHORITATIVE
                    # stats point-lookup too — synthetic price chunks join the
                    # fused set (score 1.0 rows rank ahead of fuzzy vector).
                    if decompose_active:
                        _sub_stats = await _stats_chunks_for_sub_queries(
                            state=state,
                            sub_queries=sub_queries_state,
                            parse_fn=_parse_code_query,
                            lookup_fn=_do_stats_lookup,
                            min_confidence=float(_pcfg(
                                state, "range_query_min_confidence",
                                RANGE_QUERY_MIN_CONFIDENCE)),
                            stats_limit=int(_pcfg(
                                state, "stats_index_limit",
                                DEFAULT_STATS_INDEX_LIMIT)),
                            expect_price=is_price_ask_query(
                                state.get("query") or "",
                                signals=_get_routing_signals(
                                    state.get("language") or "")),
                            max_subs=int(_pcfg(
                                state, "decompose_stats_max_subs",
                                DEFAULT_DECOMPOSE_STATS_MAX_SUBS)),
                        )
                        if _sub_stats:
                            _have = {str(c.get("chunk_id") or "") for c in chunks}
                            _added = [c for c in _sub_stats
                                      if str(c.get("chunk_id") or "") not in _have]
                            chunks = _added + chunks
                            logger.info("decompose_stats_joined",
                                        n_added=len(_added),
                                        n_subs=len(sub_queries_state))
                    step_ctx.set_metadata(
                        decompose=decompose_active,
                        multi_query=not decompose_active,
                        n_queries=len(queries),
                        branches=len(per_query_chunks),
                    )
                else:
                    chunks = list(existing)
            else:
                res = await _run_hybrid_for_query(query_text, meta_filter=metadata_filter)
                if res is None:
                    if hasattr(vector_store, "hybrid_search") or hasattr(vector_store, "search"):
                        chunks = list(existing)
                        step_ctx.set_metadata(candidates=len(chunks), top_k=_pcfg(state, "top_k", DEFAULT_TOP_K), source="fallback")
                        return {"retrieved_chunks": chunks}
                    chunks = list(existing)
                else:
                    chunks = res
        except (RetrievalError, asyncio.TimeoutError, OSError,
                RuntimeError, ValueError, KeyError, AttributeError):
            # Vector / hybrid search failure must not crash retrieve;
            # fall back to pre-seeded chunks (may be empty).
            logger.exception("retrieve_failed")
            chunks = list(existing)

        # If metadata filter zeroed results, retry without it.
        relax_enabled = bool(
            _pcfg(state, "metadata_fallback_relax_enabled", DEFAULT_METADATA_FALLBACK_RELAX_ENABLED),
        )
        if not chunks and metadata_filter and relax_enabled:
            try:
                if len(queries) > 1 and hasattr(vector_store, "hybrid_search"):
                    relax_results = await asyncio.gather(
                        *[
                            _run_hybrid_for_query(
                                q,
                                meta_filter=None,
                                top_k_override=decompose_branch_top_k,
                            )
                            for q in queries
                        ],
                        return_exceptions=True,
                    )
                    relax_per_query: list[list[dict]] = []
                    for r in relax_results:
                        if isinstance(r, Exception) or r is None:
                            continue
                        relax_per_query.append(list(r))
                    if relax_per_query:
                        rrf_k_relax = int(_pcfg(state, "rag_rrf_k", DEFAULT_RRF_K))
                        chunks = mq_rrf_merge_chunks(relax_per_query, rrf_k=rrf_k_relax)
                        chunks = chunks[:_retrieve_top_k]
                else:
                    relax_res = await _run_hybrid_for_query(query_text, meta_filter=None)
                    if relax_res:
                        chunks = relax_res
                if chunks:
                    logger.info(
                        "metadata_filter_relaxed",
                        filter_keys=list(metadata_filter.keys()),
                        recovered=len(chunks),
                    )
                    step_ctx.set_metadata(metadata_filter_relaxed=True)
            except (RetrievalError, asyncio.TimeoutError, OSError,
                    RuntimeError, ValueError, KeyError):
                # Metadata-relax retry is opportunistic; failure
                # leaves the empty result intact for grade to decide.
                logger.warning("metadata_filter_relax_failed", exc_info=True)

        # Fallback: if rewrite or multi-query fanout returned 0, retry once with verbatim query.
        fallback_enabled = bool(
            _pcfg(state, "retrieve_fallback_enabled", DEFAULT_RETRIEVE_FALLBACK_ENABLED),
        )
        original_query = state.get("query", "")
        rewrite_used = state.get("rewritten_query") or ""
        multi_query_active = len(queries) > 1 and not decompose_active
        rewrite_differs = bool(
            rewrite_used and rewrite_used.strip() != original_query.strip()
        )
        should_fallback = (
            fallback_enabled
            and not chunks
            and bool(original_query)
            and (rewrite_differs or multi_query_active)
        )
        retrieve_mode_marker: str | None = None
        if should_fallback:
            fallback_top_k = int(
                _pcfg(
                    state,
                    "retrieve_fallback_top_k",
                    DEFAULT_RETRIEVE_FALLBACK_TOP_K,
                ),
            )
            async with state["step_tracker"].step("retrieve_fallback") as fb_ctx:
                fb_chunks = await retry_hybrid_with_original(
                    vector_store,
                    original_query,
                    state,
                    embed_fn=_embed_query,
                    top_k=fallback_top_k,
                )
                fb_trigger = "multi_query_empty" if multi_query_active else "rewrite_empty"
                fb_ctx.set_metadata(
                    n_chunks=len(fb_chunks),
                    trigger=fb_trigger,
                    top_k=fallback_top_k,
                )
            if fb_chunks:
                chunks = fb_chunks
                retrieve_mode_marker = "fallback_original"
                logger.info(
                    "retrieve_fallback_to_original",
                    rewrite=rewrite_used[:80],
                    original=original_query[:80],
                    recovered=len(chunks),
                    trigger=fb_trigger,
                )
                step_ctx.set_metadata(fallback="original_query")

        # Stream S8 — multi-stage retrieval fallback. Default OFF; flips to
        # ON when ``system_config.retrieval_multistage_enabled = true``.
        # Walks the configured chain (hybrid -> bm25-only -> keyword ->
        # parent-expand) and early-exits the first time a chunk crosses
        # ``DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD``. The chain runs only
        # when the prior single-shot path produced 0 chunks or top score
        # is below threshold, so cost stays flat on the happy path.
        multistage_enabled = bool(
            _pcfg(state, "retrieval_multistage_enabled", DEFAULT_RETRIEVAL_MULTISTAGE_ENABLED),
        )
        if multistage_enabled:
            _ms_threshold = float(
                _pcfg(state, "retrieval_early_exit_threshold",
                      DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD),
            )
            _ms_top_score = max(
                (float(c.get("score", 0) or 0) for c in chunks),
                default=0.0,
            )
            _ms_should_run = not chunks or _ms_top_score < _ms_threshold
            if _ms_should_run:
                from ragbot.infrastructure.retrieval_fallback import (
                    build_retrieval_fallback,
                )
                _ms_stage_names: list[str] = []
                for _i, _default_stage in enumerate(DEFAULT_RETRIEVAL_STAGES, start=1):
                    _cfg_key = f"retrieval_stage_{_i}"
                    _ms_stage_names.append(
                        str(_pcfg(state, _cfg_key, _default_stage) or _default_stage),
                    )
                _ms_query_emb: list[float] = []
                if hasattr(vector_store, "hybrid_search"):
                    try:
                        _ms_query_emb = await _embed_query(query_text, state) or []
                    except (EmbeddingError, asyncio.TimeoutError, OSError,
                            RuntimeError, ValueError, AttributeError):
                        # Multistage retrieval pre-embed must not
                        # crash retrieve; lexical-only stages still run.
                        logger.warning("multistage_embed_failed", exc_info=True)
                        _ms_query_emb = []
                _ms_session_factory = state.get("session_factory")
                _ms_chunks: list[dict] = list(chunks)
                _ms_top_k = int(_pcfg(state, "top_k", DEFAULT_TOP_K))
                async with state["step_tracker"].step("multistage_retrieval") as _ms_ctx:
                    _ms_executed: list[str] = []
                    for _stage_name in _ms_stage_names:
                        try:
                            _impl = build_retrieval_fallback(_stage_name)
                            _stage_out = await _impl.retrieve(
                                query=query_text,
                                query_embedding=_ms_query_emb,
                                record_bot_id=state["record_bot_id"],
                                top_k=_ms_top_k,
                                prior_stage_result=_ms_chunks,
                                vector_store=vector_store,
                                session_factory=_ms_session_factory,
                                channel_type=_required_channel_type(state),
                                embedding_column=state.get("embedding_column"),
                                metadata_filter=metadata_filter or None,
                                # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                                record_tenant_id=state.get("record_tenant_id"),
                            )
                        except (RetrievalError, asyncio.TimeoutError,
                                OSError, RuntimeError, ValueError,
                                KeyError, AttributeError):
                            # Stage isolation: failure of one stage
                            # must not stop the chain (next stage retries).
                            logger.warning(
                                "multistage_retrieval_stage_failed",
                                stage=_stage_name,
                                exc_info=True,
                            )
                            continue
                        _ms_executed.append(_stage_name)
                        if _stage_out:
                            # Merge unique by chunk_id while preserving order:
                            # prior chunks first, then new stage output.
                            _existing_ids = {
                                str(c.get("chunk_id") or c.get("id") or "")
                                for c in _ms_chunks
                            }
                            for _sc in _stage_out:
                                _sc_id = str(_sc.get("chunk_id") or _sc.get("id") or "")
                                if _sc_id and _sc_id not in _existing_ids:
                                    _ms_chunks.append(_sc)
                                    _existing_ids.add(_sc_id)
                        _ms_max = max(
                            (float(c.get("score", 0) or 0) for c in _ms_chunks),
                            default=0.0,
                        )
                        if _ms_chunks and _ms_max >= _ms_threshold:
                            break
                    _ms_ctx.set_metadata(
                        stages_executed=_ms_executed,
                        final_candidates=len(_ms_chunks),
                        threshold=_ms_threshold,
                    )
                if _ms_chunks and len(_ms_chunks) > len(chunks):
                    logger.info(
                        "multistage_retrieval_recovered",
                        before=len(chunks),
                        after=len(_ms_chunks),
                        stages_run=_ms_executed,
                    )
                    chunks = _ms_chunks

        # If query is accent-free, restore diacritics + run a supplementary BM25 search.
        if _vi_preprocessing and _pcfg(state, "diacritic_restoration_enabled", False) and chunks is not None:
            try:
                _use_model = bool(_pcfg(state, "diacritic_restoration_use_model", False))
                _custom_diacritics = custom_vocab.get("diacritics", {}) if isinstance(custom_vocab, dict) else {}
                restored_query = await restore_diacritics(
                    query_text, use_model=_use_model, custom_map=_custom_diacritics or None,
                )
                if restored_query and restored_query != query_text:
                    logger.debug(
                        "diacritic_restored_query",
                        original=query_text[:80],
                        restored=restored_query[:80],
                    )
                    if (
                        hasattr(vector_store, "hybrid_search")
                        and "query_text" in inspect.signature(vector_store.hybrid_search).parameters
                    ):
                        restored_embedding = await _embed_query(restored_query, state)
                        if restored_embedding:
                            _dr_kwargs: dict[str, Any] = {
                                "query_text": restored_query,
                                "query_embedding": restored_embedding,
                                "record_bot_id": state["record_bot_id"],
                                "top_k": _pcfg(state, "top_k", DEFAULT_TOP_K),
                            }
                            _dr_sig_params = set(inspect.signature(vector_store.hybrid_search).parameters.keys())
                            if "channel_type" in _dr_sig_params:
                                _dr_kwargs["channel_type"] = _required_channel_type(state)
                            if (
                                "embedding_column" in _dr_sig_params
                                and state.get("embedding_column")
                            ):
                                _dr_kwargs["embedding_column"] = state["embedding_column"]
                            # mega-sprint-G1: thread tenant for RLS-enforced runtime DSN.
                            if (
                                "record_tenant_id" in _dr_sig_params
                                and state.get("record_tenant_id") is not None
                            ):
                                _dr_kwargs["record_tenant_id"] = state["record_tenant_id"]
                            _dr_raw = await vector_store.hybrid_search(**_dr_kwargs)
                            if _dr_raw:
                                existing_ids = {
                                    str(c.get("chunk_id") or c.get("id") or "")
                                    for c in chunks
                                }
                                for rc in _dr_raw:
                                    rc_id = str(rc.get("chunk_id") or rc.get("id") or "")
                                    if rc_id and rc_id not in existing_ids:
                                        chunks.append(rc)
                                        existing_ids.add(rc_id)
                                logger.debug(
                                    "diacritic_restore_merged",
                                    new_chunks=len(_dr_raw),
                                    total=len(chunks),
                                )
            except (RetrievalError, EmbeddingError, asyncio.TimeoutError,
                    OSError, RuntimeError, ValueError, KeyError):
                # Diacritic-restored supplementary search is best-effort;
                # original chunks are kept on failure.
                logger.warning("diacritic_restoration_search_failed", exc_info=True)

        # Lexical / BM25 retrieval branch (Strategy + DI). Runs in
        # parallel-spirit with the vector branch then RRF-fuses with
        # the existing chunk list. Default OFF via NullLexicalRetrieval
        # (provider="null") so pre-S7 behaviour is preserved bit-exact
        # when ``lexical_retrieval`` is the Null Object. Adapter failures
        # degrade silently — lexical is auxiliary, must not crash retrieve.
        if (
            lexical_retrieval is not None
            and not _is_null_lexical(lexical_retrieval)
            and state.get("record_bot_id")
        ):
            _lex_top_k = int(_pcfg(state, "lexical_top_k", DEFAULT_LEXICAL_TOP_K))
            _lex_rrf_k = int(_pcfg(state, "lexical_rrf_k", DEFAULT_LEXICAL_RRF_K))
            _lex_query = (state.get("rewritten_query") or state.get("query") or "")
            # Per-bot opt-in: widens the BM25 tsvector surface to
            # ``content + chunk_context`` so the Anthropic CR
            # situated-context string is rank-visible. Default OFF —
            # opted-out bots take the indexed legacy path.
            # LEGAL-RETRIEVAL-FIX 2026-05-21: fallback uses
            # ``DEFAULT_CR_ENHANCED_ENABLED`` (constants SSoT) so corpora
            # ingested with CR enrichment still get the BM25 tsvector
            # widening when ``pipeline_config`` is missing the key (e.g.
            # legacy chat path that pre-dates the chat_worker 3-tier
            # resolve). chat_worker injects the resolved value first;
            # this default is the last-resort fallback only.
            _lex_cr_enhanced = bool(
                _pcfg(state, "cr_enhanced_enabled", DEFAULT_CR_ENHANCED_ENABLED),
            )
            try:
                _lex_hits = await lexical_retrieval.search(
                    _lex_query,
                    state["record_bot_id"],
                    _lex_top_k,
                    cr_enhanced=_lex_cr_enhanced,
                )
            except (RetrievalError, asyncio.TimeoutError, OSError,
                    RuntimeError, ValueError, AttributeError):
                # Lexical / BM25 is auxiliary; failure must not crash
                # retrieve (vector branch result is preserved).
                logger.warning("lexical_retrieval_failed", exc_info=True)
                _lex_hits = []
            if _lex_hits:
                _before_fuse = len(chunks)
                chunks = mq_rrf_merge_chunks(
                    [chunks, _lex_hits], rrf_k=_lex_rrf_k,
                )
                chunks = chunks[:_retrieve_top_k]
                logger.debug(
                    "lexical_rrf_fused",
                    vector_count=_before_fuse,
                    lexical_count=len(_lex_hits),
                    fused_count=len(chunks),
                )
                await _audit(
                    state,
                    "lexical_rrf_fused",
                    {
                        "vector_count": _before_fuse,
                        "lexical_count": len(_lex_hits),
                        "fused_count": len(chunks),
                        "rrf_k": _lex_rrf_k,
                    },
                )

        # Permission pre-filter: chunks visible only when user_groups overlap doc.access_groups.
        if _pcfg(state, "permission_filtering_enabled", False) and chunks:
            user_groups = set(state.get("user_groups") or [])
            default_public = _pcfg(state, "permission_default_public", True)
            filtered: list[dict] = []
            for chunk in chunks:
                doc_groups = chunk.get("access_groups") or []
                if not doc_groups:
                    if default_public:
                        filtered.append(chunk)
                elif user_groups & set(doc_groups):
                    filtered.append(chunk)
            logger.debug(
                "permission_filter",
                before=len(chunks),
                after=len(filtered),
                user_groups=list(user_groups),
            )
            chunks = filtered

        # Parent-child expansion: swap child chunks for their parent doc content (small-to-big).
        _pc_session_factory = state.get("session_factory")
        if _pcfg(state, "parent_child_enabled", False) and chunks and _pc_session_factory is not None:
            child_ids_with_parent = [
                c.get("parent_chunk_id") for c in chunks
                if c.get("parent_chunk_id")
            ]
            _record_bot_id_pc = state.get("record_bot_id")
            if child_ids_with_parent and _record_bot_id_pc:
                try:
                    async with _pc_session_factory() as _pc_session:
                        _pc_result = await _pc_session.execute(
                            sa_text(
                                "SELECT dc.id, dc.content, dc.metadata_json "
                                "FROM document_chunks dc "
                                "JOIN documents d ON d.id = dc.record_document_id "
                                "WHERE dc.id = ANY(:ids) AND d.record_bot_id = :rbid "
                                "  AND d.deleted_at IS NULL"
                            ),
                            {"ids": child_ids_with_parent, "rbid": _record_bot_id_pc},
                        )
                        _parent_map: dict[str, dict] = {}
                        for row in _pc_result.fetchall():
                            _parent_map[str(row[0])] = {
                                "content": row[1],
                                "text": row[1],
                                "metadata_json": row[2],
                            }

                    parent_ids_to_fetch = child_ids_with_parent
                    fetched_ids = set(_parent_map.keys())
                    expected_ids = {str(pid) for pid in parent_ids_to_fetch}
                    missing = expected_ids - fetched_ids
                    if missing:
                        logger.warning("parent_chunks_missing", missing_count=len(missing), missing_ids=list(missing)[:5])

                    expanded = expand_parent_chunks(chunks, _parent_map)
                    logger.debug(
                        "parent_child_expansion",
                        before=len(chunks),
                        after=len(expanded),
                        parents_expanded=sum(1 for c in expanded if c.get("is_parent_expanded")),
                    )
                    chunks = expanded
                except (SQLAlchemyError, OSError, RuntimeError,
                        KeyError, ValueError):
                    # Parent-child SQL expansion failure → keep child
                    # chunks as-is (small-to-big becomes a no-op).
                    logger.warning("parent_child_expansion_failed", exc_info=True)

        # Dynamic autocut after a significant score cliff.
        if _pcfg(state, "autocut_enabled", False) and chunks:
            before_autocut = len(chunks)
            autocut_ratio = float(_pcfg(state, "autocut_min_gap_ratio", 0.3))
            chunks = _autocut(chunks, min_gap_ratio=autocut_ratio)
            if len(chunks) < before_autocut:
                logger.debug(
                    "autocut_applied",
                    before=before_autocut,
                    after=len(chunks),
                    gap_ratio=autocut_ratio,
                )

        step_ctx.set_metadata(candidates=len(chunks), top_k=_pcfg(state, "top_k", DEFAULT_TOP_K), source="vector_store")
        from ragbot.shared.constants import (
            DEFAULT_PIPELINE_AUDIT_RETRIEVAL_PREVIEW as _RP,
        )
        _scores = [float(c.get("score", 0) or 0) for c in chunks]
        await _audit(
            state,
            "hybrid_search_executed",
            {
                "top_k": _pcfg(state, "top_k", DEFAULT_TOP_K),
                "rrf_k": _pcfg(state, "rrf_k", DEFAULT_RRF_K),
                "candidates_count": len(chunks),
                "top_score": round(max(_scores), 6) if _scores else 0,
                "min_score": round(min(_scores), 6) if _scores else 0,
                "metadata_filter": metadata_filter or {},
            },
        )
        await _audit(
            state,
            "chunks_retrieved",
            {
                "count": len(chunks),
                "chunks": [
                    {
                        "chunk_id": str(c.get("chunk_id") or c.get("id") or ""),
                        "score": round(float(c.get("score", 0) or 0), 6),
                        "doc_name": (
                            c.get("document_name")
                            or (c.get("metadata") or {}).get("document_title")
                            or ""
                        ),
                        "content_preview": (c.get("content") or c.get("text") or "")[:_RP],
                    }
                    for c in chunks[:10]
                ],
            },
        )

        # Superlative enrichment: pre-sort matched items into context_base; LLM still composes the answer.
        _query_text = state.get("rewritten_query") or state.get("query") or ""
        _sup_lang = str(state.get("language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
        _enricher = _get_superlative_enricher(_sup_lang)
        _enriched = _enricher.enrich_state(
            dict(state),
            query=_query_text,
            chunks=chunks,
        )
        _context_base = _enriched.get("context_base")
        _ret_payload: dict[str, Any] = {"retrieved_chunks": chunks}
        if _context_base:
            _ret_payload["context_base"] = _context_base
        # LangGraph merges return into state; direct state[...] mutation would not propagate.
        if retrieve_mode_marker:
            _ret_payload["retrieve_mode"] = retrieve_mode_marker
        return _ret_payload


__all__ = ["retrieve"]
