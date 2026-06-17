"""DomainEvent base class.

Ref: PLAN_04 §events / RAGBOT_MASTER §14.2 Event contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from ragbot.shared.types import TenantId, TraceId


@dataclass(frozen=True, kw_only=True, slots=True)
class DomainEvent:
    """Base for all domain events. Every concrete event sets `event_type`."""

    event_type: ClassVar[str] = "domain.event"
    schema_version: ClassVar[str] = "v1"

    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime
    record_tenant_id: TenantId
    trace_id: TraceId

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type
        d["schema_version"] = self.schema_version
        d["event_id"] = str(self.event_id)
        d["occurred_at"] = self.occurred_at.isoformat()
        return d

    @property
    def subject(self) -> str:
        """Event subject: `<event_type>` (already includes vN suffix)."""
        return self.event_type


__all__ = ["DomainEvent"]
