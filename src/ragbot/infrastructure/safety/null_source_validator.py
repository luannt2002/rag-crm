"""NullSourceValidator — passthrough Strategy default for source allow-list.

Returns ``(True, None)`` for every URL regardless of the patterns argument.
This is the legacy / opt-out path: when the bot owner has not configured
``plan_limits.allowed_source_domains`` AND the ``source_allowlist_enabled``
feature flag is OFF, the orchestrator picks this Null adapter so ingest
behaves identically to the no-safety baseline (no rejection ever).
"""

from __future__ import annotations

from typing import Sequence


class NullSourceValidator:
    """No-op source validator — every URL is allowed."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def is_allowed(
        self,
        source_url: str,
        allowed_patterns: Sequence[str],
    ) -> tuple[bool, str | None]:
        # Allow regardless of inputs — preserves byte-identical behaviour
        # for tenants who have not opted into the allow-list.
        return True, None


__all__ = ["NullSourceValidator"]
