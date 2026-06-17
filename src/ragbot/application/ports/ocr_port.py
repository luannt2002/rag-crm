"""OCR port (Docling impl in infrastructure)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ragbot.domain.entities.document import Block


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: list[Block]
    language: str
    page_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OCRPort(Protocol):
    async def parse(
        self,
        source: str | bytes,
        *,
        mime_type_hint: str | None = None,
    ) -> ParsedDocument: ...

    def supported_mimes(self) -> frozenset[str]: ...

    async def close(self) -> None: ...


__all__ = ["OCRPort", "ParsedDocument"]
