"""[T1-Smartness] add missing FK constraints + reset orphan bots + drop dead column (MEGA-2 / G12)

Revision ID: 0107c
Revises: 0107b
Create Date: 2026-05-16

Live evidence (Coder-C2 RETRY audit, 2026-05-16):
  * DB head = ``0097``; working-tree alembic head = ``0103`` (A3 finding
    confirmed: migrations 0098-0103 never applied on this DB; ``alembic
    upgrade head`` fails at 0098 ``language_packs`` brokenness; full chain
    apply is blocked upstream).
  * 11 existing FK constraints in ``pg_constraint``; 37 ``record_*``
    columns lack FK; 30 of those 37 have an unambiguous target table.
  * MEGA-2 specific finding (DBA 7-trục audit): 5 bots referencing
    ``record_embedding_model_id = 170eb22b-8d93-46d3-ba47-62970948d6c4``
    with NO matching ``ai_models`` row → ``ModelResolverService`` silent
    fallback to ``system_config`` default; bot owner unaware. On the C2
    audit DB this row was already cleaned (count = 0), but the reset
    SQL is RETAINED so any non-converged environment lands in the same
    clean state.
  * Live psycopg2 LEFT-JOIN orphan sweep (see report
    ``REPORT_mega-260516-C2-orphan-fks.md``) returned 0 orphan rows on
    EVERY one of the 15 selected FK candidates AND on the MEGA-2 bots
    row. Adding the constraints is safe.
  * ``request_logs.record_knowledge_base_id`` is dead: target
    ``knowledge_bases`` table does NOT exist; column has 0 non-null
    values across 0 rows of ``request_logs``.

Fix (idempotent + reversible):
  1. Reset the known orphan UUID 170eb22b... on
     ``bots.record_embedding_model_id`` to NULL.
  2. Add 15 missing FK constraints (selected from 30 audited candidates,
     prioritising tenant integrity, bot scoping, and the audit chain).
  3. Drop dead column ``request_logs.record_knowledge_base_id``.

ON DELETE rationale:
  - tenant_id → tenants:        RESTRICT  (never delete a tenant with live data)
  - bot_id → bots:              CASCADE   (delete bot wipes per-bot rows)
  - *model_id → ai_models:      SET NULL  (model retire ≠ delete owning row)
  - request_id → request_logs:  CASCADE   (audit log delete cascades to children)

Failure mode (intentional): if an environment surfaces a hidden orphan
beyond the known MEGA-2 reset, ``ALTER TABLE ... ADD CONSTRAINT`` raises
SQLSTATE 23503. The migration FAILS LOUD rather than silently corrupt;
auditor must investigate + amend before re-running.

Multi-coder collision note: ``down_revision = "0107b"`` per the C-wave
spawn brief (Coder-C2 prompt). 0107b is shipped on
``origin/mega-260516-A3-db-migrations``; live apply on the C2 audit DB
is blocked until A1 (0104) + A3 (0105/0106/0107a/0107b) merge to ``main``.
That collision resolution is delegated to the Auditor merge step.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0107c"
down_revision = "0107b"
branch_labels = None
depends_on = None


# Known orphan UUID from MEGA-2 finding. This is a forensic constant
# tied to a one-time legacy data accident, NOT a domain literal subject
# to the domain-neutral rule.
_ORPHAN_EMBEDDING_MODEL_UUID = "170eb22b-8d93-46d3-ba47-62970948d6c4"


# (constraint_name, table, column, ref_table, ref_col, on_delete)
# 15 FK constraints, ordered for stable upgrade/downgrade iteration and
# for human review (tenant chain → bot chain → model chain → request chain).
_FK_CONSTRAINTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    # tenant chain — RESTRICT (tenant lifecycle is upstream, never wipe)
    ("fk_audit_log_tenant",          "audit_log",          "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_documents_tenant",          "documents",          "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_conversations_tenant",      "conversations",      "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_messages_tenant",           "messages",           "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_request_logs_tenant",       "request_logs",       "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_quotas_tenant",             "quotas",             "record_tenant_id", "tenants",   "id", "RESTRICT"),
    ("fk_guardrail_events_tenant",   "guardrail_events",   "record_tenant_id", "tenants",   "id", "RESTRICT"),
    # bot chain — CASCADE (deleting a bot wipes its data)
    ("fk_documents_bot",             "documents",          "record_bot_id",    "bots",      "id", "CASCADE"),
    ("fk_messages_bot",              "messages",           "record_bot_id",    "bots",      "id", "CASCADE"),
    ("fk_request_logs_bot",          "request_logs",       "record_bot_id",    "bots",      "id", "CASCADE"),
    ("fk_semantic_cache_bot",        "semantic_cache",     "record_bot_id",    "bots",      "id", "CASCADE"),
    # model chain — SET NULL (model retire ≠ delete owning row)
    ("fk_bots_embedding_model",      "bots",               "record_embedding_model_id", "ai_models", "id", "SET NULL"),
    ("fk_bots_model",                "bots",               "record_model_id",  "ai_models", "id", "SET NULL"),
    ("fk_request_logs_model",        "request_logs",       "record_model_id",  "ai_models", "id", "SET NULL"),
    # request chain — CASCADE (audit row delete cascades to children)
    ("fk_guardrail_events_request",  "guardrail_events",   "record_request_id","request_logs", "request_id", "CASCADE"),
)


_DEAD_COLUMN_TABLE = "request_logs"
_DEAD_COLUMN_NAME = "record_knowledge_base_id"


def upgrade() -> None:
    # 1. Reset the known orphan bots (idempotent: WHERE on the specific
    #    UUID, no-op if already NULL).
    op.execute(
        text(
            "UPDATE bots "
            "   SET record_embedding_model_id = NULL "
            " WHERE record_embedding_model_id = :orphan_uuid"
        ).bindparams(orphan_uuid=_ORPHAN_EMBEDDING_MODEL_UUID)
    )

    # 2. Add FK constraints. Each ALTER TABLE will FAIL LOUD if hidden
    #    orphans exist beyond the known reset above (PG SQLSTATE 23503).
    #    That failure is intentional — escalation to auditor required.
    for (cname, table, col, ref_table, ref_col, on_delete) in _FK_CONSTRAINTS:
        op.execute(
            text(
                f"ALTER TABLE {table} "
                f"  ADD CONSTRAINT {cname} "
                f"  FOREIGN KEY ({col}) "
                f"  REFERENCES {ref_table}({ref_col}) "
                f"  ON DELETE {on_delete}"
            )
        )

    # 3. Drop dead column (verified 0 non-null usage; ref table absent).
    op.execute(
        text(
            f"ALTER TABLE {_DEAD_COLUMN_TABLE} "
            f"  DROP COLUMN IF EXISTS {_DEAD_COLUMN_NAME}"
        )
    )


def downgrade() -> None:
    # 1. Recreate dead column (UUID, nullable, no default — original shape).
    op.execute(
        text(
            f"ALTER TABLE {_DEAD_COLUMN_TABLE} "
            f"  ADD COLUMN IF NOT EXISTS {_DEAD_COLUMN_NAME} UUID"
        )
    )

    # 2. Drop FK constraints in reverse order for symmetry.
    for (cname, table, _col, _ref_table, _ref_col, _on_delete) in reversed(_FK_CONSTRAINTS):
        op.execute(
            text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {cname}")
        )

    # 3. Orphan reset is intentionally NOT reversed — restoring the
    #    fabricated UUID would re-introduce a known-bad data state.
