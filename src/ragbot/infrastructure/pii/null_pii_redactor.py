"""NullPiiRedactor — passthrough Strategy default for PII redaction."""

from __future__ import annotations


class NullPiiRedactor:
    """No-op PII redactor — :meth:`redact` returns input + empty entities."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def redact(self, text: str) -> tuple[str, list[dict]]:
        return text, []


__all__ = ["NullPiiRedactor"]
