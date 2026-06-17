"""LLM provider failover — bind ``bot_model_bindings.record_fallback_model_id``.

When a primary LLM call raises ``CircuitBreakerOpen`` or a retryable
LiteLLM transient error (5xx / connect drop), the router consults this
column for a same-tier alternate model UUID and retries the request once
on that fallback. ``NULL`` keeps the bot on the primary call as terminal
(no failover, per-bot opt-out).

This migration normalises the schema for that path so the resolver +
router can rely on:

* Column ``record_fallback_model_id UUID NULL`` — already created by an
  earlier migration on most environments; we add it idempotently when
  absent so a clean clone matches.
* Foreign key ``fk_bindings_fallback_model`` → ``ai_models(id) ON DELETE
  SET NULL`` — when an admin retires the fallback model the binding
  silently reverts to no-failover instead of cascade-failing live
  traffic.
* Partial index ``ix_bindings_fallback_model_id`` covering only rows with
  a configured fallback. Hot-path JOINs from the failover wrap can scan a
  tiny subset (most bindings have NULL) without paying for the full
  column index a previous migration shipped.

Down-migrations reverse the order: drop the partial index, drop the FK,
drop the column. Idempotent guards (``information_schema`` /
``pg_indexes`` / ``pg_constraint`` lookups) keep re-runs safe across
environments where part of the schema already exists.

Revision ID: 0061
Revises: 0060
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = "0060"


_TABLE = "bot_model_bindings"
_COLUMN = "record_fallback_model_id"
_FK_NAME = "fk_bindings_fallback_model"
_PARTIAL_INDEX = "ix_bindings_fallback_model_id"
# Pre-existing full-column index from the v0.3.0 schema (migration 0009).
# Replaced here with a partial index — most bindings have NULL fallback so
# scanning the IS NOT NULL subset is materially cheaper on the hot path.
_LEGACY_INDEX = "ix_bmb_record_fallback_model_id"


def _column_exists(table: str, column: str) -> bool:
    res = op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return res is not None


def _index_exists(name: str) -> bool:
    res = op.get_bind().execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :n"
        ),
        {"n": name},
    ).fetchone()
    return res is not None


def _constraint_exists(name: str) -> bool:
    res = op.get_bind().execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
        {"n": name},
    ).fetchone()
    return res is not None


def upgrade() -> None:
    # 1. Column — guard against re-runs on environments where v0.3.0
    #    schema already shipped the column.
    if not _column_exists(_TABLE, _COLUMN):
        op.execute(
            text(
                f"ALTER TABLE {_TABLE} "
                f"ADD COLUMN {_COLUMN} UUID NULL"
            )
        )

    # 2. FK — recover the constraint when the column was added without one.
    if not _constraint_exists(_FK_NAME):
        op.execute(
            text(
                f"ALTER TABLE {_TABLE} "
                f"ADD CONSTRAINT {_FK_NAME} "
                f"FOREIGN KEY ({_COLUMN}) REFERENCES ai_models(id) "
                f"ON DELETE SET NULL"
            )
        )

    # 3. Replace the legacy full-column index with the partial variant so
    #    the failover JOIN walks only the non-NULL slice.
    if _index_exists(_LEGACY_INDEX):
        op.execute(text(f"DROP INDEX IF EXISTS {_LEGACY_INDEX}"))

    if not _index_exists(_PARTIAL_INDEX):
        op.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_PARTIAL_INDEX} "
                f"ON {_TABLE} ({_COLUMN}) "
                f"WHERE {_COLUMN} IS NOT NULL"
            )
        )


def downgrade() -> None:
    # Reverse order: index → FK → column.
    if _index_exists(_PARTIAL_INDEX):
        op.execute(text(f"DROP INDEX IF EXISTS {_PARTIAL_INDEX}"))

    if _constraint_exists(_FK_NAME):
        op.execute(
            text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_FK_NAME}")
        )

    if _column_exists(_TABLE, _COLUMN):
        op.execute(text(f"ALTER TABLE {_TABLE} DROP COLUMN {_COLUMN}"))
