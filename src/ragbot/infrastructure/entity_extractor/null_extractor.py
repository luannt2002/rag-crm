"""NullExtractor — Null Object pattern for entity extraction.

Default selection when ``system_config.entity_extractor_provider`` is
missing or set to ``"null"``. Returns an empty list so the caller
(``expand_query_with_entities``) skips entity-grounded variants and
falls through to the existing paraphrase + variant-0 behaviour
unchanged. Selecting Null is a *deliberate* operator choice — keeps
T3 entity-grounded expansion strictly opt-in per-bot to preserve
backward compatibility for tenants who do not need it.
"""

from __future__ import annotations


class NullExtractor:
    """No-op extractor — :meth:`extract` returns ``[]`` for any input."""

    def __init__(self, **_: object) -> None:
        # Accept (and ignore) any kwargs so the registry can build
        # NullExtractor with the same signature as a real provider.
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    async def extract(self, query: str, *, language: str) -> list[str]:
        # ``language`` is intentionally ignored — Null is multi-lingual
        # by being empty. Returning [] is the contract.
        return []


__all__ = ["NullExtractor"]
