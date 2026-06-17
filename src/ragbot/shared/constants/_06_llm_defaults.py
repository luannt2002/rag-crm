from __future__ import annotations
from typing import Final  # noqa: F401
from ._05_embedding_circuitbreaker import *  # noqa: F401,F403

# --- LLM defaults -----------------------------------------------------------
DEFAULT_LLM_MAX_TOKENS: Final[int] = 1000
DEFAULT_LLM_TEMPERATURE: Final[float] = 0.3
DEFAULT_GENERATION_MAX_TOKENS: Final[int] = 450
DEFAULT_METADATA_MAX_TOKENS: Final[int] = 300

# --- Multi-agent review framework (offline plan/code/sysprompt review) -----
# Cost scales linearly with agent count × debate rounds; keep tokens tight.
DEFAULT_MULTI_AGENT_MAX_TOKENS: Final[int] = 800
DEFAULT_MULTI_AGENT_TEMPERATURE: Final[float] = 0.2
DEFAULT_MULTI_AGENT_DEBATE_ROUNDS: Final[int] = 1
DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS: Final[int] = 2

# Per-intent override of the generation max_tokens budget. Keys MUST match
# UnderstandOutput.intent Literal (test-category labels stay out — load-test
# golden_set.json owns those). Numbers are concision targets, not measured
# completion-token caps; revisit when request_steps tracks finish_reason.
DEFAULT_GENERATE_MAX_TOKENS_BY_INTENT: Final[dict[str, int]] = {
    "greeting": 60,
    "feedback": 150,
    "chitchat": 80,
    "vu_vo": 80,
    "out_of_scope": 80,
    "factoid": 300,
    "comparison": 400,
    "aggregation": 400,
    "multi_hop": 400,
    "default": 250,
}

# --- Per-purpose LLM binding (cost-aware routing) -------------------
# Bot owners seed `bot_model_bindings` rows for each cheap purpose; resolver
# falls back to PRIMARY when a cheap-purpose binding is absent (per-bot
# opt-out). Purpose strings are name-only — the actual model UUID lives in
# the binding row, keeping the orchestrator domain-neutral and free of model
# literal references.
DEFAULT_LLM_PURPOSE_PRIMARY: Final[str] = "llm_primary"
DEFAULT_LLM_PURPOSE_FACTOID: Final[str] = "llm_factoid"
DEFAULT_LLM_PURPOSE_CHITCHAT: Final[str] = "llm_chitchat"
DEFAULT_LLM_PURPOSE_OOS: Final[str] = "llm_oos"
DEFAULT_LLM_PURPOSE_INTENT_UNDERSTAND: Final[str] = "llm_intent_understand"
# Ingest contextual-retrieval / narrate enrichment routes here so it can run a
# cheaper model than the answer LLM (extractive task, highest call volume).
# resolve_llm falls back to llm_primary when a bot has no binding for this
# purpose, so it never breaks resolution.
DEFAULT_LLM_PURPOSE_ENRICHMENT: Final[str] = "enrichment"

# Intent → cost-aware purpose mapping. Intents NOT listed here keep PRIMARY.
# Bot owner can disable cost-routing entirely by NOT seeding the cheap
# purpose binding rows — resolver falls back to PRIMARY automatically.
DEFAULT_CHEAP_INTENT_PURPOSES: Final[dict[str, str]] = {
    "factoid": DEFAULT_LLM_PURPOSE_FACTOID,
    "chitchat": DEFAULT_LLM_PURPOSE_CHITCHAT,
    "out_of_scope": DEFAULT_LLM_PURPOSE_OOS,
    "vu_vo": DEFAULT_LLM_PURPOSE_OOS,
    "greeting": DEFAULT_LLM_PURPOSE_CHITCHAT,
}

# --- LLM provider failover ------------------------------------------
# When a primary model's circuit breaker OPENs or LiteLLM raises a
# retryable ``LLMError``, the router can re-issue the same prompt to the
# binding's ``record_fallback_model_id`` once. ``None`` on the binding =
# no failover (per-bot opt-out). MAX_HOPS=1 caps cascade latency on
# multi-provider outages — the second failure re-raises rather than
# triggering a third try.
DEFAULT_LLM_FAILOVER_ENABLED: Final[bool] = True
DEFAULT_LLM_FAILOVER_MAX_HOPS: Final[int] = 1

# Per-model token-per-minute throttle. Matches the provider's published TPM so
# the LLM gateway PACES (queues) calls instead of bursting → 429 → retry storm.
# 0 disables the limiter. Applied as a process-local limiter (correct when the
# app runs a single uvicorn worker; multi-process would need dividing by N or a
# shared Redis counter).
DEFAULT_LLM_TPM_LIMIT: Final[int] = 200000
# Pace BELOW the org ceiling, not exactly at it. estimate_request_tokens counts
# prompt + max_tokens but the provider bills prompt + ACTUAL completion, and the
# provider's trailing-window clock is not phase-aligned with ours — so admitting
# right up to 100% of the limit still overshoots and earns a 429. Reserving a
# headroom margin keeps the limiter strictly under the real ceiling.
DEFAULT_LLM_TPM_SAFETY_FRACTION: Final[float] = 0.9

# --- Document processing ---------------------------------------------------
WHOLE_DOC_THRESHOLD_CHARS: Final[int] = 1500
# Above N distinct topical signals the whole-doc fast path is rejected.
DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS: Final[int] = 2
# Min paragraph block size to count as a topical signal.
DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS: Final[int] = 200
DEFAULT_TOPIC_NUMBERED_MARKER_RE: Final[str] = r"^\s*\d+[\.\)]\s+\S"
DEFAULT_CONTENT_PREVIEW_CHARS: Final[int] = 2000
DEFAULT_SOURCE_PREVIEW_CHARS: Final[int] = 200
DEFAULT_LOG_PREVIEW_CHARS: Final[int] = 100

# --- Guardrail defaults -----------------------------------------------------
DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH: Final[int] = 8000
DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS: Final[int] = 2
DEFAULT_GUARDRAIL_TIMEOUT_S: Final[int] = 30
DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE: Final[int] = 24
# Bot's OOS refusal text shares vocabulary with system_prompt (per-bot owner phrases),
# producing shingle collisions that mislabel legitimate refusals as prompt-leak.
# Skip leak detection when answer ≈ oos_answer_template (Jaccard on word sets).
DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD: Final[float] = 0.90

# --- Semantic chunking ------------------------------------------------------
# Legacy lexical (SequenceMatcher + Jaccard) boundary threshold.
DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD: Final[float] = 0.3

# --- Embedding-based semantic chunking (FIX gap S1) -------------------------
# Cosine similarity threshold below which adjacent sentences are considered
# a topic boundary. Distinct from the lexical threshold above because cosine
# from a dense embedder distributes mass differently than SequenceMatcher +
# Jaccard. Inspired by LangChain SemanticChunker default 0.95 percentile +
# NVIDIA RAGAS page-level consistency benchmark.
DEFAULT_EMBEDDING_SEMANTIC_SIMILARITY_THRESHOLD: Final[float] = 0.65
# Sentence-embedding cache TTL (s). Embeddings are deterministic per model
# so a 1h window comfortably outlives a single ingestion batch while letting
# operator model swaps land within the bootstrap_config refresh window.
DEFAULT_SENTENCE_EMBEDDING_CACHE_TTL_S: Final[int] = 3600
# Maximum sentences per document the embedding strategy will embed. Above
# this the strategy falls back to the lexical default — embedding 10k+
# sentences in a single ingest blows the provider budget for no recall lift.
DEFAULT_EMBEDDING_SEMANTIC_MAX_SENTENCES: Final[int] = 2000
# Feature flag default (zero-hardcode: callers MUST import this rather than
# baking ``False`` literals into branch conditions).
DEFAULT_EMBEDDING_SEMANTIC_CHUNK_ENABLED: Final[bool] = False
# Provider key for the sentence-similarity registry. ``"lexical"`` keeps the
# legacy SequenceMatcher + Jaccard path; ``"embedding"`` opts in to dense
# cosine; ``"null"`` is a hard-fail probe used by tests.
DEFAULT_SENTENCE_SIMILARITY_PROVIDER: Final[str] = "lexical"

# --- Cross-doc chunk dedup --------------------------------------------------
DEFAULT_DEDUP_JACCARD_THRESHOLD: Final[float] = 0.85
DEFAULT_DEDUP_MIN_CHARS: Final[int] = 50

# --- Corpus clean helper (scripts/corpus_clean.py) --------------------------
# Excerpt length cap when reporting chunks to a human-reviewable JSON / table.
DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS: Final[int] = 100
# Minimum substring length when grouping chunks by service mention for the
# price-conflict detector. Below this the substring is too generic to be a
# useful service identifier (e.g. single token "da").
DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS: Final[int] = 8
# Default Vietnamese price regex — matches K-suffix shorthand (199K, 1.5M),
# dotted thousands (1.499.000), comma thousands (1,499,000) and bare 4-7 digit
# numbers. Bot owner can override via --regex on the CLI.
DEFAULT_CORPUS_CLEAN_PRICE_REGEX: Final[str] = (
    r"\d+[\.,]\d{3}(?:[\.,]\d{3})*|\d+(?:[KkMm])\b|\b\d{4,7}\b"
)
# RAG-friendly heuristic targets — see docs/templates/RAG_FRIENDLY_SHEET_TEMPLATE.md.
DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS: Final[int] = 250
DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS: Final[int] = 400

# --- Pagination -------------------------------------------------------------
DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100

# --- Admin tenant CRUD pagination ----------------------------------
# Separate from generic ``DEFAULT_PAGE_SIZE`` so future per-resource tuning
# (large tenants vs lean bot list) does not require touching the global.
DEFAULT_ADMIN_TENANT_LIST_LIMIT_DEFAULT: Final[int] = 50
DEFAULT_ADMIN_TENANT_LIST_LIMIT_MAX: Final[int] = 200
# Slug + name length caps mirror the schema (``tenants.name`` VARCHAR(255)).
# Slug is platform-defined for routing safety, kept tighter than name.
DEFAULT_ADMIN_TENANT_NAME_MAX_LENGTH: Final[int] = 200
DEFAULT_ADMIN_TENANT_SLUG_MAX_LENGTH: Final[int] = 100

