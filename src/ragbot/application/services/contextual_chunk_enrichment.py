"""Contextual Retrieval — per-chunk LLM enrichment (Anthropic 2024-09).

Each chunk is rewritten with a short (50-100 token)
context prefix that situates it inside the parent document, then embedded.
Anthropic's published study shows -49% retrieval failure on a typical RAG
corpus (-67% when combined with a cross-encoder reranker).

Why this differs from ``shared/contextual_enrichment.enrich_chunks``:

* That helper builds a free-form "position summary" using a caller-supplied
  ``llm_fn``. It pre-dates the dedicated cache_control wiring and runs
  per-chunk concurrency from the application layer.
* This service issues ONE litellm call per chunk with the full document as a
  cache-controlled system block. On Anthropic that's a 90% input discount on
  every chunk after the first — making CR practical at ingest scale.
* Output format is fixed (``<chunk_context>...</chunk_context>\\n\\n<chunk>``)
  so downstream consumers can strip the prefix for citation if they want raw
  text back.

Failure mode is non-fatal: any LLM error returns the original chunk unchanged
plus a structured warn log — ingest never blocks on CR.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

from ragbot.shared.anthropic_cache import apply_anthropic_cache_control
from ragbot.shared.constants import (
    DEFAULT_CR_LLM_TIMEOUT_S,
    DEFAULT_CLEANBASE_MAX_WORDS,
    DEFAULT_CLEANBASE_MIN_WORDS,
    DEFAULT_CR_MAX_DOC_CHARS,
)

logger = structlog.get_logger(__name__)


# Prompt blocks. System block is stable across every chunk of the same
# document → eligible for Anthropic ephemeral prompt cache (90% discount on
# every read after the first within the 5-min TTL).
_SYSTEM_PROMPT_TEMPLATE = (
    "You produce a short context label for a single chunk of a larger "
    "document so that semantic search can disambiguate it.\n\n"
    "Rules:\n"
    "- Output ONLY the context label. No preamble, no quotes, no markdown.\n"
    "- Stay within {max_tokens} tokens.\n"
    "- Identify the section/topic of the chunk inside the document and any "
    "entity it depends on (e.g. which product, which policy, which date).\n"
    "- Use the same primary language as the document.\n\n"
    "<full_document>\n{full_doc}\n</full_document>"
)
_USER_PROMPT_TEMPLATE = (
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Write the context label for this chunk:"
)

# Output wrapping format — kept stable so downstream tooling (citation, audit,
# A/B benchmarks) can rely on it.
_OUTPUT_TEMPLATE = "<chunk_context>{context}</chunk_context>\n\n{chunk}"


def _wrap_with_context(chunk: str, context: str) -> str:
    """Render the CR-enriched chunk in the canonical output format."""
    return _OUTPUT_TEMPLATE.format(context=context.strip(), chunk=chunk)


def _build_messages(
    *,
    chunk: str,
    full_doc: str,
    max_context_tokens: int,
) -> list[dict[str, Any]]:
    """Build the (system, user) message pair for one CR call."""
    system = _SYSTEM_PROMPT_TEMPLATE.format(
        max_tokens=max_context_tokens,
        full_doc=full_doc,
    )
    user = _USER_PROMPT_TEMPLATE.format(chunk=chunk)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def enrich_chunk_with_context(
    chunk: str,
    full_doc: str,
    *,
    model_id: str,
    max_context_tokens: int,
    prompt_cache_enabled: bool,
    max_doc_chars: int = DEFAULT_CR_MAX_DOC_CHARS,
    provider_code: str | None = None,
    litellm_module: Any | None = None,
) -> str:
    """Rewrite ``chunk`` with a short CR context prefix.

    @param chunk: chunk text — returned unchanged on any failure path.
    @param full_doc: parent document text (cached as system block on Anthropic).
    @param model_id: litellm model id (cfg-driven; provider-prefixed wire name).
    @param max_context_tokens: hard cap on the LLM ``max_tokens`` budget.
    @param prompt_cache_enabled: when True and provider routes to Anthropic,
        wrap the system block in ``cache_control: ephemeral``.
    @param max_doc_chars: skip CR (return original chunk) when the source
        document is longer than this — ingest cost guard.
    @param provider_code: provider code from ``ai_providers.code`` if known;
        cache_control is a no-op for non-Anthropic providers.
    @param litellm_module: dependency-injected litellm module for tests; falls
        back to the real ``litellm`` import at call-time.
    @return: ``"<chunk_context>{context}</chunk_context>\\n\\n{chunk}"`` on
        success, else the original ``chunk`` unchanged.

    Never raises — LLM failure is logged at WARN and the original chunk is
    returned so ingest can continue.
    """
    # Empty / whitespace inputs: nothing to enrich.
    if not chunk or not chunk.strip():
        return chunk
    if not full_doc or not full_doc.strip():
        return chunk

    # Cost guard — large docs would torch the per-chunk LLM budget even with
    # prompt cache. Defer to ops to chunk the doc differently or raise the cap
    # via system_config.
    if len(full_doc) > max_doc_chars:
        logger.info(
            "cr_skip_doc_too_long",
            doc_chars=len(full_doc),
            max_doc_chars=max_doc_chars,
        )
        return chunk

    messages = _build_messages(
        chunk=chunk,
        full_doc=full_doc,
        max_context_tokens=max_context_tokens,
    )

    if prompt_cache_enabled:
        messages = apply_anthropic_cache_control(
            messages,
            litellm_name=model_id,
            provider_code=provider_code,
        )

    # AdapChunk-reorg Wave F1: audit Anthropic prompt-cache application so the
    # Phần 16.7 cost-saving claim (-60-90% on CR re-runs over the same doc) is
    # measurable in structlog. Logged regardless of provider; downstream
    # ``apply_anthropic_cache_control`` no-ops on non-Anthropic providers, but
    # the request-side flag still reflects operator intent.
    logger.info(
        "anthropic_cache_request",
        cache_control_applied=prompt_cache_enabled,
        full_doc_chars=len(full_doc),
        chunk_chars=len(chunk),
        prompt_cache_enabled=prompt_cache_enabled,
        model_id=model_id,
        provider_code=provider_code,
    )

    if litellm_module is None:
        try:
            import litellm as litellm_module  # type: ignore[no-redef]
        except ImportError:
            logger.warning("cr_litellm_unavailable")
            return chunk

    try:
        # Hard timeout (CRIT audit 2026-06-13): a hung provider await must not
        # pin a semaphore slot forever and stall the document. TimeoutError is
        # caught by the broad ``except`` below → returns the un-enriched chunk.
        async with asyncio.timeout(DEFAULT_CR_LLM_TIMEOUT_S):
            resp = await litellm_module.acompletion(
                model=model_id,
                messages=messages,
                max_tokens=max_context_tokens,
            )
        # AdapChunk-reorg Wave F1: surface cache_creation / cache_read counters
        # from the provider response so dashboards can compute hit-ratio per
        # ingest run (Phần 16.7 cost-savings audit trail).
        # Provider-agnostic cache accounting. Anthropic reports cache hits as
        # ``cache_read_input_tokens`` on the usage root; OpenAI reports them as
        # ``prompt_tokens_details.cached_tokens`` (auto prefix cache, no
        # cache_control needed — verified 2026-06-13: gpt-4.1-mini caches ~98.5%
        # of the full-document CR prefix). Reading only the Anthropic field made
        # the OpenAI hit-rate read as 0 even though caching was active.
        _usage = getattr(resp, "usage", None)

        def _u(field: str) -> int:
            if isinstance(_usage, dict):
                return int(_usage.get(field, 0) or 0)
            if _usage is not None:
                return int(getattr(_usage, field, 0) or 0)
            return 0

        _ptd = (
            _usage.get("prompt_tokens_details")
            if isinstance(_usage, dict)
            else getattr(_usage, "prompt_tokens_details", None)
        )
        if isinstance(_ptd, dict):
            _openai_cached = int(_ptd.get("cached_tokens", 0) or 0)
        elif _ptd is not None:
            _openai_cached = int(getattr(_ptd, "cached_tokens", 0) or 0)
        else:
            _openai_cached = 0

        _cache_creation = _u("cache_creation_input_tokens")  # Anthropic write
        _cache_read = _u("cache_read_input_tokens")          # Anthropic read
        _prompt_tokens = _u("prompt_tokens")
        # Unified hit = whichever provider populated its cache-read counter.
        _cache_read_unified = max(_cache_read, _openai_cached)
        if _prompt_tokens > 0:
            _hit_ratio = round(_cache_read_unified / _prompt_tokens, 3)
        elif (_cache_read + _cache_creation) > 0:
            _hit_ratio = round(_cache_read / (_cache_read + _cache_creation), 3)
        else:
            _hit_ratio = 0.0
        logger.info(
            "anthropic_cache_response",
            cache_creation_input_tokens=_cache_creation,
            cache_read_input_tokens=_cache_read,
            openai_cached_tokens=_openai_cached,
            prompt_tokens=_prompt_tokens,
            cache_hit=_cache_read_unified > 0,
            cache_hit_ratio=_hit_ratio,
            model_id=model_id,
        )
        context = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — non-fatal by design
        logger.warning(
            "cr_llm_failed",
            model_id=model_id,
            error=str(exc),
            chunk_chars=len(chunk),
        )
        return chunk

    if not context:
        logger.warning("cr_llm_empty_context", model_id=model_id)
        return chunk

    return _wrap_with_context(chunk, context)


# Generic vocabulary heuristic — broad VN + EN tokens that signal numeric
# content is *expected* in the chunk (price/cost/age/count). Keep generic so
# the rule stays domain-neutral; per-tenant terms belong in
# ``custom_vocabulary``, not in this regex.
_NUMERIC_EXPECTED_PATTERN = re.compile(
    r"\b(giá|cost|price|số|tuổi)\b",
    re.IGNORECASE,
)
# Last-token shape: must contain >=1 alphabetic char and total length above
# the truncation guard to count as a non-truncated word. Matches ASCII +
# Unicode letters via re.UNICODE (default on str patterns in Py3) so VN
# diacritics are accepted.
_ALPHA_TOKEN_PATTERN = re.compile(r"[^\W\d_]")
# Truncation guard: last-token length strictly greater than this is treated
# as "looks like a real word". Two-char fragments are common mid-word slice
# artifacts ("ch", "ng", "tr") so the gate is set above that.
_MIN_LAST_TOKEN_LEN = 2
# Per-component weight - four components at 0.25 each = 1.0 max. Each
# component is binary (present / absent) so the score is interpretable:
# 0.25-step buckets.
_QUALITY_COMPONENT_WEIGHT = 0.25


def score_chunk_quality(chunk: str) -> float:
    """Heuristic 0.0-1.0 quality score for a single chunk.

    @param chunk: post-enrichment chunk text. Whitespace-only / empty inputs
        score 0.0 (no signal to grade).
    @return: float in ``[0.0, 1.0]`` rounded by component aggregation.

    Components (each contributes 0.25 when satisfied):

    * Sentence completeness — text ends with a terminal punctuation mark
      (``.``, ``!``, ``?``).
    * Word count in ``[DEFAULT_CLEANBASE_MIN_WORDS, DEFAULT_CLEANBASE_MAX_WORDS]``
      — under-shot = sparse fragment, over-shot = parser leak / unsplit page.
    * Last-token shape — final whitespace-delimited token has at least one
      alphabetic char AND length above ``_MIN_LAST_TOKEN_LEN``; protects
      against trailing slice cuts (``... a partial wo``) common when
      char-based chunkers split mid-word.
    * Numeric expectation — when generic VN/EN ``giá|cost|price|số|tuổi``
      keyword is present, require at least one digit; otherwise this
      component is *neutral* (no penalty when no number is expected).

    Pure function — observability only, NEVER rejects a chunk. Caller decides
    what to log / persist with the score.
    """
    if not chunk or not chunk.strip():
        return 0.0

    score = 0.0
    text = chunk.strip()

    # 1. Sentence completeness — last non-whitespace char terminal punctuation.
    if text[-1] in ".!?":
        score += _QUALITY_COMPONENT_WEIGHT

    # 2. Word count in the configured band. Whitespace split is good enough;
    # exact tokenizer choice doesn't matter at 0.25 granularity.
    tokens = text.split()
    n_words = len(tokens)
    if DEFAULT_CLEANBASE_MIN_WORDS <= n_words <= DEFAULT_CLEANBASE_MAX_WORDS:
        score += _QUALITY_COMPONENT_WEIGHT

    # 3. Last-token shape — strip trailing punctuation before checking, since
    # a clean sentence ends in ``.`` / ``!`` / ``?`` which would otherwise
    # mask the alphabetic-content signal we actually want to verify.
    if tokens:
        last_clean = tokens[-1].rstrip(".!?,;:)\"]'…")
        if (
            len(last_clean) > _MIN_LAST_TOKEN_LEN
            and _ALPHA_TOKEN_PATTERN.search(last_clean) is not None
        ):
            score += _QUALITY_COMPONENT_WEIGHT

    # 4. Numeric expectation — only graded when a numeric-keyword is present.
    # Absence of keyword = neutral (no add, no penalty); presence of keyword
    # WITHOUT a digit = no add (signal of stripped numeric content).
    if _NUMERIC_EXPECTED_PATTERN.search(text) and any(
        ch.isdigit() for ch in text
    ):
        score += _QUALITY_COMPONENT_WEIGHT

    # Defensive clamp — components sum to <=1.0 by construction, but explicit
    # bounds make the contract obvious to readers and to mypy.
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def emit_chunk_quality_event(
    *,
    score: float,
    threshold: float,
    chunk_index: int,
    document_title: str,
) -> None:
    """Emit ``chunk_quality_below_threshold`` structured warn event.

    Observability-only — caller decides whether to invoke (typically when
    ``score < threshold``). Centralised here so the event name + field schema
    are owned by the scoring module and remain stable across callers.
    """
    logger.warning(
        "chunk_quality_below_threshold",
        score=score,
        threshold=threshold,
        chunk_index=chunk_index,
        document_title=document_title,
    )


__all__ = [
    "emit_chunk_quality_event",
    "enrich_chunk_with_context",
    "score_chunk_quality",
]
