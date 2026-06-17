"""Sprint 6, T1-Safety — seed source-URL allow-list system_config defaults.

Seeds two ``system_config`` rows that gate the per-bot source-URL allow-list
implemented under :mod:`ragbot.infrastructure.safety`. Per-bot patterns
live in the existing ``bots.plan_limits`` JSONB column under the key
``allowed_source_domains`` (no new column — the JSONB blob is the
documented extension point for per-bot toggles; see
:mod:`ragbot.shared.bot_limits.PLAN_LIMIT_SCHEMA`).

Rows seeded:

1. ``source_allowlist_enabled = false`` — feature flag default OFF so
   existing tenants see byte-identical ingest behaviour until they
   explicitly opt in. Operators flip to ``true`` to enable the gate
   platform-wide; per-bot lists still drive the actual matching.

2. ``source_validator_provider = "null"`` — Strategy registry key. Set
   to ``"domain_allowlist"`` to activate the host/prefix/regex matcher.
   Unknown / empty key falls back to ``"null"`` (allow-all) at runtime
   via :func:`ragbot.infrastructure.safety.registry.build_source_validator`.

Idempotent: ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already
carrying the rows is a no-op (value/description refreshed). ``downgrade``
deletes the two keys; runtime code falls back to the constant defaults
in :mod:`ragbot.shared.constants` so the feature simply reverts to the
Null adapter.

Proof citation: Zou et al. (2024) "PoisonedRAG", arXiv:2402.07867 §6.1
— source allow-list listed as the primary structural defence against
knowledge-corruption attacks targeting RAG pipelines.

Revision ID: 0094a
Revises: 0094
Create Date: 2026-05-14

Renumbered from 0094 to 0094a during Wave K1 sequential merge to resolve
multi-head collision with sibling MoM sprint migration that also claimed
0094 (add_feature_name_to_model_invocations). Chain reordered: 0093 →
0094 (model_invocations) → 0094a (source_allowlist) → 0095. Downstream
0095 (cost_knobs) updated to reference 0094a. No DDL semantics changed.
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "source_allowlist_enabled",
        "false",
        "bool",
        "Sprint 6 T1-Safety. Gate per-bot source-URL allow-list at ingest "
        "boundary (PoisonedRAG arXiv 2402.07867 defence). When True the "
        "DocumentService validates source_url against "
        "bots.plan_limits.allowed_source_domains using the Strategy "
        "registered under source_validator_provider; rejection raises "
        "SourceNotAllowedError before chunking/embedding. Default False "
        "= no-op (allow all).",
    ),
    (
        "source_validator_provider",
        "null",
        "string",
        "Strategy registry key for the source-URL validator. 'null' = "
        "passthrough (allow all); 'domain_allowlist' = match against "
        "bot's allowed_source_domains list (host/prefix/regex). Unknown "
        "values degrade to 'null' at runtime via the safety registry.",
    ),
)


_DOWNGRADE_KEYS: tuple[str, ...] = (
    "source_allowlist_enabled",
    "source_validator_provider",
)


def upgrade() -> None:
    for key, value, value_type, description in _TUNING_ROWS:
        # system_config.value is jsonb — wrap scalar strings, pass
        # numeric/bool literals through unchanged.
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
    """Remove the Sprint 6 seed rows; code falls back to constants on read."""
    for key in _DOWNGRADE_KEYS:
        op.execute(
            text("DELETE FROM system_config WHERE key = :key").bindparams(key=key)
        )
