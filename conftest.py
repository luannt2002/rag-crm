"""Repo-root conftest — pin ``ragbot`` imports to this worktree's ``src/``.

The project venv ships an editable install of ``ragbot``. In a multi-
worktree MoM setup that install can point at *another* worktree (last
one to ``pip install -e .``), which silently masks the source under
test. We sidestep that by prepending this worktree's ``src/`` to
``sys.path`` *before* ``tests/conftest.py`` triggers any ``from
ragbot...`` import, and by evicting any stale ``ragbot`` module already
cached against a foreign path.

In the main repo (non-worktree) the prepend resolves to the same path
the editable install already exposes, so the shim is a no-op. The
module eviction loop only fires when a stale path has actually leaked
in — keeping startup cost negligible.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WORKTREE_SRC = Path(__file__).resolve().parent / "src"
_WORKTREE_SRC_STR = str(_WORKTREE_SRC)

if _WORKTREE_SRC.is_dir():
    # Idempotent prepend — first position wins under Python's import lookup.
    if _WORKTREE_SRC_STR in sys.path:
        sys.path.remove(_WORKTREE_SRC_STR)
    sys.path.insert(0, _WORKTREE_SRC_STR)

    # Evict any pre-cached ragbot modules that resolved against a foreign
    # path (e.g. a sibling worktree's editable install). Fresh imports
    # after this point will pick up the worktree source.
    _stale = [
        name
        for name, mod in list(sys.modules.items())
        if (name == "ragbot" or name.startswith("ragbot."))
        and not (getattr(mod, "__file__", "") or "").startswith(_WORKTREE_SRC_STR)
    ]
    for _name in _stale:
        del sys.modules[_name]
