"""PII redactor port (Presidio impl)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ragbot.shared.constants import DEFAULT_LANGUAGE


@runtime_checkable
class PIIRedactorPort(Protocol):
    async def redact(
        self,
        text: str,
        *,
        language: str = DEFAULT_LANGUAGE,
    ) -> tuple[str, dict[str, list[tuple[int, int]]]]:
        """Return (redacted_text, entity_spans_by_type)."""
        ...


__all__ = ["PIIRedactorPort"]
