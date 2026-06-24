"""Pin: the normalizer renames owner header columns → canonical tokens.

A messy owner export ("Mặt hàng", "Phân loại", "Từ khoá") must be renamed to the
canonical headers ("Tên", "Nhóm", "Aliases") BEFORE role detection so the parser
recognises every column. The rewrite is DATA-PRESERVING — only the header row's
labels change; every data value survives. Domain-neutral generic map, no per-bot
literal.

No DB / network — the rename function is pure.
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


def _load_normalizer() -> ModuleType:
    script_path = _REPO_ROOT / "scripts" / "normalize_to_happy_case.py"
    spec = importlib.util.spec_from_file_location("_normalize_happy_case", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_renames_messy_headers_to_canonical() -> None:
    norm = _load_normalizer()
    raw = (
        "Mặt hàng,Phân loại,Giá,Từ khoá\n"
        'Lốp A,Mùa hè,684000,"265/50R20; 265 50 R20"\n'
    )
    out = norm.rename_headers_to_canonical(raw)
    header = next(csv.reader(io.StringIO(out)))
    assert header[0] == "Tên"
    assert header[1] == "Nhóm"
    assert header[2] == "Giá"     # already canonical → unchanged
    assert header[3] == "Aliases"


def test_rename_is_data_preserving() -> None:
    norm = _load_normalizer()
    raw = (
        "Tên hàng,Vùng,Giá\n"
        "Service A,Mặt,499000\n"
        "Service B,Cổ,599000\n"
    )
    out = norm.rename_headers_to_canonical(raw)
    rows = list(csv.reader(io.StringIO(out)))
    # header renamed
    assert rows[0] == ["Tên", "Nhóm", "Giá"]
    # every data value survives unchanged
    assert rows[1] == ["Service A", "Mặt", "499000"]
    assert rows[2] == ["Service B", "Cổ", "599000"]


def test_unknown_header_left_untouched() -> None:
    norm = _load_normalizer()
    raw = "Tên,Ghi chú,Giá\nA,note,100000\n"
    out = norm.rename_headers_to_canonical(raw)
    header = next(csv.reader(io.StringIO(out)))
    # "Ghi chú" has no canonical mapping → left as-is (checker will warn separately)
    assert header == ["Tên", "Ghi chú", "Giá"]


def test_first_data_row_not_treated_as_header() -> None:
    """Only the FIRST line (header) is renamed; a data cell that happens to equal a
    mapped owner label must NOT be rewritten."""
    norm = _load_normalizer()
    raw = "Mặt hàng,Giá\nPhân loại,500000\n"
    out = norm.rename_headers_to_canonical(raw)
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["Tên", "Giá"]
    assert rows[1] == ["Phân loại", "500000"]  # data value untouched
