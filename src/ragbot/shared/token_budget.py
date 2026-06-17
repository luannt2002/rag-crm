"""Token budget arithmetic — zero-default contract.

Sacred design eliminates None/empty/negative case in hot path:
  - DB level:    NOT NULL DEFAULT 0 + CHECK >= 0
  - Pydantic:    Field(default=0, ge=0)
  - Caller:      int(value or 0) at API boundary
  - Helper here: trusts input is int >= 0, branches only on integer math

3 callers share these helpers:
  1. chat_worker.py — pre-call gate (can_answer)
  2. query_graph.py generate node — output cap resolve
  3. infrastructure/chat_hooks/* — post-call deduct in DB hook + Redis hook

Why `tokens_used` (cumulative) over `tokens_remaining` (decremented):
  - Audit: SUM(tokens_used) GROUP BY bot → total usage per bot
  - Reset: single UPDATE bots SET tokens_used = 0 (monthly cron)
  - Race-safe: UPDATE SET tokens_used = tokens_used + :delta is atomic
  - Re-config-safe: changing extra_max_tokens auto recalculates limit

M22 — bounded-list helper
-------------------------
``truncate_to_token_budget`` is a domain-agnostic utility for any node
that needs to grow a context window bounded by a token budget. Pure
function (no DB / no async / no LLM) so neighbour-expand, citation-pack
and future fan-out nodes can share one well-tested truncation rule
instead of re-implementing the loop locally.

The contract is intentionally minimal: callers pass an iterable plus a
token-estimator callable (so the helper stays decoupled from any
tokenizer — adapters can wrap tiktoken, anthropic, or a cheap
``len()/4`` heuristic). The first item is always retained even if it
alone exceeds the budget, matching the "never silently drop the head
element the caller already selected" rule from RAG-Anything M22.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from ragbot.shared.constants import (
    DEFAULT_MAX_TOKENS_TOTAL,
    DEFAULT_OUTPUT_TOKENS_PER_RESPONSE,
)

T = TypeVar("T")


def compute_effective_max_tokens(
    *,
    system_max_tokens: int,
    bot_extra_max_tokens: int,
) -> int:
    """Effective monthly quota = platform default + paid extra.

    @return int >= 1 (clamps to fallback if system_max malformed).
    """
    base = system_max_tokens if system_max_tokens >= 1 else DEFAULT_MAX_TOKENS_TOTAL
    extra = bot_extra_max_tokens if bot_extra_max_tokens >= 0 else 0
    return base + extra


def compute_output_cap(
    *,
    system_output_default: int,
    bot_extra_output: int,
) -> int:
    """Per-response output cap = platform default + paid extra.

    @return int >= 1.
    """
    base = (
        system_output_default
        if system_output_default >= 1
        else DEFAULT_OUTPUT_TOKENS_PER_RESPONSE
    )
    extra = bot_extra_output if bot_extra_output >= 0 else 0
    return base + extra


def can_answer(
    *,
    tokens_used: int,
    effective_limit: int,
    bypass: bool,
) -> bool:
    """Pre-call gate. bypass=True short-circuits to allow.

    @param tokens_used: cumulative tokens used this period (>= 0).
    @param effective_limit: from compute_effective_max_tokens().
    @param bypass: bots.bypass_token_check flag (Boolean NOT NULL DEFAULT false).
    """
    if bypass:
        return True
    return tokens_used < effective_limit


def is_just_depleted(
    *,
    tokens_used_before: int,
    tokens_used_after: int,
    effective_limit: int,
) -> bool:
    """Detect crossing the limit threshold (one-shot notify trigger).

    True iff the call that just finished drove tokens_used from
    BELOW limit to AT-OR-ABOVE limit. Subsequent calls (also above
    limit) return False so spam notify is avoided.
    """
    return tokens_used_before < effective_limit <= tokens_used_after


def truncate_to_token_budget(
    items: Iterable[T],
    *,
    budget: int,
    token_estimator: Callable[[T], int],
) -> list[T]:
    """Return the longest prefix of ``items`` fitting within ``budget``.

    Inspired by RAG-Anything M22 (``raganything/modalprocessors.py`` —
    ``ContextExtractor._truncate_context``). Used by ``neighbor_expand``
    in the query graph and any future node that grows a list of context
    blocks until a token cap is reached.

    Contract:
      - Iteration stops as soon as adding the next item would exceed
        ``budget``. The helper is **streaming-friendly** — generators
        are consumed only as far as needed.
      - The **first** yielded item is always included, even if it alone
        exceeds the budget. This honours the caller's relevance filter
        (the head element is presumed to be the highest-priority pick)
        and avoids the surprising "empty result despite non-empty
        input" trap.
      - Estimator return values are clamped to ``>= 0`` so a buggy
        estimator returning a negative number cannot grow the budget.

    Args:
        items: Source iterable of context blocks (or any T).
        budget: Maximum cumulative token count (int). May be ``0`` —
            in which case only the first item is returned (graceful
            "always include head" rule above).
        token_estimator: Callable mapping each item to an integer
            token count. Should be cheap; called once per inspected
            item in order.

    Returns:
        A new ``list[T]`` — never the same object as ``items``.

    Example::

        chunks = retrieve_more()
        keep = truncate_to_token_budget(
            chunks, budget=1500, token_estimator=lambda c: len(c.text) // 4
        )
    """
    result: list[T] = []
    accumulated = 0
    for i, item in enumerate(items):
        tokens = max(0, int(token_estimator(item)))
        if i == 0:
            result.append(item)
            accumulated += tokens
            continue
        if accumulated + tokens <= budget:
            result.append(item)
            accumulated += tokens
        else:
            break
    return result


__all__ = [
    "can_answer",
    "compute_effective_max_tokens",
    "compute_output_cap",
    "is_just_depleted",
    "truncate_to_token_budget",
]
