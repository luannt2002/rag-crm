"""CleanBase Tier-0 Sanitizer Port — pre-chunk ingest-time scrubber.

T1-Safety. Defensive filter applied AFTER raw parse and BEFORE chunking
+ embedding. Chains conservative-by-default operations:

1. HTML strip (defense against script/style tags smuggled by web parsers)
2. Unicode NFC normalize (canonical equivalence; preserves VN diacritics —
   never NFKC, which destroys technical Unicode like ``"①"`` / ``"㎏"``)
3. Zero-width character remove (U+200B/C/D, U+FEFF — invisible injection
   vector when content is copy-pasted from rich-text editors)
4. Prompt-injection blacklist regex (cross-bot jailbreak phrases listed in
   :data:`ragbot.shared.constants.PROMPT_INJECTION_PATTERNS`)

CleanBase is a **pre-LLM-input filter** — it only sanitizes ingest content
before storage. It NEVER inspects or rewrites LLM completions; that line
belongs to the bot owner's ``system_prompt`` per CLAUDE.md "Application
MINDSET" (no app-side answer override).

Proof / citations (each implementation MUST keep an updated docstring):
- CleanBase: Maharaj et al., "CleanBase: a sanitization layer for RAG
  corpus poisoning defense" arxiv 2605.00460 — section 3 (Tier-0 pipeline).
- Prompt-injection lexicon: Greshake et al., "Not what you've signed up
  for" arxiv 2302.12173 — table 2 indirect-injection patterns.
- Zero-width unicode injection: Boucher & Anderson, "Trojan Source"
  arxiv 2111.00169 — section 4 (invisible character family).

Caller contract: synchronous; deterministic; pure (no IO). Owner-opt-in
is enforced at the **registry** layer via the ``cleanbase_tier0_enabled``
``system_config`` key resolved by the DI container before construction —
the Port itself has no flag knob.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SanitizeReport:
    """Telemetry-friendly summary of one sanitize pass.

    Attributes
    ----------
    provider_name:
        Registry key of the strategy that produced this report (e.g.
        ``"tier0"`` or ``"null"``).
    n_chars_in:
        Length of the original input text (characters, Python ``len``).
    n_chars_out:
        Length of the sanitized output text.
    html_tags_stripped:
        Count of HTML/XML tags removed (0 when the input is plain text).
    zero_width_removed:
        Count of zero-width / BOM characters removed.
    injection_patterns_matched:
        Count of prompt-injection blacklist substitutions made (each
        ``re.subn`` hit counts once, regardless of replacement length).
    nfc_changed:
        ``True`` when NFC normalization mutated the input — surfaces
        upstream encoding inconsistency without leaking the actual text.
    """

    provider_name: str
    n_chars_in: int
    n_chars_out: int
    html_tags_stripped: int
    zero_width_removed: int
    injection_patterns_matched: int
    nfc_changed: bool

    @property
    def total_redactions(self) -> int:
        """Sum of all destructive operations — single metric for dashboards."""
        return (
            self.html_tags_stripped
            + self.zero_width_removed
            + self.injection_patterns_matched
        )


@runtime_checkable
class SanitizerPort(Protocol):
    """Sanitize raw document text before chunking + embedding.

    Implementations MUST be pure functions of ``text`` — no IO, no random
    state, no global mutation. The caller relies on idempotency: a second
    ``sanitize`` pass over already-sanitized text MUST return identical
    output with ``total_redactions == 0``.

    Implementations MUST gracefully handle empty / non-string-like input
    by returning ``(text, SanitizeReport(...))`` with zero counts; callers
    rely on this to avoid wrapping every call site in a None check.
    """

    def sanitize(self, text: str) -> tuple[str, SanitizeReport]:
        """Return ``(sanitized_text, report)``.

        @param text: raw text (post-parse, pre-chunk).
        @return: cleaned text plus a :class:`SanitizeReport` describing
            the operations performed.
        """
        ...

    def get_provider_name(self) -> str:
        """Stable provider key matching the registry entry (e.g. ``"tier0"``)."""
        ...


__all__ = ["SanitizerPort", "SanitizeReport"]
