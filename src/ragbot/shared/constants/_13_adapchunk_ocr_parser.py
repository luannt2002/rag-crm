from __future__ import annotations
from typing import Final  # noqa: F401
from ._12_multi_stage_retrieval_fallba import *  # noqa: F401,F403

# --- AdapChunk Layer 1: OCR parser engine selection -------------------------
# Precedence: env ``RAGBOT_PARSER_ENGINE`` > ``system_config.parser_engine``
# (operator syncs to env before service start; DI wiring is sync) > default
# below. Fallback chain at boot: kreuzberg → docling → simple, so a missing
# optional dependency degrades transparently instead of crashing.
RAGBOT_PARSER_ENGINE_ENV: Final[str] = "RAGBOT_PARSER_ENGINE"
DEFAULT_PARSER_ENGINE: Final[str] = "kreuzberg"
# DEPRECATED 2026-05-14 AdapChunk-reorg: default flipped simple → kreuzberg
# (layout-aware F1 ~91% parity with Docling, ~9× faster). Per Wave C2 winner.
# DEFAULT_PARSER_ENGINE: Final[str] = "simple"
KREUZBERG_PARSER_ENGINE_KEY: Final[str] = "kreuzberg"
DOCLING_PARSER_ENGINE_KEY: Final[str] = "docling"
SIMPLE_PARSER_ENGINE_KEY: Final[str] = "simple"

# Customer-supplied chunk-context column (Anthropic Contextual Retrieval
# Phase 4.5). When a sheet/CSV header matches one of these labels (case
# and accent insensitive), parsers lift that column's value into
# ``metadata.enriched_prefix`` and drop it from the row content so the
# Topic phrase boosts retrieval without duplicating in the chunk body.
CUSTOMER_CONTEXT_COLUMN_NAMES: Final[tuple[str, ...]] = (
    "Topic", "Context", "Section", "Mô tả", "Mo ta", "Description",
)

# Max chars for a tabular cell to read as a "label" (column header / section title)
# rather than a prose NOTE — shape-only length gate for the L1 structured-markdown
# converter. A section title may run up to 2× this (long heading lookahead).
DEFAULT_TABLE_LABEL_MAX_CHARS: Final[int] = 40

# L1 structure-recovery: a run of >= this many consecutive fully-blank rows is a real
# TABLE BOUNDARY; a shorter run is a stray spacer that must be skipped so it does not
# close the table (which would strand the following data rows headerless → col_N).
DEFAULT_TABLE_GAP_ROWS: Final[int] = 2

# --- Sysprompt validator (Stream G) -----------------------------------------
# 10-item pre-deploy check thresholds + heuristic vocabularies. Single SSoT
# so owner-facing tooling cannot drift from runtime constraints.
SYSPROMPT_CHARS_PER_TOKEN_AVG: Final[float] = 3.7
SYSPROMPT_MAX_TOKENS_TARGET: Final[int] = 3000
SYSPROMPT_MAX_TOKENS_HARD: Final[int] = 4000

SYSPROMPT_EXPECTED_SECTIONS: Final[tuple[str, ...]] = (
    "ROLE", "SCOPE", "TONE", "RESPONSE", "OOS", "REFUSAL",
    "SAFETY", "ANTI-HALLU", "JAILBREAK",
)

# Owner-side anti-pattern phrases that suggest a runtime instruction is
# being injected into the sysprompt body (use Section 4 grounding rule
# instead). VN + EN; case-insensitive matching at call site.
SYSPROMPT_INJECT_PHRASES: Final[tuple[str, ...]] = (
    "phải trả lời", "không được trả lời", "bắt buộc",
    "must answer", "you must say", "always reply",
)

# Phrases that leak internals → block deploy. Generic; per-bot brand
# literals stay out of this list (each bot owner picks brand strings).
SYSPROMPT_LEAK_PHRASES: Final[tuple[str, ...]] = (
    "bot_id", "system prompt", "claude-opus", "claude-sonnet",
    "anthropic api", "openai api",
)

SYSPROMPT_OOS_MIN_VARIANTS: Final[int] = 3
SYSPROMPT_OOS_QUOTE_MIN_LEN: Final[int] = 20

# Memory-safety byte ceilings per parser. PDF default lifted to 10MB (was
# 50MB) so a worst-case concurrent burst of 4 PDFs caps resident memory
# at ~40MB before semaphore queueing — tenants needing larger files
# override via bots.plan_limits.pdf_max_bytes.
DEFAULT_PDF_MAX_BYTES: Final[int] = 10 * 1024 * 1024
DEFAULT_DOCX_MAX_BYTES: Final[int] = 50 * 1024 * 1024
DEFAULT_MARKDOWN_MAX_BYTES: Final[int] = 10 * 1024 * 1024
# Module-level semaphore caps inflight PDF parses across the worker so a
# concurrent upload burst never multiplies the per-document allocation.
DEFAULT_PDF_PARSE_CONCURRENCY: Final[int] = 4

# --- Text Normalizer Strategy registry --------------------------------------
DEFAULT_TEXT_NORMALIZER_PROVIDER: Final[str] = "null"

# --- Tool Client Strategy registry ------------------------------------------
DEFAULT_TOOL_CLIENT_PROVIDER: Final[str] = "null"
# Empty = block-all (default); regex compiled at runtime.
DEFAULT_MCP_SERVER_URL_ALLOWLIST: Final[str] = ""

# --- Source Validator Strategy registry (T1-Safety) -------------------------
# Per-bot source-URL allow-list (PoisonedRAG arXiv 2402.07867 defence).
# Provider key selects which adapter the DI registry returns; per-bot
# allow-list patterns live in ``bots.plan_limits.allowed_source_domains``.
# Default ``"null"`` keeps byte-identical behaviour for existing tenants.
DEFAULT_SOURCE_VALIDATOR_PROVIDER: Final[str] = "null"
# Feature flag default — opt-in. When False the Null adapter is selected
# regardless of provider key, so the bot owner has to flip BOTH the flag
# AND the provider to enable filtering. Two-step opt-in matches the PII
# redactor / CleanBase two-knob pattern.
DEFAULT_SOURCE_ALLOWLIST_ENABLED: Final[bool] = False

# --- PII Redactor Strategy registry -----------------------------------------
DEFAULT_PII_REDACTOR_PROVIDER: Final[str] = "null"

# --- PII Universal Coverage surfaces (Phase D2) -----------------------------
# Surface tags identify WHERE the redaction was applied so the structured
# ``pii_redacted`` audit event can be sliced per-pipeline-stage (compliance
# dashboards: "how many CCCD masks at chat_query vs ingest_content vs
# audit_log vs request_steps last 24h").
PII_SURFACE_CHAT_QUERY: Final[str] = "chat_query"
PII_SURFACE_INGEST_CONTENT: Final[str] = "ingest_content"
PII_SURFACE_AUDIT_LOG: Final[str] = "audit_log"
PII_SURFACE_REQUEST_STEPS: Final[str] = "request_steps"
PII_SURFACE_TELEMETRY: Final[str] = "telemetry"

# Default OFF — universal redaction is opt-in per-bot. Existing tenants
# keep `pii_redaction_enabled` (chat + ingest only) until they explicitly
# flip `pii_redaction_universal` to True. Universal coverage masks audit_log,
# request_steps metadata, telemetry events.
DEFAULT_PII_REDACTION_UNIVERSAL: Final[bool] = False



# Conservative VN PII regexes — tighten via system_config when needed.
PII_REGEX_CCCD: Final[str] = r"\b\d{12}\b"
PII_REGEX_CCCD_SPACED: Final[str] = r"\b\d{4}\s\d{4}\s\d{4}\b"
PII_REGEX_PHONE_VN: Final[str] = r"\b0\d{9,10}\b"
PII_REGEX_PHONE_VN_SPACED: Final[str] = (
    r"\b0\d{2,3}[\s.\-]\d{3,4}[\s.\-]\d{3,4}\b"
)
PII_REGEX_EMAIL: Final[str] = r"[\w.+-]+@[\w-]+\.[\w.-]+"
# Legacy 9-digit CMND (chứng minh nhân dân) — phased out 2021 but still
# present in legacy ingest material. Bare digit run guarded by word
# boundaries; the 12-digit CCCD pattern wins on overlap via the
# (start, -length) sort in the priority resolver.
PII_REGEX_CMND: Final[str] = r"\b\d{9}\b"
# VN-format bank account — generic 10-16 digit run. Domain-neutral: no
# bank-specific prefix literal. Overlap with CCCD/CMND/PHONE resolves
# via the length-priority sort so the specific class wins.
PII_REGEX_BANK_ACC: Final[str] = r"\b\d{10,16}\b"
# Phone with international +84 prefix. Companion to the 0xxx form
# already covered by ``PII_REGEX_PHONE_VN``.
PII_REGEX_PHONE_VN_INTL: Final[str] = r"\+84\d{9,10}\b"

# --- Y4 SECURITY MAX additions (2026-05-01) ---------------------------------
# Generic API/secret token shape — high-entropy alphanum bodies preceded by
# a credential-shaped prefix. Conservative length floor to avoid masking
# natural-language IDs in chat. Domain-neutral (no vendor literals).
PII_REGEX_API_KEY_GENERIC: Final[str] = (
    r"\b(?:sk|pk|rk|tok|key|api|bearer)[_\-]?[A-Za-z0-9]{16,}\b"
)
# Provider key prefixes by SHAPE (sk-, AIza, xox*-). Matches the published
# prefix grammar, not a brand name literal — domain-neutral compatible.
PII_REGEX_API_KEY_PROVIDER: Final[str] = (
    r"\b(?:sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z\-_]{20,}|"
    r"xox[abprs]-[0-9A-Za-z\-]{10,})\b"
)
# Postgres / MySQL / Mongo / Redis / AMQP DSN with inline password —
# common leak vector when error messages or stack traces hit the log/event
# stream.
PII_REGEX_DB_DSN: Final[str] = (
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)"
    r":\/\/[^\s:@]+:[^\s@]+@[^\s\/]+"
)
# JWT three base64url segments. ``eyJ`` is the base64url of ``{"`` — JWT
# headers always start with ``{"alg":...}`` so the prefix is stable.
PII_REGEX_JWT: Final[str] = (
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"
)
# Credit card — 13–19 digits with optional space/dash separators. Luhn
# validation lives downstream (regex layer keeps it cheap).
PII_REGEX_CREDIT_CARD: Final[str] = (
    r"\b(?:\d[ \-]?){13,19}\b"
)
# VN biển số xe — 2-digit province + 1-2 letter series + 4-5 digit number,
# optional dot before the last 2 digits ("51F 678.90", "29-A1 234.56").
PII_REGEX_VN_PLATE: Final[str] = (
    r"\b\d{2}[\s\-]?[A-Z]{1,2}\d?[\s\-]?\d{3,5}(?:\.\d{2})?\b"
)

# VN street-style address — keyword-anchored to "số nhà / ngõ / đường /
# phường / quận / thành phố / TP". Captures the keyword + following
# number-or-name token sequence up to the next comma / newline / "." +
# space. Domain-neutral: pattern keys on the Vietnamese postal vocabulary
# (loại / chỉ dẫn), not on any tenant-specific street or city literal.
#
# Examples covered:
#   "Số 12 Lê Lợi"          → match "Số 12 Lê Lợi"
#   "Đường Nguyễn Huệ"      → match "Đường Nguyễn Huệ"
#   "Phường 1, Quận 3"      → match "Phường 1" + "Quận 3"
#   "TP. Hồ Chí Minh"       → match "TP. Hồ Chí Minh"
#
# Conservative bounded length (max 80 chars) protects against catastrophic
# regex backtracking on adversarial input. Case-insensitive matching is
# handled at compile time by the recognizer (NOT inline) so the constant
# stays portable.
PII_REGEX_VN_ADDRESS: Final[str] = (
    r"(?:Số\s+nhà|Số|Ngõ|Ngách|Hẻm|Đường|Phố|Phường|Xã|Quận|Huyện|"
    r"Thành\s+phố|TP\.?|Tỉnh)"
    r"\s+[\wÀ-ỹ][\wÀ-ỹ\s\.\-/]{0,79}?"
    r"(?=[,;\n]|\.\s|$)"
)

# --- Y4 SECURITY MAX — response headers (2026-05-01) ------------------------
# OWASP baseline. Operator may override per environment via constructor
# kwargs; defaults are the conservative (most-restrictive) set.
DEFAULT_SECURITY_HEADERS_HSTS_ENABLED: Final[bool] = False
DEFAULT_SECURITY_HEADERS_HSTS_VALUE: Final[str] = (
    "max-age=31536000; includeSubDomains; preload"
)
DEFAULT_SECURITY_HEADERS_REFERRER_POLICY: Final[str] = (
    "strict-origin-when-cross-origin"
)
DEFAULT_SECURITY_HEADERS_CSP: Final[str] = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)
DEFAULT_SECURITY_HEADERS_PERMISSIONS_POLICY: Final[str] = (
    "camera=(), microphone=(), geolocation=(), payment=()"
)
# additional security headers (Cross-Origin isolation suite).
DEFAULT_SECURITY_HEADERS_COOP: Final[str] = "same-origin"
DEFAULT_SECURITY_HEADERS_CORP: Final[str] = "same-origin"
# COEP only applies to docs/Swagger (HTML routes). API endpoints emit
# their JSON response — COEP would block cross-origin script use of it.
DEFAULT_SECURITY_HEADERS_COEP_DOCS_ONLY: Final[str] = "require-corp"
DEFAULT_SECURITY_HEADERS_COEP_PATHS: Final[tuple[str, ...]] = (
    "/docs", "/redoc", "/openapi.json",
)
DEFAULT_SECURITY_HEADERS_PERMITTED_CROSS_DOMAIN: Final[str] = "none"
