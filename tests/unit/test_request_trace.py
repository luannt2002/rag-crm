"""P4: dev/uat-only full request trace — one verifiable JSON per request.

The trace answers the owner's verification question end-to-end: what was asked,
how many steps ran, which chunks reached the LLM, the exact final prompt, the
raw answer BEFORE any guard substitution, the guard verdict, and the final
answer. Gated to development/uat so production (where the full prompt/answer
can carry PII) never writes these files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ragbot.shared.request_trace import (
    is_request_trace_enabled,
    write_request_trace,
)


def test_disabled_in_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert is_request_trace_enabled() is False


@pytest.mark.parametrize("env", ["development", "uat"])
def test_enabled_in_dev_uat(monkeypatch, env) -> None:
    monkeypatch.setenv("APP_ENV", env)
    assert is_request_trace_enabled() is True


def test_production_write_is_noop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAGBOT_TRACE_DIR", str(tmp_path))
    out = write_request_trace(request_id="r1", trace={"question": "x"})
    assert out is None
    assert not list(tmp_path.iterdir())


def test_dev_write_produces_verifiable_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAGBOT_TRACE_DIR", str(tmp_path))
    trace = {
        "question": "Neoterra 195/65R16 giá?",
        "intent": "factoid",
        "retrieve_mode": "stats_index",
        "steps": ["retrieve", "generate", "guard_output"],
        "chunks_to_llm": [{"chunk_id": "c1", "content": "195/65R16 NEO | price: —"}],
        "full_prompt": "<system>...</system><user>...</user>",
        "raw_answer": "Dạ giá 1.500.000đ",
        "guardrail_flags": [{"rule_id": "numeric_fidelity", "blocked": True}],
        "final_answer": "Dạ mặt hàng này chưa có giá ạ",
        "numeric_fidelity": {"n_unsupported": 1},
    }
    out = write_request_trace(request_id="req-abc", trace=trace)
    assert out is not None
    p = Path(out)
    assert p.exists()
    loaded = json.loads(p.read_text(encoding="utf-8"))
    # Every verification field survives the round-trip.
    assert loaded["question"] == trace["question"]
    assert loaded["raw_answer"] == "Dạ giá 1.500.000đ"
    assert loaded["final_answer"] != loaded["raw_answer"]  # block substituted
    assert loaded["chunks_to_llm"][0]["content"].endswith("price: —")


def test_oversized_field_is_capped(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAGBOT_TRACE_DIR", str(tmp_path))
    huge = "x" * 50000
    out = write_request_trace(request_id="req-big", trace={"full_prompt": huge})
    loaded = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(loaded["full_prompt"]) < 50000
    assert loaded["full_prompt"].endswith("…[truncated]")


def test_write_never_raises(monkeypatch) -> None:
    """Trace is auxiliary — a bad dir must degrade silently, never break the
    request (graceful-degradation: aux dependency cannot kill the main app)."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAGBOT_TRACE_DIR", "/proc/nonexistent/cannot-write")
    # Must not raise.
    assert write_request_trace(request_id="r", trace={"question": "x"}) is None
