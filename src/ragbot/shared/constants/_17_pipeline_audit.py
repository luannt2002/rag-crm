from __future__ import annotations
from typing import Final  # noqa: F401
from ._16_prompt_token_squeeze import *  # noqa: F401,F403

# --- 260509-a1-pipeline-audit (6 constants) ---
DEFAULT_PROXIMITY_CACHE_LSH_BUCKETS: Final[int] = 64
DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD: Final[float] = 0.92
DEFAULT_RAGAS_STUB_SCORE: Final[float] = 0.0
DEFAULT_SELF_RAG_SKIP_INTENTS: Final[frozenset[str]] = frozenset(
    {"greeting", "chitchat", "vu_vo"}
)
RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV: Final[str] = "RAGBOT_ALLOW_SUPERUSER_RUNTIME"
RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE: Final[str] = "1"

# --- 260511-S4-security-p0-triple (4 constants) ---
# Retry-After (seconds) returned with 503 when the Layer-1 tenant
# rate-limit backend is unavailable. Tells well-behaved clients to back
# off rather than tight-loop hammering the failing limiter.
DEFAULT_RL_FAIL_CLOSED_RETRY_S: Final[int] = 30
# JWT issuer string that all service tokens must carry in the `iss` claim.
# Mismatch → pyjwt raises InvalidIssuerError; decode returns None and the
# request is 401-rejected. Keep aligned with the `iss` field minted in
# JwtTokenService.create_token / regenerate_token.
JWT_ISSUER: Final[str] = "ragbot"
# Claims that MUST be present on every service JWT. Missing any → pyjwt
# raises MissingRequiredClaimError. `exp` was already required (P0 fix);
# `iss` is added so attacker-minted tokens with no/forged issuer fail.
JWT_REQUIRED_CLAIMS: Final[list[str]] = ["exp", "iss"]
# --- Security hardening: /metrics auth + JWT iss + RL fail-closed -----------
# Env-var name carrying the operator-issued Bearer token that guards the
# Prometheus /metrics endpoint. Unset → endpoint stays open (dev mode,
# backward-compat). Set → middleware-free route handler enforces match.
RAGBOT_METRICS_AUTH_TOKEN_ENV: Final[str] = "RAGBOT_METRICS_AUTH_TOKEN"

# Cap on how many dropped chunk_ids each filter stage records into its
# request_steps metadata (C1 chunk-survival trace). Bounds the JSONB row size /
# label cardinality while still surfacing enough of the dropped set to diagnose
# a "why did the answer chunk die" case.
DEFAULT_CHUNK_SURVIVAL_TRACE_CAP: Final[int] = 20

# --- 260512-S7-adaptive-router-l4-bm25 (2 constants) ---
# RRF k constant used when fusing vector + lexical lists. Mirrors
# ``DEFAULT_RRF_K`` (Cormack canonical) — kept as a named alias so the
# wiring is self-documenting at the call site.
DEFAULT_LEXICAL_RRF_K: Final[int] = 60
# Per-query candidate pool. Higher than dense ``top_k`` because BM25 hits
# are cheap to score and the RRF merge benefits from a wider sparse pool.
DEFAULT_LEXICAL_TOP_K: Final[int] = 20

# --- 260513-B3-skip-understand-greeting (3 constants) ---
# Regex patterns (case-insensitive) for the greeting branch. Patterns are
# anchored at the start of the stripped query. VN + EN coverage stays
# domain-neutral — no brand / industry literal. Bot owner overrides via
# plan_limits.understand_greeting_patterns (list[str]) or system_config
# JSON array.
DEFAULT_GREETING_PATTERNS: Final[tuple[str, ...]] = (
    r"^(ch[aà]o|hi|hello|xin ch[aà]o|hey|good\s+(morning|afternoon|evening))\b",
    r"^(c[aả]m\s*[oơ]n|thanks|thank\s+you)\b",
    r"^(t[aạ]m bi[eệ]t|bye|goodbye|see\s+you)\b",
)
# --- Skip understand_query for greeting / short queries (Stream B3) ---------
# Short (≤ N tokens) or greeting-pattern queries don't need the LLM
# understand step — the classifier always lands on chitchat/greeting and
# the condense pass has no history worth condensing. Bypass the LLM call
# (saves ~1.5s on slow turns, ~600ms p50). Default OFF preserves
# byte-identical legacy behaviour; bot owner flips per-domain via
# plan_limits.skip_understand_for_greeting.
DEFAULT_SKIP_UNDERSTAND_FOR_GREETING: Final[bool] = False
# Token-count threshold for the short-query branch of the skip gate.
# `len(query.split()) <= N` qualifies as "short". Default 3 matches the
# observed greeting-token distribution (hi / chào / cảm ơn anh).
DEFAULT_UNDERSTAND_SKIP_BELOW_TOKENS: Final[int] = 3

# --- 260520-C2-article-metadata (3 constants) ---
# Keys in the metadata-filter dict that live on the CHUNK row
# (``document_chunks.metadata_json``) rather than the parent DOCUMENT row
# (``documents.metadata_json``). hybrid_search splits the incoming filter dict
# by this set: chunk-keys feed the per-chunk JSONB containment clause, all
# other keys feed the document-level containment clause. The split keeps the
# port surface unchanged (still one ``metadata_filter`` dict) while routing
# article/clause/section/appendix/chapter anchors to the correct row.
CHUNK_LEVEL_METADATA_FILTER_KEYS: Final[frozenset[str]] = frozenset({
    "article_no",
    "clause_no",
    "section_no",
    "appendix_no",
    "chapter_no",
})
# Default regex pattern list seeded into ``ArticleAwareFilter`` when
# ``system_config.article_ref_patterns`` is unset. The list itself lives in
# constants (not hardcoded in the strategy class) so adding / removing
# patterns is a one-line config edit — operators with non-VN corpora replace
# the list wholesale via ``system_config``. Pattern fields:
#   - name:  output key suffix (``<name>_no``) — must match ingest-side schema
#   - regex: raw regex with first capture group capturing the number/letter
#   - flags: optional "IGNORECASE" (only flag accepted; see filter module)
DEFAULT_ARTICLE_REF_PATTERNS: Final[tuple[dict[str, str], ...]] = (
    {"name": "article", "regex": r"\bĐiều\s+(\d{1,4})\b", "flags": "IGNORECASE"},
    {"name": "clause", "regex": r"\bKhoản\s+(\d{1,4})\b", "flags": "IGNORECASE"},
    {"name": "section", "regex": r"\bMục\s+(\d{1,4})\b", "flags": "IGNORECASE"},
    {"name": "appendix", "regex": r"\bPhụ\s+lục\s+([A-Z0-9]{1,4})\b", "flags": "IGNORECASE"},
    {"name": "chapter", "regex": r"\bChương\s+([IVXLCDM]{1,6}|\d{1,4})\b", "flags": "IGNORECASE"},
)
# --- Article-aware metadata pre-filter (query-side companion of ingest) -----
# Strategy port resolved via ``system_config.metadata_filter_provider``. Default
# ``"null"`` keeps retrieval byte-identical to pre-C2 behaviour; operator flips
# to ``"article_aware"`` to enable regex-driven structural-reference detection
# that narrows hybrid_search candidates via JSONB ``metadata_json @> :filter``.
DEFAULT_METADATA_FILTER_PROVIDER: Final[str] = "null"

# --- Layer 3 GenericLLMMetadataExtractor — Plan 260604-metadata-aware-v4 ---
# Pure technical constants (CLAUDE.md zero-hardcode allows: timeout, batch).
# Model name + prompt template KHÔNG ở đây — resolve via DB
# (system_config.metadata_extraction_model + language_packs.metadata_extract_default).
DEFAULT_METADATA_EXTRACT_MAX_TOKENS: Final[int] = 300
DEFAULT_METADATA_EXTRACT_TIMEOUT_S: Final[float] = 5.0
DEFAULT_METADATA_CACHE_TTL_S: Final[int] = 3600  # 1h
DEFAULT_METADATA_INGEST_CONCURRENCY: Final[int] = 8

# Schema contract for LLM output (NOT config — this is API contract)
METADATA_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    "entities", "topics", "keywords", "numbers_or_years", "intent",
)
METADATA_INTENT_ENUM: Final[tuple[str, ...]] = (
    "factoid", "comparison", "reasoning", "listing", "oos",
)

# Fallback model name when DB resolve fails (last-resort safety net).
# Production override: system_config.metadata_extraction_model.
DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL: Final[str] = "gpt-4.1-nano"

# --- sprint0-analytics (5 constants) ---
