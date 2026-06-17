"""AdapChunk Layer 7 — FORMULA narrator via LLM.

Per AdapChunk debug doc Phan 3.8 + 6.8:
    "FORMULA LaTeX -> LLM mo ta cau chu. Luu LaTeX goc trong
    metadata.original_content."

Cost (Phan 16.7 benchmark): ~$0.00005 per formula with Anthropic Haiku via
the Batch API (50% discount). The LLM is invoked at INGEST time only — the
narrated text is embedded for retrieval recall while the raw LaTeX is kept
in chunk metadata so the answer LLM sees the source of truth (HALLU=0).

Application MINDSET (CLAUDE.md Quality Gate #10):
    - This module is INGEST-side enrichment. It does NOT inject text into
      the answer LLM prompt and it does NOT override an answer.
    - Bot owner's ``system_prompt`` remains the single source of truth at
      query time.

Strategy + DI mindset (CLAUDE.md):
    - The LLM call is dependency-injected via the ``LLMFn`` Protocol. No
      provider class is imported here; the caller passes whichever adapter
      the DI container has built (litellm / anthropic batch / mock / null).
    - Model name comes from the caller (ultimately from
      ``bots.model_id`` or ``system_config``) — no hardcoded model literal
      in this module.

Failure policy: graceful degradation. Any LLM failure logs a structured
warn event and returns the raw LaTeX unchanged — ingest never blocks on
formula narration.
"""
from __future__ import annotations

from typing import Protocol

import structlog

from ragbot.shared.constants import (
    DEFAULT_FORMULA_NARRATE_MAX_TOKENS,
    DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE,
)

logger = structlog.get_logger(__name__)


class LLMFn(Protocol):
    """Minimal LLM invocation contract for formula narration.

    The Protocol is intentionally narrow: a single async call taking the
    fully-rendered prompt plus the technical parameters the caller has
    already resolved (model id, token cap, batch flag). Keeping it narrow
    means any LLM adapter can satisfy it — litellm, Anthropic Batch
    adapter, in-memory mock, NullObject — without inheritance.

    The contract is sysprompt-only / no answer override: the caller MUST
    NOT inject application-side text into the prompt at the answer LLM
    layer (Quality Gate #10). This Protocol is for the INGEST-side
    narration LLM only.
    """

    async def __call__(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        batch: bool = False,
    ) -> str:
        ...


async def narrate_formula(
    latex: str,
    *,
    llm_fn: LLMFn,
    model: str,
    max_tokens: int = DEFAULT_FORMULA_NARRATE_MAX_TOKENS,
    batch: bool = True,
) -> str:
    """Convert a LaTeX formula to a 1-2 sentence natural-language description.

    @param latex: raw LaTeX source, e.g. ``"$$E = mc^2$$"``. Empty /
        whitespace-only inputs are short-circuited to an empty-string
        return so the caller does not pay an LLM round-trip for nothing.
    @param llm_fn: injected async LLM caller satisfying :class:`LLMFn`.
        Strategy + DI per CLAUDE.md — no provider import in this module.
    @param model: model identifier resolved by the caller from
        ``bots.model_id`` or ``system_config`` (zero-hardcode policy —
        no model literal lives in this function).
    @param max_tokens: per-call output cap. Defaults to
        ``DEFAULT_FORMULA_NARRATE_MAX_TOKENS`` (100) — tighter than the
        generic narrate cap (120) because formula descriptions are
        consistently shorter than table / image narrations.
    @param batch: when True the caller should route through the Anthropic
        Message Batches API (50% discount, ~24h SLA). Passed through to
        ``llm_fn`` as-is; the adapter decides whether to honour it.

    @return: natural-language description on success, raw ``latex``
        unchanged on any LLM failure (graceful degradation — HALLU=0
        sacred). Empty input returns an empty string.

    This function NEVER raises. LLM exceptions are caught broadly and
    logged at WARN; ingest continues with the LaTeX passthrough so the
    embedder still receives the (raw) formula text rather than no chunk
    at all.
    """
    if not latex or not latex.strip():
        # Nothing to narrate — return as-is so downstream sees the same
        # empty / whitespace input the caller passed in. No LLM call.
        return latex

    prompt = DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE.format(latex=latex)

    try:
        narrated = await llm_fn(
            prompt,
            model=model,
            max_tokens=max_tokens,
            batch=batch,
        )
    except Exception as exc:  # noqa: BLE001 — graceful degradation, never block ingest
        logger.warning(
            "formula_narrate_fallback_passthrough",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            latex_len=len(latex),
            model=model,
            batch=batch,
        )
        return latex

    # Strategy contract returns a string. Defensive: empty / whitespace
    # output is treated as "no enhancement" and falls back to raw LaTeX
    # so the embedder never sees a blank chunk in place of real content.
    cleaned = (narrated or "").strip()
    if not cleaned:
        logger.warning(
            "formula_narrate_empty_output_passthrough",
            latex_len=len(latex),
            model=model,
            batch=batch,
        )
        return latex

    logger.info(
        "formula_narrate_ok",
        latex_len=len(latex),
        narrated_len=len(cleaned),
        model=model,
        batch=batch,
    )
    return cleaned


__all__ = ["LLMFn", "narrate_formula"]
