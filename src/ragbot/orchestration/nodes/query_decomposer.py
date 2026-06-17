"""Adaptive Router — Layer 3: LLM-based query decomposer (DOMAIN-NEUTRAL).

Splits a compound user question into independent atomic sub-questions
when Layer 1 (``query_complexity.classify_query_complexity``) flags
the query as ``complex``. The output is written into graph state under
the ``sub_queries`` key; the existing retrieve / fanout pipeline (S2
bypass) consumes that contract — multiple sub-queries trigger per-
branch retrieval, single-item lists fall through to the standard path.

Domain-neutral contract:
- The decomposer prompt MUST NOT mention any domain ("legal", "medical",
  "ecommerce", ...). It instructs the LLM in *linguistic* terms only:
  conjunction split, pronoun resolution, language preservation.
- Default model is ``gpt-4.1-mini`` (admin override 2026-05-12: Haiku
  banned). Bot owner overrides via ``system_config.decomposer.model``.
- All numeric knobs (max tokens, max sub-queries, enabled flag) resolve
  via ``get_boot_config`` (TTL-cached); constants.py defaults are the
  fallback floor.

Strategy + DI:
- The decomposer accepts an injected ``llm_invoker`` callable so unit
  tests stub the LLM without touching infra. Production wires the
  callable through ``ragbot.application.ports.llm_port.LLMPort`` inside
  the graph node (see ``query_graph.py``).

Failure mode: ANY exception or unparsable LLM response → identity
fallback (``[query]``). Never raise from inside the decomposer — the
upstream retrieve path stays functional even when the LLM is degraded.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.constants import (
    DEFAULT_DECOMPOSER_ENABLED,
    DEFAULT_DECOMPOSER_MAX_SUB_QUERIES,
    DEFAULT_DECOMPOSER_MAX_TOKENS,
    DEFAULT_DECOMPOSER_MODEL,
)

logger = structlog.get_logger(__name__)


# Domain-neutral system prompt. Mentions no industry, no jurisdiction,
# no document type. Linguistic instructions + structural entity examples
# only — examples avoid brand/customer/product names so the prompt remains
# tenant-agnostic per the platform's domain-neutral rule.
DECOMPOSER_SYSTEM_PROMPT: str = (
    "You are a query decomposer for a retrieval system.\n"
    "Split the user's compound question into independent atomic sub-questions.\n"
    "\n"
    "Rules:\n"
    "- Each sub-question must stand alone (no pronoun reference).\n"
    "- If the query references MULTIPLE entities (numbers/names/identifiers),\n"
    "  split EACH entity into a separate sub-question.\n"
    "- If the query is already single-intent, return a single-item array.\n"
    "- Be aggressive: over-split is safer than under-split.\n"
    "- Preserve the original language.\n"
    "\n"
    "Examples (structural identifiers — domain-neutral):\n"
    "Input: \"X and Y in document A\"\n"
    'Output: {"sub_queries": ["X in document A", "Y in document A"]}\n'
    "Input: \"X, Y, Z in document A\"\n"
    'Output: {"sub_queries": ["X in document A", "Y in document A", "Z in document A"]}\n'
    "Input: \"Compare A and B\"\n"
    'Output: {"sub_queries": ["What is A", "What is B"]}\n'
    "Input: \"What does X say\" (single entity)\n"
    'Output: {"sub_queries": ["What does X say"]}\n'
    "\n"
    "Output JSON only:\n"
    '{"sub_queries": ["q1", "q2", ...]}'
)


# Strategy + DI hooks. Tests stub these with deterministic doubles.
ConfigGetter = Callable[[str, Any], Any]
# LLMInvoker is a pure async callable: given (system, user, model,
# max_tokens) it returns the raw model text (the JSON envelope).
LLMInvoker = Callable[..., Awaitable[str]]


def _coerce_bool(raw: Any, fallback: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes", "on"}
    return fallback


def _coerce_int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool):  # bool is int subclass — exclude first
        return fallback
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return fallback
    return fallback


def _parse_decomposer_response(raw_text: str, *, fallback_query: str, cap: int) -> list[str]:
    """Extract ``sub_queries`` from the LLM JSON envelope with guard rails."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return [fallback_query]
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return [fallback_query]
    if not isinstance(parsed, dict):
        return [fallback_query]
    subs = parsed.get("sub_queries")
    if not isinstance(subs, list) or not subs:
        return [fallback_query]
    cleaned: list[str] = []
    for item in subs[: max(cap, 0)]:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned if cleaned else [fallback_query]


async def decompose_query(
    query: str,
    *,
    llm_invoker: LLMInvoker,
    config_getter: ConfigGetter | None = None,
) -> list[str]:
    """Split ``query`` into atomic sub-questions or return ``[query]``.

    The decomposer is DOMAIN-NEUTRAL. The injected ``llm_invoker`` is
    expected to call the platform LLM with the system prompt + user
    query and return the raw model text. Production wires this through
    ``LLMPort.complete``; unit tests pass in a stub.

    Idempotent + safe: any failure path returns ``[query]`` so the
    upstream retrieve pipeline stays functional.
    """
    getter: ConfigGetter = config_getter or get_boot_config

    enabled = _coerce_bool(
        getter("decomposer.enabled", DEFAULT_DECOMPOSER_ENABLED),
        DEFAULT_DECOMPOSER_ENABLED,
    )
    if not enabled:
        return [query]
    if not isinstance(query, str) or not query.strip():
        return [query if isinstance(query, str) else ""]

    model = str(
        getter("decomposer.model", DEFAULT_DECOMPOSER_MODEL)
        or DEFAULT_DECOMPOSER_MODEL
    )
    max_tokens = _coerce_int(
        getter("decomposer.max_tokens", DEFAULT_DECOMPOSER_MAX_TOKENS),
        DEFAULT_DECOMPOSER_MAX_TOKENS,
    )
    cap = _coerce_int(
        getter("decomposer.max_sub_queries", DEFAULT_DECOMPOSER_MAX_SUB_QUERIES),
        DEFAULT_DECOMPOSER_MAX_SUB_QUERIES,
    )

    try:
        raw_text = await llm_invoker(
            system=DECOMPOSER_SYSTEM_PROMPT,
            user=query,
            model=model,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — decomposer is graceful-degrade by contract
        # structlog kwargs, NOT `extra=` dict — `extra=` is stdlib-logging
        # syntax that the structlog ProcessorFormatter foreign_pre_chain
        # does not surface, so the fields silently disappeared from the
        # JSON event body (see plans/260515.../issue-4). Without the
        # error_type+model fields ops cannot diagnose why decompose fails.
        logger.warning(
            "decomposer_llm_call_failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            model=model,
        )
        return [query]

    return _parse_decomposer_response(raw_text, fallback_query=query, cap=cap)


__all__ = [
    "ConfigGetter",
    "DECOMPOSER_SYSTEM_PROMPT",
    "LLMInvoker",
    "decompose_query",
]
