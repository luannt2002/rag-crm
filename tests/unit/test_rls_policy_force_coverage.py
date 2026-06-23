"""Regression guard: every tenant-scoped table has a tenant_isolation policy,
FORCE ROW LEVEL SECURITY, and the missing-ok ``, true`` flag on
``current_setting('app.tenant_id')``.

Why this exists (F-5): the prior RLS test suite only source-greps Python for
``record_tenant_id`` usage. It cannot catch a DDL-level gap such as a table
that is ``ENABLE``d for RLS but never ``FORCE``d (so the owning role still
bypasses the policy), or a policy whose predicate omits the ``, true`` flag
and therefore throws ``unset parameter`` instead of denying when the GUC is
unbound. ``document_service_index`` had exactly that gap (the only 1/21 table
missing FORCE + the only policy without ``, true``). This test reads the
authoritative schema DDL — the squashed baseline plus every later alembic
migration — and asserts parity across all 21 tenant tables, so a future table
that forgets FORCE or the missing-ok flag fails CI rather than shipping a
silent cross-tenant write hole.

This is a pure code-introspection test (no DB) so it runs in the unit tier.
"""

from __future__ import annotations

import re
from pathlib import Path

# The 21 tenant-scoped tables that carry RLS in the canonical schema. Sourced
# from the ``ENABLE ROW LEVEL SECURITY`` set in the squashed baseline; listed
# explicitly here so a dropped policy is caught as a missing assertion rather
# than silently shrinking the checked set.
TENANT_SCOPED_TABLES: frozenset[str] = frozenset(
    {
        "audit_log",
        "bot_model_bindings",
        "bots",
        "conversations",
        "document_chunks",
        "documents",
        "document_service_index",
        "guardrail_events",
        "jobs",
        "knowledge_edges",
        "messages",
        "model_invocations",
        "outbox",
        "prompt_templates",
        "prompt_versions",
        "quotas",
        "refuse_suggestions",
        "request_logs",
        "request_steps",
        "semantic_cache",
        "tenant_model_policy",
    }
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_DIR = _REPO_ROOT / "alembic"
_BASELINE = _ALEMBIC_DIR / "squashed_baseline.sql"
_VERSIONS = _ALEMBIC_DIR / "versions"


def _schema_ddl() -> str:
    """Concatenate the FORWARD schema DDL: squashed baseline + every migration's
    ``upgrade()`` body. A later migration can ALTER an earlier definition (e.g.
    add FORCE or recreate a policy), so the union of applied-forward DDL is the
    source of truth. ``downgrade()`` bodies are excluded — they never run on the
    forward path and would otherwise reintroduce superseded (pre-fix) DDL.
    """
    parts: list[str] = []
    if _BASELINE.is_file():
        parts.append(_BASELINE.read_text(encoding="utf-8"))
    if _VERSIONS.is_dir():
        for mig in sorted(_VERSIONS.glob("*.py")):
            parts.append(_upgrade_body(mig.read_text(encoding="utf-8")))
    return "\n".join(parts)


def _upgrade_body(src: str) -> str:
    """Return the text of a migration's ``upgrade()`` function only.

    Slices from the ``def upgrade(`` line up to the next top-level ``def`` (the
    ``downgrade``). Migrations in this repo follow that two-function shape; a
    migration without an ``upgrade`` contributes nothing.
    """
    m = re.search(r"\ndef upgrade\(", src)
    if not m:
        return ""
    body = src[m.start() :]
    nxt = re.search(r"\ndef \w+\(", body[1:])
    return body[: nxt.start() + 1] if nxt else body


def _tables_with(pattern_for_table: str, ddl: str) -> set[str]:
    """Return the set of tenant tables for which ``pattern_for_table`` (a regex
    with a single ``{tbl}`` placeholder) matches somewhere in the DDL.
    """
    found: set[str] = set()
    for tbl in TENANT_SCOPED_TABLES:
        rx = re.compile(pattern_for_table.format(tbl=re.escape(tbl)))
        if rx.search(ddl):
            found.add(tbl)
    return found


def test_all_tenant_tables_have_isolation_policy() -> None:
    """Every tenant-scoped table must declare a ``tenant_isolation`` policy."""
    ddl = _schema_ddl()
    have = _tables_with(
        r"CREATE POLICY\s+tenant_isolation\s+ON\s+public\.{tbl}\b", ddl
    )
    missing = TENANT_SCOPED_TABLES - have
    assert not missing, f"tables missing tenant_isolation policy: {sorted(missing)}"


def test_all_tenant_tables_force_row_level_security() -> None:
    """Every tenant-scoped table must FORCE RLS so the owning role cannot bypass.

    ENABLE alone leaves the table owner (and any BYPASSRLS-adjacent role)
    unfenced; FORCE applies the policy to the owner too. ``document_service_index``
    was the single table that had ENABLE without FORCE (F-4b).
    """
    ddl = _schema_ddl()
    have = _tables_with(
        r"ALTER TABLE(?:\s+ONLY)?\s+public\.{tbl}\s+FORCE ROW LEVEL SECURITY",
        ddl,
    )
    missing = TENANT_SCOPED_TABLES - have
    assert not missing, f"tables missing FORCE ROW LEVEL SECURITY: {sorted(missing)}"


def test_isolation_policies_use_missing_ok_flag() -> None:
    """Every tenant_isolation policy must read the GUC with the ``, true``
    missing-ok flag.

    ``current_setting('app.tenant_id')`` (no second arg) raises when the GUC is
    unbound, turning an isolation predicate into a hard error; ``, true`` makes
    it return NULL → row denied. The ``document_service_index`` policy was the
    only one lacking it (F-4b).
    """
    ddl = _schema_ddl()
    # For each table, slice each CREATE POLICY statement up to the next
    # CREATE POLICY (or end of DDL) — terminator-agnostic, since the squashed
    # baseline ends statements with ``;`` while a migration embeds the same DDL
    # as a Python string with no SQL terminator. Later migrations override
    # earlier definitions and are concatenated AFTER the baseline, so the FINAL
    # block per table reflects the applied-forward schema.
    offenders: list[str] = []
    for tbl in sorted(TENANT_SCOPED_TABLES):
        rx = re.compile(
            r"CREATE POLICY\s+tenant_isolation\s+ON\s+public\."
            + re.escape(tbl)
            + r"\b.*?(?=CREATE POLICY|\Z)",
            re.DOTALL,
        )
        matches = rx.findall(ddl)
        assert matches, f"no CREATE POLICY found for {tbl}"
        final = matches[-1]
        if "current_setting('app.tenant_id'::text, true)" not in final and (
            "current_setting('app.tenant_id', true)" not in final
        ):
            offenders.append(tbl)
    assert not offenders, (
        "tenant_isolation policies missing the ', true' missing-ok flag on "
        f"app.tenant_id: {offenders}"
    )
