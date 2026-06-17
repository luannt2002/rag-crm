"""Elevate provider/model mutate permissions to super_admin.

Background
----------
Provider_*/model_* mutate routes were previously seeded at level 80 (tenant).
This
is unsafe because ``ai_providers`` and ``ai_models`` rows do NOT carry a
``record_tenant_id`` column — they are platform-shared resources. A tenant
admin (level 80) editing provider X transparently affects every other tenant
that points at the same provider.

Resolution
----------
Move all mutate permissions on platform-shared AI resources to level 100
(super_admin only). Read-like permissions (provider_read, provider_test) stay
at 60 (admin) because reads do not change shared state.

For ``bot_model_bindings`` rows the picture is different — they DO carry
``record_tenant_id`` so tenant admins are still allowed (level 80) but the
route handlers add a row-level ownership pre-verify (see
``interfaces/http/_resource_ownership.py``).

Idempotent: ON CONFLICT (module, permission) DO UPDATE SET
min_role_level = EXCLUDED.min_role_level.

Run:
    set -a && source .env && set +a
    python3 scripts/seed_rbac_permissions_s12a.py

After:
    redis-cli DEL ragbot:rbac:perms
"""

from __future__ import annotations

import os
import sys
from typing import Final

from sqlalchemy import create_engine, text

from ragbot.shared.constants import (
    DEFAULT_SERVICE_LEVEL,
    DEFAULT_SUPER_ADMIN_LEVEL,
    DEFAULT_TENANT_ADMIN_LEVEL,
)


# Elevate platform-shared AI mutate gates to super_admin.
# Reads / test-call stay at the historic baseline; this list only flips
# mutate gates that touch platform-shared resources.
ELEVATED_PERMISSIONS: Final[list[tuple[str, str, int]]] = [
    ("ai", "provider_create", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "provider_update", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "provider_delete", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "provider_rotate_key", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "model_create", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "model_update", DEFAULT_SUPER_ADMIN_LEVEL),
    ("ai", "model_delete", DEFAULT_SUPER_ADMIN_LEVEL),
]

# Admin tenant policy CRUD endpoints.
# - tenant:policy_read   level 80 (tenant admin reads its own row only —
#                                  cross-tenant reads are blocked at the
#                                  route layer, returning 404 to avoid
#                                  enumeration).
# - tenant:policy_update level 100 (super_admin only — flipping
#                                   bypass_rate_limit / rate_limit_per_min /
#                                   monthly_token_cap is a platform-trust
#                                   change that can affect cost + abuse
#                                   posture across tenants).
TENANT_POLICY_PERMISSIONS: Final[list[tuple[str, str, int]]] = [
    ("tenant", "policy_read", DEFAULT_TENANT_ADMIN_LEVEL),
    ("tenant", "policy_update", DEFAULT_SUPER_ADMIN_LEVEL),
]

# Production SSE streaming endpoint POST /chat/stream.
# Mirrors ``chat:submit`` (level 50 — service token from upstream NestJS).
# Tenant end-users do not call /chat/stream directly; the upstream service
# proxies on their behalf with its service token, so the gate stays at 50.
CHAT_STREAM_PERMISSIONS: Final[list[tuple[str, str, int]]] = [
    ("chat", "stream", DEFAULT_SERVICE_LEVEL),
]

# Combined list — order doesn't matter (ON CONFLICT idempotent), but keep
# Item 1 entries first so existing print-output ordering stays familiar.
ALL_PERMISSIONS: Final[list[tuple[str, str, int]]] = (
    ELEVATED_PERMISSIONS + TENANT_POLICY_PERMISSIONS + CHAT_STREAM_PERMISSIONS
)


def main() -> int:
    dsn = os.getenv("DATABASE_URL_SYNC") or (
        os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    )
    if not dsn:
        print("ERROR: DATABASE_URL / DATABASE_URL_SYNC env required", file=sys.stderr)
        return 1

    engine = create_engine(dsn)
    with engine.begin() as conn:
        for module, permission, level in ALL_PERMISSIONS:
            conn.execute(
                text(
                    """
                    INSERT INTO module_permissions(module, permission, min_role_level)
                    VALUES (:m, :p, :l)
                    ON CONFLICT (module, permission)
                    DO UPDATE SET min_role_level = EXCLUDED.min_role_level
                    """,
                ),
                {"m": module, "p": permission, "l": level},
            )
        rows = conn.execute(
            text(
                """
                SELECT module, permission, min_role_level
                FROM module_permissions
                WHERE (module, permission) IN (
                    ('ai', 'provider_create'),
                    ('ai', 'provider_update'),
                    ('ai', 'provider_delete'),
                    ('ai', 'provider_rotate_key'),
                    ('ai', 'model_create'),
                    ('ai', 'model_update'),
                    ('ai', 'model_delete'),
                    ('tenant', 'policy_read'),
                    ('tenant', 'policy_update')
                )
                ORDER BY module, permission
                """,
            ),
        ).fetchall()

    print(
        f"Seeded {len(ALL_PERMISSIONS)} permissions "
        f"(Item 1 elevate: {len(ELEVATED_PERMISSIONS)}, "
        f"Item 2 tenant-policy: {len(TENANT_POLICY_PERMISSIONS)})",
    )
    for r in rows:
        print(f"  {r[0]}:{r[1]} = {r[2]}")
    print("Remember to invalidate the Redis cache:")
    print("    redis-cli DEL ragbot:rbac:perms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
