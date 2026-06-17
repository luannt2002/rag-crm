"""TTL jitter helper for SystemConfigService cache fills.

Pins the contract from CLAUDE.md TTL jitter spec:
- jitter spans ±ratio of base TTL
- ratio=0 → no jitter
- ttl<=0 → returns at least 1
- never returns negative
"""

from __future__ import annotations

import random

from ragbot.application.services.system_config_service import (
    CACHE_TTL,
    _jittered_ttl,
)
from ragbot.shared.constants import DEFAULT_TTL_JITTER_RATIO


def test_jittered_ttl_within_expected_band():
    """1000 draws on default base — all within ±DEFAULT_TTL_JITTER_RATIO band."""
    base = CACHE_TTL
    spread = int(base * DEFAULT_TTL_JITTER_RATIO)
    rng = random.Random(42)
    random.seed(42)
    for _ in range(1000):
        t = _jittered_ttl()
        assert base - spread <= t <= base + spread, t


def test_jittered_ttl_zero_ratio_returns_base():
    base = 300
    for _ in range(50):
        assert _jittered_ttl(base, ratio=0.0) == base


def test_jittered_ttl_never_below_one():
    # base=10, ratio=2.0 → would push some draws below 0; clamp to 1.
    for _ in range(200):
        t = _jittered_ttl(10, ratio=2.0)
        assert t >= 1


def test_jittered_ttl_zero_ttl_clamps_to_one():
    assert _jittered_ttl(0) == 1


def test_jittered_ttl_actually_jitters():
    """1000 draws on base=1000 — should see >5 distinct values."""
    seen = {_jittered_ttl(1000, ratio=0.1) for _ in range(1000)}
    assert len(seen) > 5
