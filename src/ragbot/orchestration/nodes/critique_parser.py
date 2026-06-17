"""Self-RAG critique-token post-processor (Asai et al., 2023).

The bot owner appends a refusal rule to ``bots.system_prompt`` instructing the
LLM to tag each factual claim with ``[Supported]`` when it is grounded in
retrieved context and ``[Unsupported]`` otherwise.  This module parses those
markers out of the answer, computes the unsupported ratio, and gates an
optional refusal when the ratio exceeds the per-bot threshold.

Sacred rules honoured (CLAUDE.md):
- **Application KHÔNG inject vào LLM prompt** — this module never modifies
  ``bot_system_prompt``; the operator wires the rule via DB.  See
  ``docs/sysprompt/self_rag_critique_template.md``.
- **Application KHÔNG override LLM answer** — only the *markers* are
  stripped (cosmetic cleanup); the model's prose is returned verbatim.
  When refusal triggers, the substitute text comes from
  ``bots.oos_answer_template`` (per-bot column), never an i18n fallback.
- **HALLU=0 sacred** — any parse error fails open (treat as raw answer,
  no critique gate); the orchestrator falls back to the model's text and
  logs a warning so the operator sees the regression.

Pure functions, no side effects, no DB.
"""

from __future__ import annotations

import re
from typing import Final

# Domain-neutral markers per the paper.  Case-sensitive because the
# operator instructs the LLM to emit the literal English token.
_SUPPORTED_TOKEN: Final[str] = "[Supported]"
_UNSUPPORTED_TOKEN: Final[str] = "[Unsupported]"

# Single regex with alternation so a single pass over the answer scores
# both classes.  ``re.IGNORECASE`` lets bot owners normalise to lower-case
# at the prompt layer without breaking parsing.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(Supported|Unsupported)\]",
    re.IGNORECASE,
)


def parse_critique_tokens(answer_text: str) -> dict:
    """Parse Self-RAG critique markers from an LLM answer.

    Returns a dict with five keys:

    * ``clean_text`` — the answer with all ``[Supported]`` / ``[Unsupported]``
      tokens removed (cosmetic strip; whitespace collapsed to single spaces
      so the user never sees the bookkeeping).
    * ``supported_count`` — count of ``[Supported]`` matches (case-insensitive).
    * ``unsupported_count`` — count of ``[Unsupported]`` matches.
    * ``total_claims`` — ``supported_count + unsupported_count``.
    * ``unsupported_ratio`` — ``unsupported_count / total_claims`` or ``0.0``
      when ``total_claims == 0`` (no markers found ⇒ feature inactive on
      this turn; downstream gate must treat as pass-through).

    Empty / non-string input returns the zero-claims dict with
    ``clean_text == ""``.
    """
    if not isinstance(answer_text, str) or not answer_text:
        return {
            "clean_text": "",
            "supported_count": 0,
            "unsupported_count": 0,
            "total_claims": 0,
            "unsupported_ratio": 0.0,
        }

    supported_count = 0
    unsupported_count = 0
    for match in _TOKEN_RE.finditer(answer_text):
        label = match.group(1).lower()
        if label == "supported":
            supported_count += 1
        else:
            unsupported_count += 1

    # Strip every marker (case-insensitive) then collapse leftover
    # whitespace runs so the user-visible prose flows naturally.
    clean_text = _TOKEN_RE.sub("", answer_text)
    clean_text = re.sub(r"[ \t]{2,}", " ", clean_text)
    clean_text = re.sub(r"\s+([.,;:!?])", r"\1", clean_text)
    clean_text = clean_text.strip()

    total_claims = supported_count + unsupported_count
    if total_claims > 0:
        unsupported_ratio = unsupported_count / total_claims
    else:
        unsupported_ratio = 0.0

    return {
        "clean_text": clean_text,
        "supported_count": supported_count,
        "unsupported_count": unsupported_count,
        "total_claims": total_claims,
        "unsupported_ratio": unsupported_ratio,
    }


def should_refuse_critique(parse_result: dict, threshold: float) -> bool:
    """Return True when the unsupported ratio meets/exceeds the threshold.

    ``total_claims == 0`` always returns False — no markers means the LLM
    did not engage the Self-RAG protocol on this turn (either the operator
    has not wired the sysprompt rule yet, or the answer is a greeting /
    chitchat with no factual claims).  In that case the orchestrator
    returns the answer untouched.

    Negative / non-numeric ``threshold`` is coerced to ``0.0`` so a
    misconfigured plan_limits row cannot flip every answer to refuse.
    """
    if not isinstance(parse_result, dict):
        return False
    total = int(parse_result.get("total_claims", 0) or 0)
    if total <= 0:
        return False
    try:
        ratio = float(parse_result.get("unsupported_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = 0.0
    if thr < 0.0:
        thr = 0.0
    return ratio >= thr


__all__ = [
    "parse_critique_tokens",
    "should_refuse_critique",
]
