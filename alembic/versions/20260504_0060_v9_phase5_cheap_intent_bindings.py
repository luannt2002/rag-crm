"""Cost-aware per-purpose LLM bindings (cheap-intent route).

The orchestrator routes ``factoid`` / ``chitchat`` / ``out_of_scope`` intents
to a per-bot cheap-purpose binding while keeping ``llm_primary`` as the
universal fallback (resolved by :func:`resolve_purpose_for_intent`).

Schema impact in this migration is intentionally **minimal**: the
``bot_model_bindings.purpose`` column is already ``VARCHAR(32)`` with no
native ENUM / CHECK constraint, so the four new canonical values
(``llm_factoid``, ``llm_chitchat``, ``llm_oos``, ``llm_intent_understand``)
are accepted by the existing column without any DDL change.

What this migration does ship:

* ``COMMENT ON COLUMN bot_model_bindings.purpose`` — single source of truth
  for the canonical purpose set so DBAs / future migrations see the allowed
  vocabulary at the schema level.
* A partial index ``ix_binding_bot_cheap_purpose`` accelerating the
  cost-aware route's ``list_bindings(purpose='llm_factoid' | ...)`` query
  (the existing ``ix_binding_bot_purpose`` covers all purposes; the new
  partial index reduces scan cost when only cheap-purpose rows are
  fetched on the hot path). Created ``IF NOT EXISTS`` for idempotency.

Per CLAUDE.md the migration **does not** seed any binding row — model UUIDs
+ pricing live entirely in the ``ai_models`` table and bot-owner ops:

.. code-block:: sql

    INSERT INTO bot_model_bindings
        (record_tenant_id, record_bot_id, purpose, record_model_id,
         rank, weight, temperature, max_tokens, top_p)
    VALUES
        (:tenant, :bot, 'llm_factoid', :cheap_model_uuid,
         0, 100, 0.0, 512, 1.0);

Bots that should keep ``llm_primary`` for every intent simply skip these
inserts — the resolver auto-falls back, no flag toggle needed.

Revision ID: 0060
Revises: 0059
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


_TABLE = "bot_model_bindings"
_COLUMN = "purpose"
_CHEAP_PURPOSES = (
    "llm_factoid",
    "llm_chitchat",
    "llm_oos",
    "llm_intent_understand",
)
_CANONICAL_PURPOSES = (
    "llm_primary",
    "llm_fallback",
    *_CHEAP_PURPOSES,
    "embedding",
    "rerank",
    "understand_query",
    "decompose",
    "grading",
    "grounding",
    "rewriting",
)
_PARTIAL_INDEX = "ix_binding_bot_cheap_purpose"


def _index_exists(name: str) -> bool:
    """Return True iff the named index already exists in the public schema."""
    res = op.get_bind().execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :n"
        ),
        {"n": name},
    ).fetchone()
    return res is not None


def upgrade() -> None:
    purposes_csv = ", ".join(_CANONICAL_PURPOSES)
    comment = (
        "Canonical purpose vocabulary. "
        f"Cheap-intent route values: {', '.join(_CHEAP_PURPOSES)}. "
        f"All allowed values: {purposes_csv}. "
        "Bot owners INSERT cheap-purpose rows opt-in; resolver falls back "
        "to llm_primary when absent."
    )
    # Comment is idempotent — running twice produces the same result.
    op.execute(
        text(f"COMMENT ON COLUMN {_TABLE}.{_COLUMN} IS :c").bindparams(c=comment)
    )

    # Partial index speeds up the cost-aware route's binding lookup when
    # multiple cheap-purpose bindings live alongside the primary. Idempotent
    # via IF NOT EXISTS so reruns are safe.
    if not _index_exists(_PARTIAL_INDEX):
        purposes_sql = ", ".join(f"'{p}'" for p in _CHEAP_PURPOSES)
        op.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_PARTIAL_INDEX} "
                f"ON {_TABLE} (record_bot_id, purpose) "
                f"WHERE purpose IN ({purposes_sql}) AND active = TRUE"
            )
        )


def downgrade() -> None:
    # Drop the partial index first (cheap, reversible).
    if _index_exists(_PARTIAL_INDEX):
        op.execute(text(f"DROP INDEX IF EXISTS {_PARTIAL_INDEX}"))
    # Clear the column comment. Postgres treats COMMENT IS NULL as remove.
    op.execute(text(f"COMMENT ON COLUMN {_TABLE}.{_COLUMN} IS NULL"))
