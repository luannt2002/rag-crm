"""[S1 STRUCTURAL GUARD] AST pin-test: every key a node RETURNS must be declared
in GraphState.

Root cause (audit 2026-07-03): langgraph 1.2.4's reducer DROPS any state key not
declared in the GraphState TypedDict — from node return dicts and in-place writes.
A returned-but-undeclared key silently vanishes at the node boundary, killing the
feature that reads it (paid output tokens, rerank-score-mode floor, loop cap, ...).
This has recurred 3× (M17, _mq_speculative_variants, and the 2026-07-03 batch)
because nothing enforced the schema-vs-usage invariant. This pin does.

It walks orchestration node modules for ``return {"key": ...}`` dict literals inside
node functions and asserts every literal key is in GraphState.__annotations__ (plus
a small allowlist of genuinely within-node / DB-backed / terminal scratch keys that
never need to cross a boundary). A NEW undeclared cross-node key fails collection.
"""
from __future__ import annotations

import ast
import pathlib

from ragbot.orchestration.state import GraphState

# Keys that are legitimately NOT GraphState channels (verified in the audit):
#  - DB-backed (survives via its own store, not the reducer)
#  - terminal-node returns (persist → END, never re-fed)
#  - within-node scratch consumed before any boundary
_ALLOWED_NON_STATE_KEYS: frozenset[str] = frozenset({
    "action_state",       # DB-backed: conversations.action_state JSONB (reload, not reducer)
    "_persist_meta",      # persist is terminal (→END); return never re-fed
    "_generate_empty_answer",  # within-node scratch
    # _do_stats_lookup(state, ...) is a HELPER whose result dict is consumed inline
    # by the retrieve node — not a LangGraph node update, so these are not channels:
    "entities", "linked_chunks", "range_filter",
})

_NODE_DIRS = [
    pathlib.Path("src/ragbot/orchestration/nodes"),
    pathlib.Path("src/ragbot/orchestration/query_graph.py"),
]


def _iter_py_files():
    for p in _NODE_DIRS:
        if p.is_dir():
            yield from p.rglob("*.py")
        elif p.is_file():
            yield p


def _is_node_fn(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A graph node's first positional param is ``state`` (the GraphState). Helper
    functions (``_so_usage(ctx)``, ``_do_stats_lookup(...)``) are excluded — their
    return dicts are NOT node state and must not be pinned."""
    args = fn.args.posonlyargs + fn.args.args
    return bool(args) and args[0].arg == "state"


def _returned_keys(tree: ast.AST) -> set[str]:
    """Collect string keys from ``return {"k": ...}`` dict literals that are the
    RETURN of a node function (first param ``state``)."""
    keys: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _is_node_fn(fn):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    return keys


def _inplace_written_keys(tree: ast.AST) -> set[str]:
    """Collect string keys from ``state["k"] = ...`` in-place writes ANYWHERE in
    the module (closures included). langgraph 1.2.4 drops an undeclared in-place
    write exactly like an undeclared return dict key — the return-only pin above
    is blind to these (UNCTRL-A). Any Subscript-assignment whose object is a
    Name ``state`` and whose slice is a string constant is a channel write."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (
                isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name)
                and tgt.value.id == "state"
                and isinstance(tgt.slice, ast.Constant)
                and isinstance(tgt.slice.value, str)
            ):
                keys.add(tgt.slice.value)
    return keys


def test_returned_state_keys_are_declared_in_graphstate() -> None:
    declared = set(GraphState.__annotations__) | _ALLOWED_NON_STATE_KEYS
    offenders: dict[str, set[str]] = {}
    for f in _iter_py_files():
        tree = ast.parse(f.read_text(), filename=str(f))
        used = _returned_keys(tree)
        bad = {k for k in used if not k.startswith("__") and k not in declared}
        if bad:
            offenders[str(f)] = bad
    assert not offenders, (
        "Node functions RETURN state keys not declared in GraphState — langgraph "
        "drops them at the reducer, silently killing the reader. Declare them in "
        "state.py (or add to the allowlist if genuinely non-channel):\n"
        + "\n".join(f"  {f}: {sorted(ks)}" for f, ks in sorted(offenders.items()))
    )


def test_inplace_written_state_keys_are_declared_in_graphstate() -> None:
    """UNCTRL-A: an in-place ``state[k] = …`` write with an undeclared ``k`` is
    dropped by the reducer just like an undeclared return — the return-only pin
    misses it. This closes that coverage hole."""
    declared = set(GraphState.__annotations__) | _ALLOWED_NON_STATE_KEYS
    offenders: dict[str, set[str]] = {}
    for f in _iter_py_files():
        tree = ast.parse(f.read_text(), filename=str(f))
        used = _inplace_written_keys(tree)
        bad = {k for k in used if not k.startswith("__") and k not in declared}
        if bad:
            offenders[str(f)] = bad
    assert not offenders, (
        "Node code writes ``state[k]=`` for keys not declared in GraphState — "
        "langgraph drops them at the reducer, silently killing any later reader. "
        "Declare them in state.py (or add to the allowlist if genuinely "
        "non-channel):\n"
        + "\n".join(f"  {f}: {sorted(ks)}" for f, ks in sorted(offenders.items()))
    )
