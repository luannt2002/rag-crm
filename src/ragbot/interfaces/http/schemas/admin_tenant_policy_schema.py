"""admin tenant policy request/response schemas.

GET /admin/tenants/{tenant_id}/policy returns the 3 P33-era columns
(``bypass_rate_limit``, ``rate_limit_per_min``, ``monthly_token_cap``)
plus the tenant ``name`` for the ops UI.

PATCH semantics: every field is optional. A ``None`` field means "do not
change"; a non-null value (including ``0``) is a real write. ``0`` for
``rate_limit_per_min`` is a soft-unlimited override (NULL = inherit
``system_config.tenant_rate_limit_per_min``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TenantPolicyResponse(BaseModel):
    """Read response shape for GET /admin/tenants/{id}/policy."""

    record_tenant_id: str
    name: str
    bypass_rate_limit: bool
    rate_limit_per_min: int | None
    monthly_token_cap: int | None


class TenantPolicyUpdateRequest(BaseModel):
    """PATCH body — every field optional. ``None`` = do not change."""

    model_config = ConfigDict(extra="forbid")

    bypass_rate_limit: bool | None = None
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)


__all__ = ["TenantPolicyResponse", "TenantPolicyUpdateRequest"]
