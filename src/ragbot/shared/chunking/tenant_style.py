"""P3 Tenant-Profiling — per-bot ingest STYLE normalizer.

Some bot owners author documents with a non-standard convention the global
rule-based block detector (:mod:`ragbot.shared.chunking.analyze`) does not
recognise — an ALL-CAPS line used as a section heading without a ``#`` marker,
or columns separated by ``;`` / ``~`` instead of a pipe. Rather than make every
global detection rule per-bot config-aware, this module PROMOTES the owner's
convention into the canonical markdown the global rules already understand
(``## `` headings, ``| a | b |`` pipe rows) at pre-process, so AdapChunk /
``analyze_document`` work unchanged ("normalize the dirty input to the poor
standard BEFORE chunking").

Domain-neutral and opt-in: both knobs default OFF, and the function is then a
byte-identity no-op — existing bots are unaffected until the owner enables a
knob in ``plan_limits.chunking_config.style_profile`` and re-ingests.
"""
from __future__ import annotations

import re

from ragbot.shared.chunking.analyze import _is_heading_line, _is_table_line
from ragbot.shared.constants import (
    DEFAULT_TOPIC_UPPER_SECTION_MAX_CHARS,
    DEFAULT_TOPIC_UPPER_SECTION_MIN_CHARS,
)

# A heading promoted from an uppercase line uses H2 — owner uppercase lines are
# section titles, not document titles (H1 is reserved for the doc/sheet name).
_PROMOTED_HEADING_PREFIX = "## "

# Owner separator must be a SINGLE punctuation char that is not already
# meaningful to the markdown the global rules emit (pipe/heading/bold/code/tab).
_FORBIDDEN_SEPARATORS = frozenset({"|", "#", "*", "`", "\t"})


def is_valid_table_separator(sep: str) -> bool:
    """A usable owner column separator: one non-alphanumeric, non-reserved char."""
    return (
        len(sep) == 1
        and not sep.isalnum()
        and not sep.isspace()
        and sep not in _FORBIDDEN_SEPARATORS
    )


def _promote_uppercase_heading(stripped: str) -> str | None:
    """Return the ``## ``-prefixed line if ``stripped`` is an uppercase section
    title, else None. Mirrors the UPPERCASE-section signal in
    ``analyze._count_topic_signals`` (isupper + length bounds)."""
    if not stripped.isupper():
        return None
    if not (
        DEFAULT_TOPIC_UPPER_SECTION_MIN_CHARS
        < len(stripped)
        < DEFAULT_TOPIC_UPPER_SECTION_MAX_CHARS
    ):
        return None
    return _PROMOTED_HEADING_PREFIX + stripped


def _to_pipe_row(stripped: str, sep: str) -> str | None:
    """Return a ``| a | b | c |`` pipe row if ``stripped`` is an owner-separated
    data row (>=2 separators = >=3 cells, not sentence-shaped), else None."""
    if stripped.count(sep) < 2:  # noqa: PLR2004 — >=2 separators = >=3 cells
        return None
    # Exclude sentence-shaped lines (same carve-out as analyze._is_table_line):
    # a genuine data row has no mid-sentence ". " and does not end in . ; :
    if ". " in stripped or stripped.endswith((".", ";", ":")):
        return None
    cells = [c.strip() for c in stripped.split(sep)]
    if any(not c for c in cells):
        return None  # an empty cell → malformed, leave the owner line untouched
    return "| " + " | ".join(cells) + " |"


def apply_tenant_style(
    text: str,
    *,
    heading_uppercase_promote: bool = False,
    table_separator: str = "",
) -> str:
    """Normalise an owner's non-standard styling into canonical markdown.

    @param text: the cleaned document text (one document).
    @param heading_uppercase_promote: when True, a standalone ALL-CAPS short
        line (within the uppercase-section length bounds, not already a heading
        or table row) is prefixed with ``## ``.
    @param table_separator: a single owner column-separator char; when valid,
        a row carrying >=2 of it (>=3 cells, not sentence-shaped) is rewritten
        as a pipe table row. Invalid / empty → ignored.
    @return: the normalised text; byte-identical to the input when both knobs
        are off / inert.
    """
    sep_active = bool(table_separator) and is_valid_table_separator(table_separator)
    if not heading_uppercase_promote and not sep_active:
        return text

    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Never touch a line the global rules already classify structurally.
        if not stripped or _is_heading_line(stripped) or _is_table_line(stripped):
            out.append(line)
            continue
        if sep_active:
            pipe = _to_pipe_row(stripped, table_separator)
            if pipe is not None:
                out.append(pipe)
                continue
        if heading_uppercase_promote:
            promoted = _promote_uppercase_heading(stripped)
            if promoted is not None:
                out.append(promoted)
                continue
        out.append(line)
    return "\n".join(out)


# Pre-compiled here for the (rare) caller that wants to count promotions for
# observability without re-running the normalizer.
_PROMOTED_HEADING_RE = re.compile(r"^" + re.escape(_PROMOTED_HEADING_PREFIX))


__all__ = ["apply_tenant_style", "is_valid_table_separator"]
