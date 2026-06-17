"""Seed ``query_router_provider`` in ``system_config`` (default OFF).

Phase-2 Stream S9 ships the ``QueryRouterPort`` abstraction with three
strategies (Null / Regex / LLM). The router runs BEFORE embed+retrieve
and emits a coarse intent label (``structured_ref`` / ``comparison`` /
``factoid`` / ``smalltalk`` / ``hallu_trap`` / ``semantic``) used
downstream to bias retrieval strategy, tune cliff floors and select
sysprompt rendering.

Shipping the registry without a seed row would leave the bootstrap
resolver reading a missing key and falling through to the
``DEFAULT_QUERY_ROUTER_PROVIDER = "null"`` schema default — correct but
opaque to operators inspecting the config table. This migration writes
the default explicitly so the row is visible in ``system_config`` and
the operator can flip it to ``"regex"`` (or ``"llm"`` later) via a
single UPDATE without redeploying code.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running the migration
on a DB already at the new value is a no-op. Downgrade deletes the
seed row so the schema default takes over again.

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-11
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


_SEED_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "query_router_provider",
        "null",
        "string",
        "Pre-retrieve query intent classifier strategy. "
        "Values: 'null' (always semantic, backward-compat default), "
        "'regex' (fast pattern-based VN+EN classifier), "
        "'llm' (LLM-backed classifier, requires bootstrap classify_fn). "
        "Flip to 'regex' to enable lightweight intent routing without "
        "incurring LLM cost.",
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _SEED_ROWS:
        # system_config.value is jsonb — wrap scalar strings, pass numeric/bool literals through.
        json_value = json.dumps(value) if value_type == "string" else value
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, (:value)::jsonb, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=json_value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Remove the seeded row so the schema-side default takes over."""
    op.execute(
        text("DELETE FROM system_config WHERE key = 'query_router_provider'")
    )
