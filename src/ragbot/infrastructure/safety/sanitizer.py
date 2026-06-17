"""CleanBase Tier-0 sanitizer — corpus-poisoning defence at ingest boundary.

T1-Safety. The Tier-0 layer is the **baseline corpus safety net** —
applied AFTER parse, BEFORE chunk + embed — and chains four
deterministic, zero-cost operations:

1. **HTML / XML tag strip** — defends against ``<script>`` / ``<style>``
   blocks slipping past a permissive web parser. Pattern lives in
   ``shared.constants.HTML_TAG_REGEX``.

2. **Unicode NFC normalize** — canonical equivalence (preserves VN
   diacritic glyphs). The companion ``normalize_vn`` helper is the same
   normalization used by the retrieve hot path, so ingest + query agree
   byte-for-byte (cache + lexical hits would otherwise miss silently).
   **Never** swap to NFKC — that destroys technical Unicode like ``"①"``
   and ``"㎏"`` per the ``shared/text_normalization`` docstring.

3. **Zero-width / BOM remove** — strips the invisible character family
   that the Trojan Source paper (arxiv 2111.00169 §4) identifies as the
   primary invisible-injection vector. Class lives in
   ``shared.constants.ZERO_WIDTH_CHAR_REGEX``.

4. **Prompt-injection blacklist** — replaces high-confidence cross-bot
   jailbreak phrases (``"ignore all previous instructions"``, Vietnamese
   ``"bỏ qua tất cả hướng dẫn"``, ``<|im_start|>`` chat-ML tokens, etc.)
   with ``DEFAULT_INJECTION_REDACTION_TOKEN`` so retrieval still finds
   neighbouring legitimate content while the injection payload is
   defanged. Patterns live in
   ``shared.constants.PROMPT_INJECTION_PATTERNS``.

The sanitizer is a **pre-LLM-input filter only**. It NEVER inspects or
rewrites LLM completions — per CLAUDE.md "Application MINDSET", the bot
owner's ``system_prompt`` is the single source of truth for answer
content. Tier-0 is also **idempotent**: running it twice on the same
text returns identical output with ``total_redactions == 0`` on the
second pass.

Proof / citations
-----------------
- CleanBase: Maharaj et al., "CleanBase: a sanitization layer for RAG
  corpus poisoning defense" arxiv 2605.00460 — section 3 Tier-0 pipeline.
  Reported corpus-attack success drop 90% → 12% with the four-stage chain.
- Prompt-injection lexicon: Greshake et al., "Not what you've signed up
  for: Compromising Real-World LLM-Integrated Applications with Indirect
  Prompt Injection" arxiv 2302.12173 — table 2 indirect-injection patterns.
- Zero-width unicode injection: Boucher & Anderson, "Trojan Source:
  Invisible Vulnerabilities" arxiv 2111.00169 — section 4 invisible
  character family enumeration.
"""

from __future__ import annotations

import re
import unicodedata

from ragbot.application.ports.sanitizer_port import SanitizeReport
from ragbot.shared.constants import (
    DEFAULT_INJECTION_REDACTION_TOKEN,
    DEFAULT_NORMALIZATION_FORM,
    HTML_TAG_REGEX,
    PROMPT_INJECTION_PATTERNS,
    ZERO_WIDTH_CHAR_REGEX,
)


# Compile once at import — Tier-0 is on the ingest hot path; the regex
# objects are immutable so module-level compilation is safe across
# multiple sanitizer instances.
_HTML_TAG_RE: re.Pattern[str] = re.compile(HTML_TAG_REGEX, re.DOTALL)
_ZERO_WIDTH_RE: re.Pattern[str] = re.compile(ZERO_WIDTH_CHAR_REGEX)

# ``PROMPT_INJECTION_PATTERNS`` carries leading ``(?i)`` inline flags from
# the legacy ingest helper. Python 3.12's ``re`` no longer accepts inline
# flags mid-alternation after ``"|"``-join, so we strip the prefix and
# compile with ``re.IGNORECASE`` instead — behaviourally equivalent.
_INJECTION_RE: re.Pattern[str] = re.compile(
    "|".join(p.removeprefix("(?i)") for p in PROMPT_INJECTION_PATTERNS),
    re.IGNORECASE | re.MULTILINE,
)


class CleanBaseTier0Sanitizer:
    """Default-ON Tier-0 sanitizer — see module docstring for rationale.

    The constructor accepts ``**kwargs`` so the DI registry can forward
    construction options without binding the call site to a specific
    signature. No options are currently honoured; future Tier-1 / Tier-2
    layers can extend the dataclass without breaking callers.
    """

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "tier0"

    def sanitize(self, text: str) -> tuple[str, SanitizeReport]:
        """Run the four-stage Tier-0 chain on ``text``.

        @param text: raw post-parse document content.
        @return: ``(sanitized_text, report)``. ``report.total_redactions``
            == 0 implies the input was already clean — useful for sampling
            "interesting" docs in observability dashboards.
        """
        if not isinstance(text, str):
            text = "" if text is None else str(text)

        n_chars_in = len(text)
        if not text:
            return text, SanitizeReport(
                provider_name="tier0",
                n_chars_in=0,
                n_chars_out=0,
                html_tags_stripped=0,
                zero_width_removed=0,
                injection_patterns_matched=0,
                nfc_changed=False,
            )

        # Stage 1 — HTML / XML tag strip. Replace each tag with a single
        # space so adjacent text is not glued into a single token (e.g.
        # ``"foo<br/>bar"`` -> ``"foo bar"``, not ``"foobar"``).
        text_after_html, html_n = _HTML_TAG_RE.subn(" ", text)

        # Stage 2 — Unicode NFC normalize. Track whether normalization
        # changed the byte sequence so callers can surface inconsistent
        # upstream encodings without leaking the actual content.
        text_after_nfc = unicodedata.normalize(
            DEFAULT_NORMALIZATION_FORM, text_after_html,
        )
        nfc_changed = text_after_nfc != text_after_html

        # Stage 3 — Zero-width / BOM remove. The character class itself is
        # invisible; substituting with the empty string is intentional
        # (these characters have no semantic carrier function in document
        # text — they are formatting / injection markers only).
        text_after_zw, zw_n = _ZERO_WIDTH_RE.subn("", text_after_nfc)

        # Stage 4 — Prompt-injection blacklist. ``subn`` returns the
        # number of NON-OVERLAPPING substitutions, which is exactly the
        # metric we want (each distinct match = one redaction).
        sanitized, inj_n = _INJECTION_RE.subn(
            DEFAULT_INJECTION_REDACTION_TOKEN, text_after_zw,
        )

        return sanitized, SanitizeReport(
            provider_name="tier0",
            n_chars_in=n_chars_in,
            n_chars_out=len(sanitized),
            html_tags_stripped=html_n,
            zero_width_removed=zw_n,
            injection_patterns_matched=inj_n,
            nfc_changed=nfc_changed,
        )


__all__ = ["CleanBaseTier0Sanitizer"]
