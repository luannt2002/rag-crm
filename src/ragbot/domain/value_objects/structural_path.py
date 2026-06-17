"""StructuralPath — breadcrumb of a chunk inside a document hierarchy.

Ref: PLAN_03 §ids.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StructuralPath:
    """Hierarchical path: ('Chương 3', '3.1', 'Kết quả thực nghiệm')."""

    segments: tuple[str, ...]

    def breadcrumb(self, *, separator: str = " > ") -> str:
        return separator.join(self.segments)

    def depth(self) -> int:
        return len(self.segments)

    def parent(self) -> StructuralPath | None:
        if not self.segments:
            return None
        return StructuralPath(self.segments[:-1])


__all__ = ["StructuralPath"]
