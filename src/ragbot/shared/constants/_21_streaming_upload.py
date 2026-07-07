from __future__ import annotations
from typing import Final  # noqa: F401
from ._20_cag_mode_cache_augmented_gen import *  # noqa: F401,F403

# --- Streaming upload (WB-2 P1-5) ------------------------------------------
# Partner-facing /documents/upload-stream pushes large bodies (>50MB scanned
# binders, compliance archives) directly to a temp file.  The full-load
# ``await request.body()`` path peaks at >2x file size in resident memory
# (Starlette buffer + framework copy + parser bytes view); streaming caps
# resident memory at the chunk size regardless of upload bytes.  Hard cap
# defends the host from disk-fill DoS even before the per-tenant quota
# kicks in.
DEFAULT_UPLOAD_STREAM_MAX_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MiB
# 1 MiB read window — balance between syscall overhead (smaller) and
# resident memory peak (larger). Matches the default block size used by
# Python's tempfile and aiofiles helpers.
DEFAULT_UPLOAD_STREAM_CHUNK_SIZE: Final[int] = 1024 * 1024  # 1 MiB
# Temp dir lives under ``/tmp`` so the OS reclaims orphaned chunks on
# reboot; ``mkdir -p`` is the route's responsibility.  Partner data never
# survives a host crash even if the cleanup branch is skipped.
DEFAULT_UPLOAD_TEMP_DIR: Final[str] = "/tmp/ragbot_uploads"
# Redis Stream subject for upload-worker hand-off.  The route ``XADD``s
# the temp-file pointer + 4-key identity; a separate worker (PHASE 2)
# consumes, runs the parser registry, persists chunks, and unlinks the
# temp file.  ``.v1`` matches existing ``SUBJECT_DOCUMENT_*`` naming
# convention (wire-protocol topic suffix — not a code version-ref).
SUBJECT_DOCUMENT_UPLOAD_STREAM: Final[str] = "document.upload_stream.v1"

# --- Stats Index (document_service_index table) ----------------------------
# Maximum rows returned from a single stats-index list/query call.
DEFAULT_STATS_INDEX_QUERY_LIMIT: Final[int] = 1000
# Reverse/token keyword fallback (query_by_name_keyword): when the forward match
# (entity name CONTAINS the keyword) finds nothing, match entities whose NAME is
# a word INSIDE the query — e.g. query "Item A variant combo" vs entity "Variant".
# Min entity-name length so 1-3 char zone words ("Var", "sub") can't over-match;
# small result cap so a granular price-of-entity lookup stays focused, not a flood.
DEFAULT_STATS_REVERSE_MATCH_MIN_LEN: Final[int] = 4
# A SHORTER name is still accepted in the reverse fallback when the keyword ENDS
# with it — a category-qualified zone like "Item A sub" → zone "Sub" (3 chars).
# The trailing position disambiguates the real target (the zone, at the end) from
# a category word in the MIDDLE ("lông"), which a plain CONTAINS match over-picks.
# Floor of 3 keeps 1-2 char tokens out; trailing-anchored so noise stays bounded.
DEFAULT_STATS_REVERSE_MATCH_SHORT_FLOOR: Final[int] = 3
DEFAULT_STATS_REVERSE_MATCH_LIMIT: Final[int] = 10
# Min keyword length for the LAST-RESORT attributes_json fallback: a code / SKU /
# date the corpus keeps in a non-role column (Mã, stock, date, image) lives in
# attributes_json, which the name/synonym match never sees. The fallback fires
# only when forward AND reverse matched nothing, and only for a keyword this long
# so a short token can't over-match every row sharing a warehouse/category word.
DEFAULT_STATS_ATTRS_MATCH_MIN_LEN: Final[int] = 5
# Serve-side SHELL filter (truth-audit option (b), decision record
# specs/001-rag-truth-audit/evidence/decision_shell_entities.md): customer-facing
# stats queries exclude entities that carry NO price AND NO value-bearing
# attribute — such "shell" rows (name+aliases only) served next to a priced
# same-size sibling caused 45/45 wrong-brand price answers in the N=15 baseline.
# Rows whose attributes hold a real value (arrival date, stock, ...) are KEPT —
# date questions are answered from price-less rows. Per-bot opt-out via
# plan_limits["stats_serve_require_value"].
DEFAULT_STATS_SERVE_REQUIRE_VALUE: Final[bool] = True
# Threshold below which a numeric token is ignored by the price extractor
# (avoids treating article numbers like "Điều 12" → 12 as prices).
DEFAULT_STATS_PRICE_MIN_DIGITS: Final[int] = 4
# Price bucket boundaries (VND) used by aggregate_summary.
# Bucket keys are generated from these thresholds at call-time so names
# stay in sync with values. Override at runtime via system_config.
DEFAULT_PRICE_BUCKETS_VND: Final[tuple[int, ...]] = (
    500_000,    # "under_500k"
    1_000_000,  # "under_1M"
    2_000_000,  # "under_2M"
    5_000_000,  # "under_5M"
)
# Minimum amount considered a valid price (filters ordinal numbers / row IDs).
DEFAULT_PRICE_MIN_VND: Final[int] = 10_000
# Maximum amount considered a valid price (filters dates / timestamps / barcodes
# leaking into a price column — e.g. a Google-Sheet serial "2025122435548").
# 500M VND comfortably covers any single catalog item; a real price above this is
# vanishingly rare and not worth poisoning every range query for.
DEFAULT_PRICE_MAX_VND: Final[int] = 500_000_000

# --- Stats-index query routing (B3 — Self-Query Retrieval) ------------------
# Max rows returned from document_service_index when routing an
# aggregation / comparison query via SQL instead of vector retrieve.
DEFAULT_STATS_INDEX_LIMIT: Final[int] = 100
# Parser confidence floor — parse_range_query results below this
# threshold are ignored and the pipeline falls back to vector retrieve.
RANGE_QUERY_MIN_CONFIDENCE: Final[float] = 0.7
# --- Superlative aggregation ("đắt nhất" / "rẻ nhất" → ORDER BY price) -------
# A price-superlative query carries no numeric bound, so parse_range_query
# returns a RangeFilter with operation "max"/"min" and null bounds; the stats
# route then runs ORDER BY price DESC/ASC LIMIT K against the clean
# document_service_index (not a re-parse of raw retrieved chunks, which fails
# on CSV price formats like "Item A,1234000"). Domain-neutral.
SUPERLATIVE_QUERY_CONFIDENCE: Final[float] = 0.8
# --- Code/spec lookup ("mã A1/B2C3 còn hàng?" → name/category ILIKE) -------
# A query carrying a product/spec CODE (e.g. "A1/B2C3", "2-X17", a SKU /
# part number) is a single-record lookup: the user wants the row for that
# exact code (stock / restock-date / price), not a fuzzy vector neighbour
# (which returns a near-duplicate code's row → wrong record). When such a code
# is present and no price/list/superlative signal applies, route to the
# clean structured index via query_by_name_keyword(code). Domain-neutral:
# keyed on "a code token is present", never on a bot/brand/corpus literal.
CODE_QUERY_CONFIDENCE: Final[float] = 0.8
# Master switch (per-bot override: pipeline_config 'stats_code_lookup_enabled').
# Fail-soft: a bot whose stats index has no row matching the code gets zero
# entities → the route returns nothing → falls back to vector retrieve.
DEFAULT_STATS_CODE_LOOKUP_ENABLED: Final[bool] = True
# BUG-1 CONFLATE fix: route "<entity> giá bao nhiêu" price-of-entity factoids to
# the structured name lookup (1 entity = 1 labelled price) instead of the vector
# path. Per-bot opt-out via plan_limits.stats_price_of_entity_enabled.
DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED: Final[bool] = True
# Sentinel chunk_id for the stats-route synthetic context chunk. The stats rows
# carry no DB chunk FK, so the synthetic chunk would otherwise have an EMPTY
# chunk_id — and the generate node DROPS any chunk whose id is falsy from the
# <documents> block (guards against malformed rows). An empty id therefore made
# the authoritative stats answer invisible to the LLM → false "không tìm thấy"
# refuse on an in-stock product. A non-empty sentinel keeps the synthetic chunk
# in context. Not a real DB id (no FK), just a stable structural marker.
DEFAULT_STATS_SYNTHETIC_CHUNK_ID: Final[str] = "stats_index_synthetic"
# Spec/product-code token detector for the code-lookup route. A code is an
# alphanumeric run joined by one of / . - (e.g. A1/B2C3, 2-X17, A1.B2) — a
# format no natural-language word takes. Universal token shape, not corpus
# data, so it stays domain-neutral. Mirrors the BM25 symbol-phrase token in
# pgvector_store so the same codes that need exact BM25 matching also route to
# the structured index. Operators override via system_config 'code_query_pattern'.
DEFAULT_CODE_QUERY_PATTERN: Final[str] = (
    r"[A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+"
)
# Master switch (per-bot override: pipeline_config 'stats_superlative_enabled').
# Fail-soft: a bot whose stats index has no priced rows (e.g. a legal corpus)
# gets zero entities → the route returns nothing → falls back to vector.
DEFAULT_STATS_SUPERLATIVE_ENABLED: Final[bool] = True
# Rows returned for a superlative query — small so the LLM sees the ranked
# head ("the 5 most expensive"), not the whole price table.
DEFAULT_STATS_SUPERLATIVE_LIMIT: Final[int] = 5
# Max chars of a single structured attribute value surfaced into the stats
# synthetic chunk. Skips mega-cells (e.g. a 64-synonym variant column) that
# would dilute the chunk while keeping normal fields (answer/quantity/date).
DEFAULT_STATS_ATTR_MAX_CHARS: Final[int] = 120
# Max WORDS of a surfaced attribute value — a real field (price/date/"30 phút",
# or a full product name like "Product Item A A1/B2C3 91H Variant G/P")
# is a short phrase; a mis-captured paragraph is many words. The char cap above
# (120) is the primary bound; this word cap only skips short-but-wordy free-text
# noise. Kept generous enough that a real product name / title (≈6-10 words) is
# surfaced — dropping it strips the one human-readable label the LLM needs to
# map the row to the owner's answer schema. Override via system_config.
DEFAULT_STATS_ATTR_MAX_WORDS: Final[int] = 12
# Fallback structural-reference detector for the stats_index guard. The guard
# normally relies on the injected metadata_filter_strategy to detect an
# article/clause anchor and skip stats routing — but that strategy is None on
# the default path, leaving the guard a no-op (a "Điều 34 giá" query then wrongly
# routes to stats_index and bypasses the exact-article fetch). This regex is the
# always-on fallback: universal DOCUMENT-STRUCTURE vocabulary (not brand/domain
# data), so it stays domain-neutral. Operators can override via system_config
# 'structural_ref_fallback_pattern'. Matches a structural word + number.
DEFAULT_STRUCTURAL_REF_FALLBACK_PATTERN: Final[str] = (
    r"(?i)\b(điều|khoản|điểm|chương|mục|tiết|article|section|clause|chapter|"
    r"paragraph|art\.?|sec\.?)\s*\.?\s*\d+"
)
# Safety timeout (seconds) for the stats-vs-vector race: both tasks are
# cancelled and the fallback path runs if neither completes within this
# window.  Kept generous (3 s) to accommodate slow SQL on cold cache;
# vector retrieve p95 is ~700 ms so the race always resolves well before
# the downstream LLM call.  Per-bot override: ``stats_race_timeout_s``
# in pipeline_config.  Default OFF — enable per-bot via
# ``stats_index_race_enabled = true`` in pipeline_config.
DEFAULT_STATS_RACE_TIMEOUT_S: Final[float] = 3.0
# Race mode is opt-in per-bot so existing deployments keep the current
# sequential gate (stats first → if empty, vector) until explicitly
# enabled.  Flip to True in system_config to make it the platform
# default once A/B validates recall improvement.
DEFAULT_STATS_INDEX_RACE_ENABLED: Final[bool] = False
# Vietnamese range-query signal patterns used by parse_range_query.
# Parser compares the diacritic-folded query against these tokens;
# adding a new pattern = extend this tuple, no code change needed.
RANGE_QUERY_PATTERNS_VI: Final[tuple[str, ...]] = (
    "dưới",
    "trên",
    "từ",
    "đến",
    "ít hơn",
    "nhỏ hơn",
    "lớn hơn",
    "cao hơn",
    "thấp hơn",
    "khoảng",
    "không quá",
    "tối đa",
    "có bao nhiêu",
    "liệt kê",
)
# Summary-query patterns — when any of these tokens appears in the query
# the pipeline routes to doc-level summary_json instead of chunk retrieve.
SUMMARY_QUERY_PATTERNS_VI: Final[tuple[str, ...]] = (
    "tóm tắt",
    "tổng quan",
    "tổng cộng",
    "tất cả",
    "toàn bộ",
    "overview",
    "summarize",
    "summarise",
)
# --- Heuristic Intent Classifier (Layer 1 latency opt) ----------------------
# Regex-based fast-path for common Vietnamese query patterns. When a pattern
# matches with confidence >= threshold the LLM understand_query call is
# skipped entirely (saves ~1.6s p50). LLM fallback activates for any query
# where no pattern matches OR confidence is below the threshold.
#
# HALLU=0 sacred: the heuristic is a SKIP gate — it only fires on clear-signal
# intents (greeting, chitchat). Ambiguous or domain queries always fall through
# to the LLM path. Threshold 0.85 keeps false-positive rate < 1% per manual
# audit of 50-turn VN probe set. Bot owners cannot lower threshold via config
# (would break HALLU sacred).
HEURISTIC_INTENT_CONFIDENCE_THRESHOLD: Final[float] = 0.85
# Two-tier classifier confidence, straddling the threshold above:
#   STRONG (anchored greeting/chitchat) > THRESHOLD → skip LLM (zero-retrieval,
#     100% safe to fast-path).
#   WEAK (mid-string aggregation/multi_hop/comparison) < THRESHOLD → the gate
#     forces the LLM check. These patterns can appear inside a domain query, so
#     the heuristic is a hint only and must NOT short-circuit validation.
# The prior code set the WEAK tier to the SAME 0.85 as the threshold, so
# ``0.85 >= 0.85`` skipped the LLM for exactly the intents that needed it —
# the opposite of the documented intent. Keeping the tiers as distinct
# constants that straddle the floor makes the relationship explicit.
HEURISTIC_INTENT_CONFIDENCE_STRONG: Final[float] = 0.90
HEURISTIC_INTENT_CONFIDENCE_WEAK: Final[float] = 0.80
# Flag lets ops disable the heuristic layer per-bot without redeploying.
# Default ON — measured latency saving 1.4-1.6s on greeter / chitchat turns.
DEFAULT_HEURISTIC_INTENT_ENABLED: Final[bool] = True

# --- Grounding check truly parallel flag ------------------------------------
# When True, `_schedule_grounding_check_background` wraps the coroutine in
# asyncio.create_task so the judge runs concurrently with response shipping
# rather than as a deferred sequential call. Decouples HALLU guard latency
# from user-visible response time entirely.
DEFAULT_GROUNDING_CHECK_TRULY_PARALLEL: Final[bool] = True

# --- Guard output parallel 3-checks flag ------------------------------------
# When True the three output guard checks (PII, math_lockdown, leak) that are
# currently serial are dispatched via asyncio.gather so total latency is
# max(t_pii, t_math, t_leak) instead of sum. Safe to parallelise: each check
# reads state (immutable during guard_output) and writes only guardrail_flags
# (list — merged additively post-gather). Falls back to serial when flag is
# False (default True post-validation).
DEFAULT_GUARD_OUTPUT_PARALLEL_ENABLED: Final[bool] = True

# --- Served-chunks persistence (truth-audit verification, alembic served_chunks_260703)
# Cap the per-turn chunk list persisted with the assistant message so a fat
# retrieval (top-20 wide net) cannot bloat chat_histories. Items = chunks the
# LLM actually saw (post-grade), chars = content head per chunk.
SERVED_CHUNKS_PERSIST_MAX_ITEMS: Final[int] = 12
SERVED_CHUNKS_PERSIST_MAX_CHARS: Final[int] = 400

# 002-C: cap per-sub-query stats lookups when a decomposed comparison joins
# the fan-out (bounds DB round-trips; comparisons are 2-3 legs in practice).
DEFAULT_DECOMPOSE_STATS_MAX_SUBS: Final[int] = 4

# 002-B: trace-harness chunk capture cap. 500 chars blinded the grader (the
# exact-SKU row sat past the alias megacell → 4 wrongful sai_bia verdicts);
# the capture's own docstring promises "grader sees exactly what the LLM saw".
# Capped (not unlimited) to keep evidence JSONs committable; every truncation
# now carries an explicit flag so no verdict may rely on a cut chunk.
TRACE_CHUNK_CAPTURE_MAX_CHARS: Final[int] = 2000

# 002-F: explicit price-absent marker in the stats synthetic chunk. When a
# served set mixes priced rows with price-LESS rows, a null-price entity used
# to emit NO price field at all — so the LLM silently borrowed the neighbour
# row's number (truth-audit 002 B-001: NEO 195/65R16 price-NULL → answered with
# adjacent Rovelo 1.350.000). Emitting an explicit structural absent-marker for
# the price column gives the LLM the missing "this cell IS empty" signal, so it
# can honour the owner's "chưa có giá" behaviour instead of guessing. The marker
# is a structural description of an empty cell — same category as the ``price:``
# label already emitted for present values (QG#10-safe: describes DATA, injects
# no behaviour). Language-neutral em-dash so it never reads as a corpus/brand
# literal or a real number. Override via system_config for a bot that prefers a
# different convention.
STATS_NULL_PRICE_MARKER: Final[str] = "—"

# ADR-0008 A1: serve the SHAPE-detected descriptive name (e.g. "Lốp Rovelo
# 195/55R16 …") as the stats entity's identity instead of the raw ``entity_name``
# when that turned out to be an internal code ("2-R16 195/55 LPD"). The name is
# picked from the entity's OWN field values by value-shape (zero vocab, zero
# model) — see ``shared/table_shape.pick_descriptive_name``. Default OFF: the
# legacy field-like name path is byte-identical until a bot opts in.
DEFAULT_STATS_NAME_BY_SHAPE: Final[bool] = False

# ADR-0008 A2/B3: narrow the stats candidate set by the query's DISCRIMINATING
# tokens so a brand-named spec query ("giá lốp Rovelo 195/55R16") is not served a
# same-size row of a DIFFERENT brand (the size-code keyword alone is brand-blind).
# Domain-neutral: uses the candidate set itself as the dictionary (a token some
# candidates carry and others don't = a brand/model word) — zero vocab, zero
# stopword list. See shared/table_shape.discriminating_token_filter. Default OFF.
DEFAULT_STATS_BRAND_AWARE: Final[bool] = False
