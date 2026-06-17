"""Regression test for issue-1 — ``_fanout_bypassed`` gate logic.

Bug fixed in commit (this commit): ``query_graph.py:2399`` was
``not decompose_active and not _has_preset_mq``, which set
``_fanout_bypassed=True`` precisely when NEITHER decompose succeeded
NOR a preset MQ list was supplied — i.e. the situation where the
LLM-paraphrase fanout branch (line 2410 elif) must run. Result: VN
compound-entity queries like "Điều 38 và 3" where decompose soft-fails
got 0 fanout fires → 1 retrieve → missed sibling articles.

Fixed semantic: ``_fanout_bypassed = decompose_active or _has_preset_mq``.
Bypass only when sub_queries already exist or preset is upstream-supplied.

The gate logic is inline in ``query_graph.py`` rather than a separate
function, so this regression test reconstructs the truth table the gate
must satisfy. Future refactors that lift the gate into a helper can
import this table directly.
"""

from __future__ import annotations

import pytest


def _compute_fanout_bypassed(*, decompose_active: bool, has_preset_mq: bool) -> bool:
    """Mirror of the gate at query_graph.py:2399 (post-fix).

    Two-source-of-truth alarm: if you change this helper without
    changing the inline gate in query_graph.py, the regression test
    becomes a lie. The inline gate is canonical; this helper exists
    only for the parametric test below.
    """
    return decompose_active or has_preset_mq


@pytest.mark.parametrize(
    "decompose_active, has_preset_mq, expected_bypass, reason",
    [
        (True, False, True, "decompose-succeeded: sub_queries available, no fanout needed"),
        (False, True, True, "preset-mq supplied upstream: use preset branch, no LLM fanout"),
        (False, False, False, "neither: LLM-paraphrase fanout MUST run (Case B path)"),
        (True, True, True, "both: bypass (decompose wins; preset ignored downstream)"),
    ],
)
def test_fanout_bypass_truth_table(decompose_active, has_preset_mq, expected_bypass, reason):
    """The four-cell truth table the gate must satisfy.

    Pre-fix, the (False, False) row produced True — that was the bug.
    Post-fix the row produces False, unblocking the LLM-paraphrase
    branch for compound queries where decompose soft-fails.
    """
    got = _compute_fanout_bypassed(
        decompose_active=decompose_active,
        has_preset_mq=has_preset_mq,
    )
    assert got is expected_bypass, (
        f"Gate broke for ({decompose_active=}, {has_preset_mq=}): "
        f"expected {expected_bypass} — reason: {reason}"
    )


def test_inline_gate_matches_helper():
    """Static guard: scan the live source of query_graph.py and assert
    the gate line uses the post-fix expression.

    Catches a regression where someone re-introduces the inverted form
    (``not decompose_active and not _has_preset_mq``) — that is the
    pattern that broke Case B "Điều 38 và 3" pre-fix.
    """
    from pathlib import Path

    # The inline retrieve fanout gate moved out of build_graph into
    # orchestration/nodes/retrieve.py (pure relocation); scan both the
    # orchestrator wiring file and every node module.
    _orch = Path(__file__).resolve().parents[2] / "src/ragbot/orchestration"
    text = (_orch / "query_graph.py").read_text(encoding="utf-8") + "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((_orch / "nodes").glob("*.py"))
    )

    # Allowed (post-fix) form. The exact expression must appear somewhere.
    assert "_fanout_bypassed = decompose_active or _has_preset_mq" in text, (
        "Post-fix gate expression missing from query_graph.py — refactor "
        "may have moved it. Update this test or restore the expression."
    )

    # Forbidden (pre-fix) form. Must NOT appear.
    assert "_fanout_bypassed = not decompose_active and not _has_preset_mq" not in text, (
        "Pre-fix inverted gate has been re-introduced in query_graph.py. "
        "See plans/260515-multi-query-audit-fix/issues/issue-1-fanout-bypass-logic.md"
    )
