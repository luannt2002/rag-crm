"""[T2-CostPerf] guardrail_rules table + seed 12 platform defaults (Agent J)

Revision ID: 010f
Revises: 010c
Create Date: 2026-05-16

DB-driven moderation: lifts the 12 hard-compiled regex patterns out of
``infrastructure/guardrails/local_guardrail.py`` into a queryable table so
admins can add/disable rules without a code deploy. Per CLAUDE.md
Strategy + DI mandate — "Add provider = add 1 file, KHÔNG sửa orchestrator".
Same logic applies to moderation rules; runtime config beats build-time
constants.

Schema:
  - ``id``               UUID PK (gen_random_uuid)
  - ``record_tenant_id`` UUID NULL — NULL ⇒ platform default,
                                     non-NULL ⇒ tenant override
  - ``workspace_id``     VARCHAR(64) NOT NULL DEFAULT 'system'
  - ``rule_id``          VARCHAR(64) NOT NULL — stable identifier
  - ``pattern``          TEXT NOT NULL — regex source (no leading flags)
  - ``pattern_flags``    VARCHAR(32) NOT NULL DEFAULT '' — csv re flag names
  - ``severity``         VARCHAR(16) NOT NULL — info | warn | block
  - ``action_taken``     VARCHAR(16) NOT NULL — allow | redact | block | hitl
  - ``scope``            VARCHAR(16) NOT NULL — input | output | both
  - ``enabled``          BOOLEAN NOT NULL DEFAULT true
  - ``priority``         INT NOT NULL DEFAULT 100 — ASC = run earlier
  - ``metadata_json``    JSONB NOT NULL DEFAULT '{}'::jsonb
  - ``created_at``/``updated_at`` TIMESTAMPTZ

Indexes:
  - ``ix_guardrail_rules_tenant_scope_enabled`` partial on enabled=true
  - ``uq_guardrail_rules_tenant_rule`` partial unique (per-tenant override)
  - ``uq_guardrail_rules_platform_default_rule`` partial unique (platform)

Seed:
  12 platform-default rows mirroring the previous hard-coded patterns from
  ``local_guardrail.py``. Source-of-truth dict lives in
  ``infrastructure/guardrails/_default_patterns.py``; this migration
  imports it so a missed-edit can't drift the DB from the runtime fallback.

Rollback:
  ``downgrade()`` drops the table outright; seed rows are recreated on
  next ``upgrade``.
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op


revision = "010f"
down_revision = "010g"
branch_labels = None
depends_on = None


def _seed_rows() -> list[dict]:
    """Materialise platform-default rule rows from the SSoT module.

    Imported lazily so alembic env loading (which spins up the whole
    ragbot package) does not fail collection if the patterns module
    moves; the import error surfaces in upgrade() with a clear traceback.
    """
    from ragbot.infrastructure.guardrails._default_patterns import (
        DEFAULT_GUARDRAIL_RULES,
    )

    out: list[dict] = []
    for row in DEFAULT_GUARDRAIL_RULES:
        out.append(
            {
                "id": str(uuid.uuid4()),
                "record_tenant_id": None,
                "workspace_id": "system",
                "rule_id": row["rule_id"],
                "pattern": row["pattern"],
                "pattern_flags": row["pattern_flags"],
                "severity": row["severity"],
                "action_taken": row["action_taken"],
                "scope": row["scope"],
                "enabled": True,
                "priority": row["priority"],
                "metadata_json": json.dumps(row["metadata"]),
            },
        )
    return out


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE guardrail_rules (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          record_tenant_id UUID NULL,
          workspace_id VARCHAR(64) NOT NULL DEFAULT 'system',
          rule_id VARCHAR(64) NOT NULL,
          pattern TEXT NOT NULL,
          pattern_flags VARCHAR(32) NOT NULL DEFAULT '',
          severity VARCHAR(16) NOT NULL,
          action_taken VARCHAR(16) NOT NULL,
          scope VARCHAR(16) NOT NULL,
          enabled BOOLEAN NOT NULL DEFAULT true,
          priority INT NOT NULL DEFAULT 100,
          metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_guardrail_rules_severity
            CHECK (severity IN ('info','warn','block')),
          CONSTRAINT ck_guardrail_rules_action
            CHECK (action_taken IN ('allow','redact','block','hitl')),
          CONSTRAINT ck_guardrail_rules_scope
            CHECK (scope IN ('input','output','both'))
        )
        """,
    )
    op.execute(
        "CREATE INDEX ix_guardrail_rules_tenant_scope_enabled "
        "ON guardrail_rules (record_tenant_id, scope) "
        "WHERE enabled = true",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_guardrail_rules_tenant_rule "
        "ON guardrail_rules (record_tenant_id, rule_id) "
        "WHERE record_tenant_id IS NOT NULL",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_guardrail_rules_platform_default_rule "
        "ON guardrail_rules (rule_id) "
        "WHERE record_tenant_id IS NULL",
    )

    # Seed the 12 platform-default rows from SSoT.
    conn = op.get_bind()
    rows = _seed_rows()
    if rows:
        conn.execute(
            sa.text(
                """
                INSERT INTO guardrail_rules
                  (id, record_tenant_id, workspace_id, rule_id, pattern,
                   pattern_flags, severity, action_taken, scope, enabled,
                   priority, metadata_json)
                VALUES
                  (:id, :record_tenant_id, :workspace_id, :rule_id, :pattern,
                   :pattern_flags, :severity, :action_taken, :scope, :enabled,
                   :priority, CAST(:metadata_json AS JSONB))
                """,
            ),
            rows,
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guardrail_rules CASCADE")
