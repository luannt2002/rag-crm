"""1.3a — the config-completeness gate must actually run in CI.

``scripts/check_config_completeness.py`` exists and ``README_DEVOPS.md`` §1
promises it as a "required CI step, red = no build" — but no workflow invoked
it (grep .github = 0). A gate nobody runs guards nothing: a new contract key
added without a seed silently falls back to its code constant forever.

This pins that at least one workflow invokes the gate script.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_GATE = "check_config_completeness"


def test_config_completeness_gate_is_wired_in_ci() -> None:
    assert _WORKFLOWS.is_dir(), f"missing workflows dir: {_WORKFLOWS}"
    invoking = [
        f.name
        for f in sorted(_WORKFLOWS.glob("*.yml"))
        if _GATE in f.read_text(encoding="utf-8")
    ]
    assert invoking, (
        "no CI workflow runs scripts/check_config_completeness.py — "
        "README_DEVOPS.md §1 promises a required gate but nothing invokes it"
    )
