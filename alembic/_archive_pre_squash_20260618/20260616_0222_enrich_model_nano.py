"""Switch ingest-enrichment model from gpt-4.1-mini to gpt-4.1-nano.

Cost-control for the highest-volume LLM step in the platform. The
contextual-retrieval (CR) + narrate enrichment runs once per chunk for
every ingested document, so it dominates ingest token spend. The task is
extractive ("situate this chunk in the doc, copy verbatim numbers/dates/
names") — it does NOT need a reasoning-grade model. This mirrors the
canonical Anthropic Contextual-Retrieval recipe, which uses the cheapest
model (Claude 3 Haiku) + prompt caching for exactly this step.

gpt-4.1-nano is $0.16/$0.64 per 1M vs gpt-4.1-mini $0.40/$1.60 — ~2.5×
cheaper input on the most voluminous LLM call path, with a 1M context
window (fits full-doc CR context). Paired with the warm-then-fan-out
prompt-cache change in llm_chunk_context_provider, ingest cost drops on
two axes (cheaper model + cached doc prefix).

Answer-path models (generate / grounding_check / crag grade) stay on
gpt-4.1-mini — those need quality to keep HALLU=0; only the extractive
enrichment + decompose/HyDE side is safe to run on nano.

Governed config change via alembic per the no-psql-hotfix rule
(system_config.value is in the forbidden-manual-edit set). Reversible:
downgrade restores gpt-4.1-mini verbatim.
"""
import sqlalchemy as sa
from alembic import op

revision = "0222"
down_revision = "0221"
branch_labels = None
depends_on = None

_SC = sa.table("system_config", sa.column("key", sa.String), sa.column("value", sa.Text))
_KEYS = ("contextual_retrieval_model", "enrichment_model")


def _set_model(model_json: str) -> None:
    for k in _KEYS:
        op.execute(
            _SC.update().where(_SC.c.key == op.inline_literal(k)).values(value=model_json)
        )


def upgrade() -> None:
    _set_model('"gpt-4.1-nano"')


def downgrade() -> None:
    _set_model('"gpt-4.1-mini"')
