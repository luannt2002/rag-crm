"""Anthropic Haiku Batch API client.

Narrate-then-Embed cost-optimisation path. Anthropic's
**Message Batches API** (https://docs.anthropic.com/en/docs/build-with-claude/batch-processing)
offers a **50% discount** on input + output tokens for jobs that tolerate
a 24-hour SLA. Document ingest is exactly that workload: a corpus refresh
linearises hundreds-to-thousands of TABLE / FORMULA / IMAGE blocks; the
embed step only needs the narration BEFORE the user queries arrive, not
synchronously while the ingest worker is blocked.

Workflow:
    1. ``submit(items)`` — pack up to 100 narration prompts into one batch
       request and POST to ``/v1/messages/batches``. Returns a
       ``batch_id`` and a ``processing_status`` (``in_progress``).
    2. ``poll(batch_id)`` — GET ``/v1/messages/batches/{batch_id}`` and
       return the current status; ``ended`` means results are downloadable.
    3. ``fetch_results(batch_id)`` — stream the results JSONL and yield
       ``(custom_id, content)`` tuples back to the ingest pipeline.

Cost math (paper-claim, narrate-only path):
    100 chunks × ~200 input tok × $0.80/M + 100 × ~80 output tok × $4.00/M
        ≈ $0.016 + $0.032 = $0.048    (non-batch)
    With 50% Batch discount                ≈ $0.024 per 100 chunks
    AdapChunk plan target: ≤ $0.005 per 100 chunks via input-prompt-caching
    + batched submissions; that target is documented as a goal — actual
    cost depends on input length distribution per corpus.

Default OFF. The platform exposes the client behind a feature flag
(``narrate_use_batch_api``) and the DI container instantiates a
``NullAnthropicHaikuBatchClient`` until the operator opts in. We never
auto-submit to a paid API; HALLU=0 isn't directly at risk here, but
silent paid-API enrolment is a separate trust violation.

This module is intentionally a **thin client skeleton**: it declares the
Protocol + Null Object + the cost-accounting helpers so the rest of the
narrate stack can be wired and tested today, with the live HTTP impl
added later behind the same Protocol. Adding the real client = drop a
new class in this file (or a sibling file) and register in DI; no edits
to the caller side.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

from ragbot.shared.constants import (
    DEFAULT_NARRATE_BATCH_SIZE,
    DEFAULT_NARRATE_BATCH_USE,
    DEFAULT_NARRATE_BATCH_DISCOUNT_FACTOR,
)
from ragbot.shared.types import BlockType

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NarrateBatchItem:
    """One narration request inside a batch.

    @param custom_id: caller-chosen id echoed back in the results so the
        ingest pipeline can correlate a row in the JSONL response with the
        chunk metadata waiting on disk. Typical pattern: ``str(chunk.id)``.
    @param block_type: which narration prompt template to apply.
    @param content: raw block content (markdown table / LaTeX / OCR text).
    """

    custom_id: str
    block_type: BlockType
    content: str


@dataclass(frozen=True, slots=True)
class NarrateBatchResultItem:
    """One narration response from a batch.

    @param custom_id: echoes the request's ``custom_id``.
    @param content: the LLM-narrated text, or the empty string when the
        per-item request failed inside the batch (the caller falls back
        to the raw chunk — HALLU=0 sacred).
    @param succeeded: True iff the per-item request returned non-empty
        content. The caller uses this to decide whether to persist the
        narration or fall back to the raw content.
    """

    custom_id: str
    content: str
    succeeded: bool


@dataclass(frozen=True, slots=True)
class NarrateBatchStatus:
    """Polled status of a submitted batch.

    @param batch_id: server-assigned identifier (echo of submit return).
    @param processing_status: one of ``"in_progress"`` | ``"ended"`` |
        ``"canceled"`` | ``"expired"``.
    @param succeeded_count: number of per-item requests with a result.
    @param errored_count: number of per-item requests that failed.
    @param ended: convenience boolean — True when results are downloadable.
    """

    batch_id: str
    processing_status: str
    succeeded_count: int
    errored_count: int
    ended: bool


@runtime_checkable
class AnthropicHaikuBatchClientPort(Protocol):
    """Contract for Anthropic Message Batch clients.

    A real adapter wraps the HTTP API; the Null Object below records
    no-op metrics so ingest can run end-to-end with the flag OFF.
    """

    async def submit(self, items: Sequence[NarrateBatchItem]) -> str:
        """Submit a batch and return its ``batch_id``."""
        ...

    async def poll(self, batch_id: str) -> NarrateBatchStatus:
        """Return current status for ``batch_id``."""
        ...

    async def fetch_results(
        self, batch_id: str
    ) -> AsyncIterator[NarrateBatchResultItem]:
        """Stream per-item results for an ended batch."""
        ...


class NullAnthropicHaikuBatchClient:
    """No-op Batch client — default OFF.

    Records the call shape for telemetry but does NOT contact any
    external API. Used when ``narrate_use_batch_api`` is False or when
    the real adapter has not been wired in DI yet.

    Operators flip ``narrate_use_batch_api`` to True AND swap this
    class for the live adapter in the DI container; we never auto-call
    a paid API.
    """

    def __init__(self, *, batch_size: int = DEFAULT_NARRATE_BATCH_SIZE) -> None:
        if batch_size <= 0:
            raise ValueError(
                f"batch_size must be positive, got {batch_size!r}"
            )
        self._batch_size = batch_size

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def submit(self, items: Sequence[NarrateBatchItem]) -> str:
        """Pretend-submit: log shape, return a stable ``"null:<n>"`` id.

        The caller can still exercise the poll → fetch path against this
        Null object in tests / dry runs without paying for the live API.
        """
        n = len(items)
        logger.info(
            "anthropic_haiku_batch_submit_null",
            n_items=n,
            batch_size_cap=self._batch_size,
            step_name="narrate_batch_submit",
            feature_flag="narrate_use_batch_api",
        )
        if n > self._batch_size:
            # The real API caps at 100 messages per batch; we surface the
            # same constraint here so callers get an early failure rather
            # than a 4xx at submit time.
            raise ValueError(
                f"batch exceeds size cap: {n} > {self._batch_size}"
            )
        return f"null:{n}"

    async def poll(self, batch_id: str) -> NarrateBatchStatus:
        """Null poll: report the batch as immediately ``ended`` with zero work."""
        return NarrateBatchStatus(
            batch_id=batch_id,
            processing_status="ended",
            succeeded_count=0,
            errored_count=0,
            ended=True,
        )

    async def fetch_results(
        self, batch_id: str
    ) -> AsyncIterator[NarrateBatchResultItem]:
        """Null fetch: yields nothing — caller falls back to raw chunks."""
        logger.debug(
            "anthropic_haiku_batch_fetch_null",
            batch_id=batch_id,
            step_name="narrate_batch_fetch",
            feature_flag="narrate_use_batch_api",
        )
        if False:  # pragma: no cover — empty async generator pattern
            yield NarrateBatchResultItem(
                custom_id="",
                content="",
                succeeded=False,
            )


def estimate_batch_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_million_usd: float,
    output_price_per_million_usd: float,
    use_batch_discount: bool = DEFAULT_NARRATE_BATCH_USE,
    discount_factor: float = DEFAULT_NARRATE_BATCH_DISCOUNT_FACTOR,
) -> float:
    """Compute the (possibly discounted) cost of a narration batch.

    Helper for the cost-audit dashboard. Pricing is passed in (not
    hard-coded model rates) so a model swap doesn't require touching
    this module — the DI container resolves model rates from
    ``system_config.model_prices`` per the platform pricing policy.

    @param input_tokens: total input tokens across the batch.
    @param output_tokens: total output tokens across the batch.
    @param input_price_per_million_usd: per-million-token input price.
    @param output_price_per_million_usd: per-million-token output price.
    @param use_batch_discount: when True, multiply the gross cost by
        ``discount_factor`` (Anthropic Batch API discount).
    @param discount_factor: usually 0.5 (50% off). Constants pin the
        platform default; callers may override per cost-audit scenario.
    @return: cost in USD.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    if input_price_per_million_usd < 0 or output_price_per_million_usd < 0:
        raise ValueError("prices must be non-negative")
    if not 0.0 < discount_factor <= 1.0:
        raise ValueError("discount_factor must lie in (0, 1]")

    per_token_input = input_price_per_million_usd / 1_000_000.0
    per_token_output = output_price_per_million_usd / 1_000_000.0
    gross = input_tokens * per_token_input + output_tokens * per_token_output
    return gross * discount_factor if use_batch_discount else gross


__all__ = [
    "AnthropicHaikuBatchClientPort",
    "NarrateBatchItem",
    "NarrateBatchResultItem",
    "NarrateBatchStatus",
    "NullAnthropicHaikuBatchClient",
    "estimate_batch_cost_usd",
]
