"""HyDE production wire — application-layer facade over the infrastructure strategy.

This service is the **production wire entry point** for HyDE (Hypothetical
Document Embeddings, Gao et al. 2022). The infrastructure layer ships
``LLMHyDEGenerator`` / ``NullHyDEGenerator`` (raw Port + Strategy + Registry,
see ``ragbot.infrastructure.hyde``) and exposes the plain ``generate(query)``
contract. This application service adds two production-only concerns on top
of that contract WITHOUT touching the strategy classes:

1. **Wall-clock timeout** — ``asyncio.wait_for`` clamps the hypothetical
   generation to ``DEFAULT_HYDE_GENERATION_TIMEOUT_S`` so an upstream LLM
   stall cannot blow the chat turn's p95 budget.
2. **Single call site for the retrieve pipeline** — ``query_graph._embed_query``
   reads the per-bot ``hyde_enabled`` flag and (when ON) delegates here.
   The Port stays narrow; the timeout policy lives in one place.

DI contract:
    ``HyDEGenerator`` is constructed with an injected ``LLMPort`` + the
    pre-resolved ``LLMSpec`` for the platform-default HyDE model. It carries
    no tenant identity at construction time — the per-request tenant/trace
    IDs flow into ``generate_hypothetical_answer`` so the same instance can
    safely be shared across requests (LangGraph nodes hold no per-tenant
    state in compiled-graph closures).

Application MINDSET compliance:
    HyDE rewrites the **retrieval embedding query** only. The hypothetical
    answer text NEVER reaches the answer LLM prompt — see Quality Gate #10
    in ``CLAUDE.md``. ``query_graph`` calls this service *before*
    ``embedder.embed_one`` and then discards the hypothetical text.

Failure contract:
    Every failure path (timeout, LLM adapter error, empty completion,
    HyDE disabled) returns the **original** query so retrieval keeps
    working — HyDE is an enhancement, never a hard dependency.
"""

from __future__ import annotations

import asyncio

import structlog

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMPort
from ragbot.shared.constants import DEFAULT_HYDE_GENERATION_TIMEOUT_S
from ragbot.shared.errors import RetrievalError
from ragbot.shared.types import TenantId, TraceId

logger = structlog.get_logger(__name__)


# Domain-neutral system instruction — no industry literals. Mirrors the
# infrastructure strategy's prompt so behaviour is identical whether a
# caller wires the application service or the raw infrastructure class.
_HYDE_SYSTEM_INSTRUCTION = (
    "You are a domain-agnostic retrieval helper. Given a question, write a "
    "SHORT (50-100 words) hypothetical answer that would likely appear in a "
    "relevant document.\n\n"
    "Rules:\n"
    "- Use declarative style (statement, not question).\n"
    "- Stay close to the query topic; do NOT invent facts or numbers.\n"
    "- Preserve the user's language exactly.\n"
    "- Output: just the hypothetical answer text, no preamble or hedging."
)


class HyDEGenerator:
    """Application-layer HyDE facade with bounded latency.

    @param llm: shared ``LLMPort`` — typically the platform's cheap/fast tier
        (e.g. ``gpt-4.1-mini``); never the answer-LLM router. The Port
        contract is honoured exactly; no provider-specific branching here.
    @param timeout_s: wall-clock ceiling on a single ``generate`` call;
        defaults to ``DEFAULT_HYDE_GENERATION_TIMEOUT_S``. ``asyncio.wait_for``
        cancels the underlying LLM task on overrun and the caller receives
        the original query.
    """

    def __init__(
        self,
        *,
        llm: LLMPort,
        timeout_s: float = DEFAULT_HYDE_GENERATION_TIMEOUT_S,
    ) -> None:
        self._llm = llm
        self._timeout_s = float(timeout_s)

    async def generate_hypothetical_answer(
        self,
        query: str,
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> str:
        """Return a hypothetical answer to ``query`` for retrieval embedding.

        The caller (typically ``query_graph._embed_query``) embeds the
        returned text INSTEAD of the raw query. On any failure path the
        original ``query`` is returned verbatim so the embedder still gets
        a non-empty input and retrieval continues.

        @param query: raw user query (post-language-normalisation upstream).
        @param spec: pre-resolved ``LLMSpec`` for the HyDE model (carries
            ``max_tokens`` + ``temperature`` from constants / system_config).
        @param record_tenant_id: tenant scope for the LLM call.
        @param trace_id: distributed trace id forwarded into the LLM call.
        @return: hypothetical answer text on success; the original ``query``
            on empty input, timeout, adapter error, or empty completion.
        """
        if not query or not query.strip():
            return query

        try:
            response = await asyncio.wait_for(
                self._llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_HYDE_SYSTEM_INSTRUCTION),
                        LLMMessage(role="user", content=query),
                    ],
                    spec=spec,
                    record_tenant_id=record_tenant_id,
                    trace_id=trace_id,
                ),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            # Hard latency budget exceeded — fall back to raw query so the
            # p95 of the chat turn stays bounded.
            logger.warning(
                "hyde_generation_timeout",
                timeout_s=self._timeout_s,
                query_chars=len(query),
            )
            return query
        except (RetrievalError, OSError, ValueError) as exc:
            # Transport / value error → degrade silent (HyDE is best-effort).
            logger.warning(
                "hyde_generation_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                query_chars=len(query),
            )
            return query

        hypothetical = (response.content or "").strip()
        if not hypothetical:
            # Model returned blank — preserve retrieval path with raw query.
            logger.info(
                "hyde_empty_completion",
                query_chars=len(query),
            )
            return query

        logger.debug(
            "hyde_generated",
            query_chars=len(query),
            hyde_chars=len(hypothetical),
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return hypothetical


__all__ = ["HyDEGenerator"]
