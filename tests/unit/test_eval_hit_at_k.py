"""Unit coverage for `scripts/eval_retrieval_hit_at_k.py`.

Stream 16-eval-hit-at-k — verify:
  1. Pure metric functions (hit@k, dcg@k, ndcg@k, reciprocal_rank) give
     the textbook answers on synthetic ground truth.
  2. ``compute_bot_metrics`` aggregates correctly across multiple queries
     and handles empty / runner-error inputs without raising.
  3. Fixture parsing rejects malformed JSONL with a clear error.
  4. Markdown + JSON report renderers emit stable, parseable output.
  5. CLI entry point wires a custom runner end-to-end without touching
     the network.

The script lives in ``scripts/`` (not a package), so we load it via
``importlib.util`` — same pattern used by existing script tests under
``tests/unit/scripts/``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "eval_retrieval_hit_at_k.py"
    assert script_path.exists(), f"eval script missing at {script_path}"
    spec = importlib.util.spec_from_file_location("_eval_hit_at_k", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def evalmod() -> ModuleType:
    return _load_module()


# --------------------------------------------------------------------------- #
# Public API exposure.
# --------------------------------------------------------------------------- #


def test_module_exposes_public_api(evalmod: ModuleType) -> None:
    """Public surface required by docs + CI integration."""
    for name in (
        "GoldenQuery",
        "BotMetrics",
        "hit_at_k",
        "dcg_at_k",
        "ndcg_at_k",
        "reciprocal_rank",
        "compute_bot_metrics",
        "parse_golden_queries",
        "discover_bot_files",
        "render_markdown_report",
        "render_json_report",
        "build_arg_parser",
        "main",
        "EVAL_STEP_NAME",
    ):
        assert hasattr(evalmod, name), f"missing public symbol: {name}"


def test_step_name_constant_is_stable(evalmod: ModuleType) -> None:
    """Downstream alerting joins on this string — must not drift."""
    assert evalmod.EVAL_STEP_NAME == "eval_retrieval_hit_at_k"


# --------------------------------------------------------------------------- #
# Pure metric correctness.
# --------------------------------------------------------------------------- #


def test_hit_at_k_first_position_is_hit(evalmod: ModuleType) -> None:
    """Relevant doc at rank 1 → hit at every k>=1."""
    retrieved = ["a", "b", "c", "d"]
    expected = ["a"]
    assert evalmod.hit_at_k(retrieved, expected, 1) == 1.0
    assert evalmod.hit_at_k(retrieved, expected, 3) == 1.0
    assert evalmod.hit_at_k(retrieved, expected, 10) == 1.0


def test_hit_at_k_deep_position_misses_shallow(evalmod: ModuleType) -> None:
    """Relevant doc at rank 5 → miss at k<5, hit at k>=5."""
    retrieved = ["x1", "x2", "x3", "x4", "a"]
    expected = ["a"]
    assert evalmod.hit_at_k(retrieved, expected, 1) == 0.0
    assert evalmod.hit_at_k(retrieved, expected, 3) == 0.0
    assert evalmod.hit_at_k(retrieved, expected, 4) == 0.0
    assert evalmod.hit_at_k(retrieved, expected, 5) == 1.0
    assert evalmod.hit_at_k(retrieved, expected, 10) == 1.0


def test_hit_at_k_edge_cases(evalmod: ModuleType) -> None:
    """Empty inputs + non-positive k → 0.0 (no fabricated hit)."""
    assert evalmod.hit_at_k([], ["a"], 5) == 0.0
    assert evalmod.hit_at_k(["a"], [], 5) == 0.0
    assert evalmod.hit_at_k(["a"], ["a"], 0) == 0.0
    assert evalmod.hit_at_k(["a"], ["a"], -1) == 0.0


def test_dcg_at_k_textbook_example(evalmod: ModuleType) -> None:
    """DCG@3 for [relevant, irrelevant, relevant] vs expected={a, c}.

    rel = [1, 0, 1] → DCG@3 = 1/log2(2) + 0/log2(3) + 1/log2(4)
                              = 1.0     + 0         + 0.5
                              = 1.5
    """
    import math

    retrieved = ["a", "b", "c"]
    expected = ["a", "c"]
    got = evalmod.dcg_at_k(retrieved, expected, 3)
    want = 1.0 / math.log2(2) + 1.0 / math.log2(4)
    assert got == pytest.approx(want, rel=1e-9)
    assert got == pytest.approx(1.5, rel=1e-9)


def test_ndcg_at_k_perfect_ranking_is_one(evalmod: ModuleType) -> None:
    """All relevant docs first → DCG == IDCG → nDCG = 1.0."""
    retrieved = ["a", "b", "c", "d", "e"]
    expected = ["a", "b", "c"]
    assert evalmod.ndcg_at_k(retrieved, expected, 5) == pytest.approx(1.0)
    assert evalmod.ndcg_at_k(retrieved, expected, 10) == pytest.approx(1.0)


def test_ndcg_at_k_no_relevant_returns_zero(evalmod: ModuleType) -> None:
    """No relevant doc retrieved → DCG=0 → nDCG=0 (no fabricated lift)."""
    retrieved = ["x", "y", "z"]
    expected = ["a"]
    assert evalmod.ndcg_at_k(retrieved, expected, 5) == 0.0


def test_ndcg_at_k_worse_than_ideal_is_lt_one(evalmod: ModuleType) -> None:
    """Relevant at rank 3 (not 1) → nDCG@5 strictly < 1.0."""
    retrieved = ["x", "y", "a", "b", "c"]
    expected = ["a"]
    got = evalmod.ndcg_at_k(retrieved, expected, 5)
    assert 0.0 < got < 1.0


def test_ndcg_empty_expected_returns_zero(evalmod: ModuleType) -> None:
    """Empty expected set → 0.0 (avoid 0/0)."""
    assert evalmod.ndcg_at_k(["a", "b"], [], 5) == 0.0


def test_reciprocal_rank_first_position(evalmod: ModuleType) -> None:
    """First-position relevant → RR = 1/1 = 1.0."""
    assert evalmod.reciprocal_rank(["a", "b"], ["a"]) == 1.0


def test_reciprocal_rank_third_position(evalmod: ModuleType) -> None:
    """Third-position relevant → RR = 1/3."""
    rr = evalmod.reciprocal_rank(["x", "y", "a", "b"], ["a"])
    assert rr == pytest.approx(1.0 / 3.0)


def test_reciprocal_rank_no_hit(evalmod: ModuleType) -> None:
    """Nothing relevant retrieved → RR = 0.0."""
    assert evalmod.reciprocal_rank(["x", "y"], ["a"]) == 0.0


# --------------------------------------------------------------------------- #
# Bot-level aggregation.
# --------------------------------------------------------------------------- #


def test_compute_bot_metrics_aggregates_two_queries(evalmod: ModuleType) -> None:
    """Two queries: Q1 hits at 1, Q2 misses entirely → averages are halves."""
    queries = [
        evalmod.GoldenQuery(question="q1", expected_doc_ids=("a",)),
        evalmod.GoldenQuery(question="q2", expected_doc_ids=("z",)),
    ]

    def runner(bot_id: str, question: str, top_k: int) -> list[str]:
        # Q1 returns relevant first; Q2 returns only irrelevants.
        if question == "q1":
            return ["a", "b", "c"][:top_k]
        return ["x", "y", "w"][:top_k]

    metrics = evalmod.compute_bot_metrics(
        "bot-uuid-1",
        queries,
        runner,
        hit_depths=(1, 3),
        ndcg_depths=(3,),
        retrieval_top_k=5,
    )

    assert metrics.record_bot_id == "bot-uuid-1"
    assert metrics.total_queries == 2
    # Q1 hits at all depths, Q2 hits at none → avg = 0.5.
    assert metrics.hit_at_k[1] == pytest.approx(0.5)
    assert metrics.hit_at_k[3] == pytest.approx(0.5)
    # nDCG@3: Q1 = 1.0 (perfect rank 1), Q2 = 0.0 → avg = 0.5.
    assert metrics.ndcg_at_k[3] == pytest.approx(0.5)
    # MRR: Q1 = 1.0, Q2 = 0.0 → avg = 0.5.
    assert metrics.mrr == pytest.approx(0.5)


def test_compute_bot_metrics_empty_queries(evalmod: ModuleType) -> None:
    """Empty fixture → zeroed metrics, no crash."""

    def never_called(*args: object, **kw: object) -> list[str]:
        raise AssertionError("runner must not be invoked for empty queries")

    metrics = evalmod.compute_bot_metrics(
        "bot-empty",
        [],
        never_called,
        hit_depths=(1, 5),
        ndcg_depths=(5,),
    )
    assert metrics.total_queries == 0
    assert metrics.hit_at_k[1] == 0.0
    assert metrics.hit_at_k[5] == 0.0
    assert metrics.ndcg_at_k[5] == 0.0
    assert metrics.mrr == 0.0


def test_compute_bot_metrics_runner_error_counts_as_miss(
    evalmod: ModuleType,
) -> None:
    """Runner raising → treated as miss, not propagated (regression signal)."""
    queries = [
        evalmod.GoldenQuery(question="q1", expected_doc_ids=("a",)),
        evalmod.GoldenQuery(question="q2", expected_doc_ids=("b",)),
    ]
    calls = {"n": 0}

    def flaky(bot_id: str, question: str, top_k: int) -> list[str]:
        calls["n"] += 1
        if question == "q1":
            return ["a"]  # hit
        raise RuntimeError("boom — simulating retrieval infra failure")

    metrics = evalmod.compute_bot_metrics(
        "bot-flaky",
        queries,
        flaky,
        hit_depths=(1,),
        ndcg_depths=(1,),
    )
    assert calls["n"] == 2  # both queries attempted
    # 1 hit out of 2 → 0.5 average; no exception escaped.
    assert metrics.hit_at_k[1] == pytest.approx(0.5)
    assert metrics.mrr == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Fixture parsing.
# --------------------------------------------------------------------------- #


def test_parse_golden_queries_round_trip(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    """Valid JSONL parses into ``GoldenQuery`` objects with right ids."""
    fixture = tmp_path / "bot-1.jsonl"
    fixture.write_text(
        '{"question": "qA", "expected_doc_ids": ["d1", "d2"]}\n'
        "\n"  # blank line is skipped
        '{"question": "qB", "expected_doc_ids": ["d3"]}\n',
        encoding="utf-8",
    )
    queries = evalmod.parse_golden_queries(fixture)
    assert len(queries) == 2
    assert queries[0].question == "qA"
    assert queries[0].expected_doc_ids == ("d1", "d2")
    assert queries[1].expected_doc_ids == ("d3",)


def test_parse_golden_queries_rejects_missing_question(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    fixture = tmp_path / "bot-bad.jsonl"
    fixture.write_text(
        '{"expected_doc_ids": ["d1"]}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        evalmod.parse_golden_queries(fixture)


def test_parse_golden_queries_rejects_empty_doc_ids(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    fixture = tmp_path / "bot-bad2.jsonl"
    fixture.write_text(
        '{"question": "q", "expected_doc_ids": []}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        evalmod.parse_golden_queries(fixture)


def test_parse_golden_queries_rejects_malformed_json(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    fixture = tmp_path / "bot-bad3.jsonl"
    fixture.write_text('{"question": "q",\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        evalmod.parse_golden_queries(fixture)


def test_discover_bot_files_sorted(evalmod: ModuleType, tmp_path: Path) -> None:
    """Discover returns sorted JSONL files; non-JSONL ignored."""
    (tmp_path / "b.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "a.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("", encoding="utf-8")
    files = evalmod.discover_bot_files(tmp_path)
    assert [p.name for p in files] == ["a.jsonl", "b.jsonl"]


def test_discover_bot_files_missing_dir_returns_empty(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    assert evalmod.discover_bot_files(tmp_path / "does-not-exist") == []


# --------------------------------------------------------------------------- #
# Report rendering.
# --------------------------------------------------------------------------- #


def test_render_json_report_is_stable_and_parseable(
    evalmod: ModuleType,
) -> None:
    metrics = evalmod.BotMetrics(
        record_bot_id="bot-x",
        total_queries=2,
        hit_at_k={1: 0.5, 3: 1.0},
        ndcg_at_k={3: 0.75},
        mrr=0.625,
    )
    blob = evalmod.render_json_report(
        [metrics], hit_depths=(1, 3), ndcg_depths=(3,)
    )
    payload = json.loads(blob)
    assert payload["schema_version"] == 1
    assert payload["hit_at_k_depths"] == [1, 3]
    assert payload["ndcg_at_k_depths"] == [3]
    assert payload["bots"][0]["record_bot_id"] == "bot-x"
    assert payload["bots"][0]["hit_at_k"]["1"] == 0.5
    assert payload["bots"][0]["hit_at_k"]["3"] == 1.0
    assert payload["bots"][0]["mrr"] == 0.625


def test_render_markdown_report_has_expected_columns(
    evalmod: ModuleType,
) -> None:
    metrics = evalmod.BotMetrics(
        record_bot_id="bot-y",
        total_queries=4,
        hit_at_k={1: 0.25, 5: 0.75},
        ndcg_at_k={5: 0.5},
        mrr=0.4,
    )
    md = evalmod.render_markdown_report(
        [metrics], hit_depths=(1, 5), ndcg_depths=(5,)
    )
    assert "hit@1" in md
    assert "hit@5" in md
    assert "nDCG@5" in md
    assert "MRR" in md
    assert "bot-y" in md
    # 4-decimal formatting
    assert "0.2500" in md
    assert "0.7500" in md


# --------------------------------------------------------------------------- #
# CLI entry point.
# --------------------------------------------------------------------------- #


def test_main_end_to_end_with_injected_runner(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    """CLI runs end-to-end: writes JSON + markdown reports with correct data."""
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "bot-uuid-1.jsonl").write_text(
        '{"question": "q1", "expected_doc_ids": ["a"]}\n'
        '{"question": "q2", "expected_doc_ids": ["b"]}\n',
        encoding="utf-8",
    )

    def runner(bot_id: str, question: str, top_k: int) -> list[str]:
        # Q1 hits at rank 1; Q2 hits at rank 2.
        if question == "q1":
            return ["a", "x", "y"][:top_k]
        return ["z", "b", "w"][:top_k]

    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    rc = evalmod.main(
        [
            "--golden-dir",
            str(golden_dir),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--hit-at-k",
            "1,3",
            "--ndcg-at-k",
            "3",
            "--retrieval-top-k",
            "5",
        ],
        runner=runner,
    )
    assert rc == 0
    assert out_json.exists()
    assert out_md.exists()

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    bots = payload["bots"]
    assert len(bots) == 1
    bot = bots[0]
    assert bot["record_bot_id"] == "bot-uuid-1"
    # hit@1: Q1=1, Q2=0 → 0.5
    assert bot["hit_at_k"]["1"] == pytest.approx(0.5)
    # hit@3: Q1=1, Q2=1 → 1.0
    assert bot["hit_at_k"]["3"] == pytest.approx(1.0)
    # MRR: 1/1 + 1/2 = 1.5 → avg 0.75
    assert bot["mrr"] == pytest.approx(0.75)


def test_main_no_golden_dir_returns_zero(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    """Missing golden dir → graceful no-op exit 0."""
    rc = evalmod.main(
        [
            "--golden-dir",
            str(tmp_path / "missing"),
            "--output-json",
            str(tmp_path / "out.json"),
        ],
        runner=lambda *_a, **_kw: [],
    )
    assert rc == 0
    # No reports written when no golden dir.
    assert not (tmp_path / "out.json").exists()


def test_main_rejects_invalid_depth_list(
    evalmod: ModuleType, tmp_path: Path
) -> None:
    """Invalid depth literal → SystemExit (no silent fallback)."""
    golden_dir = tmp_path / "g"
    golden_dir.mkdir()
    (golden_dir / "b.jsonl").write_text(
        '{"question": "q", "expected_doc_ids": ["a"]}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        evalmod.main(
            [
                "--golden-dir",
                str(golden_dir),
                "--hit-at-k",
                "not-a-number",
            ],
            runner=lambda *_a, **_kw: ["a"],
        )


def test_main_default_depths_from_constants(evalmod: ModuleType) -> None:
    """Default depths must come from shared.constants, not script literals."""
    from ragbot.shared.constants import (
        DEFAULT_HIT_AT_K_DEPTHS,
        DEFAULT_NDCG_AT_K_DEPTHS,
    )

    # Spot-check: the module-level imports point at the same tuple objects.
    assert evalmod._parse_depths(None, DEFAULT_HIT_AT_K_DEPTHS) == tuple(
        DEFAULT_HIT_AT_K_DEPTHS
    )
    assert evalmod._parse_depths(None, DEFAULT_NDCG_AT_K_DEPTHS) == tuple(
        DEFAULT_NDCG_AT_K_DEPTHS
    )


def test_stub_runner_fails_loud(evalmod: ModuleType) -> None:
    """No-runner default raises rather than silently producing zero metrics."""
    with pytest.raises(RuntimeError, match="no retrieval runner injected"):
        evalmod._stub_runner("bot-x", "q", 5)


def test_main_reads_sample_fixture(evalmod: ModuleType, tmp_path: Path) -> None:
    """End-to-end smoke against the committed sample_bot.jsonl fixture."""
    repo_root = Path(__file__).resolve().parents[2]
    sample_dir = repo_root / "tests" / "fixtures" / "golden_queries"
    assert (sample_dir / "sample_bot.jsonl").exists()

    def runner(bot_id: str, question: str, top_k: int) -> list[str]:
        # Stub: return the first expected doc id from the fixture so we
        # exercise the parse → score → render pipeline end-to-end.
        per_q = {
            "What is the return policy?": ["doc-001", "noise"],
            "How do I reset my password?": ["noise", "doc-003"],
            "Where can I find the API reference?": ["doc-005", "doc-004"],
        }
        return per_q.get(question, [])[:top_k]

    out_json = tmp_path / "out.json"
    rc = evalmod.main(
        [
            "--golden-dir",
            str(sample_dir),
            "--output-json",
            str(out_json),
            "--output-md",
            str(tmp_path / "out.md"),
        ],
        runner=runner,
    )
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    bot = next(b for b in payload["bots"] if b["record_bot_id"] == "sample_bot")
    # 3 queries, all hit at depth 3 (or earlier): hit@3 = 1.0.
    assert bot["total_queries"] == 3
    assert bot["hit_at_k"]["3"] == pytest.approx(1.0)
