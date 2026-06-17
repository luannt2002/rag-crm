# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: query_router infra never wired in bootstrap or graph.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """LLMQueryRouter — LLM-based pre-retrieve intent classifier.

# Slower / more accurate strategy: delegates classification to a small
# LLM that returns a Pydantic-validated ``QueryIntent`` literal. Useful
# when regex coverage misses paraphrased queries (e.g. "anh muốn đối
# chiếu hai gói này" — comparison intent without the ``so sánh`` keyword).

# This strategy is opt-in. It is shaped as a thin adapter around a
# ``classify_fn`` callable so the LLM transport (LiteLLM router, structured
# output handler, retry policy) can be assembled at bootstrap time
# without bleeding LLM-port plumbing into this strategy file. The
# adapter contract: ``classify_fn(query) -> str`` returning one of the
# six labels in ``QUERY_INTENT_TYPES``. Anything else collapses to the
# ``semantic`` catch-all so a wobbly LLM cannot break the pipeline.

# Operator wiring (bootstrap):

#     async def _llm_classify(q: str) -> str:
#         out = await llm.complete(
#             messages=[LLMMessage(role="system", content=PROMPT),
#                       LLMMessage(role="user", content=q)],
#             spec=_QUERY_ROUTER_LLM_SPEC,
#             record_tenant_id=<system>,
#             trace_id=<request>,
#             response_schema=_QueryIntentResponse,
#         )
#         return (out.structured.intent if out.structured else "semantic")
#     router = LLMQueryRouter(classify_fn=_llm_classify)
# """

# from __future__ import annotations

# from collections.abc import Awaitable, Callable

# from ragbot.application.ports.query_router_port import QueryIntent
# from ragbot.shared.constants import (
#     QUERY_INTENT_SEMANTIC,
#     QUERY_INTENT_TYPES,
# )

# ClassifyFn = Callable[[str], Awaitable[str]]


# class LLMQueryRouter:
#     """LLM-backed query classifier — accuracy over latency."""

#     def __init__(self, classify_fn: ClassifyFn | None = None) -> None:
        # ``classify_fn`` is None for default construction (e.g. registry
        # smoke tests) — in that case classify() degrades to ``semantic``.
        # Bootstrap MUST supply a real callable for production traffic.
#         self._classify_fn: ClassifyFn | None = classify_fn

#     @staticmethod
#     def get_provider_name() -> str:
#         return "llm"

#     async def classify(self, query: str) -> QueryIntent:
#         if not query or not query.strip():
#             return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]
#         if self._classify_fn is None:
            # No classifier wired — degrade silently to the catch-all so
            # the pipeline keeps running. Operator should see a non-null
            # callable injected at bootstrap; absence is a wiring bug, not
            # a runtime error to surface to end users.
#             return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]
#         try:
#             label = await self._classify_fn(query.strip())
#         except (ValueError, TypeError, RuntimeError):
            # Graceful degradation — any classifier wobble routes through
            # the semantic default. Transport / LLM errors propagate up
            # only if the injected callable chooses to raise something
            # outside this narrow set.
#             return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]
#         if label in QUERY_INTENT_TYPES:
#             return label  # type: ignore[return-value]
#         return QUERY_INTENT_SEMANTIC  # type: ignore[return-value]


# __all__ = ["ClassifyFn", "LLMQueryRouter"]
