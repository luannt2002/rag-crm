"""Language Pack Port — contract for DB-driven prompt translations.

Provides the prompt-key → text mapping for one language; backed by the
``language_packs`` table so adding a new language is a seed, not code.

A "pack" is the full mapping ``prompt_key → text`` for one language code.
Callers should use ``get_pack(language)`` whenever they need ≥ 2 prompts
in the same node — it is one cache + one DB round-trip.

The port intentionally returns plain ``dict[str, str]`` (not the legacy
``LanguagePack`` dataclass) so adapters can extend the registry without
touching the dataclass. ``ragbot.shared.i18n`` retains a thin in-memory
fallback used when the DB is unreachable at boot, mirroring how the
reranker registry falls back to ``NullReranker``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LanguagePackPort(Protocol):
    """Contract for resolving prompt content by (language, prompt_key)."""

    async def get(self, language: str, prompt_key: str) -> str:
        """Return prompt text for ``(language, prompt_key)``.

        Implementations MUST fall back to the deployment-default language
        when the requested ``language`` has no row for ``prompt_key``,
        and finally to an empty string so callers never crash.
        """
        ...

    async def get_pack(self, language: str) -> dict[str, str]:
        """Return the full ``prompt_key → text`` map for ``language``.

        Implementations SHOULD merge default-language rows with
        language-specific overrides so partially translated languages
        still work end-to-end.
        """
        ...


__all__ = ["LanguagePackPort"]
