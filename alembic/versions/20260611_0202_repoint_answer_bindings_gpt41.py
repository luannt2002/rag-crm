"""Repoint per-bot ANSWER bindings gpt-4.1-mini → gpt-4.1.

Migration 0201 switched the system_config default_answer_model, but every bot
carries an explicit ``bot_model_bindings`` row (llm_primary / generation /
llm_factoid / …) that OVERRIDES the system default — so the answer kept running
on gpt-4.1-mini. This repoints the ANSWER-generating roles to gpt-4.1 so the
4.1-vs-4.1-mini comparison actually exercises the new model.

Only answer/generation roles are moved. Sub-task roles stay cheap:
  - kept on mini/nano: chat, decompose, multi_query, enrichment, intent,
    understand_query, rewrite, grade, rerank, embedding, condense, reflection.
  - moved to gpt-4.1: llm_primary, generation, llm_factoid, llm_aggregation,
    llm_comparison, llm_multi_hop.

Reversible: downgrade moves the same rows back to gpt-4.1-mini.
"""
from alembic import op

revision = "0202"
down_revision = "0201"
branch_labels = None
depends_on = None

_ANSWER_PURPOSES = (
    "'llm_primary','generation','llm_factoid','llm_aggregation',"
    "'llm_comparison','llm_multi_hop'"
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = (SELECT id FROM ai_models WHERE name='gpt-4.1' LIMIT 1)
        WHERE purpose IN ({_ANSWER_PURPOSES})
          AND record_model_id = (SELECT id FROM ai_models WHERE name='gpt-4.1-mini' LIMIT 1);
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = (SELECT id FROM ai_models WHERE name='gpt-4.1-mini' LIMIT 1)
        WHERE purpose IN ({_ANSWER_PURPOSES})
          AND record_model_id = (SELECT id FROM ai_models WHERE name='gpt-4.1' LIMIT 1);
        """
    )
