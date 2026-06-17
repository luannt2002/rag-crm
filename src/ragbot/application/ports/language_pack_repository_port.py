"""LanguagePackRepositoryPort — Protocol for ``language_packs`` reads.

Pure read-only contract over the platform-wide language-pack table.
Writes happen via seed migrations / admin tooling, never through this
port — see ``infrastructure.repositories.language_pack_repository``
for the SqlAlchemy adapter and migration 0055 for schema.

Defined here so ``application.services.LanguagePackService`` can depend
on the contract instead of importing the concrete adapter (hexagonal
boundary; see Issue #7 in the deep-dive report).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LanguagePackRepositoryPort(Protocol):
    """Read-only access to the ``language_packs`` table.

    Implementations MUST be tenant-agnostic — language packs are global.
    """

    async def get_pack(self, code: str, prompt_key: str) -> str | None:
        """Return ``content`` for ``(code, prompt_key)`` or ``None`` when
        the row does not exist. MUST NOT raise on a missing row."""
        ...

    async def list_pack(self, code: str) -> dict[str, str]:
        """Return all rows for ``code`` as ``{prompt_key: content}``.

        Empty dict when no rows exist for the language — caller decides
        whether to fall back to ``DEFAULT_LANGUAGE``.
        """
        ...


__all__ = ["LanguagePackRepositoryPort"]
