"""Bug 2 P0 — guard rails against naive ``datetime`` usage.

The codebase uses tz-aware UTC timestamps everywhere (see
``invocation_logger.py``, ``models_*.py`` ``DateTime(timezone=True)``).
A drive-by audit found one straggler in
``test_chat.py::generate_test_questions`` that did
``_dt.now().astimezone().isoformat()`` — naive ``now()`` localized to the
host's tz, so the recorded ``generated_at`` field would shift if the
server tz changed (e.g. container restart, DST cutover).

Tests in this file:

1. Sweep guard — assert the codebase contains zero naive
   ``datetime.now()`` / ``datetime.utcnow()`` / ``_dt.now()`` calls
   outside an ``# allow-naive-datetime`` opt-out comment.
2. Targeted — verify the specific ``test_chat.py`` callsite emits a
   tz-aware UTC ISO timestamp (ends with ``+00:00``).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest


_REPO_SRC = Path(__file__).resolve().parents[2] / "src" / "ragbot"


# Naive-datetime patterns we want to keep at zero. Any line matching one
# of these but NOT containing an explicit timezone arg is a violation.
_NAIVE_PATTERNS = [
    re.compile(r"datetime\.now\(\s*\)"),
    re.compile(r"datetime\.utcnow\(\s*\)"),
    # `_dt` is a common alias (`from datetime import datetime as _dt`).
    re.compile(r"\b_dt\.now\(\s*\)"),
]
# A line is OK if it carries any of these explicit-tz markers — the
# patterns above zero out empty-arg calls only, so this is a safety net.
_TZ_OK_RE = re.compile(r"tz\s*=|timezone\.utc|UTC\)|now\(timezone")


def test_no_naive_datetime_in_src() -> None:
    """Sweep ``src/ragbot/**.py`` for naive ``datetime.now()`` calls."""
    hits: list[str] = []
    for py in _REPO_SRC.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments don't count
            if "# allow-naive-datetime" in line:
                continue
            for pat in _NAIVE_PATTERNS:
                if pat.search(line) and not _TZ_OK_RE.search(line):
                    hits.append(f"{py}:{lineno}:{stripped}")
                    break

    assert not hits, (
        "Naive datetime usage detected — replace with "
        "datetime.now(tz=timezone.utc):\n" + "\n".join(hits)
    )


def test_test_chat_generated_at_uses_utc() -> None:
    """The fixed callsite in test_chat.py must produce a UTC tz-aware ISO."""
    test_chat = (
        _REPO_SRC / "interfaces" / "http" / "routes" / "test_chat"
        / "bot_insights_routes.py"
    )
    text = test_chat.read_text(encoding="utf-8")
    # Must contain the explicit-UTC form.
    assert (
        '_dt.now(tz=_tz.utc).isoformat()' in text
        or '_dt.now(tz=timezone.utc).isoformat()' in text
    ), "test_chat.py must use a tz-aware UTC datetime for generated_at"
    # Must NOT contain the old naive form.
    assert "_dt.now().astimezone()" not in text, (
        "Naive _dt.now().astimezone() form must be removed"
    )


def test_canonical_utc_now_format_is_iso8601_with_offset() -> None:
    """Sanity: ``datetime.now(tz=timezone.utc).isoformat()`` carries an offset."""
    iso = datetime.now(tz=timezone.utc).isoformat()
    # Either ``+00:00`` or ``+0000`` at the end depending on Python version.
    assert iso.endswith("+00:00"), (
        f"expected iso to end with +00:00, got {iso!r}"
    )


def test_no_backcompat_import_time_stub() -> None:
    """Bug 3 P1 — no ``__import__('time')`` survivors in src/."""
    hits: list[str] = []
    pat = re.compile(r"__import__\(\s*['\"]time['\"]\s*\)")
    for py in _REPO_SRC.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pat.search(line):
                hits.append(f"{py}:{lineno}:{line.strip()}")

    assert not hits, (
        "__import__('time') survivors detected — add 'import time' to the top "
        "of the file and use time.X directly:\n" + "\n".join(hits)
    )
