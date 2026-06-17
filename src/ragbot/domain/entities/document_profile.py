"""DocumentProfile — quantitative features for AdapChunk strategy selection.

Ref: PLAN_04 / RAGBOT_MASTER §6.4 / AdapChunk §3.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeadingCounts:
    h1: int = 0
    h2: int = 0
    h3: int = 0
    h4: int = 0

    @property
    def total(self) -> int:
        return self.h1 + self.h2 + self.h3 + self.h4


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    """Rule-based extracted features used by AdapChunk LLM Strategy Selector."""

    heading_counts: HeadingCounts
    has_toc: bool
    table_count: int
    table_avg_rows: float
    formula_count: int
    image_count: int
    code_block_count: int
    avg_text_block_length: float
    heading_ratio: float
    mixed_content_score: float
    detected_language: str
    total_blocks: int
    total_words: int


__all__ = ["DocumentProfile", "HeadingCounts"]
