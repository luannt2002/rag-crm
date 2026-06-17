"""Unit test: ORM model record_tenant_id must be NOT NULL on core tables.

Phase 2 Y1 infra audit 2026-04-29: P0-BUG-3 / P1-BUG-6 fix verification.

Ensures that the SQLAlchemy ORM models declare record_tenant_id as
nullable=False on core operational tables. The Alembic migration 0049
enforces this at the DB level (safe check before altering).
"""

from __future__ import annotations

import pytest


# (table_name, model_class) pairs that MUST have nullable=False
_CORE_MODELS = [
    ("conversations", "ConversationModel"),
    ("messages", "MessageModel"),
    ("documents", "DocumentModel"),
    ("jobs", "JobModel"),
    ("outbox", "OutboxModel"),
    ("bot_model_bindings", "BotModelBindingModel"),
    ("prompt_templates", "PromptTemplateModel"),
    ("audit_log", "AuditLogModel"),
    ("request_logs", "RequestLogModel"),
    ("guardrail_events", "GuardrailEventModel"),
]


def _get_model_class(name: str):
    """Lazy import to avoid circular dependency."""
    from ragbot.infrastructure.db import models, models_monitoring, models_guardrail

    for module in (models, models_monitoring, models_guardrail):
        if hasattr(module, name):
            return getattr(module, name)
    raise ImportError(f"Cannot find model class {name!r}")


@pytest.mark.parametrize("table_name,model_name", _CORE_MODELS)
def test_record_tenant_id_not_nullable(table_name: str, model_name: str) -> None:
    """ORM model must declare record_tenant_id as nullable=False."""
    cls = _get_model_class(model_name)
    table = cls.__table__
    assert "record_tenant_id" in table.c, (
        f"{model_name} ({table_name!r}) has no record_tenant_id column"
    )
    col = table.c["record_tenant_id"]
    assert col.nullable is False, (
        f"{model_name}.record_tenant_id is nullable=True but must be nullable=False. "
        "Migration 0049 enforces NOT NULL at DB level; ORM model must match."
    )
