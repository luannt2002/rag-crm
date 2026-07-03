"""GraphState — TypedDict threaded through the LangGraph pipeline."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID


class GraphState(TypedDict, total=False):
    """Mutable state shared across all nodes of the query graph.

    `total=False` lets nodes return partial updates; LangGraph merges them.
    """

    record_tenant_id: UUID | None
    request_id: UUID
    message_id: int
    conversation_id: UUID | None
    record_bot_id: UUID
    channel_type: str | None
    # Tenant-scoped workspace slug propagated from the resolved bot
    # config. Persistence node uses it to satisfy NOT NULL CHECK on
    # data tables (semantic_cache, request_steps, ...).
    workspace_id: str

    query: str
    rewritten_query: str | None
    # Sub-queries from decompose node when multi-hop is split into atomic parts.
    sub_queries: list[str]

    # Adaptive Router Layer 1 classifier output. ``complexity_label`` is
    # "simple" or "complex"; ``complexity_score`` is the raw signal sum.
    # Populated by the ``query_complexity`` node; consumed by
    # ``_complexity_route`` to gate the L3 decomposer.
    complexity_label: str
    complexity_score: float
    retrieved_chunks: list[dict]
    reranked_chunks: list[dict]
    graded_chunks: list[dict]
    answer: str
    citations: list[dict]
    guardrail_flags: list[dict]
    tokens: dict
    cost_usd: float
    model_used: str
    intent: str
    # LLM-reported intent classification confidence in [0, 1].
    # Populated by understand_query when the structured-output schema
    # carries a ``confidence`` field; defaults to
    # DEFAULT_INTENT_CONFIDENCE_FALLBACK in any other path. Consumed by
    # _router_route (decompose gate) + Prometheus histogram observation.
    intent_confidence: float
    conversation_history: list[dict]
    original_query: str | None
    retrieval_adequate: bool
    grade_retries: int
    reflect_retries: int

    # Permission filtering — groups the requesting user belongs to.
    user_groups: list[str]

    # GraphRAG — knowledge graph context retrieved via entity-relation traversal.
    graph_context: list[dict]

    # Pipeline config from system_config (Redis-cached DB), per-bot resolved.
    pipeline_config: dict

    # System prompt used during generation — stored so guard_output can hash it.
    system_prompt: str

    # Bot language tag, drives prompt selection.
    language: str

    # Response contract — answered | greeting | out_of_scope | no_context | blocked | error | cache_hit.
    answer_type: str
    answer_reason: str

    # When set, generate streams real LLM tokens onto this asyncio.Queue.
    # None payload = end-of-stream sentinel.
    _stream_sink: object

    # Wave H Phase 1 — TTFT (ms) captured at the LLM first non-empty
    # delta. ``_invoke_llm_node`` writes this on the streaming branch
    # only; ``generate`` then mirrors it onto
    # ``request_steps.metadata_json.first_token_ms`` via
    # ``step_tracker.step('generate').set_metadata(...)``. Absent for
    # structured-output / non-streaming / refuse-short-circuit paths.
    _stream_first_token_ms: int

    # SHA-256 of (system_prompt | oos_template) reused across cache check + write.
    _bot_cache_version: str

    # Pre-resolved OOS / refuse template from the 7-tier chain
    # (OosTemplateResolver, Phase 1). Entry points
    # (test_chat / chat_stream / chat_worker) walk the chain ONCE per
    # request and stash the result here so the orchestration helper
    # ``_resolved_oos_template(state)`` stays sync — no event-loop
    # juggling at every OOS short-circuit. Empty string means every
    # tier (bot column → plan_limits → workspace_config → tenants →
    # system_config → language_packs → constants) was empty.
    oos_answer_template_resolved: str

    # Application Context Base from SuperlativeContextEnricher / vocabulary.
    # Generate node reads this; never sets state["answer"] (LLM owns the answer).
    context_base: dict

    # Test-mode bypass flag — only /test/chat sets it; production never injects.
    bypass_cache: bool

    # check_cache outcome: "bypassed" | "hit" | None on miss.
    cache_status: str | None

    # Set by retrieve when primary path returned 0 chunks and rescue retry recovered.
    retrieve_mode: str

    # S2 perf gate: True when retrieve skipped the multi_query paraphrase
    # fanout because decompose did not produce ≥2 sub-queries. Surfaces the
    # bypass for downstream observability (tests, metrics, traces).
    fanout_bypassed: bool

    # ``{prompt_key: content}`` snapshot loaded by guard_input from
    # LanguagePackService. Downstream nodes resolve via ``_lang(state)``
    # which prefers this dict over the legacy in-memory ``i18n`` pack.
    _language_pack_rows: dict

    # Per-request StepTracker; nodes call ``state["step_tracker"].step("…")``.
    # Carried on state (not closed over by build_graph) so the compiled
    # graph instance is safe to cache across requests/tenants. Typed ``Any``
    # to keep this module port-free — orchestration imports the concrete
    # ``StepTracker`` at use site, but state.py must not depend on it.
    step_tracker: Any

    # Bot owner's system prompt for THIS request. Single source of truth
    # for the LLM generation node + cache-version hash. Carried on state
    # so a cached compiled graph cannot leak prompt across tenants.
    bot_system_prompt: str

    # Optional knowledge-graph service handle for the graph_retrieve
    # node; ``None`` disables GraphRAG fan-out. Per-request because it
    # closes over the request-scoped DB session factory.
    kg_service: Any

    # Optional async DB session factory for nodes that need direct SQL
    # (parent-child expansion, GraphRAG). ``None`` disables those paths.
    session_factory: Any

    # ─── Parallel-path markers (sprint-1-G6) ───
    # Set by ``cache_check_and_understand_parallel`` when it has already
    # computed the rewritten + intent; signals the downstream
    # ``understand_query`` node to short-circuit so the LLM round-trip
    # never fires twice per turn. MUST be declared here — LangGraph's
    # reducer drops keys absent from the TypedDict schema during state
    # merge, which silently re-enables the duplicate call.
    # ``force_re_understand`` is the CRAG-retry escape hatch: an upstream
    # node sets it to bypass the gate after a rewrite_retry needs a fresh
    # intent pass.
    _understand_skipped_by_parallel: bool
    force_re_understand: bool

    # ─── Multi-query pre-computed paraphrase hand-off ───────────────────────
    # Set by ``rewrite_and_mq_parallel`` when multi-query LLM expansion
    # ran concurrently with rewrite. The ``retrieve`` node reads this to
    # skip the inline LLM paraphrase step (work already paid for).
    # MUST be declared here — LangGraph's reducer drops keys absent from
    # the TypedDict schema during state merge, which would silently
    # fall back to the single-query path even when paraphrases are ready.
    _mq_queries: list[str]

    # Speculative sibling of ``_mq_queries``: the router's speculative
    # multi-query wrapper races the paraphrase LLM concurrently with the
    # rewrite branch and stashes its variants here; ``retrieve`` prefers
    # the committed ``_mq_queries`` and falls back to this slot. Same
    # hand-off shape (paraphrase strings) → same annotation / reducer.
    # MUST be declared here — an un-declared key is dropped by LangGraph's
    # reducer across node hops, silently discarding the already-paid-for
    # speculative paraphrases.
    _mq_speculative_variants: list[str]

    # ─── Heuristic Layer-1 intent classify markers ───
    # ``intent_source`` identifies which path produced the intent label:
    #   "heuristic" — regex fast-path (Layer 1); no LLM call fired.
    #   "llm"       — standard LLM understand_query path (Layer 2).
    #   "cache"     — Redis understand_query cache hit (Layer 0).
    # Absent on error paths. Downstream nodes MUST NOT branch on this field
    # for behaviour changes — it is an observability tag only.
    intent_source: str

    # ─── Speculative-retrieve hand-off slots ───
    # Populated by the multi-query speculative wrapper when it races a
    # warm embed + initial retrieve concurrently with the rewrite branch.
    # ``retrieve`` reads these to decide whether to keep the speculative
    # hit or fall back to the cold path. MUST be declared here — LangGraph
    # reducer drops un-declared keys during state merge, which would
    # silently disable the speculative speedup.
    _speculative_raw_embed: Any
    _speculative_chunks: Any
    _speculative_hit: bool

    # ─── Stats-index routing (B3 Self-Query Retrieval) ───────────────────────
    # Set by the retrieve node when intent is aggregation/comparison AND a
    # price-range filter is parsed from the query. Each entry is a row dict
    # from document_service_index (entity_name, price_primary, etc.).
    # Absent on the normal vector-retrieve path (None / missing key).
    stats_entities: list[dict]

    # ─── Cross-node keys — written in one node / the initial input and read in
    # another. They MUST be declared here or langgraph's reducer drops them, silently killing
    # the feature. Declaring restores the hand-off. Each is written in one node /
    # the initial input and read in another. Guarded by
    # tests/unit/test_audit_pass2_repro.py::TestS1StateKeyDrop + the AST pin test.
    bot_extra_output_tokens_per_response: int  # input → generate (paid output cap)
    bot_created_at: Any                        # input → xml-wrap date-default resolver
    raw_user_message: str                      # input → generate (slot extraction)
    rerank_score_mode: str                     # rerank → grade (absolute vs relative floor)
    _total_graph_iterations: int               # grade → reflect routing (loop cap)
    crag_skip_retry: bool                       # grade → grade routing (fast-path)
    _corpus_version: str                        # check_cache → persist (cache-key memo)
    embedding_column: str                       # cache/embed → semantic-cache preflight
    retrieval_degraded: bool                    # retrieve → answer path (HALLU-safety flag)
    embed_degraded: bool                        # embed → answer path (HALLU-safety flag)
    # node-return observability keys (surfaced to final state / persist)
    cache_hit: bool                             # check_cache → routing / final state
    chunks_used: int                            # generate → persist (answer provenance)
    crag_skip_reason: str                       # grade → observability
    grade_timeout_fallback: bool                # grade → observability


__all__ = ["GraphState"]
