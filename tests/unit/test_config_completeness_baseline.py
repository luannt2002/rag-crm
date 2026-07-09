"""Regression guard for the config-completeness gate (DB-free).

The full gate (``scripts/check_config_completeness.py``) needs a seeded DB and
runs in CI after ``alembic upgrade head``. This unit test guards the two things
that can be checked from source alone, so drift is caught in the fast suite too:

1. **No stale baseline entry** — every key in
   ``config_constant_fallback_baseline.txt`` must still appear in the
   ``_PIPELINE_CFG_KEYS`` contract tuple. A stale entry means the key was seeded
   or removed from the contract but the baseline was never trimmed → the gate
   would keep silencing a key that no longer needs silencing.

2. **Decreasing-only** — the baseline count must never exceed its pinned
   ceiling. New contract keys must be SEEDED, not added to the baseline to
   quiet the gate. When the DATABASE team seeds a key and regenerates the
   baseline (``--write-baseline``), lower ``_BASELINE_MAX`` to lock in the win.

Mirrors ``tests/unit/test_narrow_exception_hierarchy.py::test_broad_except_count_decreases``.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_CFG = (
    _REPO_ROOT
    / "src" / "ragbot" / "interfaces" / "http" / "routes" / "test_chat"
    / "_pipeline_config.py"
)
_BASELINE = _REPO_ROOT / "scripts" / "config_constant_fallback_baseline.txt"

# Pinned ceiling — the number of contract keys currently resolving from a code
# constant (measured 2026-07-08). This MUST only ever be lowered. Lower it each
# time the DATABASE team seeds a key and regenerates the baseline.
_BASELINE_MAX = 71


def _contract_keys() -> set[str]:
    src = _PIPELINE_CFG.read_text(encoding="utf-8")
    m = re.search(r"^_PIPELINE_CFG_KEYS\s*:\s*tuple\b.*?=\s*\(\s*$", src, re.MULTILINE)
    assert m, "could not locate _PIPELINE_CFG_KEYS tuple"
    rest = src[m.end():]
    close = re.search(r"^\)\s*$", rest, re.MULTILINE)
    assert close, "could not find close of _PIPELINE_CFG_KEYS tuple"
    body = rest[: close.start()]
    # Strip comments so commented-out (disabled) keys are not counted as contract.
    body = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    return set(re.findall(r'["\']([a-zA-Z_][\w.]*)["\']', body))


def _baseline_keys() -> set[str]:
    return {
        line.strip()
        for line in _BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_no_stale_baseline_entry() -> None:
    """Every baselined key must still be a real contract key."""
    stale = _baseline_keys() - _contract_keys()
    assert not stale, (
        "config_constant_fallback_baseline.txt lists keys no longer in "
        "_PIPELINE_CFG_KEYS — trim them (they were seeded or removed from the "
        f"contract):\n  {sorted(stale)!r}"
    )


def test_baseline_is_decreasing_only() -> None:
    """The constant-fallback backlog must never grow. Seed keys, don't baseline them."""
    count = len(_baseline_keys())
    assert count <= _BASELINE_MAX, (
        f"config-constant-fallback baseline grew to {count} (ceiling {_BASELINE_MAX}). "
        "A new contract key was added to the baseline instead of being seeded in "
        "system_config. Seed it (README_DATABASE.md) — do not silence the gate."
    )
