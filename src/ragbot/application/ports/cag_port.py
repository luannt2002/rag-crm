"""CAG Port — contract for Cache-Augmented Generation strategy implementations.

Cache-Augmented Generation (CAG) Mode.

Citation: Chan et al. 2024 — "Don't Do RAG: When Cache-Augmented Generation
is All You Need for Knowledge Tasks" (https://arxiv.org/abs/2412.15605,
ACM Web 2025 peer-reviewed). Reported 10.9-40.5x latency reduction vs RAG
on small knowledge bases that fit inside the model context window.

Design rationale
----------------
Standard RAG embeds + retrieves chunks per turn — that hot path is built
for KBs of arbitrary size. For SMALL corpora (under ~80K tokens) the
retrieve/rerank cost dominates while adding no recall benefit: the LLM
could simply read the whole corpus once and answer every question from
prompt cache. CAG is that alternative path.

The Port deliberately splits the decision into two methods:

    should_engage(...)        — pure gating predicate (no I/O on hot path)
    build_corpus_payload(...) — load corpus + emit cache-ready prompt block

so callers can short-circuit BEFORE touching the corpus loader when the
flag is OFF or the corpus is too large. This keeps the per-turn overhead
of an OFF deployment at exactly one function call (the Null adapter).

Owner-opt-in
------------
The platform exposes the Port + Registry but never auto-enables CAG.
Operators flip ``system_config.cag_mode_enabled`` (tenant-wide) or bot
owners flip ``bots.plan_limits.cag_mode_enabled`` (per-bot); otherwise the
default ``NullCAGService`` returns ``should_engage=False`` so the retrieve
hot path runs identically to today.

Implementations
---------------
- ``NullCAGService``      — always returns False (default OFF baseline).
- ``AnthropicCAGService`` — gates by corpus token count, returns the corpus
                            as a single ``cache_control: ephemeral`` block
                            for Anthropic prompt-cache reuse.

HALLU=0 sacred
--------------
CAG never invents facts: the corpus block is the ground truth. The
strategy MUST emit ``should_engage=False`` whenever:
    - the feature flag is OFF
    - the corpus is empty
    - the corpus exceeds ``cag_max_corpus_tokens``
    - the corpus loader raises any error

so the query falls back to RAG instead of asking the LLM to answer from
parametric memory alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ragbot.shared.types import TenantId


@dataclass(frozen=True, slots=True)
class CAGPayload:
    """Cache-ready corpus block for prompt injection.

    @param corpus_text: the full text of the bot's knowledge base, ready
        to be wrapped in the LLM system prompt. The implementation has
        ALREADY validated this fits below the configured token ceiling.
    @param corpus_tokens: measured token count of ``corpus_text`` (used
        for observability — what the gate let through).
    @param cache_breakpoint: opaque hint to the LLM adapter that the
        corpus block should carry a prompt-cache marker
        (e.g. Anthropic ``cache_control: ephemeral``). The adapter may
        ignore this for providers without explicit cache control.
    """

    corpus_text: str
    corpus_tokens: int
    cache_breakpoint: bool = True


@runtime_checkable
class CAGServicePort(Protocol):
    """Decide whether to bypass retrieval and inject the full corpus.

    The Port stays minimal — it does NOT generate the answer. Its job is
    purely the gating + payload-shaping decision. The query graph wires
    the payload into the LLM call when ``should_engage`` is True.

    All methods MUST be safe to call without holding any DB session
    open — implementations open their own short-lived session if needed
    so the orchestrator stays decoupled from persistence concerns.
    """

    async def should_engage(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: str,
    ) -> bool:
        """Return True iff CAG should replace retrieval for this turn.

        @param record_tenant_id: tenant scope (4-key identity).
        @param record_bot_id: internal bot UUID (resolved upstream from the
            external 4-key tuple).
        @return: True only when feature flag is ON AND corpus token count
            is at-or-below the configured ceiling AND the corpus exists.
            Any error path MUST return False — fall back to RAG, never
            answer from parametric memory.
        """
        ...

    async def build_corpus_payload(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: str,
    ) -> CAGPayload | None:
        """Load + return the corpus payload for prompt injection.

        @param record_tenant_id: tenant scope (4-key identity).
        @param record_bot_id: internal bot UUID.
        @return: ``CAGPayload`` when corpus loaded successfully and fits
            the ceiling; ``None`` if the ceiling check fails or the
            corpus is empty. ``None`` is a hard signal to the caller to
            fall back to RAG.
        @note: implementations MUST NOT raise on missing/empty corpus —
            return ``None`` so the orchestrator can degrade gracefully.
        """
        ...


__all__ = ["CAGPayload", "CAGServicePort"]
