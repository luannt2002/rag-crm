"""Y3-P1 regression: chat_schema + admin_bots use constants, not literals.

Audit catches:
  - chat_schema.ChatRequest.history_limit had `default=6, le=20` literals.
    Both must come from constants (DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT_REQUEST).
  - admin_bots._require_admin used `require_min_level(request, 80)` and
    _admin_tenant_scope used `check_min_level(request, 100)` — magic
    numbers must come from DEFAULT_TENANT_ADMIN_LEVEL +
    DEFAULT_SUPER_ADMIN_LEVEL.
  - chat_stream silently accepted None vector_store — must fail loud 503
    when core deps missing.

These tests assert the binding without booting Postgres / Redis.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from ragbot.interfaces.http.schemas.chat_schema import ChatRequest
from ragbot.shared.constants import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_SUPER_ADMIN_LEVEL,
    DEFAULT_TENANT_ADMIN_LEVEL,
    MAX_HISTORY_LIMIT_REQUEST,
)


def _valid_payload(**override: object) -> dict:
    # tenant is lifted from JWT bearer (request.state.record_tenant_id);
    # the body never carries it (anti-spoof). Body is the 2-key bot
    # identity + optional workspace_id slug — see chat_schema docstring.
    base = {
        "bot_id": "test-bot",
        "channel_type": "web",
        "user_id": "u1",
        "content": "hi",
    }
    base.update(override)
    return base


def test_history_limit_default_uses_constant() -> None:
    req = ChatRequest.model_validate(_valid_payload())
    assert req.history_limit == DEFAULT_HISTORY_LIMIT
    assert DEFAULT_HISTORY_LIMIT == 6


def test_history_limit_upper_cap_uses_constant() -> None:
    """Boundary inclusive: MAX_HISTORY_LIMIT_REQUEST should be acceptable;
    one above must fail Pydantic validation."""
    req = ChatRequest.model_validate(
        _valid_payload(history_limit=MAX_HISTORY_LIMIT_REQUEST),
    )
    assert req.history_limit == MAX_HISTORY_LIMIT_REQUEST
    assert MAX_HISTORY_LIMIT_REQUEST == 20

    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            _valid_payload(history_limit=MAX_HISTORY_LIMIT_REQUEST + 1),
        )


def test_history_limit_lower_cap_unchanged() -> None:
    """Lower bound is 1 — accepted. 0 must reject."""
    req = ChatRequest.model_validate(_valid_payload(history_limit=1))
    assert req.history_limit == 1
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_valid_payload(history_limit=0))


def test_admin_bots_levels_are_constants_not_literals() -> None:
    """Read the source of admin_bots.py and assert the literal `80`/`100`
    no longer appear in the gate helpers — they must reference the
    constants. Helper is named ``_admin_record_tenant`` (UUID return
    type)."""
    from ragbot.interfaces.http.routes import admin_bots

    src_require_admin = inspect.getsource(admin_bots._require_admin)
    src_tenant_scope = inspect.getsource(admin_bots._admin_record_tenant)

    # Constants used:
    assert "DEFAULT_TENANT_ADMIN_LEVEL" in src_require_admin
    assert "DEFAULT_SUPER_ADMIN_LEVEL" in src_tenant_scope

    # Literals NOT used in these helpers:
    assert "80" not in src_require_admin
    assert "100" not in src_tenant_scope


def test_admin_levels_have_consistent_values() -> None:
    """Sanity: tenant_admin < super_admin, and the well-known platform
    convention (80 < 100) is preserved by the constants."""
    assert DEFAULT_TENANT_ADMIN_LEVEL < DEFAULT_SUPER_ADMIN_LEVEL
    assert DEFAULT_TENANT_ADMIN_LEVEL == 80
    assert DEFAULT_SUPER_ADMIN_LEVEL == 100


def test_chat_stream_fails_loud_when_vector_store_missing() -> None:
    """The fix introduced an explicit 503 path BEFORE build_graph when
    vector_store or embedder is None. Read source to assert the guard."""
    import ragbot.interfaces.http.routes.chat_stream as cs

    src = inspect.getsource(cs)
    # Y3-P1 contract survives ADR-W1-DI: required deps (vector_store/embedder)
    # now fail loudly inside the canonical builder as GraphAssemblyError and
    # the route maps that to 503.
    assert "GraphAssemblyError" in src
    assert "RAG pipeline misconfigured" in src
    assert "503" in src
    from ragbot.orchestration.graph_assembly import GRAPH_DI_REQUIRED

    assert "vector_store" in GRAPH_DI_REQUIRED
    assert "embedder" in GRAPH_DI_REQUIRED
