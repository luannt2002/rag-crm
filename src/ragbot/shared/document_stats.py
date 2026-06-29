"""[T1-Smartness] Document stats extractor — deterministic Python parser for table/CSV chunks.

Pattern: industry-verified Stats Index (Pinecone / AI21 metadata filter).
LLM HALLU risk = 0 because parser is pure Python regex with no LLM calls.

Design principles (CLAUDE.md):
- HALLU=0 sacred: all numeric extraction is deterministic regex, never LLM.
- Domain-neutral: parser is generic for table/CSV format (header + data rows).
  No hardcoded service names, languages, or bot names.
- Zero-hardcode: bucket boundaries imported from shared/constants.py.
- No broad-except: narrow ValueError only.

Usage (ingest pipeline — Agent B2 wires this):
    from ragbot.shared.document_stats import parse_table_chunks, aggregate_summary
    entities = parse_table_chunks(chunks)   # chunks = list[dict] from DB
    summary  = aggregate_summary(entities)  # for documents.summary_json
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ragbot.shared.constants import (
    DEFAULT_LOCALE_STRUCTURE_LANG,
    DEFAULT_PRICE_BUCKETS_VND,
    DEFAULT_PRICE_MAX_VND,
    DEFAULT_PRICE_MIN_VND,
    DEFAULT_STATS_ATTR_MAX_CHARS,
    DEFAULT_STATS_ATTR_MAX_WORDS,
    DEFAULT_STATS_CLAUSE_OPENER_FIRST_BY_LANG,
    DEFAULT_STATS_DISCOURSE_OPENERS_BY_LANG,
)
from ragbot.shared.number_format import parse_money_vn as _canonical_parse_money
from ragbot.shared.tabular_markdown import _is_pure_money

# Bullet/list-marker leads. A first cell starting with one of these is a prose
# DESCRIPTION line ("- Mô tả công dụng dài, nhiều mệnh đề …"), not a catalog entity —
# it merely contains a comma so the row-splitter mistakes it for a table row.
# Domain-neutral: markdown/plain bullet syntax, no corpus literal.
_STATS_BULLET_LEADS: frozenset[str] = frozenset({"-", "•", "*", "–", "—", "●", "·", "▪", "+"})

# A real catalog entity name never LEADS with a "<bareword>: " metadata key. A
# search-synonym / key-value source row (a warehouse export "question: <variant
# spellings>", "date1: 26", "quantity: 29") comma-splits to a short col[0] that
# passes the field-like guard and floods the stats index with synonym/metadata
# noise. Reject on the prefix SHAPE — domain-neutral, no header literal.
# A multi-word name with a LATER colon ("Đơn giá gói N: …") is not a metadata
# lead (the colon does not immediately follow the first word) and survives.
_STATS_METADATA_LEAD_RE: re.Pattern[str] = re.compile(r"^\w+:\s")

# An image / link column mis-picked as the entity name (Google-Drive URL cells in
# the warehouse export). Keyed on URL/link SHAPE — scheme, domain/path, or the
# image-dimension param — never a brand. Domain-neutral.
_STATS_URL_NOISE_RE: re.Pattern[str] = re.compile(
    r"https?://|[\w-]+\.(?:com|net|org|vn|io|co)/|=w\d+-h\d+|auditcontext",
    re.IGNORECASE,
)

# Extraction-artefact name SHAPES that are never a catalog entity — domain-neutral
# (grammar / structure only, no service/brand/price literal):
#   - tag lead: a leaked structure tag ("<chunk_context>…") mis-read as a name.
#   - section/step enumeration lead: a roman/numbered outline marker ("II/ …",
#     "Bước 1: …") — a heading, not a priced row.
#   - discourse/temporal opener: a consultation-script SENTENCE comma-split into a
#     row leaves a grammar opener as the "name" ("Hiện tại …", "Khi đến với …") with
#     an incidental number mis-read as a price — a real HALLU source from a
#     consultation-script corpus.
# Deliberately NO short-code rule (^[A-Z/+]{2,5}$): it false-drops real all-caps
# service/package codes (IPL/VIP/HIFU). The shapes below are unambiguous.
_STATS_TAG_LEAD: str = "<"
_STATS_SECTION_LEAD_RE: re.Pattern[str] = re.compile(
    r"^([IVXLCDM]+\s*[/.)]|\w+\s+\d+\s*[:.])", re.IGNORECASE
)


def _discourse_openers(lang: str = DEFAULT_LOCALE_STRUCTURE_LANG) -> frozenset[str]:
    """Resolve the temporal-adverb opener set for *lang* (P0-3 locale pack).

    Default locale (``vi``) returns the EXACT prior frozenset, so VN extraction
    is byte-identical; an unknown locale resolves to the empty set (no VN leak).
    """
    return DEFAULT_STATS_DISCOURSE_OPENERS_BY_LANG.get(lang, frozenset())


def _clause_opener_first(lang: str = DEFAULT_LOCALE_STRUCTURE_LANG) -> frozenset[str]:
    """Resolve the clause-conjunction first-word set for *lang* (P0-3 locale pack).

    Default locale (``vi``) returns the EXACT prior frozenset (byte-identical);
    an unknown locale resolves to the empty set (no VN leak).
    """
    return DEFAULT_STATS_CLAUSE_OPENER_FIRST_BY_LANG.get(lang, frozenset())


def _is_discourse_opener(
    label: str, lang: str = DEFAULT_LOCALE_STRUCTURE_LANG
) -> bool:
    """True when *label* IS a temporal adverb or STARTS with a clause conjunction.

    A catalog entity name never does; only a prose sentence mis-split into a row
    does ("Hiện tại …", "Khi đến với …"). The opener word-sets are resolved
    per-locale from the P0-3 locale packs (``lang`` defaults to ``vi`` →
    byte-identical VN grammar). Domain-neutral.
    """
    low = label.lower().strip()
    if low in _discourse_openers(lang):
        return True
    parts = low.split()
    return bool(parts) and parts[0] in _clause_opener_first(lang)


def _is_delimited_list_cell(cell: str) -> bool:
    """True when a long cell is a DELIMITED VALUE LIST (a synonym/alias blob like
    ``"code-a, code a, brand code-a, …"``), NOT a prose sentence.

    Many separators + short segments = a list; a prose sentence of the same length
    has few commas and long clauses. Domain-neutral (punctuation shape only, no
    corpus literal). Lets a long ALIASES column sitting in col-0 coexist with a real
    NAME in a later column instead of the prose-noise guard dropping the whole row
    (a real catalog's identifier is a short id/name column, not a huge synonym blob
    that some exports put in the first column)."""
    seps = cell.count(",") + cell.count(";")
    if seps < 3:  # noqa: PLR2004 — minimum separators for a list vs a prose clause
        return False
    segs = [s.strip() for s in re.split(r"[,;]", cell) if s.strip()]
    if len(segs) < 3:  # noqa: PLR2004 — at least 3 value segments
        return False
    # Every segment is short (value-like, not a sentence clause).
    return all(len(s.split()) <= DEFAULT_STATS_ATTR_MAX_WORDS for s in segs)

# ---------------------------------------------------------------------------
# Money-format regex patterns — Vietnamese currency conventions.
#
# Supported formats (all produce an integer VND value):
#   "1.234.000"  — dotted thousands (Vietnamese locale)
#   "1,234,000"  — comma thousands (Western locale)
#   "1234000"    — bare integer (4-8 digits)
#   "1tr234"     — Vietnamese shorthand: 1 triệu + 234 (= 1,000,000 + 234,000)
#   "499k"       — k-suffix (kilos = ×1,000)
#   "1.5tr"      — decimal-triệu (1,500,000)
#   "1M"         — M-suffix (= ×1,000,000 for English-language sheets)
#
# Negative amounts are explicitly not matched — no valid price is negative.
# ---------------------------------------------------------------------------

# Money parsing delegates to the canonical platform NUMBER STANDARD
# (shared.number_format) so the corpus price extracted at ingest and the
# range/superlative filter parsed at query time agree on every format
# ("1.200.000", "700,000", "500k", "5000 nghìn", "1tr499", "1.5tr", "1M").

# ---------------------------------------------------------------------------
# Header detection heuristics.
# A row is a header when ALL non-empty cells contain only label words (no
# money values) AND at least one cell exactly matches a known column label
# (after accent normalisation). Substring containment is too broad because
# entity names like "Service A" would match the "service" token.
# ---------------------------------------------------------------------------
# ════════════════════════════════════════════════════════════════════════════
# INPUT SCOPE — SINGLE SOURCE OF TRUTH (happy-case column vocabulary)
# ════════════════════════════════════════════════════════════════════════════
# These role token sets DEFINE what column headers the platform parses cleanly.
# They are THE scope: the happy-case spec (docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md)
# documents them and the format checker (scripts/check_happy_case.py) IMPORTS them —
# so spec, checker, and parser can never drift. Add a header alias = add it HERE.
# A document whose headers fall outside these sets is "out of scope" → the checker
# flags it and the customer fixes the SOURCE (we do NOT grow the parser per format).
#
# Column-ROLE token sets (normalised, accent-stripped) — generic domain-neutral
# grammar/structure words, NO service/brand literal. SOTA cell-role (TATR / Docling
# row-header): name → entity-name column, category → stub/group column, price → value.
_NAME_COL_TOKENS: frozenset[str] = frozenset({
    "ten", "name", "dich vu", "service", "san pham", "goi", "combo",
    "ten dich vu", "ten san pham", "ten goi", "product", "item",
    # G1 synonyms — common header variants kept here so spec + checker stay in sync.
    "ten hang", "ten mat hang", "ten san pham/dich vu", "mat hang",
    "ten sp", "sp", "hang hoa", "ten goi dich vu", "product name", "item name",
})
_CATEGORY_COL_TOKENS: frozenset[str] = frozenset({
    "nhom", "danh muc", "category", "loai", "vung", "type", "khu vuc",
    # G1 synonyms — group/stub/brand columns. "kho"/"ten kho" = warehouse stub
    # (NOT the product name) — pins the 'Tên kho' ⊥ 'Tên hàng' disambiguation.
    "kho", "ten kho", "kho hang", "phan loai", "thuong hieu", "nhan hieu",
    "hang san xuat", "hsx", "brand", "group", "nhom san pham",
})
_PRICE_COL_TOKENS: frozenset[str] = frozenset({
    "gia", "price", "phi", "amount", "cost",
    "don gia", "gia le", "gia goc", "gia sale", "gia ban", "thanh tien", "unit price",
    # G1 synonyms — price column variants.
    "don gia ban", "gia ban le", "gia niem yet", "gia tien", "gia ban (vnd)",
    "gia (vnd)", "selling price", "list price",
})
# An ALIASES/synonym column: ``;``-separated search variants for the entity (a size
# asked in a different notation, a spelling/typo variant). Captured into
# ParsedEntity.aliases → entity_synonyms so query_by_name_keyword matches an alias
# even when entity_name uses another notation. Generic search/keyword grammar words,
# domain-neutral — NO product/brand literal. Normalised (accent-stripped).
_ALIASES_COL_TOKENS: frozenset[str] = frozenset({
    "aliases", "synonym", "synonyms", "tu khoa", "keyword", "keywords",
    "bien the", "variant", "variants",
})
# Non-role header words (ordinals / ids) — used for header DETECTION only, no role.
_HEADER_EXTRA_TOKENS: frozenset[str] = frozenset({
    "stt", "buoi", "no", "id", "qty", "quantity",
})
# Exact-match (normalised) column label keywords — union of all roles, for
# _is_header_row(). Generic, domain-neutral.
_HEADER_EXACT_TOKENS: frozenset[str] = (
    _NAME_COL_TOKENS | _CATEGORY_COL_TOKENS | _PRICE_COL_TOKENS
    | _ALIASES_COL_TOKENS | _HEADER_EXTRA_TOKENS
)
# Aggregate / structural-label words that are NEVER a catalog entity name — a
# transposed / key-value / total row promotes one of these to a "name" ("Giá"=100k,
# "Tổng tiền"=300k). Reject on EXACT normalised match (NOT prefix) so a real name
# carrying a label word ("Giá vàng" → "gia vang" ≠ "gia"; "Tổng hợp dịch vụ" →
# "tong hop dich vu" ≠ "tong") is NOT dropped. Generic accounting terms (VN+EN),
# domain-neutral — covers the common multi-word total labels, not just "tong cong".
_AGGREGATE_TOKENS: frozenset[str] = frozenset({
    "tong", "tong cong", "tong tien", "tong gia", "tam tinh",
    "total", "grand total", "subtotal", "cong", "thuoc tinh", "chi so",
})
_REJECT_NAME_TOKENS: frozenset[str] = _HEADER_EXACT_TOKENS | _AGGREGATE_TOKENS

# Separator-line detection: a line is a separator when every comma/pipe-split
# field matches only dashes, equals, or spaces.
_SEP_FIELD_RE = re.compile(r"^[\-=\s]*$")


def _normalise(text: str) -> str:
    """Lower-case + accent-strip for header token matching.

    Uses unicodedata.normalize(NFD) to decompose accented characters, then
    removes Mark (Mn) combining characters. Deterministic, no LLM.
    """
    nfkd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def parse_money_vn(text: str) -> int | None:
    """Parse Vietnamese money format to integer VND.

    Supported:
      - "1.234.000" / "1,234,000"  → dotted/comma thousands
      - "1234000"                  → bare integer (4-8 digits)
      - "1tr234"                   → 1,000,000 + 234,000 = 1,234,000
      - "1.5tr"                    → 1,500,000
      - "234k" / "234K"            → 234,000
      - "1M"                       → 1,000,000

    Returns None when:
      - No money pattern is found.
      - Value < DEFAULT_PRICE_MIN_VND (filters ordinal numbers / SKU codes).
      - The number is preceded by a minus sign (negative prices are invalid).

    Design note: This function extracts the FIRST money value found in the
    input string. Callers that need all prices from a row should split columns
    first and call once per cell. The ingest floor ``DEFAULT_PRICE_MIN_VND``
    keeps ordinal/SKU numbers (row index 3, year 2024) out of the price index.
    """
    return _canonical_parse_money(
        text, min_value=DEFAULT_PRICE_MIN_VND, max_value=DEFAULT_PRICE_MAX_VND,
    )


@dataclass(frozen=True)
class ParsedEntity:
    """A single row extracted from a table/CSV chunk.

    Fields:
        name:            The entity name (col 0 or first non-numeric col).
        category:        Optional category label from a preceding header group.
        price_primary:   First price column (lowest / single-session price), VND int.
        price_secondary: Second price column (e.g. combo / package price), VND int.
        chunk_index:     Index of the source chunk in the input list.
        attributes:      Remaining key→value pairs from extra columns.
        aliases:         ``;``-separated search variants from an Aliases/synonym
                         column (size notations, spelling variants). ``None`` when
                         the catalog has no aliases column. Written to the
                         ``entity_synonyms`` search column at ingest so a query in a
                         different notation still matches the entity.
    """

    name: str
    category: str | None
    price_primary: int | None
    price_secondary: int | None
    chunk_index: int
    attributes: dict[str, Any] = field(default_factory=dict)
    aliases: str | None = None


def _next_nonempty_is_separator(lines: list[str], idx: int) -> bool:
    """True when the next non-empty line after ``idx`` is a separator (| --- |).

    The structure-aware converter (``tabular_markdown``) emits a separator
    directly UNDER every header it detects, so a row sitting above one IS the
    header — trusted structurally, in any language/domain, with zero vocabulary.
    This is the primary fix for the ``col_N`` CRUX (dual-oracle drift): the
    extractor stops re-judging by a word-list and trusts the converter's signal.
    """
    for k in range(idx + 1, len(lines)):
        if not lines[k].strip():
            continue
        return _is_separator_line(lines[k])
    return False


def _is_header_row(
    cols: list[str],
    declared_labels: frozenset[str] | set[str] = frozenset(),
    *,
    next_is_separator: bool = False,
) -> bool:
    """Return True if this row looks like a column-label header.

    STRUCTURAL detection (THE ONE LAW — shape, not vocabulary):
    1. No cell may contain a parseable value/money — a data row has values; a
       header has only labels (value-contrast). [unchanged]
    2. ``next_is_separator`` — a ≥2-cell row sitting directly above a ``| --- |``
       separator IS the header, in ANY language / domain, with ZERO vocabulary.
       A data row is never positioned above a separator, so this cannot
       over-promote. This is the col_N fix for out-of-vocabulary headers in any
       language (e.g. ``MARKS | CARGO DESCRIPTION``, Spanish ``Producto | Precio``).

    Vocabulary (``_HEADER_EXACT_TOKENS``) and the per-bot ``declared_labels`` are
    a positive HINT ONLY — they rescue a header that reaches the extractor with no
    separator (e.g. hand-written markdown). They are NEVER the sole gate; lexical
    gating was the col_N bug for every non-VN / non-VND header. The happy path
    (known VN/EN vocab or owner-declared labels) stays byte-identical.
    """
    has_label_match = False
    non_empty = 0
    for col in cols:
        if not col or not col.strip():
            continue
        non_empty += 1
        if parse_money_vn(col) is not None:
            # Data row (has a value/price cell) → not a header.
            return False
        normalised = _normalise(col.strip())
        if normalised in _HEADER_EXACT_TOKENS or normalised in declared_labels:
            has_label_match = True
    # Structural floor: trust the converter's separator (zero-vocab, any language).
    if next_is_separator and non_empty >= 2:  # noqa: PLR2004 — a header needs ≥2 label cells
        return True
    # Hint fallback: known vocab / owner-declared label (happy-path byte-identical).
    return has_label_match


def _is_separator_line(line: str) -> bool:
    """Return True when the line is a Markdown/ASCII separator row.

    Handles both pipe-style (| --- | --- |) and bare (---,---,---) forms.
    """
    # Pipe-delimited separator
    if "|" in line:
        fields = [f.strip() for f in line.split("|") if f.strip()]
        return bool(fields) and all(_SEP_FIELD_RE.match(f) for f in fields)
    # Comma-delimited separator
    if "," in line:
        fields = [f.strip() for f in line.split(",")]
        return bool(fields) and all(_SEP_FIELD_RE.match(f) for f in fields)
    # Plain separator line (all dashes / equals)
    return bool(re.match(r"^[\-=\s]+$", line))


def _split_cols(line: str) -> list[str]:
    """Split a CSV / TSV / pipe-delimited line into stripped columns."""
    # Pipe branch — ONLY for a genuine Markdown pipe-table row. Such a row starts
    # with ``|`` (the converter's _md_escape + table format always emits a leading
    # pipe and escapes any literal in-cell pipe as ``\|``). A raw-CSV chunk row
    # (what the table_csv / table_dual_index chunkers persist) starts with a value
    # or a quote, NEVER ``|`` — but its cells may contain a LITERAL pipe (e.g. a
    # synonym/Aliases column "a; b | code: X | price: 684000"). Gating on a leading
    # pipe stops that literal from hijacking the split (which glued name+price into
    # one over-long cell → name-guard reject → 0 entities, the price-loss bug).
    # Non-pipe lines fall through to the RFC-4180 CSV branch below.
    if line.lstrip().startswith("|"):
        parts = [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", line)]
        # Strip leading/trailing empty parts from Markdown pipe tables
        if parts and not parts[0]:
            parts = parts[1:]
        if parts and not parts[-1]:
            parts = parts[:-1]
        return parts
    # Tab separator
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    # Comma separator (common for CSV chunks). Parse with the csv module so a
    # quoted field that itself contains commas stays ONE column. A naive
    # line.split(",") shatters a quoted cell (e.g. a "code-variant, variant, …"
    # synonym list) into N phantom columns, shifting every real column right so
    # the header labels no longer align with their values (quantity/price/date
    # end up holding garbage, the real numbers land in col_N). RFC-4180 quoting
    # is domain-neutral — no corpus/bot assumption.
    try:
        row = next(csv.reader(io.StringIO(line)))
    except (csv.Error, StopIteration):
        # Malformed quoting → fall back to the naive split so a single bad
        # line cannot abort extraction for the whole document.
        return [c.strip() for c in line.split(",")]
    return [c.strip() for c in row]


def _role_match_score(token: str, words: set[str], role_tokens: frozenset[str]) -> int:
    """Affinity of a normalised header ``token`` to a role's token set.

    Cascade (no LLM, deterministic, domain-neutral): EXACT membership (100) >
    multi-word phrase substring (60, e.g. 'don gia' inside 'don gia ban') >
    whole-word match (30, e.g. 'gia' is a word of 'gia ban'). 0 = no signal.
    The tiers keep the exact happy-case path winning outright while letting an
    unseen header variant still bind instead of being silently dropped.
    """
    if token in role_tokens:
        return 100
    best = 0
    for rt in role_tokens:
        if " " in rt and rt in token:
            best = max(best, 60)
        elif rt in words:
            best = max(best, 30)
    return best


# Per-bot ``custom_vocabulary["column_roles"]`` role-name vocabulary (ADR-0006
# Tier 2). The owner declares what a column MEANS — the engine stays domain-neutral
# (it never hardcodes "RAM" or "Tồn kho"). Free-form owner role strings are folded
# to the four internal roles; ``attribute`` is an explicit "no special role, keep it
# a generic searchable attribute" that also SUPPRESSES inference on that column.
_CUSTOM_ROLE_ALIASES: dict[str, str] = {
    "name": "name",
    "value": "price", "price": "price",
    "category": "category",
    "aliases": "aliases", "alias": "aliases",
    "attribute": "attribute", "attr": "attribute",
}


def _normalise_custom_roles(custom_roles: dict[str, str] | None) -> dict[str, str]:
    """Fold an owner ``{header_label: role}`` map to ``{norm_label: internal_role}``.

    Both the header label and the role string are accent/case folded so the owner
    can write free-form ("Giá bán" → "Value"). An unknown role string is dropped
    (that column falls through to inference), never raised — a typo in a per-bot
    config must not abort ingest. Domain-neutral: code knows the four ROLE names,
    never the owner's column meanings.
    """
    out: dict[str, str] = {}
    if not custom_roles:
        return out
    for label, role in custom_roles.items():
        if not label or not isinstance(role, str):
            continue
        canon = _CUSTOM_ROLE_ALIASES.get(_normalise(role.strip()))
        if canon:
            out[_normalise(str(label).strip())] = canon
    return out


def _column_roles(
    header: list[str], custom_roles: dict[str, str] | None = None
) -> dict[str, Any]:
    """Assign each header column a ROLE by its normalised token: the NAME column, a
    CATEGORY/stub column, the PRICE column(s), and an ALIASES/synonym column. Lets a
    data row bind its entity to the right column instead of blindly taking col-0 —
    which may be a category stub (``Nhóm | Tên | Giá`` → name is col-1, not the "Cao
    cấp" group in col-0). The aliases role pulls a ``;``-separated search-variant
    column out of the generic attributes dump and into ``ParsedEntity.aliases`` so it
    becomes a searchable key. SOTA cell-role (Microsoft TATR / Docling row-header).
    Returns ``{"name": idx|None, "category": idx|None, "price": [idx, ...],
    "aliases": idx|None}``.

    Role resolution is a 3-tier cascade (ADR-0006), precedence high→low:
      * Tier 2 — per-bot ``custom_roles`` (owner-declared) is AUTHORITATIVE and wins
        over inference; the engine stays domain-neutral (it reads the declaration,
        it does not hardcode column meanings).
      * Tier 1 — structural/vocab inference (the G1 cascade below).
      * Tier 3 — every other column → generic ``attributes`` (downstream default).
    """
    declared = _normalise_custom_roles(custom_roles)
    # G1 cascade: each header scores against each role by exact (100) >
    # phrase-substring (60) > whole-word (30). A header binds to its strictly-best
    # role; a TIE (e.g. "Tên kho" scoring name via "ten" AND category via "kho")
    # is SKIPPED so an ambiguous stub can't steal a role. Exact-vocab
    # still wins outright, so the happy-case path is byte-identical.
    _roles_def = (
        ("name", _NAME_COL_TOKENS),
        ("category", _CATEGORY_COL_TOKENS),
        ("aliases", _ALIASES_COL_TOKENS),
        ("price", _PRICE_COL_TOKENS),
    )
    # ``price`` is a LIST (multiple price columns); name/category/aliases are
    # single-valued, first-wins. One ``_bind`` ladder is shared by both tiers so the
    # Tier-2 and Tier-1 assignment can't drift (and keeps the branch count low).
    single_idx: dict[str, int | None] = {"name": None, "category": None, "aliases": None}
    price_idxs: list[int] = []
    # Columns the OWNER explicitly declared as a generic ``attribute`` (no special
    # role). They must keep their value as a labelled attribute and — crucially —
    # NEVER be hijacked by the unknown-pure-money→price fallback: a stock/count the
    # owner tagged ``attribute`` (e.g. "Số lượng tồn" = 40400) is NOT a price even
    # though it parses as one. Late-binding / attribute-generic: the owner's
    # declaration is authoritative over a numeric-shape guess.
    attr_idxs: list[int] = []

    def _bind(role: str, i: int) -> None:
        if role == "price":
            price_idxs.append(i)
        elif single_idx.get(role) is None:
            single_idx[role] = i

    for i, cell in enumerate(header):
        token = _normalise(cell.strip()) if cell else ""
        if not token:
            continue
        # Tier 2 (authoritative): owner-declared role wins outright over inference.
        # ``attribute`` is an explicit no-role → record the index so the row
        # extractor keeps it a generic attribute AND suppresses the pure-money price
        # fallback for that column.
        forced = declared.get(token)
        if forced is not None:
            if forced == "attribute":
                attr_idxs.append(i)
            else:
                _bind(forced, i)
            continue
        # Tier 1: structural/vocab inference (G1 cascade).
        words = set(token.split())
        scores = {role: _role_match_score(token, words, toks) for role, toks in _roles_def}
        top = max(scores.values())
        if top == 0:
            continue
        winners = [r for r, s in scores.items() if s == top]
        if len(winners) > 1:
            continue  # ambiguous (name⊥category tie) → bind nothing, avoid mis-pick
        _bind(winners[0], i)
    return {
        "name": single_idx["name"], "category": single_idx["category"],
        "price": price_idxs, "aliases": single_idx["aliases"],
        "attribute": attr_idxs,
    }


def _extract_entity_from_row(
    cols: list[str],
    header: list[str],
    chunk_index: int,
    current_category: str | None,
    roles: dict[str, Any] | None = None,
) -> ParsedEntity | None:
    """Build a ParsedEntity from a split data row.

    Role-aware (domain-neutral): when the header assigns column roles, the NAME
    comes from the name column, prices from PRICE columns, and the CATEGORY/stub
    column is skipped (its value is forward-filled by the caller). With no roles it
    falls back to POSITIONAL (first non-money col = name). A cell is a PRICE only when
    it is PURELY money (``_is_pure_money``) so a name carrying a money phrase ("Gói 6
    triệu") is no longer misread as a value and dropped. Returns None when the row
    yields no field-like catalog name.
    """
    if not cols or all(c == "" for c in cols):
        return None

    name: str | None = None
    price_primary: int | None = None
    price_secondary: int | None = None
    aliases: str | None = None
    attributes: dict[str, Any] = {}

    name_idx = roles.get("name") if roles else None
    cat_idx = roles.get("category") if roles else None
    alias_idx = roles.get("aliases") if roles else None
    price_cols = set(roles["price"]) if roles and roles.get("price") else None
    # Owner-declared generic-attribute columns — the pure-money price fallback is
    # SUPPRESSED here so a numeric value the owner tagged ``attribute`` (a stock /
    # count that parses as money) is kept a labelled attribute, never a fake price.
    attr_cols = set(roles["attribute"]) if roles and roles.get("attribute") else None
    # A category/stub column is a SEPARATE axis only when a distinct NAME column
    # also exists (``Nhóm | Tên | Giá``). In a 2-col ``Vùng | Giá`` the category-token
    # column IS the entity name — skipping it would drop the row (no name left).
    if name_idx is None:
        cat_idx = None

    for idx, col in enumerate(cols):
        if not col:
            continue
        if cat_idx is not None and idx == cat_idx:
            # Category/stub column — its GROUP label is resolved + forward-filled into
            # ``entity.category`` by the caller. But the per-row VALUE must still be
            # retained as a labelled attribute (late-binding / attribute-generic): a
            # header that the inference reads as a category may actually hold a generic
            # value the owner queries by ("Tồn kho" stock-count mis-bound by the "kho"
            # warehouse token). Surfacing it under its own header keeps every column
            # equal — no value is dropped because of a role guess. Skipped when the
            # cell is blank or duplicates the name. Domain-neutral (label = the corpus
            # header), zero floor applied (a stock/count is NOT a price).
            _cat_val = col.strip()
            if _cat_val and _cat_val != name:
                _cat_hdr = (
                    header[idx].strip() if idx < len(header) and header[idx] else ""
                )
                attributes.setdefault(_cat_hdr or f"col_{idx}", _cat_val)
            continue
        if alias_idx is not None and idx == alias_idx:
            # ALIASES/synonym column → its dedicated field, NOT name/price/attributes.
            # Keeps the ``;``-separated search variants out of the name slot (they must
            # never become the entity name) and out of the attributes dump (they go to
            # the searchable ``entity_synonyms`` column instead). Empty cell → None.
            aliases = col.strip() or None
            continue

        # Price detection is column-role aware:
        #   * KNOWN price column → parse even when the cell carries extra words
        #     ("500k/buổi", "từ 500k", "Giá: 300k"); the column context already
        #     asserts it is a price, so the pure-money guard would only lose data.
        #   * UNKNOWN column (no header role) → require PURE money so a NAME that
        #     merely contains a money phrase ("Gói 6 triệu") is not read as a value.
        #   * OWNER-DECLARED ``attribute`` column → NEVER a price; the owner's
        #     declaration overrides the numeric shape (a stock/count tagged
        #     ``attribute`` that parses as money stays a labelled attribute).
        if attr_cols is not None and idx in attr_cols:
            label = header[idx] if idx < len(header) else f"col_{idx}"
            attributes[label] = col
            continue
        if price_cols is not None and idx in price_cols:
            # KNOWN price column → parse even with extra words ("500k/buổi").
            money = parse_money_vn(col)
        elif _is_pure_money(col):
            # UNKNOWN column but a PURE-money cell → still a price (a 2nd price column
            # whose header is out-of-vocab would otherwise drop to a string attribute
            # and never reach price_secondary). The pure-money gate keeps a NAME that
            # merely contains a money phrase ("Gói 6 triệu") from being read as a value.
            money = parse_money_vn(col)
        else:
            money = None
        if money is not None:
            if price_primary is None:
                price_primary = money
            elif price_secondary is None:
                price_secondary = money
            else:
                # Third+ price column → attributes
                label = header[idx] if idx < len(header) else f"price_{idx}"
                attributes[label] = money
            # Also surface the price under its COLUMN HEADER so the synthetic
            # list/keyword chunk keeps the column semantics that the positional
            # price_primary/secondary drop: a "gói N buổi" query needs to see
            # "Đơn giá gói N: 1199000", not a bare price_secondary the LLM
            # can't attribute (a combo-query attribution miss). Domain-neutral — the label IS
            # the corpus header, no hardcoded term; skipped when the header is a
            # number / blank, and ``setdefault`` keeps a 3rd+ labelled price.
            _hdr = header[idx].strip() if idx < len(header) and header[idx] else ""
            if _hdr and parse_money_vn(_hdr) is None:
                attributes.setdefault(_hdr, money)
            continue

        # Non-money cell. The name comes from the NAME column (role-aware) or the
        # first eligible column (positional fallback); everything else → attributes.
        eligible_name = name is None and (name_idx is None or idx == name_idx)
        if eligible_name:
            stripped = col.strip()
            # Skip pure ordinal row numbers (1, 2, 3 … or 1. 2. etc.)
            if re.match(r"^\d{1,3}\.?$", stripped):
                continue
            # OUT-OF-SCOPE DEFENSE: skip an over-long first cell — a search-synonym /
            # description blob (a warehouse export's synonym column, 1000+ chars) is
            # NOT a template name. Happy-case names are short, so this never fires on
            # in-scope data; it only salvages a non-template row whose source should
            # really be restyled. Keep the blob as an attribute (stays searchable).
            if len(stripped) > DEFAULT_STATS_ATTR_MAX_CHARS:
                attributes[header[idx] if idx < len(header) else f"col_{idx}"] = stripped
                continue
            name = stripped
        else:
            label = header[idx] if idx < len(header) else f"col_{idx}"
            attributes[label] = col

    # Role/data misalignment guard: a header with a leading empty column gives a
    # name_idx that points one cell past an un-padded data row, so the role-aware
    # name lands on a price/blank and the row would drop. When the name column
    # yielded no name, fall back to the positional first field-like, non-money,
    # non-role cell — recovering ragged rows WITHOUT changing aligned rows (where
    # ``name`` is already set, so this never fires).
    if name is None and name_idx is not None:
        for idx, col in enumerate(cols):
            stripped = (col or "").strip()
            if not stripped or _is_pure_money(stripped):
                continue
            if re.match(r"^\d{1,3}\.?$", stripped):
                continue
            if (
                (price_cols is not None and idx in price_cols)
                or (cat_idx is not None and idx == cat_idx)
                or (alias_idx is not None and idx == alias_idx)
                or (attr_cols is not None and idx in attr_cols)
            ):
                continue
            if len(stripped) <= DEFAULT_STATS_ATTR_MAX_CHARS:
                name = stripped
                break

    # ══ OUT-OF-SCOPE DEFENSE — NOT happy-case behaviour, defence-in-depth only ══
    # A row that conforms to the template (short name cell + a price column) NEVER
    # trips the guard below — it is a NO-OP on in-scope data. It exists solely to
    # reject NON-template rows that slip in before the checker is wired as a
    # pre-ingest gate: a prose/description/FAQ line that merely contains a delimiter
    # (bullet "- mô tả …", long FAQ sentences, name-less stray-number cells).
    # The RIGHT fix for such input is at the DATA layer (restyle the source to the
    # template) — we do NOT grow parser tolerance to absorb arbitrary formats.
    # Domain-neutral: bullet/grammar SHAPE + shared word/char caps, no brand literal.
    if name is not None:
        _lead = name.lstrip()[:1]
        _label = name.lstrip("-•*–—●·▪+ \t").strip()
        # Strip balanced surrounding quotes ("Item A, bản đặc biệt" → Item A, …) left
        # by an upstream CSV cell that kept its quoting. Only when both ends match.
        if len(_label) >= 2 and _label[0] == '"' and _label[-1] == '"':  # noqa: PLR2004
            _label = _label[1:-1].strip()
        if (
            not _label
            or _lead in _STATS_BULLET_LEADS
            or _label.startswith(_STATS_TAG_LEAD)
            or len(_label) > DEFAULT_STATS_ATTR_MAX_CHARS
            or len(_label.split()) > DEFAULT_STATS_ATTR_MAX_WORDS
            or _STATS_METADATA_LEAD_RE.match(_label)
            or _STATS_SECTION_LEAD_RE.match(_label)
            or _STATS_URL_NOISE_RE.search(_label)
            or _is_discourse_opener(_label)
            or _normalise(_label) in _REJECT_NAME_TOKENS
            or _is_pure_money(_label)
        ):
            name = None
        else:
            name = _label

    # Row-level prose-noise guard: when the NAME was taken from a LATER column
    # (role-based name_idx skipped col0), the row's natural LEAD cell must still be a
    # structured field. A bullet / discourse-opener / long-prose lead means the whole
    # row is a description that merely comma-split — positional extraction caught this
    # via col0, role-based must too (else "- mô tả …, nhiều mệnh đề, …" leaks the
    # clean middle column as a fake entity). Domain-neutral: same SHAPE guards.
    if name is not None:
        first_cell = next((c.strip() for c in cols if c.strip()), "")
        if first_cell and first_cell != name and (
            first_cell[:1] in _STATS_BULLET_LEADS
            or _is_discourse_opener(first_cell)
            or (
                len(first_cell) > DEFAULT_STATS_ATTR_MAX_CHARS
                # A long first cell drops the row ONLY when it is PROSE. A long
                # DELIMITED list (an aliases/synonym blob in col-0) is legitimate —
                # the real name lives in a later column; keep the row.
                and not _is_delimited_list_cell(first_cell)
            )
        ):
            name = None

    # A row with no field-like catalog name is not an entity — its number (if any)
    # is a prose figure, not a labelled catalog price. Previously kept (name="")
    # which surfaced empty-named price rows as noise.
    if name is None:
        return None

    return ParsedEntity(
        name=name or "",
        category=current_category,
        price_primary=price_primary,
        price_secondary=price_secondary,
        chunk_index=chunk_index,
        attributes=attributes,
        aliases=aliases,
    )


# A real catalog row ends on a price / code / short field, never on a sentence
# terminator; a prose sentence does. Grammar/punctuation only — domain-neutral.
_STATS_SENTENCE_END: tuple[str, ...] = (".", "!", "?", "…", "。")

# A markdown section heading ("## Nhóm dịch vụ A") — the authoritative B3
# section title emitted by the structure-aware parser.
_MD_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,6}\s+(.+?)\s*$")
# A single-col line carrying a thousands-grouped number ("Đơn giá: 1.600.000 đ")
# is a price NOTE, not a section title — must not become a category. Shape-only.
_STATS_PRICE_NOTE_RE: re.Pattern[str] = re.compile(r"\d[.,]\d{3}")


def _is_prose_row(cols: list[str]) -> bool:
    """True when a comma-split "row" is really a prose sentence, not a catalog row.

    A legal/policy sentence with an incidental comma ("… hạ tầng kỹ thuật (nhà
    trạm, hệ thống cáp) và …") passes the chunk-level delimiter gate and splits
    into prose cells, whose first clause then becomes a false entity (M7). Two
    structural signals separate it from a real catalog row, with NO catalog-row
    false-drop: the row ENDS on a sentence terminator AND NO cell parses as a
    price (a real priced catalog row always carries one; a description cell that
    ends in "." is kept because its row still has a price). Pure grammar/structure.
    """
    non_empty = [c.strip() for c in cols if c.strip()]
    # A 1-cell "row" is the category-heading branch; a real catalog row is tabular.
    if len(non_empty) < 2:  # noqa: PLR2004 — minimum tabular width, not a tunable
        return False
    if any(parse_money_vn(c) is not None for c in non_empty):
        return False
    return non_empty[-1].endswith(_STATS_SENTENCE_END)


def _row_has_gaps(cols: list[str]) -> bool:
    """True when a row has BOTH filled and empty cells — the shape of the top row of
    a split (2-row) header (``Tên kho|Mã|Tên hàng|(empty)|(empty)``). A normal
    single-row header has no empty cells, so this never fires on the happy case."""
    return any(c.strip() for c in cols) and any(not c.strip() for c in cols)


def _is_header_continuation(top: list[str], bottom: list[str]) -> bool:
    """True when ``bottom`` is the SECOND row of a split header: it fills ≥1 of
    ``top``'s empty positions and does NOT overlap any of ``top``'s filled cells.

    Domain-neutral (no money/number assumption): a real DATA row carries a value
    under every named column → it overlaps ``top``'s filled cells → rejected. Only a
    complementary continuation row (names where row 1 was blank, blank where row 1
    named) is merged."""
    m = min(len(top), len(bottom))
    fills = False
    for j in range(m):
        t, b = top[j].strip(), bottom[j].strip()
        if t and b:
            return False  # overlap → bottom is a data row, not a header continuation
        if b and not t:
            fills = True
    return fills


def _merge_header_rows(top: list[str], bottom: list[str]) -> list[str]:
    """Header-path concat (SOTA: Docling/TATR/MixRAG): per column, join the non-empty
    parts of the two header rows. Gap-fill (``(empty)`` + ``date1`` → ``date1``) and
    hierarchical concat (``Giá`` + ``2024`` → ``Giá 2024``). Domain-neutral, no LLM."""
    n = max(len(top), len(bottom))
    out: list[str] = []
    for j in range(n):
        t = top[j].strip() if j < len(top) else ""
        b = bottom[j].strip() if j < len(bottom) else ""
        out.append(" ".join(p for p in (t, b) if p))
    return out


def _cols_to_csv(cols: list[str]) -> str:
    """Serialise merged header cols back to one CSV line (RFC-4180 quoting) so the
    main parse loop re-splits it identically to a real header row."""
    buf = io.StringIO()
    csv.writer(buf).writerow(cols)
    return buf.getvalue().rstrip("\r\n")


def _premerge_split_headers(
    lines: list[str], declared_labels: frozenset[str]
) -> list[str]:
    """Collapse a 2-row (split / merged-cell) header into ONE labelled header line.

    A header row whose own cells have GAPS, immediately followed by a label-only row
    that FILLS those gaps, is a split header — merge the two so the row-2 column names
    (date1 / hình ảnh / Tồn) are not lost to ``col_N`` at extraction. Deterministic,
    domain-neutral. A single-row header (no gaps) or a priced data row below is left
    untouched, so the happy case is byte-identical.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip() and not _is_separator_line(line):
            cols = _split_cols(line)
            if cols and _is_header_row(cols, declared_labels) and _row_has_gaps(cols):
                j = i + 1
                while j < n and (not lines[j].strip() or _is_separator_line(lines[j])):
                    j += 1
                if j < n:
                    next_cols = _split_cols(lines[j])
                    if _is_header_continuation(cols, next_cols):
                        out.append(_cols_to_csv(_merge_header_rows(cols, next_cols)))
                        i = j + 1
                        continue
        out.append(line)
        i += 1
    return out


def parse_table_chunks(
    chunks: list[dict], custom_roles: dict[str, str] | None = None
) -> list[ParsedEntity]:
    """Extract structured entities from a list of CSV/table chunks.

    Each chunk dict is expected to have at minimum:
        {"content": "<chunk text>", ...}

    ``custom_roles`` is the per-bot ``custom_vocabulary["column_roles"]`` map
    (owner-declared ``{header_label: role}``, ADR-0006 Tier 2). It is AUTHORITATIVE
    over header inference so a domain whose columns the engine can't infer (phone:
    "RAM"/"Pin", real-estate: "Diện tích") still binds NAME/value correctly without
    the engine hardcoding any domain vocabulary.

    Heuristic flow per chunk:
    1. Check if chunk has delimiter characters (skip pure prose).
    2. Split content into lines.
    3. Detect separator lines (---,---,--- or | --- | --- |) and skip.
    4. Detect a header row using _is_header_row() (exact-token match).
    5. Parse subsequent data rows via _extract_entity_from_row().
    6. Track category changes from single-column non-price heading rows.

    Returns a flat list of ParsedEntity across all input chunks.
    """
    entities: list[ParsedEntity] = []
    # Owner-declared labels let a fully-custom header (no built-in token match) still
    # be recognised so Tier-2 ``custom_roles`` actually apply at ingest.
    _declared_labels = frozenset(_normalise_custom_roles(custom_roles))

    for chunk_idx, chunk in enumerate(chunks):
        # Prefer the RAW pre-enrichment chunk text when present: the persisted
        # ``content`` may carry a narrate/CR prefix ("Đoạn X nằm trong phần…")
        # that is prose, not a catalog row — parsing it floods the stats index
        # with narration-sentence noise (18-40% of entities). The raw row text
        # is the clean source of truth for entity extraction.
        content: str = (
            chunk.get("raw_chunk") or chunk.get("content", "") or ""
        )
        lines = [ln.rstrip() for ln in content.splitlines()]
        if not lines:
            continue

        # Heuristic: skip chunks that have no delimiter characters (prose)
        has_delimiter = any(
            "|" in ln or "\t" in ln or ln.count(",") >= 1
            for ln in lines
        )
        if not has_delimiter:
            continue

        # Collapse a 2-row (split / merged-cell) header so row-2 column names
        # (date1 / hình ảnh / Tồn) are not lost to col_N — SOTA header-path concat.
        lines = _premerge_split_headers(lines, _declared_labels)

        header: list[str] = []
        roles: dict[str, Any] = {}
        stub_fill: str | None = None
        current_category: str | None = None

        for _li, line in enumerate(lines):
            stripped_line = line.strip()
            if not stripped_line:
                continue
            if _is_separator_line(line):
                continue

            # Markdown section heading ("## Nhóm dịch vụ A") — AUTHORITATIVE
            # category for the rows that follow (AdapChunk B3 context-binding). The
            # structure-aware parser emits one above each sub-table; binding it here
            # is what lets a sub-category query reach its section's rows. A real
            # heading always wins over a stray single-col note below it.
            _hmatch = _MD_HEADING_RE.match(stripped_line)
            if _hmatch:
                current_category = _hmatch.group(1).strip()
                continue

            cols = _split_cols(line)
            if not cols:
                continue

            # Detect header row — STRUCTURAL (trust the | --- | separator the
            # converter emits) with vocab/owner-declared labels as a fallback hint.
            if _is_header_row(
                cols, _declared_labels,
                next_is_separator=_next_nonempty_is_separator(lines, _li),
            ):
                header = cols
                roles = _column_roles(cols, custom_roles)
                stub_fill = None  # new table → reset rowspan forward-fill
                continue

            # Single non-delimiter col → category heading. Reject noise candidates so
            # a DESCRIPTION line never overwrites a real section title (M5 + B3):
            # tag-lead, discourse opener, section-enum, bullet description, and a
            # note carrying a price ("Đơn giá: 1.600.000 đ").
            if len(cols) == 1:
                candidate = cols[0].strip()
                if (
                    candidate
                    and parse_money_vn(candidate) is None
                    and candidate[:1] not in _STATS_BULLET_LEADS
                    and not _STATS_PRICE_NOTE_RE.search(candidate)
                    and not candidate.startswith(_STATS_TAG_LEAD)
                    and not _is_discourse_opener(candidate)
                    and not _STATS_SECTION_LEAD_RE.match(candidate)
                ):
                    current_category = candidate
                continue

            # Skip a prose sentence mis-split into a "row" by an incidental comma
            # (legal/policy text) — it is not a catalog row (M7).
            if _is_prose_row(cols):
                continue

            # Resolve the row category: a stub/category column (role-based, with
            # rowspan forward-fill for a blank continuation cell) takes precedence
            # over the section heading (SOTA T-11 vertical-span).
            forced_category = current_category
            _cat_idx = roles.get("category")
            # Stub category only when a SEPARATE name column exists — else the
            # category-token column is the entity name (2-col "Vùng | Giá").
            if _cat_idx is not None and roles.get("name") is not None:
                _raw_cat = cols[_cat_idx].strip() if _cat_idx < len(cols) else ""
                if _raw_cat and not _is_pure_money(_raw_cat):
                    stub_fill = _raw_cat
                forced_category = stub_fill or current_category

            entity = _extract_entity_from_row(
                cols, header, chunk_idx, forced_category, roles
            )
            if entity is not None:
                entities.append(entity)

    return entities


def analyze_table_headers(
    chunks: list[dict], custom_roles: dict[str, str] | None = None
) -> dict[str, Any]:
    """Domain-neutral ingest data-quality ADVISORY (ADR-0005 — advisory, NOT blocking).

    Walks the same header rows ``parse_table_chunks`` detects and, using the SAME
    role resolver (``_column_roles`` → Tier-2 owner ``custom_roles`` + Tier-1 G1
    inference), reports for the owner:

      * ``has_name_column`` — did ANY table bind a NAME column? ``False`` is the real
        coverage risk (entities can't be name-keyed); the owner should declare a NAME
        role in ``custom_vocabulary["column_roles"]``.
      * ``unassigned_columns`` — header labels that bound NO role AND the owner did
        NOT declare → they fall to a generic searchable attribute (Tier 3). FYI only:
        the data is NOT dropped; the owner declares a role only if they want
        name/price/category behaviour for that column.
      * ``tables_seen`` — number of header rows detected.

    Never raises on bad input, never blocks ingest, never drops data. The engine
    assumes NO column meanings — it reports the owner's own header labels back.
    """
    declared = _normalise_custom_roles(custom_roles)
    declared_labels = frozenset(declared)
    unassigned: list[str] = []
    seen_unassigned: set[str] = set()
    has_name = False
    tables_seen = 0

    for chunk in chunks:
        content: str = chunk.get("raw_chunk") or chunk.get("content", "") or ""
        _lines = content.splitlines()
        for _li, line in enumerate(_lines):
            if _is_separator_line(line):
                continue
            cols = _split_cols(line)
            if not cols or not _is_header_row(
                cols, declared_labels,
                next_is_separator=_next_nonempty_is_separator(_lines, _li),
            ):
                continue
            tables_seen += 1
            roles = _column_roles(cols, custom_roles)
            if roles.get("name") is not None:
                has_name = True
            # A header cell is "assigned" when it holds a role index OR the owner
            # declared it (incl. an explicit 'attribute' the owner chose) — those are
            # intentional and must not be reported back as a surprise.
            assigned_idx: set[int] = set()
            if roles.get("name") is not None:
                assigned_idx.add(roles["name"])
            if roles.get("category") is not None:
                assigned_idx.add(roles["category"])
            if roles.get("aliases") is not None:
                assigned_idx.add(roles["aliases"])
            assigned_idx.update(roles.get("price") or [])
            assigned_idx.update(roles.get("attribute") or [])
            for idx, cell in enumerate(cols):
                if idx in assigned_idx:
                    continue
                label = (cell or "").strip()
                token = _normalise(label)
                if not token or token.isdigit():
                    continue  # empty / pure-number cell — not a labelled column
                if token in declared:
                    continue  # owner-declared (e.g. 'attribute') — intentional
                if label not in seen_unassigned:
                    seen_unassigned.add(label)
                    unassigned.append(label)

    return {
        "has_name_column": has_name,
        "unassigned_columns": unassigned,
        "tables_seen": tables_seen,
    }


def aggregate_summary(entities: list[ParsedEntity]) -> dict[str, Any]:
    """Build a per-document summary blob for storage in documents.summary_json.

    Returns:
    {
      "entity_count": int,
      "price_primary_min": int | null,
      "price_primary_max": int | null,
      "price_buckets": {
          "under_500k": int,
          "under_1M": int,
          "under_2M": int,
          "under_5M": int,
          "above_5M": int,
      },
      "categories": list[str],
    }

    Bucket keys are generated from DEFAULT_PRICE_BUCKETS_VND constants so
    bucket thresholds never drift between this function and the constant.
    """
    bucket_keys = _build_bucket_keys()

    if not entities:
        return {
            "entity_count": 0,
            "price_primary_min": None,
            "price_primary_max": None,
            "price_buckets": {k: 0 for k in bucket_keys},
            "categories": [],
        }

    prices = [e.price_primary for e in entities if e.price_primary is not None]
    price_min = min(prices) if prices else None
    price_max = max(prices) if prices else None

    buckets: dict[str, int] = {k: 0 for k in bucket_keys}
    last_key = f"above_{_bucket_label(DEFAULT_PRICE_BUCKETS_VND[-1])}"

    for price in prices:
        placed = False
        for threshold, label in zip(DEFAULT_PRICE_BUCKETS_VND, bucket_keys[:-1]):
            if price < threshold:
                buckets[label] += 1
                placed = True
                break
        if not placed:
            buckets[last_key] += 1

    categories: list[str] = sorted(
        {e.category for e in entities if e.category}
    )

    return {
        "entity_count": len(entities),
        "price_primary_min": price_min,
        "price_primary_max": price_max,
        "price_buckets": buckets,
        "categories": categories,
    }


def _bucket_label(threshold: int) -> str:
    """Convert a VND integer threshold to a human-readable bucket label suffix.

    Examples:
        500_000   → "500k"
        1_000_000 → "1M"
        5_000_000 → "5M"
    """
    if threshold >= 1_000_000 and threshold % 1_000_000 == 0:
        return f"{threshold // 1_000_000}M"
    if threshold >= 1_000 and threshold % 1_000 == 0:
        return f"{threshold // 1_000}k"
    return str(threshold)


def _build_bucket_keys() -> list[str]:
    """Build ordered bucket key list from DEFAULT_PRICE_BUCKETS_VND.

    Returns e.g. ["under_500k", "under_1M", "under_2M", "under_5M", "above_5M"].
    """
    keys: list[str] = [f"under_{_bucket_label(t)}" for t in DEFAULT_PRICE_BUCKETS_VND]
    last_label = _bucket_label(DEFAULT_PRICE_BUCKETS_VND[-1])
    keys.append(f"above_{last_label}")
    return keys
