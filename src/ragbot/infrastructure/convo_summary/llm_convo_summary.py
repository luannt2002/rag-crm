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
# Reason: convo_summary infra never wired in bootstrap or graph.
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

# """LLMConvoSummary — summarise turns through an injected ``LLMPort``.

# The instruction prompt enumerates turns with their ordinal index so the LLM
# preserves chronology when compressing. The summary is bounded by the
# caller-supplied ``max_tokens`` (forwarded as ``LLMSpec.max_tokens`` override
# in the call kwargs); the platform never auto-injects the result anywhere —
# the bot owner's admin wiring decides where the summary flows.
# """

# from __future__ import annotations

# import structlog

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.convo_summary_port import Turn
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort
# from ragbot.shared.errors import RetrievalError
# from ragbot.shared.types import TenantId, TraceId

# logger = structlog.get_logger(__name__)


# _SUMMARY_SYSTEM_INSTRUCTION = (
#     "You are a conversation summariser. Compress the dialogue below into a "
#     "short factual summary that preserves the chronological order of turns. "
#     "Each turn is numbered (#1, #2, ...) — refer to that ordering implicitly "
#     "by keeping earlier facts before later ones. Do NOT invent information "
#     "not present in the turns. Keep the summary within the requested token "
#     "budget."
# )


# class LLMConvoSummary:
#     """LLM-backed conversation summary strategy."""

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

#     async def summarise(self, turns: list[Turn], max_tokens: int) -> str:
#         """Summarise ``turns`` using the injected LLM.

#         Returns ``""`` when ``turns`` is empty (no work to do) or when the
#         provider returns empty content. Adapter failures are surfaced as
#         ``RetrievalError`` so the owner-side caller can decide whether to
#         degrade silently or propagate.
#         """
#         if not turns:
#             return ""

#         numbered = "\n".join(
#             f"#{idx} [{turn.role}] {turn.content}"
#             for idx, turn in enumerate(turns, start=1)
#         )
#         user_prompt = (
#             f"Summarise the following {len(turns)} turns within "
#             f"{max_tokens} tokens. Preserve chronological order.\n\n"
#             f"{numbered}"
#         )

#         spec_for_call = self._spec.model_copy(update={"max_tokens": max_tokens})

#         try:
#             response = await self._llm.complete(
#                 messages=[
#                     LLMMessage(role="system", content=_SUMMARY_SYSTEM_INSTRUCTION),
#                     LLMMessage(role="user", content=user_prompt),
#                 ],
#                 spec=spec_for_call,
#                 record_tenant_id=self._record_tenant_id,
#                 trace_id=self._trace_id,
#             )
#         except (RetrievalError, OSError, ValueError) as exc:
#             logger.error(
#                 "llm_convo_summary_adapter_failure",
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#                 turns=len(turns),
#             )
#             raise RetrievalError("convo_summary_llm_failed") from exc

#         return response.content.strip()


# __all__ = ["LLMConvoSummary"]
