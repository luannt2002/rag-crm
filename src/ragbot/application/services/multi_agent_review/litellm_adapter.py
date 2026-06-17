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
# Reason: Part of unused multi_agent_review subpackage.
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

# from __future__ import annotations

# import time
# from collections.abc import AsyncIterator

# import litellm
# from pydantic import BaseModel

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort, LLMResponse
# from ragbot.shared.constants import DEFAULT_LITELLM_DIRECT_ADAPTER_TIMEOUT_S
# from ragbot.shared.types import TenantId, TraceId


# class DirectLiteLLMAdapter(LLMPort):
#     """Thin LLMPort adapter for offline tools (CLI review, scripts).

#     Bypasses the DB-driven router. Calls `litellm.acompletion` directly with
#     the provider-prefixed model name carried by the LLMSpec. Use for the
#     multi-agent reviewer when the script has no DB binding to read from.
#     Production hot path keeps using DynamicLiteLLMRouter.
#     """

#     def __init__(
#         self,
#         *,
#         default_timeout_s: float = DEFAULT_LITELLM_DIRECT_ADAPTER_TIMEOUT_S,
#     ) -> None:
#         self._timeout_s = default_timeout_s

#     async def health_check(self) -> bool:
#         return True

#     async def complete(
#         self,
#         messages: list[LLMMessage],
#         *,
#         spec: LLMSpec,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#         response_schema: type[BaseModel] | None = None,
#     ) -> LLMResponse:
#         del record_tenant_id, trace_id, response_schema

#         litellm_messages = [
#             {"role": str(m.role), "content": m.content} for m in messages
#         ]
#         kwargs = spec.to_litellm_kwargs()
#         kwargs["messages"] = litellm_messages
#         kwargs["timeout"] = self._timeout_s

#         t0 = time.perf_counter()
#         resp = await litellm.acompletion(**kwargs)
#         latency_ms = int((time.perf_counter() - t0) * 1000)

#         choice = resp.choices[0]
#         content = choice.message.content or ""
#         usage = getattr(resp, "usage", None) or {}
#         tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
#         tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
#         cost_usd = float(getattr(resp, "_response_cost", 0.0) or 0.0)
#         return LLMResponse(
#             content=content,
#             model=spec.model_name,
#             provider=spec.provider,
#             tokens_in=tokens_in,
#             tokens_out=tokens_out,
#             cost_usd=cost_usd,
#             latency_ms=latency_ms,
#         )

#     async def stream(
#         self,
#         messages: list[LLMMessage],
#         *,
#         spec: LLMSpec,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#     ) -> AsyncIterator[str]:
#         del messages, spec, record_tenant_id, trace_id
#         raise NotImplementedError("DirectLiteLLMAdapter is non-streaming")

#     async def refresh_routing(self) -> None:
#         return None

#     async def close(self) -> None:
#         return None


# __all__ = ["DirectLiteLLMAdapter"]
