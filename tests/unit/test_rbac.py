"""Tests for ragbot.shared.rbac — centralized RBAC role levels."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ragbot.shared.rbac import (
    ROLE_LEVELS,
    check_min_level,
    get_role_level,
    require_min_level,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_request(role: str = "guest") -> MagicMock:
    """Return a minimal Request-like object with ``state.role``."""
    req = MagicMock()
    req.state = SimpleNamespace(role=role)
    return req


# ---------------------------------------------------------------------------
# get_role_level
# ---------------------------------------------------------------------------

class TestGetRoleLevel:
    def test_known_roles(self) -> None:
        assert get_role_level("super_admin") == 100
        assert get_role_level("superadmin") == 100
        assert get_role_level("tenant") == 80
        assert get_role_level("admin") == 60
        assert get_role_level("operator") == 40
        assert get_role_level("user") == 20
        assert get_role_level("viewer") == 10
        assert get_role_level("guest") == 0

    def test_unknown_role_is_guest(self) -> None:
        assert get_role_level("hacker") == 0
        assert get_role_level("") == 0

    def test_service_is_admin_level(self) -> None:
        assert get_role_level("service") == 60

    def test_system_is_super(self) -> None:
        assert get_role_level("system") == 100

    def test_tenant_admin_alias(self) -> None:
        assert get_role_level("tenant_admin") == 80

    def test_platform_admin_alias(self) -> None:
        assert get_role_level("platform_admin") == 100


# ---------------------------------------------------------------------------
# check_min_level
# ---------------------------------------------------------------------------

class TestCheckMinLevel:
    def test_admin_passes_60(self) -> None:
        assert check_min_level(_fake_request("admin"), 60) is True

    def test_user_fails_60(self) -> None:
        assert check_min_level(_fake_request("user"), 60) is False

    def test_superadmin_passes_any(self) -> None:
        assert check_min_level(_fake_request("superadmin"), 100) is True

    def test_guest_fails_10(self) -> None:
        assert check_min_level(_fake_request("guest"), 10) is False

    def test_no_role_attr_defaults_guest(self) -> None:
        req = MagicMock()
        req.state = SimpleNamespace()  # no role attribute
        assert check_min_level(req, 10) is False

    def test_exact_boundary(self) -> None:
        assert check_min_level(_fake_request("operator"), 40) is True
        assert check_min_level(_fake_request("operator"), 41) is False


# ---------------------------------------------------------------------------
# require_min_level
# ---------------------------------------------------------------------------

class TestRequireMinLevel:
    def test_sufficient_level_no_error(self) -> None:
        require_min_level(_fake_request("superadmin"), 100)  # should not raise

    def test_insufficient_level_raises(self) -> None:
        from ragbot.shared.errors import ForbiddenError

        with pytest.raises(ForbiddenError, match="Insufficient permission"):
            require_min_level(_fake_request("user"), 60)

    def test_error_includes_level_hint(self) -> None:
        from ragbot.shared.errors import ForbiddenError

        with pytest.raises(ForbiddenError, match="level 80"):
            require_min_level(_fake_request("admin"), 80)


# ---------------------------------------------------------------------------
# ROLE_LEVELS completeness
# ---------------------------------------------------------------------------

class TestRoleLevelsCompleteness:
    def test_seven_tier_structure(self) -> None:
        """Ensure unique levels cover the documented 7-tier structure."""
        unique_levels = sorted(set(ROLE_LEVELS.values()))
        assert unique_levels == [0, 10, 20, 40, 60, 80, 100]

    def test_all_aliases_present(self) -> None:
        expected_aliases = {
            "super_admin", "superadmin", "platform_admin",
            "owner",
            "tenant", "tenant_admin",
            "admin", "operator", "service", "system",
            "user", "viewer", "guest",
        }
        assert set(ROLE_LEVELS.keys()) == expected_aliases
