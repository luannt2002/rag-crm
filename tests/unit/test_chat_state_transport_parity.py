"""Initial ``GraphState`` parity across transports (ADR-W1-DI §2.4).

Before the shared builder, ``chat_stream`` omitted three keys the graph
actually consumes:

- ``workspace_id``  → direct subscript in persist (``query_graph.py:7417``)
  ⇒ latent ``KeyError`` on the SSE cache-write path.
- ``user_groups``   → permission pre-filter treated SSE users as group-less.
- ``bot_extra_output_tokens_per_response`` → paid output budget silently off.

``build_chat_initial_state`` is the single source for the canonical key set;
transport-specific keys (``_stream_sink``, ``bypass_cache``) are added by the
caller AFTER receiving the dict.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4


def _build(**overrides: Any) -> dict:
    from ragbot.orchestration.graph_assembly import build_chat_initial_state

    bot_cfg = SimpleNamespace(
        id=uuid4(),
        system_prompt="bot prompt",
        created_at=None,
        extra_output_tokens_per_response=128,
    )
    kwargs: dict[str, Any] = dict(
        record_tenant_id=uuid4(),
        request_id=uuid4(),
        message_id=1,
        conversation_id=None,
        record_bot_id=bot_cfg.id,
        bot_cfg=bot_cfg,
        channel_type="web",
        workspace_id="ws-slug",
        user_groups=["g1"],
        query="câu hỏi",
        conversation_history=[],
        pipeline_config={"graph_recursion_limit": 50},
        tracker=object(),
        assembled_sysprompt="assembled",
        oos_template_resolved="",
        bot_language="vi",
        kg_service=None,
        session_factory=None,
    )
    kwargs.update(overrides)
    return build_chat_initial_state(**kwargs)


# Snapshot of the keys the worker path sets today
# (chat_worker.py:1441-1473) — the canonical contract.
_WORKER_KEY_SNAPSHOT = frozenset(
    {
        "record_tenant_id", "request_id", "message_id", "conversation_id",
        "record_bot_id", "channel_type", "workspace_id", "user_groups",
        "query", "raw_user_message", "rewritten_query", "retrieved_chunks", "reranked_chunks",
        "graded_chunks", "answer", "citations", "guardrail_flags", "tokens",
        "cost_usd", "model_used", "conversation_history", "pipeline_config",
        "step_tracker", "bot_system_prompt", "bot_created_at",
        "bot_extra_output_tokens_per_response", "language",
        "oos_answer_template_resolved", "kg_service", "session_factory",
    }
)


def test_builder_emits_worker_canonical_key_set():
    state = _build()
    assert frozenset(state) == _WORKER_KEY_SNAPSHOT, (
        f"diff: +{sorted(frozenset(state) - _WORKER_KEY_SNAPSHOT)} "
        f"-{sorted(_WORKER_KEY_SNAPSHOT - frozenset(state))}"
    )


def test_workspace_id_always_subscriptable():
    """Persist node does ``state['workspace_id']`` — must never KeyError."""
    state = _build()
    assert state["workspace_id"] == "ws-slug"


def test_extra_output_tokens_lifted_from_bot_cfg():
    state = _build()
    assert state["bot_extra_output_tokens_per_response"] == 128


def test_extra_output_tokens_defaults_zero_when_unset():
    bot_cfg = SimpleNamespace(
        id=uuid4(), system_prompt="p", created_at=None,
        extra_output_tokens_per_response=None,
    )
    state = _build(bot_cfg=bot_cfg, record_bot_id=bot_cfg.id)
    assert state["bot_extra_output_tokens_per_response"] == 0


def test_tokens_dict_unified_with_cached_bucket():
    """Worker historically had {prompt, completion}; stream added "cached".
    The canonical shape carries all three so accounting code can rely on it."""
    state = _build()
    assert state["tokens"] == {"prompt": 0, "completion": 0, "cached": 0}


def test_transport_specific_keys_not_in_builder_output():
    """``_stream_sink`` / ``bypass_cache`` are caller add-ons, not canon."""
    state = _build()
    assert "_stream_sink" not in state
    assert "bypass_cache" not in state
