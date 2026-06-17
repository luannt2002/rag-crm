"""Coverage for ``scripts/diagnose_p95_bottleneck.py``.

T2-CostPerf — verify pure helpers in the diagnostic script:

1. ``resolve_dsn`` strips ``+asyncpg``/``+psycopg`` driver suffix so
   psycopg2 (sync) accepts the URL.
2. ``resolve_dsn`` exits with code 2 when DATABASE_URL absent.
3. ``fmt_ms`` formats ms/s correctly (boundary at 1000ms).
4. SQL builders accept window_hours int + optional bot filter without
   crashing — parameterized via psycopg2 params (no SQL injection).
5. End-to-end ``run()`` is NOT covered here (needs live DB); covered by
   smoke run + the ``--json-out`` output schema test below.
6. JSON output schema is stable (keys present, types correct) so
   downstream tooling (CI gates, dashboards) can rely on it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_diag() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "diagnose_p95_bottleneck.py"
    assert script_path.exists(), f"script missing: {script_path}"
    spec = importlib.util.spec_from_file_location("_diag_p95", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_diag_p95"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------- resolve_dsn -----------------------------------------------
def test_resolve_dsn_strips_asyncpg_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    diag = _load_diag()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    assert diag.resolve_dsn(None) == "postgresql://u:p@h:5432/db"


def test_resolve_dsn_strips_psycopg_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    diag = _load_diag()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@h/db")
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    assert diag.resolve_dsn(None) == "postgresql://u:p@h/db"


def test_resolve_dsn_prefers_sync_url_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diag = _load_diag()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://a/x")
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql://b/y")
    assert diag.resolve_dsn(None) == "postgresql://b/y"


def test_resolve_dsn_cli_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    diag = _load_diag()
    monkeypatch.setenv("DATABASE_URL", "postgresql://ignored/db")
    assert (
        diag.resolve_dsn("postgresql://override/db")
        == "postgresql://override/db"
    )


def test_resolve_dsn_missing_env_exits_code_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    diag = _load_diag()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    with pytest.raises(SystemExit) as exc:
        diag.resolve_dsn(None)
    assert exc.value.code == diag.EXIT_DB_UNREACHABLE
    err = capsys.readouterr().err
    assert "DATABASE_URL not set" in err


# ---------------- fmt_ms ----------------------------------------------------
def test_fmt_ms_none() -> None:
    diag = _load_diag()
    assert diag.fmt_ms(None) == "—"


def test_fmt_ms_below_one_second() -> None:
    diag = _load_diag()
    assert diag.fmt_ms(500.4) == "500ms"
    assert diag.fmt_ms(999.0) == "999ms"


def test_fmt_ms_above_one_second_renders_seconds() -> None:
    diag = _load_diag()
    assert diag.fmt_ms(1000.0) == "1.00s"
    assert diag.fmt_ms(21500.0) == "21.50s"


# ---------------- SQL builders ----------------------------------------------
def test_percentile_query_contains_window_and_groupby() -> None:
    diag = _load_diag()
    sql = diag.percentile_query(24)
    assert "INTERVAL '24 hours'" in sql
    assert "PERCENTILE_CONT(0.95)" in sql
    assert "GROUP BY step_name" in sql
    assert "ORDER BY p95_ms DESC" in sql


def test_end_to_end_query_with_bot_filter_uses_param() -> None:
    """SQL injection guard — bot slug must be a psycopg2 named param."""
    diag = _load_diag()
    sql_filtered = diag.end_to_end_query(24, "legalbot")
    sql_unfiltered = diag.end_to_end_query(24, None)
    assert "%(bot)s" in sql_filtered
    assert "legalbot" not in sql_filtered  # not inlined
    assert "%(bot)s" not in sql_unfiltered


def test_per_bot_query_limits_and_orders() -> None:
    diag = _load_diag()
    sql = diag.per_bot_query(168, 5)
    assert "LIMIT 5" in sql
    assert "INTERVAL '168 hours'" in sql
    assert "JOIN bots" in sql


def test_grade_retry_query_pulls_metadata_field() -> None:
    diag = _load_diag()
    sql = diag.grade_retry_distribution_query(24)
    assert "grade_retries" in sql
    assert "step_name = 'grade'" in sql


def test_llm_calls_per_turn_subquery() -> None:
    diag = _load_diag()
    sql = diag.llm_calls_per_turn_query(24)
    assert "AVG(call_count)" in sql
    assert "GROUP BY record_request_id" in sql


def test_dead_path_flags_query_lists_three_flags() -> None:
    diag = _load_diag()
    sql = diag.dead_path_flags_query()
    assert "metadata_extraction_enabled" in sql
    assert "adapchunk_layer3_doc_profile_enabled" in sql
    assert "cleanbase_tier0_enabled" in sql


# ---------------- Bug #3 cache_check 1.21s diagnostics ----------------------

def test_semantic_cache_index_query_targets_pg_indexes() -> None:
    diag = _load_diag()
    sql = diag.semantic_cache_index_query()
    assert "pg_indexes" in sql
    assert "tablename = 'semantic_cache'" in sql
    assert "indexname" in sql and "indexdef" in sql


def test_semantic_cache_config_query_filters_relevant_keys() -> None:
    diag = _load_diag()
    sql = diag.semantic_cache_config_query()
    assert "system_config" in sql
    # ILIKE filters semantic_cache + cache_similarity_threshold + cache_ttl.
    assert "semantic_cache" in sql
    assert "cache_similarity_threshold" in sql
    assert "cache_ttl" in sql


def test_semantic_cache_size_query_reports_active_expired_split() -> None:
    diag = _load_diag()
    sql = diag.semantic_cache_size_query()
    assert "COUNT(*)" in sql
    assert "expires_at" in sql
    assert "n_active" in sql and "n_expired" in sql


# ---------------- JSON output schema ----------------------------------------
def test_write_json_emits_full_schema(tmp_path: Path) -> None:
    diag = _load_diag()
    report = diag.DiagReport(
        generated_at="2026-05-18T00:00:00Z",
        window_hours=24,
        bot_filter=None,
        end_to_end={"n": 0},
        per_step=[],
        per_bot=[],
        grade_retries=[],
        llm_calls={},
        reflect_opt_in=[],
        dead_path_flags=[],
        # Bug #3 cache_check 1.21s diagnostic fields (added 2026-05-18).
        semantic_cache_indexes=[],
        semantic_cache_config=[],
        semantic_cache_size={},
    )
    out = tmp_path / "report.json"
    diag.write_json(str(out), report)
    assert out.exists()
    parsed = json.loads(out.read_text())
    # Required keys (stable contract). Additive fields (e.g. CT-4
    # rerank_score_histogram, off-by-default) may be present too;
    # assert subset to stay forward-compatible with new optional flags.
    expected_keys = {
        "generated_at",
        "window_hours",
        "bot_filter",
        "end_to_end",
        "per_step",
        "per_bot",
        "grade_retries",
        "llm_calls",
        "reflect_opt_in",
        "dead_path_flags",
        "semantic_cache_indexes",
        "semantic_cache_config",
        "semantic_cache_size",
    }
    assert expected_keys.issubset(set(parsed.keys()))
    assert parsed["window_hours"] == 24


# ---------------- argparse --------------------------------------------------
def test_build_parser_defaults() -> None:
    diag = _load_diag()
    p = diag.build_parser()
    args = p.parse_args([])
    assert args.hours == diag.DEFAULT_WINDOW_HOURS
    assert args.top == diag.DEFAULT_TOP_STEPS
    assert args.top_bots == diag.DEFAULT_TOP_BOTS
    assert args.bot is None
    assert args.dsn is None
    assert args.json_out is None


def test_build_parser_override_all() -> None:
    diag = _load_diag()
    args = diag.build_parser().parse_args([
        "--hours", "168",
        "--top", "10",
        "--top-bots", "5",
        "--bot", "legalbot",
        "--json-out", "/tmp/out.json",
    ])
    assert args.hours == 168
    assert args.top == 10
    assert args.top_bots == 5
    assert args.bot == "legalbot"
    assert args.json_out == "/tmp/out.json"
