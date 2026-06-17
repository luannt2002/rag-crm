"""PII Redactor Strategy Port (4).

Distinct from the legacy :mod:`ragbot.application.ports.pii_port` (which is
async + returns spans-by-type) — this port is the *Strategy* port for the
new registry-driven implementation:

    redact(text) -> tuple[str, list[dict]]

Returned ``found_entities`` items have shape::

    {"type": str, "start": int, "end": int}

Default implementation is :class:`NullPiiRedactor` (passthrough). Heavy
adapters (Presidio) are opt-in via ``system_config.pii_redactor_provider``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PiiRedactorPort(Protocol):
    """Strategy interface for PII redactors."""

    def redact(self, text: str) -> tuple[str, list[dict]]:
        """Return ``(redacted_text, found_entities)``.

        ``found_entities`` is a list of dicts with at least ``type``,
        ``start`` and ``end`` keys. Implementations MUST report spans
        relative to the *input* text so callers can correlate.
        """
        ...

    def get_provider_name(self) -> str:
        ...


__all__ = ["PiiRedactorPort"]
