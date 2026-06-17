"""GraphRAG ``kg_service`` parity across transports (P2-A 🐛-2 / ADR-W1-DI).

``chat_worker`` built a ``KnowledgeGraphService`` whenever
``graph_rag_mode != "disabled"`` while ``chat_stream`` (and both demo
endpoints) hardcoded ``kg_service: None`` — a bot opting into GraphRAG got
two different behaviours depending on transport. ``resolve_kg_service`` is
the single shared resolver (logic lifted verbatim from
``chat_worker.py:1357-1360``).
"""

from __future__ import annotations


def test_disabled_mode_returns_none():
    from ragbot.orchestration.graph_assembly import resolve_kg_service

    assert resolve_kg_service({"graph_rag_mode": "disabled"}) is None


def test_missing_key_defaults_disabled():
    from ragbot.orchestration.graph_assembly import resolve_kg_service

    assert resolve_kg_service({}) is None


def test_enabled_mode_returns_service():
    from ragbot.orchestration.graph_assembly import resolve_kg_service

    svc = resolve_kg_service({"graph_rag_mode": "adaptive"})
    assert svc is not None, (
        "graph_rag_mode != 'disabled' must yield a kg_service on EVERY "
        "transport — SSE previously dropped GraphRAG the worker honoured "
        "(chat_stream.py:330 hardcoded None)"
    )


def test_builder_threads_kg_service_into_state():
    from ragbot.orchestration.graph_assembly import (
        build_chat_initial_state,
        resolve_kg_service,
    )
    from types import SimpleNamespace
    from uuid import uuid4

    kg = resolve_kg_service({"graph_rag_mode": "adaptive"})
    bot_cfg = SimpleNamespace(
        id=uuid4(), system_prompt="p", created_at=None,
        extra_output_tokens_per_response=0,
    )
    state = build_chat_initial_state(
        record_tenant_id=uuid4(), request_id=uuid4(), message_id=1,
        conversation_id=None, record_bot_id=bot_cfg.id, bot_cfg=bot_cfg,
        channel_type="web", workspace_id="ws", user_groups=[],
        query="q", conversation_history=[],
        pipeline_config={"graph_rag_mode": "adaptive"},
        tracker=object(), assembled_sysprompt="p", oos_template_resolved="",
        bot_language="vi", kg_service=kg, session_factory=None,
    )
    assert state["kg_service"] is kg
