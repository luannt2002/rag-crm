"""NullNarrateGenerator — Null Object for the Narrate-then-Embed strategy.

Default-OFF baseline. ``narrate(content, block_type)`` returns the input
verbatim so callers can wire ``await narrate.narrate(c, t)`` unconditionally
and still pay zero LLM cost until the operator opts in. Selecting this
implementation is a deliberate operator choice (or the platform default
until opt-in).
"""

from __future__ import annotations

import structlog

from ragbot.shared.types import BlockType

logger = structlog.get_logger(__name__)


class NullNarrateGenerator:
    """No-op Narrate — always returns the input ``content`` unchanged."""

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    async def narrate(self, content: str, block_type: BlockType) -> str:
        """Return ``content`` verbatim.

        The ingest pipeline then embeds the raw block content — i.e.
        legacy behaviour. Logged at debug so an operator can confirm
        the Null branch is in effect without spamming hot-path logs.
        """
        logger.debug(
            "null_narrate_bypass",
            block_type=block_type,
            content_chars=len(content),
        )
        return content


__all__ = ["NullNarrateGenerator"]
