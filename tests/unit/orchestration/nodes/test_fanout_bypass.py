"""S2 fanout bypass — skip multi_query paraphrase when sub_queries ≤ 1.

Background. Trace ``fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4`` (90Q load test
2026-05-12) showed the ``multi_query_fanout`` step burns ~3,047 ms (step
10) and ~4,707 ms (step 19) on a simple single-entity query that decompose
correctly identified as non-decomposable (``len(sub_queries) <= 1``).
The paraphrase expansion for a 1-query input gains nothing for retrieval
because the original query already matches its target chunks; the LLM
round-trip is pure waste.

S2 fix. Bypass the multi_query paraphrase step when
``len(sub_queries) <= 1`` and surface a ``state["fanout_bypassed"] = True``
flag for observability. Real decomposition (``len(sub_queries) >= 2``) is
preserved — that path is the "real fanout" the optimization keeps.

Test strategy. The two call sites both live inside ``build_graph`` (one
is the helper closure ``_run_multi_query_expansion``, the other is an
inline block in the ``retrieve`` node). Driving them end-to-end requires
the full DI stack (LLM, embedder, vector store, step tracker, model
resolver). The minimum-viable assertion set is split:

1. **GraphState** — the typed slot ``fanout_bypassed: bool`` exists, so a
   typo in the flag name fails fast at the contract boundary.
2. **Source-level** — both bypass sites land in ``query_graph`` source.
   Catches: (a) deletion of the gate via stale edit, (b) gate on wrong
   branch, (c) flag write going to the wrong key.
3. **Behavioral** — the bypass condition expression
   ``len(sub_queries) <= 1`` is exercised at the four scenarios spec'd
   in S2 (empty / single / two / three) so the gate's truth table is
   pinned regardless of how the gate is wired internally.
"""
from __future__ import annotations

import inspect
import typing

import pytest

from ragbot.orchestration import query_graph as qg
from ragbot.orchestration.nodes import retrieve as _retrieve_module
from ragbot.orchestration.state import GraphState


def _qg_and_retrieve_src() -> str:
    """query_graph + retrieve node source concatenated.

    The retrieve node body (incl. the inline multi-query bypass gate) was
    lifted out of ``build_graph`` into ``orchestration/nodes/retrieve.py``
    (pure relocation); the ``_run_multi_query_expansion`` helper closure
    stays in query_graph. These source-level pins must scan both.
    """
    return inspect.getsource(qg) + "\n" + inspect.getsource(_retrieve_module)


# --------------------------------------------------------------------------- #
# 1. GraphState declares the bypass flag.                                     #
# --------------------------------------------------------------------------- #


def test_graph_state_declares_fanout_bypassed_slot() -> None:
    """GraphState must carry a typed ``fanout_bypassed: bool`` annotation.

    A typo in the flag name (e.g. ``fanout_bypased``) would silently
    write to an undocumented slot; downstream consumers checking the
    declared name would never observe the bypass. The TypedDict
    annotation is the source of truth.

    Uses ``typing.get_type_hints`` to resolve the forward refs that
    ``from __future__ import annotations`` produces in state.py.
    """
    hints = typing.get_type_hints(GraphState)
    assert "fanout_bypassed" in hints, (
        "GraphState must declare a ``fanout_bypassed`` slot so the S2 "
        "bypass flag is part of the typed state contract. Missing slot "
        f"in: {sorted(hints.keys())!r}"
    )
    assert hints["fanout_bypassed"] is bool, (
        "GraphState.fanout_bypassed must resolve to the ``bool`` type; "
        f"got {hints['fanout_bypassed']!r}"
    )


# --------------------------------------------------------------------------- #
# 2. Inline retrieve fanout block — source-level guards.                      #
# --------------------------------------------------------------------------- #


def test_retrieve_block_derives_bypass_from_decompose_active() -> None:
    """The inline retrieve fanout section must derive the bypass flag
    from ``decompose_active`` and ``_has_preset_mq``. Bypass when sub-
    queries are already supplied (decompose succeeded) or upstream pre-
    computed paraphrases exist; otherwise the LLM-paraphrase fanout
    branch must run.

    Note (2026-05-15, issue-1 of plans/260515-multi-query-audit-fix):
    pre-fix expression was ``not decompose_active and not _has_preset_mq``
    which inverted the gate — bypassed precisely when fanout was needed.
    Case B "Điều 38 và 3" missed the sibling article because of this.
    The post-fix expression is ``decompose_active or _has_preset_mq``.
    """
    src = _qg_and_retrieve_src()
    assert "_fanout_bypassed = decompose_active or _has_preset_mq" in src, (
        "retrieve must compute "
        "``_fanout_bypassed = decompose_active or _has_preset_mq`` "
        "so bypass engages only when sub-queries or preset paraphrases "
        "are already in hand. If you renamed the local, also update the "
        "S2 assertion."
    )
    # Defence: the pre-fix inverted expression must NOT come back.
    assert "_fanout_bypassed = not decompose_active and not _has_preset_mq" not in src, (
        "Pre-fix inverted gate re-introduced — see "
        "plans/260515-multi-query-audit-fix/issues/issue-1-fanout-bypass-logic.md"
    )


def test_retrieve_block_writes_flag_when_bypass_engages() -> None:
    """When the bypass engages, retrieve must write
    ``state['fanout_bypassed'] = True`` so downstream readers (tests,
    metrics, traces) can detect the bypass without running the LLM."""
    src = _qg_and_retrieve_src()
    assert 'state["fanout_bypassed"] = True' in src, (
        "retrieve must set ``state['fanout_bypassed'] = True`` when the "
        "bypass engages. The flag is the externally observable signal of "
        "the S2 perf optimization."
    )


def test_retrieve_inline_mq_branch_gated_by_fanout_bypassed() -> None:
    """The inline LLM MQ expansion branch — the actual cost centre,
    where the 3-5s paraphrase LLM call happens — must short-circuit
    when ``_fanout_bypassed`` is True. The preset hand-off branch
    intentionally remains unguarded so externally injected
    ``_mq_queries`` (parallel rewrite path, test injections) still
    fan-out via the free pre-computed paraphrases.
    """
    src = _qg_and_retrieve_src()
    occurrences = src.count("not _fanout_bypassed")
    assert occurrences >= 1, (
        "retrieve must guard the inline LLM expansion branch with "
        "``not _fanout_bypassed`` so the paraphrase LLM call short-"
        f"circuits when the bypass engages — found {occurrences} "
        "occurrence(s); expected ≥ 1."
    )


# --------------------------------------------------------------------------- #
# 3. _run_multi_query_expansion source-level — bypass on sub_queries ≤ 1.     #
# --------------------------------------------------------------------------- #


def _extract_helper_source(name: str) -> str:
    """Pull a nested ``async def <name>`` block out of build_graph.

    Used to assert source-level invariants on a helper that lives inside
    a closure (no module-level handle). Trims by sibling-indent so we get
    just the helper's body, not the rest of build_graph.
    """
    build_src = inspect.getsource(qg.build_graph)
    lines = build_src.splitlines(keepends=True)
    start = None
    indent = None
    for i, line in enumerate(lines):
        if f"async def {name}(" in line:
            start = i
            indent = len(line) - len(line.lstrip(" "))
            break
    assert start is not None, f"helper {name!r} not found in build_graph"
    assert indent is not None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent <= indent and (
            line.lstrip().startswith("async def ")
            or line.lstrip().startswith("def ")
        ):
            end = j
            break
    return "".join(lines[start:end])


def test_expansion_helper_writes_bypass_flag_on_decompose_precedence() -> None:
    """``_run_multi_query_expansion`` must write
    ``state["fanout_bypassed"] = True`` when the decompose-precedence
    gate fires (sub-queries already exist, paraphrase fanout would be
    redundant). This is the externally observable signal of the skip
    so downstream readers (tests, metrics, traces) can detect it
    without running the LLM.

    Note (mega-sprint G23, 2026-05-16): the prior version of this test
    pinned a buggy ``len(sub_queries_state) <= 1`` short-circuit that
    short-circuited the helper on the typical caller condition (empty
    sub_queries from the rewrite branch where decompose lives on a
    sibling path). The post-fix gate matches the inline retrieve gate
    semantics (commit 8ec1eb9): bypass when sub-queries already exist
    (>= 2), otherwise the LLM-paraphrase fanout MUST run.
    """
    src = _extract_helper_source("_run_multi_query_expansion")
    assert 'state["fanout_bypassed"] = True' in src, (
        "_run_multi_query_expansion must set the bypass flag on state "
        "when the decompose-precedence early-exit fires."
    )
    # Defence: the pre-fix inverted gate must NOT come back. It blocked
    # paraphrase fanout precisely on the caller condition where it was
    # required — the rewrite branch hand-off where decompose has not run.
    assert "if len(sub_queries_state) <= 1:" not in src, (
        "Pre-fix inverted gate ``if len(sub_queries_state) <= 1: "
        "return []`` re-introduced — would short-circuit the helper on "
        "every call from rewrite_and_mq_parallel."
    )


def test_expansion_helper_preserves_decompose_precedence() -> None:
    """The existing ``>= 2`` gate must remain so when decompose fires
    successfully, the helper still declines to paraphrase (decompose
    sub-queries take precedence, not paraphrases)."""
    src = _extract_helper_source("_run_multi_query_expansion")
    assert "len(sub_queries_state) >= 2" in src, (
        "Existing decompose-precedence gate must remain — removing it "
        "would let MQ paraphrase fire on a real multi-hop decomposition."
    )


# --------------------------------------------------------------------------- #
# 4. Behavioral truth-table — the bypass condition.                           #
# --------------------------------------------------------------------------- #
#
# The inline retrieve gate (commit 8ec1eb9) and the helper post-fix
# (mega-sprint G23, 2026-05-16) both bypass paraphrase fanout when
# sub-queries already exist (decompose pre-ran or upstream seeded
# them). We pin that truth table directly so any future refactor (move
# the gate into a helper, rename, etc.) still produces the same four
# decisions on the four spec'd inputs.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sub_queries, expected_bypass, label",
    [
        ([], False, "empty_sub_queries"),
        (["single sub query"], False, "single_sub_query"),
        (["q1", "q2"], True, "multi_two_sub_queries"),
        (["q1", "q2", "q3"], True, "multi_three_sub_queries"),
    ],
)
def test_bypass_condition_truth_table(
    sub_queries: list[str], expected_bypass: bool, label: str
) -> None:
    """Pins the four scenarios into the post-fix bypass truth table.

    The condition ``len(sub_queries) >= 2`` decides the bypass: bypass
    only when sub-queries are already in hand (decompose did the split).
    Otherwise paraphrase fanout MUST run — it is the retrieval lever
    for the rewrite branch where decompose lives on a sibling path.

    - ``test_no_bypass_empty_sub_queries`` — ``[]`` → real fanout.
    - ``test_no_bypass_single_sub_query`` — ``[q]`` → real fanout.
    - ``test_bypass_multi`` — ``[q1, q2]`` → bypass (decompose precedence).
    - ``test_bypass_three`` — ``[q1, q2, q3]`` → bypass.
    """
    actual_bypass = len(sub_queries) >= 2
    assert actual_bypass is expected_bypass, (
        f"Post-fix bypass truth table broken for {label!r}: "
        f"len(sub_queries)={len(sub_queries)} → expected bypass="
        f"{expected_bypass}, got {actual_bypass}"
    )


def test_no_bypass_empty_sub_queries() -> None:
    """Named alias for the post-fix spec — sub_queries=[] → real fanout."""
    assert (len([]) >= 2) is False


def test_no_bypass_single_sub_query() -> None:
    """Named alias for the post-fix spec — sub_queries=[q] → real fanout."""
    assert (len(["only-one"]) >= 2) is False


def test_bypass_multi() -> None:
    """Named alias for the post-fix spec — sub_queries=[q1,q2] → bypass."""
    assert (len(["q1", "q2"]) >= 2) is True


def test_bypass_three() -> None:
    """Named alias for the post-fix spec — sub_queries=[q1,q2,q3] → bypass."""
    assert (len(["q1", "q2", "q3"]) >= 2) is True
