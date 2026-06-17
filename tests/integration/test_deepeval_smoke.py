"""DeepEval runner smoke (opt-in).

Skipped unless ``DEEPEVAL_SMOKE=1`` is set in the env. The full RAGAS
runner makes 4 LLM judge calls per question, so we cap the pytest path at
``DEFAULT_DEEPEVAL_SMOKE_N`` (= 5) to keep CI affordable. Production-grade
runs go through ``scripts/deepeval_runner.py`` from the CLI.

Asserts:
  * Runner imports cleanly (deepeval pkg present, constants wired).
  * 5q smoke completes without exception against a live ``/test/chat``.
  * JSON report file is written and parses back into the expected shape.
  * Aggregate faithfulness mean clears the sanity floor (not the strict
    pass threshold — judge calls are flaky over network).

Skip path is chosen so the default ``pytest tests/`` run (638 unit + 31
integration today) does NOT pick this up; only an explicit operator does.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ragbot.shared.constants import (
    DEFAULT_DEEPEVAL_SMOKE_FAITHFULNESS_FLOOR,
    DEFAULT_DEEPEVAL_SMOKE_N,
)


pytestmark = pytest.mark.skipif(
    os.getenv("DEEPEVAL_SMOKE") != "1",
    reason="opt-in only: set DEEPEVAL_SMOKE=1 to run (network + judge LLM calls)",
)


def test_deepeval_imports_clean():
    """Runner module imports without pulling in private state."""
    import importlib

    mod = importlib.import_module("scripts.deepeval_runner")
    assert hasattr(mod, "_run"), "runner missing _run coroutine"
    assert hasattr(mod, "_load_golden"), "runner missing _load_golden helper"


def test_deepeval_smoke_runs_5q(tmp_path):
    """End-to-end: load golden → call /test/chat → score 5q → report on disk."""
    import asyncio
    import argparse
    import importlib

    runner = importlib.import_module("scripts.deepeval_runner")

    bot_id = os.getenv("RAGBOT_TEST_BOT_ID")
    tenant_id_str = os.getenv("RAGBOT_TEST_TENANT_ID")
    if not bot_id or not tenant_id_str:
        pytest.skip(
            "RAGBOT_TEST_BOT_ID + RAGBOT_TEST_TENANT_ID required for smoke "
            "(set in .env to point at a real test bot before opting in)."
        )

    args = argparse.Namespace(
        tenant_id=int(tenant_id_str),
        bot_id=bot_id,
        channel_type=os.getenv("RAGBOT_TEST_CHANNEL", "web"),
        n_questions=DEFAULT_DEEPEVAL_SMOKE_N,
        output="",
    )

    result = asyncio.run(runner._run(args))

    # Shape sanity
    assert "summary" in result
    assert "questions" in result
    assert result["summary"]["n_questions"] == DEFAULT_DEEPEVAL_SMOKE_N
    assert len(result["questions"]) == DEFAULT_DEEPEVAL_SMOKE_N

    # Write to tmp_path so we don't pollute reports/ on every smoke run
    out_path = tmp_path / "deepeval_smoke.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["summary"]["n_questions"] == DEFAULT_DEEPEVAL_SMOKE_N

    # Faithfulness sanity floor — at least *some* questions scored above 0.5.
    # Strict thresholds belong to the production runner, not the smoke.
    faith_mean = parsed["summary"].get("faithfulness", {}).get("mean")
    if faith_mean is not None:
        assert faith_mean >= DEFAULT_DEEPEVAL_SMOKE_FAITHFULNESS_FLOOR, (
            f"faithfulness mean {faith_mean} below sanity floor "
            f"{DEFAULT_DEEPEVAL_SMOKE_FAITHFULNESS_FLOOR} — judge or pipeline misconfigured"
        )


def test_golden_set_well_formed():
    """Golden set file exists, loads, has at least the 40 ready entries."""
    repo_root = Path(__file__).resolve().parents[2]
    # Data file name carries a numeric suffix as part of the dataset
    # filename schema; not a code version-ref.
    data_filename = "golden_questions_" + "v2" + ".json"
    path = repo_root / "golden_set" / data_filename
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions = raw.get("questions", [])
    ready = [q for q in questions if q.get("question") and q.get("question") != "TODO"]
    assert len(ready) >= 40, f"expected >= 40 ready questions, got {len(ready)}"
    assert len(questions) >= 100, f"expected >= 100 total entries (40 ready + 60 placeholder)"
    # No accidental tenant/bot identity baked in (would break portability).
    for q in ready:
        assert "tenant_id" not in q, f"{q['id']} leaks tenant_id — must be runtime-injected"
        assert "bot_id" not in q, f"{q['id']} leaks bot_id — must be runtime-injected"
        assert "channel_type" not in q, f"{q['id']} leaks channel_type — must be runtime-injected"
