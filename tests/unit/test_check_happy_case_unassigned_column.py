"""Pin: the happy-case checker WARNS when a header column maps to NO recognised role.

A column whose normalised header token is not in name/category/price/aliases is
silently dumped to ``attributes_json`` at ingest (unsearchable). The checker must
surface that so the owner renames the header to a canonical token. The Aliases role
is now first-class: a header carrying an aliases token must NOT be flagged unassigned.

No DB / network — synthetic CSV through the loaded checker module.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402


def _load_checker() -> ModuleType:
    script_path = _REPO_ROOT / "scripts" / "check_happy_case.py"
    spec = importlib.util.spec_from_file_location("_check_happy_case_unassigned", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _cards(checker: ModuleType, raw_csv: str):
    rows = list(csv.reader(io.StringIO(raw_csv)))
    md = rows_to_structured_markdown(rows)
    chunks = checker._ingest_table_chunks(raw_csv)
    return checker.check_sheet(rows, md, chunks)


def test_unassigned_column_card_warns() -> None:
    """A header column with an out-of-vocab token ("Ghi chú") → a column-role card
    that is not-OK and names the dropped column."""
    checker = _load_checker()
    raw = (
        "Tên,Giá,Ghi chú\n"
        "Service A,499000,note one\n"
        "Service B,599000,note two\n"
    )
    _verdict, cards = _cards(checker, raw)
    role_card = next((c for c in cards if c.name == "column roles"), None)
    assert role_card is not None, "expected a 'column roles' card"
    assert role_card.ok is not True, "an unassigned column must NOT pass clean"
    assert "ghi chú" in role_card.detail.lower() or "ghi chu" in role_card.detail.lower()


def test_all_canonical_columns_pass() -> None:
    """Name + price + category + aliases headers → the column-role card is OK."""
    checker = _load_checker()
    raw = (
        "Tên,Nhóm,Giá,Aliases\n"
        'Service A,Cat 1,499000,"kw1; kw2"\n'
    )
    _verdict, cards = _cards(checker, raw)
    role_card = next((c for c in cards if c.name == "column roles"), None)
    assert role_card is not None
    assert role_card.ok is True, f"all-canonical headers must pass: {role_card.detail}"


def test_aliases_column_not_flagged_unassigned() -> None:
    """An Aliases header is a first-class role, NOT an unassigned column."""
    checker = _load_checker()
    raw = (
        "Tên,Giá,Từ khoá\n"
        'Service A,499000,"kw1; kw2"\n'
    )
    _verdict, cards = _cards(checker, raw)
    role_card = next((c for c in cards if c.name == "column roles"), None)
    assert role_card is not None
    assert role_card.ok is True, f"aliases header must be recognised: {role_card.detail}"
