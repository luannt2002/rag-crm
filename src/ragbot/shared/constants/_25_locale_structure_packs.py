"""Per-locale document-STRUCTURE packs (P0-3 multilang structure word-lists).

Language is DATA, not code: every word-list / regex / script-range that a
structure-deciding path needs is keyed by ISO language code here, so a non-VN
bot resolves its OWN set instead of silently inheriting the Vietnamese
literals. Resolution is constants-only (no DB read): these are structural
vocabulary on the ingest / profile hot path, not tenant content.

What lives here:

* ``DEFAULT_DOTTED_LEADER_TOC_RE`` — a language-AGNOSTIC structural
  Table-of-Contents line detector (a dot-leader run followed by a trailing
  page number, e.g. ``Introduction ........ 3`` / ``Mở đầu . . . 7``). It
  complements the literal ``mục lục`` / ``table of contents`` markers so a TOC
  page in ANY language is detectable by shape alone.
* ``DEFAULT_STATS_DISCOURSE_OPENERS_BY_LANG`` /
  ``DEFAULT_STATS_CLAUSE_OPENER_FIRST_BY_LANG`` — prose-opener word-sets the
  table/CSV stats extractor uses to reject a sentence mis-split into a row.
  The ``vi`` entry is byte-identical to the prior hardcoded frozensets in
  ``shared/document_stats.py``; ``en`` adds an English equivalent; ``ja`` is an
  empty placeholder (no VN leak).
* ``DEFAULT_LANG_SCRIPT_RANGES`` — Unicode codepoint ranges per language used
  by the rule-based profiler to classify a document by SCRIPT (e.g. Japanese
  kana) instead of a VN-vs-auto diacritic binary.
* ``LOCALE_STRUCTURE_MARKERS`` — a thin alias of
  ``DEFAULT_STRUCTURAL_MARKERS_BY_LANG`` (defined in the sibling
  ``_24_structural_markers_by_lang`` module) so callers that want the full
  locale-structure surface import it from one place. The marker tuples are NOT
  re-declared here — single source of truth stays in ``_24`` (zero drift).

DOMAIN-NEUTRAL: linguistic / structural grammar only — no brand, service, or
industry literal. All defaults are SSoT here; per-bot behaviour differs by the
resolved language code, never by hardcoded per-bot logic.

ADD A LANGUAGE = add one entry to each dict below + the matching marker tuple
in ``_24_structural_markers_by_lang`` — no code change in the structure paths.
"""
from __future__ import annotations

from typing import Final

from ._24_structural_markers_by_lang import (
    DEFAULT_STRUCTURAL_MARKERS_BY_LANG,
    DEFAULT_STRUCTURAL_MARKERS_LANG,
)

# --- Structural (vocabulary-free) Table-of-Contents line detector -----------
# A dot-leader TOC entry is a title followed by a run of leader dots (contiguous
# ``....`` OR spaced ``. . .``) and then a trailing page number at end of line:
#   "Introduction ............ 3"
#   "Chapter 1: Overview . . . . . . 12"
#   "Mục 2 .......... 7"
# This is the universal print/word-processor TOC convention, independent of the
# document language — so it catches TOC pages whose words are NOT the literal
# ``mục lục`` / ``table of contents`` markers. Requiring ≥ 3 leader dots AND a
# trailing integer keeps it off prose ("a normal sentence."), dotted thousands
# prices ("1.234.000"), version strings ("1.2.3"), and URLs ("www.x.com 2024").
# Stored as a pattern STRING (SSoT); consumers compile it once at module load.
DEFAULT_DOTTED_LEADER_TOC_RE: Final[str] = r"(?:\.\s?){3,}\s*\d{1,4}\s*$"

# --- Per-locale prose-opener word-sets (table/CSV stats extractor) ----------
# A catalog entity name never IS a temporal adverb nor STARTS with a clause
# conjunction — only a prose sentence mis-split into a row does. These sets let
# the extractor reject such rows by grammar, per the document's language.
#
# vi: byte-identical to the prior hardcoded frozensets in document_stats.py
# (``_STATS_DISCOURSE_OPENERS`` / ``_STATS_CLAUSE_OPENER_FIRST``). Changing the
# ``vi`` entry changes VN behaviour — keep it exact.
DEFAULT_STATS_DISCOURSE_OPENERS_BY_LANG: Final[dict[str, frozenset[str]]] = {
    "vi": frozenset({"hiện tại", "hiện nay", "bây giờ", "tuy nhiên"}),
    "en": frozenset(
        {"currently", "now", "however", "meanwhile", "nowadays", "today"}
    ),
    "ja": frozenset(),
}
DEFAULT_STATS_CLAUSE_OPENER_FIRST_BY_LANG: Final[dict[str, frozenset[str]]] = {
    "vi": frozenset({"khi", "nếu", "vì", "tuy", "do", "bởi"}),
    "en": frozenset(
        {"when", "if", "because", "although", "since", "while"}
    ),
    "ja": frozenset(),
}

# --- Per-language Unicode script ranges (rule-based language detection) ------
# Each language maps to a tuple of inclusive ``(lo, hi)`` codepoint ranges that
# are UNAMBIGUOUS for it. A document with ≥ 1 character in a language's range is
# classified that language by script (the profiler counts hits and picks the
# dominant script before falling back to the diacritic-ratio binary).
#
# ja: Hiragana + Katakana (U+3040–U+30FF). Kana never appears in VN/EN/CJK-Han
# text, so even one kana char is a reliable Japanese signal. Han (U+4E00–U+9FFF)
# is intentionally NOT listed for ``ja`` here because Han alone is shared with
# Chinese; kana is the disambiguator. Latin / VN-diacritic languages have no
# dedicated script range (they are detected by the diacritic-ratio fallback),
# so they are absent by design.
DEFAULT_LANG_SCRIPT_RANGES: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "ja": ((0x3040, 0x30FF),),
}

# --- Convenience alias: the full per-locale structural-marker surface --------
# Single source of truth stays in ``_24_structural_markers_by_lang`` — this is a
# read-only alias so a caller can import the marker map from the same module as
# the other locale packs without the tuples being declared twice (zero drift).
LOCALE_STRUCTURE_MARKERS: Final[dict[str, tuple[str, ...]]] = (
    DEFAULT_STRUCTURAL_MARKERS_BY_LANG
)

# Default locale used when a caller does not specify one — mirrors
# ``DEFAULT_STRUCTURAL_MARKERS_LANG`` so every locale-pack resolution shares one
# default (Vietnamese), keeping the historical happy-path byte-identical.
DEFAULT_LOCALE_STRUCTURE_LANG: Final[str] = DEFAULT_STRUCTURAL_MARKERS_LANG
