"""Bot plan limits — single source of truth.

Logic: bot column > bot plan_limits JSON > system_config default > schema default.
Thêm tính năng = thêm entry vào PLAN_LIMIT_SCHEMA, không cần migration.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    ALLOWED_SYSPROMPT_VERSIONS,
    DEFAULT_BLOCKS_API_ENABLED,
    DEFAULT_CASCADE_ROUTING_ENABLED,
    DEFAULT_CHUNK_HASH_ID_ENABLED,
    DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
    DEFAULT_EMBEDDING_PASSAGE_PREFIX,
    DEFAULT_GREETING_PATTERNS,
    DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS,
    DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
    DEFAULT_GROUNDING_CHECK_ENABLED,
    DEFAULT_STATS_SERVE_REQUIRE_VALUE,
    DEFAULT_GROUNDING_CONFIRMED_ACTION,
    DEFAULT_HYDE_ENABLED,
    DEFAULT_MODALITY_RERANK_ENABLED,
    DEFAULT_NEIGHBOR_EXPAND_ENABLED,
    DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
    DEFAULT_NEIGHBOR_TOKEN_BUDGET,
    DEFAULT_NEIGHBOR_WINDOW_SIZE,
    DEFAULT_PDF_MAX_BYTES,
    DEFAULT_REFLECT_SKIP_IF_GROUNDED,
    DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
    DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR,
    DEFAULT_RERANK_CLIFF_MIN_KEEP,
    DEFAULT_RERANK_RETRIEVAL_SAFETY_N,
    DEFAULT_ADAPTIVE_CONTEXT_ENABLED,
    DEFAULT_RERANK_FILTER_STRATEGY,
    DEFAULT_RERANK_SKIP_INTENTS,
    DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
    DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
    DEFAULT_SELF_RAG_ENABLED,
    DEFAULT_SELF_RAG_THRESHOLD,
    DEFAULT_SEMANTIC_CACHE_THRESHOLD,
    SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED,
    DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    DEFAULT_SPECULATIVE_STREAMING_ENABLED,
    DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
    DEFAULT_SYSPROMPT_VERSION,
    DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
)


# Schema cho plan_limits — document tất cả keys có thể có
PLAN_LIMIT_SCHEMA: dict[str, dict[str, Any]] = {
    "retrieval_top_k": {"type": "int", "default": 20, "min": 5, "max": 200},
    "reflection_enabled": {"type": "bool", "default": False},
    # Speculative Streaming Phase 3 (Wave K2) — HALLU verifier gate.
    # When True AND speculative-streaming is wired, the draft model's
    # buffered first ~50 tokens are compared against the main model's
    # first chunk; mismatch → emit ``redo`` SSE + switch to main stream.
    # Default OFF preserves HALLU=0 sacred until operator validates the
    # verifier on production traffic.
    "speculative_hallu_verify_enabled": {"type": "bool", "default": False},
    "grounding_check_enabled": {"type": "bool", "default": DEFAULT_GROUNDING_CHECK_ENABLED},
    # Truth-audit option (b): customer-facing stats queries exclude SHELL rows
    # (no price + identity-only attrs) — serving them next to a priced sibling
    # produced 45/45 wrong-brand prices (baseline N=15). Per-bot opt-OUT knob;
    # platform default ON per decision record (specs/001-rag-truth-audit).
    "stats_serve_require_value": {"type": "bool", "default": DEFAULT_STATS_SERVE_REQUIRE_VALUE},
    # When True the stats/structured-index route SKIPS the grounding judge.
    # Default False = grounding applies to stats answers too (HALLU-safe; catches
    # an answer citing a value absent from the matched entity). Owners who hit
    # false-blocks on legitimately-reformatted structured numbers opt back in.
    "stats_route_skip_grounding": {
        "type": "bool",
        "default": DEFAULT_STATS_ROUTE_SKIP_GROUNDING,
    },
    # B5 Phase B: opt-in async grounding. When True the judge runs as a
    # background task for high-confidence requests (factoid intent +
    # top_score >= async_top_score_threshold) and the response ships
    # immediately. Breach paths log + emit metric for out-of-band alerting.
    # Default False keeps HALLU=0 sacred sync path.
    "grounding_check_async_enabled": {
        "type": "bool",
        "default": DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED,
    },
    "grounding_check_async_top_score_threshold": {
        "type": "float",
        "default": DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD,
        "min": 0.0,
        "max": 1.0,
    },
    "grounding_check_async_intents": {
        "type": "list_str",
        "default": tuple(DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS),
    },
    "enrichment_mode": {
        "type": "str",
        "default": "template",
        "options": ["template", "llm"],
    },
    "cache_ttl_s": {"type": "int", "default": 3600, "min": 60, "max": 86400},
    "embedding_model": {"type": "str", "default": ""},
    # Per-bot passage prefix for asymmetric embedding; re-ingest required
    # after toggling. Empty string falls back to system_config (also empty by default).
    "embedding_passage_prefix": {
        "type": "str",
        "default": DEFAULT_EMBEDDING_PASSAGE_PREFIX,
    },
    "graph_rag_mode": {
        "type": "str",
        "default": "disabled",
        "options": ["disabled", "enabled", "adaptive"],
    },
    "priority_tier": {
        "type": "str",
        "default": "shared",
        "options": ["shared", "priority"],
    },
    "vietnamese_preprocessing_enabled": {"type": "bool", "default": True},
    # PII redaction at boundary (Master Finding #4 wire). Per-bot opt-in.
    # Default False keeps backward compat — bot owner flips True to mask
    # raw email/phone/CCCD/card/etc. before persist + LLM. Provider selected
    # via system_config.pii_redactor_provider.
    "pii_redaction_enabled": {"type": "bool", "default": False},
    # Universal PII coverage (Phase D2). Extends `pii_redaction_enabled`
    # beyond chat-query + ingest-content to ALL data paths: audit_log
    # before/after JSONB, request_steps metadata + error text, telemetry
    # events. Default False keeps backward compat — bot owner must
    # explicitly opt-in. When True AND a redactor is wired AND the bot
    # also has `pii_redaction_enabled=True`, every persistence boundary
    # masks recognised PII shapes before the row hits Postgres.
    "pii_redaction_universal": {"type": "bool", "default": False},
    # ── Threshold overrides (Stream V Phase 2) ─────────────────────────
    # Bot owner tunes these to balance precision/recall per-domain.
    # Resolve: bots.threshold_overrides > plan_limits > system_config.
    # ``reranker_min_score_active`` doubles as the post-rerank refuse gate
    # threshold: when the rerank node's top-1 final score sits below this
    # value (and a real reranker actually ran), the gate drops all chunks
    # so the existing refuse short-circuit at generate emits the bot's
    # ``oos_answer_template`` (no application-injected text).
    "reranker_min_score_active":   {"type": "float", "default": DEFAULT_RERANKER_MIN_SCORE_ACTIVE, "min": 0.0, "max": 1.0},
    "grounding_check_threshold":   {"type": "float", "default": 0.30, "min": 0.0, "max": 1.0},
    # A1 — action when the grounding judge CONFIRMS an ungrounded answer.
    # "observe" (default) ships + flags; "block" substitutes the bot's
    # oos_answer_template. Per-bot opt-in only (see the constant's rationale).
    "grounding_confirmed_action":  {"type": "str",   "default": DEFAULT_GROUNDING_CONFIRMED_ACTION, "options": ["observe", "block"]},
    "guard_output_min_score":      {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0},
    "generate_context_chars_cap":  {"type": "int",   "default": 2900, "min": 500, "max": 50000},
    # ── Semantic cache similarity threshold (WA-7) ──────────────────────
    # Per-bot override for the cosine-similarity cut-off in
    # ``semantic_cache.find_similar``. Default mirrors
    # ``DEFAULT_SEMANTIC_CACHE_THRESHOLD`` (0.97) and is KEPT strict —
    # HALLU sacred risk: a low threshold serves wrong-intent answers
    # because the cache key does NOT include intent classification.
    # Lower (e.g. 0.90) only for explicit A/B opt-in bots after the
    # operator has measured baseline hit rate via
    # ``diagnose_p95_bottleneck.py --cache-stats``.
    "semantic_cache_threshold": {
        "type": "float",
        "default": DEFAULT_SEMANTIC_CACHE_THRESHOLD,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Cliff-detect adaptive filter (Stream V Phase 3) ────────────────
    # Per-bot opt-in to score-distribution-aware filter. When strategy =
    # "cliff", reranker_min_score_active is ignored; pipeline cuts at
    # consecutive score drop > gap_ratio (Pattern B from research).
    "rerank_filter_strategy":      {"type": "str",   "default": DEFAULT_RERANK_FILTER_STRATEGY, "options": ["threshold", "cliff"]},
    "rerank_cliff_gap_ratio":      {"type": "float", "default": 0.35, "min": 0.05, "max": 0.95},
    "rerank_cliff_absolute_floor": {"type": "float", "default": DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR, "min": 0.0, "max": 1.0},
    "rerank_cliff_min_keep":       {"type": "int",   "default": DEFAULT_RERANK_CLIFF_MIN_KEEP, "min": 1, "max": 20},
    "rerank_retrieval_safety_n":   {"type": "int",   "default": DEFAULT_RERANK_RETRIEVAL_SAFETY_N, "min": 0, "max": 10},
    "adaptive_context_enabled":    {"type": "bool",  "default": DEFAULT_ADAPTIVE_CONTEXT_ENABLED},
    # Wave J2 (2026-05-20): owner opt-in to keep the legacy static threshold
    # gate running AFTER the cliff filter. Default OFF — cliff strategy owns
    # filtering. Flip True for audit-heavy compliance bots that prefer
    # "refuse over weak-answer" semantics.
    "rerank_threshold_gate_after_cliff_enabled": {
        "type": "bool",
        "default": DEFAULT_RERANK_THRESHOLD_GATE_AFTER_CLIFF_ENABLED,
    },
    # ── Per-intent rerank skip gate (T2.S7) ────────────────────────────
    # Lightweight intents (greeting/chitchat/factoid lookup) bypass the
    # rerank API when the retrieved-pool already fits inside ``rerank_top_n``
    # (no ambiguity to resolve). Owner override = list[str]; falls back to
    # ``DEFAULT_RERANK_SKIP_INTENTS``. Empty list disables the skip gate
    # entirely (always rerank).
    "rerank_skip_intents": {
        "type": "list_str",
        "default": tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
    },
    # ── Per-bot PDF size override ──────────────────────────────────────
    # System default ships at 10MB; tenants ingesting larger reports
    # (compliance binders, scanned manuals) raise this without changing
    # the global cap. min/max guards keep the override sane.
    "pdf_max_bytes": {
        "type": "int",
        "default": DEFAULT_PDF_MAX_BYTES,
        "min": 1024,
        "max": 200 * 1024 * 1024,
    },
    # ── Sysprompt template version metadata (S10) ──────────────────────
    # Records which reference template the bot owner used as a starting
    # point when authoring ``bots.system_prompt``. METADATA ONLY — the
    # platform never substitutes template text into the LLM call. The
    # bot owner's ``bots.system_prompt`` column remains the single source
    # of truth (CLAUDE.md "Application MINDSET — Bot owner owns
    # everything"). Default keeps the baseline rule set; switching the
    # value to the context-aware label signals to admin tooling that the
    # owner has migrated their prompt to the looser refuse template.
    "sysprompt_version": {
        "type": "str",
        "default": DEFAULT_SYSPROMPT_VERSION,
        "options": list(ALLOWED_SYSPROMPT_VERSIONS),
    },
    # ── Smart-skip retry knobs (T1-Smartness, S1 Pipeline-Opt) ──────────
    # CRAG: pass-1 top retrieval score >= floor → skip grade-LLM call AND
    # bypass rewrite_retry (saves ~10s on high-confidence turns). Default
    # 0.7 (production-tuned). Set > 1.0 to disable. HALLU sacred preserved
    # by downstream grounding_check guardrail.
    "crag_skip_retry_above_score": {
        "type": "float",
        "default": DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE,
        "min": 0.0,
        "max": 1.1,  # > 1.0 = disable-by-overshoot (sentinel)
    },
    # Reflect: if grounded (no llm_grounding_fail flag) AND top_score >=
    # ``reflect_skip_top_score_floor`` → honour the answer rather than
    # burn a second generate+guard pass (~5-6s). Default False keeps
    # legacy retry behaviour.
    "reflect_skip_if_grounded": {
        "type": "bool",
        "default": DEFAULT_REFLECT_SKIP_IF_GROUNDED,
    },
    "reflect_skip_top_score_floor": {
        "type": "float",
        "default": DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Skip understand_query for greeting / short query (T2 perf, Stream B3) ─
    # Bypass the understand LLM call when the user message is either:
    #   1. a "short" query (token count ≤ understand_skip_below_tokens), OR
    #   2. matches one of the configured greeting regex patterns.
    # Saves ~1.5s per greeting/chitchat turn; intent is set to "greeting"
    # directly. Default OFF preserves byte-identical legacy behaviour;
    # bot owner flips per-domain via plan_limits.skip_understand_for_greeting.
    "skip_understand_for_greeting": {
        "type": "bool",
        "default": DEFAULT_SKIP_UNDERSTAND_FOR_GREETING,
    },
    "understand_skip_below_tokens": {
        "type": "int",
        "default": DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS,
        "min": 0,
        "max": 50,
    },
    # Regex patterns (case-insensitive, anchored at query start) for the
    # greeting branch of the skip gate. Domain-neutral VN + EN defaults
    # carried in shared/constants.DEFAULT_GREETING_PATTERNS. Empty list
    # disables the regex branch entirely (only token-count short-circuit
    # remains).
    "understand_greeting_patterns": {
        "type": "list_str",
        "default": tuple(DEFAULT_GREETING_PATTERNS),
    },
    # ── RAG-Anything Wave 3 mindsets (A4 — Tier 2) ─────────────────────
    # M2 — Neighbor window expansion. When True, retrieve graph adds
    # ±N adjacent chunks after MMR (broader context for LLM, no extra
    # LLM call). M22 token budget caps total payload. Default OFF
    # because expansion changes ``reranked_chunks`` shape; bot owners
    # opt in after validating their corpus is chunk-index-ordered.
    "neighbor_expand_enabled": {
        "type": "bool",
        "default": DEFAULT_NEIGHBOR_EXPAND_ENABLED,
    },
    "neighbor_window_size": {
        "type": "int",
        "default": DEFAULT_NEIGHBOR_WINDOW_SIZE,
        "min": 0,
        "max": 10,
    },
    "neighbor_token_budget": {
        "type": "int",
        "default": DEFAULT_NEIGHBOR_TOKEN_BUDGET,
        "min": 0,
        "max": 32000,
    },
    "neighbor_max_concurrency": {
        "type": "int",
        "default": DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
        "min": 1,
        "max": 16,
    },
    # M11 — Blocks API wrap. When True, retrieve+rerank stages wrap
    # chunks in the ``application.dto.block.Block`` dataclass.
    # Backward-compat via Block.__getitem__/get. Default OFF until
    # tests bake.
    "blocks_api_enabled": {
        "type": "bool",
        "default": DEFAULT_BLOCKS_API_ENABLED,
    },
    # M17 — Modality-aware rerank boost. When True, rerank node
    # applies multiplicative boost based on intent ↔ chunk_type
    # match (table_lookup × table = +20 %, code_lookup × code =
    # +30 %, ...). Default OFF.
    "modality_rerank_enabled": {
        "type": "bool",
        "default": DEFAULT_MODALITY_RERANK_ENABLED,
    },
    # M21 — Deterministic chunk UUID5. When True, ingest stamps each
    # chunk with UUID5 derived from (record_bot_id, document_id,
    # content) so re-ingest of same content is idempotent. Default
    # OFF preserves legacy uuid.uuid4() path.
    "chunk_hash_id_enabled": {
        "type": "bool",
        "default": DEFAULT_CHUNK_HASH_ID_ENABLED,
    },
    # Enhanced Contextual Retrieval storage. When True, ingest runs
    # ``ChunkContextEnricher`` (Haiku batch + prompt cache) for every
    # leaf chunk and persists the situated-context string in the
    # dedicated ``document_chunks.chunk_context`` column so the hybrid
    # retrieval path can BM25-boost over chunk_context independently of
    # the embedded text. Default OFF keeps the legacy inline-wrap CR
    # path active for backward compat; bot owners flip this on after
    # re-ingesting their corpus. STORAGE-ONLY — the column is consumed
    # by retrieval; the application never prepends ``chunk_context`` to
    # the LLM answer prompt (Quality Gate #10).
    "cr_enhanced_enabled": {"type": "bool", "default": False},
    # Cascade Routing per-bot opt-in. When True, the answer-LLM model
    # is chosen by query complexity score: simple → cheap tier,
    # ambiguous mid-band → bot default, multi-entity → premium tier.
    # Resolves via ``ModelResolverService.resolve_cascade_runtime`` so
    # the helper node never touches model-name strings directly. Default
    # OFF preserves the bot's current single-model behaviour until the
    # owner has validated tier swap on their corpus.
    "cascade_routing_enabled": {
        "type": "bool",
        "default": DEFAULT_CASCADE_ROUTING_ENABLED,
    },
    # Self-RAG critique tokens (Asai 2023). When True, the orchestrator
    # parses ``[Supported]`` / ``[Unsupported]`` markers from the LLM
    # answer (operator must wire the rule in ``bots.system_prompt`` —
    # see ``docs/sysprompt/self_rag_critique_template.md``).  Markers
    # are stripped from the user-visible text; when the unsupported
    # ratio meets/exceeds ``self_rag_critique_threshold`` the answer
    # is replaced by ``bots.oos_answer_template`` (Quality Gate #10).
    # Default OFF preserves byte-identical behaviour.
    "self_rag_critique_enabled": {
        "type": "bool",
        "default": DEFAULT_SELF_RAG_ENABLED,
    },
    "self_rag_critique_threshold": {
        "type": "float",
        "default": DEFAULT_SELF_RAG_THRESHOLD,
        "min": 0.0,
        "max": 1.0,
    },
    # HyDE (Hypothetical Document Embeddings, Gao et al. 2022). Per-bot
    # opt-in to draft a short LLM hypothetical answer + embed THAT in
    # place of the raw query so the retrieval vector lives closer to
    # declarative chunk text. Default OFF preserves the legacy raw-query
    # embed path; bot owners flip True after validating their corpus
    # benefits from declarative-style query rewriting. Failure modes
    # (LLM timeout, empty completion, transport error) degrade silent
    # to the raw query so the pipeline never blocks on HyDE.
    "hyde_enabled": {"type": "bool", "default": DEFAULT_HYDE_ENABLED},
    # Speculative Streaming Phase 2 (Wave K1) — per-bot opt-in to race a
    # cheap draft LLM against the main LLM. Default False; flipping True
    # before Phase 3 verifier ships means the bot owner accepts draft
    # fabrication risk on whichever turn the draft wins. ``draft_model``
    # is the litellm wire name (e.g. "openai/gpt-4.1-mini"); empty falls
    # back to the main model — which makes speculation a no-op race
    # between two identical calls (still useful as a tail-latency hedge).
    "speculative_streaming_enabled": {
        "type": "bool",
        "default": DEFAULT_SPECULATIVE_STREAMING_ENABLED,
    },
    "draft_model": {"type": "str", "default": ""},
}

# ── Defaults for dedicated bot columns ──────────────────────────────────
# Single source of truth — referenced by BotConfig DTO, ORM model, repo.
COLUMN_DEFAULTS: dict[str, Any] = {
    "max_documents": 5,
    "max_history": None,        # None = use system_config "chat_max_history"
    "prompt_max_tokens": None,
    "rerank_top_n": None,
}

# Keys that have dedicated bot columns (separate for easy SQL queries).
_COLUMN_KEYS = frozenset({"max_documents", "max_history", "prompt_max_tokens", "rerank_top_n"})


def resolve_bot_limit(
    bot_cfg: Any,
    key: str,
    system_default: Any = None,
) -> Any:
    """Resolve limit value: bot value WINS > system_config > schema default.

    Priority order (highest to lowest):
        1. ``bot_cfg.threshold_overrides[key]`` (Stream V Phase 2 — explicit tuning)
        2. ``bot_cfg.<key>`` (dedicated bot column for hot-path keys)
        3. ``bot_cfg.plan_limits[key]`` (JSONB per-bot)
        4. ``system_default`` (system_config row, caller passes in)
        5. ``PLAN_LIMIT_SCHEMA[key]["default"]``

    Numeric range guard (260525 Bug #6 fix): when ``PLAN_LIMIT_SCHEMA``
    declares ``min`` / ``max`` for a numeric key AND the resolved bot
    value falls OUTSIDE that range, the bot value is rejected (log
    warn + fall back to ``system_default`` → schema default). This
    replaces the prior ``max(bot, system)`` heuristic which prevented
    bot owners from overriding numeric defaults DOWNWARD even with a
    valid in-range value.

    @param bot_cfg: BotConfig DTO (or any object with matching attrs)
    @param key: config key to resolve
    @param system_default: value from system_config table (optional)
    @return: resolved value
    """
    # 1. Dedicated bot column
    col_val = None
    if hasattr(bot_cfg, key):
        col_val = getattr(bot_cfg, key, None)

    # 1b. Per-bot threshold_overrides JSONB (Stream V Phase 2)
    # Highest priority after dedicated columns — bot owner's explicit tuning.
    threshold_overrides: dict = getattr(bot_cfg, "threshold_overrides", None) or {}
    threshold_val = threshold_overrides.get(key)

    # 2. JSONB plan_limits
    plan_limits: dict = getattr(bot_cfg, "plan_limits", None) or {}
    json_val = plan_limits.get(key)

    # 3. system_config default (from caller)
    # 4. Schema default
    schema = PLAN_LIMIT_SCHEMA.get(key)
    fallback = system_default if system_default is not None else (schema["default"] if schema else None)

    # Priority: threshold_overrides > dedicated column > plan_limits
    bot_val = threshold_val if threshold_val is not None else (col_val if col_val is not None else json_val)

    # 260525 Bug #6 fix — schema-driven range guard for numeric keys.
    # Reject the bot value when it lies outside the documented ``min``
    # / ``max`` window. Defence vs operator typo ("rerank_top_n=1") is
    # now done in-place rather than by silently elevating to system.
    if bot_val is not None and isinstance(bot_val, (int, float)) and not isinstance(bot_val, bool):
        if schema:
            _min = schema.get("min")
            _max = schema.get("max")
            out_of_range = (
                (_min is not None and bot_val < _min)
                or (_max is not None and bot_val > _max)
            )
            if out_of_range:
                import structlog
                _log = structlog.get_logger(__name__)
                _log.warning(
                    "bot_limit_out_of_range_rejected",
                    key=key,
                    bot_val=bot_val,
                    schema_min=_min,
                    schema_max=_max,
                    fallback_to=fallback,
                )
                bot_val = None  # drop → fall through to fallback

    # Bot WINS when present (no more max() heuristic).
    if bot_val is not None:
        return bot_val
    if fallback is not None:
        return fallback

    return None


def resolve_semantic_cache_threshold(
    bot_cfg: Any,
    system_default: float | None = None,
) -> float:
    """Resolve the semantic cache cosine-similarity threshold.

    Resolve chain (per-bot WINS outright, no ``max()`` safety net):

        1. ``bot_cfg.threshold_overrides['semantic_cache_threshold']``
        2. ``bot_cfg.plan_limits['semantic_cache_threshold']``
        3. ``system_default`` (caller passes
           ``system_config.pipeline_cache_similarity_threshold``)
        4. ``PLAN_LIMIT_SCHEMA['semantic_cache_threshold']['default']``
           which mirrors ``DEFAULT_SEMANTIC_CACHE_THRESHOLD``.

    Why a dedicated resolver instead of ``resolve_bot_limit``: that helper
    applies ``max(bot, system)`` for numeric keys (defence vs accidental
    low-input). For this knob the WHOLE POINT is letting an operator A/B
    test a LOWER threshold (0.90, 0.85) per-bot, so per-bot must win
    outright. Clamp to [0.0, 1.0] is enforced by ``validate_plan_limits``
    at write-time, so we trust the stored value here.

    CLAUDE.md compliance:
    - Zero hardcode: schema default reads from ``DEFAULT_SEMANTIC_CACHE_THRESHOLD``.
    - Resolver MUST fallback system_config: caller passes
      ``system_default`` from ``system_config.pipeline_cache_similarity_threshold``.
    - HALLU sacred: caller (chat_worker) keeps the strict 0.97 system
      default; lowering is opt-in per-bot only.
    """
    schema_default = float(PLAN_LIMIT_SCHEMA["semantic_cache_threshold"]["default"])

    # 1. threshold_overrides JSONB (highest priority — explicit operator tuning)
    threshold_overrides: dict = getattr(bot_cfg, "threshold_overrides", None) or {}
    val = threshold_overrides.get("semantic_cache_threshold")
    if val is None:
        # 2. plan_limits JSONB
        plan_limits: dict = getattr(bot_cfg, "plan_limits", None) or {}
        val = plan_limits.get("semantic_cache_threshold")
    if val is not None:
        try:
            resolved = float(val)
            # A2 warn-only: a per-bot threshold BELOW the recommended minimum is
            # NOT clamped (an operator may deliberately A/B 0.90/0.85), but too
            # low a cosine threshold collides semantically-different questions
            # onto one cached answer — a wrong-answer / HALLU vector. Log it
            # loudly for review. The safe HARD floor is a per-model calibration
            # decision (needs score-distribution data), so it is intentionally
            # NOT hardcoded as a clamp here.
            if resolved < SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED:
                import structlog
                structlog.get_logger(__name__).warning(
                    "semantic_cache_threshold_below_recommended",
                    resolved=resolved,
                    recommended_min=SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED,
                    bot_id=getattr(bot_cfg, "bot_id", None),
                )
            return resolved
        except (TypeError, ValueError):
            # Malformed stored value — fall through to system_default.
            pass

    # 3. system_config caller-supplied default
    if system_default is not None:
        try:
            return float(system_default)
        except (TypeError, ValueError):
            pass

    # 4. Schema default (mirrors DEFAULT_SEMANTIC_CACHE_THRESHOLD)
    return schema_default


def validate_plan_limits(data: dict) -> dict:
    """Validate + sanitize plan_limits dict against schema.

    Unknown keys are dropped. Values are coerced/clamped to schema bounds.
    Raises ValueError for invalid enum options.

    @param data: raw plan_limits dict from API input
    @return: sanitized dict (only known keys, valid values)
    """
    if not isinstance(data, dict):
        raise ValueError("plan_limits must be a dict")

    result: dict[str, Any] = {}
    for key, value in data.items():
        schema = PLAN_LIMIT_SCHEMA.get(key)
        if schema is None:
            continue  # drop unknown keys

        expected_type = schema["type"]

        if expected_type == "int":
            try:
                value = int(value)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{key}: expected int, got {type(value).__name__}") from e
            if "min" in schema and value < schema["min"]:
                value = schema["min"]
            if "max" in schema and value > schema["max"]:
                value = schema["max"]

        elif expected_type == "float":
            try:
                value = float(value)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{key}: expected float, got {type(value).__name__}") from e
            if "min" in schema and value < schema["min"]:
                value = schema["min"]
            if "max" in schema and value > schema["max"]:
                value = schema["max"]

        elif expected_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key}: expected bool, got {type(value).__name__}")

        elif expected_type == "str":
            if not isinstance(value, str):
                raise ValueError(f"{key}: expected str, got {type(value).__name__}")
            if "options" in schema and value not in schema["options"]:
                raise ValueError(
                    f"{key}: must be one of {schema['options']}, got '{value}'",
                )

        elif expected_type == "list_str":
            # Accept list / tuple / set inputs; coerce to a deduplicated tuple
            # of stripped, lowercase non-empty strings so downstream
            # set-membership against canonical intent labels is stable.
            if not isinstance(value, (list, tuple, set, frozenset)):
                raise ValueError(
                    f"{key}: expected list[str], got {type(value).__name__}",
                )
            seen: set[str] = set()
            cleaned: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                token = item.strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                cleaned.append(token)
            value = tuple(cleaned)

        result[key] = value

    return result


def get_effective_config(
    bot_cfg: Any,
    system_defaults: dict[str, Any],
) -> dict[str, Any]:
    """Merge bot config + system defaults into final pipeline_config.

    Resolves all PLAN_LIMIT_SCHEMA keys plus column keys via the
    resolution chain: bot column > plan_limits > system_config > schema default.

    @param bot_cfg: BotConfig DTO
    @param system_defaults: dict of system_config values (key -> value)
    @return: merged pipeline config dict
    """
    config: dict[str, Any] = {}

    # Resolve all schema keys
    for key in PLAN_LIMIT_SCHEMA:
        config[key] = resolve_bot_limit(
            bot_cfg, key, system_default=system_defaults.get(key),
        )

    # Resolve column keys
    for key in _COLUMN_KEYS:
        config[key] = resolve_bot_limit(
            bot_cfg, key, system_default=system_defaults.get(key),
        )

    return config


__all__ = [
    "COLUMN_DEFAULTS",
    "PLAN_LIMIT_SCHEMA",
    "get_effective_config",
    "resolve_bot_limit",
    "resolve_semantic_cache_threshold",
    "validate_plan_limits",
]
