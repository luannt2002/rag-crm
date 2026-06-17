"""P26 L2 — Pydantic max_length cap on bot system_prompt.

Guards DB storage + downstream token budget from pathological admin input.
Cap is MAX_SYSTEM_PROMPT_CHARS (5_000 ≈ 2500 tokens, below the ~3000-tok
reasoning-degradation threshold) enforced on both
CreateBotCommand (create) and UpdateBotCommand (patch).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.application.services.bot_management_service import (
    CreateBotCommand,
    UpdateBotCommand,
)
from ragbot.shared.constants import MAX_SYSTEM_PROMPT_CHARS
from tests.conftest import TEST_TENANT_UUID


# Common valid required fields for CreateBotCommand fixtures — keep
# system_prompt as the ONLY varying field under test.
# record_tenant_id is REQUIRED UUID.
_VALID_CREATE_KWARGS = {
    "bot_id": "b",
    "channel_type": "web",
    "bot_name": "Test",
    "record_tenant_id": TEST_TENANT_UUID,
}


def test_create_accepts_under_cap() -> None:
    """19_000 chars < 20_000 cap → accepted."""
    cmd = CreateBotCommand(
        **_VALID_CREATE_KWARGS,
        system_prompt="x" * (MAX_SYSTEM_PROMPT_CHARS - 1_000),
    )
    assert len(cmd.system_prompt) == MAX_SYSTEM_PROMPT_CHARS - 1_000


def test_create_rejects_over_cap() -> None:
    """20_001 chars > 20_000 cap → ValidationError."""
    with pytest.raises(ValidationError):
        CreateBotCommand(
            **_VALID_CREATE_KWARGS,
            system_prompt="x" * (MAX_SYSTEM_PROMPT_CHARS + 1),
        )


def test_patch_accepts_under_cap() -> None:
    """Patch command accepts system_prompt under the cap."""
    cmd = UpdateBotCommand(
        system_prompt="x" * (MAX_SYSTEM_PROMPT_CHARS - 1_000),
    )
    assert cmd.system_prompt is not None
    assert len(cmd.system_prompt) == MAX_SYSTEM_PROMPT_CHARS - 1_000


def test_patch_over_cap_rejects() -> None:
    """Patch command rejects system_prompt over the cap."""
    with pytest.raises(ValidationError):
        UpdateBotCommand(
            system_prompt="x" * (MAX_SYSTEM_PROMPT_CHARS + 1),
        )


def test_patch_allows_none() -> None:
    """Patch command allows None (field is optional for partial updates)."""
    cmd = UpdateBotCommand(system_prompt=None)
    assert cmd.system_prompt is None
