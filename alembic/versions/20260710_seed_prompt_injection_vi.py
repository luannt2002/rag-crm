"""Seed the Vietnamese prompt-injection platform-default guardrail rule.

The English-only ``prompt_injection`` rule let Vietnamese injections through
("bỏ qua hướng dẫn trước đó" → no match), so a VN-phrased jailbreak bypassed
the input guard. This seeds a NON-classic ``prompt_injection_vi`` rule
(scope=input, block) — the shape ``check_input`` enforces via
``_run_db_input_regex_rules``. The pattern is pulled from the single source of
truth (``_default_patterns.DEFAULT_GUARDRAIL_RULES``) so code and DB cannot
drift.

Platform-default row: ``record_tenant_id = NULL``, ``workspace_id = 'system'``
(mirrors the existing ``prompt_injection`` row). Idempotent via
``WHERE NOT EXISTS`` — a re-run or a live DB that already has the row is a
no-op, and a tenant's own override is never touched.

Revision ID: seed_prompt_injection_vi_260710
Revises: seed_cliff_mmr_parity_260709
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from ragbot.infrastructure.guardrails._default_patterns import (
    DEFAULT_GUARDRAIL_RULES,
)

revision = "seed_prompt_injection_vi_260710"
down_revision = "seed_cliff_mmr_parity_260709"
branch_labels = None
depends_on = None

_RULE_ID = "prompt_injection_vi"
_RULE = next(r for r in DEFAULT_GUARDRAIL_RULES if r["rule_id"] == _RULE_ID)


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO guardrail_rules
              (id, record_tenant_id, workspace_id, rule_id, pattern,
               pattern_flags, severity, action_taken, scope, enabled,
               priority, metadata_json, created_at, updated_at)
            SELECT gen_random_uuid(), NULL, 'system', :rule_id, :pattern,
                   :flags, :severity, :action, :scope, true, :priority,
                   '{}'::jsonb, now(), now()
            WHERE NOT EXISTS (
                SELECT 1 FROM guardrail_rules
                WHERE rule_id = :rule_id AND record_tenant_id IS NULL
            )
            """
        ).bindparams(
            rule_id=_RULE_ID,
            pattern=_RULE["pattern"],
            flags=_RULE["pattern_flags"],
            severity=_RULE["severity"],
            action=_RULE["action_taken"],
            scope=_RULE["scope"],
            priority=_RULE["priority"],
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "DELETE FROM guardrail_rules "
            "WHERE rule_id = :rule_id AND record_tenant_id IS NULL"
        ).bindparams(rule_id=_RULE_ID)
    )
