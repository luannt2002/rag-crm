"""format→markdown normalizer (Phase C) — parse-better, config-gated.

Turns the parser's joined output into clean markdown BEFORE chunking:

  * raw CSV regions → markdown pipe tables (header↔column association kept,
    so the embedder sees a coherent table instead of comma-soup rows), and
  * plain-text VN ``Chương / Mục / Điều`` markers → ATX headings.

Everything else passes through byte-identical. The transform is idempotent
(a pipe table has no commas, so it is never re-detected as CSV) and is gated
OFF by default (``system_config.markdown_normalize_enabled``) — flip only
after re-ingest validation.

Reuses the CSV region detector + heading promoter from
:mod:`ragbot.shared.chunking` so detection logic cannot drift.
"""

from __future__ import annotations

from ragbot.shared.chunking import (
    _detect_csv_regions_all,
    promote_vn_hierarchical_headings,
)


def _csv_region_to_markdown(header: str, rows: list[str]) -> str:
    """Render one CSV region (header line + data rows) as a markdown table."""
    cols = [c.strip() for c in header.split(",")]
    n = len(cols)
    out = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["---"] * n) + "|",
    ]
    for row in rows:
        cells = [c.strip() for c in row.split(",")]
        # Pad/truncate ragged rows to the header width so the pipe table
        # stays rectangular (markdown renderers require it).
        cells = (cells + [""] * n)[:n]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def normalize_to_markdown(text: str) -> str:
    """Normalise ``text`` to clean markdown (CSV→pipe table, VN markers→ATX).

    Non-table / non-legal text is returned unchanged. Idempotent.
    """
    if not text or not text.strip():
        return text

    # Promote VN admin/legal hierarchy first (no-op without ≥3 markers).
    text = promote_vn_hierarchical_headings(text)

    lines = text.split("\n")
    regions = _detect_csv_regions_all(lines)
    if not regions:
        return text

    region_by_start = {r.header_idx: r for r in regions}
    out_lines: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        region = region_by_start.get(i)
        if region is not None:
            header = lines[region.header_idx]
            rows = lines[region.header_idx + 1 : region.last_data_idx + 1]
            out_lines.append(_csv_region_to_markdown(header, rows))
            i = region.last_data_idx + 1
        else:
            out_lines.append(lines[i])
            i += 1
    return "\n".join(out_lines)


__all__ = ["normalize_to_markdown"]
