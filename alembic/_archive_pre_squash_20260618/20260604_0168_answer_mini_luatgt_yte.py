"""Answer model nano->mini for luat-giao-thong + y-te-co-ban (verify batch 2).

Revision: 0168
Prev:     0167

Same rationale as 0165: gpt-4.1-nano too weak for the answer node (citation,
key-fact extraction). Lift llm_primary to gpt-4.1-mini + max_tokens 2048 for
the two bots selected for the second clean re-upload + verify pass.

NOTE: platform default_answer_model is already gpt-4.1-mini; these bots carry
an explicit nano binding (from 0161) that overrides it. A future generic pass
could realign all bots to the platform default instead of per-bot bindings.

Sacred-rule: pure alembic DML, reversible, zero-hardcode (model by name),
bot slugs in DML are exempt (data, not code). Re-ingest not required for this
(query-time model change), but the batch re-ingests anyway for clean state.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0168"
down_revision: str | None = "0167"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOTS = ("luat-giao-thong", "y-te-co-ban")


def upgrade() -> None:
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
                SELECT id FROM ai_models
                WHERE name = 'gpt-4.1-mini' AND deleted_at IS NULL LIMIT 1
            ),
            max_tokens = 2048,
            version = bmb.version + 1,
            updated_at = NOW()
        WHERE bmb.purpose = 'llm_primary' AND bmb.active = true
          AND bmb.record_bot_id IN (SELECT id FROM bots WHERE bot_id = ANY(:slugs))
    """).bindparams(slugs=list(_BOTS)))


def downgrade() -> None:
    op.execute(text("""
        UPDATE bot_model_bindings bmb
        SET record_model_id = (
                SELECT id FROM ai_models
                WHERE name = 'gpt-4.1-nano' AND deleted_at IS NULL LIMIT 1
            ),
            max_tokens = 1024,
            version = bmb.version + 1,
            updated_at = NOW()
        WHERE bmb.purpose = 'llm_primary' AND bmb.active = true
          AND bmb.record_bot_id IN (SELECT id FROM bots WHERE bot_id = ANY(:slugs))
    """).bindparams(slugs=list(_BOTS)))
