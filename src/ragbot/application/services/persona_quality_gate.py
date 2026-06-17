"""Persona quality gate — audit-only checks for bot system_prompt.

Detects RAG anti-patterns in bot owner's system_prompt and emits metrics.
Does NOT override LLM answer or inject text. Bot owner reads warnings on
dashboard and decides whether to fix.

Anti-patterns detected:
- Oversized persona (long prompts dilute every rule).
- Persona pollution (pricing / numbers / answer templates in persona —
  should live in corpus docs).
- Directive conflict (contradictory rules across the prompt).

Each check runs in O(prompt_chars) regex; cheap to run on every save +
cache the result keyed by system_prompt hash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ragbot.shared.constants import (
    DEFAULT_PERSONA_OVERSIZED_CHAR_THRESHOLD,
    DEFAULT_PERSONA_POLLUTION_PATTERNS,
    DEFAULT_PERSONA_DIRECTIVE_CONFLICT_PAIRS,
)


@dataclass(frozen=True)
class PersonaWarning:
    """One audit finding for a system_prompt."""

    code: str
    severity: str
    detail: str


def _scan_oversized(system_prompt: str, threshold: int) -> list[PersonaWarning]:
    """Flag system_prompt longer than threshold (cost + dilution risk)."""
    chars = len(system_prompt)
    if chars > threshold:
        return [PersonaWarning(
            code="persona_oversized",
            severity="warn",
            detail=f"system_prompt is {chars} chars (>{threshold}). Long "
                   f"persona dilutes every rule and increases per-turn cost. "
                   f"Move pricing / facts to corpus docs.",
        )]
    return []


def _scan_pollution(system_prompt: str, patterns: tuple[str, ...]) -> list[PersonaWarning]:
    """Flag pricing/data/answer-templates baked into persona."""
    findings: list[PersonaWarning] = []
    for pattern in patterns:
        match = re.search(pattern, system_prompt, re.IGNORECASE)
        if match:
            findings.append(PersonaWarning(
                code="persona_pollution",
                severity="warn",
                detail=f"Pattern '{pattern}' detected in persona. Move data "
                       f"to corpus docs so retrieval+grounding fires.",
            ))
    return findings


def _scan_directive_conflict(
    system_prompt: str,
    pairs: tuple[tuple[str, str], ...],
) -> list[PersonaWarning]:
    """Flag pairs of contradictory directives co-present in persona."""
    findings: list[PersonaWarning] = []
    for negative, positive in pairs:
        if (
            re.search(negative, system_prompt, re.IGNORECASE)
            and re.search(positive, system_prompt, re.IGNORECASE)
        ):
            findings.append(PersonaWarning(
                code="persona_directive_conflict",
                severity="high",
                detail=f"Conflicting directives: '{negative}' AND '{positive}'."
                       f" LLM may pick the wrong branch.",
            ))
    return findings


def audit_system_prompt(
    system_prompt: str | None,
    *,
    oversized_threshold: int = DEFAULT_PERSONA_OVERSIZED_CHAR_THRESHOLD,
    pollution_patterns: tuple[str, ...] = DEFAULT_PERSONA_POLLUTION_PATTERNS,
    conflict_pairs: tuple[tuple[str, str], ...] = DEFAULT_PERSONA_DIRECTIVE_CONFLICT_PAIRS,
) -> list[PersonaWarning]:
    """Run all persona quality checks. Returns list of warnings (audit-only)."""
    if not system_prompt:
        return []
    return (
        _scan_oversized(system_prompt, oversized_threshold)
        + _scan_pollution(system_prompt, pollution_patterns)
        + _scan_directive_conflict(system_prompt, conflict_pairs)
    )


__all__ = ["PersonaWarning", "audit_system_prompt"]
