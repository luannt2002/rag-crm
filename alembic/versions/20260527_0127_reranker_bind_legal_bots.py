"""Wire zerank-2 reranker for legal bots (tessss + thong-tu-09-2020-tt-nhnn).

Revision: 0127
Prev:     0126

Phase 1 retrospective (alembic 0123-0126 + eval round 2):
  Round 1: Faith 57.4%, Avg 51.7%
  Round 2: Faith 55.6%, Avg 50.7% — NET no improvement

Root cause discovery (multi-agent deepdive 2026-05-27):
  test-spa-id has rerank binding to zerank-2 → 225 rerank calls last 2h
  tessss   : NO rerank binding → NullReranker fallback (model_used=0)
  thong-tu : NO rerank binding → NullReranker fallback

Industry evidence (Anthropic CR 2024 + ZeroEntropy bench):
  + Reranker zerank-2: ELO 1638 (top of class), +5-8pp CtxPrecision
  + Top-K retrieve 20 → rerank cuts to top-7 (factoid) / 20 (aggregation)
  + Without reranker: raw RRF top-K passes through → noise dilutes prompt

Fix: Add rerank binding for 2 legal bots using same zerank-2 model UUID
already wired for test-spa-id. Idempotent via ON CONFLICT skip.

Sacred-rule alignment:
  ✅ Strategy + DI preserved (reranker_resolver pattern)
  ✅ 4-key identity (record_tenant_id + record_bot_id scoped)
  ✅ Reversible: downgrade removes bindings
  ✅ No psql UPDATE outside alembic
"""

from alembic import op
from sqlalchemy import text

revision: str = "0127"
down_revision: str | None = "0126"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# zerank-2 model UUID (verified live 2026-05-27)
_ZERANK_2_MODEL_ID = "314cf5ad-6184-4af0-8c32-3cb164eb9c20"


def upgrade() -> None:
    """Add rerank binding for tessss + thong-tu-09-2020-tt-nhnn."""
    op.execute(
        text(
            """
            INSERT INTO bot_model_bindings (
                id, record_tenant_id, record_bot_id, record_model_id,
                purpose, active, rank, weight, version,
                temperature, top_p, max_tokens, extra_params,
                created_at, updated_at, effective_from, workspace_id
            )
            SELECT
                gen_random_uuid(),
                b.record_tenant_id,
                b.id,
                CAST(:model_id AS uuid),
                'rerank',
                true,
                0,
                100,
                1,
                0.30,
                1.00,
                450,
                '{}'::jsonb,
                NOW(),
                NOW(),
                NOW(),
                b.workspace_id
            FROM bots b
            WHERE b.bot_id IN ('tessss', 'thong-tu-09-2020-tt-nhnn')
              AND b.is_deleted = false
              AND NOT EXISTS (
                  SELECT 1 FROM bot_model_bindings bmb
                  WHERE bmb.record_bot_id = b.id
                    AND bmb.purpose = 'rerank'
                    AND bmb.deleted_at IS NULL
              )
            """
        ).bindparams(model_id=_ZERANK_2_MODEL_ID),
    )


def downgrade() -> None:
    """Remove rerank binding for the 2 legal bots."""
    op.execute(
        text(
            """
            UPDATE bot_model_bindings
            SET active = false, deleted_at = NOW(), updated_at = NOW()
            WHERE purpose = 'rerank'
              AND record_bot_id IN (
                  SELECT id FROM bots
                  WHERE bot_id IN ('tessss', 'thong-tu-09-2020-tt-nhnn')
              )
            """
        ),
    )
