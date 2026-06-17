from __future__ import annotations
from typing import Final  # noqa: F401
from ._09_message_feedback_thumbs_verd import *  # noqa: F401,F403

# --- RBAC -------------------------------------------------------------------
DEFAULT_RBAC_CACHE_TTL_S: int = 300

# Mirror of ROLE_LEVELS in shared/rbac.py — lift literals out of hot-path code.
DEFAULT_SUPER_ADMIN_LEVEL: Final[int] = 100
DEFAULT_TENANT_ADMIN_LEVEL: Final[int] = 80
DEFAULT_ADMIN_LEVEL: Final[int] = 60
DEFAULT_SERVICE_LEVEL: Final[int] = 50

# --- single-flight (cache stampede prevention) -------------------------
# Soft upper bound on the per-key ``asyncio.Lock`` registry held by an
# ``AsyncSingleFlight`` instance. The dict grows under sustained miss bursts
# and an LRU sweep drops the oldest *unlocked* entries once the size crosses
# this cap. Keep generous: a multi-tenant worker with ~hundreds of bots and
# tenants stays well under, but a hostile-key probe must not OOM the worker.
DEFAULT_SINGLE_FLIGHT_MAX_LOCKS: Final[int] = 10_000

# Default UPSERT (False) — wipe mode requires super-admin and is opt-in.
DEFAULT_SYNC_DOCUMENTS_WIPE_MODE: Final[bool] = False

# --- Body-size limits -------------------------------------------------------
DEFAULT_MAX_BODY_CHAT_BYTES: Final[int] = 262144
DEFAULT_MAX_BODY_DEFAULT_BYTES: Final[int] = 10_485_760  # 10 MB
DEFAULT_MAX_BODY_INGEST_BYTES: Final[int] = 16777216

# Stream MAXLEN cap — ~200MB ceiling per stream at 2KB/event.
DEFAULT_STREAM_MAXLEN: Final[int] = 100_000

# --- LLM async queue (chat_async_worker.py) ---------------------------------
# Stream where API enqueues chat jobs; worker XREADGROUP-consumes.
CHAT_ASYNC_STREAM: Final[str] = "chat.requested"
# Consumer group name — single group across all worker processes so each
# message is delivered to exactly one worker (load-balanced).
CHAT_ASYNC_CONSUMER_GROUP: Final[str] = "chat-workers"
# Result hash key prefix; full key = f"{prefix}{job_id}".
CHAT_ASYNC_RESULT_KEY_PREFIX: Final[str] = "chat:result:"
# Result TTL — caller has this long to poll before result is GC'd.
DEFAULT_CHAT_ASYNC_RESULT_TTL_S: Final[int] = 600
# XREADGROUP block timeout (ms) — bounds shutdown latency on SIGTERM.
DEFAULT_CHAT_ASYNC_BLOCK_MS: Final[int] = 5_000
# XREADGROUP batch size per poll.
DEFAULT_CHAT_ASYNC_BATCH_COUNT: Final[int] = 1
# Result hash ``error`` field truncation cap — keep below 1KB so a long
# stack-trace string never balloons the hash payload.
DEFAULT_CHAT_ASYNC_ERROR_MAX_CHARS: Final[int] = 300

# LLM router fallback per-provider concurrency (overridden by ai_providers row).
DEFAULT_PROVIDER_MAX_CONCURRENT: Final[int] = 16

# Separate, smaller concurrency lane for background (post-response) LLM calls so
# they can NEVER starve foreground request-path calls. Root-cause 2026-06-13:
# the async grounding judge (fire-and-forget after the answer ships) shares the
# foreground provider semaphore; under burst a backlog of grounding calls
# saturated all DEFAULT_PROVIDER_MAX_CONCURRENT slots and the next turn's
# ``generate`` queued behind them → p95 24-37s while the steady-state was 3-5s.
# Background purposes (see DEFAULT_BACKGROUND_LLM_PURPOSES) run on this isolated
# lane; foreground keeps the full provider lane untouched.
DEFAULT_PROVIDER_BACKGROUND_MAX_CONCURRENT: Final[int] = 4

# ai_providers ORM defaults — milliseconds (DB columns timeout_ms / connect_timeout_ms).
DEFAULT_PROVIDER_TIMEOUT_MS: Final[int] = 30_000
DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS: Final[int] = 5_000

# --- Prompt-injection patterns (ingest-time, conservative) ------------------
PROMPT_INJECTION_PATTERNS: Final[tuple[str, ...]] = (
    r"(?i)ignore\s+((all|previous|the\s+above|prior)\s+)+(instruction|prompt|direction|rule)s?",
    r"(?i)disregard\s+((all|previous|prior)\s+)+(instruction|prompt)s?",
    r"(?i)you\s+are\s+now\s+(dan|stan|an?\s+unrestricted|a\s+different)",
    r"(?i)forget\s+(your|the|all)\s+(instruction|previous)",
    r"(?i)system\s*(prompt|message)\s*:\s*",
    r"(?i)reveal\s+(your|the|all)\s+(system\s*prompt|instructions?)",
    r"(?i)bỏ\s+qua\s+(tất\s*cả\s*)?(các\s+)?(hướng\s*dẫn|chỉ\s*dẫn|yêu\s*cầu|quy\s*tắc)",
    r"(?i)quên\s+đi\s+(tất\s*cả\s+)?(các\s+)?(hướng\s*dẫn|chỉ\s*dẫn)",
    r"(?i)tiết\s*lộ\s+(system\s*prompt|hướng\s*dẫn\s*hệ\s*thống)",
    r"(?i)\[\[SYSTEM\]\]|\[\[USER\]\]|<\|im_start\|>|<\|im_end\|>",
)

# Hard cap on bot system_prompt length, enforced by CreateBot/UpdateBot command
# DTOs (Pydantic max_length). 5000 chars ≈ 2500 tokens (Vietnamese ~2 char/tok),
# safely below the ~3000-token reasoning-degradation threshold (RAG-prompt
# research 2024-25: long personas dilute every rule + cause over-refusal +
# lost-in-the-middle). Was 20000 — that let test-spa bloat to 17K. Move
# pricing/facts/domain data to corpus docs, not the persona.
MAX_SYSTEM_PROMPT_CHARS: Final[int] = 5_000

# --- CRAG grader defaults ---------------------------------------------------
DEFAULT_CRAG_MIN_RELEVANT_COUNT: Final[int] = 1
DEFAULT_CRAG_MIN_RELEVANT_FRACTION: Final[float] = 0.0
DEFAULT_CRAG_MIN_FALLBACK_SCORE: Final[float] = 0.3

# Per-intent CRAG fallback score gate; missing key falls back to global default.
# Promotional / pricing-bearing topical keys ("promo", "sale", "voucher")
# are seeded here so the runtime gate is forward-compatible: bot owners
# can route these via vocabulary expansion or future intent-classifier
# extensions and the strict floor (0.40) is already in place. Until the
# classifier emits these labels, the keys remain dormant — no behavioural
# change vs the prior baseline. The active HALLU defence on the
# pricing/promo gray zone (top_score 0.18..0.30) is the LLM-judge
# grounding check enabled in coder-260509-c2-grounding-check-enabled.
DEFAULT_CRAG_MIN_FALLBACK_SCORE_BY_INTENT: Final[dict[str, float]] = {
    # Factoid threshold lowered (0.35 → 0.25) per LOAD_TEST_VERDICT
    # Q18 Điều 45 analysis: single-article legal/regulatory queries
    # often score ~0.30-0.40 on dense retrieval; 0.35 was too strict
    # and CRAG grader rejected enough chunks to trigger rewrite_retry
    # loops on factoid-class regulatory queries. Lower bound keeps
    # HALLU sacred because the Anti-fake-section sysprompt + grounding
    # check still gates fabrication downstream.
    "factoid": 0.25,
    "comparison": 0.20,
    "multi_hop": 0.15,
    "aggregation": 0.20,
    "out_of_scope": 0.30,
    "greeting": 0.30,
    "feedback": 0.30,
    "promo": 0.40,
    "sale": 0.40,
    "voucher": 0.40,
}

DEFAULT_CRAG_FALLBACK_COUNT: Final[int] = 2
DEFAULT_CRAG_MAX_GRADE_RETRIES: Final[int] = 1
# CRAG fallback score calibration. The absolute floors above
# (DEFAULT_CRAG_MIN_FALLBACK_SCORE*) are tuned for the reranker cross-encoder
# scale (0..1). When the reranker was bypassed (intent-skip / small pool / API
# fallback) the surviving chunks carry RRF scores (~0.01 = 1/(k+rank)) which are
# NOT comparable to a 0.25 absolute floor — so the floor would always reject and
# the bot refuses a question it could answer. When scores are RRF-scale we
# switch to a RELATIVE gate: keep candidates within this ratio of the top score
# (scale-invariant). 0.5 = "at least half as strong as the best candidate".
DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO: Final[float] = 0.5

# Compound-intent grader leniency (mega-sprint G10b · Issue 10).
# Live evidence Case B: a compound query "X and Y in document Z" decomposes
# into 2 sub-queries; retrieve+RRF+MMR collapses to 1 surviving chunk that
# answers ONE sub-entity. The grader sees the FULL compound query and the
# single chunk and rationally returns "no" (chunk does NOT answer the WHOLE
# question) -> retrieval_adequate=False -> empty answer.
#
# Fix: for synthesis-style intents (comparison, multi_hop, aggregation),
# remap an "irrelevant" verdict to "ambiguous" so the chunk stays in the
# graded pool and downstream ``generate`` can synthesize a partial answer.
# HALLU=0 sacred preserved by downstream ``grounding_check`` guardrail
# which evaluates the FINAL answer against the chunks.
#
# Operator escape hatch: set ``crag_lenient_grade_for_compound_intents_enabled``
# in pipeline_config to ``False`` (per-bot via ``plan_limits`` resolver) to
# revert to strict grading.
DEFAULT_CRAG_LENIENT_GRADE_FOR_COMPOUND_INTENTS_ENABLED: Final[bool] = True
DEFAULT_CRAG_LENIENT_GRADE_INTENTS: Final[frozenset[str]] = frozenset(
    {INTENT_COMPARISON, INTENT_MULTI_HOP, INTENT_AGGREGATION}
)

# Smart-skip CRAG retry knob (T1-Smartness · S1 Pipeline-Opt). When the
# pass-1 top retrieval score is at or above this floor, the grade-LLM call
# AND the rewrite_retry loop are both bypassed — pass-1 already cleared the
# confidence bar, so a second rewrite + retrieve + grade pass would burn
# ~10s without changing the answer set. Default 0.7 (production-tuned from
# trace fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4 — top_score=0.91 wasted
# 10683ms on retry). Set to 1.1 (or any value > 1.0) to disable. Bot owner
# overrides per-domain via ``plan_limits.crag_skip_retry_above_score``.
# HALLU sacred preserved by downstream grounding_check guardrail.
DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE: Final[float] = 0.7

# Wall-clock cap on the CRAG grade-LLM call before the node falls back
# to the reranker-supplied order. Distribution skew (live diag
# 2026-05-18: p50 ~0ms when the high-score skip fires; p95 2.56s when
# the LLM is invoked) means the tail caller dominates the chat-graph
# p95. The cap acts as a safety net — the answer LLM still sees the
# top reranker chunks, just without CRAG re-ordering. Per-bot override
# via ``pipeline_config.grade_timeout_s`` for tenants who prefer
# fidelity over latency. ``0`` disables the cap.
DEFAULT_GRADE_TIMEOUT_S: Final[float] = 2.0

# BE-to-BE upload idempotency window. Partner retries within this
# window with the same ``X-Idempotency-Key`` get a 200 + original
# document_id; the worker does NOT double-ingest. Default 24h matches
# the typical gateway retry-budget upper bound; a nightly sweep
# (``scripts/cleanup_expired_idempotency_keys.py``) drops expired
# rows so the table stays bounded.
DEFAULT_INGEST_IDEMPOTENCY_TTL_HOURS: Final[int] = 24
INGEST_IDEMPOTENCY_KEY_MAX_LEN: Final[int] = 128
INGEST_IDEMPOTENCY_STATE_PROCESSING: Final[str] = "processing"
INGEST_IDEMPOTENCY_STATE_DONE: Final[str] = "done"
INGEST_IDEMPOTENCY_STATE_FAILED: Final[str] = "failed"
INGEST_IDEMPOTENCY_HEADER: Final[str] = "X-Idempotency-Key"

# Hard ceiling on LangGraph node iterations per request.
DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS: Final[int] = 8

# RRF rank-miss penalty (Cormack et al 2009 canonical).
DEFAULT_RRF_RANK_MISS_PENALTY: Final[int] = 1000

DEFAULT_CONVERSATION_RETENTION_DAYS: Final[int] = 90

# Generation temperature 0.0 — required for docs-only faithfulness.
DEFAULT_GENERATION_TEMPERATURE: Final[float] = 0.0

# Deterministic temperature for mechanical query-transform + classification
# steps. These reformulate the query (decompose/rewrite/multi_query/condense)
# or make a discrete decision (routing/intent/grade/grounding) — randomness adds
# no value and makes retrieval (hence the final answer) non-reproducible run to
# run. Measured 2026-06-09: at the inherited ~0.3 the SAME multi-fact question
# intermittently refused vs answered (spa Q7) because the reformulated sub-query
# shifted which chunks reached generation. Forcing 0.0 makes the pipeline
# reproducible. HyDE is excluded on purpose (light variation aids recall).
DEFAULT_DETERMINISTIC_TEMPERATURE: Final[float] = 0.0
DEFAULT_DETERMINISTIC_LLM_PURPOSES: Final[frozenset[str]] = frozenset({
    "decompose", "rewrite", "rewriting", "multi_query",
    "condense", "condensing", "routing", "understand_query",
    "intent", "grade", "grading", "grounding", "guard",
    "reflect", "reflection",
})

# Condense / understand-query trigger gates (audit 2026-06-13 zero-hardcode:
# thresholds 2 + 100 were inline-duplicated across the condense gate and the
# understand_query gate — a drift hazard if one site is edited. A conversation
# shorter than MIN_HISTORY turns, or carrying fewer than MIN_CHARS total, is too
# small to benefit from history condensation; skip the LLM call.
DEFAULT_CONDENSE_MIN_HISTORY_TURNS: Final[int] = 2
DEFAULT_CONDENSE_MIN_HISTORY_CHARS: Final[int] = 100

# Persona quality gate — audit-only; never overrides LLM.
DEFAULT_PERSONA_OVERSIZED_CHAR_THRESHOLD: Final[int] = 20000
DEFAULT_PERSONA_POLLUTION_PATTERNS: Final[tuple[str, ...]] = (
    r"\d+\.\d{3}\.\d{3}",
    r"\d+\s*triệu",
    r"\d+\s*đ\b",
    r"chưa có chương trình",
    r"giá kịch bản",
)
# Pairs (negative, positive) — both present = contradictory persona rules.
DEFAULT_PERSONA_DIRECTIVE_CONFLICT_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    (r"KHÔNG\s+tra\s+TL", r"tra\s+\[TL"),
    (r"KHÔNG\s+đọc\s+tài\s+liệu", r"đọc\s+tài\s+liệu"),
)

# --- Prompt caching (provider-side) -----------------------------------------
# OpenAI auto-caches prompts ≥ this many tokens.
OPENAI_AUTO_CACHE_MIN_TOKENS: Final[int] = 1024

# Anthropic prompt caching — provider codes that support cache_control breakpoints.
ANTHROPIC_PROVIDER_CODES: Final[tuple[str, ...]] = ("anthropic", "claude")

# --- Answer Autonomy --------------------------------------------------------
AUTONOMY_PERCENT_MIN: Final[int] = 0
AUTONOMY_PERCENT_MAX: Final[int] = 100

# Bands: 0=docs_only; 1..33=constrained; 34..66=moderate; 67..99=liberal; 100=research.
AUTONOMY_BAND_CONSTRAINED_MIN: Final[int] = 1
AUTONOMY_BAND_MODERATE_MIN: Final[int] = 34
AUTONOMY_BAND_LIBERAL_MIN: Final[int] = 67
AUTONOMY_BAND_RESEARCH_MIN: Final[int] = 100

DEFAULT_ANSWER_AUTONOMY_PERCENT: Final[int] = 0


# CSV row detection: ≥N commas + low sentence-end ratio = table row.
DEFAULT_CSV_MIN_COMMAS: Final[int] = 1
DEFAULT_STRATEGY_MIN_CONFIDENCE: Final[float] = 0.45
DEFAULT_CHUNK_MIN_CLAUSE_LEN: Final[int] = 20
# Parent-child chunking sizes (small-to-big retrieval).
DEFAULT_PARENT_CHUNK_SIZE: Final[int] = 1024
DEFAULT_CHILD_CHUNK_SIZE: Final[int] = 256
DEFAULT_CHILD_CHUNK_OVERLAP: Final[int] = 50
# Contextual enrichment (prefix injection at ingest).
# 8000 chars (~2700 token) đủ context global mà cache hit 95%+ trong 5min
# TTL. 2000 quá ít → Haiku gen wrong context (chunk số 1000 trong doc 600K
# không có signal Chương/Điều nào). 50K quá nhiều → context window pressure
# + cache miss cao. Sweet spot 8K = balance recall vs cost (audit findings
# 2026-05-13: Haiku Contextual Retrieval P/P optimization).
DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS: Final[int] = 8000
DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS: Final[int] = 500
DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS: Final[int] = 500
# Bumped 5→20 (2026-05-13): Anthropic Tier 1 = 4000 req/min, 20 concurrent
# = ~6000 req/min peak nhưng amortized OK. Doc 3849 chunks enrich:
# 5 concurrent = ~770s (~13 phút). 20 concurrent = ~190s (~3 phút). 4× speedup.
DEFAULT_ENRICHMENT_MAX_CONCURRENCY: Final[int] = 20
# Skip Haiku enrich cho doc nhỏ (Fix 4): doc <50K chars chỉ benefit
# marginal +5pp recall từ Contextual Retrieval, KHÔNG đáng tốn $0.35 + 2-5 phút.
# Doc lớn (legal, gov >100K) vẫn enrich full để giữ +35-49% recall.
DEFAULT_ENRICHMENT_SKIP_BELOW_CHARS: Final[int] = 50_000

# --- AdapChunk Layer 3 — Document Profile rule-based extractors ------------
# Regex patterns feeding analyze_document() additional fields per AdapChunk
# Phần 6.4 (formula_count / image_count / code_block_count / heading_ratio).
# Pure technical (regex pattern strings) — zero domain coupling.
DOCPROFILE_FORMULA_PATTERN: Final[str] = r"\$.*?\$|\\\(.*?\\\)|\\\[.*?\\\]"
DOCPROFILE_IMAGE_PATTERN: Final[str] = r"!\[.*?\]\(.*?\)|<img\s"
DOCPROFILE_CODE_FENCE_PATTERN: Final[str] = r"^```"
# Markdown spec: one fenced code block uses exactly 1 opening + 1 closing
# fence. Pair fence hits via integer division by this constant to recover
# block count.
DOCPROFILE_CODE_FENCES_PER_BLOCK: Final[int] = 2

