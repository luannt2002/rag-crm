"""End-to-end slug round-trip from HTTP body to BotConfig + cache key.

The 4-key resolver flows the workspace slug through three boundaries:
the Pydantic request schema, the ``resolve_workspace_id`` resolver, and
the runtime ``BotConfig`` DTO that the cache key is built from. Each
boundary has its own validation rules; this suite walks a slug across
all three so a regression in any single boundary surfaces here even when
the others remain healthy.

Coverage:

1. Happy path — a clean slug ``"sales-q4"`` survives every boundary
   intact and lands in the canonical cache key shape.
2. Tenant-UUID fallback — a ``None`` body slug resolves to
   ``str(record_tenant_id)`` (a 36-char UUID-form slug that satisfies
   ``WORKSPACE_ID_PATTERN``) and the resulting cache key still has
   exactly 4 segments.
3. Schema rejection — a body slug containing a space, an underscore,
   an accent, or a length over the cap fails Pydantic field
   validation before the route handler runs.
4. Validator rejection — calling ``WorkspaceIdValidator.validate``
   directly on the same illegal slugs raises ``WorkspaceIdInvalid``
   so callers that bypass Pydantic still hit the same fail-loud gate.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_registry_service import (
    REDIS_PREFIX,
    BotRegistryService,
)
from ragbot.interfaces.http.schemas.chat_schema import ChatRequest
from ragbot.shared.constants import (
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_SYSTEM_SLUG,
)
from ragbot.shared.errors import WorkspaceIdInvalid
from ragbot.shared.workspace_id_validator import (
    WorkspaceIdValidator,
    resolve_workspace_id,
)


# ---------------------------------------------------------------------------
# 1. Happy path — body slug survives every boundary
# ---------------------------------------------------------------------------


def test_round_trip_clean_slug_lands_in_cache_key() -> None:
    rt = uuid4()
    body_slug = "sales-q4"

    # Boundary 1 — Pydantic request schema accepts it.
    req = ChatRequest(
        bot_id="support",
        channel_type="web",
        workspace_id=body_slug,
        user_id="u1",
        content="hello",
    )
    assert req.workspace_id == body_slug

    # Boundary 2 — resolver passes a non-empty slug straight through.
    resolved = resolve_workspace_id(req.workspace_id, record_tenant_id=rt)
    assert resolved == body_slug

    # Boundary 3 — ``BotConfig`` DTO accepts the same slug.
    cfg = BotConfig(
        id=uuid4(),
        bot_id=req.bot_id,
        channel_type=req.channel_type,
        record_tenant_id=rt,
        workspace_id=resolved,
        bot_name="round-trip",
    )
    assert cfg.workspace_id == body_slug

    # The cache key carries the slug in slot 2 and has 4 colon-separated
    # parts after the prefix.
    key = BotRegistryService._key(rt, cfg.workspace_id, cfg.bot_id, cfg.channel_type)
    parts = key[len(REDIS_PREFIX) + 1:].split(":")
    assert parts == [str(rt), body_slug, "support", "web"]


# ---------------------------------------------------------------------------
# 2. Fallback path — None body slug resolves to tenant UUID
# ---------------------------------------------------------------------------


def test_round_trip_none_falls_back_to_tenant_uuid() -> None:
    rt = uuid4()
    req = ChatRequest(
        bot_id="support",
        channel_type="web",
        # workspace_id omitted on the wire
        user_id="u1",
        content="hello",
    )
    assert req.workspace_id is None

    resolved = resolve_workspace_id(req.workspace_id, record_tenant_id=rt)
    assert resolved == str(rt)

    cfg = BotConfig(
        id=uuid4(),
        bot_id=req.bot_id,
        channel_type=req.channel_type,
        record_tenant_id=rt,
        workspace_id=resolved,
        bot_name="fallback",
    )
    key = BotRegistryService._key(rt, cfg.workspace_id, cfg.bot_id, cfg.channel_type)
    parts = key[len(REDIS_PREFIX) + 1:].split(":")
    # Tenant UUID appears twice — once as record_tenant_id, once as the
    # fallback workspace slug.
    assert parts[0] == str(rt)
    assert parts[1] == str(rt)


def test_round_trip_empty_string_treated_as_missing() -> None:
    rt = uuid4()
    resolved = resolve_workspace_id("", record_tenant_id=rt)
    assert resolved == str(rt)


# ---------------------------------------------------------------------------
# 3. Schema rejection — Pydantic Field guards illegal slugs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    [
        "has space",       # space — separator confusion in Redis keys
        "team_alpha",      # underscore — not in the ASCII shape
        "dự_án",            # accent — Unicode bypasses ASCII pattern
        "sales/q4",        # slash — URL path / Redis key delimiter
        "'; DROP TABLE--", # SQL inject attempt
    ],
)
def test_chat_request_rejects_illegal_workspace_slugs(bad_slug: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(
            bot_id="support",
            channel_type="web",
            workspace_id=bad_slug,
            user_id="u1",
            content="hello",
        )
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("workspace_id",) for e in errors), (
        f"expected workspace_id field error for {bad_slug!r}; got {errors!r}"
    )


def test_chat_request_rejects_over_max_length() -> None:
    too_long = "a" * (WORKSPACE_ID_MAX_LEN + 1)
    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(
            bot_id="support",
            channel_type="web",
            workspace_id=too_long,
            user_id="u1",
            content="hello",
        )
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("workspace_id",) for e in errors)


def test_chat_request_accepts_max_length_slug() -> None:
    """Boundary case: exactly ``WORKSPACE_ID_MAX_LEN`` chars must pass."""
    boundary = "a" * WORKSPACE_ID_MAX_LEN
    req = ChatRequest(
        bot_id="support",
        channel_type="web",
        workspace_id=boundary,
        user_id="u1",
        content="hello",
    )
    assert req.workspace_id == boundary


# ---------------------------------------------------------------------------
# 4. Validator rejection — direct calls behave the same way
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    [
        "has space",
        "dự án",
        "team_alpha",
        "@admin",
        "#channel",
        "$root",
        "a" * (WORKSPACE_ID_MAX_LEN + 1),
        "",
    ],
)
def test_validator_rejects_bad_slugs(bad_slug: str) -> None:
    with pytest.raises(WorkspaceIdInvalid):
        WorkspaceIdValidator.validate(bad_slug)


def test_validator_accepts_system_slug() -> None:
    """The reserved system slug is a valid identifier and must round-trip
    intact through the validator. It is the slug used for tenant-level /
    forensic rows that do not belong to any tenant-supplied workspace.
    """
    out = WorkspaceIdValidator.validate(WORKSPACE_SYSTEM_SLUG)
    assert out == WORKSPACE_SYSTEM_SLUG
