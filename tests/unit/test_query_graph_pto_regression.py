"""Regression guard — generate node must define _pto_* before use.

Bug 2026-05-14: wave-J reorg merge (`d3fb2cd`, `-X theirs`) dropped the
35-line initialization block introduced by Phase B B2 (`b8557ef`), but
left the downstream references in place at lines 3825/3852-3855. Result:
every chat request hit `NameError: name '_pto_skip_history' is not
defined` inside the `generate` step and bot answered with empty text and
0 tokens despite retrieving 7 chunks.

This test guards against the same regression by asserting that any line
referencing `_pto_skip_history` / `_pto_enabled` / `_pto_metrics` in the
generate-node region has at least one matching assignment earlier in the
same function. Pure static check — no graph invocation required.
"""

from __future__ import annotations

from pathlib import Path

QUERY_GRAPH_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "ragbot"
    / "orchestration"
    / "query_graph.py"
)

GUARDED_NAMES = (
    "_pto_enabled",
    "_pto_skip_history",
    "_pto_metrics",
)


def test_pto_variables_are_assigned_before_use():
    """Every `_pto_*` reference must be preceded by an assignment.

    A line is an assignment when the variable appears on the LHS of `=`
    or inside an unpack target `_pto_enabled, ... =`. Anything else is a
    use site.
    """
    src = QUERY_GRAPH_PATH.read_text(encoding="utf-8") + "".join(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py") and [p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py"))])
    lines = src.splitlines()

    for name in GUARDED_NAMES:
        first_assign_line = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            # LHS assignment: "_pto_x = ..." or "..., _pto_x, ... = ..."
            if (
                stripped.startswith(f"{name} =")
                or stripped.startswith(f"{name}: ")
                or f", {name}" in stripped.split("=")[0]
                or f"{name}," in stripped.split("=")[0]
            ):
                first_assign_line = idx
                break

        assert first_assign_line is not None, (
            f"{name} is referenced in query_graph.py but never assigned. "
            "Regression of 2026-05-14 bug — wave-J merge dropped the B2 "
            "init block. Re-add the `apply_token_opt(...)` call inside "
            "the prompt_build step before any `_pto_*` reference."
        )

        # Find first USE site that is NOT the assignment line itself.
        for idx, line in enumerate(lines):
            if idx == first_assign_line:
                continue
            if name in line and not line.strip().startswith("#"):
                # Must come AFTER the assignment.
                assert idx > first_assign_line, (
                    f"{name} is used at line {idx + 1} before its first "
                    f"assignment at line {first_assign_line + 1}. "
                    "Reorder the generate-node block so the init runs first."
                )
                break


def test_apply_token_opt_import_present():
    """The init block depends on `apply_token_opt` being imported."""
    src = QUERY_GRAPH_PATH.read_text(encoding="utf-8") + "".join(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py") and [p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py"))])
    assert "from ragbot.shared.prompt_token_opt import apply_token_opt" in src, (
        "apply_token_opt import was removed but the generate-node still "
        "calls it. Either restore the import or remove the call site."
    )


def test_default_prompt_token_opt_constants_imported():
    """Init block reads 4 DEFAULT_PROMPT_TOKEN_OPT_* constants."""
    src = QUERY_GRAPH_PATH.read_text(encoding="utf-8") + "".join(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py") and [p.read_text(encoding="utf-8") for p in sorted(__import__("pathlib").Path(__file__).resolve().parents[2].joinpath("src","ragbot","orchestration","nodes").glob("*.py"))])
    required = (
        "DEFAULT_PROMPT_TOKEN_OPT_ENABLED",
        "DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE",
        "DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD",
        "DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY",
    )
    for name in required:
        assert name in src, (
            f"{name} is referenced by the prompt-token-opt block but not "
            "imported from ragbot.shared.constants."
        )
