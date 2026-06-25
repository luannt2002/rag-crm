"""Align reranker system_config provider/model so the resolver finds a row.

Bug (CHUẨN-audit 2026-06-25, live-verified): the reranker was the Null Object
SYSTEM-WIDE for every bot without an explicit purpose='rerank' binding. Cause —
``system_config`` drift:
    reranker_provider = "jina"        (NOT a valid provider code)
    reranker_model    = "zerank-2"    (belongs to provider 'zeroentropy')
``reranker_resolver._lookup_platform_default`` JOINs ai_models.name=reranker_model
with ai_providers.code=reranker_provider → 0 rows (name='zerank-2' AND code='jina')
→ returns None → NullReranker. Symptom: warranty/factoid queries collapse to a
single mis-ranked chunk (cliff keeps top-1 because no real rerank score exists).

Fix: align to the VERIFIED-working Jina reranker (keys rotated + tested 200 this
session): provider code 'jina_ai' + model 'jina-reranker-v3' (both enabled in
ai_models, DB-verified). Mirrors the embedding binding (jina-embeddings-v3 under
jina_ai), so embed + rerank share one provider/key pool.

This is content-state via tracked migration (sacred-rule 7 — never psql).
"""
from __future__ import annotations

from alembic import op

revision = "rerank_provider_align_260625"
down_revision = "stats_idx_synonyms_260624"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON string values (system_config.value is JSONB) → keep the quotes.
    op.execute(
        "UPDATE system_config SET value = '\"jina_ai\"' "
        "WHERE key = 'reranker_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"jina-reranker-v3\"' "
        "WHERE key = 'reranker_model'",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE system_config SET value = '\"jina\"' "
        "WHERE key = 'reranker_provider'",
    )
    op.execute(
        "UPDATE system_config SET value = '\"zerank-2\"' "
        "WHERE key = 'reranker_model'",
    )
