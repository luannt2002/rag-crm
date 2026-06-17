"""Admin tenant CRUD request/response schemas.

Routes are super-admin-only (``record_tenant_id``-agnostic on the wire —
the path UUID is the *target* tenant, not a JWT-bound caller scope).
Pydantic v2 (``ConfigDict(extra="forbid")`` + ``Field(pattern=...)``).

Slug semantics
--------------
``slug`` is a platform-issued routing identifier (URL-safe, lowercase,
hyphenated). It is NOT the legacy upstream NestJS ``tenant_id`` int —
that integer (when present) is stored in ``config['upstream_tenant_id']``
so legacy ``/sync/*`` routes can still translate INT → UUID.

Domain-neutral: ``slug`` regex rejects underscores, dots, and uppercase
to avoid downstream filesystem / URL collisions across tenants.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ragbot.shared.constants import (
    DEFAULT_ADMIN_TENANT_NAME_MAX_LENGTH,
    DEFAULT_ADMIN_TENANT_SLUG_MAX_LENGTH,
)

# URL-safe lowercase slug — ``[a-z0-9]`` plus ``-`` separator. Bound by
# ``DEFAULT_ADMIN_TENANT_SLUG_MAX_LENGTH``. Empty string rejected via
# ``min_length=1``. Single source of truth for the regex; the repo and tests
# import this constant rather than duplicating the literal.
TENANT_SLUG_PATTERN: str = r"^[a-z0-9][a-z0-9-]*$"


class TenantCreateRequest(BaseModel):
    """POST /admin/tenants body — admin-issued tenant onboarding."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=DEFAULT_ADMIN_TENANT_NAME_MAX_LENGTH,
        description="Human-readable tenant name (display only).",
    )
    slug: str = Field(
        min_length=1,
        max_length=DEFAULT_ADMIN_TENANT_SLUG_MAX_LENGTH,
        pattern=TENANT_SLUG_PATTERN,
        description=(
            "URL-safe routing slug — lowercase, digits, hyphen. "
            "Stored on ``tenants.config['slug']`` (the schema does not yet "
            "have a dedicated column; the JSONB key is the canonical store)."
        ),
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Optional initial JSONB config payload.",
    )
    upstream_tenant_id: int | None = Field(
        default=None,
        description=(
            "Optional legacy NestJS upstream INT id. When present, persisted "
            "as ``config['upstream_tenant_id']`` so /sync/* routes can map "
            "INT → UUID for the rolling-upgrade window."
        ),
    )


class TenantPatchRequest(BaseModel):
    """PATCH /admin/tenants/{id} body — every field optional.

    Matches ``admin_tenant_policy``'s patch semantics: ``None`` means
    "do not change"; non-null (incl. ``0``) is a real write. ``slug`` is
    deliberately immutable post-create — slug rotation breaks every cached
    URL and existing integration. Use a manual SQL window if needed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=DEFAULT_ADMIN_TENANT_NAME_MAX_LENGTH,
    )
    config: dict[str, Any] | None = None
    bypass_rate_limit: bool | None = None
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)
    # Per-tenant CORS strict whitelist. Each entry is an exact origin
    # (``https://app.example.com``) or wildcard pattern with a leading
    # ``*.`` host part (``https://*.example.com``). Empty list = block
    # all browser cross-origin traffic for this tenant.
    allowed_origins: list[str] | None = Field(
        default=None,
        description=(
            "Per-tenant CORS strict whitelist. Exact origin or wildcard "
            "pattern (``https://*.example.com``). [] = block all."
        ),
    )


class TenantResponse(BaseModel):
    """Single tenant row — used by POST/GET/PATCH responses."""

    record_tenant_id: UUID
    name: str
    slug: str | None  # legacy rows may not have slug yet
    config: dict[str, Any]
    bypass_rate_limit: bool
    rate_limit_per_min: int | None
    monthly_token_cap: int | None
    allowed_origins: list[str] = []
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class TenantListResponse(BaseModel):
    """Paginated list of tenants."""

    items: list[TenantResponse]
    total: int
    limit: int
    offset: int


__all__ = [
    "TENANT_SLUG_PATTERN",
    "TenantCreateRequest",
    "TenantListResponse",
    "TenantPatchRequest",
    "TenantResponse",
]
