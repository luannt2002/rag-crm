"""RBAC gates for the test_chat harness — destructive-endpoint protection.

Covers the cross-tenant hard-delete escalation chain proven in the deep audit
(``reports/EXPERT_DEEP_AUDIT_20260623.md`` — CHAT + TEST-CHAT, finding D-B1/D-B2):

  * D-B1 — every destructive test_chat handler must enforce a permission/owner
    gate at the top (``delete_bot``, ``update_bot``, ``test_chat_clear``,
    ``reinit_bots``, ``delete_document``).
  * D-B2 — the ``test_chat.router`` + ``chat_async.router`` mounts must carry a
    router-level dependency so the whole harness is fail-closed by default.

The tests drive the imperative gate (``require_permission``) + the new
``require_min_level_dep`` factory directly with a mocked Redis + container, the
same lightweight pattern used by ``test_rbac_admin_ai.py`` (no full app spin-up).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.interfaces.http.middlewares.rbac import require_permission
from ragbot.shared.errors import ForbiddenError

# Seed levels mirrored from scripts/seed_rbac_permissions_s11b.py for the
# destructive test_chat handlers. A drift here vs the DB seed trips CI rather
# than silently downgrading a gate.
_DESTRUCTIVE_PERMISSIONS: dict[str, int] = {
    "bot:delete": 60,
    "bot:update": 40,
    "document:delete": 60,
}


def _fake_request(role: str) -> Any:
    """Minimal request stub with a Redis client returning the seed map."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(_DESTRUCTIVE_PERMISSIONS))
    redis.set = AsyncMock(return_value=None)
    container = MagicMock()
    container.redis_client = MagicMock(return_value=redis)
    container.session_factory = MagicMock()
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(app=app, state=SimpleNamespace(role=role))


# ---------------------------------------------------------------------------
# D-B1 — imperative permission gate per destructive handler.
# ---------------------------------------------------------------------------


class TestDestructivePermissionGate:
    @pytest.mark.asyncio
    async def test_guest_blocked_on_every_destructive_permission(self) -> None:
        req = _fake_request("guest")  # level 0
        for key in _DESTRUCTIVE_PERMISSIONS:
            module, perm = key.split(":")
            with pytest.raises(ForbiddenError):
                await require_permission(req, module, perm)

    @pytest.mark.asyncio
    async def test_user_blocked_on_bot_delete_and_document_delete(self) -> None:
        req = _fake_request("user")  # level 20 < 60
        with pytest.raises(ForbiddenError):
            await require_permission(req, "bot", "delete")
        with pytest.raises(ForbiddenError):
            await require_permission(req, "document", "delete")

    @pytest.mark.asyncio
    async def test_operator_can_update_bot_but_not_delete(self) -> None:
        req = _fake_request("operator")  # level 40
        await require_permission(req, "bot", "update")  # 40 — exact pass
        with pytest.raises(ForbiddenError):
            await require_permission(req, "bot", "delete")  # 60 — blocked

    @pytest.mark.asyncio
    async def test_admin_passes_all_destructive_gates(self) -> None:
        req = _fake_request("admin")  # level 60
        for key in _DESTRUCTIVE_PERMISSIONS:
            module, perm = key.split(":")
            await require_permission(req, module, perm)  # must pass


# ---------------------------------------------------------------------------
# D-B1 — static contract: each destructive handler references its gate.
# ---------------------------------------------------------------------------


class TestDestructiveHandlersWired:
    """Source-level proof the gate call is present in each handler module."""

    def test_bot_admin_routes_gate_delete_and_update(self) -> None:
        from pathlib import Path

        from ragbot.interfaces.http.routes.test_chat import bot_admin_routes

        src = Path(bot_admin_routes.__file__).read_text()
        assert 'require_permission(request, "bot", "delete")' in src
        assert 'require_permission(request, "bot", "update")' in src

    def test_chat_routes_clear_gated(self) -> None:
        from pathlib import Path

        from ragbot.interfaces.http.routes.test_chat import chat_routes

        src = Path(chat_routes.__file__).read_text()
        assert 'require_permission(request, "bot", "delete")' in src

    def test_monitoring_reinit_owner_gated(self) -> None:
        from pathlib import Path

        from ragbot.interfaces.http.routes.test_chat import monitoring_routes

        src = Path(monitoring_routes.__file__).read_text()
        # reinit_bots must call _require_owner like its sibling monitoring route.
        assert src.count("_require_owner(request)") >= 2

    def test_document_routes_delete_gated_and_tenant_scoped(self) -> None:
        from pathlib import Path

        from ragbot.interfaces.http.routes.test_chat import document_routes

        src = Path(document_routes.__file__).read_text()
        assert 'require_permission(request, "document", "delete")' in src
        # tenant-scope the existence SELECT so cross-tenant probing is impossible.
        assert "record_tenant_id" in src.split("async def delete_document")[1]


# ---------------------------------------------------------------------------
# D-B2 — router-level mount dependency factory + mount wiring.
# ---------------------------------------------------------------------------


class TestRequireMinLevelDep:
    @pytest.mark.asyncio
    async def test_dep_blocks_below_level(self) -> None:
        from ragbot.shared.rbac import require_min_level_dep

        dep = require_min_level_dep(100)
        req = SimpleNamespace(state=SimpleNamespace(role="admin"))  # 60 < 100
        with pytest.raises(ForbiddenError):
            await dep(req)

    @pytest.mark.asyncio
    async def test_dep_allows_at_or_above_level(self) -> None:
        from ragbot.shared.rbac import require_min_level_dep

        dep = require_min_level_dep(100)
        req = SimpleNamespace(state=SimpleNamespace(role="super_admin"))  # 100
        await dep(req)  # no exception

    def test_dep_name_is_self_describing(self) -> None:
        from ragbot.shared.rbac import require_min_level_dep

        dep = require_min_level_dep(100)
        assert dep.__name__ == "require_min_level_100"


class TestHarnessMountGated:
    """The whole test_chat + chat_async mounts must be fail-closed."""

    def _test_route_dep_names(self) -> list[str]:
        """Return dependency closure names attached to every /test/* route.

        ``include_router(..., dependencies=[...])`` copies the dependency onto
        each child route, so inspecting any route's ``dependencies`` proves the
        mount-level gate is present.
        """
        from ragbot.interfaces.http.router import router as top

        names: list[str] = []
        for r in top.routes:
            path = getattr(r, "path", "")
            if "/test/" not in path:
                continue
            for dep in getattr(r, "dependencies", ()) or ():
                fn = getattr(dep, "dependency", None)
                nm = getattr(fn, "__name__", "") if fn else ""
                names.append(nm)
        return names

    def test_test_chat_routes_carry_min_level_gate(self) -> None:
        names = self._test_route_dep_names()
        assert any(n.startswith("require_min_level_") for n in names), (
            f"no router-level min-level gate found on /test/* routes: {names}"
        )


# ---------------------------------------------------------------------------
# D-A3 — quota fail-open narrowed + observable.
# ---------------------------------------------------------------------------


class TestQuotaGateNarrowAndObservable:
    """The quota gate must catch narrow types and emit a distinct bypass event.

    A bare ``except Exception`` here fails OPEN on ANY error (incl. a logic bug
    in the gate itself), leaking paid quota silently. The fix narrows to
    transient (SQLAlchemyError, RedisError, ValueError, TypeError) and emits a
    deliberately-named ``quota_gate_bypassed`` event so the bypass is alertable.
    """

    def _src(self, module) -> str:
        from pathlib import Path

        return Path(module.__file__).read_text()

    def test_chat_async_quota_gate_narrowed(self) -> None:
        from ragbot.interfaces.http.routes import chat_async

        src = self._src(chat_async)
        assert "except (SQLAlchemyError, RedisError, ValueError, TypeError)" in src
        # The deliberately-named observable bypass event.
        assert '"quota_gate_bypassed"' in src
        # No bare broad-except left on the quota gate path.
        assert "except Exception as quota_exc" not in src

    def test_test_chat_quota_gate_narrowed(self) -> None:
        from ragbot.interfaces.http.routes.test_chat import chat_routes

        src = self._src(chat_routes)
        assert "except (SQLAlchemyError, RedisError, ValueError, TypeError)" in src
        assert '"quota_gate_bypassed"' in src
        assert "except Exception as _qe" not in src
