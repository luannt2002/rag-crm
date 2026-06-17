"""Markdown parser strategy — section-aware passthrough chunker.

Strips optional YAML front matter (``---\\n...\\n---\\n``) and splits the
body on top-level headings (lines starting with ``# `` or ``## ``) so each
returned chunk maps to one logical section. No new dep needed: stdlib only.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from ragbot.shared.constants import DEFAULT_MARKDOWN_MAX_BYTES

logger = structlog.get_logger(__name__)


_MD_MIMES: tuple[str, ...] = (
    "text/markdown",
    "text/x-markdown",
    # Plain text falls through to the markdown parser — there is no
    # dedicated TextParser and the markdown path (UTF-8 decode + section
    # split + sentence chunking) degrades correctly to a single section
    # when no ``#`` headings are present. This unblocks .txt corpora
    # (legal documents, transcripts) without a new strategy class.
    "text/plain",
)
_MD_EXTS: tuple[str, ...] = (".md", ".markdown", ".txt")


_FRONT_MATTER_RE: re.Pattern[str] = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n",
    flags=re.DOTALL,
)
_TOP_HEADING_RE: re.Pattern[str] = re.compile(
    r"(?m)^(#{1,2}) +(.+?)\s*$",
)


def _strip_front_matter(text: str) -> str:
    return _FRONT_MATTER_RE.sub("", text, count=1).lstrip()


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading_or_empty, body) sections.

    Pre-heading content is returned with an empty heading. Each H1/H2 line
    starts a new section that runs until the next H1/H2 (or EOF).
    """
    matches = list(_TOP_HEADING_RE.finditer(text))
    if not matches:
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []
    first_start = matches[0].start()
    if first_start > 0:
        preamble = text[:first_start].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        heading = m.group(0).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # Keep heading as the first line so chunker sees the marker.
        section = heading + ("\n\n" + body if body else "")
        sections.append((m.group(2).strip(), section.strip()))
    return sections


class MarkdownParser:
    """Markdown parser — strips YAML front matter, splits on H1/H2."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "markdown"

    def supports(self, mime_type: str, file_ext: str) -> bool:
        mime = (mime_type or "").strip().lower()
        ext = (file_ext or "").strip().lower()
        return mime in _MD_MIMES or ext in _MD_EXTS

    async def parse(
        self,
        content: bytes,
        *,
        file_name: str,
    ) -> list[dict]:
        if len(content) > DEFAULT_MARKDOWN_MAX_BYTES:
            raise ValueError(
                f"Markdown too large: {len(content)} bytes "
                f"(max {DEFAULT_MARKDOWN_MAX_BYTES})",
            )

        text = content.decode("utf-8", errors="replace")
        body = _strip_front_matter(text)
        sections = _split_sections(body)

        chunks: list[dict[str, Any]] = []
        for idx, (heading, section_text) in enumerate(sections):
            if not section_text.strip():
                continue
            metadata: dict[str, Any] = {
                "section_index": idx,
                "file_name": file_name,
                "parser": "markdown",
            }
            if heading:
                metadata["heading"] = heading
            chunks.append({
                "content": section_text,
                "metadata": metadata,
            })

        logger.info(
            "markdown_parsed",
            file_name=file_name,
            sections=len(chunks),
            bytes=len(content),
        )
        return chunks


__all__ = ["MarkdownParser"]
