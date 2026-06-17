"""Mode-aware reranker minimum-score gate.

The rerank node reads two mode-specific floors so the bypass path
(RRF scores in the 0.01-0.05 range) and the active path (cross-encoder
0..1 scores) can apply meaningfully different cuts:

* ``DEFAULT_RERANKER_MIN_SCORE_ACTIVE`` — used when the rerank adapter
  actually ran. Floor matches the PLAN_LIMIT_SCHEMA default so per-bot
  tuning and the constant agree.
* ``DEFAULT_RERANKER_MIN_SCORE_BYPASS`` — used when the node bypassed
  (disabled / no_reranker / empty_input).
"""

from __future__ import annotations


def test_active_threshold_higher_than_bypass():
    """Active (0..1 cross-encoder) must keep a non-zero floor while bypass
    (small RRF scores) leaves the gate fully open."""
    from ragbot.shared.constants import (
        DEFAULT_RERANKER_MIN_SCORE_ACTIVE,
        DEFAULT_RERANKER_MIN_SCORE_BYPASS,
    )

    assert DEFAULT_RERANKER_MIN_SCORE_ACTIVE > DEFAULT_RERANKER_MIN_SCORE_BYPASS
    # Active floor stays above the legacy 0.01 bypass-shaped default so
    # cross-encoder noise still gets dropped.
    assert DEFAULT_RERANKER_MIN_SCORE_ACTIVE >= 0.10
    # Bypass = no filter (RRF range too small to threshold meaningfully).
    assert DEFAULT_RERANKER_MIN_SCORE_BYPASS == 0.0


def test_backcompat_default_kept_for_backward_compat():
    """Legacy single-key default still imports — call sites that read
    ``reranker_min_score`` (without a ``_active`` / ``_bypass`` suffix)
    keep working until  cleanup.
    """
    from ragbot.shared.constants import DEFAULT_RERANKER_MIN_SCORE

    # Legacy value is bypass-shaped (0.01) — the rerank node treats it as
    # such and promotes to mode-default when the live mode is active.
    assert DEFAULT_RERANKER_MIN_SCORE == 0.01


def test_query_graph_resolves_mode_specific_keys():
    """The query_graph rerank node must read the mode-specific config keys.

    Static-text assertion: confirms both ``reranker_min_score_active`` and
    ``reranker_min_score_bypass`` appear in the file (i.e. the new lookup
    branches are wired). Behaviour-level assertions are covered by the
    integration smoke test that exercises the chat pipeline end-to-end.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "orchestration"
        / "query_graph.py"
    )
    body = src.read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py")))

    assert "reranker_min_score_active" in body
    assert "reranker_min_score_bypass" in body
    # The mode-aware constants must be imported (not magic numbers).
    assert "DEFAULT_RERANKER_MIN_SCORE_ACTIVE" in body
    assert "DEFAULT_RERANKER_MIN_SCORE_BYPASS" in body


def test_chat_worker_forwards_both_mode_keys():
    """chat_worker must propagate BOTH new keys into pipeline_config so
    the rerank node can resolve them via _pcfg(state, ...).
    """
    from pathlib import Path

    # chat_worker was split into a package — scan every module.
    pkg = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "interfaces"
        / "workers"
        / "chat_worker"
    )
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )

    # Both keys must appear in the pipeline_config dict.
    assert '"reranker_min_score_active"' in body
    assert '"reranker_min_score_bypass"' in body
