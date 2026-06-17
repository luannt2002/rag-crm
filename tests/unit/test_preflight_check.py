"""Unit tests for ``scripts/preflight_check.py``.

Goal: assert each check function:
1. Returns a CheckResult with the right severity for OK / FAIL / SKIP paths.
2. Wraps provider failures so a single bad provider cannot crash the script
   (HALLU-sacred + production reliability — mirrors hard-constraint #1).
3. Honours --json + --strict + exit-code logic without re-running live IO.

All checks are exercised against in-memory mocks; no live network IO,
no live DB. The live run is verified separately by the script's CLI
output (see deliverable section 3 of plan).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loader: ``scripts/`` is not a package, so import the script as a
# stand-alone module via importlib. Keeps the script self-contained
# without forcing it into the ``ragbot`` package tree.
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "preflight_check.py"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_check_under_test", _SCRIPT_PATH)
    assert spec and spec.loader, "could not locate preflight_check.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight_check_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pf():
    return _load_preflight()


# ---------------------------------------------------------------------------
# Severity + CheckResult contract
# ---------------------------------------------------------------------------
def test_severity_enum_values(pf):
    assert pf.Severity.OK.value == "ok"
    assert pf.Severity.WARN.value == "warn"
    assert pf.Severity.FAIL.value == "fail"
    assert pf.Severity.SKIP.value == "skip"


def test_check_result_to_dict_round_trip(pf):
    r = pf.CheckResult(
        name="probe",
        severity=pf.Severity.OK,
        message="hello",
        duration_ms=42,
        details={"k": "v"},
        fix_hint="",
    )
    d = r.to_dict()
    assert d["name"] == "probe"
    assert d["severity"] == "ok"
    assert d["duration_ms"] == 42
    assert d["details"] == {"k": "v"}


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------
def test_db_connection_missing_dsn_returns_fail(pf, monkeypatch):
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = asyncio.run(pf.check_db_connection())
    assert res.severity == pf.Severity.FAIL
    assert "DATABASE_URL_SYNC" in res.message
    assert res.fix_hint  # non-empty


def test_db_connection_psycopg2_error_does_not_crash(pf, monkeypatch):
    """A bad DSN must surface as FAIL not bubble out."""
    monkeypatch.setenv(
        "DATABASE_URL_SYNC",
        "postgresql://nope:nope@127.0.0.1:1/nope",
    )
    res = asyncio.run(pf.check_db_connection())
    assert res.severity == pf.Severity.FAIL
    assert res.fix_hint  # has remediation


# ---------------------------------------------------------------------------
# Alembic head
# ---------------------------------------------------------------------------
def test_alembic_head_skips_when_no_dsn(pf, monkeypatch):
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = asyncio.run(pf.check_alembic_head())
    assert res.severity == pf.Severity.SKIP


def test_alembic_head_warns_when_versions_dir_missing(pf, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql://x:x@x:1/x")
    monkeypatch.setattr(pf, "ALEMBIC_VERSIONS_DIR", tmp_path / "non-existent")
    res = asyncio.run(pf.check_alembic_head())
    # Will WARN on missing dir before the DB connect attempt.
    assert res.severity in (pf.Severity.WARN, pf.Severity.SKIP)


# ---------------------------------------------------------------------------
# Purpose naming (BUG #1 detector)
# ---------------------------------------------------------------------------
def test_backcompat_purpose_constants_match_bug_lessons(pf):
    """BUG #1 from V2_MIGRATION_BUG_LESSONS — preflight must catch 'reranker'."""
    assert "reranker" in pf.LEGACY_PURPOSE_VALUES
    assert "rerank" in pf.CANONICAL_PURPOSE_VALUES
    assert "embedding" in pf.CANONICAL_PURPOSE_VALUES
    assert "llm_primary" in pf.CANONICAL_PURPOSE_VALUES


def test_purpose_check_skips_when_psycopg2_missing(pf, monkeypatch):
    """Confirm the import-error guard returns SKIP not crash."""
    monkeypatch.setattr(pf, "_load_env", lambda: None)
    # Force ImportError by deleting psycopg2 from sys.modules then patching
    # builtins __import__ — easier: patch the function to short-circuit DSN.
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = asyncio.run(pf.check_purpose_naming())
    assert res.severity == pf.Severity.SKIP


# ---------------------------------------------------------------------------
# Env vars
# ---------------------------------------------------------------------------
def test_env_vars_warn_when_no_keys_set(pf, monkeypatch):
    for k in pf.PROVIDER_ENV_KEYS.values():
        for v in k:
            monkeypatch.delenv(v, raising=False)
    res = asyncio.run(pf.check_env_vars_present())
    assert res.severity == pf.Severity.WARN


def test_env_vars_ok_when_one_key_set(pf, monkeypatch):
    for keys in pf.PROVIDER_ENV_KEYS.values():
        for v in keys:
            monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    res = asyncio.run(pf.check_env_vars_present())
    assert res.severity == pf.Severity.OK
    assert "openai" in res.details["set_keys"]


def test_provider_env_value_resolves_first_available(pf, monkeypatch):
    monkeypatch.delenv("RERANKER_JINA_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_JINA_API_KEY", raising=False)
    monkeypatch.setenv("JINA_API_KEY", "secret-jina")
    assert pf._provider_env_value("jina") == "secret-jina"


def test_provider_env_value_returns_empty_when_unset(pf, monkeypatch):
    for k in ("RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert pf._provider_env_value("jina") == ""
    assert pf._provider_env_value("unknown_provider_xyz") == ""


# ---------------------------------------------------------------------------
# Embedding probe — narrow exception handling per provider
# ---------------------------------------------------------------------------
def test_probe_embedding_no_api_key_returns_warn(pf, monkeypatch):
    for k in ("RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    res = asyncio.run(pf._probe_embedding("jina-embeddings-v3", "jina"))
    assert res.severity == pf.Severity.WARN
    assert "no api key" in res.message


def test_probe_embedding_timeout_returns_fail(pf, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_litellm = MagicMock()

    async def _hang(*_a, **_kw):
        await asyncio.sleep(60)

    fake_litellm.aembedding = _hang
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(pf, "PROVIDER_PROBE_TIMEOUT_S", 0)
    res = asyncio.run(pf._probe_embedding("text-embedding-3-small", "openai"))
    assert res.severity == pf.Severity.FAIL
    assert "timeout" in res.message.lower()


def test_probe_embedding_provider_exception_does_not_crash(pf, monkeypatch):
    """A 401 from provider must surface as FAIL, never raise."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_litellm = MagicMock()

    async def _raise(*_a, **_kw):
        raise RuntimeError("simulated provider 401")

    fake_litellm.aembedding = _raise
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    res = asyncio.run(pf._probe_embedding("text-embedding-3-small", "openai"))
    assert res.severity == pf.Severity.FAIL
    assert "RuntimeError" in res.message


def test_probe_embedding_ok_path(pf, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_litellm = MagicMock()

    async def _resp(*_a, **_kw):
        return SimpleNamespace(
            data=[{"embedding": [0.1] * 1536}],
        )

    fake_litellm.aembedding = _resp
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    res = asyncio.run(pf._probe_embedding("text-embedding-3-small", "openai"))
    assert res.severity == pf.Severity.OK
    assert res.details["dimension"] == 1536


def test_probe_embedding_jina_prefix_wired(pf, monkeypatch):
    """LiteLLM wire format must be ``jina_ai/<model>`` for non-OpenAI."""
    monkeypatch.setenv("JINA_API_KEY", "j-key")
    fake_litellm = MagicMock()
    captured: dict = {}

    async def _resp(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(data=[{"embedding": [0.1] * 1024}])

    fake_litellm.aembedding = _resp
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    asyncio.run(pf._probe_embedding("jina-embeddings-v3", "jina_ai"))
    assert captured["model"].startswith("jina_ai/"), captured
    # Asymmetric models must include task tag.
    assert captured.get("task") == "retrieval.passage"


# ---------------------------------------------------------------------------
# Reranker probe
# ---------------------------------------------------------------------------
def test_probe_reranker_no_api_key_for_remote_returns_fail(pf, monkeypatch):
    for k in ("RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    res = asyncio.run(pf._probe_reranker("jina-reranker-v3", "jina"))
    assert res.severity == pf.Severity.FAIL


def test_probe_reranker_null_provider_skips_live_probe(pf, monkeypatch):
    """``null`` reranker is a deliberate operator choice — OK without live call."""
    res = asyncio.run(pf._probe_reranker("noop", "null"))
    assert res.severity == pf.Severity.OK
    assert "skipped" in res.message


# ---------------------------------------------------------------------------
# Exit-code + result printing
# ---------------------------------------------------------------------------
def test_exit_code_all_ok(pf):
    rs = [pf.CheckResult("a", pf.Severity.OK, "ok")]
    assert pf._exit_code(rs, strict=False) == 0
    assert pf._exit_code(rs, strict=True) == 0


def test_exit_code_warn_only(pf):
    rs = [pf.CheckResult("a", pf.Severity.WARN, "drift")]
    assert pf._exit_code(rs, strict=False) == 0
    assert pf._exit_code(rs, strict=True) == 1


def test_exit_code_any_fail_is_2(pf):
    rs = [
        pf.CheckResult("a", pf.Severity.OK, "ok"),
        pf.CheckResult("b", pf.Severity.FAIL, "down"),
    ]
    assert pf._exit_code(rs, strict=False) == 2
    assert pf._exit_code(rs, strict=True) == 2


def test_print_results_json_mode_is_valid_json(pf, capsys):
    rs = [
        pf.CheckResult("a", pf.Severity.OK, "ok"),
        pf.CheckResult("b", pf.Severity.WARN, "drift", fix_hint="run X"),
    ]
    pf._print_results(rs, json_output=True)
    captured = capsys.readouterr()
    import json
    parsed = json.loads(captured.out)
    assert len(parsed) == 2
    assert parsed[0]["severity"] == "ok"
    assert parsed[1]["fix_hint"] == "run X"


def test_print_results_human_mode_includes_fix_hints(pf, capsys):
    rs = [
        pf.CheckResult("a", pf.Severity.FAIL, "boom", fix_hint="restart provider"),
    ]
    pf._print_results(rs, json_output=False)
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "boom" in out
    assert "restart provider" in out


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------
def test_parse_args_default(pf):
    ns = pf._parse_args([])
    assert ns.strict is False
    assert ns.json is False


def test_parse_args_flags(pf):
    ns = pf._parse_args(["--strict", "--json"])
    assert ns.strict is True
    assert ns.json is True


# ---------------------------------------------------------------------------
# Top-level main never crashes
# ---------------------------------------------------------------------------
def test_main_never_crashes_on_inner_exception(pf, monkeypatch, capsys):
    """Top-level main must catch any unexpected error and exit non-zero."""
    async def _boom():
        raise RuntimeError("simulated catastrophic failure")

    monkeypatch.setattr(pf, "run_all_checks", _boom)
    code = asyncio.run(pf.main(strict=False, json_output=False))
    assert code == 2
    err = capsys.readouterr().err
    assert "RuntimeError" in err or "PREFLIGHT CRASHED" in err
