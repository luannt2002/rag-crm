from __future__ import annotations
from typing import Final  # noqa: F401
from ._23_crm_analytics_readlayer_ import *  # noqa: F401,F403

# --- Per-language document-structure markers (multi-language hardening) ------
# The chunker + retrieval pre-filter detect and promote document hierarchy
# markers ("Chương III" / "Mục 2" / "Điều 13" for Vietnamese legal/admin
# prose). Those marker literals used to be hardcoded inline in
# ``shared/chunking/vn_structural.py``, which silently left non-Vietnamese
# bots (EN / JP) with no structural vocabulary at all. The literal sets now
# live here, keyed by language code, so a bot's locale selects its own marker
# alternation instead of the Vietnamese one.
#
# Resolution is constants-only (NOT a runtime DB read): the chunk + intent
# hot paths run per-document / per-query, so a DB round-trip would add an
# answer-path latency cost for zero tunability gain (these are structural
# vocabulary, not tenant content). Operators that need a new language extend
# this dict — one entry, no code change.
#
# CANONICAL FORM: each tuple holds the Title-case canonical prefix the chunk
# paths store. ``vn_structural`` builds its regex alternation + canonical map
# from the ``vi`` tuple, so the ``vi`` entry MUST stay byte-identical to the
# pre-refactor hardcoded Vietnamese values — changing it changes VN behaviour.
DEFAULT_STRUCTURAL_MARKERS_BY_LANG: Final[dict[str, tuple[str, ...]]] = {
    # vi: byte-identical to the prior hardcoded set in vn_structural.py
    # (_VN_HEADING_DETECT_RE / _VN_STRUCTURAL_QUERY_DETECT_RE alternation +
    # _VN_PREFIX_CANONICAL keys). Order: Chapter, Part, Section, Article.
    "vi": ("Chương", "Phần", "Mục", "Điều"),
    # en: English legal/admin document hierarchy. Enables an EN bot's chunker
    # to promote "Chapter II" / "Section 3" / "Article 5" headings the same
    # way VN promotes Chương/Mục/Điều.
    "en": ("Part", "Chapter", "Section", "Article", "Clause"),
    # ja: placeholder — left empty until a JP corpus + marker set is curated.
    # Empty tuple = no structural promotion for JP (flat chunking), which is
    # the safe default (never the Vietnamese literals).
    "ja": (),
}

# Default language used when a caller does not specify a locale. Vietnamese is
# the historical platform default, so omitting a language keeps the existing
# VN behaviour byte-for-byte.
DEFAULT_STRUCTURAL_MARKERS_LANG: Final[str] = "vi"

# --- Per-language aggregation / list-all keywords ---------------------------
# Intent signals that a query asks for an enumeration / aggregation
# ("list all", "how many", "compare") rather than a single factoid. The
# Vietnamese set mirrors the tokens already curated in
# RANGE_QUERY_PATTERNS_VI / SUMMARY_QUERY_PATTERNS_VI; the EN set gives an
# English bot equivalent detection. JP left empty (placeholder).
#
# DOMAIN-NEUTRAL: linguistic enumeration words only — no brand / service /
# industry tokens. These are case-folded by the consumer before matching.
DEFAULT_AGGREGATION_KEYWORDS_BY_LANG: Final[dict[str, tuple[str, ...]]] = {
    "vi": (
        "tất cả",
        "liệt kê",
        "bao nhiêu",
        "có bao nhiêu",
        "toàn bộ",
        "so sánh",
        "tổng cộng",
        "tổng quan",
        # Superlatives / extrema need the WHOLE set scanned to be correct
        # (max/min over all rows) — same retrieval-completeness need as a list.
        "đắt nhất",
        "rẻ nhất",
        "mắc nhất",
        "cao nhất",
        "thấp nhất",
        "nhiều nhất",
        "ít nhất",
    ),
    "en": (
        "all",
        "list",
        "list all",
        "how many",
        "compare",
        "every",
        "overview",
        "total",
        "most expensive",
        "cheapest",
        "highest",
        "lowest",
        "maximum",
        "minimum",
    ),
    "ja": (),
}
