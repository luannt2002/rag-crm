"""Invariant — record_tenant_id UUID schema.

Pins the canonical schema shape so a future regression can't silently
re-introduce ``bots.tenant_id`` INT or rename the DTO field.
"""

from __future__ import annotations

import inspect
from uuid import UUID

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.services.bot_management_service import CreateBotCommand
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.infrastructure.db.models import BotModel
from ragbot.infrastructure.repositories.bot_repository import SqlAlchemyBotRepository


def test_bot_config_field_name_is_record_tenant_id() -> None:
    assert "record_tenant_id" in BotConfig.model_fields
    assert "tenant_id" not in BotConfig.model_fields


def test_bot_config_record_tenant_id_is_uuid_typed() -> None:
    field = BotConfig.model_fields["record_tenant_id"]
    assert field.annotation is UUID


def test_create_bot_command_field_is_record_tenant_id() -> None:
    assert "record_tenant_id" in CreateBotCommand.model_fields
    assert "tenant_id" not in CreateBotCommand.model_fields


def test_bot_model_has_record_tenant_id_column() -> None:
    cols = {c.name for c in BotModel.__table__.columns}
    assert "record_tenant_id" in cols
    assert "tenant_id" not in cols


def test_repository_find_by_4key_signature() -> None:
    sig = inspect.signature(SqlAlchemyBotRepository.find_by_4key)
    params = list(sig.parameters.keys())
    # Positional order: self, record_tenant_id, workspace_id, bot_id,
    # channel_type — pinning the order keeps the lookup contract honest.
    assert params[1] == "record_tenant_id"
    assert params[2] == "workspace_id"
    assert params[3] == "bot_id"
    assert params[4] == "channel_type"


def test_registry_lookup_signature() -> None:
    sig = inspect.signature(BotRegistryService.lookup)
    params = list(sig.parameters.keys())
    assert params[1] == "record_tenant_id"
    assert params[2] == "workspace_id"
    assert params[3] == "bot_id"
    assert params[4] == "channel_type"


def test_bot_model_record_tenant_id_not_null() -> None:
    col = BotModel.__table__.columns["record_tenant_id"]
    assert col.nullable is False


def test_bot_model_unique_constraint_on_4key() -> None:
    """Bot uniqueness is the 4-tuple (record_tenant_id, workspace_id,
    bot_id, channel_type). The earlier 3-key constraint was dropped to
    support per-tenant workspace isolation; same tenant can host the same
    (bot_id, channel_type) slug
    across distinct workspaces.
    """
    constraints = {c.name for c in BotModel.__table__.constraints}
    assert "uq_bots_record_tenant_workspace_bot_channel" in constraints
    # The narrower constraint must not coexist — its presence would re-open
    # the cross-workspace collision the wider constraint closes.
    assert "uq_bots_record_tenant_bot_channel" not in constraints
