"""Happy-Case Document Checker — lint a document against the canonical format spec.

Customer/dev gives a file (or a DB document) → this runs the L1→L7 pipeline on it and
produces a FORMAT REPORT CARD: is the document a "happy case" the platform parses
cleanly, or does the SOURCE need fixing? Each failed dimension maps to a concrete
source-fix recommendation (per docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md).

Philosophy (SOTA: "fix source first"): we do NOT try to parse every malformed export.
We define the happy case + tell the customer how to make their document match it.

    set -a && source .env && set +a
    python scripts/check_happy_case.py path/to/file.csv      # a local CSV/MD/TXT
    python scripts/check_happy_case.py --db spa-1            # one DB document
    python scripts/check_happy_case.py --db-all              # every DB document
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Import the INPUT-SCOPE vocabulary from the parser — the single source of truth.
# The checker enforces EXACTLY what the parser parses (no spec/checker/code drift).
from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.constants import DEFAULT_TABLE_STRATEGY  # noqa: E402
from ragbot.shared.document_stats import (  # noqa: E402
    _ALIASES_COL_TOKENS,
    _CATEGORY_COL_TOKENS,
    _HEADER_EXTRA_TOKENS,
    _NAME_COL_TOKENS,
    _PRICE_COL_TOKENS,
    _normalise,
    parse_table_chunks,
)
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402

# Every header token that maps to a recognised ROLE (name/category/price/aliases) or a
# structural non-role label (ordinal/id). A header column whose normalised token is in
# NONE of these is silently dumped to attributes_json at ingest (unsearchable) — the
# checker flags it so the owner renames to a canonical token. Domain-neutral: imported
# from the parser's single-source vocabulary, no per-bot literal.
_RECOGNISED_COL_TOKENS: frozenset[str] = (
    _NAME_COL_TOKENS | _CATEGORY_COL_TOKENS | _PRICE_COL_TOKENS
    | _ALIASES_COL_TOKENS | _HEADER_EXTRA_TOKENS
)


def _ingest_table_chunks(content: str) -> list[dict]:
    """Reproduce the chunk content the INGEST pipeline persists + extracts from.

    The stats extractor (``document_stats.parse_table_chunks``) does NOT run on the
    structured-markdown; it runs on the row-as-chunk content the chunking stage emits
    for the resolved table strategy (``table_csv`` raw-CSV ``<header>\\n<row>`` chunks,
    ``table_dual_index`` group + row chunks). The checker must feed the SAME content so
    its price/entity verdict matches what the live ingest indexed — not a markdown path
    the ingest never takes (where ``_md_escape`` neutralises in-cell pipes that the
    raw-CSV path must survive instead).

    Mirrors the ingest chunking stage's auto-detect call
    (``smart_chunk(content, table_strategy=DEFAULT_TABLE_STRATEGY)``) and adapts each
    chunk to the ``{"content": ...}`` dict shape the ingest's ``_raw_row`` adapter hands
    to ``parse_table_chunks``.
    """
    return [{"content": c} for c in smart_chunk(content, table_strategy=DEFAULT_TABLE_STRATEGY)]

_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_METADATA_LEAD_RE = re.compile(r"^\w+:\s")


def _header_role(md: str, tokens: frozenset[str]) -> bool:
    """True when some markdown header cell is in *tokens* (the canonical scope)."""
    for ln in md.splitlines():
        if ln.startswith("|") and "---" not in ln:
            if any(_normalise(c.strip()) in tokens for c in ln.split("|") if c.strip()):
                return True
    return False
_GIANT_CELL = 200   # a cell longer than this is prose, not a catalog field
_PROSE_CELL = 80


def _unassigned_header_cols(md: str) -> list[str]:
    """Return header cells whose normalised token maps to NO recognised role.

    Reads the FIRST pipe-table header row (the row before the ``| --- |`` separator,
    or — when no separator — the first non-separator pipe row). A header cell whose
    ``_normalise`` token is not in ``_RECOGNISED_COL_TOKENS`` is dumped to
    attributes_json at ingest (unsearchable). Empty / pure-number / numbered-only cells
    are skipped (they are not labelled columns). Domain-neutral: token-set membership.
    """
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        if set(s) <= set("|-: "):
            continue  # separator row — header is the row before, already returned
        cells = [c.strip() for c in s.strip("|").split("|")]
        unassigned: list[str] = []
        for c in cells:
            if not c:
                continue
            tok = _normalise(c)
            if not tok or tok.isdigit():
                continue
            if tok not in _RECOGNISED_COL_TOKENS:
                unassigned.append(c)
        return unassigned
    return []


class Card:
    """One dimension result on the report card."""

    def __init__(self, ok: bool | None, name: str, detail: str, fix: str = "") -> None:
        self.ok, self.name, self.detail, self.fix = ok, name, detail, fix

    def line(self) -> str:
        mark = {True: "✅", False: "🔴", None: "🟡"}[self.ok]
        out = f"  {mark} {self.name}: {self.detail}"
        if self.ok is not True and self.fix:
            out += f"\n       → FIX: {self.fix}"
        return out


def _is_doc(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:60]
    return sum(1 for ln in lines if _HEADING_RE.match(ln)) >= 3  # noqa: PLR2004


def check_sheet(
    rows: list[list[str]], md: str, ingest_chunks: list[dict],
) -> tuple[str, list[Card]]:
    cards: list[Card] = []
    data_rows = [r for r in rows if any(c.strip() for c in r)]
    n_rows = len(data_rows)
    # Entity/price extraction runs on the SAME chunk content the ingest persists +
    # extracts from (``ingest_chunks``), NOT on the structured-markdown — so the
    # checker's verdict mirrors the live ingest instead of a path it never takes.
    # Header-role cards below still read ``md`` (a reliable pipe-table view of the
    # column labels; header detection is not where the path divergence bites).
    ents = parse_table_chunks(ingest_chunks)
    n_ents = len(ents)
    priced = [e for e in ents if e.price_primary]
    cov = (len(priced) / n_ents) if n_ents else 0.0

    has_price = _header_role(md, _PRICE_COL_TOKENS)
    has_name = _header_role(md, _NAME_COL_TOKENS)

    # C2 — header clarity
    if has_name and has_price:
        cards.append(Card(True, "header clarity", "name + price columns recognised"))
    elif has_name:
        cards.append(Card(True, "header clarity", "name column found (non-price sheet — inventory/manifest)"))
    else:
        cards.append(Card(None, "header clarity", "no clear name/price header",
                          "add a header row: 'Tên, Giá' (or Tên dịch vụ / Đơn giá)"))

    # C2b — column-role coverage. A header column whose token maps to NO role
    # (name/category/price/aliases) is silently dumped to attributes_json at ingest →
    # unsearchable. Tell the owner to rename it to a canonical token. A synonym/search
    # column should be renamed to 'Aliases' (now a first-class role).
    unassigned = _unassigned_header_cols(md)
    if not unassigned:
        cards.append(Card(True, "column roles", "every header column maps to a role"))
    else:
        cols = ", ".join(f"'{c}'" for c in unassigned)
        cards.append(Card(None, "column roles",
                          f"{len(unassigned)} column(s) map to no role → "
                          f"dumped to attributes (unsearchable): {cols}",
                          "rename to a canonical header — Tên / Nhóm / Giá / Aliases "
                          "(a synonym/keyword column → 'Aliases')"))

    # C3 — entity density = the GROUND TRUTH of "did extraction succeed". A row that
    # parses to a clean entity is atomic regardless of how long an aux column (Aliases,
    # Ghi chú) is. Only when density is LOW do we diagnose WHY (synonym lead / prose
    # cell in the NAME position) — a long *aux* column never fails a happy row.
    density = (n_ents / n_rows) if n_rows else 0
    if density >= 0.5:  # noqa: PLR2004
        cards.append(Card(True, "row atomicity", f"{n_ents}/{n_rows} rows parse to clean entities (≈1 per row)"))
    else:
        synonym = sum(1 for r in data_rows if r and _METADATA_LEAD_RE.match(r[0].strip()))
        giant_name = sum(1 for r in data_rows if r and len((r[0] if r[0].strip() else (r[1] if len(r) > 1 else "")).strip()) > _GIANT_CELL)
        if synonym >= giant_name:
            cards.append(Card(False, "row atomicity", f"{synonym}/{n_rows} rows are 'key: value' synonym/metadata rows",
                              "this is a SEARCH-INDEX export, not a catalog — re-export as 'Tên | Giá [| Aliases]'"))
        else:
            cards.append(Card(False, "row atomicity", f"{giant_name}/{n_rows} rows have a >200-char prose cell in the NAME column",
                              "this is a prose/script doc — move it to a DOC (## headings + 'Bước N:'), not a sheet"))

    # C5 — price coverage (catalog only)
    if has_price:
        if cov >= 0.9:  # noqa: PLR2004
            cards.append(Card(True, "price coverage", f"{len(priced)}/{n_ents} = {cov:.0%}"))
        elif cov > 0:
            cards.append(Card(None, "price coverage", f"{len(priced)}/{n_ents} = {cov:.0%} (some prices unparsed)",
                              "check price cells use a supported format (700000 / 899k / 1.5tr / từ 500k)"))
        else:
            cards.append(Card(False, "price coverage", "0% — has a price column but no price parsed",
                              "price column values are not money — check the column or remove the price header"))

    verdict = _verdict(cards)
    return verdict, cards


def check_doc(md: str) -> tuple[str, list[Card]]:
    cards: list[Card] = []
    headings = [ln for ln in md.splitlines() if _HEADING_RE.match(ln.strip())]
    tables = md.count("| --- ")
    cards.append(Card(bool(headings), "heading structure", f"{len(headings)} markdown headings",
                      "add markdown headings (# / ## / Điều / Bước N:) so retrieval can anchor sections"))
    cards.append(Card(True, "doc type", f"prose document ({tables} tables) — price extraction N/A"))
    verdict = _verdict(cards)
    return verdict, cards


def _verdict(cards: list[Card]) -> str:
    if any(c.ok is False for c in cards):
        return "🔴 NON-HAPPY (fix source)"
    if any(c.ok is None for c in cards):
        return "🟡 NEEDS-MINOR-FIX"
    return "✅ HAPPY-CASE"


def _rows_from_markdown(md: str) -> list[list[str]]:
    """Recover row cells from already-structured markdown (DB raw_content) so the
    sheet checks keep row counts/leads without re-running the CSV converter. Skips
    section headings and the ``| --- |`` separator; the header row is kept (the CSV
    path keeps it too)."""
    out: list[list[str]] = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        if set(s) <= set("|-: "):
            continue  # header separator row
        out.append([c.strip() for c in s.strip("|").split("|")])
    return out


def check_one(name: str, raw: str, *, from_db: bool = False) -> str:
    is_doc = _is_doc(raw)
    if is_doc:
        verdict, cards = check_doc(raw)
        kind = "DOC"
    elif from_db:
        # DB raw_content is the parser's structured output (already markdown) — use it
        # as the header-role view. For entity/price extraction, re-chunk that same
        # content through the ingest chunking path so the verdict tracks what the live
        # ingest indexed (``smart_chunk`` auto-detects the strategy the same way).
        rows = _rows_from_markdown(raw)
        verdict, cards = check_sheet(rows, raw, _ingest_table_chunks(raw))
        kind = "SHEET"
    else:
        rows = list(csv.reader(io.StringIO(raw)))
        md = rows_to_structured_markdown(rows)
        # Extraction runs on the raw-CSV chunks the ingest actually persists (resolved
        # via the same chunking path) — NOT the markdown — closing the false-green where
        # ``_md_escape`` hid an in-cell pipe that hijacks the raw-CSV column split.
        verdict, cards = check_sheet(rows, md, _ingest_table_chunks(raw))
        kind = "SHEET"
    print(f"\n{'─' * 90}\n📄 {name}  [{kind}]  ({len(raw)} chars)   →   {verdict}\n{'─' * 90}")
    for c in cards:
        print(c.line())
    return verdict


def _read_db(name: str | None) -> list[tuple[str, str]]:
    import asyncio

    import asyncpg

    async def go() -> list[tuple[str, str]]:
        dsn = re.sub(r"\+\w+", "", os.environ.get("DATABASE_URL", ""))
        con = await asyncpg.connect(dsn)
        if name:
            rows = await con.fetch(
                "SELECT document_name, raw_content FROM documents "
                "WHERE document_name=$1 AND deleted_at IS NULL", name)
        else:
            rows = await con.fetch(
                "SELECT document_name, raw_content FROM documents "
                "WHERE deleted_at IS NULL AND raw_content IS NOT NULL ORDER BY document_name")
        await con.close()
        return [(r["document_name"], r["raw_content"]) for r in rows]

    return asyncio.run(go())


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)

    docs: list[tuple[str, str]]
    # DB documents store the parser's structured-markdown in raw_content; file
    # arguments are raw CSV. The checker must not re-convert already-markdown.
    from_db = args[0] in ("--db", "--db-all")
    if args[0] == "--db-all":
        docs = _read_db(None)
    elif args[0] == "--db":
        docs = _read_db(args[1])
    else:
        p = Path(args[0])
        docs = [(p.name, p.read_text(encoding="utf-8"))]

    print(f"HAPPY-CASE CHECK — {len(docs)} document(s)")
    tally: dict[str, int] = {}
    for nm, raw in docs:
        v = check_one(nm, raw, from_db=from_db)
        tally[v] = tally.get(v, 0) + 1
    print(f"\n{'█' * 90}\nSUMMARY: " + "   ".join(f"{k} ×{v}" for k, v in tally.items()))


if __name__ == "__main__":
    main()
