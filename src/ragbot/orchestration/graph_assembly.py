"""Shared graph DI + initial-state assembly for every chat transport.

Root cause this module closes (ADR-W1-DI): four production callsites
(``chat_worker``, ``chat_stream``, ``test_chat`` sync + stream) each
hand-rolled their own ``get_graph(...)`` kwargs list and initial
``GraphState`` dict. ``get_graph`` is first-caller-wins by design, so the
transport that warmed up first silently decided which optional deps
(HyDE, understand-query cache, stats repo, parent-child doc repo) existed
for the whole process — and the SSE state dict was missing keys the graph
dereferences (``workspace_id`` is a direct subscript in persist).

One builder, one key set, every transport:

- :func:`build_graph_di_kwargs` — canonical kwargs for ``build_graph``;
  fail-loud on required deps, narrow-catch optional deps, one structured
  ``graph_di_assembled`` event naming whatever resolved to ``None``.
- :func:`resolve_kg_service` — GraphRAG service gate, lifted verbatim from
  the worker so SSE/demo honour per-bot ``graph_rag_mode`` identically.
- :func:`build_chat_initial_state` — canonical ``GraphState``; transports
  append their transport-specific keys (``_stream_sink``, ``bypass_cache``)
  AFTER receiving the dict.

The ``get_graph`` singleton and its ignore-kwargs-after-first-build
semantics are untouched — this module fixes the *callsite assembly* layer,
not the engine (P2-A §5.1 "đã chuẩn — đừng đụng").
"""

from __future__ import annotations

import inspect
from typing import Any, Final

import structlog

from ragbot.infrastructure.graph.knowledge_graph import KnowledgeGraphService
from ragbot.orchestration.state import GraphState
from ragbot.shared.errors import GraphAssemblyError

logger = structlog.get_logger(__name__)

# Deps build_graph cannot run without: llm/model_resolver/invocation_logger/
# guardrail have no signature default; vector_store/embedder are required by
# the Y3-P1 precedent (missing either silently degenerates retrieval to a
# no-context LLM call, so the route already 503s on them).
GRAPH_DI_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "llm",
        "model_resolver",
        "invocation_logger",
        "guardrail",
        "vector_store",
        "embedder",
    }
)

# kwarg name → container provider attr, where they differ.
_PROVIDER_ALIASES: Final[dict[str, str]] = {
    "audit_logger": "pipeline_audit_logger",
    "doc_repo": "document_repo",
}


def _build_graph_param_names() -> frozenset[str]:
    """Parameter set of ``build_graph`` — resolved at call time so this
    module can never drift from the engine signature."""
    from ragbot.orchestration.query_graph import build_graph  # noqa: PLC0415 — avoid import cycle at module load

    return frozenset(inspect.signature(build_graph).parameters)


def _resolve_optional(container: Any, attr: str) -> Any | None:
    """Optional DI resolve — narrow-catch contract shared by all transports.

    The provider may legitimately raise on missing config (KeyError), bad
    attribute wiring (AttributeError), or wrong-arg shape (TypeError).
    Anything else is a real bug and propagates.
    """
    if not hasattr(container, attr):
        return None
    try:
        return getattr(container, attr)()
    except (KeyError, AttributeError, TypeError) as exc:
        logger.warning(
            "graph_di_optional_dep_unavailable",
            attr=attr,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def build_graph_di_kwargs(container: Any) -> dict[str, Any]:
    """Canonical DI kwargs for ``build_graph`` — the single source of truth.

    Required deps that fail to resolve raise :class:`GraphAssemblyError`
    (the route layer maps it to 503); optional deps degrade to ``None``
    with a structured warning. Emits one ``graph_di_assembled`` event so
    a warm-up that lost deps is visible in the journal instead of silent.
    """
    kwargs: dict[str, Any] = {}
    for param in sorted(_build_graph_param_names()):
        attr = _PROVIDER_ALIASES.get(param, param)
        if param in GRAPH_DI_REQUIRED:
            try:
                value = getattr(container, attr)()
            except Exception as exc:  # noqa: BLE001 — re-raised as typed assembly error
                raise GraphAssemblyError(
                    f"required graph dependency '{param}' failed to resolve",
                    details={"dep": param, "error": str(exc)},
                ) from exc
            if value is None:
                raise GraphAssemblyError(
                    f"required graph dependency '{param}' resolved to None",
                    details={"dep": param},
                )
            kwargs[param] = value
        else:
            kwargs[param] = _resolve_optional(container, attr)

    none_deps = sorted(k for k, v in kwargs.items() if v is None)
    logger.info("graph_di_assembled", none_deps=none_deps)
    return kwargs


def resolve_kg_service(pipeline_config: dict) -> Any | None:
    """GraphRAG service when ``graph_rag_mode != "disabled"``.

    Lifted verbatim from the worker path so every transport honours a
    bot's GraphRAG opt-in identically (SSE previously hardcoded ``None``).
    """
    if pipeline_config.get("graph_rag_mode", "disabled") != "disabled":
        return KnowledgeGraphService()
    return None


def build_chat_initial_state(
    *,
    record_tenant_id: Any,
    request_id: Any,
    message_id: Any,
    conversation_id: Any,
    record_bot_id: Any,
    bot_cfg: Any,
    channel_type: str,
    workspace_id: str,
    user_groups: list[str],
    query: str,
    conversation_history: list[dict[str, str]],
    pipeline_config: dict,
    tracker: Any,
    assembled_sysprompt: str,
    oos_template_resolved: str,
    bot_language: str,
    kg_service: Any | None,
    session_factory: Any | None,
) -> GraphState:
    """Canonical initial ``GraphState`` — key set matches the worker path.

    ``workspace_id`` / ``user_groups`` /
    ``bot_extra_output_tokens_per_response`` are part of the canon: the
    graph dereferences all three (persist subscripts ``workspace_id``
    directly). Transport-specific keys are added by the caller afterwards.
    """
    return {
        "record_tenant_id": record_tenant_id,
        "request_id": request_id,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "record_bot_id": record_bot_id,
        "channel_type": channel_type,
        "workspace_id": workspace_id,
        "user_groups": user_groups,
        "query": query,
        # Literal user input, never overwritten by condense/rewrite — slot
        # extraction reads THIS so a bare slot turn ("Tên Lan") is not mangled
        # into a question by the search-query rewrite (root cause 2026-06-15).
        "raw_user_message": query,
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0, "cached": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "conversation_history": conversation_history,
        "pipeline_config": pipeline_config,
        "step_tracker": tracker,
        "bot_system_prompt": assembled_sysprompt,
        "bot_created_at": getattr(bot_cfg, "created_at", None),
        "bot_extra_output_tokens_per_response": int(
            getattr(bot_cfg, "extra_output_tokens_per_response", 0) or 0,
        ),
        "language": bot_language,
        "oos_answer_template_resolved": oos_template_resolved,
        "kg_service": kg_service,
        "session_factory": session_factory,
    }


__all__ = [
    "GRAPH_DI_REQUIRED",
    "build_chat_initial_state",
    "build_graph_di_kwargs",
    "resolve_kg_service",
]
