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

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ragbot.shared.constants import DEFAULT_PRICE_BUCKETS_VND, DEFAULT_PRICE_MIN_VND
from ragbot.shared.number_format import parse_money_vn as _canonical_parse_money

# ---------------------------------------------------------------------------
# Money-format regex patterns — Vietnamese currency conventions.
#
# Supported formats (all produce an integer VND value):
#   "1.499.000"  — dotted thousands (Vietnamese locale)
#   "1,499,000"  — comma thousands (Western locale)
#   "1499000"    — bare integer (4-8 digits)
#   "1tr499"     — Vietnamese shorthand: 1 triệu + 499 (= 1,000,000 + 499,000)
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
# Exact-match (normalised) column label keywords — generic, domain-neutral.
_HEADER_EXACT_TOKENS: frozenset[str] = frozenset({
    # Vietnamese column labels (normalised, no accents)
    "stt", "ten", "gia", "vung", "loai", "dich vu", "buoi", "combo",
    "goi", "danh muc", "phi",
    # English column labels
    "service", "price", "name", "category", "type", "amount", "cost",
    "no", "id", "qty", "quantity",
})

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
      - "1.499.000" / "1,499,000"  → dotted/comma thousands
      - "1499000"                  → bare integer (4-8 digits)
      - "1tr499"                   → 1,000,000 + 499,000 = 1,499,000
      - "1.5tr"                    → 1,500,000
      - "499k" / "499K"            → 499,000
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
    return _canonical_parse_money(text, min_value=DEFAULT_PRICE_MIN_VND)


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
    """

    name: str
    category: str | None
    price_primary: int | None
    price_secondary: int | None
    chunk_index: int
    attributes: dict[str, Any] = field(default_factory=dict)


def _is_header_row(cols: list[str]) -> bool:
    """Heuristic: return True if this row looks like a column-label header.

    Rules:
    1. At least one cell must exactly match a known header token (after
       accent normalisation). Exact-match avoids false positives like
       "Service A" matching the "service" token.
    2. No cell may contain a parseable money value — real data rows have
       prices; header rows have only column labels.
    """
    has_label_match = False
    for col in cols:
        if not col:
            continue
        if parse_money_vn(col) is not None:
            # Data row (has a price cell) → not a header
            return False
        normalised = _normalise(col.strip())
        if normalised in _HEADER_EXACT_TOKENS:
            has_label_match = True
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
    # Try pipe first (common for Markdown tables)
    if "|" in line:
        parts = [c.strip() for c in line.split("|")]
        # Strip leading/trailing empty parts from Markdown pipe tables
        if parts and not parts[0]:
            parts = parts[1:]
        if parts and not parts[-1]:
            parts = parts[:-1]
        return parts
    # Tab separator
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    # Comma separator (common for CSV chunks)
    return [c.strip() for c in line.split(",")]


def _extract_entity_from_row(
    cols: list[str],
    header: list[str],
    chunk_index: int,
    current_category: str | None,
) -> ParsedEntity | None:
    """Build a ParsedEntity from a split data row.

    Rules (domain-neutral):
    - First non-empty, non-ordinal column is the entity name.
    - Columns that parse as money → price_primary (first), price_secondary (second).
    - Remaining columns go into attributes keyed by header label (if available).
    - Returns None when the row yields no entity name and no price.
    """
    if not cols or all(c == "" for c in cols):
        return None

    name: str | None = None
    price_primary: int | None = None
    price_secondary: int | None = None
    attributes: dict[str, Any] = {}

    for idx, col in enumerate(cols):
        if not col:
            continue

        money = parse_money_vn(col)
        if money is not None:
            if price_primary is None:
                price_primary = money
            elif price_secondary is None:
                price_secondary = money
            else:
                # Third+ price column → attributes
                label = header[idx] if idx < len(header) else f"price_{idx}"
                attributes[label] = money
            continue

        # First non-money col → entity name, unless it's a pure ordinal
        if name is None:
            stripped = col.strip()
            # Skip pure ordinal row numbers (1, 2, 3 … or 1. 2. etc.)
            if re.match(r"^\d{1,3}\.?$", stripped):
                continue
            name = stripped
        else:
            label = header[idx] if idx < len(header) else f"col_{idx}"
            attributes[label] = col

    if name is None and price_primary is None:
        return None

    return ParsedEntity(
        name=name or "",
        category=current_category,
        price_primary=price_primary,
        price_secondary=price_secondary,
        chunk_index=chunk_index,
        attributes=attributes,
    )


def parse_table_chunks(chunks: list[dict]) -> list[ParsedEntity]:
    """Extract structured entities from a list of CSV/table chunks.

    Each chunk dict is expected to have at minimum:
        {"content": "<chunk text>", ...}

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

    for chunk_idx, chunk in enumerate(chunks):
        content: str = chunk.get("content", "") or ""
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

        header: list[str] = []
        current_category: str | None = None

        for line in lines:
            if not line.strip():
                continue
            if _is_separator_line(line):
                continue

            cols = _split_cols(line)
            if not cols:
                continue

            # Detect header row (exact token match, no prices)
            if _is_header_row(cols):
                header = cols
                continue

            # Single non-delimiter col → category heading
            if len(cols) == 1:
                candidate = cols[0].strip()
                if candidate and parse_money_vn(candidate) is None:
                    current_category = candidate
                continue

            entity = _extract_entity_from_row(cols, header, chunk_idx, current_category)
            if entity is not None:
                entities.append(entity)

    return entities


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
