"""P0-2 (increment) — lossless NUMERIC coverage gate.

A chunker that silently drops a row/price leaves Faithfulness at 1.0 while the
number is simply gone ("honest but blind" HALLU class). ``find_dropped_numbers``
is the observe-only signal: every significant source number must survive into
some chunk. Deterministic, currency/language-neutral.
"""
from __future__ import annotations

from ragbot.shared.number_format import find_dropped_numbers


def test_full_coverage_no_drops() -> None:
    source = "Lốp A 1.500.000đ\nLốp B 899000đ\nLốp C 2.300.000đ"
    chunks = ["Lốp A 1.500.000đ\nLốp B 899000đ", "Lốp C 2.300.000đ"]
    assert find_dropped_numbers(source, chunks) == []


def test_dropped_price_is_flagged() -> None:
    source = "Lốp A 1.500.000đ\nLốp B 899000đ\nLốp C 2.300.000đ"
    # chunker dropped the "Lốp C" row → its price vanished.
    chunks = ["Lốp A 1.500.000đ\nLốp B 899000đ"]
    assert find_dropped_numbers(source, chunks) == ["2.300.000"]


def test_ordinals_and_sizes_not_flagged() -> None:
    # 1/2/3 row indices + size "15" are < min_digits → never flagged even if absent.
    source = "1. Lốp 205/55R16\n2. Lốp 195/65R15"
    chunks = ["1. Lốp 205/55R16"]  # row 2 dropped, but only short tokens
    assert find_dropped_numbers(source, chunks) == []


def test_currency_neutral_usd_eur() -> None:
    source = "Item A $1,250.00\nItem B €2.000\nItem C 5000 USD"
    chunks = ["Item A $1,250.00\nItem B €2.000"]  # Item C dropped
    assert find_dropped_numbers(source, chunks) == ["5000"]


def test_empty_inputs() -> None:
    assert find_dropped_numbers("", ["x"]) == []
    assert find_dropped_numbers("1.500.000", []) == []
