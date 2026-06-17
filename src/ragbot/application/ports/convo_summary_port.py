"""ConvoSummary Port — contract for conversation summary compression strategies.

Owner-opt-in service: the platform exposes the Port + Registry but never
inserts summary text into the LLM prompt automatically. Bot owners flip
``bots.convo_summary_enabled`` and the admin layer wires a ``ConvoSummaryPort``
implementation through DI. Until the owner opts in, the default
``NullConvoSummary`` returns an empty string so the chat hot path is
unaffected.

Implementations:
    - ``NullConvoSummary``  — returns "" (default OFF)
    - ``LLMConvoSummary``  — uses an injected ``LLMPort`` to summarise

Caller contract:
    summarise(turns, max_tokens) -> str

The Port deliberately does NOT carry tenant or trace identifiers — those
are bound at construction time by the implementation (``LLMConvoSummary``
takes them as constructor args) so the call site stays minimal and stable
when the admin wiring lands in the subsequent task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ragbot.shared.types import Role


@dataclass(frozen=True, slots=True)
class Turn:
    """One conversation turn (role + content) for summary input.

    @param role: chat role (``"user"`` / ``"assistant"`` / ``"system"`` / ``"tool"``).
    @param content: turn text exactly as sent / received — no truncation.
    """

    role: Role
    content: str


@runtime_checkable
class ConvoSummaryPort(Protocol):
    """Compress conversation history into a short text summary.

    The summary is intended for owner-side use (e.g. replacing older history
    once ``DEFAULT_CONVO_SUMMARY_TRIGGER_TURNS`` is reached) — the platform
    never auto-injects it into an LLM prompt.
    """

    async def summarise(self, turns: list[Turn], max_tokens: int) -> str:
        """Return a summary of ``turns`` bounded by ``max_tokens``.

        @param turns: ordered conversation turns (oldest first).
        @param max_tokens: soft upper bound for summary length.
        @return: summary string. Empty string means no summary was produced
            (e.g. NullConvoSummary, empty input, or provider failure handled
            silent at adapter level).
        """
        ...


__all__ = ["ConvoSummaryPort", "Turn"]
