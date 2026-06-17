"""Unit tests for the Phase D Stream D5 smartness 300Q fixture + tooling.

Coverage:
1. Fixture schema (every record has required keys, valid types, valid
   ``pattern`` and ``expected_verdict``).
2. Fixture distribution (300 total, 100 per bot, 7 patterns × 3 bots).
3. Runner ``classify()`` — sacred HALLU breach, refuse-gap, pass cases.
4. Analyzer ``reclassify()`` — idempotent under heuristic re-run.
5. Analyzer ``analyze()`` — per-bot + per-pattern aggregation correctness.
6. Analyzer ``analyze()`` — acceptance gate FAIL when HALLU breach > 0.
7. Analyzer ``render_markdown()`` — emits PASS/FAIL verdict line.

These are pure-Python tests: no network, no DB, no Docker.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "tests" / "loadtest" / "smartness_300q_fixture.json"
RUNNER_PATH = REPO_ROOT / "scripts" / "loadtest_smartness_300q.py"
ANALYZER_PATH = REPO_ROOT / "scripts" / "analyze_smartness_300q.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fixture_records() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(scope="module")
def analyzer_mod():
    return _load_module("analyze_smartness_300q", ANALYZER_PATH)


@pytest.fixture(scope="module")
def runner_mod():
    return _load_module("loadtest_smartness_300q", RUNNER_PATH)


# ---------------------------------------------------------------------------
# 1. Fixture schema
# ---------------------------------------------------------------------------
REQUIRED_KEYS = {
    "id",
    "industry",
    "bot_id",
    "channel_type",
    "workspace_id",
    "pattern",
    "question",
    "hallu_trap",
    "trap_kind",
    "expected_verdict",
    "notes",
}
VALID_PATTERNS = {
    "single_entity",
    "multi_entity",
    "typo_no_diacritic",
    "abbreviation",
    "semantic",
    "cross_reference",
    "trap_hallu",
}
VALID_VERDICTS = {"ANSWERED", "REFUSED"}


def test_fixture_schema_every_record_has_required_keys(fixture_records):
    """Schema gate — missing key = silent runner fail."""
    for rec in fixture_records:
        missing = REQUIRED_KEYS - set(rec.keys())
        assert not missing, f"id={rec.get('id')} missing keys: {missing}"
        assert rec["pattern"] in VALID_PATTERNS, (
            f"id={rec['id']} bad pattern {rec['pattern']!r}"
        )
        assert rec["expected_verdict"] in VALID_VERDICTS, (
            f"id={rec['id']} bad verdict {rec['expected_verdict']!r}"
        )
        # trap consistency
        if rec["pattern"] == "trap_hallu":
            assert rec["hallu_trap"] is True, f"id={rec['id']} trap mismatch"
            assert rec["expected_verdict"] == "REFUSED"
        else:
            assert rec["hallu_trap"] is False


# ---------------------------------------------------------------------------
# 2. Fixture distribution
# ---------------------------------------------------------------------------
def test_fixture_distribution_300_total_100_per_bot_7_patterns(fixture_records):
    """Spec §D5: 3 bot × 100Q × 7 pattern = 300 turns."""
    assert len(fixture_records) == 300, f"got {len(fixture_records)}"
    by_bot: dict[str, int] = {}
    by_pat: dict[str, int] = {}
    for rec in fixture_records:
        by_bot[rec["bot_id"]] = by_bot.get(rec["bot_id"], 0) + 1
        by_pat[rec["pattern"]] = by_pat.get(rec["pattern"], 0) + 1
    assert len(by_bot) == 3, f"expected 3 bots, got {sorted(by_bot)}"
    for bot, count in by_bot.items():
        assert count == 100, f"{bot}: expected 100, got {count}"
    assert set(by_pat) == VALID_PATTERNS, (
        f"missing patterns: {VALID_PATTERNS - set(by_pat)}"
    )


# ---------------------------------------------------------------------------
# 3. Runner classify()
# ---------------------------------------------------------------------------
class TestRunnerClassify:
    def test_trap_unrefused_is_hallu_breach(self, runner_mod):
        """Sacred: trap question + non-refusal = HALLU_BREACH."""
        verdict = runner_mod.classify(
            "Điều 9001 quy định về tài sản công.",
            is_trap=True,
            expected_verdict="REFUSED",
        )
        assert verdict == "HALLU_BREACH"

    def test_trap_refused_is_pass_refused(self, runner_mod):
        """Trap + refusal cue = PASS_REFUSED."""
        verdict = runner_mod.classify(
            "Xin lỗi, em chưa có thông tin về điều này.",
            is_trap=True,
            expected_verdict="REFUSED",
        )
        assert verdict == "PASS_REFUSED"

    def test_answerable_refused_is_refuse_gap(self, runner_mod):
        """Answerable + refusal = REFUSE_GAP (bot too cautious)."""
        verdict = runner_mod.classify(
            "Hiện chưa có thông tin trong tài liệu.",
            is_trap=False,
            expected_verdict="ANSWERED",
        )
        assert verdict == "REFUSE_GAP"

    def test_answerable_answered_is_pass_answered(self, runner_mod):
        """Answerable + content = PASS_ANSWERED."""
        verdict = runner_mod.classify(
            "Điều 1 quy định phạm vi áp dụng của thông tư.",
            is_trap=False,
            expected_verdict="ANSWERED",
        )
        assert verdict == "PASS_ANSWERED"

    def test_empty_answer_is_err(self, runner_mod):
        assert (
            runner_mod.classify("", is_trap=False, expected_verdict="ANSWERED")
            == "ERR"
        )


# ---------------------------------------------------------------------------
# 4. Analyzer reclassify() — idempotent
# ---------------------------------------------------------------------------
def test_analyzer_reclassify_matches_runner_classify(analyzer_mod, runner_mod):
    """Running analyzer over runner output yields the same verdicts."""
    cases = [
        {
            "answer": "Điều 1 nói về phạm vi áp dụng.",
            "hallu_trap": False,
            "expected_verdict": "ANSWERED",
        },
        {
            "answer": "Xin lỗi, em chưa có thông tin.",
            "hallu_trap": True,
            "expected_verdict": "REFUSED",
        },
        {
            "answer": "Điều 9999 nói về abc xyz.",
            "hallu_trap": True,
            "expected_verdict": "REFUSED",
        },
        {
            "answer": "",
            "hallu_trap": False,
            "expected_verdict": "ANSWERED",
            "error": "timeout",
        },
    ]
    for c in cases:
        from_runner = runner_mod.classify(
            c["answer"],
            is_trap=c["hallu_trap"],
            expected_verdict=c["expected_verdict"],
        )
        from_analyzer = analyzer_mod.reclassify(c)
        assert from_runner == from_analyzer, (
            f"mismatch on case={c}: runner={from_runner} "
            f"analyzer={from_analyzer}"
        )


# ---------------------------------------------------------------------------
# 5. Analyzer analyze() — per-bot + per-pattern aggregation
# ---------------------------------------------------------------------------
def test_analyzer_aggregates_per_bot_and_per_pattern(analyzer_mod):
    records = [
        # bot A: 2 pass (single_entity), 1 hallu breach (trap)
        {
            "bot_id": "bot-a", "pattern": "single_entity",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Điều 1 quy định abc.", "latency_ms": 1000,
        },
        {
            "bot_id": "bot-a", "pattern": "single_entity",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Điều 2 quy định xyz.", "latency_ms": 2000,
        },
        {
            "bot_id": "bot-a", "pattern": "trap_hallu",
            "hallu_trap": True, "expected_verdict": "REFUSED",
            "answer": "Điều 9999 quy định khoản tiền 500.", "latency_ms": 1500,
        },
        # bot B: 1 pass refused, 1 refuse-gap
        {
            "bot_id": "bot-b", "pattern": "trap_hallu",
            "hallu_trap": True, "expected_verdict": "REFUSED",
            "answer": "Xin lỗi, em chưa có thông tin.", "latency_ms": 800,
        },
        {
            "bot_id": "bot-b", "pattern": "semantic",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Xin lỗi, không tìm thấy.", "latency_ms": 1200,
        },
    ]
    analysis = analyzer_mod.analyze(records)

    assert analysis["totals"]["total"] == 5
    assert analysis["totals"]["hallu_breach"] == 1
    assert analysis["totals"]["refuse_gap"] == 1

    bot_a = analysis["per_bot"]["bot-a"]
    assert bot_a["total"] == 3
    assert bot_a["pass"] == 2
    assert bot_a["hallu_breach"] == 1

    bot_b = analysis["per_bot"]["bot-b"]
    assert bot_b["total"] == 2
    assert bot_b["pass"] == 1
    assert bot_b["refuse_gap"] == 1

    pat_trap = analysis["per_pattern"]["trap_hallu"]
    assert pat_trap["total"] == 2
    assert pat_trap["pass"] == 1
    assert pat_trap["hallu_breach"] == 1


# ---------------------------------------------------------------------------
# 6. Acceptance gate — HALLU breach > 0 → overall FAIL
# ---------------------------------------------------------------------------
def test_acceptance_gate_fails_when_hallu_breach_nonzero(analyzer_mod):
    """Sacred: 1 hallu breach → overall FAIL regardless of pass rate."""
    # 100% pass rate but one trap leaks → must FAIL.
    records = [
        {
            "bot_id": "bot-a", "pattern": "single_entity",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Điều 1 nói abc.", "latency_ms": 100,
        }
        for _ in range(99)
    ] + [
        {
            "bot_id": "bot-a", "pattern": "trap_hallu",
            "hallu_trap": True, "expected_verdict": "REFUSED",
            "answer": "Điều 9999 quy định abc.",  # NO refusal cue → breach
            "latency_ms": 100,
        }
    ]
    analysis = analyzer_mod.analyze(records)
    assert analysis["totals"]["hallu_breach"] == 1
    assert analysis["acceptance"]["hallu_zero_sacred"] is False
    assert analysis["acceptance"]["overall"] is False


def test_acceptance_gate_passes_on_clean_run(analyzer_mod):
    """All pass + zero hallu + fast latency → overall PASS."""
    records = [
        {
            "bot_id": "bot-a", "pattern": "single_entity",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Điều 1 quy định abc.", "latency_ms": 200,
        }
        for _ in range(50)
    ] + [
        {
            "bot_id": "bot-a", "pattern": "trap_hallu",
            "hallu_trap": True, "expected_verdict": "REFUSED",
            "answer": "Xin lỗi, em chưa có thông tin.", "latency_ms": 200,
        }
        for _ in range(50)
    ]
    analysis = analyzer_mod.analyze(records)
    assert analysis["acceptance"]["hallu_zero_sacred"] is True
    assert analysis["acceptance"]["per_bot_pass_rate_ok"] is True
    assert analysis["acceptance"]["overall"] is True


# ---------------------------------------------------------------------------
# 7. Markdown rendering emits PASS/FAIL line
# ---------------------------------------------------------------------------
def test_render_markdown_emits_verdict_line(analyzer_mod):
    records = [
        {
            "bot_id": "bot-a", "pattern": "single_entity",
            "hallu_trap": False, "expected_verdict": "ANSWERED",
            "answer": "Điều 1 abc.", "latency_ms": 100,
        }
    ]
    analysis = analyzer_mod.analyze(records)
    md = analyzer_mod.render_markdown(analysis, label="UNITTEST")
    assert "Overall verdict:" in md
    assert "UNITTEST" in md
    assert "Per-bot breakdown" in md
    assert "Per-pattern breakdown" in md
