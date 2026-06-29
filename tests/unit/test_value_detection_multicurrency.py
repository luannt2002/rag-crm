"""F2 — value detection is multi-currency via Unicode currency SYMBOLS.

THE ONE LAW for values: a cell is a VALUE when it is digit-dominant carrying a
currency symbol (Unicode ``Sc`` category: $ € £ ₫ ¥ ₩ …) — decided by SHAPE, not
by a single hardcoded currency. VND ("đ"/"triệu"/"tr"/"k") stays byte-identical.

Regression guard: locks the already-correct multi-currency-SYMBOL behaviour so a
future edit to the money parser cannot silently regress to single-VND. (Currency
WORD units like "USD"/"won" are a separate ISO-4217 follow-up — see report F2.)
"""
from __future__ import annotations

import pytest

from ragbot.shared.tabular_markdown import _is_pure_money

# Unicode Sc currency symbols + grouped/decimal shapes — all are pure values.
_SYMBOL_VALUES = ["$500", "€1.500", "£2,000", "₫500000", "500000₫", "$1,250.00", "1.5M"]
# VN forms must stay recognised (byte-identical happy path).
_VND_VALUES = ["899000", "1.499.000", "6 triệu", "1tr499", "234k"]
# Descriptive NAMES that merely contain a number must NOT be read as a value.
_NAMES = ["Gói 6 triệu", "30 phút", "Áo thun size M"]


@pytest.mark.parametrize("cell", _SYMBOL_VALUES)
def test_currency_symbol_is_value(cell: str) -> None:
    assert _is_pure_money(cell) is True, cell


@pytest.mark.parametrize("cell", _VND_VALUES)
def test_vnd_value_byte_identical(cell: str) -> None:
    assert _is_pure_money(cell) is True, cell


@pytest.mark.parametrize("cell", _NAMES)
def test_descriptive_name_is_not_value(cell: str) -> None:
    assert _is_pure_money(cell) is False, cell
