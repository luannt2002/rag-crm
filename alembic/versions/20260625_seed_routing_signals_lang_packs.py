"""Seed ``language_packs[vi|en][routing_signals]`` — locale-scoped router signals.

Trigger (Track B of the domain-neutral fix, audit DOMAIN_NEUTRAL_BETRAYAL_
AUDIT_20260625): the stats/intent ROUTERS (query_range_parser +
heuristic_intent_classifier) hard-coded the Vietnamese signal literals inside
engine LOGIC (below/above tokens, list/count/strip phrases, superlative
tokens, price-ask signals, the measure-unit guard regex, and the Layer-1
intent regex). A non-Vietnamese bot therefore could not route a "below X" /
"list all" / superlative query and silently fell through to vector — or an
ascii-fold collision mis-routed it.

This migration moves those literals OUT of code into per-locale language-pack
content (prompt_key ``routing_signals``, JSON-encoded):

  1. ``vi`` row — serialized verbatim from ``i18n._VI_ROUTING_SIGNALS``, which
     is byte-identical to the OLD hard-coded literals. A ``vi`` bot's routing
     stays BYTE-IDENTICAL (the moved literals ARE the vi seed).
  2. ``en`` row — serialized from ``i18n._EN_ROUTING_SIGNALS`` (reasonable
     English signals) so an English bot routes on English signals instead of
     silently falling through.

Source-of-truth note: the JSON is generated from the in-memory seed via
``routing_signals_to_json`` so the DB row and the boot-guard fallback can
NEVER drift (the parity test pins this). The runtime path reads the DB row via
``LanguagePackService.get(locale, "routing_signals")``; the in-memory seed is
the last-resort fallback for a DB outage at boot.

Sacred-rule alignment:
  ✅ Pure DB INSERT via tracked alembic (rule 7 — never psql).
  ✅ Domain-neutral: signal tokens are generic function/measure words + regex
     shapes, no brand/service/price literal.
  ✅ Idempotent (ON CONFLICT DO NOTHING preserves an operator override).
  ✅ Reversible (downgrade deletes the seeded rows).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.shared.i18n import (
    _EN_ROUTING_SIGNALS,
    _VI_ROUTING_SIGNALS,
    routing_signals_to_json,
)

revision = "seed_routing_signals_260625"
down_revision = "rerank_provider_align_260625"
branch_labels = None
depends_on = None


_PROMPT_KEY = "routing_signals"
_SEED_ROWS = (
    ("vi", _PROMPT_KEY, routing_signals_to_json(_VI_ROUTING_SIGNALS)),
    ("en", _PROMPT_KEY, routing_signals_to_json(_EN_ROUTING_SIGNALS)),
)


def upgrade() -> None:
    """Insert locale-scoped routing-signal rows (vi byte-identical, en English)."""
    conn = op.get_bind()
    for code, prompt_key, content in _SEED_ROWS:
        conn.execute(
            text(
                """
                INSERT INTO language_packs (code, prompt_key, content)
                VALUES (:c, :k, :v)
                ON CONFLICT (code, prompt_key) DO NOTHING
                """,
            ),
            {"c": code, "k": prompt_key, "v": content},
        )


def downgrade() -> None:
    """Remove the seeded routing-signal rows."""
    op.execute(
        text(
            """
            DELETE FROM language_packs
            WHERE prompt_key = 'routing_signals' AND code IN ('vi', 'en')
            """,
        ),
    )
