"""Regression: the happy-case checker must test the SAME content the ingest stores.

Bug (verified 2026-06-23): ``scripts/check_happy_case.py`` extracted entities by
running ``parse_table_chunks`` on ``rows_to_structured_markdown(rows)`` — the
STRUCTURED-MARKDOWN. But the live ingest persists raw row-as-chunk content
(``table_csv`` / ``table_dual_index``: ``<header>\\n<row>``, NO markdown pipe-escaping)
and runs the SAME extractor on THOSE. The checker therefore validated a code path the
ingest never takes:

  * markdown path → ``_md_escape`` rewrites in-cell ``|`` to ``\\|`` and the table
    state-machine truncates data cells to the header width;
  * raw-CSV path → the literal ``|`` survives and every data column is kept.

These are DIFFERENT branches of ``document_stats._split_cols`` and of the table
state-machine, so a checker verdict on markdown does not predict the ingest verdict.

This test locks the fix: the checker's entity/price extraction now runs on the
ingest-faithful chunks (``_ingest_table_chunks`` → ``smart_chunk(..., table_strategy=...)``)
and its numbers equal the ingest path's numbers — AND differ from the markdown path on a
fixture where the two genuinely diverge. No DB, no network: synthetic CSV only.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest

# Silence the chunker's per-row "oversized row kept whole" structlog warnings so the
# test output stays readable; they are not part of the assertion surface.
logging.disable(logging.CRITICAL)

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.constants import DEFAULT_TABLE_STRATEGY  # noqa: E402
from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402


def _load_checker() -> ModuleType:
    """Load ``scripts/check_happy_case.py`` (not a package) via importlib — the same
    pattern as ``tests/unit/test_chunk_scoring_scripts.py``."""
    script_path = _REPO_ROOT / "scripts" / "check_happy_case.py"
    assert script_path.exists(), f"script missing at {script_path}"
    spec = importlib.util.spec_from_file_location("_check_happy_case", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load_checker()


def _priced(entities) -> int:
    return sum(1 for e in entities if e.price_primary)


def _ingest_priced(raw_csv: str) -> tuple[int, int]:
    """(n_entities, n_priced) the LIVE INGEST would index for this CSV.

    Reproduces the ingest chunking-stage call (``smart_chunk(content,
    table_strategy=DEFAULT_TABLE_STRATEGY)`` → row-as-chunk content) and the stats
    extractor (``parse_table_chunks`` over those chunks) — the ground truth the
    checker must mirror.
    """
    chunks = smart_chunk(raw_csv, table_strategy=DEFAULT_TABLE_STRATEGY)
    ents = parse_table_chunks([{"content": c} for c in chunks])
    return len(ents), _priced(ents)


def _markdown_priced(raw_csv: str) -> tuple[int, int]:
    """(n_entities, n_priced) the OLD checker produced — extractor over the
    structured-markdown. The path the ingest never takes."""
    rows = list(csv.reader(io.StringIO(raw_csv)))
    md = rows_to_structured_markdown(rows)
    ents = parse_table_chunks([{"content": md}])
    return len(ents), _priced(ents)


# ── Fixtures ────────────────────────────────────────────────────────────────────

# xe-3 SHAPE: name + price columns, plus an Aliases column carrying a LITERAL pipe and
# an embedded "price:" decoy (the real export that first surfaced the bug). The literal
# pipe must NOT hijack the raw-CSV column split — price stays in the price column.
_CSV_EMBEDDED_PIPE = (
    "Ten,Gia,Aliases\n"
    'Combo A,899000,"a; b | code: Z1 | price: 899000 | x: y"\n'
    'Combo B,1299000,"c; d | code: Z2 | price: 1299000 | x: y"\n'
    'Combo C,1599000,"e; f | code: Z3 | price: 1599000 | x: y"\n'
)

# DIVERGENT shape: header declares only 2 columns (Ten,Ma) but each data row carries the
# price in an UNLABELLED 3rd column. The markdown converter opens a 2-col table and
# TRUNCATES the price column (vals = cells[:len(header)]) → 0 priced. The raw-CSV
# ``table_csv`` path keeps the full row → every price extracted. Proves the two paths
# genuinely disagree, so testing markdown could not predict the ingest.
_CSV_EXTRA_PRICE_COL = (
    "Ten,Ma\n"
    "Combo A,Z1,899000\n"
    "Combo B,Z2,1299000\n"
    "Combo C,Z3,1599000\n"
)

# BROKEN-FOR-INGEST shape: a priced header, but every row's NAME cell is an over-long
# blob (>DEFAULT_STATS_ATTR_MAX_CHARS) with an embedded literal pipe + "price:" decoy and
# an EMPTY price column. The ingest extracts 0 entities (name-guard rejects). The checker
# must report this RED (0% coverage) — proving it would catch an ingest-breaking format.
_LONG_NAME = "Combo X " + ("desc clause " * 30) + "| code: Z | price: 777000"
_CSV_BROKEN_INGEST = "Ten,Gia\n" + "\n".join(
    f'"{_LONG_NAME} #{i}",' for i in range(4)
) + "\n"


# ── Tests ───────────────────────────────────────────────────────────────────────


def test_checker_helper_reproduces_ingest_chunk_content() -> None:
    """``_ingest_table_chunks`` yields the EXACT chunk content the ingest persists +
    feeds to the stats extractor — the same ``smart_chunk(..., table_strategy=...)``
    row-as-chunk output, wrapped in the ``{"content": ...}`` adapter shape."""
    checker = _load_checker()
    expected = [{"content": c} for c in smart_chunk(
        _CSV_EMBEDDED_PIPE, table_strategy=DEFAULT_TABLE_STRATEGY,
    )]
    got = checker._ingest_table_chunks(_CSV_EMBEDDED_PIPE)
    assert got == expected
    # And it really is raw-CSV content, NOT escaped markdown: a data chunk holds the
    # literal pipe and the comma-delimited header, with no ``\\|`` escape.
    body = "\n".join(c["content"] for c in got)
    assert "Ten,Gia,Aliases" in body          # raw comma header, not "| Ten | Gia |"
    assert "| code: Z1 |" in body             # literal pipe survives
    assert "\\|" not in body                  # markdown escaping never applied


def test_checker_extraction_matches_ingest_on_embedded_pipe() -> None:
    """On the xe-3-shape fixture the checker's price card mirrors the ingest: every
    row priced (the literal pipe does not steal the price column)."""
    checker = _load_checker()
    rows = list(csv.reader(io.StringIO(_CSV_EMBEDDED_PIPE)))
    md = rows_to_structured_markdown(rows)
    chunks = checker._ingest_table_chunks(_CSV_EMBEDDED_PIPE)

    _verdict, cards = checker.check_sheet(rows, md, chunks)
    price_card = next((c for c in cards if c.name == "price coverage"), None)
    assert price_card is not None, "priced header must yield a price-coverage card"

    n_ents_ingest, n_priced_ingest = _ingest_priced(_CSV_EMBEDDED_PIPE)
    assert n_priced_ingest == 3                  # ground truth: all 3 rows priced
    assert price_card.ok is True
    assert f"{n_priced_ingest}/{n_ents_ingest}" in price_card.detail


def test_checker_uses_ingest_path_not_markdown_on_divergent_fixture() -> None:
    """THE regression guard: on a fixture where the markdown and raw-CSV extraction
    paths genuinely diverge, the checker must follow the INGEST (raw-CSV) path.

    Markdown drops the unlabelled price column → 0 priced (the old false verdict).
    Ingest keeps it → 3 priced. The checker's extraction must equal the ingest, not
    the markdown — otherwise it is testing a path the ingest never takes.
    """
    checker = _load_checker()

    n_ents_md, n_priced_md = _markdown_priced(_CSV_EXTRA_PRICE_COL)
    n_ents_ingest, n_priced_ingest = _ingest_priced(_CSV_EXTRA_PRICE_COL)

    # Precondition: the two paths really disagree (else the fixture proves nothing).
    assert n_priced_md == 0, "markdown path must drop the unlabelled price column"
    assert n_priced_ingest == 3, "ingest path must keep the full row + price it"
    assert n_priced_md != n_priced_ingest

    # The checker, given the ingest chunks, must extract the INGEST numbers.
    chunks = checker._ingest_table_chunks(_CSV_EXTRA_PRICE_COL)
    ents = parse_table_chunks(chunks)
    assert _priced(ents) == n_priced_ingest
    assert _priced(ents) != n_priced_md


def test_checker_reports_red_when_ingest_indexes_zero_prices() -> None:
    """A format the ingest extracts 0 prices from (over-long name cell + literal pipe,
    empty price column) must make the checker RED — it now mirrors the ingest's 0%
    coverage instead of a markdown path that could mask it."""
    checker = _load_checker()

    n_ents_ingest, n_priced_ingest = _ingest_priced(_CSV_BROKEN_INGEST)
    assert n_priced_ingest == 0, "ground truth: ingest indexes no priced entity here"

    rows = list(csv.reader(io.StringIO(_CSV_BROKEN_INGEST)))
    md = rows_to_structured_markdown(rows)
    chunks = checker._ingest_table_chunks(_CSV_BROKEN_INGEST)
    verdict, cards = checker.check_sheet(rows, md, chunks)

    price_card = next((c for c in cards if c.name == "price coverage"), None)
    assert price_card is not None
    assert price_card.ok is False                       # RED, mirrors ingest 0%
    assert "0%" in price_card.detail
    assert verdict.startswith("\U0001f534")             # 🔴 NON-HAPPY
