from __future__ import annotations
from typing import Final  # noqa: F401
from ._01_http_db_client_construction_ import *  # noqa: F401,F403

# --- Per-intent rerank skip gate (T2.S7) ------------------------------------
# Lightweight intents bypass the rerank API call when the candidate pool is
# already at-or-below ``rerank_top_n`` (no ranking ambiguity to resolve).
# Distinct from the per-bot whitelist above: this is the system-wide default
# fast-path; the whitelist is the per-bot opt-in narrowing of ALL intents.
# Members are lower-case canonical intent labels; the gate compares
# ``state["intent"].lower()`` so classifier casing drift cannot silently
# defeat the skip.
DEFAULT_RERANK_SKIP_INTENTS: Final[frozenset[str]] = frozenset(
    {"chitchat", "oos", "greeting", "feedback", "vu_vo", "factoid"},
)

# --- AI model defaults ------------------------------------------------------
DEFAULT_METADATA_EXTRACTION_MODEL: Final[str] = "gpt-4.1-mini"
DEFAULT_EMBEDDING_MODEL: Final[str] = "text-embedding-3-small"

# --- Asymmetric embedding (provider-agnostic) -------------------------------
# Provider binding (`bot_model_bindings.purpose='embedding'`) carries model
# name + dim at runtime; the data-table column is single, purpose-named.
DEFAULT_JINA_EMBEDDING_MODEL: Final[str] = "jina_ai/jina-embeddings-v3"
DEFAULT_EMBEDDING_TASK_QUERY: Final[str] = "retrieval.query"
DEFAULT_EMBEDDING_TASK_PASSAGE: Final[str] = "retrieval.passage"
# Jina embeddings direct-HTTP adapter (OpenAI-shaped ``{data:[{embedding}]}``).
# jina-embeddings-v3 = 1024-dim multilingual (Vietnamese in top-30 optimised
# languages). Flip via ``system_config.embedding_provider="jina"``.
DEFAULT_JINA_EMBEDDING_API_URL: Final[str] = "https://api.jina.ai/v1/embeddings"
DEFAULT_JINA_EMBEDDING_DIM: Final[int] = 1024
# late_chunking=True → the embedder runs one long-context forward pass over the
# concatenated chunk window, then pools per-chunk → cross-chunk context lands
# INSIDE the embedding with ZERO generative-LLM calls (replaces per-chunk nano
# CR, the O(n^2) ingest bottleneck). Window capped by token budget below.
DEFAULT_JINA_EMBEDDING_LATE_CHUNKING: Final[bool] = True
# Jina embedding rate cap (free key = 100 RPM / 100,000 TPM, enforced per-key).
# The embedder PACES under this so a multi-doc / multi-bot ingest burst queues
# instead of 429-storming (the LLM-router TPM limiter does NOT cover the embed
# path). Safety fraction keeps us below the ceiling for estimation slack.
DEFAULT_JINA_EMBEDDING_TPM_LIMIT: Final[int] = 100000
DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION: Final[float] = 0.90
# Jina free key = 2 CONCURRENT requests (separate from TPM). Exceeding it returns
# HTTP 429 RATE_CONCURRENCY even when under TPM — e.g. ingest embeds + a live
# query embed firing together. The embedder Singleton is shared, so capping its
# semaphore here bounds TOTAL in-flight Jina calls (ingest + query) to the plan
# limit. Raise to 50 on the paid tier.
DEFAULT_JINA_EMBEDDING_MAX_CONCURRENT: Final[int] = 2
# Per-request token ceiling when late_chunking is on (Jina caps the concatenated
# input at 8192 tokens). Chunks are grouped into windows under this budget so a
# big document is embedded as several context-preserving windows, not one blob.
DEFAULT_JINA_LATE_CHUNK_WINDOW_TOKENS: Final[int] = 7800
# ZeroEntropy embedding — hosted multilingual 2560-dim direct-HTTP adapter.
# Default OFF; flip via ``system_config.embedding_provider="zeroentropy"``.
DEFAULT_ZEROENTROPY_API_URL: Final[str] = "https://api.zeroentropy.dev"
DEFAULT_ZEROENTROPY_EMBEDDING_MODEL: Final[str] = "zembed-1"
# zembed-1 supports matryoshka truncation in {2560, 1280, 640, 320, 160, 80, 40}.
# 1280 chosen because pgvector HNSW caps at 2000 dim (full 2560 needs halfvec).
DEFAULT_ZEROENTROPY_EMBEDDING_DIM: Final[int] = 1280
# BKAI Vietnamese Bi-Encoder — PhoBERT-base-v2 backbone, 768-dim, self-hosted
# via HuggingFace Text-Embeddings-Inference (TEI) compatible endpoint.
# Default OFF; flip via ``system_config.embedding_provider="bkai_vn"`` AND
# ``system_config.bkai_vn_embedder_enabled=true``.
# Endpoint env override: ``BKAI_VN_EMBEDDING_URL`` (no default URL — must be
# explicitly configured per-deployment, no shared public endpoint).
DEFAULT_BKAI_VN_EMBEDDING_MODEL: Final[str] = (
    "bkai-foundation-models/vietnamese-bi-encoder"
)
DEFAULT_BKAI_VN_EMBEDDING_DIM: Final[int] = 768
# TEI exposes POST /embed (HF inference / TEI / vllm-compatible).
DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH: Final[str] = "/embed"
# LiteLLM model wire prefixes routing to the Jina embedding API key pool.
# Provider-routing surface — extend on new provider onboarding.
JINA_EMBEDDING_MODEL_PREFIXES: Final[tuple[str, ...]] = ("jina_ai/", "jina/")
# Single embedding column; data-table SSoT for vector storage.
DEFAULT_EMBEDDING_COLUMN: Final[str] = "embedding"
# SQL-injection defence: every f-string column name must pass this gate.
ALLOWED_EMBEDDING_COLUMNS: Final[frozenset[str]] = frozenset(
    {DEFAULT_EMBEDDING_COLUMN}
)

# Asymmetric passage prefix — empty by default; per-bot override via plan_limits.
DEFAULT_EMBEDDING_PASSAGE_PREFIX: Final[str] = ""

# --- Embedding-text strategy (prefix-pollution mitigation) ------------------
# Controls *what text* is fed to the dense encoder during ingest.
#   "prefix_plus_raw" (legacy default) — embed "{enriched_prefix}\n\n{raw_chunk}";
#       preserves backward compat with already-ingested corpora.
#   "raw_only" — embed `raw_chunk` only; the enriched prefix stays on the
#       persisted ``content`` column (so BM25 + rerank still see it) but the
#       dense encoder never tokenises it. Fixes short-keyword dilution
#       (e.g. "Điều 3?" was losing to chunks whose prefix said "Đoạn 3 ...").
# Per-bot override via ``bots.plan_limits.embedding_text_strategy``. Re-embed
# REQUIRED after toggling.
DEFAULT_EMBEDDING_TEXT_STRATEGY: Final[str] = "prefix_plus_raw"

# "auto" embedding-text strategy — DOMAIN-NEUTRAL, drives the choice from the
# document's CHUNK STRUCTURE, never from bot identity. Structural docs (HDT:
# legal / regulatory with "Điều/Chương/Mục" anchors) embed raw_only so the
# Contextual-Retrieval prefix does not dilute exact-anchor lookup; prose /
# table / FAQ docs embed prefix_plus_raw so the situated context aids semantic
# match. Any bot in any domain auto-gets the right strategy — no per-bot config.
EMBEDDING_TEXT_STRATEGY_AUTO: Final[str] = "auto"
# Chunk strategies that carry structural anchors → exact-match retrieval wins
# (raw_only). Everything else benefits from the contextual prefix.
STRUCTURAL_CHUNK_STRATEGIES: Final[frozenset[str]] = frozenset({"hdt", "hybrid"})

# --- Article-number metadata extraction (Vietnamese legal corpus) -----------
# Ingest scans each chunk for structured references (Điều / Chương / Khoản /
# Mục / Phụ lục) using language-agnostic regex (Latin + Roman numerals). The
# matches are persisted into ``metadata_json.article_no`` / ``chapter_no`` /
# ``clause_no`` / ``section_no`` / ``appendix_no`` so hybrid_search can
# pre-filter on a literal "Điều 3?" query before vector search runs. Default
# ON because the regex is cheap and bot owners that never have structured
# corpora simply see all metadata keys empty.
DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED: Final[bool] = True

# --- Hybrid-search RRF weight knobs (BM25 vs dense vector) ------------------
# RRF score = (bm25_weight / (rrf_k + rank_s)) + (vector_weight / (rrf_k + rank_d)).
# Equal weights (0.5 / 0.5) reproduce the historical formula
# ``1.0 / (rrf_k + rank_d) + 1.0 / (rrf_k + rank_s)`` after rescaling by 2.
# Bumping ``bm25_weight`` lifts keyword-heavy queries (legal article refs,
# product SKU lookups) without touching the dense top-k pool.
DEFAULT_HYBRID_RRF_BM25_WEIGHT: Final[float] = 0.5
DEFAULT_HYBRID_RRF_VECTOR_WEIGHT: Final[float] = 0.5

# --- Adaptive per-intent RRF weights (Phase-C C5) ---------------------------
# Feature flag — default OFF; rollout via ``system_config`` flip without
# redeploy. When ON, the retrieve node looks up the active ``intent`` in
# ``DEFAULT_RERANK_WEIGHTS_BY_INTENT`` (with ``pipeline_config`` override)
# and passes intent-tuned ``vector_weight`` / ``bm25_weight`` into the
# hybrid_search RRF fusion. Reverts to flat 0.5 / 0.5 when OFF or when the
# active intent has no entry (``"default"`` bucket).
#
# Each intent entry is a mapping with keys ``"vector"``, ``"bm25"``, and
# ``"reranker"``. The ``"reranker"`` weight is currently a forward-compat
# field (the rerank stage is downstream of fusion in the present pipeline
# and reorders chunks without per-component blending); it is parsed and
# logged but not yet applied to the fusion score. The ``"vector"`` and
# ``"bm25"`` weights flow directly into the pgvector hybrid_search RRF
# numerator. Sum-to-1 normalisation is recommended but not enforced — the
# pgvector store clamps to ``[0.0, +inf)`` per component.
#
# Defaults reflect HANDOFF §C5 intuition: ``factoid`` queries (literal
# entity / SKU lookups) lean vector + reranker for precision, ``multi_hop``
# / ``aggregation`` / ``comparison`` lean vector for recall across paraphrases,
# ``default`` keeps the historical 50/50 split as the safe fallback.
DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED: Final[bool] = False
DEFAULT_RERANK_WEIGHTS_BY_INTENT: Final[dict[str, dict[str, float]]] = {
    "factoid": {"vector": 0.5, "bm25": 0.3, "reranker": 0.2},
    "multi_hop": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "aggregation": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "comparison": {"vector": 0.6, "bm25": 0.2, "reranker": 0.2},
    "default": {"vector": 0.5, "bm25": 0.5, "reranker": 0.0},
}

# --- Embedder failover + BM25 fallback ----------------------------
# Domain-neutral provider keys; concrete adapter classes live under
# ``infrastructure/embedding/`` and are wired through the registry.
# Provider name "openai" is technical config (LiteLLM model namespace), not a
# brand literal — same shape as ``"jina"`` already accepted.
DEFAULT_EMBEDDING_PROVIDER: Final[str] = "jina"
# Opt-in via env ``APP_EMBEDDING_SECONDARY_PROVIDER=openai``. ``None`` =
# no failover (single-provider deployment); the FailoverEmbedder degrades
# transparently to a single-strategy passthrough so the wire-up is uniform.
DEFAULT_EMBEDDING_SECONDARY_PROVIDER: Final[str | None] = None
# Failover wrapper enabled at the registry level. When ``False`` the
# bootstrap returns the primary embedder bare (no wrapper, zero overhead).
DEFAULT_EMBEDDING_FAILOVER_ENABLED: Final[bool] = False
# BM25-only retrieval fallback when the embedder dies (no vector available).
# Always-on guard: a dead embedder must NOT silently kill retrieval; the
# orchestrator falls through to BM25 tsvector ranking with the same scoring
# semantics. Per-bot opt-out via ``pipeline_config.bm25_fallback_enabled``.
DEFAULT_RETRIEVAL_BM25_FALLBACK_ENABLED: Final[bool] = True
DEFAULT_RETRIEVAL_BM25_FALLBACK_TOP_K: Final[int] = 10
# OpenAI embedding fallback (cloud, LiteLLM-routed). Both constants are
# *technical* identifiers (model namespace + native dimension), not brand
# personalisation.
DEFAULT_OPENAI_EMBEDDING_MODEL: Final[str] = "text-embedding-3-small"
DEFAULT_OPENAI_EMBEDDING_DIMENSION: Final[int] = 1536
# Health-check probe timeout for boot-time embedder readiness checks. Short
# so an outage does not delay container start; CB takes over from there.
DEFAULT_EMBEDDER_HEALTHCHECK_TIMEOUT_S: Final[float] = 5.0

# --- Multi-vector embedding (late-interaction scaffold) ---------------------
# Operator flag in system_config; per-bot override via plan_limits.
# Default OFF — keeps single-vector retrieval semantics until storage layer
# + scoring rewrite land in a later phase (full ColBERT-style late
# interaction is deferred — this scaffold ships the Port + Null + a simple
# sentence-split strategy so calling sites can be wired ahead of time).
DEFAULT_MULTI_VECTOR_ENABLED: Final[bool] = False
DEFAULT_MULTI_VECTOR_PROVIDER: Final[str] = "null"
# Sentence-split strategy: max sentences per chunk. Caps the storage blow-up
# from very long chunks while keeping the first-N sentences (which carry the
# heading + opening claim in most layouts). 0 = no cap (emit every sentence).
DEFAULT_MULTI_VECTOR_MAX_SENTENCES: Final[int] = 8
# Minimum sentence length (chars) to keep — drops dangling fragments that
# would otherwise consume a vector slot with low-signal content.
DEFAULT_MULTI_VECTOR_MIN_SENTENCE_CHARS: Final[int] = 16

# --- i18n -------------------------------------------------------------------
DEFAULT_LANGUAGE: Final[str] = "vi"
# Languages with VN-domain teencode/abbreviation expansion.
VI_DOMAIN_LANGUAGES: Final[tuple[str, ...]] = ("vi",)
SUPERLATIVE_SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("vi", "en")
SYSTEM_CONFIG_KEY_DEFAULT_VOCAB_VI: Final[str] = "default_vocabulary_vi"

