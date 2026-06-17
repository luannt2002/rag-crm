"""Unit tests for conversation-state Registry + Null + Jsonb strategies."""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.ports.conversation_state_port import ConversationStatePort
from ragbot.application.ports.guardrail_port import GuardrailHit
from ragbot.infrastructure.conversation_state.jsonb_conversation_state import (
    JsonbConversationState,
)
from ragbot.infrastructure.conversation_state.null_conversation_state import (
    NullConversationState,
)
from ragbot.infrastructure.conversation_state.registry import (
    available_providers,
    build_conversation_state,
)


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #
def test_registry_lists_builtin_strategies() -> None:
    assert set(available_providers()).issuperset({"null", "jsonb"})


def test_build_null_returns_null() -> None:
    assert isinstance(build_conversation_state("null"), NullConversationState)


def test_build_jsonb_returns_jsonb() -> None:
    sf = MagicMock()
    out = build_conversation_state("jsonb", session_factory=sf)
    assert isinstance(out, JsonbConversationState)


def test_unknown_provider_degrades_to_null() -> None:
    assert isinstance(build_conversation_state("does_not_exist"), NullConversationState)


def test_none_provider_degrades_to_null() -> None:
    assert isinstance(build_conversation_state(None), NullConversationState)


def test_provider_string_is_case_insensitive() -> None:
    sf = MagicMock()
    assert isinstance(
        build_conversation_state("JSONB", session_factory=sf), JsonbConversationState,
    )
    assert isinstance(build_conversation_state("  Null  "), NullConversationState)


# --------------------------------------------------------------------------- #
# Null contract                                                                #
# --------------------------------------------------------------------------- #
def test_null_implements_port_protocol() -> None:
    assert isinstance(NullConversationState(), ConversationStatePort)


@pytest.mark.asyncio
async def test_null_load_state_returns_empty_dict() -> None:
    g = NullConversationState()
    assert (await g.load_state(conversation_id=uuid4())) == {}
    assert (await g.load_state(conversation_id=None)) == {}


@pytest.mark.asyncio
async def test_null_save_state_is_noop() -> None:
    g = NullConversationState()
    await g.save_state(conversation_id=uuid4(), state={"x": 1})
    await g.save_state(conversation_id=None, state={})


@pytest.mark.asyncio
async def test_null_detect_drift_returns_empty() -> None:
    g = NullConversationState()
    out = await g.detect_drift(
        prior_state={"service_locked": {"name": "X", "price_buoi_le": 199_000}},
        proposed_answer="something",
        chunks=[],
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Jsonb contract                                                               #
# --------------------------------------------------------------------------- #
class _FakeSession:
    """Async context manager + execute stub. Stores last UPDATE call."""

    def __init__(self) -> None:
        self.load_value: object = None
        self.last_update_state: dict | None = None
        self.commit_called = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, stmt, params=None) -> "_FakeResult":  # noqa: ARG002
        sql = str(stmt).lower()
        if "select" in sql:
            return _FakeResult(self.load_value)
        if "update" in sql:
            self.last_update_state = json.loads(params["s"]) if params else None
        return _FakeResult(None)

    async def commit(self) -> None:
        self.commit_called = True


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


def _session_factory(session: _FakeSession) -> AsyncMock:
    def _factory() -> _FakeSession:
        return session
    return _factory


@pytest.mark.asyncio
async def test_jsonb_load_state_returns_dict_value() -> None:
    sess = _FakeSession()
    sess.load_value = {"intent": "booking", "slots_filled": {"name": "X"}}
    g = JsonbConversationState(session_factory=_session_factory(sess))
    out = await g.load_state(conversation_id=uuid4())
    assert out["intent"] == "booking"


@pytest.mark.asyncio
async def test_jsonb_load_state_handles_string_value() -> None:
    sess = _FakeSession()
    sess.load_value = json.dumps({"intent": "factoid"})
    g = JsonbConversationState(session_factory=_session_factory(sess))
    out = await g.load_state(conversation_id=uuid4())
    assert out["intent"] == "factoid"


@pytest.mark.asyncio
async def test_jsonb_load_state_none_conversation_id_returns_empty() -> None:
    sess = _FakeSession()
    g = JsonbConversationState(session_factory=_session_factory(sess))
    assert (await g.load_state(conversation_id=None)) == {}


@pytest.mark.asyncio
async def test_jsonb_save_state_writes_jsonb_and_commits() -> None:
    sess = _FakeSession()
    g = JsonbConversationState(session_factory=_session_factory(sess))
    state = {"intent": "booking", "service_locked": {"name": "X"}}
    await g.save_state(conversation_id=uuid4(), state=state)
    assert sess.last_update_state == state
    assert sess.commit_called


@pytest.mark.asyncio
async def test_jsonb_save_state_none_conversation_id_noops() -> None:
    sess = _FakeSession()
    g = JsonbConversationState(session_factory=_session_factory(sess))
    await g.save_state(conversation_id=None, state={"x": 1})
    assert sess.last_update_state is None


@pytest.mark.asyncio
async def test_jsonb_load_state_graceful_degrade_on_db_error() -> None:
    class _BrokenSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def execute(self, *a, **kw):
            raise RuntimeError("db down")
    g = JsonbConversationState(session_factory=lambda: _BrokenSession())
    out = await g.load_state(conversation_id=uuid4())
    assert out == {}  # graceful degrade


# --------------------------------------------------------------------------- #
# Drift detection                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_drift_service_name_change_detected() -> None:
    """BP-1 service fusion: locked X, answer mentions Y from chunk."""
    g = JsonbConversationState(session_factory=lambda: _FakeSession())
    chunk = {
        "preview": "4,Chăm sóc da thải độc da,800.000",  # CSV row, name="chăm sóc da thải độc da"
    }
    prior = {
        "service_locked": {
            "name": "chăm sóc da chuyên sâu",
            "price_buoi_le": 199_000,
        },
    }
    proposed = "dịch vụ chăm sóc da thải độc da giá 800.000 đồng"
    hits = await g.detect_drift(prior_state=prior, proposed_answer=proposed, chunks=[chunk])
    assert any(h.rule_id == "conversation_state_service_drift" for h in hits)


@pytest.mark.asyncio
async def test_drift_price_flip_flop_detected() -> None:
    """BP-2 price drift: locked at 199000, answer says 800000."""
    g = JsonbConversationState(session_factory=lambda: _FakeSession())
    prior = {
        "service_locked": {
            "name": "chăm sóc da chuyên sâu",
            "price_buoi_le": 199_000,
        },
    }
    proposed = "Giá dịch vụ là 800.000 đồng cho 1 buổi ạ"
    hits = await g.detect_drift(prior_state=prior, proposed_answer=proposed, chunks=[])
    assert any(h.rule_id == "conversation_state_price_drift" for h in hits)


@pytest.mark.asyncio
async def test_drift_empty_prior_state_returns_no_hits() -> None:
    g = JsonbConversationState(session_factory=lambda: _FakeSession())
    out = await g.detect_drift(prior_state={}, proposed_answer="anything", chunks=[])
    assert out == []


@pytest.mark.asyncio
async def test_drift_consistent_answer_no_hit() -> None:
    """Locked at 199K, answer correctly says 199K — no drift."""
    g = JsonbConversationState(session_factory=lambda: _FakeSession())
    prior = {
        "service_locked": {
            "name": "chăm sóc da chuyên sâu",
            "price_buoi_le": 199_000,
        },
    }
    proposed = "Chăm sóc da chuyên sâu giá 199.000đ/buổi (giá gốc 700.000đ)"
    out = await g.detect_drift(prior_state=prior, proposed_answer=proposed, chunks=[])
    # 199K matches locked → no drift. 700K is original price context, allowed.
    drift_ids = {h.rule_id for h in out}
    assert "conversation_state_price_drift" not in drift_ids


# --------------------------------------------------------------------------- #
# Surface parity sentinel                                                      #
# --------------------------------------------------------------------------- #
def test_null_and_jsonb_match_port_surface() -> None:
    surface = ("load_state", "save_state", "detect_drift")
    for name in surface:
        null_sig = inspect.signature(getattr(NullConversationState, name))
        jsonb_sig = inspect.signature(getattr(JsonbConversationState, name))
        null_params = set(null_sig.parameters) - {"self"}
        jsonb_params = set(jsonb_sig.parameters) - {"self"}
        assert null_params == jsonb_params, (
            f"Surface drift on {name}: Null {null_params} != Jsonb {jsonb_params}"
        )


# --- Phase 1: anti-bloat sanitize (max 5 slots, drop garbage/null) ---
def test_jsonb_sanitize_caps_slots_drops_garbage():
    from ragbot.infrastructure.conversation_state.jsonb_conversation_state import (
        JsonbConversationState,
    )
    j = JsonbConversationState(session_factory=None, ttl_hours=24, max_slots=5)
    dirty = {
        "intent": "booking",
        "slots_filled": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6, "g": None},
        "service_locked": {"name": "x"},
        "GARBAGE": "evil",          # unknown top-level key → dropped
        "__drift_severity": {"x": 1},  # runtime-only → not persisted
    }
    out = j._sanitize(dirty)
    assert set(out.keys()) == {"intent", "slots_filled", "service_locked"}
    assert len(out["slots_filled"]) == 5            # capped
    assert "g" not in out["slots_filled"]            # null dropped
    assert "GARBAGE" not in out and "__drift_severity" not in out


def test_jsonb_sanitize_handles_non_dict():
    from ragbot.infrastructure.conversation_state.jsonb_conversation_state import (
        JsonbConversationState,
    )
    j = JsonbConversationState(session_factory=None)
    assert j._sanitize("not a dict") == {}
    assert j._sanitize({}) == {}
