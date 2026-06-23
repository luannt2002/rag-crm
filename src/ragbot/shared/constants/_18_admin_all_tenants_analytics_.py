from __future__ import annotations
from typing import Final  # noqa: F401
from ._17_260509_a1_pipeline_audit_6_c import *  # noqa: F401,F403

# --- Admin all-tenants analytics endpoint -----------------------------------
# Default page size when caller omits ``limit`` on /admin/analytics/all-tenants.
# Sized so a super-admin dashboard renders one screen of tenants without
# paging on a small platform; larger platforms page via ``sort_by`` ordering.
DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT: Final[int] = 50
# Default lookback window (days) when caller omits ``since``. Matches the
# per-tenant analytics default so operator dashboards stay consistent.
DEFAULT_ANALYTICS_ALL_TENANTS_WINDOW_DAYS: Final[int] = 7
# --- Admin workspace-aggregate analytics endpoint ---------------------------
# Default lookback window (days) when caller omits ``since`` on
# /admin/analytics/workspace-aggregate. Matches the sibling all-tenants /
# per-bot endpoints so operator dashboards remain consistent.
DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS: Final[int] = 7
# Hard cap to bound query cost (percentile_cont GROUP BY scans request_logs).
# Anything above this would need cursor pagination, not a wider limit.
MAX_ANALYTICS_ALL_TENANTS_LIMIT: Final[int] = 200
# Hard cap on rows returned by /admin/analytics/workspace-aggregate. Bounds
# query cost — percentile_cont GROUP BY (record_tenant_id, workspace_id)
# scans request_logs and the row count grows with bot fan-out per tenant.
MAX_ANALYTICS_WORKSPACE_RESULTS: Final[int] = 100

# --- sprint1-context-buffer-atomic (2 constants) ---
# --- Context Buffer for Atomic Blocks (AdapChunk L2) -----------
# AdapChunk Layer 2 pattern: populate ``Block.context_before`` +
# ``Block.context_after`` with 1-2 sentences of neighbouring TEXT content so
# atomic chunks (TABLE / FORMULA / IMAGE / CODE) carry their introducing +
# trailing prose into retrieval. Default ON (default==happy) — adds retrieval
# context with graceful degradation; affects new ingests only. Disable per-bot
# via ``context_buffer_atomic_enabled`` in ``system_config`` if needed.
DEFAULT_CONTEXT_BUFFER_ATOMIC_ENABLED: Final[bool] = True
# Sentence window per side (look-back + look-ahead). 2 captures the typical
# "Theo định lý / công thức..." intro + "Trong đó..." outro without bloating
# the chunk metadata. 1 loses context on multi-clause intros; >3 dilutes
# semantic signal and inflates storage.
DEFAULT_CONTEXT_BUFFER_SENTENCE_WINDOW: Final[int] = 2

# --- sprint1-databricks-complexity (4 constants) ---
# Databricks adaptive complexity sizing -----------------
# Reference: Databricks Technical Blog — Debu Sinha (2025-03)
# Method: lexical_density + sentence_length combined complexity → adaptive
# per-document chunk size. Complex text → smaller chunks; simple text →
# larger chunks. Feature gated by ``databricks_complexity_sizing_enabled``
# (default OFF — opt-in via system_config). Pure rule-based, no LLM call.
# Lexical density normalisation: divide ``unique/total`` by 0.8 so the typical
# prose ceiling (~0.6 unique-word ratio) maps to ~0.75 complexity. Matches the
# Databricks reference implementation.
DEFAULT_COMPLEXITY_LEX_DENSITY_NORM: Final[float] = 0.8
DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE: Final[int] = 1000
# Adaptive chunk size bounds. Min/max picked so the most-complex doc still
# leaves room for a few sentences per chunk, and the simplest doc does not
# blow past the platform-wide ``DEFAULT_CHUNK_MAX_SIZE``. Both bounds are
# per-bot overridable via ``system_config`` keys ``complexity_min_chunk_size``
# and ``complexity_max_chunk_size``.
DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE: Final[int] = 300
# Sentence length normalisation: avg sentence length in chars / 200 → [0, 1].
# Sentences > 200 chars (legal / technical-dense) saturate at 1.0.
DEFAULT_COMPLEXITY_SENTENCE_LEN_NORM: Final[float] = 200.0

# --- sprint1-docprofile-refine (9 constants) ---
# --- (AdapChunk Layer 3 — DocumentProfile refine) ------
# Inspired by internal AdapChunk Layer 3 blueprint + Ekimetrics LREC 2026
# proven adaptive-chunking metrics. Pure rule-based extraction (no LLM, no
# external lang-detect dep) so the analyzer stays cheap and deterministic
# in the ingest hot path.
# Feature flag governing whether the call site logs the enriched profile
# event and feeds the entity downstream. Default OFF — shipped
# the dict form; this stream wires the entity refine but keeps existing
# callers untouched until the operator opts in.
DEFAULT_ADAPCHUNK_LAYER3_DOC_PROFILE_ENABLED: Final[bool] = False
# Marker for code-fence boundary (Markdown ``` style). Each pair of
# markers = 1 code block. We count opening fences (toggling logic) in
# the analyzer rather than dividing by 2 to keep the count robust to
# unbalanced docs (the parser already normalizes most upstream cases).
DEFAULT_CODE_FENCE_MARKER: Final[str] = "```"
# Number of leading lines scanned for a TOC marker ("Mục lục", "Table of
# Contents"). 30 lines covers cover pages + a small front-matter block
# without sweeping the whole document — TOCs that sit deeper than this
# are vanishingly rare in the corpora the platform serves.
DEFAULT_DOC_PROFILE_TOC_SCAN_LINES: Final[int] = 30
# Regex for inline LaTeX formulas — matches `$...$` and `$$...$$` blocks.
# Used by ``analyze_document_profile`` to count formula occurrences as a
# proxy for math-heavy documents.
DEFAULT_FORMULA_INLINE_RE: Final[str] = r"\${1,2}[^$\n]+\${1,2}"
# Regex for Markdown image references — matches `![alt](url)` patterns
# (alt text optional). Used to count image references for layout-mix
# scoring; an HTML `<img>` tag is intentionally out of scope (parser
# normalizes to MD upstream).
DEFAULT_IMAGE_MD_RE: Final[str] = r"!\[[^\]]*\]\([^)]+\)"
# Fallback language tag when detection is ambiguous / document is too
# short / no alphabetic characters present. Downstream embedding paths
# already handle ``"auto"`` as multilingual.
DEFAULT_LANG_DETECT_FALLBACK: Final[str] = "auto"
# Minimum total alphabetic character count before language detection
# trusts the diacritic ratio. Below this, the document is too short
# to reliably classify — return ``"auto"`` rather than risk a wrong
# language tag locking in a downstream embed model.
DEFAULT_LANG_DETECT_MIN_ALPHA_CHARS: Final[int] = 40
# Vietnamese-diacritic detection ratio threshold. A document whose
# letter alphabet contains ≥ this fraction of VN diacritic characters
# (ăâđêôơưáàảãạ…) is classified ``"vi"``. Below this, fallback to
# ``"auto"`` (the platform's ASR-neutral language sentinel).
DEFAULT_VN_DIACRITIC_RATIO: Final[float] = 0.02
# Set of Vietnamese-specific lowercase diacritic characters (not present
# in plain ASCII or English). Used by the language detector to compute
# the diacritic ratio. Tuple keeps it immutable + cheap to iterate.
VN_DIACRITIC_CHARS: Final[tuple[str, ...]] = (
    "ă", "â", "đ", "ê", "ô", "ơ", "ư",
    "á", "à", "ả", "ã", "ạ",
    "ấ", "ầ", "ẩ", "ẫ", "ậ",
    "ắ", "ằ", "ẳ", "ẵ", "ặ",
    "é", "è", "ẻ", "ẽ", "ẹ",
    "ế", "ề", "ể", "ễ", "ệ",
    "í", "ì", "ỉ", "ĩ", "ị",
    "ó", "ò", "ỏ", "õ", "ọ",
    "ố", "ồ", "ổ", "ỗ", "ộ",
    "ớ", "ờ", "ở", "ỡ", "ợ",
    "ú", "ù", "ủ", "ũ", "ụ",
    "ứ", "ừ", "ử", "ữ", "ự",
    "ý", "ỳ", "ỷ", "ỹ", "ỵ",
)

# --- sprint1-kreuzberg-parser (3 constants) ---
# Memory-safety byte ceiling — mirrors DEFAULT_PDF_MAX_BYTES so a scanned
# upload routed through Kreuzberg cannot exceed what the worker would
# accept via the native pypdfium2 path. Operators raise via system_config
# when running larger budgets.
DEFAULT_KREUZBERG_MAX_BYTES: Final[int] = 10 * 1024 * 1024
# Tesseract language codes. ``vie+eng`` chosen because the default
# deployment language is "vi" (Vietnamese) and English co-occurs in most
# scanned legal/medical PDFs; operators override per system_config when
# their corpus is single-language.
DEFAULT_KREUZBERG_OCR_LANGUAGE: Final[str] = "vie+eng"
# MIMEs claimed by Kreuzberg. Frozen at constant-time so the OCRPort impl
# can return the exact same set without re-allocating per call.
KREUZBERG_SUPPORTED_MIMES: Final[tuple[str, ...]] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/tiff",
)

# --- sprint2-late-chunking-sliding (4 constants) ---
DEFAULT_LATE_CHUNKING_LONG_DOC_THRESHOLD_CHARS: Final[int] = 16000
DEFAULT_LATE_CHUNKING_OVERLAP_CHARS: Final[int] = 2048
# --- Late Chunking Sliding Window (long-doc late chunking) ------------------
# Sliding-window late chunking for long documents that exceed the embedder's
# single-pass context window. Without it, a single late_chunking call sends the
# WHOLE document to the embedder; once the doc passes the window, the tail
# chunks fall "beyond the truncation window" and the provider rejects the batch
# (Jina v3 → HTTP 422 "could not be tokenized for late_chunking"). Sliding
# instead processes the doc in overlapping windows so every window fits.
# Sizing: Jina v3 late-chunk window is DEFAULT_JINA_LATE_CHUNK_WINDOW_TOKENS
# (7800 tokens). Mixed VN + URL-heavy corpora tokenize at ~2.5–3 chars/token
# (URLs/diacritics inflate token count), so a SAFE window is ~16000 chars
# (≈ 5300–6400 tokens) with margin under 7800 — NOT 32768 chars, which assumed
# an optimistic 4 chars/token and overflowed on real catalog data. Overlap 2048
# chars preserves cross-window continuity. Threshold mirrors the window so docs
# below it use the single-pass fast path. Enabled by default: sliding is
# strictly safer for long docs and a no-op for short ones. Config via system_config.
DEFAULT_LATE_CHUNKING_SLIDING_ENABLED: Final[bool] = True
DEFAULT_LATE_CHUNKING_WINDOW_CHARS: Final[int] = 16000

# Embedders reject empty / whitespace-only inputs (Jina v3 → HTTP 422
# "could not be tokenized"). Some chunking strategies (e.g. table_dual_index
# group/divider rows) can linearise a chunk to whitespace; embedding it is
# pointless but it must not abort the whole document. Such inputs are
# substituted with this neutral, tokenizable placeholder so the batch is
# accepted — the resulting vector carries no real signal and never matches a
# genuine query. Domain-neutral; value is irrelevant beyond being non-empty.
DEFAULT_EMPTY_EMBED_FALLBACK_TEXT: Final[str] = "blank"

# --- sprint2-narrate-then-embed (7 constants) ---
# Anthropic Batch API discount factor (50% off at time of writing).
DEFAULT_NARRATE_BATCH_DISCOUNT_FACTOR: Final[float] = 0.5
# Anthropic Message Batches API per-batch message cap (platform side).
DEFAULT_NARRATE_BATCH_SIZE: Final[int] = 100
# Default OFF — never auto-enrol an operator into a paid API path.
DEFAULT_NARRATE_BATCH_USE: Final[bool] = False
# Bounded concurrency for the per-chunk narrate dispatch. Each chunk's
# narrate_chunk() is an independent LLM round-trip with no cross-chunk data
# dep, so they fan out concurrently (perf: a 50-chunk doc drops from ~N×latency
# serial to ~ceil(N/this)×latency). Semaphore caps in-flight LLM calls so a
# wide doc cannot saturate the provider pool (CLAUDE.md Async Rule 6).
DEFAULT_NARRATE_MAX_CONCURRENCY: Final[int] = 20
# Which block_type labels are eligible for narration when the feature
# flag is on. Prose-like blocks (HEADING / TEXT / CODE / LIST) embed
# fine raw — narrating them adds cost without recall benefit.
NARRATE_BLOCK_TYPES_DEFAULT: Final[tuple[str, ...]] = ("TABLE", "FORMULA", "IMAGE")
NARRATE_METADATA_KEY_BLOCK_TYPE: Final[str] = "block_type"
# Chunk-metadata keys for dual-content storage. Single source of truth
# so retrieval / eval / admin debug tooling reads them consistently.
NARRATE_METADATA_KEY_NARRATED_TEXT: Final[str] = "narrated_text"
NARRATE_METADATA_KEY_RAW_CHUNK: Final[str] = "raw_chunk"

