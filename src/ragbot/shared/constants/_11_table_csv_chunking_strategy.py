from __future__ import annotations
from typing import Final  # noqa: F401
from ._10_rbac import *  # noqa: F401,F403

# --- table_csv chunking strategy --------------------------------------------
DEFAULT_CSV_FORMAT_SAMPLE_LINES: Final[int] = 20
DEFAULT_CSV_FORMAT_COMMA_RATIO: Final[float] = 0.6
DEFAULT_CSV_FORMAT_SENTENCE_END_RATIO: Final[float] = 0.3
# Mixed-content guard (260525-4BUG Phase A — Bug #5). When the pure-CSV
# ratio criterion fails (because intro + footer prose dilutes the
# fraction), still fast-path to ``table_csv`` if the document contains
# ≥ this many CONSECUTIVE lines sharing the same comma count (i.e. a
# real embedded table). 5 is the smallest run that resists false-
# positive from accidental bullet lists; tables in production are
# almost always ≥ 10 rows so the threshold has comfortable headroom.
DEFAULT_CSV_FORMAT_TABLE_RUN_MIN_LINES: Final[int] = 5
# Hard cap per CSV row chunk; oversized row kept whole + warn log emitted.
DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS: Final[int] = 1500

# Which strategy to use when a document is detected as a CSV/column table.
# "table_csv" = row-as-chunk only (1 row → 1 chunk; precise lookup, but
# aggregation/"list-all" queries miss rows after top-k/rerank cap).
# "table_dual_index" = row chunks PLUS whole-table group chunk(s) so
# aggregation queries retrieve the full row set in one chunk. Platform
# default stays "table_csv" (behaviour-neutral); flip per-bot via
# ``plan_limits.chunking_config.table_strategy`` or globally via
# ``system_config.chunking_policy`` after re-ingest validation.
DEFAULT_TABLE_STRATEGY: Final[str] = "table_csv"
# Baseline strategy name recorded on the DocumentIngested event when the
# ingest pipeline has not yet resolved a more specific one (and the safe
# low-confidence fallback inside ``select_strategy``). Matches the
# ``_IngestCtx.chunking_strategy`` field default — single source of truth
# for "unknown/baseline strategy" so no caller inlines the literal.
DEFAULT_INGEST_STRATEGY_NAME: Final[str] = "recursive"
# Allowed table strategies — used by the policy resolver to reject typos.
ALLOWED_TABLE_STRATEGIES: Final[frozenset[str]] = frozenset({
    "table_csv", "table_dual_index",
})
# Whole-table group chunk char cap (table_dual_index). A table whose full
# (header + all rows) text fits under this becomes ONE group chunk; larger
# tables are packed into consecutive multi-row group chunks each ≤ this cap
# (header prepended) so aggregation coverage survives without a single
# embedding-token-budget-busting chunk.
DEFAULT_TABLE_DUAL_GROUP_MAX_CHARS: Final[int] = 4000

# format→markdown normalizer (Phase C). When ON, the parser's joined output
# is normalised to clean markdown BEFORE chunking: raw CSV regions become
# markdown pipe tables (header↔column association preserved, so retrieval
# embeds a coherent table instead of comma soup) and plain-text VN
# Chương/Mục/Điều markers are promoted to ATX headings. Default OFF — flip
# ``system_config.markdown_normalize_enabled`` after re-ingest validation.
DEFAULT_MARKDOWN_NORMALIZE_ENABLED: Final[bool] = False

# Mixed-CSV emit header + footer chunks (260521-CHUNK-AGGREGATION-UNIVERSAL).
# Mixed doc = intro paragraph + CSV table + trailing notes (promo, warranty).
# Legacy ``_chunk_table_csv`` only emits 1 chunk per data row, dropping the
# pre-/post-table prose. With this flag ON, additional synthetic chunks are
# emitted so retrieval can still surface "what is this table about" + any
# trailing note. Default OFF — flip per-bot via plan_limits or globally via
# system_config after re-ingest validation (Phase 5).
DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED: Final[bool] = False
# Number of leading data rows included in the synthetic header chunk
# (preview of column structure + sample values). 3 strikes a balance
# between informative preview and chunk-size budget.
DEFAULT_TABLE_CSV_HEADER_CHUNK_SAMPLE_ROWS: Final[int] = 3
# Number of trailing data rows included in the synthetic footer chunk.
DEFAULT_TABLE_CSV_FOOTER_CHUNK_SAMPLE_ROWS: Final[int] = 3
# Minimum line length in the pre-/post-table region to qualify as
# "non-trivial" content worth emitting a header/footer chunk for.
# Filters out empty separators and stray punctuation lines.
DEFAULT_TABLE_CSV_PRE_MIN_CHARS: Final[int] = 30
DEFAULT_TABLE_CSV_POST_MIN_CHARS: Final[int] = 30
# 260525 Bug #9-followup — a "real" CSV-shape line must carry at least
# this many non-empty cells when split by commas. Defence vs prose
# lines that end with stray trailing commas (e.g. spreadsheet export
# artifacts: "- bullet sentence,,," counts 3 commas but only 1 cell).
# 2 is the minimum to qualify as a key/value pair; real tables have ≥3
# columns so this floor has comfortable headroom.
DEFAULT_TABLE_CSV_MIN_NON_EMPTY_CELLS: Final[int] = 2

# Persist contextual prefix in `content` so BM25 + rerank both see enrichment.
DEFAULT_ENRICHED_PREFIX_PERSIST: Final[bool] = True

# Test-only Google Sheets bounds — production ingest stays user-paste-tab-only.
DEFAULT_GOOGLE_SHEETS_TEST_MAX_TABS: Final[int] = 50
DEFAULT_GOOGLE_SHEETS_TEST_TIMEOUT_S: Final[int] = 10
# Minimum stripped char length for a fetched Google Doc body to count as real
# content (zero-hardcode lift 2026-06-13 of the inline ``> 10`` guard). Below
# this the fetch returned an empty/placeholder doc — reject rather than ingest
# a near-empty document.
DEFAULT_GOOGLE_DOC_MIN_CONTENT_CHARS: Final[int] = 10

# --- Contextual Retrieval (Anthropic 2024-09) -------------------------------
# Default OFF (2026-06-17): per-chunk nano CR nhồi full-doc per chunk = O(n^2)
# token storm. Jina late_chunking giờ cấp ngữ cảnh cross-chunk ngay trong lần
# embed (0 LLM), nên CR opt-in. Safe-by-default: cold-start trước khi
# system_config load KHÔNG được tái-storm. Xem alembic 0228.
DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED: Final[bool] = False
DEFAULT_CR_CONTEXT_MAX_TOKENS: Final[int] = 100
DEFAULT_CR_PROMPT_CACHE_ENABLED: Final[bool] = True
# Final safety cap — only TRULY pathological docs skip CR wholesale. With
# the local-context WINDOW below, CR cost is O(n_chunks × window) regardless
# of document size, so this cap is no longer a cost lever (it was, before
# windowing, the band-aid that DROPPED enrichment quality on large customer
# corpora). Kept high purely as a runaway guard.
DEFAULT_CR_MAX_DOC_CHARS: Final[int] = 5_000_000

# Local-context WINDOW for Contextual Retrieval — OPT-IN, default OFF.
# 2026-06-13 research verdict (Anthropic CR blog + arxiv 2503.17952 SLIDE +
# arxiv 2604.01733 + arxiv 2604.15802 CHOP): there is NO published ablation
# proving a local window matches whole-document context for LLM chunk
# contextualization, and local windows demonstrably FAIL on cross-section
# coreference — exactly the Vietnamese legal "điều khoản chung" pattern where
# a definition in Điều 1 is referenced in Điều 50. The paper-backed cost fix
# is PROMPT CACHING the whole-doc prefix (90% off, quality preserved), NOT
# windowing (which also DEFEATS caching by changing the prefix per chunk).
# Kept as an opt-in lever (flip ``cr_context_window_chars`` > 0 per A/B) for
# very large PROSE docs where caching is unavailable; 0 = whole doc (default).
DEFAULT_CR_CONTEXT_WINDOW_CHARS: Final[int] = 0

# LEGAL-RETRIEVAL-FIX Phase 3 2026-05-20: WA-3 "enhanced CR" path
# (chunk_context column + structural-anchor prefix in ``content``). Default
# ON so legal / regulatory / structured corpora get the structural context
# without bot owner action. Per-bot disable via ``plan_limits.cr_enhanced_enabled
# = false`` for cost-sensitive tenants. Each ingest call costs ~1 LLM call /
# chunk (= ~$0.0003 / chunk on gpt-4.1-mini) — for an 80-chunk document
# ≈ $0.024. Document this cost trade-off in admin onboarding.
# Default OFF (2026-06-17): this WA-3 "enhanced CR" is a SECOND per-chunk nano
# path (independent of CONTEXTUAL_RETRIEVAL_ENABLED above) — it was the real
# legal-doc storm blocker. Jina late_chunking supersedes it. Opt-in only. See
# alembic 0231.
DEFAULT_CR_ENHANCED_ENABLED: Final[bool] = False

# Hard timeout on ONE Contextual-Retrieval LLM call. Audit 2026-06-13 (CRIT):
# ``enrich_chunk_with_context`` awaited ``acompletion`` with NO timeout while a
# bounded semaphore (20) gates the fan-out — so a single hung provider call
# never releases its slot, blocking the other 19 and stalling the whole
# document's ingest (the embedder already wraps its call in 90s; CR did not).
# On timeout the call falls back to the un-enriched chunk (CR is a recall
# booster, not a correctness gate — graceful degradation per CLAUDE.md).
DEFAULT_CR_LLM_TIMEOUT_S: Final[int] = 120

# CR prompt-cache warm-up (2026-06-13). The full-document CR prefix is identical
# across every chunk, so OpenAI (gpt-4.1-mini) and Anthropic auto-cache it — but
# a concurrent ``Semaphore``-wide burst races before the first response seeds the
# cache, so the opening wave pays full price (measured: early-burst calls cache
# 26-54% vs 97%+ once warm). Seeding ONE enrich sequentially before the fan-out
# lifts the per-doc cache ratio from ~75% → ~95%. Only worth the +1 sequential
# round-trip when the doc has enough chunks to form a real burst; tiny docs skip.
DEFAULT_CR_CACHE_WARM_MIN_CHUNKS: Final[int] = 8

# CR / per-chunk-enrich ROW GATE (2026-06-13). Tabular corpora chunked by
# ``table_csv`` / ``table_dual_index`` emit one chunk per data row, and each
# row already carries its column header + key:value structure (e.g.
# "STT,Tên dịch vụ,Giá\n10,Item A,1.234.000"). The Anthropic
# Contextual-Retrieval lift — and the legacy inline-enrich lift — is designed
# for PROSE chunks that lose surrounding context; it is ~0 on self-describing
# rows. Yet those rows dominate chunk count (a 225K-char sheet → hundreds of
# rows, each one a per-chunk LLM call), so enriching them is the dominant
# ingest-latency / cost driver. Skipping per-chunk enrichment for these
# strategies cuts ingest LLM calls ~80-90% with no measurable retrieval loss
# (rows stay fully searchable via their header + BM25). Default ON; flip
# ``system_config.enrich_row_gate_enabled = false`` to roll back instantly
# (no redeploy) if an A/B shows recall regression.
DEFAULT_ENRICH_ROW_GATE_ENABLED: Final[bool] = True
# Strategies whose row chunks are self-describing → skip per-chunk enrichment.
CR_ROW_GATED_STRATEGIES: Final[frozenset[str]] = ALLOWED_TABLE_STRATEGIES

# WA-3 Enhanced CR storage path — chunk_context column lives alongside the
# chunk text so the hybrid retrieval path can BM25-boost over the context
# string without re-deriving it on every query. Token budget cap is enforced
# at the application boundary (ChunkContextEnricher) so a model that ignores
# the prompt ``max_tokens`` field still cannot blow up the VARCHAR(1024)
# storage column shipped in alembic 010l.
DEFAULT_CHUNK_CONTEXT_MAX_TOKENS: Final[int] = 100

# Bounded concurrency for LLMChunkContextProvider.generate(): max simultaneous
# LLM calls per batch when enriching chunks. Raised 3→8 (2026-06-13) now that
# the per-call payload is a bounded local window (DEFAULT_CR_CONTEXT_WINDOW_CHARS)
# instead of the whole document — smaller calls tolerate higher fan-out within
# the same provider rate budget, cutting wall-clock ingest latency ~2.5×.
DEFAULT_CHUNK_CONTEXT_ENRICHMENT_CONCURRENCY: Final[int] = 8

# --- CleanBase ingest quality scoring (observability-only) ------------------
# Heuristic 0.0-1.0 score per chunk emitted post-enrichment. Score is logged +
# persisted to ``metadata_json.quality_score`` but NEVER rejects a chunk —
# ingest stays a fixed pipeline; quality is a downstream signal for retrieval
# tuning + admin dashboards.
DEFAULT_CLEANBASE_QUALITY_THRESHOLD: Final[float] = 0.6
DEFAULT_CLEANBASE_MIN_WORDS: Final[int] = 20
DEFAULT_CLEANBASE_MAX_WORDS: Final[int] = 400

# --- Lost-in-the-middle reorder (Liu et al., 2023) --------------------------
DEFAULT_LITM_REORDER_ENABLED: Final[bool] = True

# --- Generic Vocabulary Expander (domain-neutral) ---------------------------
DEFAULT_GENERIC_VOCAB_ENABLED: Final[bool] = True
DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY: Final[int] = 10
DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH: Final[int] = 5

# n-gram phase for VN compound recall — fixes single-token loss of compound
# semantics (e.g. "trẻ hóa" vs "trẻ con/em" are different concepts but both
# share the unigram "trẻ"). Bigram + trigram match against the same merged
# vocab dict (generic + per-bot custom). 4-gram doesn't lift recall and
# slows the match loop quadratically — capped at 3.
DEFAULT_VOCAB_NGRAM_MAX_N: Final[int] = 3
# Look-back window for the negation guard (in TOKENS, left of an n-gram).
# 2 tokens covers patterns like "không bán retail" / "chưa từng dùng X".
DEFAULT_VOCAB_NEGATION_LOOKBACK_TOKENS: Final[int] = 2
# VN negation tokens — frozenset for O(1) membership + immutability.
# Domain-neutral: pure language particles, NOT brand / industry vocabulary.
DEFAULT_VN_NEGATION_TOKENS: Final[frozenset[str]] = frozenset({
    "không", "ko", "k", "kh", "hk",
    "chưa", "chẳng", "chả",
    "đừng", "đâu", "đếch",
})

# VN honorifics — addressee/self-reference particles bot owners can branch on
# inside their own system_prompt for tone selection. Emit-only signal: the
# platform NEVER injects honorifics into the LLM prompt or overrides answers
# (Quality Gate #10). Domain-neutral — pure language particles.
VN_HONORIFIC_LABELS: Final[frozenset[str]] = frozenset({
    "anh", "chị", "em", "cô", "chú", "bác", "mình",
})

# --- Multi-query expansion --------------------------------------------------
DEFAULT_MULTI_QUERY_ENABLED: Final[bool] = True
# Paraphrases broaden the recall net for Vietnamese morphological
# variants (case, dialect, synonym), feeding RRF more candidates before
# rerank.
# Wave M3 2026-05-20: lowered 5→3 after observed multi_query_fanout
# 2168-4710ms p95 dominating retrieve step. 3 variants retain ~85%
# of recall lift vs 5 (per Anthropic MQ paper). Per-bot override via
# pipeline_config.multi_query_n_variants for bots needing deeper recall.
DEFAULT_MULTI_QUERY_N_VARIANTS: Final[int] = 3
# Headroom so a per-bot pipeline_config override can push beyond the default
# without hitting the safety cap.
DEFAULT_MULTI_QUERY_MAX_VARIANTS: Final[int] = 7
DEFAULT_MULTI_QUERY_TIMEOUT_S: Final[int] = 5
# "auto" → resolve_runtime picks the model bound to purpose=multi_query.
# DEPRECATED 2026-05-14 AdapChunk-reorg: MQ model explicit "haiku" prevents
# auto-resolve spike to Sonnet/Opus on cost-sensitive paths. Per Phần 21.3 W5.
# DEFAULT_MULTI_QUERY_MODEL: Final[str] = "auto"
DEFAULT_MULTI_QUERY_MODEL: Final[str] = "haiku"
# include_original=True emits user's verbatim query as variant 0 — guards
# against stochastic rewriters dropping critical signal terms in all paraphrases.
DEFAULT_MULTI_QUERY_REWRITE_COUNT: Final[int] = 3
DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL: Final[bool] = True
# Skip MQ expansion for short / chitchat queries — paraphrasing a 3-token
# greeting wastes an LLM call and adds latency without recall benefit.
DEFAULT_MULTI_QUERY_MIN_TOKENS: Final[int] = 5
DEFAULT_MULTI_QUERY_SKIP_CHITCHAT_INTENT: Final[bool] = True
# Adaptive-RAG auto-mode gate (Jeong et al. 2024 — route by query complexity):
# multi-query paraphrase fanout fires ONLY when the complexity classifier
# scores the query >= this floor. Trivial single-fact queries (score 0) skip
# the LLM fanout → faster + cheaper; anything carrying a complexity signal
# (commas, conjunctions, multi-numeral, list/aggregation cues) still expands.
# 0.0 = gate DISABLED (current behaviour, zero regression) — flip to a
# calibrated value (load-test first; the "complex" label boundary is
# DEFAULT_QUERY_COMPLEXITY_THRESHOLD=1.2) per-bot via
# pipeline_config.multi_query_complexity_min. Honest default: OFF until measured.
DEFAULT_MULTI_QUERY_COMPLEXITY_MIN: Final[float] = 0.0
# dedup: cosine-similarity threshold above which two MQ variants
# are treated as redundant (one is dropped). 0.95 keeps near-duplicate
# rewrites from doubling fan-out cost without recall benefit.
DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD: Final[float] = 0.95
# entity gate: when entity extractor returns nothing the
# paraphrase fan-out rarely produces a matching BM25 token, so multi-query
# is skipped to save cost. Confidence proxy = "entity returned" boolean
# (no per-entity score in the Port contract); when an extractor later
# emits per-entity scores, this constant becomes the cut-off threshold.
DEFAULT_MQ_ENTITY_CONFIDENCE_GATE: Final[float] = 0.6

# Concurrency-only optimisations preserve LLM-output bytes; flag-on after
# VJ load test validated 0 regression. Per-bot pipeline_config still overrides.
DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED: Final[bool] = True
DEFAULT_PIPELINE_PARALLEL_CACHE_UNDERSTAND_ENABLED: Final[bool] = True
# Split the LLM grounding judge into a sibling task to the regex output
# guards and dispatch via asyncio.gather. Both branches write only
# guardrail_flags (list), merged additively — no state-clobber risk.
DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED: Final[bool] = True
# Pre-batch the embedding call for all multi-query variants so retrieve fan-out
# pays one HTTP round-trip instead of N. Cheap optimisation — kept default ON
# because the caller already runs N parallel hybrid_search calls anyway.
DEFAULT_PIPELINE_MULTI_QUERY_EMBED_BATCH_ENABLED: Final[bool] = True
# Parallelize query_complexity (heuristic), router_select_model (DB), and
# semantic_cache_preflight (lightweight) in a single asyncio.gather after
# understand_query. Saves ~200ms p95 when all three run sequentially.
# Each branch degrades independently on exception (return_exceptions=True).
DEFAULT_PIPELINE_PRE_RETRIEVAL_PARALLEL_ENABLED: Final[bool] = True

# Phase-B B4 — batch request_steps INSERT at end-of-turn (single executemany)
# instead of one INSERT+commit per step (~27 calls per turn). Default OFF; flip
# system_config.batch_step_logging_enabled to "true" once verified in load test.
# When ON, StepTracker buffers rows in-memory and flushes once via
# RequestLogRepository.add_steps_batch(). If the buffer flush itself fails the
# request body has already been returned to the user — degraded observability
# (logged) is preferred over user-visible failure.
DEFAULT_BATCH_STEP_LOGGING_ENABLED: Final[bool] = False

# --- Entity-grounded query expansion ----------------------------------------
# Default OFF (`null`) — per-bot opt-in via pipeline_config.entity_extractor_provider.
DEFAULT_ENTITY_EXTRACTOR_PROVIDER: Final[str] = "null"
DEFAULT_ENTITY_GROUNDING_ENABLED: Final[bool] = False
DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES: Final[int] = 3

# --- Retrieve-fallback-with-original ----------------------------------------
# Rescue retry with verbatim query when multi-query fanout returns 0 chunks.
DEFAULT_RETRIEVE_FALLBACK_ENABLED: Final[bool] = True
DEFAULT_RETRIEVE_FALLBACK_TOP_K: Final[int] = 5

