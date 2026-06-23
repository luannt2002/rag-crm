"""Audit-fix #2 (2026-06-23) — the conversation-state price extractor must use the
CANONICAL, domain-neutral price bounds (DEFAULT_PRICE_MIN_VND .. DEFAULT_PRICE_MAX_VND),
NOT a tenant-tuned inline 10K–50M window. A high-value package price (100M, well within
the 500M ceiling) must be captured, and no industry literal may sit in the generic
price logic. Locks the "no support riêng lẻ" mandate at the code level.
"""
from __future__ import annotations

from pathlib import Path

from ragbot.infrastructure.conversation_state.jsonb_conversation_state import (
    JsonbConversationState,
)
from ragbot.shared.constants import DEFAULT_PRICE_MAX_VND, DEFAULT_PRICE_MIN_VND

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src/ragbot/infrastructure/conversation_state/jsonb_conversation_state.py"
)


def test_extract_prices_captures_high_value_within_canonical_ceiling():
    """A 100M price (a premium package) is within the canonical 500M ceiling and
    MUST be captured — the old spa-tuned 50M ceiling silently dropped it."""
    prices = JsonbConversationState._extract_prices("Gói cao cấp 100.000.000đ")
    assert 100_000_000 in prices
    assert DEFAULT_PRICE_MAX_VND >= 100_000_000


def test_extract_prices_k_shorthand_preserved():
    """The '199k' → 199000 shorthand must survive the refactor."""
    assert 199_000 in JsonbConversationState._extract_prices("199k")


def test_extract_prices_respects_canonical_floor():
    """Nothing below the canonical floor is surfaced as a price."""
    prices = JsonbConversationState._extract_prices("còn 9.999 suất")
    assert all(p >= DEFAULT_PRICE_MIN_VND for p in prices)


def test_no_industry_literal_in_generic_price_logic():
    """Domain-neutral — the 'spa range' comment and the wrong inline 50M ceiling
    must be gone (note: 'workspace' legitimately contains the substring 'spa',
    so the assertion targets the exact offending phrase / literal)."""
    src = _SRC.read_text(encoding="utf-8")
    assert "spa range" not in src.lower()
    assert "50_000_000" not in src
