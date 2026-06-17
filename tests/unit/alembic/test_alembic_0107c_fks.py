"""Regression test for mega-sprint G12 — alembic 0107c.

Validates the structural shape of the migration: 15 FK constraints
declared, known-orphan reset present, dead column drop present, downgrade
symmetric, no domain-literal leak.

Live-DB application of the chain ``0103 → 0107c`` is gated on the
upstream A1 (0104) + A3 (0105/0106/0107a/0107b) merges. This file
performs a static-analysis audit only, plus an OPTIONAL pg_constraint
introspection probe that runs IFF a fully-migrated DB is reachable
(skipped silently when not — A3 finding: live DB blocked at 0098).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "alembic/versions/20260516_0107c_missing_fks_orphan_reset.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mig_0107c", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src() -> str:
    return _MIGRATION.read_text()


# ---------- static structural tests ----------


def test_migration_file_exists() -> None:
    assert _MIGRATION.exists(), f"missing migration: {_MIGRATION}"


def test_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "0107c"
    assert mod.down_revision == "0107b"


def test_fifteen_fk_constraints_declared() -> None:
    mod = _load_module()
    fks = mod._FK_CONSTRAINTS
    assert isinstance(fks, tuple)
    assert len(fks) == 15, f"expected 15 FK rows, got {len(fks)}"


def test_fk_tuple_shape_well_formed() -> None:
    mod = _load_module()
    seen_names: set[str] = set()
    for row in mod._FK_CONSTRAINTS:
        assert len(row) == 6, f"bad FK row arity: {row!r}"
        cname, table, col, ref_table, ref_col, on_delete = row
        for s in (cname, table, col, ref_table, ref_col, on_delete):
            assert isinstance(s, str) and s, f"empty/non-str field in {row!r}"
        assert cname not in seen_names, f"duplicate constraint name: {cname}"
        seen_names.add(cname)
        assert on_delete in {"CASCADE", "RESTRICT", "SET NULL", "NO ACTION"}, on_delete


def test_required_fk_targets_present() -> None:
    """Spec-required FKs (CODE_FLOW_DB_DESIGN_DEEPDIVE.md §1.2 + Coder-C2 brief)."""
    mod = _load_module()
    targets = {(r[1], r[2]) for r in mod._FK_CONSTRAINTS}
    required = {
        ("documents", "record_bot_id"),
        ("documents", "record_tenant_id"),
        ("request_logs", "record_tenant_id"),
        ("request_logs", "record_bot_id"),
        ("conversations", "record_tenant_id"),
        ("bots", "record_embedding_model_id"),
    }
    missing = required - targets
    assert not missing, f"missing required FKs: {missing}"


def test_on_delete_policy_per_target() -> None:
    """ON DELETE rationale from migration docstring is followed."""
    mod = _load_module()
    for cname, table, col, ref_table, _ref_col, on_delete in mod._FK_CONSTRAINTS:
        if ref_table == "tenants":
            assert on_delete == "RESTRICT", f"{cname} → tenants must RESTRICT, got {on_delete}"
        elif ref_table == "bots":
            assert on_delete == "CASCADE", f"{cname} → bots must CASCADE, got {on_delete}"
        elif ref_table == "ai_models":
            assert on_delete == "SET NULL", f"{cname} → ai_models must SET NULL, got {on_delete}"
        elif ref_table == "request_logs":
            assert on_delete == "CASCADE", f"{cname} → request_logs must CASCADE, got {on_delete}"


def test_orphan_reset_idempotent_and_targets_known_uuid() -> None:
    src = _src()
    assert "170eb22b-8d93-46d3-ba47-62970948d6c4" in src
    # Strip Python multi-line string-concat artifacts then collapse whitespace
    # so the regex matches across `"..." "..."` literals.
    src_one_line = re.sub(r'"\s*"', "", src)
    src_one_line = re.sub(r"\s+", " ", src_one_line)
    assert re.search(
        r"UPDATE\s+bots\s+SET\s+record_embedding_model_id\s*=\s*NULL",
        src_one_line,
        re.IGNORECASE,
    ), "missing idempotent NULL reset on bots.record_embedding_model_id"


def test_dead_column_drop_present() -> None:
    src = _src()
    assert "DROP COLUMN IF EXISTS" in src
    assert "record_knowledge_base_id" in src


def test_downgrade_recreates_dead_column_and_drops_fks() -> None:
    src = _src()
    assert "ADD COLUMN IF NOT EXISTS" in src
    assert "DROP CONSTRAINT IF EXISTS" in src


def test_module_has_upgrade_and_downgrade_callables() -> None:
    mod = _load_module()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_no_brand_or_customer_literals() -> None:
    """CLAUDE.md domain-neutral rule."""
    src = _src().lower()
    forbidden = ("medispa", "vietcombank", "innocom", "<brand>")
    for word in forbidden:
        assert word not in src, f"brand/customer literal leaked: {word}"


def test_no_version_ref_violation() -> None:
    """CLAUDE.md no version-ref rule (excluding alembic revision IDs)."""
    src = _src()
    # Strip alembic identifiers that legitimately reference revision numbers.
    stripped = re.sub(r"\b0\d{3}[a-z]?\b", "", src)
    forbidden_patterns = [r"_legacy\b", r"_v[0-9]\b", r"_old\b", r"\bSprint\s+S?\d+\b"]
    for pat in forbidden_patterns:
        assert not re.search(pat, stripped), f"version-ref leak: {pat}"


# ---------- optional pg_constraint introspection ----------


def _live_dsn() -> str | None:
    """Return sync DSN if a fully-migrated DB is reachable, else None."""
    raw = os.environ.get("DATABASE_URL_SYNC", "")
    if not raw:
        return None
    return raw.replace("postgresql+psycopg2://", "postgresql://")


@pytest.mark.skipif(
    _live_dsn() is None,
    reason="DATABASE_URL_SYNC not set — live introspection skipped",
)
def test_live_pg_constraint_post_apply() -> None:
    """If DB is migrated past 0107c, every declared FK MUST exist in pg_constraint.

    Skips silently if the DB has not yet reached 0107c (most envs at the
    time of this commit — A3 finding: chain blocked at 0098).
    """
    psycopg2 = pytest.importorskip("psycopg2")
    mod = _load_module()
    dsn = _live_dsn()
    assert dsn is not None  # mypy / safety
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        if not row or row[0] != "0107c":
            pytest.skip(f"DB not at 0107c (currently {row[0] if row else 'unknown'})")
        # All 15 declared FK constraints must exist in pg_constraint.
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f'"
        )
        live = {r[0] for r in cur.fetchall()}
        declared = {row[0] for row in mod._FK_CONSTRAINTS}
        missing = declared - live
        assert not missing, f"declared FKs not present in DB: {missing}"
    finally:
        conn.close()
