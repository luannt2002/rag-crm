"""[T1-Smartness] audit_log tamper detection — hash chain + immutable trigger

Revision ID: 010g
Revises: 010e
Create Date: 2026-05-16

Per SECURITY_AUDIT_20260516 SEC-11 + Agent F P0:
``audit_log`` is append-only at application layer but lacks DB-level
tamper detection. A compromised DB admin or SQL injection could UPDATE
or DELETE rows undetected, breaking compliance (HIPAA / GDPR audit
trail requirements).

Strategy = Hash Chain + DB Trigger Guard:

1. Add ``row_hash CHAR(64)`` column — SHA256 of ``prev_hash || critical_fields``
   (canonical field order). Each row depends on the prior row's hash,
   so any field mutation breaks the chain at that point AND every row
   downstream — visible at verify time.
2. Backfill ``row_hash`` for existing rows ordered by ``(created_at, id)``.
   The chain seed is empty bytes for the first row.
3. Mark column NOT NULL once backfilled.
4. Install ``BEFORE UPDATE OR DELETE`` trigger that ``RAISE EXCEPTION`` —
   blocks tampering at DB level even for superuser sessions that DO NOT
   ``ALTER TABLE ... DISABLE TRIGGER``. (A determined superuser CAN
   disable the trigger; the hash chain catches that case at verify.)
5. Add ``ix_audit_log_chain (created_at, id)`` index for the
   ``ORDER BY created_at, id`` scan that ``verify_audit_chain()`` does.

Canonical field order for hash input (concatenated as UTF-8 bytes,
joined by ``\\x1f`` ASCII unit separator to avoid ambiguity):
  prev_hash || US || record_tenant_id || US || workspace_id ||
  US || actor_user_id || US || action || US || resource_type ||
  US || resource_id || US || before_json::text || US || after_json::text ||
  US || reason || US || trace_id || US || created_at_iso

Notes:
  - ``before_json`` / ``after_json`` cast to ``::text`` uses Postgres'
    canonical JSONB text repr — keys re-sorted alphabetically, no
    whitespace variations. Bit-stable across versions.
  - ``created_at`` is included so a row that legitimately re-INSERTs the
    same logical event at a later time produces a distinct hash.
  - NULL fields rendered as empty string (Postgres COALESCE) — matches
    the Python hasher (``audit_log_hasher.compute_audit_row_hash``).

Backfill scope: small in dev (~5 rows). Production audit_log can grow
large; rerun the backfill as a one-off if upgrading a populated DB —
the SQL self-recovers because it ORDER BYs deterministically.
"""
from __future__ import annotations

from alembic import op


revision = "010g"
down_revision = "010e"
branch_labels = None
depends_on = None


# Unit Separator (US, 0x1F) — ambiguity-free delimiter for hash input.
_US = r"\x1f"


def _hash_expr(prev_hash_sql: str, alias: str = "o") -> str:
    """Build a SQL SHA256-hex expression for one ``audit_log`` row.

    ``prev_hash_sql`` is a SQL expression (e.g. ``''::text`` for the seed
    or ``c.row_hash`` for the recursive step). ``alias`` is the table
    alias holding the row's column values.

    ``created_at`` is normalised to UTC and formatted with the same
    ``strftime("%Y-%m-%d %H:%M:%S.%f")`` shape the Python hasher uses
    (``audit_log_hasher.compute_audit_row_hash``), so SQL backfill and
    Python writer produce bit-identical bytes.

    JSON fields use ``jsonb::text`` (Postgres' canonical sort-keys repr);
    the Python hasher mirrors this with
    ``json.dumps(sort_keys=True, separators=(",", ":"))``.
    """
    # to_char(US -> 6-digit microseconds) gives the same width as
    # Python's ``%f`` so the two sides agree byte-for-byte.
    created_at_canonical = (
        f"to_char({alias}.created_at AT TIME ZONE 'UTC', "
        f"'YYYY-MM-DD HH24:MI:SS.US')"
    )
    return (
        "encode(sha256("
        f"(COALESCE({prev_hash_sql}, '') "
        f"|| E'{_US}' || COALESCE({alias}.record_tenant_id::text, '') "
        f"|| E'{_US}' || COALESCE({alias}.workspace_id, '') "
        f"|| E'{_US}' || COALESCE({alias}.actor_user_id, '') "
        f"|| E'{_US}' || COALESCE({alias}.action, '') "
        f"|| E'{_US}' || COALESCE({alias}.resource_type, '') "
        f"|| E'{_US}' || COALESCE({alias}.resource_id, '') "
        f"|| E'{_US}' || COALESCE({alias}.before_json::text, '') "
        f"|| E'{_US}' || COALESCE({alias}.after_json::text, '') "
        f"|| E'{_US}' || COALESCE({alias}.reason, '') "
        f"|| E'{_US}' || COALESCE({alias}.trace_id, '') "
        f"|| E'{_US}' || COALESCE({created_at_canonical}, '')"
        ")::bytea), 'hex')"
    )


def upgrade() -> None:
    # 1. Add column (nullable initially so backfill can run).
    op.execute(
        "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS row_hash CHAR(64)"
    )

    # 2. Backfill via recursive CTE. ``rn`` orders deterministically by
    # ``(created_at, id)``; the recursive step pipes the prior row's
    # ``row_hash`` into the next row's hash input via the chain CTE.
    seed_hash = _hash_expr("''::text", alias="o")
    next_hash = _hash_expr("c.row_hash", alias="o")
    op.execute(
        f"""
        WITH RECURSIVE ordered AS (
            SELECT id, record_tenant_id, workspace_id, actor_user_id, action,
                   resource_type, resource_id, before_json, after_json,
                   reason, trace_id, created_at,
                   row_number() OVER (ORDER BY created_at, id) AS rn
            FROM audit_log
        ),
        chain AS (
            -- Seed: rn=1 with empty prev_hash.
            SELECT o.id, o.rn,
                   {seed_hash} AS row_hash
            FROM ordered o
            WHERE o.rn = 1
            UNION ALL
            -- Recursive: feed the prior chain row's row_hash as prev_hash.
            SELECT o.id, o.rn,
                   {next_hash} AS row_hash
            FROM ordered o
            JOIN chain c ON o.rn = c.rn + 1
        )
        UPDATE audit_log AS al
        SET row_hash = c.row_hash
        FROM chain c
        WHERE al.id = c.id;
        """
    )

    # 3. Lock NOT NULL after backfill (zero-row tables are valid no-op).
    op.execute(
        "ALTER TABLE audit_log ALTER COLUMN row_hash SET NOT NULL"
    )

    # 4. Block UPDATE / DELETE via trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
              'audit_log is append-only; UPDATE/DELETE denied (row id=%)',
              OLD.id
              USING ERRCODE = 'check_violation';
        END;
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS audit_log_immutable_trigger ON audit_log"
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_immutable_trigger
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
        """
    )

    # 5. Index for verify scan (ORDER BY created_at, id).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_chain "
        "ON audit_log (created_at, id)"
    )


def downgrade() -> None:
    # Reverse order — index, trigger, function, column.
    op.execute("DROP INDEX IF EXISTS ix_audit_log_chain")
    op.execute(
        "DROP TRIGGER IF EXISTS audit_log_immutable_trigger ON audit_log"
    )
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS row_hash")
