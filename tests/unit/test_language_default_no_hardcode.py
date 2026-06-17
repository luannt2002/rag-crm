"""Pin test: no ``"vi"`` literal as default-value in orchestration/application code.

CLAUDE.md sacred domain-neutral rule: code MUST NOT default-pin any tenant to
Vietnamese. Multi-tenant bots can be EN / KM / any future language — the
fallback must resolve from ``DEFAULT_LANGUAGE`` (single source of truth in
``shared/constants.py``), never a string literal.

Permitted ``"vi"`` occurrences:
- docstring examples (``e.g. "vi", "en"``)
- ``dict`` keys / lookup tables (data structure, not a default)
- comment-only mentions
- tests / archives (we're testing the rule, not breaking it)

Forbidden:
- function param ``language: str = "vi"`` — must be ``= DEFAULT_LANGUAGE``
- inline ``state.get("language", "vi")`` — must be ``state.get("language", DEFAULT_LANGUAGE)``
- module-level ``LANG = "vi"``

The regex catches the assignment / default-value patterns and ignores the
permitted forms by line context.
"""

from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCAN_DIRS = ("src/ragbot/orchestration", "src/ragbot/application")

# Match: `something = "vi"` or `, "vi")` (Python default-value / inline default).
# Reject: docstring/comment-only lines (handled by skipping ``#`` and ``"""``
# in the per-line filter loop).
_DEFAULT_LITERAL = re.compile(r'(?:=|,)\s*"vi"(?=[,)\s])')


def test_no_vi_default_literal_in_orchestration_application() -> None:
    violations: list[str] = []
    for d in _SCAN_DIRS:
        base = _REPO_ROOT / d
        for path in base.rglob("*.py"):
            if "_archive" in path.parts or "test_" in path.name:
                continue
            with path.open("r", encoding="utf-8") as fh:
                in_docstring = False
                for lineno, raw in enumerate(fh, start=1):
                    stripped = raw.strip()
                    # Crude docstring tracking — toggles on triple-quote.
                    if stripped.count('"""') == 1:
                        in_docstring = not in_docstring
                        continue
                    if in_docstring:
                        continue
                    if stripped.startswith("#"):
                        continue
                    # Skip dict-key entries (key: ``"vi": ...``)
                    if re.match(r'\s*"vi"\s*:\s*', raw):
                        continue
                    if _DEFAULT_LITERAL.search(raw):
                        violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {stripped}")
    assert not violations, (
        "Found ``\"vi\"`` literal as default value in orchestration/application "
        "code. Use ``DEFAULT_LANGUAGE`` from ``shared/constants.py`` instead — "
        "the literal pins multi-tenant bots to Vietnamese and violates the "
        "CLAUDE.md domain-neutral rule.\nViolations:\n  "
        + "\n  ".join(violations)
    )
