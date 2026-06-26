"""Swap platform reranker jina_ai → zeroentropy (Jina keys burned: 403/1010).

Live-verified 2026-06-26: ALL Jina API keys (2 old + 3 new) return HTTP 403
error 1010 ("insufficient balance") on every model/endpoint — the free-tier token
quota is exhausted, so the Jina reranker is the Null/fallback path system-wide
(every query degrades to RRF). The ZeroEntropy key in env tests 200 OK on
zerank-1/zerank-2/zerank-1-small.

Fix: align ``system_config`` to the VERIFIED-working ZeroEntropy provider —
``reranker_provider='zeroentropy'`` + ``reranker_model='zerank-2'`` (ai_models row
``zerank-2`` → provider ``zeroentropy``, enabled, DB-verified). The resolver
``_lookup_platform_default`` then JOINs a real row and returns ZeroEntropyReranker
instead of the Null object.

Content-state via tracked migration (sacred-rule 7 — never psql). Reverses
``rerank_provider_align_260625`` (which had pointed at the now-burned Jina keys).
"""
from __future__ import annotations

from alembic import op

revision = "rerank_swap_zeroentropy_260626"
down_revision = "seed_routing_signals_260625"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON string values (system_config.value is JSONB) → keep the quotes.
    op.execute(
        "UPDATE system_config SET value = '\"zeroentropy\"' "
        "WHERE key = 'reranker_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"zerank-2\"' "
        "WHERE key = 'reranker_model'",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE system_config SET value = '\"jina_ai\"' "
        "WHERE key = 'reranker_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"jina-reranker-v3\"' "
        "WHERE key = 'reranker_model'",
    )
