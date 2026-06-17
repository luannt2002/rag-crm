"""Coverage for ``scripts/audit_bot_sysprompt_rules.py``.

T1-Smartness — verify pure helpers (no live DB) in the audit script:

1. ``resolve_dsn`` strips ``+asyncpg`` / ``+psycopg`` suffix and prefers
   ``DATABASE_URL_SYNC`` over ``DATABASE_URL``.
2. ``resolve_dsn`` exits 2 when no env var is set.
3. SQL builder ``bot_sysprompt_audit_query`` contains the three marker
   columns + skip-empty filter + ordering clause.
4. ``format_audit_row`` returns ``⚠`` marker when all three flags are
   False and ``✓`` when at least one is True.
5. ``is_bot_missing_all_anti_fake`` matches table semantics.
6. JSON output schema is stable (downstream tooling rely on it).
7. ``build_parser`` exposes ``--dsn`` and ``--json-out`` only (no
   bot-name filter — domain-neutral by construction).

End-to-end ``run()`` is NOT covered (needs live DB); the SQL builder +
formatter contract is enough to guard the script.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_audit() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "audit_bot_sysprompt_rules.py"
    assert script_path.exists(), f"script missing: {script_path}"
    spec = importlib.util.spec_from_file_location("_audit_sysprompt", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_audit_sysprompt"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- resolve_dsn -----------------------------------------------
def test_resolve_dsn_strips_asyncpg_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _load_audit()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    assert audit.resolve_dsn(None) == "postgresql://u:p@h:5432/db"


def test_resolve_dsn_strips_psycopg_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _load_audit()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@h/db")
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    assert audit.resolve_dsn(None) == "postgresql://u:p@h/db"


def test_resolve_dsn_prefers_sync_url_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _load_audit()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://a/x")
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql://b/y")
    assert audit.resolve_dsn(None) == "postgresql://b/y"


def test_resolve_dsn_cli_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _load_audit()
    monkeypatch.setenv("DATABASE_URL", "postgresql://ignored/db")
    assert (
        audit.resolve_dsn("postgresql://override/db")
        == "postgresql://override/db"
    )


def test_resolve_dsn_missing_env_exits_code_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit = _load_audit()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    with pytest.raises(SystemExit) as exc:
        audit.resolve_dsn(None)
    assert exc.value.code == audit.EXIT_DB_UNREACHABLE
    err = capsys.readouterr().err
    assert "DATABASE_URL not set" in err


# ---------------- SQL builder -----------------------------------------------
def test_bot_sysprompt_audit_query_lists_three_markers() -> None:
    audit = _load_audit()
    sql = audit.bot_sysprompt_audit_query()
    # Three boolean columns surfaced — name kept stable for downstream
    # JSON consumers.
    assert "has_anti_fake" in sql
    assert "has_anti_fabricate_vi" in sql
    assert "has_anti_hallucinate" in sql


def test_bot_sysprompt_audit_query_skip_empty_prompts() -> None:
    audit = _load_audit()
    sql = audit.bot_sysprompt_audit_query()
    # Empty / template-default prompts (<= 50 chars) skipped so they
    # don't dominate the WARN column.
    assert "system_prompt IS NOT NULL" in sql
    assert "LENGTH(system_prompt)" in sql
    assert f"> {audit.MIN_PROMPT_LENGTH}" in sql


def test_bot_sysprompt_audit_query_orders_by_prompt_size() -> None:
    audit = _load_audit()
    sql = audit.bot_sysprompt_audit_query()
    assert "ORDER BY prompt_chars DESC" in sql


def test_bot_sysprompt_audit_query_includes_4key_identity() -> None:
    """Identity rule: surface (bot_id, workspace_id, channel_type)."""
    audit = _load_audit()
    sql = audit.bot_sysprompt_audit_query()
    assert "bot_id" in sql
    assert "workspace_id" in sql
    assert "channel_type" in sql


def test_bot_sysprompt_audit_query_uses_ilike_case_insensitive() -> None:
    audit = _load_audit()
    sql = audit.bot_sysprompt_audit_query()
    # All three flag predicates are ILIKE (case-insensitive); LIKE alone
    # would miss prompts written in mixed case.
    assert "ILIKE '%anti-fake%'" in sql
    assert "ILIKE '%anti fake%'" in sql
    assert "ILIKE '%KHÔNG bịa%'" in sql
    assert "ILIKE '%hallucinat%'" in sql


# ---------------- format_audit_row ------------------------------------------
def test_format_audit_row_warn_when_all_flags_false() -> None:
    audit = _load_audit()
    row = {
        "bot_id": "demo",
        "workspace_id": "ws-demo",
        "channel_type": "web",
        "prompt_chars": 1200,
        "has_anti_fake": False,
        "has_anti_fabricate_vi": False,
        "has_anti_hallucinate": False,
    }
    out = audit.format_audit_row(row)
    assert "⚠" in out
    assert "demo" in out
    assert "anti-fake=." in out
    assert "anti-bia-vi=." in out
    assert "anti-hallu=." in out


def test_format_audit_row_ok_when_any_flag_true() -> None:
    audit = _load_audit()
    row = {
        "bot_id": "demo",
        "workspace_id": "ws-demo",
        "channel_type": "web",
        "prompt_chars": 800,
        "has_anti_fake": False,
        "has_anti_fabricate_vi": True,
        "has_anti_hallucinate": False,
    }
    out = audit.format_audit_row(row)
    assert "✓" in out
    assert "⚠" not in out
    assert "anti-bia-vi=Y" in out


def test_format_audit_row_all_flags_true() -> None:
    audit = _load_audit()
    row = {
        "bot_id": "demo",
        "workspace_id": "ws-demo",
        "channel_type": "web",
        "prompt_chars": 4096,
        "has_anti_fake": True,
        "has_anti_fabricate_vi": True,
        "has_anti_hallucinate": True,
    }
    out = audit.format_audit_row(row)
    assert "✓" in out
    assert "anti-fake=Y" in out
    assert "anti-bia-vi=Y" in out
    assert "anti-hallu=Y" in out


def test_format_audit_row_handles_missing_keys() -> None:
    audit = _load_audit()
    # Truncated row (None values) — formatter must not crash.
    row: dict = {}
    out = audit.format_audit_row(row)
    assert "<unknown>" in out
    assert "⚠" in out  # all flags absent → warn


def test_format_audit_row_truncates_long_identifiers() -> None:
    audit = _load_audit()
    row = {
        "bot_id": "x" * 60,
        "workspace_id": "y" * 40,
        "channel_type": "web",
        "prompt_chars": 100,
        "has_anti_fake": True,
        "has_anti_fabricate_vi": False,
        "has_anti_hallucinate": False,
    }
    out = audit.format_audit_row(row)
    # Aggressively truncated so the table stays aligned.
    assert "x" * 60 not in out
    assert "y" * 40 not in out


# ---------------- is_bot_missing_all_anti_fake ------------------------------
def test_is_bot_missing_all_anti_fake_true_when_all_false() -> None:
    audit = _load_audit()
    row = {
        "has_anti_fake": False,
        "has_anti_fabricate_vi": False,
        "has_anti_hallucinate": False,
    }
    assert audit.is_bot_missing_all_anti_fake(row) is True


def test_is_bot_missing_all_anti_fake_false_when_one_true() -> None:
    audit = _load_audit()
    row = {
        "has_anti_fake": False,
        "has_anti_fabricate_vi": True,
        "has_anti_hallucinate": False,
    }
    assert audit.is_bot_missing_all_anti_fake(row) is False


# ---------------- JSON output schema ----------------------------------------
def test_write_json_emits_stable_schema(tmp_path: Path) -> None:
    audit = _load_audit()
    report = audit.AuditReport(
        generated_at="2026-05-18T00:00:00Z",
        n_bots=2,
        n_warn=1,
        bots=[
            {
                "bot_id": "demo",
                "workspace_id": "ws",
                "channel_type": "web",
                "prompt_chars": 500,
                "has_anti_fake": False,
                "has_anti_fabricate_vi": False,
                "has_anti_hallucinate": False,
            },
        ],
    )
    out = tmp_path / "report.json"
    audit.write_json(str(out), report)
    assert out.exists()
    parsed = json.loads(out.read_text())
    assert set(parsed.keys()) == {"generated_at", "n_bots", "n_warn", "bots"}
    assert parsed["n_bots"] == 2
    assert parsed["n_warn"] == 1
    assert parsed["bots"][0]["bot_id"] == "demo"


# ---------------- argparse --------------------------------------------------
def test_build_parser_defaults() -> None:
    audit = _load_audit()
    args = audit.build_parser().parse_args([])
    assert args.dsn is None
    assert args.json_out is None


def test_build_parser_accepts_dsn_and_json_out() -> None:
    audit = _load_audit()
    args = audit.build_parser().parse_args([
        "--dsn", "postgresql://x/y",
        "--json-out", "/tmp/out.json",
    ])
    assert args.dsn == "postgresql://x/y"
    assert args.json_out == "/tmp/out.json"


# ---------------- domain-neutral guard --------------------------------------
def test_audit_script_has_no_brand_or_bot_name_literals() -> None:
    """Domain-neutral rule — no bot slug / brand in the script source."""
    audit = _load_audit()
    src = Path(audit.__file__).read_text()
    # Bot-identifier literals (CLAUDE.md "No per-bot logic in core")
    forbidden = ["legalbot", "test-spa-id", "medispa", "gisbot", "dr.medispa"]
    for term in forbidden:
        assert term.lower() not in src.lower(), (
            f"audit_bot_sysprompt_rules.py must not hard-code bot slug "
            f"{term!r} — domain-neutral rule"
        )
