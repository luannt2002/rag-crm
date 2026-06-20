"""Sync DB reader for system_config (hot-reload friendly).

Used by DI Factory providers in ``bootstrap.py`` to materialise per-call
behaviour-toggle reads against ``system_config.value`` without forcing
``async`` into the synchronous DI resolution path.

Why this exists:
- CLAUDE.md ``Zero hardcode`` mandates DB is the single source of truth
  for behavior-toggle defaults; constants are the FALLBACK only.
- DI providers had been frozen at boot to constants, ignoring DB row
  entirely — operator flips of ``system_config.<key>`` had no effect
  until app restart.

Hot-reload contract:
- Each ``get_boot_config(key, default)`` call returns the value present
  in DB at most ``_CACHE_TTL_S`` seconds ago. An admin API update lands
  on the next request after the in-process TTL expires.
- TTL is short (30s) so admin flips feel instant; long enough that one
  call per hot-path turn doesn't hammer PG.

Safety:
- Whitelist ``key`` against ``_ALLOWED_KEYS`` to prevent accidental SQL
  injection vectors from any caller passing user input.
- Connection failures log + fall through to ``default``; the worker
  never crashes because Postgres dropped a packet.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

# psycopg2 import is lazy inside the helper so import of this module is
# free at static-analysis time even if the binary isn't installed yet.

logger = logging.getLogger(__name__)

# Whitelist of system_config keys callable from DI bootstrap. Adding a
# key here is a deliberate decision — most config should resolve via
# the async path at request time.
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "reranker_provider",
        "reranker_model",
        "embedding_provider",
        "embedding_model",
        "embedding_dimension",
        "embedding_text_strategy",
        # Per-provider feature flag — gates the BKAI Vietnamese Bi-Encoder
        # adapter. Default OFF; flip in system_config after staging the
        # ``embedding_provider="bkai_vn"`` row (two-step rollout).
        "bkai_vn_embedder_enabled",
        "enrichment_model",
        "crag_grader_provider",
        "entity_extractor_provider",
        "pii_redactor_provider",
        # Adaptive Router L1 (query complexity classifier) — domain-neutral
        # detector. Weights/threshold/conjunction-list resolved at node
        # invocation time. Boot-cached because the values change rarely
        # and the classifier runs on every query (hot path).
        "query_complexity.weight_comma",
        "query_complexity.weight_conjunction",
        "query_complexity.weight_numbers",
        "query_complexity.weight_question",
        "query_complexity.length_normalizer",
        "query_complexity.complexity_threshold",
        "query_complexity.conjunctions",
        # Adaptive Router L3 (LLM decomposer) — fires only when L1 reports
        # complex. Per-tenant override happens via system_config row update;
        # short TTL ensures bot owner flips feel instant.
        "decomposer.enabled",
        "decomposer.model",
        "decomposer.max_tokens",
        "decomposer.max_sub_queries",
        # Token quota monetization keys (operator-tunable runtime).
        # Admin API can PATCH these without restart; bootstrap_config
        # respects the 30s TTL cache.
        "max_tokens_total",
        "output_tokens_per_response_default",
        "token_quota_notify_enabled",
        "token_quota_notify_throttle_s",
        "token_quota_reset_timezone",
        # Cascade Routing — tier model names resolved at request time.
        # ``default_answer_model`` is the existing platform default that
        # mid-band cascade falls back to; ``cascade_low_model`` /
        # ``cascade_high_model`` are the tier-specific overrides. All three
        # are model-name strings (e.g. ``claude-haiku-4-5-20251001``)
        # matched against the ``ai_models.name`` SSoT.
        "default_answer_model",
        "cascade_low_model",
        "cascade_high_model",
        # Threshold knobs — resolved on every cascade route call. Short
        # TTL keeps bot-owner tuning observable without a restart.
        "cascade_t_low",
        "cascade_t_high",
        # LEGAL-RETRIEVAL-FIX 2026-05-20 — ArticleAwareFilter + WA-3 enhanced
        # CR enrichment are container-boot resolved via DI factory; the
        # underlying ``system_config`` rows must be on the whitelist or
        # ``get_boot_config`` swallows the lookup as "not allowlisted" +
        # falls back to the constants default (no-op filter / per-bot OFF).
        "metadata_filter_provider",
        "article_ref_patterns",
        "cr_enhanced_enabled",
        "structured_ref_extraction_enabled",
        # LEGAL-RETRIEVAL-FIX 2026-05-21 — Wave S7 ship `8fae3b4` introduced
        # these DI Factory keys in bootstrap.py but forgot to add them to the
        # whitelist. Operator UPDATE on system_config silently no-ops until
        # the matching default value happens to differ from the seeded one.
        # ``lexical_retrieval_provider`` defaults to ``"null"`` (no-op
        # adapter) so the BM25 hybrid path was dead-code for any tenant that
        # tried to enable it via DB.
        "lexical_retrieval_provider",
        "vector_store_provider",
        # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 1 — emit header + footer
        # synthetic chunks for mixed-CSV docs (intro + table + trailing
        # notes). Default ``DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED``
        # is OFF so existing ingest stays byte-identical; flipped in Phase 5
        # after re-ingest validation pass.
        "table_csv_emit_header_footer_chunks_enabled",
        # 260620 — per-key Jina embedding TPM limiter, config-driven so the
        # leader scales free→pro (or down) without a deploy. Read by
        # build_embedder → JinaEmbedder(tpm_per_key=, tpm_safety_fraction=).
        # Defaults: DEFAULT_JINA_EMBEDDING_TPM_LIMIT (100k) / _SAFETY_FRACTION (0.9).
        "jina_embedding_tpm_per_key",
        "jina_embedding_tpm_safety_fraction",
        # 260525-WHITELIST-COMPLETE — 9 keys that ``get_boot_config()`` was
        # already reading at runtime but the whitelist gate silently dropped
        # to constant defaults (zero-hardcode regression). Verified via grep
        # diff of ``get_boot_config`` call sites against this allow-list.
        # AdapChunk Layer 5 thresholds (chunking.py:apply_cross_check):
        "adapchunk_layer5_cross_check_enabled",
        "adapchunk_l5_confidence_threshold",
        "adapchunk_l5_hdt_min_headings",
        "adapchunk_l5_semantic_min_avg_block_len",
        "adapchunk_l5_proposition_max_avg_block_len",
        "adapchunk_l5_proposition_max_headings",
        "adapchunk_l5_mixed_content_warn_threshold",
        # Atomic-block protect for formulas/images/code fences
        # (chunking.py:_atomic_protect_enabled).
        "formula_image_atomic_protect_enabled",
        # understand_query Redis cache TTL (query_graph.py around line 2047)
        # — operator override knob for the per-conversation rewrite/intent
        # memoisation window.
        "understand_query.cache_ttl_s",
        # 260521-CHUNK-AGGREGATION-UNIVERSAL Phase 3 — per-intent caps on
        # rerank top_n + assembled context chars. JSONB value with intent
        # name keys (e.g. {"aggregation": 20, "factoid": 7}). Aggregation
        # queries need wider funnel; defaults defined in constants.py
        # (``DEFAULT_RERANK_TOP_N_BY_INTENT`` /
        # ``DEFAULT_GENERATE_CONTEXT_CHARS_CAP_BY_INTENT``).
        "rerank_top_n_by_intent",
        "generate_context_chars_cap_by_intent",
        # 260526 — per-intent retrieve top_k cap applied at RRF-fuse
        # and lexical-fuse slice points in the retrieve node.
        "retrieve_top_k_by_intent",
        # 260525 Bug #10 — per-intent MMR similarity threshold.
        # Aggregation queries need looser dedup so row-shape CSV chunks
        # with the same column structure but different data values
        # (e.g. multiple price rows at "1499000") survive MMR.
        "mmr_similarity_threshold_by_intent",
        # 260526 T2-CostPerf — per-intent skip flags for rewrite +
        # multi_query. Lightweight intents skip both LLM calls (~3.5s
        # saved/turn). Operator override via system_config JSONB row.
        "rewrite_enabled_by_intent",
        "multi_query_enabled_by_intent",
        # 260525 Bug #7c — bulk close 78 keys reachable from
        # ``query_graph._pcfg`` but never on the whitelist; without
        # this addition any operator UPDATE on the matching
        # ``system_config`` row gets dropped by ``get_boot_config``
        # as "not allowlisted" + falls back to caller default.
        # Defence-in-depth pin test:
        # ``test_pipeline_cfg_keys_parity::test_per_intent_keys_in_pipeline_cfg_tuple``
        # gates each new ``_pcfg`` introduction in code review.
        "adaptive_router_l1_enabled",
        "bm25_substring_fallback_enabled",
        "crag_grade_concurrency",
        "crag_lenient_grade_for_compound_intents_enabled",
        "crag_min_relevant_count",
        "crag_min_relevant_fraction",
        "decompose_confidence_gate",
        "decompose_enabled",
        "decompose_min_tokens",
        "decompose_top_k_per_subquery",
        "decompose_use_structured_output",
        "draft_model",
        "entity_grounding_enabled",
        "entity_grounding_max_entities",
        "generate_context_chars_cap",
        "generate_context_trust_hint_enabled",
        "generate_p95_sla_ms",
        "generate_use_structured_output",
        "generic_vocab_enabled",
        "generic_vocab_max_expansions",
        "generic_vocab_max_matches",
        "grade_timeout_s",
        "grade_use_batch",
        "grade_use_structured_output",
        "grounding_check_async_enabled",
        "grounding_check_async_intents",
        "grounding_check_async_top_score_threshold",
        "grounding_intents",
        "guardrail_oos_similarity_threshold",
        "intent_extractor_model",
        "intent_extractor_system_prompt",
        "lexical_rrf_k",
        "lexical_top_k",
        "lost_in_middle_reorder_enabled",
        "max_total_graph_iterations",
        "metadata_extraction_vocabulary",
        "multi_query_dedup_threshold",
        "multi_query_entity_gate_enabled",
        "multi_query_min_tokens",
        "multi_query_skip_chitchat_intent",
        "neighbor_expand_enabled",
        "neighbor_max_concurrency",
        "neighbor_token_budget",
        "neighbor_window_size",
        "output_tokens_per_response_default",
        "pipeline_multi_query_embed_batch_enabled",
        "pipeline_multi_query_speculative_enabled",
        "pipeline_multi_query_speculative_timeout_s",
        "pipeline_parallel_cache_understand_enabled",
        "pipeline_parallel_output_guards_enabled",
        "pipeline_parallel_rewrite_mq_enabled",
        "prompt_token_opt_dedupe_jaccard_threshold",
        "prompt_token_opt_enabled",
        "prompt_token_opt_factoid_skip_history",
        "prompt_token_opt_min_chunk_score",
        "rag_rrf_k",
        "reflect_skip_if_grounded",
        "reflect_skip_top_score_floor",
        "reflect_use_structured_output",
        "reflection_enabled",
        "rerank_threshold_gate_after_cliff_enabled",
        "retrieval_early_exit_threshold",
        "retrieval_multistage_enabled",
        "retrieve_fallback_enabled",
        "retrieve_fallback_top_k",
        "rrf_k",
        "self_rag_critique_enabled",
        "self_rag_critique_threshold",
        "skip_understand_for_greeting",
        "speculative_hallu_verify_enabled",
        "speculative_retrieve_enabled",
        "speculative_retrieve_timeout_s",
        "speculative_similarity_threshold",
        "speculative_streaming_enabled",
        "structured_output_enabled",
        "understand_greeting_patterns",
        "understand_skip_below_tokens",
        "understand_use_structured_output",
    }
)

# Short in-process cache so DI Factory providers don't open a PG
# connection on every request. TTL chosen short enough that an admin
# system_config flip feels instant (≤30s); long enough that one
# request per hot path doesn't hammer PG.
_CACHE_TTL_S: float = 30.0
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def invalidate_cache(key: str | None = None) -> None:
    """Drop cached value for ``key`` (or all keys if ``None``).

    Call from an admin API handler after writing to ``system_config`` so
    the next request observes the new value without waiting for the TTL.
    """
    with _cache_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


def _resolve_dsn() -> str | None:
    """Build a psycopg2-compatible DSN from the runtime DATABASE_URL."""
    raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not raw:
        return None
    # Strip async driver prefix (``postgresql+asyncpg://...``) → ``postgresql://...``.
    if "+" in raw.split("://", 1)[0]:
        scheme, rest = raw.split("://", 1)
        raw = scheme.split("+", 1)[0] + "://" + rest
    parsed = urlparse(raw)
    if not parsed.hostname:
        return None
    return raw


def get_boot_config(key: str, default: Any) -> Any:
    """Read ``system_config.value`` for ``key`` with short-TTL caching.

    Returns ``default`` on any failure (key missing, DB unreachable,
    malformed JSON). Strings come back unquoted (jsonb "foo" → "foo").
    Hot-reload: admin API mutating system_config should call
    ``invalidate_cache(key)`` so the next request reads fresh DB state.
    """
    if key not in _ALLOWED_KEYS:
        logger.warning("bootstrap_config_key_not_allowlisted", extra={"key": key})
        return default

    # Fast path — TTL cache hit.
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) < _CACHE_TTL_S:
            return entry[1]

    dsn = _resolve_dsn()
    if not dsn:
        return default
    try:
        import psycopg2  # noqa: PLC0415 — lazy import keeps module-level light
    except ImportError:
        logger.warning("bootstrap_config_psycopg2_missing")
        return default
    # Lazy constant import — bootstrap_config is the SSoT for system_config
    # itself, so we can't pull constants at module level without risking a
    # circular import on cold boot. The 3-second cap is the same value we
    # had inline; it just lives in shared/constants now for ops tunability.
    from ragbot.shared.constants import DEFAULT_DB_BOOTSTRAP_CONNECT_TIMEOUT_S  # noqa: PLC0415
    try:
        conn = psycopg2.connect(dsn, connect_timeout=DEFAULT_DB_BOOTSTRAP_CONNECT_TIMEOUT_S)
    except (psycopg2.OperationalError, psycopg2.DatabaseError) as exc:
        logger.warning("bootstrap_config_connect_failed", extra={"err": str(exc)})
        return default
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,),
            )
            row = cur.fetchone()
    except psycopg2.Error as exc:
        logger.warning("bootstrap_config_query_failed", extra={"err": str(exc), "key": key})
        return default
    finally:
        conn.close()
    if row is None:
        # Negative cache too so we don't re-hit DB for missing keys.
        with _cache_lock:
            _cache[key] = (now, default)
        return default
    raw_value = row[0]
    # psycopg2 returns jsonb as Python type already (dict/list/str/int).
    # Some drivers return as str — defensively re-parse if needed.
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except (json.JSONDecodeError, ValueError):
            pass
    with _cache_lock:
        _cache[key] = (now, raw_value)
    return raw_value


__all__ = ["get_boot_config", "invalidate_cache"]
