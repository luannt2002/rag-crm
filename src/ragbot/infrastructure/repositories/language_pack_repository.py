"""SqlAlchemy repository for ``language_packs`` table (migration 0055).

Schema reminder (composite PK ``code, prompt_key``):

    code           VARCHAR(8)   NOT NULL  -- BCP47-ish, e.g. "vi", "en", "es"
    prompt_key     VARCHAR(64)  NOT NULL  -- one of LANGUAGE_PACK_PROMPT_KEYS
    content        TEXT         NOT NULL
    version        INTEGER      NOT NULL  DEFAULT 1
    created_at     TIMESTAMPTZ  NOT NULL  DEFAULT now()
    updated_at     TIMESTAMPTZ  NOT NULL  DEFAULT now()

The repository is intentionally tenant-agnostic — language packs are
platform-wide. Per-bot overrides (later milestone) live on the bot row;
this layer only services the global default.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class LanguagePackRepository:
    """Read-only access to ``language_packs``. Writes go through the seed
    migration or future admin tooling, not this repo."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise with an async session factory.

        @param session_factory: SQLAlchemy async sessionmaker.
        """
        self._sf = session_factory

    async def get_pack(self, code: str, prompt_key: str) -> str | None:
        """Return ``content`` for ``(code, prompt_key)`` or ``None``.

        @param code: language code (e.g. ``"vi"``).
        @param prompt_key: one of ``LANGUAGE_PACK_PROMPT_KEYS``.
        """
        async with self._sf() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT content FROM language_packs "
                        "WHERE code = :c AND prompt_key = :k"
                    ),
                    {"c": code, "k": prompt_key},
                )
            ).fetchone()
        if row is None:
            return None
        return str(row[0])

    async def list_pack(self, code: str) -> dict[str, str]:
        """Return all rows for ``code`` as ``{prompt_key: content}``.

        Empty dict when the language has no rows yet — callers MUST
        handle the missing-language case (typically via fallback to
        ``DEFAULT_LANGUAGE``).
        """
        async with self._sf() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT prompt_key, content FROM language_packs "
                        "WHERE code = :c"
                    ),
                    {"c": code},
                )
            ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}


__all__ = ["LanguagePackRepository"]
