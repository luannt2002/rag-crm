"""Workspace_id slug validator + fallback resolver.

Pure-function module — no I/O, no DB, no Redis. The validator enforces a
strict ASCII slug shape so the 4-key identity ``(record_tenant_id,
workspace_id, bot_id, channel_type)`` flows through URL paths, Redis keys,
log labels, and SQL WHERE clauses without escaping or transformation.

Resolution rule for missing values follows ``DEFAULT_WORKSPACE_FALLBACK_MODE``:
when the wire payload omits ``workspace_id``, the resolver substitutes
``str(record_tenant_id)`` so legacy callers without a workspace claim land
on a deterministic per-tenant slug. Empty-string is treated identically to
``None`` to keep the fallback symmetric across JSON ``null`` and missing
keys.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog

from ragbot.shared.constants import (
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_MIN_LEN,
    WORKSPACE_ID_PATTERN,
    WORKSPACE_ID_PATTERN_HUMAN,
)
from ragbot.shared.errors import WorkspaceIdInvalid
from ragbot.shared.types import WorkspaceId

_logger = structlog.get_logger(__name__)


class WorkspaceIdValidator:
    """Validate slug format. Pure function, no I/O."""

    # ASCII-only flag prevents Unicode digits / letters from sneaking past
    # the regex (`re.compile` defaults to Unicode-aware classes).
    _COMPILED = re.compile(WORKSPACE_ID_PATTERN, flags=re.ASCII)

    @classmethod
    def validate(cls, value: Any) -> WorkspaceId:
        """Return ``value`` as ``WorkspaceId`` when valid, else raise.

        Raises ``WorkspaceIdInvalid`` (HTTP 422) on:
        - ``None`` input
        - non-string type
        - empty string / length below ``WORKSPACE_ID_MIN_LEN``
        - length above ``WORKSPACE_ID_MAX_LEN``
        - regex mismatch (space, accent, underscore, special chars)
        """
        if value is None:
            raise WorkspaceIdInvalid("workspace_id missing (None)")
        if not isinstance(value, str):
            raise WorkspaceIdInvalid(
                f"workspace_id must be string, got {type(value).__name__}"
            )
        n = len(value)
        if n < WORKSPACE_ID_MIN_LEN:
            raise WorkspaceIdInvalid("workspace_id empty")
        if n > WORKSPACE_ID_MAX_LEN:
            raise WorkspaceIdInvalid(
                f"workspace_id too long: {n} chars "
                f"(max={WORKSPACE_ID_MAX_LEN})"
            )
        if not cls._COMPILED.fullmatch(value):
            raise WorkspaceIdInvalid(
                f"workspace_id invalid format: {WORKSPACE_ID_PATTERN_HUMAN}"
            )
        return WorkspaceId(value)


def resolve_workspace_id(
    value: str | None,
    *,
    record_tenant_id: UUID,
    warn_on_fallback: bool = True,
) -> WorkspaceId:
    """Resolve workspace_id from request body with tenant-UUID fallback.

    Missing / empty → ``str(record_tenant_id)`` UUID slug (the
    tenant-uuid fallback mode). Non-empty → strict format check via
    ``WorkspaceIdValidator.validate``.

    The UUID stringification is the canonical 36-char hyphenated form,
    which matches ``WORKSPACE_ID_PATTERN`` (digits + letters + hyphen) and
    fits within ``WORKSPACE_ID_MAX_LEN``, so the fallback is always valid
    by construction.

    @param warn_on_fallback: when True (default), emit a structured
        ``workspace_id_fallback_to_tenant_uuid`` warning event so ops can
        track which callers still ship requests without an explicit
        workspace_id. Audit tools / batch jobs that DELIBERATELY want
        the tenant-uuid fallback (e.g. tenant-level forensic queries)
        pass ``warn_on_fallback=False`` to silence the breadcrumb.
    """
    if value is None or value == "":
        if warn_on_fallback:
            _logger.warning(
                "workspace_id_fallback_to_tenant_uuid",
                record_tenant_id=str(record_tenant_id),
                reason="missing_or_empty",
            )
        return WorkspaceId(str(record_tenant_id))
    return WorkspaceIdValidator.validate(value)


__all__ = [
    "WorkspaceIdValidator",
    "resolve_workspace_id",
]
