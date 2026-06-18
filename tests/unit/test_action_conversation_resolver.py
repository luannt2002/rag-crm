"""Pin the shared action-conversation resolver wired into the SSE path.

Regression guard for the SSE booking-slot loss: ``chat_stream.py`` used to
hardcode ``conversation_id=None`` so multi-turn action state never persisted.
The fix routes through ``resolve_action_conversation_id`` (get-or-create for
action bots, ``None`` for factoid bots).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from ragbot.interfaces.http.routes._action_conversation import (
    resolve_action_conversation_id,
)
# Production SSE path must reuse the SAME helper (no divergent copy).
from ragbot.interfaces.http.routes.test_chat._shared import (
    _resolve_action_conversation_id,
)


class _FakeConvRepo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._id = uuid.uuid4()

    async def get_or_create(self, bot_id, user_id, *, record_tenant_id, workspace_id):
        self.calls.append((str(bot_id), str(user_id), str(record_tenant_id), str(workspace_id)))
        return SimpleNamespace(id=self._id)


@pytest.mark.asyncio
async def test_action_bot_resolves_persistent_conversation_id():
    repo = _FakeConvRepo()
    bot = SimpleNamespace(id=str(uuid.uuid4()), action_config={"enabled": True})
    cid = await resolve_action_conversation_id(
        repo, bot,
        connect_id="user-42",
        tenant_id=uuid.uuid4(),
        workspace_slug="ws-1",
    )
    assert cid == repo._id, "action bot must get a real conversation_id (not None)"
    assert len(repo.calls) == 1, "must call get_or_create exactly once"


@pytest.mark.asyncio
async def test_factoid_bot_returns_none_no_churn():
    repo = _FakeConvRepo()
    bot = SimpleNamespace(id=str(uuid.uuid4()), action_config={"enabled": False})
    cid = await resolve_action_conversation_id(
        repo, bot,
        connect_id="user-42",
        tenant_id=uuid.uuid4(),
        workspace_slug="ws-1",
    )
    assert cid is None, "factoid bot must stay None to avoid conversation-row churn"
    assert repo.calls == [], "factoid bot must NOT touch the conversation repo"


@pytest.mark.asyncio
async def test_missing_repo_degrades_to_none():
    bot = SimpleNamespace(id=str(uuid.uuid4()), action_config={"enabled": True})
    cid = await resolve_action_conversation_id(
        None, bot,
        connect_id="user-42",
        tenant_id=uuid.uuid4(),
        workspace_slug="ws-1",
    )
    assert cid is None, "no repo wired → graceful None, not a crash"


def test_test_path_reexports_same_object():
    assert _resolve_action_conversation_id is resolve_action_conversation_id, (
        "test_chat path must reuse the promoted shared helper, not a copy"
    )
