"""Regression guards for the F4-SOLID-CLEANUP fix-set landed off
``reports/MEGA_QUERY_GRAPH_AUDIT_20260430.md`` §7.

Three small refactors, three pinning tests:

1. ``test_no_duplicate_min_relevant_threshold`` — the merge-accident dup
   block at ~line 2237 is gone (audit §3 row #1, HIGH).
2. ``test_invoke_llm_records_each_attempt`` — the four caller-side
   ``ctx.record(...)`` echoes that followed ``_invoke_llm_node(...)`` in
   ``condense_question / router / rewrite / reflect`` are gone
   (audit §3 row #2, MED). Also pins the "single record per LLM call"
   contract that justified deleting them: the inner helper is the only
   recorder.
3. ``test_pcfg_constants_lifted`` — every default we lifted is now a
   live ``Final[...]`` symbol on ``shared.constants`` and the only
   inline numeric literal still wired into a ``_pcfg(state, key, ...)``
   call inside ``orchestration.query_graph`` is the explicitly-allowed
   ``autocut_min_gap_ratio`` site (out-of-scope per F4 brief).

The tests intentionally read ``query_graph.py`` source rather than mock
the runtime — these are anti-regression pins, not behaviour checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ORCH_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ragbot"
    / "orchestration"
)
QUERY_GRAPH_PATH = _ORCH_DIR / "query_graph.py"


@pytest.fixture(scope="module")
def query_graph_source() -> str:
    # Several node bodies (grade / reflect / …) were lifted out of
    # ``build_graph`` into ``orchestration/nodes/*.py`` (pure relocation).
    # Concatenate the orchestrator wiring file with every node module so the
    # anti-regression source pins keep matching after the structural carve.
    parts = [QUERY_GRAPH_PATH.read_text(encoding="utf-8")]
    parts.extend(
        p.read_text(encoding="utf-8")
        for p in sorted((_ORCH_DIR / "nodes").glob("*.py"))
    )
    return "\n".join(parts)


# --------------------------------------------------------------------- #
# Fix #1 — duplicate ``min_relevant`` block deleted
# --------------------------------------------------------------------- #


def test_no_duplicate_min_relevant_threshold(query_graph_source: str) -> None:
    """The ``crag_min_relevant_count`` _pcfg lookup must appear exactly once
    inside the ``grade`` node body. Audit §3 row #1 caught a merge-accident
    duplicate at lines 2237-2240; this regression guard fails fast if a
    future merge re-introduces the dup."""
    pattern = re.compile(
        r'_pcfg\(state,\s*"crag_min_relevant_count",\s*'
        r"DEFAULT_CRAG_MIN_RELEVANT_COUNT\)"
    )
    hits = pattern.findall(query_graph_source)
    # Exactly one site inside the grade node. (The function-signature default
    # at module top — ``min_relevant_count: int = DEFAULT_...`` — is not
    # written through ``_pcfg`` so it does not match this regex.)
    assert len(hits) == 1, (
        f"Expected exactly 1 _pcfg(crag_min_relevant_count, ...) site, "
        f"got {len(hits)}. Did a merge re-introduce the dup at "
        f"~line 2237?"
    )


# --------------------------------------------------------------------- #
# Fix #2 — dead caller-side ``ctx.record(...)`` echoes deleted
# --------------------------------------------------------------------- #


def test_no_dead_ctx_record_echoes_after_invoke_llm_node(
    query_graph_source: str,
) -> None:
    """``_invoke_llm_node`` records on every exit path (see comment at
    "Must record() before async-with exits; caller-side ctx.record() is
    dropped"). The audit confirmed four caller-side echoes in
    ``condense_question / router / rewrite / reflect`` were therefore
    pure clutter — this guard fails if any future change re-introduces a
    caller-side ``ctx.record(...)`` immediately after the
    ``payload, _ctx = await _invoke_llm_node(...)`` pattern."""
    # Grab the four nodes and ensure none of them contain a ``ctx.record(``
    # call after the ``_invoke_llm_node`` await. We do a coarse string
    # check rather than full AST: if any of the four node names is
    # followed within the next 2 KB by both ``_invoke_llm_node(`` and a
    # bare ``ctx.record(`` (not ``_ctx.record`` / ``ctx_xx.record``), fail.
    for node_name in ("condense_question", "router", "rewrite", "reflect"):
        marker = f"async def {node_name}("
        idx = query_graph_source.find(marker)
        assert idx != -1, f"Could not locate node {node_name!r}"
        # Window = node body up to the NEXT ``async def`` (any name) so we
        # never bleed into a sibling node's body — bleeding caused false
        # positives when the next node legitimately calls ctx.record() on
        # its own (e.g. router has multi-fan-out branches that record).
        # Bound the window at the NEXT ``async def`` at ANY indent. Nodes were
        # carved out of build_graph into module-level functions in
        # ``nodes/*.py`` (0-indent), so the old 4-space-only boundary would
        # over-run past the function into a sibling module's body (false
        # positive on that sibling's legitimate ctx.record()).
        _next_m = re.search(r"\n\s*async def ", query_graph_source[idx + len(marker):])
        next_def = idx + len(marker) + _next_m.start() if _next_m else idx + 4000
        window = query_graph_source[idx:next_def]
        if "_invoke_llm_node(" not in window:
            # decompose-style: node may use _invoke_structured_llm_node
            # only — out of scope for this regression.
            continue
        # Only flag echoes following ``_invoke_llm_node(`` (the helper that
        # records on every exit path per its in-source comment). The
        # structured-output sibling ``_invoke_structured_llm_node`` legit-
        # imately gets a caller-side ``ctx.record(...)`` so the audit row
        # captures the JSON-dumped parsed response (not the raw stream).
        # Walk each ``payload, _ctx = await _invoke_llm_node(...)`` site
        # and assert the next ~600 chars do not call ``ctx.record(\n``.
        for m in re.finditer(
            r"=\s*await\s+_invoke_llm_node\(", window,
        ):
            tail = window[m.end() : m.end() + 800]
            bad = re.findall(r"(?<![_a-zA-Z])ctx\.record\(\s*\n", tail)
            assert not bad, (
                f"Caller-side ctx.record(...) echo reappeared after "
                f"_invoke_llm_node(...) inside {node_name!r}. The helper "
                f"already records on every exit path — remove the echo "
                f"or update this guard."
            )


def test_invoke_llm_records_on_each_exit_path(query_graph_source: str) -> None:
    """Pin the contract that justified deleting the echoes: every exit
    path of ``_invoke_llm_node`` calls ``ctx.record(...)`` before the
    ``async with invocation_logger.invoke_model(...)`` block exits.

    We don't try to reason about control flow — instead we pin the count:
    the helper's body contains exactly two ``ctx.record(`` calls, one per
    exit path (streaming branch + non-streaming branch). If a future
    refactor adds a third exit path without a record, this guard fails
    and forces the author to re-justify deleting caller echoes.
    """
    # Slice the helper body.
    start = query_graph_source.index("async def _invoke_llm_node(")
    end = query_graph_source.index("async def _invoke_structured_llm_node(", start)
    body = query_graph_source[start:end]
    # ``ctx.record(\n`` (open-paren-newline) — real call sites only;
    # excludes the in-line comment ``caller-side ctx.record() is dropped``
    # which contains the literal but is not an actual invocation.
    record_calls = re.findall(r"\bctx\.record\(\s*\n", body)
    assert len(record_calls) == 2, (
        f"_invoke_llm_node must record on every exit path; expected "
        f"exactly 2 ctx.record(...) sites (stream + non-stream), got "
        f"{len(record_calls)}. If you added a third exit path, add a "
        f"matching ctx.record(...) — caller-side echoes are dropped."
    )


# --------------------------------------------------------------------- #
# Fix #3 — inline ``_pcfg(state, key, LITERAL)`` defaults lifted
# --------------------------------------------------------------------- #

# (constant_name, expected_truthy_value)
LIFTED_CONSTANTS = (
    "DEFAULT_BM25_NORMALIZATION_FLAGS",
    "DEFAULT_CACHE_SIMILARITY_THRESHOLD",
    "DEFAULT_CONDENSE_HISTORY_LIMIT",
    "DEFAULT_GROUNDING_CHECK_THRESHOLD",
    "DEFAULT_MAX_REFLECT_RETRIES",
    "DEFAULT_MMR_LAMBDA",
    "DEFAULT_MMR_SIMILARITY_THRESHOLD",
)


def test_pcfg_constants_lifted_present_and_nonzero() -> None:
    """Each lifted constant must be importable from ``shared.constants``
    and carry a truthy default. Catches the regression where a constant
    is referenced from ``query_graph.py`` but accidentally deleted from
    ``constants.py``."""
    import ragbot.shared.constants as C

    missing: list[str] = []
    zero_valued: list[str] = []
    for name in LIFTED_CONSTANTS:
        if not hasattr(C, name):
            missing.append(name)
            continue
        if not getattr(C, name):
            zero_valued.append(name)
    assert not missing, f"Missing lifted constants: {missing}"
    assert not zero_valued, f"Zero-valued lifted constants: {zero_valued}"


def test_query_graph_uses_lifted_constants(query_graph_source: str) -> None:
    """Each lifted constant must be referenced (imported + used) by
    ``query_graph.py``. Otherwise we have a dead constant and the lift
    didn't actually replace anything."""
    for name in LIFTED_CONSTANTS:
        assert name in query_graph_source, (
            f"{name} not referenced in query_graph.py — did the inline "
            f"literal swap silently revert?"
        )


def test_query_graph_no_inline_pcfg_numeric_literal_except_whitelisted(
    query_graph_source: str,
) -> None:
    """No ``_pcfg(state, "key", <numeric-literal>)`` call may remain
    inside ``query_graph.py`` except the explicitly-out-of-scope
    ``autocut_min_gap_ratio`` site (not in F4 brief's 8-site list — left
    for a future sweep). All other inline literals were the exact
    anti-pattern flagged in CLAUDE.md "Common violation patterns"."""
    pattern = re.compile(
        r'_pcfg\(\s*state\s*,\s*"([^"]+)"\s*,\s*'
        r"[0-9]+(?:\.[0-9]+)?\s*\)"
    )
    hits = pattern.findall(query_graph_source)
    # The autocut_min_gap_ratio default of 0.3 is intentionally left
    # inline — out of scope for F4, tracked for a future sweep.
    unexpected = [k for k in hits if k != "autocut_min_gap_ratio"]
    assert not unexpected, (
        f"Inline _pcfg numeric-literal defaults reappeared for keys: "
        f"{unexpected}. Lift to shared/constants.py per "
        f"CLAUDE.md zero-hardcode rule."
    )
