"""Regression pin — the canonical B2B /sync onboarding must pick the LLM model by
the schema-canonical kind ``'llm'`` (NOT ``'chat'``).

The prior ``kind IN ('chat', 'embedding')`` literal in /sync mismatched the
schema (``ai_models.kind`` is ``'llm'``), so every bot onboarded via the
documented server-to-server /sync path got NO llm_primary binding → resolve_llm
raised → first chat 500. This pins the fix and guards against the two onboarding
paths (/sync and bot_admin auto-pick) drifting apart again.
"""
from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "ragbot" / "interfaces" / "http" / "routes"
_SYNC = (_SRC / "sync.py").read_text(encoding="utf-8")
_ADMIN = (_SRC / "test_chat" / "bot_admin_routes.py").read_text(encoding="utf-8")


def test_sync_picks_llm_kind_not_chat() -> None:
    assert "kind IN ('llm', 'embedding')" in _SYNC, (
        "/sync default-model pick must select kind='llm' (schema canonical), "
        "not 'chat' — the 'chat' literal leaves new bots without an llm_primary "
        "binding and the first chat 500s."
    )


def test_sync_no_chat_kind_literal() -> None:
    # The wrong literal must be gone from the model-pick SQL.
    assert "kind IN ('chat', 'embedding')" not in _SYNC


def test_both_onboarding_paths_agree_on_llm_kind() -> None:
    # Drift guard: bot_admin auto-pick is the corrected reference; /sync must match.
    assert "kind IN ('llm', 'embedding')" in _ADMIN
    assert "kind IN ('llm', 'embedding')" in _SYNC
