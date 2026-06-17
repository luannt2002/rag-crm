"""bonus — AuditLogModel kwarg consistency guard.

Lesson learned regression test. The pre-fix bug
(``AuditLogModel(tenant_id=...)`` while the column is ``record_tenant_id``)
was hidden for the project's lifetime by a broad ``except Exception``
swallow. This test pins the column-name contract so future drift fails
fast at unit-test time, before it reaches production.
"""

from __future__ import annotations

import pytest


def test_audit_log_model_uses_record_tenant_id() -> None:
    """``AuditLogModel`` must expose ``record_tenant_id``, never plain ``tenant_id``.

    Reject any future migration that renames the column back without
    also fixing every kwarg call-site.
    """
    from ragbot.infrastructure.db.models import AuditLogModel
    fields = AuditLogModel.__table__.columns.keys()
    assert "record_tenant_id" in fields
    assert "tenant_id" not in fields  # explicit reject — drift guard


def test_audit_log_model_rejects_backcompat_kwarg() -> None:
    """Constructing with the old ``tenant_id=`` kwarg must raise.

    Pre-fix the constructor silently accepted it as a stray attribute
    and the broad-except above swallowed the SQLAlchemy ``TypeError``
    raised at flush time. The current ORM mapping rejects it at
    construction — keeping the failure shallow.
    """
    from ragbot.infrastructure.db.models import AuditLogModel
    with pytest.raises(TypeError):
        AuditLogModel(
            tenant_id=None,  # type: ignore[call-arg]
            actor_user_id="x",
            action="create",
            resource_type="bot",
            resource_id="y",
        )
