"""[T1-Smartness] Domain-neutral / multi-bot fairness guard — ratchet, decreasing-only.

The platform must be FAIR to every bot: engine code may reference STRUCTURE
(entity, label, number, locale-token-from-pack) but never MEANING (a specific
bot/brand, or a single industry's first-class concept like price/VND).

Two coupling families are counted across ``src/ragbot``. Each has a BASELINE equal
to the measured count at guard-introduction; the test fails if the count INCREASES.
The numbers may only go DOWN as the codebase is generalised (PRICE-index →
ATTRIBUTE-index, language literals → language_packs). This is the same ratchet
pattern as ``test_narrow_exception_hierarchy.py``.

To add a legitimate, reviewed exception on a single line, append
``# noqa: DN001 — <reason>`` and it stops counting (and lower the baseline).

Audit that motivated this guard: ``reports/DOMAIN_NEUTRAL_BETRAYAL_AUDIT_20260625.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ragbot"

# A specific bot slug / customer brand named in engine code (logic OR comment) —
# the "support 1 bot" betrayal. Word/slug shaped to avoid false hits ("spatial").
_BOT_BRAND_RE = re.compile(
    r"medispa|dr\.?medi|chinh-sach-xe|thong-?tu|legalbot|gisbot|test-spa"
    r"|payot|landspider|citytraxx|spa-0[0-9]|xe-[0-9]|spa q[0-9]|spa Q[0-9]",
    re.IGNORECASE,
)
# Numbers below are FALSE-POSITIVE excluders for the bot/brand pattern.
_BOT_BRAND_EXCLUDE_RE = re.compile(r"spatial|despa", re.IGNORECASE)

# Price/commerce as a FIRST-CLASS engine concept — the "support 1 domain" betrayal.
# The generic path is ``attributes_json`` (ADR-0006) + a numeric-attribute index;
# every hit here is a place the engine hardcodes "price" instead of a generic field.
_PRICE_COUPLING_RE = re.compile(
    r"price_primary|price_secondary|parse_money_vn|PRICE_BUCKETS_VND"
    r"|price_of_entity|query_by_price_range|top_by_price",
)

_NOQA_RE = re.compile(r"#\s*noqa:\s*DN001")

# Measured 2026-06-25 after the full customer-literal scrub. Ratchet: DECREASE-only.
# Bot/brand is now ZERO — the engine names no single customer; any new reference
# fails CI. Price-coupling is the remaining Betrayal #1 surface; it shrinks as
# ADR-0007 (PRICE-index → ATTRIBUTE-index) lands.
_BOT_BRAND_BASELINE = 0
_PRICE_COUPLING_BASELINE = 127


def _count(pattern: re.Pattern[str], *, exclude: re.Pattern[str] | None = None) -> int:
    count = 0
    for py in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            if not pattern.search(line):
                continue
            if exclude is not None and exclude.search(line):
                continue
            if _NOQA_RE.search(line):
                continue
            count += 1
    return count


def test_no_new_per_bot_or_brand_coupling() -> None:
    """Engine code must not gain new references to a specific bot/brand/customer.

    A new mention of a bot slug or customer brand (in logic OR comment) fails this.
    Use a domain-neutral placeholder (``<a tenant bot>``, ``<a tenant document>``)
    or, if genuinely required, ``# noqa: DN001 — <reason>`` and lower the baseline.
    """
    n = _count(_BOT_BRAND_RE, exclude=_BOT_BRAND_EXCLUDE_RE)
    assert n <= _BOT_BRAND_BASELINE, (
        f"Per-bot/brand references in src/ragbot = {n}, exceeds baseline "
        f"{_BOT_BRAND_BASELINE}. The engine must be fair to every bot — name no "
        f"single customer. Scrub to a generic placeholder."
    )


def test_no_new_price_domain_coupling() -> None:
    """Engine code must not gain new price/commerce first-class coupling.

    The fair, multi-bot path is a generic labelled-attribute index (ADR-0006
    attributes_json + the planned numeric-attribute index, ADR-0007). A new
    ``price_primary`` / ``parse_money_vn`` / price-route reference fails this —
    use the generic attribute path instead.
    """
    n = _count(_PRICE_COUPLING_RE)
    assert n <= _PRICE_COUPLING_BASELINE, (
        f"Price-domain coupling tokens in src/ragbot = {n}, exceeds baseline "
        f"{_PRICE_COUPLING_BASELINE}. Price is one bot's domain, not the engine's "
        f"— route through the generic labelled-attribute path, not price_* columns."
    )
