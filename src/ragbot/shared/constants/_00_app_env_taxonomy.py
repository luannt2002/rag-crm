"""Shared constants — single source of truth for runtime defaults.

Per-bot overrides flow through DB (system_config / bots.plan_limits / pipeline_config).
"""

from __future__ import annotations

from typing import Final

# --- APP_ENV taxonomy -------------------------------------------------------
# Canonical env names. Aligned with ``AppSettings.env: Literal[...]`` in
# settings.py. Modules that gate behaviour by environment MUST import these
# constants instead of inlining the literal strings — that is the
# zero-hardcode rule in CLAUDE.md applied to env identifiers.
APP_ENV_DEVELOPMENT: Final[str] = "development"
APP_ENV_UAT: Final[str] = "uat"
APP_ENV_STAGING: Final[str] = "staging"
APP_ENV_PRODUCTION: Final[str] = "production"

# Envs that MUST reject CORS wildcards, dev-mode JWT secrets, and other
# permissive fallbacks. Development is intentionally absent so a local dev
# box need not maintain an explicit allow-list for same-origin work.
APP_ENVS_STRICT: Final[frozenset[str]] = frozenset({
    APP_ENV_UAT, APP_ENV_STAGING, APP_ENV_PRODUCTION,
})

# --- Retrieval / RAG defaults -----------------------------------------------
DEFAULT_TOP_K: Final[int] = 20
# Wider answer-context window into generate node; rerank cost is one batched
# call, so growing top_n keeps multi-fact coverage without measurable latency
# hit. Lifts top_score recall on probe set.
DEFAULT_RERANK_TOP_N: Final[int] = 7
# Cap on auto-mapped citations when LLM omits explicit [chunk:<id>] markers.
DEFAULT_CITATIONS_TOP_K: Final[int] = 3
# Smoke-level retrieval health floor — below = embedding pipeline broken.
DEFAULT_RETRIEVE_SMOKE_MIN_COSINE: Final[float] = 0.30
DEFAULT_RETRIEVE_SMOKE_MIN_CHUNKS: Final[int] = 3
DEFAULT_CHUNK_SIZE: Final[int] = 1024
DEFAULT_CHUNK_OVERLAP: Final[int] = 128
# Section-bounded sub-splits skip overlap to avoid topic bleed-through.
DEFAULT_CHUNK_OVERLAP_BOUNDARY: Final[int] = 0
# Below this size a chunk is treated as orphan and merged forward.
DEFAULT_CHUNK_ORPHAN_THRESHOLD: Final[int] = 100
DEFAULT_CHUNK_MAX_SIZE: Final[int] = 1024
# Fingerprint window for chunk-to-source string match (parent heading
# resolution, O(1) cache build). Short enough to find chunks unchanged
# by recursive overlap rewrites, long enough to disambiguate near-dupes.
DEFAULT_CHUNK_FINGERPRINT_CHARS: Final[int] = 80
# Footer-below-table preservation (RAG-Anything M18). When a TABLE block is
# immediately followed by a short non-heading TEXT block whose body is at
# most this many characters, the two are merged into a single TABLE block
# so the footer travels with the table. Per-bot opt-in flag:
# ``bots.plan_limits.table_footer_preserve_enabled`` (default TRUE — this
# is a bug-fix mindset; tables and their explanatory footers form one
# semantic unit and must not split across chunks).
DEFAULT_TABLE_FOOTER_MAX_CHARS: Final[int] = 200
DEFAULT_TABLE_FOOTER_PRESERVE_ENABLED: Final[bool] = True
# Chunk-type metadata (RAG-Anything M10). Per-chunk ``chunk_type`` column
# enables modality-aware retrieval / rerank. Allowed values are kept tight
# so the DB CHECK / index stays predictable; new types must be added here
# AND via alembic migration touching ``document_chunks.chunk_type``.
DEFAULT_CHUNK_TYPE_TEXT: Final[str] = "text"
DEFAULT_CHUNK_TYPE_TABLE: Final[str] = "table"
DEFAULT_CHUNK_TYPE_TABLE_ROW: Final[str] = "table_row"
DEFAULT_CHUNK_TYPE_CODE: Final[str] = "code"
CHUNK_TYPES_ALLOWED: Final[tuple[str, ...]] = (
    DEFAULT_CHUNK_TYPE_TEXT,
    DEFAULT_CHUNK_TYPE_TABLE,
    DEFAULT_CHUNK_TYPE_TABLE_ROW,
    DEFAULT_CHUNK_TYPE_CODE,
)
# XML-wrapped chunk rendering inside the LLM prompt (RAG-Anything M14).
# Format: ``<chunk id="c12" type="table" section="3.1"><content>…</content></chunk>``
# helps the model attribute citations and treat each chunk as an atomic
# unit. Default OFF for backward compatibility; bots created after the
# below cutoff date receive an implicit TRUE when ``plan_limits.xml_wrap_enabled``
# is unset. Explicit per-bot value (TRUE / FALSE) always wins.
DEFAULT_XML_WRAP_ENABLED: Final[bool] = False
# ISO-8601 cutoff: bots with ``created_at >= XML_WRAP_DEFAULT_ON_FROM_DATE``
# default ON when ``plan_limits.xml_wrap_enabled`` is absent. Parsed via
# ``datetime.fromisoformat`` at use-site.
XML_WRAP_DEFAULT_ON_FROM_DATE: Final[str] = "2026-05-18"
# Operator debug-view formats (admin GET /admin/documents/{id}/debug-view).
# Only "md" is implemented today; "html" / "json" reserved for future use
# without breaking the URL contract.
DEFAULT_DEBUG_VIEW_FORMAT: Final[str] = "md"
DEBUG_VIEW_FORMATS_ALLOWED: Final[frozenset[str]] = frozenset({"md"})
# FORMULA / IMAGE / CODE atomic protection. Default OFF — flip via
# ``system_config.formula_image_atomic_protect_enabled``. When ON, the
# block splitter recognises LaTeX formulas (``$$…$$`` / ``$…$``), image
# references (``![alt](url)``) and fenced code, marking them as atomic
# (``is_atomic=True``) so every chunking strategy keeps them whole —
# never cuts across the boundary. Inspired by RAG-Anything HKUDS 06/2025
# "tables as atomic semantic units" and AdapChunk Layer 2 "vùng cấm cắt".
DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED: Final[bool] = False
# Oversize threshold multiplier: atomic block larger than this ×
# ``chunk_size`` triggers a structured warning (FORMULA / IMAGE are
# still kept whole — splitting would destroy semantic atomicity).
DEFAULT_ATOMIC_OVERSIZE_WARN_MULTIPLIER: Final[float] = 2.0
# AdapChunk Layer 6 — atomic block-type set ("vùng cấm cắt").
# These BlockType labels mark structurally indivisible spans: cutting
# mid-block destroys their semantic atomicity (table row, formula token,
# image caption, code fence). The chunker emits them as standalone
# chunks regardless of ``chunk_size``. Values mirror the ``BlockType``
# Literal in ``shared/types.py`` (kept as plain str frozenset here to
# avoid pulling the type module into ``constants.py``).
DEFAULT_ATOMIC_BLOCK_TYPES: Final[frozenset[str]] = frozenset({
    "TABLE", "FORMULA", "IMAGE", "CODE",
})
# Active embedding output dimension. Production uses Jina v3 (1024-dim).
# Legacy OpenAI text-embedding-3-small dim (1536) remains exposed via
# DEFAULT_OPENAI_EMBEDDING_DIMENSION for backward-compat audits only.
# Runtime resolution: ``system_config.embedding_dimension`` wins; this
# constant is only the fallback when DB row is missing (post-DR scenarios).
DEFAULT_EMBEDDING_DIM: Final[int] = 1024
DEFAULT_HISTORY_LIMIT: Final[int] = 6
DEFAULT_CONDENSE_HISTORY_LIMIT: Final[int] = 6
# Owner-opt-in conversation summary compression (bots.convo_summary_enabled).
# Trigger: when a conversation reaches N turns the admin layer may replace
# older history with a summary produced by a ConvoSummaryPort. Bot owner can
# override per-bot; platform never auto-injects the summary into LLM prompts.
DEFAULT_CONVO_SUMMARY_MAX_TOKENS: Final[int] = 200
DEFAULT_CONVO_SUMMARY_TRIGGER_TURNS: Final[int] = 8
# Phase-C C1 — HyDE (Hypothetical Document Embeddings, Gao et al. 2022).
# Default OFF (Null Object). When opted in via ``system_config.hyde_enabled``
# (tenant-wide) or ``bots.plan_limits.hyde_enabled`` (per-bot), the platform
# asks an LLM to draft a short hypothetical answer and embeds THAT in place
# of the raw query so the vector lives closer to declarative chunk text.
DEFAULT_HYDE_ENABLED: Final[bool] = False
DEFAULT_HYDE_PROVIDER: Final[str] = "null"
# Multimodal VLM image captioning. Default OFF (Null Object): an image upload is
# parsed by the legacy OCR path until an operator flips ``system_config.vlm_provider``
# to ``"vlm_image"``. When ON, the ingest worker routes image MIMEs to the VLM
# parser (a vision model captions the image into retrievable text). Model is locked
# to a vision-capable binding (gpt-4.1-mini, supports_vision=true).
DEFAULT_VLM_PROVIDER: Final[str] = "null"
# Admin override 2026-05-12: HyDE is a utility task (not user-facing answer)
# so the platform default uses gpt-4.1-mini — Haiku is banned by the
# higher-tier admin policy regardless of cost savings.
DEFAULT_HYDE_MODEL: Final[str] = "gpt-4.1-mini"
# Short ceiling — the hypothetical must stay tight to query topic and the
# embedder typically truncates beyond a few hundred tokens anyway.
DEFAULT_HYDE_MAX_TOKENS: Final[int] = 200
# Low-but-not-zero temperature: light variation helps generalise to
# paraphrased chunk text while still keeping the answer on-topic.
DEFAULT_HYDE_TEMPERATURE: Final[float] = 0.3
# Wall-clock ceiling on the hypothetical-answer generation step. HyDE is a
# best-effort retrieval enhancement; the caller MUST degrade silently to
# the raw query when the LLM takes longer than this budget so the chat
# turn's p95 latency is bounded regardless of upstream model jitter.
DEFAULT_HYDE_GENERATION_TIMEOUT_S: Final[float] = 5.0
MAX_TOKENS_PER_REQUEST: Final[int] = 4000
MAX_ITERATION_CAP: Final[int] = 3

# --- Robust JSON Parser (T2 Defensive) ---------------------------------------
# 4-strategy fallback for LLM JSON output (direct → fence-strip → quote+comma
# repair → balanced-span extract). Default ON — pure-defensive helper with
# no quality regression risk: strict input keeps the fast path (strategy 1),
# malformed input that previously raised ``json.JSONDecodeError`` now has a
# chance to recover before the caller falls back to its degraded path.
# Flag exists so ops can flip OFF on the rare chance an upstream change
# regresses parsing behaviour without redeploying.
DEFAULT_ROBUST_JSON_PARSER_ENABLED: Final[bool] = True
ROBUST_JSON_PARSER_FLAG_KEY: Final[str] = "robust_json_parser_enabled"
# Preview length captured on JSONParseError + telemetry so dashboards have
# enough context to identify the offending LLM output without leaking the
# full payload into log storage.
DEFAULT_ROBUST_JSON_PARSE_PREVIEW_CHARS: Final[int] = 120

# --- Vector Search (pgvector) ------------------------------------------------
# Higher ef_search broadens HNSW candidate pool at query time, lifting ANN
# recall ~3-5pp on near-neighbour misses with negligible latency cost (a few
# extra millis per probe in pgvector benchmarks).
# Wave M3.6-F2 2026-05-20: lowered 100→64 to match ef_construction (HNSW
# build-time). pgvector docs: ef_search >= ef_construction = optimal recall;
# 100 was 1.56× diminishing-returns territory. Recall preserved ≥95% per
# Sonnet audit Finding 5 + paper-confirmed pgvector benchmark.
# DEFAULT_EF_SEARCH: Final[int] = 100  # Pre-M3.6-F2 value (kept for ref)
DEFAULT_EF_SEARCH: Final[int] = 64
DEFAULT_RRF_K: Final[int] = 60
MAX_EF_SEARCH: Final[int] = 1000
# ts_rank_cd bitmask: 1 (length norm) | 4 (harmonic distance).
DEFAULT_BM25_NORMALIZATION_FLAGS: Final[int] = 5

# --- Reranker ----------------------------------------------------------------
# Jina default (alembic 0228). Provider key matches infrastructure/reranker/
# registry.py. Boot-time fallback before system_config loads — keep aligned with
# the active reranker_provider so a cold-start doesn't probe the wrong vendor.
# Override via system_config.reranker_provider.
DEFAULT_RERANK_MODEL: Final[str] = "jina-reranker-v3"
DEFAULT_RERANKER_PROVIDER: Final[str] = "jina"

# --- Vector store ------------------------------------------------------------
# Provider key resolved by ``infrastructure/vector/registry.py``. Override via
# ``system_config.vector_store_provider`` (Redis-cached). Default = pgvector,
# the only DB-backed provider shipped in-tree; ``"null"`` is the fail-soft
# disabled mode. Adding a new backend (qdrant / weaviate / …) = drop a file
# in ``infrastructure/vector/`` and add it to the registry; no bootstrap edit.
DEFAULT_VECTOR_STORE_PROVIDER: Final[str] = "pgvector"

# --- Jina Reranker v3 --------------------------------------------------------
DEFAULT_JINA_RERANKER_MODEL: Final[str] = "jina-reranker-v3"
DEFAULT_JINA_RERANKER_TIMEOUT_S: Final[float] = 30.0
# Jina rerank API hard limit per request — exceeding triggers 400.
DEFAULT_JINA_RERANKER_MAX_DOCS: Final[int] = 64
DEFAULT_JINA_HEALTH_CHECK_TIMEOUT_S: Final[float] = 10.0
DEFAULT_JINA_RERANKER_SCORE_PRECISION: Final[int] = 6
# Base URL for Jina AI API — override via env JINA_API_BASE_URL (e.g. proxy/self-hosted).
DEFAULT_JINA_API_BASE_URL: Final[str] = "https://api.jina.ai/v1"
# Timeout for key-verify pre-check calls (lightweight ping, not full rerank load).
DEFAULT_KEY_VERIFY_TIMEOUT_S: Final[float] = 10.0

