"""NullParser — Null Object pattern for the document parser strategy.

Default selection when ``system_config.document_parser_provider`` is missing
or set to ``"null"``. Reports it does not support any MIME / extension and
raises ``NotImplementedError`` from :meth:`parse` so a caller that bypasses
``supports()`` discovers the misconfig loudly.
"""

from __future__ import annotations


class NullParser:
    """No-op parser — supports nothing, parse is a hard error."""

    def __init__(self, **_: object) -> None:
        # Accept arbitrary kwargs so the registry can build NullParser with
        # the same kwargs it would pass to a real provider.
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def supports(self, mime_type: str, file_ext: str) -> bool:  # noqa: ARG002
        return False

    async def parse(self, content: bytes, *, file_name: str) -> list[dict]:  # noqa: ARG002
        raise NotImplementedError(
            "NullParser is the default no-op strategy; configure "
            "system_config.document_parser_provider to enable a real parser."
        )


__all__ = ["NullParser"]
