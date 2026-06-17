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
# Reason: HyDE infra never wired. Active HyDE path uses application/services/hyde_generator.py instead.
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

# """LLMHyDEGenerator — produce a hypothetical answer via an injected ``LLMPort``.

# Gao et al. 2022 — "Precise Zero-Shot Dense Retrieval without Relevance
# Labels". The hypothetical answer is what gets embedded; the resulting
# vector is closer to actual chunk text (declarative style) than the raw
# query, lifting top-k recall on ambiguous queries.

# Graceful degradation contract: any failure path (LLM adapter raise,
# timeout, empty content) returns the **original query** so the retrieve
# pipeline keeps working — HyDE is an enhancement, never a hard dependency.
# Adapter failures are logged with ``error_type`` for ops triage.

# Domain-neutral: the system instruction never mentions any specific
# industry / domain — it instructs the model to mirror the query's own
# language and topic and stay declarative.
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort
# from ragbot.shared.errors import RetrievalError
# from ragbot.shared.types import TenantId, TraceId

# logger = structlog.get_logger(__name__)


# _HYDE_SYSTEM_INSTRUCTION = (
#     "You are a domain-agnostic retrieval helper. Given a question, write a "
#     "SHORT (50-100 words) hypothetical answer that would likely appear in a "
#     "relevant document.\n\n"
#     "Rules:\n"
#     "- Use declarative style (statement, not question).\n"
#     "- Stay close to the query topic; do NOT invent facts or numbers.\n"
#     "- Preserve the user's language exactly.\n"
#     "- Output: just the hypothetical answer text, no preamble or hedging."
# )


# class LLMHyDEGenerator:
#     """LLM-backed HyDE strategy.

#     @param llm: the ``LLMPort`` to call (typically the small/fast tier).
#     @param spec: ``LLMSpec`` bound at construction; model + max_tokens +
#         temperature flow from constants / system_config so the call site
#         carries no magic numbers.
#     @param record_tenant_id: tenant scope for the LLM call.
#     @param trace_id: distributed trace id to thread through the LLM call.
#     """

#     def __init__(
#         self,
#         *,
#         llm: LLMPort,
#         spec: LLMSpec,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> None:
#         self._llm = llm
#         self._spec = spec
#         self._record_tenant_id = record_tenant_id
#         self._trace_id = trace_id

#     @staticmethod
#     def get_provider_name() -> str:
#         return "llm"

#     async def generate(self, query: str) -> str:
#         """Generate a hypothetical answer for ``query`` to embed instead.

#         Returns:
#             The LLM-drafted hypothetical answer text on success;
#             the **original** ``query`` if the input is empty, the LLM
#             returns empty content, or the adapter raises a known
#             transport / value error (degrade silent — HyDE is best-effort).
#         """
#         if not query or not query.strip():
#             return query

#         try:
#             response = await self._llm.complete(
#                 messages=[
#                     LLMMessage(role="system", content=_HYDE_SYSTEM_INSTRUCTION),
#                     LLMMessage(role="user", content=query),
#                 ],
#                 spec=self._spec,
#                 record_tenant_id=self._record_tenant_id,
#                 trace_id=self._trace_id,
#             )
#         except (RetrievalError, OSError, ValueError, TimeoutError) as exc:
            # Degrade silent — HyDE is an enhancement, never a hard dep.
#             logger.warning(
#                 "llm_hyde_adapter_failure",
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#                 query_chars=len(query),
#             )
#             return query

#         hypothetical = (response.content or "").strip()
#         if not hypothetical:
#             logger.info("llm_hyde_empty_completion", query_chars=len(query))
#             return query

#         logger.debug(
#             "llm_hyde_generated",
#             query_chars=len(query),
#             hyde_chars=len(hypothetical),
#             tokens_in=response.tokens_in,
#             tokens_out=response.tokens_out,
#         )
#         return hypothetical


# __all__ = ["LLMHyDEGenerator"]
