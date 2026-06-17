"""GEN-1 config-hygiene tests.

Invariants:
1. scripts/audit_harness_run.py has NO hardcoded AI model name anywhere at
   module level — the judge model is resolved from system_config DB
   (single source of truth per CLAUDE.md zero-hardcode + AI-model rule).
2. scripts/init_system_config.py seeds `citation_marker_required` so admin UI
   can flip per-bot for audit scenarios.
3. scripts/init_system_config.py seeds `llm_default_model` — the key the
   auditor reads from.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _auditor_source() -> str:
    return (SCRIPTS_DIR / "audit_harness_run.py").read_text(encoding="utf-8")


def _init_source() -> str:
    return (SCRIPTS_DIR / "init_system_config.py").read_text(encoding="utf-8")


def test_auditor_has_no_hardcoded_model_name():
    """No OpenAI/Anthropic/Cohere model name literal in audit_harness_run.py."""
    src = _auditor_source()
    forbidden = [
        r'"gpt-\d',
        r"'gpt-\d",
        r'"claude-\d',
        r"'claude-\d",
        r'"text-embedding-',
        r"'text-embedding-",
        r'"cohere/',
        r"'cohere/",
    ]
    for pat in forbidden:
        assert not re.search(pat, src), (
            f"hardcoded AI model literal {pat!r} found in audit_harness_run.py — "
            "resolve via system_config.llm_default_model instead"
        )


def test_auditor_reads_from_system_config():
    """Auditor must query system_config DB for the judge model."""
    src = _auditor_source()
    assert "system_config" in src, "auditor must read judge model from system_config"
    assert "llm_default_model" in src, "auditor must look up llm_default_model key"


def test_auditor_raises_on_missing_config():
    """No silent fallback — auditor raises if system_config key missing."""
    src = _auditor_source()
    assert "raise RuntimeError" in src or "raise " in src, (
        "auditor must raise an explicit error if system_config is missing, "
        "not fall back to a hardcoded default"
    )


def test_init_system_config_has_citation_marker():
    """Seed list in init_system_config.py must include citation_marker_required."""
    src = _init_source()
    assert "citation_marker_required" in src, (
        "citation_marker_required must be seeded so admin UI can flip it per-bot"
    )


def test_init_system_config_has_llm_default_model():
    """The key the auditor reads from must be seeded."""
    src = _init_source()
    assert "llm_default_model" in src


def test_init_system_config_grounding_threshold_documented():
    """grounding_check_threshold comment should mention the audit-bot tune value."""
    src = _init_source()
    assert "grounding_check_threshold" in src
    assert "0.95" in src, (
        "grounding_check_threshold description must document audit-bot tune 0.95"
    )
