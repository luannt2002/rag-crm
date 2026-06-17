"""Unit tests for ``scripts/loadtest_master_ablation.py`` + ``decision_gate.py``.

Stream 20 (master-of-master plan) — Master Ablation Loadtest.

These tests use a **synthetic golden Q set** and an **in-memory asker** so the
suite runs in seconds with no live HTTP. We assert real behavior:

  1. Feature matrix wires correctly to known DEFAULT_*_ENABLED constants.
  2. ``build_named_configs`` produces the 4 canonical configs with the right
     overrides for each tier.
  3. ``classify`` correctly bins (answer, trap?) into PASS / HALLU / REFUSE.
  4. ``aggregate`` computes PASS%, HALLU count, p50/p95/p99, avg cost.
  5. ``classify_feature_decision`` produces KEEP / TUNE / DROP per spec —
     including the HALLU-sacred guard (feature introduces HALLU ⇒ DROP).
  6. End-to-end ``run_master_ablation`` writes ``aggregate.json`` +
     ``results.md`` + per-config records; the markdown contains the
     expected sections.
  7. ``decision_gate.gate`` re-classifies the aggregate identically to the
     in-line decision matrix.

Domain-neutral: all bot_ids / industries are fixture-synthetic
(``mock-gov-bot`` style); zero brand literal.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load(name: str, file: str) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "scripts" / file
    assert path.exists(), f"script missing: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def master() -> ModuleType:
    return _load("_master_ablation_t", "loadtest_master_ablation.py")


@pytest.fixture(scope="module")
def gate_mod() -> ModuleType:
    return _load("_decision_gate_t", "decision_gate.py")


# ---------------------------------------------------------------------------
# Feature matrix wiring.
# ---------------------------------------------------------------------------


def test_default_feature_matrix_has_t1_and_t2(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    tiers = {f.tier for f in feats}
    # Must cover both smartness and cost/perf tiers (T3 is the loadtest tier
    # itself, not an ablated feature).
    assert "T1" in tiers, f"T1 features missing, got tiers={tiers}"
    assert "T2" in tiers, f"T2 features missing, got tiers={tiers}"
    # At least one T1 and one T2.
    assert sum(1 for f in feats if f.tier == "T1") >= 1
    assert sum(1 for f in feats if f.tier == "T2") >= 1


def test_feature_flags_resolve_to_real_constants(master: ModuleType) -> None:
    """Each ``FeatureSpec.flag_constant`` must exist in shared.constants."""
    from ragbot.shared import constants as C  # type: ignore[import-not-found]
    feats = master.build_default_feature_matrix()
    for f in feats:
        assert hasattr(C, f.flag_constant), (
            f"flag_constant {f.flag_constant} for feature {f.name} "
            f"not found in shared.constants"
        )


def test_feature_env_var_naming_convention(master: ModuleType) -> None:
    """Env vars must start with the RAGBOT_FLAG_ namespace prefix."""
    for f in master.build_default_feature_matrix():
        assert f.env_var.startswith("RAGBOT_FLAG_"), (
            f"env_var {f.env_var} for {f.name} must use RAGBOT_FLAG_ prefix"
        )


# ---------------------------------------------------------------------------
# Named configs.
# ---------------------------------------------------------------------------


def test_named_configs_have_canonical_four(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    assert set(cfgs.keys()) == {
        "baseline_off", "t1_only", "t2_only", "all_on",
    }


def test_baseline_off_disables_all(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    base = cfgs["baseline_off"]
    for f in feats:
        assert base.overrides[f.name] is False, (
            f"baseline_off must disable {f.name}, got {base.overrides[f.name]}"
        )


def test_all_on_enables_all(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    allon = cfgs["all_on"]
    for f in feats:
        assert allon.overrides[f.name] is True, (
            f"all_on must enable {f.name}, got {allon.overrides[f.name]}"
        )


def test_t1_only_isolates_t1(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    t1cfg = cfgs["t1_only"]
    for f in feats:
        if f.tier == "T1":
            assert t1cfg.overrides[f.name] is True, f"{f.name} T1 → expect ON"
        else:
            assert t1cfg.overrides[f.name] is False, (
                f"{f.name} ({f.tier}) → expect OFF in t1_only"
            )


def test_t2_only_isolates_t2(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    t2cfg = cfgs["t2_only"]
    for f in feats:
        if f.tier == "T2":
            assert t2cfg.overrides[f.name] is True
        else:
            assert t2cfg.overrides[f.name] is False


def test_ablation_configs_drop_one_each(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    abls = master.build_ablation_configs(feats)
    assert len(abls) == len(feats)
    for f, cfg in zip(feats, abls):
        assert cfg.name == f"ablate_{f.name}"
        # Every other feature is ON, this one is OFF.
        for other in feats:
            if other.name == f.name:
                assert cfg.overrides[other.name] is False
            else:
                assert cfg.overrides[other.name] is True


def test_overrides_to_env_uses_lowercase_bool(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    cfgs = master.build_named_configs(feats)
    env = master.overrides_to_env(cfgs["all_on"].overrides, feats)
    # All env vars should map to "true" since all_on enables all.
    for f in feats:
        assert env[f.env_var] == "true", f"{f.env_var} expected 'true'"
    env_off = master.overrides_to_env(cfgs["baseline_off"].overrides, feats)
    for f in feats:
        assert env_off[f.env_var] == "false", f"{f.env_var} expected 'false'"


# ---------------------------------------------------------------------------
# Classifier.
# ---------------------------------------------------------------------------


def test_classify_pass_answered_non_trap(master: ModuleType) -> None:
    v = master.classify(
        "Theo Điều 1, phạm vi điều chỉnh bao gồm các tổ chức tín dụng.",
        is_trap=False, expected_verdict="ANSWERED",
    )
    assert v == "PASS_ANSWERED"


def test_classify_hallu_breach_on_trap(master: ModuleType) -> None:
    # Trap question + non-refusal answer = sacred breach.
    v = master.classify(
        "Lãi suất cụ thể là 7.5% theo bảng biểu định kỳ.",
        is_trap=True, expected_verdict="REFUSED",
    )
    assert v == "HALLU_BREACH"


def test_classify_pass_refused_on_trap(master: ModuleType) -> None:
    v = master.classify(
        "Xin lỗi, tôi không có thông tin về vấn đề này trong tài liệu được cung cấp.",
        is_trap=True, expected_verdict="REFUSED",
    )
    assert v == "PASS_REFUSED"


def test_classify_refuse_gap_on_answerable(master: ModuleType) -> None:
    v = master.classify(
        "Tôi không có dữ liệu để trả lời câu hỏi này.",
        is_trap=False, expected_verdict="ANSWERED",
    )
    assert v == "REFUSE_GAP"


def test_classify_err_on_empty(master: ModuleType) -> None:
    assert master.classify("", is_trap=False, expected_verdict="ANSWERED") == "ERR"


# ---------------------------------------------------------------------------
# Percentile + aggregate.
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_zero(master: ModuleType) -> None:
    assert master.percentile([], 50) == 0.0
    assert master.percentile([], 99) == 0.0


def test_percentile_basic(master: ModuleType) -> None:
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    # Nearest-rank: p50 → idx round(0.5*4)=2 → 30.0; p99 → idx 4 → 50.0.
    assert master.percentile(vals, 50) == 30.0
    assert master.percentile(vals, 99) == 50.0
    assert master.percentile(vals, 0) == 10.0


def test_aggregate_basic_metrics(master: ModuleType) -> None:
    records = [
        {
            "verdict": "PASS_ANSWERED", "hallu_trap": False,
            "latency_ms": 100, "cost_usd": 0.001,
        },
        {
            "verdict": "PASS_ANSWERED", "hallu_trap": False,
            "latency_ms": 200, "cost_usd": 0.002,
        },
        {
            "verdict": "PASS_REFUSED", "hallu_trap": True,
            "latency_ms": 50, "cost_usd": 0.0005,
        },
        {
            "verdict": "HALLU_BREACH", "hallu_trap": True,
            "latency_ms": 300, "cost_usd": 0.003,
        },
        {
            "verdict": "REFUSE_GAP", "hallu_trap": False,
            "latency_ms": 150, "cost_usd": 0.0015,
        },
    ]
    agg = master.aggregate(records)
    assert agg["n"] == 5
    assert agg["pass_total"] == 3  # 2 PASS_ANSWERED + 1 PASS_REFUSED
    assert agg["pass_rate_pct"] == 60.0
    assert agg["hallu_breach"] == 1
    assert agg["hallu_zero_sacred"] is False
    assert agg["refuse_gap"] == 1
    assert agg["trap_total"] == 2
    assert agg["non_trap_total"] == 3
    # Cost: sum/5 = 0.0080/5 = 0.0016
    assert agg["avg_cost_usd"] == pytest.approx(0.0016, abs=1e-6)
    assert agg["p50_latency_ms"] > 0
    assert agg["p95_latency_ms"] > 0
    assert agg["p99_latency_ms"] > 0


# ---------------------------------------------------------------------------
# Decision gate logic.
# ---------------------------------------------------------------------------


def _summary(
    *,
    pass_rate: float = 80.0,
    hallu: int = 0,
    p95: float = 1000.0,
    cost: float = 0.001,
    n: int = 90,
) -> dict[str, Any]:
    return {
        "n": n,
        "pass_rate_pct": pass_rate,
        "hallu_breach": hallu,
        "hallu_zero_sacred": hallu == 0,
        "p50_latency_ms": p95 * 0.6,
        "p95_latency_ms": p95,
        "p99_latency_ms": p95 * 1.2,
        "avg_cost_usd": cost,
        "total_cost_usd": cost * n,
    }


def test_decision_keep_on_strong_pass_lift(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=90.0),
        ablate=_summary(pass_rate=85.0),
    )
    assert d.verdict == "KEEP"
    assert d.pass_lift_pp == pytest.approx(5.0, abs=0.01)


def test_decision_drop_on_regression(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=80.0),
        ablate=_summary(pass_rate=85.0),  # ablating helps PASS rise → feature regressed
    )
    assert d.verdict == "DROP"
    assert "regression" in d.reason


def test_decision_drop_on_feature_hallu_introduction(master: ModuleType) -> None:
    """If turning the feature ON introduces HALLU (sacred breach) → DROP."""
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=85.0, hallu=2),
        ablate=_summary(pass_rate=83.0, hallu=0),
    )
    assert d.verdict == "DROP"
    assert "HALLU" in d.reason


def test_decision_keep_when_feature_is_hallu_protective(master: ModuleType) -> None:
    """If removing the feature triggers HALLU → KEEP regardless of PASS lift."""
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=80.0, hallu=0),
        ablate=_summary(pass_rate=80.0, hallu=3),
    )
    assert d.verdict == "KEEP"
    assert "protective" in d.reason


def test_decision_tune_on_marginal_lift(master: ModuleType) -> None:
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=81.0),
        ablate=_summary(pass_rate=80.0),  # +1pp lift → 0..2pp → TUNE
    )
    assert d.verdict == "TUNE"


def test_decision_keep_on_latency_drop_only(master: ModuleType) -> None:
    """If PASS stays flat but p95 drops materially → KEEP."""
    feats = master.build_default_feature_matrix()
    f = feats[0]
    d = master.classify_feature_decision(
        feature=f,
        all_on=_summary(pass_rate=80.0, p95=500.0),
        ablate=_summary(pass_rate=80.0, p95=1000.0),  # 50% drop
    )
    assert d.verdict == "KEEP"
    assert "p95 drop" in d.reason


# ---------------------------------------------------------------------------
# End-to-end with in-memory asker — verifies orchestrator + I/O artifacts.
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_fixture(tmp_path: Path) -> Path:
    """Tiny 12-question fixture: 3 bots × 4 patterns incl. 1 trap each."""
    qs: list[dict[str, Any]] = []
    qid = 0
    for bot in ("mock-bot-a", "mock-bot-b", "mock-bot-c"):
        for pat in ("single_entity", "multi_entity", "abbreviation"):
            qid += 1
            qs.append({
                "id": qid,
                "industry": "synthetic",
                "bot_id": bot,
                "channel_type": "web",
                "workspace_id": "ws-synthetic",
                "pattern": pat,
                "question": f"What is {pat} for {bot}?",
                "hallu_trap": False,
                "trap_kind": None,
                "expected_verdict": "ANSWERED",
            })
        # one trap per bot
        qid += 1
        qs.append({
            "id": qid,
            "industry": "synthetic",
            "bot_id": bot,
            "channel_type": "web",
            "workspace_id": "ws-synthetic",
            "pattern": "trap_hallu",
            "question": f"Tell me the exact percentage X for {bot}",
            "hallu_trap": True,
            "trap_kind": "fabricate",
            "expected_verdict": "REFUSED",
        })

    p = tmp_path / "synthetic.json"
    p.write_text(json.dumps(qs, ensure_ascii=False), encoding="utf-8")
    return p


def _make_fake_asker(master: ModuleType, *, scripted: dict[str, Any]):
    """Build an asker that returns deterministic answers from a script.

    ``scripted`` maps env_overlay key signature → answer pattern. Trap
    questions get a refusal if ``refuse_traps`` is True for that signature.
    """

    async def _ask(q: dict[str, Any], env_overlay: dict[str, str]) -> dict[str, Any]:
        # Use the All-ON signature as a key — count True flags.
        n_true = sum(1 for v in env_overlay.values() if v == "true")
        is_trap = bool(q.get("hallu_trap"))
        # Default behavior: more flags ON ⇒ more refuse on traps (good).
        refuse_traps = n_true >= len(env_overlay) // 2
        if is_trap:
            if refuse_traps:
                answer = "Xin lỗi, tôi không có thông tin về vấn đề này."
            else:
                answer = "Câu trả lời cụ thể là 7.5%."  # hallucination
        else:
            # Non-trap: always answer with a real-ish payload.
            answer = (
                f"Theo Điều 1, phạm vi điều chỉnh: {q.get('pattern')} cho bot."
            )
        return {
            "answer": answer,
            "answer_type": "rag",
            "top_score": 0.85,
            "chunks_used": 4,
            "latency_ms": 100 + (q["id"] % 50),
            "cost_usd": 0.0008 + (q["id"] % 5) * 0.00005,
            "trace_id": f"trace-{q['id']}",
            "request_id": f"req-{q['id']}",
        }

    return _ask


def test_end_to_end_run_writes_artifacts(
    master: ModuleType, synthetic_fixture: Path, tmp_path: Path,
) -> None:
    output_dir = tmp_path / "report"

    async def factory():
        return _make_fake_asker(master, scripted={})

    blob = asyncio.run(
        master.run_master_ablation(
            fixture_path=synthetic_fixture,
            output_dir=output_dir,
            per_bot_cap=4,
            pace_s=0.0,
            asker_factory=factory,
            skip_ablation=False,
        )
    )

    # Artifacts.
    assert (output_dir / "aggregate.json").exists()
    assert (output_dir / "results.md").exists()
    assert (output_dir / "records").is_dir()

    # JSON structure.
    assert set(blob["matrix"].keys()) == {
        "baseline_off", "t1_only", "t2_only", "all_on",
    }
    assert len(blob["ablation"]) == len(master.build_default_feature_matrix())
    assert len(blob["decisions"]) == len(master.build_default_feature_matrix())

    # Each matrix entry has all metrics.
    for name, s in blob["matrix"].items():
        assert "pass_rate_pct" in s
        assert "p50_latency_ms" in s
        assert "p95_latency_ms" in s
        assert "p99_latency_ms" in s
        assert "hallu_breach" in s
        assert s["n"] == 12, f"config {name} should run 12Q (4×3)"

    # Markdown contains all required sections.
    md = (output_dir / "results.md").read_text(encoding="utf-8")
    assert "# Master Ablation Load Test" in md
    assert "## 1. 4-Config Matrix" in md
    assert "## 2. Per-Feature Ablation" in md
    assert "## 3. Decision Matrix" in md
    assert "Proof / Methodology" in md


def test_end_to_end_skip_ablation_works(
    master: ModuleType, synthetic_fixture: Path, tmp_path: Path,
) -> None:
    output_dir = tmp_path / "report-no-abl"

    async def factory():
        return _make_fake_asker(master, scripted={})

    blob = asyncio.run(
        master.run_master_ablation(
            fixture_path=synthetic_fixture,
            output_dir=output_dir,
            per_bot_cap=4,
            pace_s=0.0,
            asker_factory=factory,
            skip_ablation=True,
        )
    )
    assert blob["ablation"] == {}
    assert blob["decisions"] == []


# ---------------------------------------------------------------------------
# Decision gate consumer.
# ---------------------------------------------------------------------------


def test_decision_gate_reproduces_inline_decisions(
    master: ModuleType,
    gate_mod: ModuleType,
    synthetic_fixture: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "report-gate"

    async def factory():
        return _make_fake_asker(master, scripted={})

    blob = asyncio.run(
        master.run_master_ablation(
            fixture_path=synthetic_fixture,
            output_dir=output_dir,
            per_bot_cap=4,
            pace_s=0.0,
            asker_factory=factory,
            skip_ablation=False,
        )
    )

    re_decisions = gate_mod.gate(blob)

    # Same set of features, same verdicts.
    assert {d["feature"] for d in re_decisions} == {
        d["feature"] for d in blob["decisions"]
    }
    by_name_inline = {d["feature"]: d["verdict"] for d in blob["decisions"]}
    by_name_gate = {d["feature"]: d["verdict"] for d in re_decisions}
    assert by_name_inline == by_name_gate, (
        f"gate disagrees: inline={by_name_inline} vs gate={by_name_gate}"
    )


def test_decision_gate_env_override_cannot_relax_hallu(
    gate_mod: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env RAGBOT_GATE_HALLU_SACRED_MAX=99 must be clamped to 0."""
    monkeypatch.setenv("RAGBOT_GATE_HALLU_SACRED_MAX", "99")

    # Synthetic aggregate where feature X introduces HALLU.
    blob = {
        "feature_set": [
            {"name": "hyde", "tier": "T1", "env_var": "RAGBOT_FLAG_HYDE_ENABLED"},
        ],
        "matrix": {
            "all_on": _summary(pass_rate=85.0, hallu=3),
        },
        "ablation": {
            "ablate_hyde": _summary(pass_rate=85.0, hallu=0),
        },
    }
    decisions = gate_mod.gate(blob)
    assert len(decisions) == 1
    # HALLU breach must NOT be relaxable: feature introduces HALLU ⇒ DROP.
    assert decisions[0]["verdict"] == "DROP"
    assert "HALLU" in decisions[0]["reason"]


def test_decision_gate_cli_exit_code_on_drop(
    master: ModuleType,
    gate_mod: ModuleType,
    synthetic_fixture: Path,
    tmp_path: Path,
) -> None:
    """CLI exits 2 if any DROP, 0 otherwise. Synthetic scenario: force a DROP."""
    # Build a minimal aggregate JSON file with one feature that should DROP.
    blob = {
        "feature_set": [
            {"name": "hyde", "tier": "T1", "env_var": "RAGBOT_FLAG_HYDE_ENABLED"},
        ],
        "matrix": {"all_on": _summary(pass_rate=80.0, hallu=5)},  # feature breaks sacred
        "ablation": {"ablate_hyde": _summary(pass_rate=80.0, hallu=0)},
    }
    agg_path = tmp_path / "aggregate.json"
    agg_path.write_text(json.dumps(blob), encoding="utf-8")

    rc = gate_mod.main(["--aggregate", str(agg_path)])
    assert rc == 2  # at least one DROP → exit 2


# ---------------------------------------------------------------------------
# Fixture loader.
# ---------------------------------------------------------------------------


def test_load_fixture_caps_per_bot(
    master: ModuleType, synthetic_fixture: Path,
) -> None:
    qs = master.load_fixture(synthetic_fixture, per_bot_cap=2)
    # 3 bots × cap 2 = 6 questions total.
    assert len(qs) == 6
    from collections import Counter
    by_bot = Counter(q["bot_id"] for q in qs)
    for bot, n in by_bot.items():
        assert n == 2, f"bot {bot} expected 2, got {n}"


def test_load_fixture_rejects_non_list(
    master: ModuleType, tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"questions": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="list of question dicts"):
        master.load_fixture(bad, per_bot_cap=10)
