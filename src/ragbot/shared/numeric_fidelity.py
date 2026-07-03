"""Deterministic numeric-fidelity check — OBSERVE-ONLY (truth-audit Phase 4).

Contract: specs/001-rag-truth-audit/contracts/numeric-fidelity-event.md.

Classifies every significant number token in an ANSWER against the SERVED
context (the graded chunks the LLM actually saw):

  * ``grounded``       — token appears literally in the context, OR its parsed
                          value equals a parsed context-number value (catches
                          formatting drift: ``1.242.000đ`` vs ``1242000``).
  * ``derived_valid``  — equals ``|a−b|`` or ``a+b`` of two grounded values
                          (research D2 allow-list; recomputed, not trusted).
  * ``unsupported``    — neither: the fabrication signal (the q20 class —
                          ``26.000.000đ`` minted from a stray date "26").

Pure string/arithmetic — no model call, no DB, no I/O. This module NEVER
touches the answer; blocking (if ever) is a separate, owner-gated step after
observe-mode false-positive/catch rates are reviewed (FR-010).

Mirror of the ingest-side ``find_dropped_numbers`` (source numbers missing from
chunks); both share the significant-number tokenizer in ``number_format`` so
the two coverage checks can never drift.
"""
from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_NUMERIC_COVERAGE_MIN_DIGITS,
    NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP,
)
from ragbot.shared.number_format import (
    iter_significant_number_tokens,
    parse_money_vn,
)


def _token_value(token: str) -> int | None:
    """Parse a number token to a comparable integer value (money-aware)."""
    val = parse_money_vn(token)
    if val is not None:
        return val
    digits = token.replace(".", "").replace(",", "")
    return int(digits) if digits.isdigit() else None


def classify_answer_numbers(
    answer: str,
    context_texts: list[str],
    *,
    min_digits: int = DEFAULT_NUMERIC_COVERAGE_MIN_DIGITS,
) -> dict:
    """Classify answer numbers vs served context. Returns the trace-field dict:

    ``{"n_numbers", "n_grounded", "n_derived_valid", "n_unsupported",
       "unsupported_tokens"}`` — counts exact, token list capped (PII-lean:
    tokens only, never answer text).
    """
    joined = "\n".join(t for t in context_texts if t)
    context_values: set[int] = set()
    for tok in iter_significant_number_tokens(joined, min_digits=min_digits):
        val = _token_value(tok)
        if val is not None:
            context_values.add(val)

    grounded_vals: list[int] = []
    pending: list[tuple[str, int | None]] = []
    n_grounded = 0
    seen: set[str] = set()
    for tok in iter_significant_number_tokens(answer, min_digits=min_digits):
        if tok in seen:
            continue
        seen.add(tok)
        val = _token_value(tok)
        if tok in joined or (val is not None and val in context_values):
            n_grounded += 1
            if val is not None:
                grounded_vals.append(val)
        else:
            pending.append((tok, val))

    n_derived = 0
    unsupported: list[str] = []
    for tok, val in pending:
        derived = val is not None and any(
            val == abs(a - b) or val == a + b
            for i, a in enumerate(grounded_vals)
            for b in grounded_vals[i:]
        )
        if derived:
            n_derived += 1
        else:
            unsupported.append(tok)

    return {
        "n_numbers": len(seen),
        "n_grounded": n_grounded,
        "n_derived_valid": n_derived,
        "n_unsupported": len(unsupported),
        "unsupported_tokens": unsupported[:NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP],
    }


__all__ = ["classify_answer_numbers"]
