"""P29-A autonomy resolver — pure helper behaviour tests."""
from __future__ import annotations

import pytest

from ragbot.shared.autonomy_resolver import (
    autonomy_band,
    clamp_autonomy_percent,
    resolve_effective_autonomy_percent,
)


@pytest.mark.parametrize(
    "val,expected",
    [
        (None, 0),
        (0, 0),
        (50, 50),
        (100, 100),
        (-5, 0),
        (150, 100),
        (55.5, 55),      # float floored to int
        ("50", 50),      # numeric string coerces
        ("abc", 0),      # garbage → MIN
        (True, 1),       # bool → 1
        (False, 0),      # bool → 0
    ],
)
def test_clamp_autonomy_percent(val, expected):
    got = clamp_autonomy_percent(val)
    assert got == expected
    assert isinstance(got, int)
    # bool is an int subclass; ensure we return pure int not bool for hygiene.
    assert type(got) is int


@pytest.mark.parametrize(
    "bot,sys_d,expected",
    [
        (None, None, 0),
        (0, 0, 0),
        (None, 30, 30),
        (50, 0, 50),
        (20, 60, 60),
        (80, 40, 80),
        (150, 200, 100),   # both clamped then max
    ],
)
def test_resolve_effective_autonomy_percent_max(bot, sys_d, expected):
    got = resolve_effective_autonomy_percent(bot, sys_d)
    assert got == expected
    assert isinstance(got, int)


@pytest.mark.parametrize(
    "p,band",
    [
        (0, "docs_only"),
        (1, "constrained"),
        (33, "constrained"),
        (34, "moderate"),
        (66, "moderate"),
        (67, "liberal"),
        (99, "liberal"),
        (100, "research"),
        (200, "research"),   # clamp first, then research
        (-5, "docs_only"),   # clamp first, then docs_only
    ],
)
def test_autonomy_band(p, band):
    assert autonomy_band(p) == band


def test_resolve_returns_pure_int_not_bool():
    """Guard: max(True, False) returns True in Python. We force int."""
    got = resolve_effective_autonomy_percent(True, False)
    assert got == 1
    assert type(got) is int


def test_clamp_rejects_none_explicitly():
    """None must map to MIN without raising."""
    assert clamp_autonomy_percent(None) == 0


def test_autonomy_band_none_safe():
    """autonomy_band should not crash on None via clamp."""
    # clamp(None) → 0 → docs_only
    assert autonomy_band(clamp_autonomy_percent(None)) == "docs_only"
