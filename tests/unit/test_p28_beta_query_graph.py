"""P28-β — query_graph: CRAG defaults wired + dead state reads removed.

Covers Block β cleanup done in `feat(P28-β)`:
  * CRAG call-sites (`_pcfg(state, "crag_*", …)` + `max_total_graph_iterations`)
    now take their defaults from `shared.constants.DEFAULT_CRAG_*`, not
    inline literals.
  * `_is_retrieval_adequate` pure helper encapsulates the
    min-count + min-fraction gate that used to be inline.
  * Dead state reads for `bot_version` / `corpus_version` /
    `embedding_model_version` removed from the source (post-migration 0011
    these are always ``"latest"`` / ``"v1"``).
  * `"realtime"` removed from `_VALID_INTENTS` — no router arm existed.
  * `embedding_model_mismatch_total` import hoisted to module level
    (prior audit F5), no longer re-imported on every `_embed_query` call.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from ragbot.orchestration import query_graph as qg
from ragbot.shared.constants import (
    DEFAULT_CRAG_FALLBACK_COUNT,
    DEFAULT_CRAG_MAX_GRADE_RETRIES,
    DEFAULT_CRAG_MIN_FALLBACK_SCORE,
    DEFAULT_CRAG_MIN_RELEVANT_COUNT,
    DEFAULT_CRAG_MIN_RELEVANT_FRACTION,
    DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS,
)

_SOURCE_PATH = Path(qg.__file__)
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8") + "".join(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py") and [p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py"))])


def test_crag_defaults_imported_and_used() -> None:
    """All 6 DEFAULT_* names appear in query_graph.py source."""
    for name in (
        "DEFAULT_CRAG_FALLBACK_COUNT",
        "DEFAULT_CRAG_MAX_GRADE_RETRIES",
        "DEFAULT_CRAG_MIN_FALLBACK_SCORE",
        "DEFAULT_CRAG_MIN_RELEVANT_COUNT",
        "DEFAULT_CRAG_MIN_RELEVANT_FRACTION",
        "DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS",
    ):
        assert name in _SOURCE, f"{name} not wired into query_graph.py"

    # And the inline literals that used to default these knobs are gone.
    assert '"crag_min_fallback_score", 0.3' not in _SOURCE
    assert '"crag_fallback_count", 2' not in _SOURCE
    assert '"max_grade_retries", 1' not in _SOURCE
    assert '"max_total_graph_iterations", 8' not in _SOURCE


def test_is_retrieval_adequate_count_gate() -> None:
    """count gate: relevant_count must reach min_relevant_count."""
    base = {
        qg.CRAG_GRADE_RELEVANT: 0,
        qg.CRAG_GRADE_IRRELEVANT: 3,
        qg.CRAG_GRADE_AMBIGUOUS: 0,
    }
    # 0 relevant, min=1 → False
    assert qg._is_retrieval_adequate(base, min_relevant_count=1) is False
    # 1 relevant, min=2 → False
    one = {**base, qg.CRAG_GRADE_RELEVANT: 1, qg.CRAG_GRADE_IRRELEVANT: 2}
    assert qg._is_retrieval_adequate(one, min_relevant_count=2) is False
    # 2 relevant, min=1 → True
    two = {**base, qg.CRAG_GRADE_RELEVANT: 2, qg.CRAG_GRADE_IRRELEVANT: 1}
    assert qg._is_retrieval_adequate(two, min_relevant_count=1) is True


def test_is_retrieval_adequate_fraction_gate() -> None:
    """fraction gate: relevant / total must reach min_relevant_fraction."""
    # 1 relevant of 10 → 0.1; fraction 0.5 → False
    counts = {
        qg.CRAG_GRADE_RELEVANT: 1,
        qg.CRAG_GRADE_IRRELEVANT: 9,
        qg.CRAG_GRADE_AMBIGUOUS: 0,
    }
    assert qg._is_retrieval_adequate(
        counts, min_relevant_count=1, min_relevant_fraction=0.5,
    ) is False
    # 5 of 10 → 0.5; threshold 0.5 → True
    counts2 = {
        qg.CRAG_GRADE_RELEVANT: 5,
        qg.CRAG_GRADE_IRRELEVANT: 5,
        qg.CRAG_GRADE_AMBIGUOUS: 0,
    }
    assert qg._is_retrieval_adequate(
        counts2, min_relevant_count=1, min_relevant_fraction=0.5,
    ) is True
    # All-zero dict must not divide by zero — treat total as 1.
    empty: dict[str, int] = {
        qg.CRAG_GRADE_RELEVANT: 0,
        qg.CRAG_GRADE_IRRELEVANT: 0,
        qg.CRAG_GRADE_AMBIGUOUS: 0,
    }
    assert qg._is_retrieval_adequate(empty, min_relevant_count=1) is False


def test_is_retrieval_adequate_defaults_match_constants() -> None:
    """Default kwargs on helper = canonical constants."""
    sig = inspect.signature(qg._is_retrieval_adequate)
    assert sig.parameters["min_relevant_count"].default == DEFAULT_CRAG_MIN_RELEVANT_COUNT
    assert sig.parameters["min_relevant_fraction"].default == DEFAULT_CRAG_MIN_RELEVANT_FRACTION


def test_dead_state_reads_removed() -> None:
    """No more state.get() for bot_version / corpus_version / embedding_model_version."""
    assert 'state.get("bot_version"' not in _SOURCE
    assert 'state.get("corpus_version"' not in _SOURCE
    assert 'state.get("embedding_model_version"' not in _SOURCE


def test_state_graph_rag_mode_field_removed() -> None:
    """state.py top-level graph_rag_mode TypedDict field gone (pipeline_config only)."""
    from ragbot.orchestration import state as state_mod

    src = Path(state_mod.__file__).read_text(encoding="utf-8")
    assert "graph_rag_mode: str" not in src


def test_realtime_removed_from_valid_intents() -> None:
    """_VALID_INTENTS no longer advertises an intent with no router arm.

    Updated (B1-Q3 fix): _VALID_INTENTS is now a list (ordered) for
    deterministic fallback text-scan. The test checks membership + ordering
    constraints instead of exact-set equality.
    """
    assert "realtime" not in qg._VALID_INTENTS
    # Must be a list for deterministic iteration (hash-randomization-safe).
    assert isinstance(qg._VALID_INTENTS, list), "_VALID_INTENTS must be a list"
    # Required intents must all be present.
    required = {"factoid", "multi_hop", "aggregation", "out_of_scope", "greeting"}
    assert required.issubset(set(qg._VALID_INTENTS)), (
        f"Required intents missing from _VALID_INTENTS: {required - set(qg._VALID_INTENTS)}"
    )
    # 'factoid' must be first — ensures fallback text-scan prefers retrieval over OOS.
    assert qg._VALID_INTENTS[0] == "factoid", (
        f"'factoid' must be first in _VALID_INTENTS, got '{qg._VALID_INTENTS[0]}'"
    )


def test_embed_mismatch_import_hoisted_to_module_level() -> None:
    """F5: metrics counter imported once at module scope, not per-call.

    Uses the module-level ``_SOURCE`` snapshot (read once at test module import)
    instead of ``inspect.getsource`` to avoid a race when concurrent edits to
    ``query_graph.py`` invalidate the bytecode line numbers vs current file
    content. ``_SOURCE`` is a stable string captured at the start of this run.
    """
    # Module exposes the symbol (may be None in test env, that's fine).
    assert hasattr(qg, "embedding_model_mismatch_total")

    # Locate the function body in the captured _SOURCE snapshot.
    marker = "def _check_embed_model_consistency("
    start = _SOURCE.find(marker)
    assert start != -1, "_check_embed_model_consistency not found in source snapshot"
    # Body extends until the next top-level "def " or "class " at column 0.
    rest = _SOURCE[start + len(marker):]
    next_top = len(rest)
    for token in ("\ndef ", "\nclass "):
        idx = rest.find(token)
        if idx != -1 and idx < next_top:
            next_top = idx
    body = rest[:next_top]

    # Inner function no longer re-imports it.
    assert "from ragbot.infrastructure.observability.metrics" not in body
    # And guards the module-level symbol properly.
    assert "embedding_model_mismatch_total is not None" in body


def test_max_total_graph_iterations_constant_used() -> None:
    """Both call-sites (grade node + reflect router) use the constant."""
    occurrences = _SOURCE.count(
        '_pcfg(state, "max_total_graph_iterations", DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS)'
    )
    assert occurrences >= 2, f"expected >=2 uses, got {occurrences}"


def test_max_grade_retries_constant_used_both_sites() -> None:
    """Grade node + grade_route use DEFAULT_CRAG_MAX_GRADE_RETRIES."""
    occurrences = _SOURCE.count(
        '_pcfg(state, "max_grade_retries", DEFAULT_CRAG_MAX_GRADE_RETRIES)'
    )
    assert occurrences >= 2, f"expected >=2 uses, got {occurrences}"
    # Sanity: default numeric value still 1.
    assert DEFAULT_CRAG_MAX_GRADE_RETRIES == 1
    assert DEFAULT_CRAG_FALLBACK_COUNT == 2
    assert DEFAULT_CRAG_MIN_FALLBACK_SCORE == 0.3
