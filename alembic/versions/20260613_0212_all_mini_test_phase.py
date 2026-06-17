"""Make-it-work phase: route ALL nano LLM tasks → gpt-4.1-mini.

Owner directive 2026-06-13: this is still the "get it correct" phase, not the
"shave cost" phase. Premature nano downgrades risk weak outputs before a
quality baseline exists. Route every nano-bound task to gpt-4.1-mini (better
long-context comprehension, still cheap, OpenAI auto-prefix-cache), establish
the quality ceiling + real cost, THEN selectively drop back to nano where an
A/B shows no regression.

Scope (reversible):
  * system_config: contextual_retrieval_model, metadata_extraction_model,
    cascade_low_model  → "gpt-4.1-mini"
  * bot_model_bindings: every ACTIVE row pointing at gpt-4.1-nano → gpt-4.1-mini

Downgrade restores the three config keys to nano and flips the bindings back
for the purposes that were nano-bound at write time. No data touched — model
routing only; re-runnable.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0212"
down_revision = "0211"
branch_labels = None
depends_on = None

_CONFIG_KEYS = (
    "contextual_retrieval_model",
    "metadata_extraction_model",
    "cascade_low_model",
)
# Purposes observed on nano at write time — used to scope the downgrade.
_NANO_PURPOSES = (
    "enrichment", "grounding", "condensing", "condense", "grade", "grading",
    "guard", "intent", "reflect", "reflection", "rewrite", "rewriting",
    "routing", "chat", "understand_query",
)


def _set_config(conn, value: str) -> None:
    for key in _CONFIG_KEYS:
        conn.execute(sa.text(
            "UPDATE system_config SET value = CAST(:v AS jsonb) WHERE key = :k"
        ), {"v": json.dumps(value), "k": key})


def _reroute_bindings(conn, *, from_name: str, to_name: str, purposes=None) -> None:
    params = {"from_name": from_name, "to_name": to_name}
    purpose_clause = ""
    if purposes is not None:
        purpose_clause = " AND purpose = ANY(:purposes)"
        params["purposes"] = list(purposes)
    conn.execute(sa.text(
        "UPDATE bot_model_bindings SET record_model_id = "
        "(SELECT id FROM ai_models WHERE name = :to_name LIMIT 1) "
        "WHERE active = true AND record_model_id = "
        "(SELECT id FROM ai_models WHERE name = :from_name LIMIT 1)"
        + purpose_clause
    ), params)


def upgrade() -> None:
    conn = op.get_bind()
    _set_config(conn, "gpt-4.1-mini")
    _reroute_bindings(conn, from_name="gpt-4.1-nano", to_name="gpt-4.1-mini")


def downgrade() -> None:
    conn = op.get_bind()
    _set_config(conn, "gpt-4.1-nano")
    _reroute_bindings(
        conn, from_name="gpt-4.1-mini", to_name="gpt-4.1-nano",
        purposes=_NANO_PURPOSES,
    )
