"""Unit coverage for the ekimetrics chunk-scoring harness scripts.

Guards the pure logic of:
  * ``scripts/score_chunks_intrinsic.py`` — L1 intrinsic scorer
  * ``scripts/bakeoff_chunking_strategies.py`` — strategy bake-off

Both live in ``scripts/`` (not a package), loaded via ``importlib.util`` — the
same pattern as ``tests/unit/test_eval_hit_at_k.py``. No DB, no network: every
test feeds synthetic chunks / text and asserts on real values.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load(script_name: str, mod_name: str) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / script_name
    assert script_path.exists(), f"script missing at {script_path}"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def l1() -> ModuleType:
    return _load("score_chunks_intrinsic.py", "_score_chunks_intrinsic")


@pytest.fixture(scope="module")
def bake() -> ModuleType:
    return _load("bakeoff_chunking_strategies.py", "_bakeoff_chunking_strategies")


# --------------------------------------------------------------------------- #
# L1 — score_chunks_intrinsic
# --------------------------------------------------------------------------- #


def _leaf(content: str, chars: int | None = None) -> dict:
    return {"is_leaf": True, "content": content, "chunk_chars": chars or len(content)}


def _parent(content: str) -> dict:
    return {"is_leaf": False, "content": content, "chunk_chars": len(content)}


def test_composite_is_uniform_mean_of_five(l1: ModuleType) -> None:
    m = l1.IntrinsicMetrics(RC=1.0, ICC=0.5, DCC=0.5, BI=0.0, SC=1.0)
    # uniform 0.2 weight → (1+0.5+0.5+0+1)/5 = 0.6
    assert l1._composite(m) == pytest.approx(0.6)


def test_score_document_returns_metrics_in_unit_interval(l1: ModuleType) -> None:
    chunks = [
        _parent("Heading one. Body sentence about pricing and policy details."),
        _leaf("Body sentence about pricing and policy details for the bot."),
        _leaf("Another leaf chunk with somewhat related policy wording here."),
    ]
    s = l1.score_document("bot-x", "doc-1", chunks, target_chunk_chars=256)
    assert s is not None
    assert s.n_leaf == 2
    assert s.n_parent == 1
    for v in (s.metrics.RC, s.metrics.ICC, s.metrics.DCC, s.metrics.BI, s.metrics.SC):
        assert 0.0 <= v <= 1.0
    assert 0.0 <= s.composite <= 1.0


def test_score_document_none_when_no_leaves(l1: ModuleType) -> None:
    # parents only (small-to-big with no embedded children) → nothing to score
    assert l1.score_document("b", "d", [_parent("only a parent block")],
                             target_chunk_chars=256) is None


def test_aggregate_by_bot_averages_per_bot(l1: ModuleType) -> None:
    mk = lambda rc: l1.DocScore(  # noqa: E731 — terse test factory
        bot_id="b", record_document_id="d", n_leaf=1, n_parent=0,
        mean_leaf_chars=100.0,
        metrics=l1.IntrinsicMetrics(RC=rc, ICC=0.0, DCC=0.0, BI=0.0, SC=0.0),
    )
    agg = l1.aggregate_by_bot([mk(1.0), mk(0.0)])
    assert agg["b"].RC == pytest.approx(0.5)


def test_l1_render_json_is_parseable(l1: ModuleType) -> None:
    chunks = [_leaf("a leaf chunk of policy text"), _leaf("second leaf of text")]
    s = l1.score_document("bot-x", "doc-1", chunks, target_chunk_chars=256)
    per_bot = l1.aggregate_by_bot([s])
    payload = json.loads(l1.render_json(per_bot, [s]))
    assert payload["metric_impl"] == "lexical"
    assert "bot-x" in payload["per_bot"]
    assert payload["per_document"][0]["n_leaf"] == 2


# --------------------------------------------------------------------------- #
# bake-off — bakeoff_chunking_strategies
# --------------------------------------------------------------------------- #

_HEADING_DOC = (
    "# Chương 1: Quy định chung\n\n"
    "Điều khoản đầu tiên mô tả phạm vi áp dụng của chính sách này một cách rõ ràng. "
    "Nội dung bao gồm nhiều câu mô tả chi tiết để bảo đảm độ dài đủ cho việc phân đoạn.\n\n"
    "## Mục 1.1: Phạm vi\n\n"
    "Phạm vi áp dụng cho mọi đối tượng được nêu trong văn bản này và các phụ lục kèm theo. "
    "Mỗi đoạn cần đủ dài để các chiến lược phân đoạn tạo ra nhiều hơn một chunk khi chạy.\n\n"
    "# Chương 2: Điều khoản chi tiết\n\n"
    "Điều khoản chi tiết quy định cách thức thực hiện và trách nhiệm của các bên liên quan. "
    "Phần này lặp lại đủ nội dung để vượt ngưỡng kích thước một chunk đơn lẻ trong thử nghiệm."
) * 4


def test_bakeoff_document_scores_all_strategies(bake: ModuleType) -> None:
    r = bake.bakeoff_document("bot-x", "doc-1", _HEADING_DOC)
    assert r is not None
    # every prose strategy that produced chunks must have a composite in [0,1]
    assert set(r.scores).issubset(set(bake.STRATEGIES))
    assert r.scores, "expected at least one strategy to produce chunks"
    for v in r.scores.values():
        assert 0.0 <= v <= 1.0
    # oracle_best is the argmax and gap is non-negative by construction
    assert r.oracle_best in r.scores
    assert r.scores[r.oracle_best] == max(r.scores.values())
    assert r.gap >= 0.0


def test_bakeoff_document_none_on_empty_text(bake: ModuleType) -> None:
    assert bake.bakeoff_document("b", "d", "   ") is None


def test_bakeoff_adaptive_pick_is_a_known_strategy(bake: ModuleType) -> None:
    r = bake.bakeoff_document("bot-x", "doc-1", _HEADING_DOC)
    assert r is not None
    # select_strategy may return table_csv etc.; adaptive_composite must still
    # resolve to a real number via the recursive fallback.
    assert isinstance(r.adaptive_pick, str) and r.adaptive_pick
    assert 0.0 <= r.adaptive_composite <= 1.0


def test_bakeoff_render_json_is_parseable(bake: ModuleType) -> None:
    r = bake.bakeoff_document("bot-x", "doc-1", _HEADING_DOC)
    payload = json.loads(bake.render_json([r]))
    assert payload["metric_impl"] == "lexical"
    assert payload["aggregate"]["documents"] == 1
    assert payload["documents"][0]["bot_id"] == "bot-x"
