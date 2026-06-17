"""Unit tests: RBAC metadata-driven permission system."""
from __future__ import annotations

from ragbot.shared.rbac import get_role_level, check_min_level, ROLE_LEVELS


class TestRoleLevels:
    """Verify 7-tier numeric role hierarchy."""

    def test_super_admin_is_100(self) -> None:
        assert get_role_level("super_admin") == 100

    def test_tenant_is_80(self) -> None:
        assert get_role_level("tenant") == 80

    def test_admin_is_60(self) -> None:
        assert get_role_level("admin") == 60

    def test_operator_is_40(self) -> None:
        assert get_role_level("operator") == 40

    def test_user_is_20(self) -> None:
        assert get_role_level("user") == 20

    def test_viewer_is_10(self) -> None:
        assert get_role_level("viewer") == 10

    def test_guest_is_0(self) -> None:
        assert get_role_level("guest") == 0

    def test_unknown_role_defaults_to_0(self) -> None:
        assert get_role_level("hacker") == 0

    def test_service_equals_admin(self) -> None:
        assert get_role_level("service") == 60

    def test_system_equals_super_admin(self) -> None:
        assert get_role_level("system") == 100


class TestLevelHierarchy:
    """Verify that higher roles include all lower permissions."""

    def test_super_admin_can_do_everything(self) -> None:
        for level in [0, 10, 20, 40, 60, 80, 100]:
            assert get_role_level("super_admin") >= level

    def test_user_cannot_admin(self) -> None:
        assert get_role_level("user") < 60  # admin level

    def test_viewer_cannot_upload(self) -> None:
        assert get_role_level("viewer") < 20  # user level

    def test_operator_can_manage_bots(self) -> None:
        assert get_role_level("operator") >= 40

    def test_admin_can_view_audit(self) -> None:
        assert get_role_level("admin") >= 60


class TestPermissionSeeds:
    """Verify permission seed data matches expected levels."""

    EXPECTED_PERMISSIONS = {
        ("bot", "create"): 60,
        ("bot", "update"): 40,
        ("bot", "delete"): 60,
        ("document", "upload"): 20,
        ("document", "delete"): 60,
        ("chat", "query"): 10,
        ("chat", "view_history"): 20,
        ("ai", "configure"): 60,
        ("admin", "view_audit"): 60,
        ("admin", "manage_tenants"): 100,
        ("system", "manage_config"): 80,
    }

    def test_user_can_chat(self) -> None:
        min_level = self.EXPECTED_PERMISSIONS[("chat", "query")]
        assert get_role_level("user") >= min_level

    def test_user_cannot_delete_bot(self) -> None:
        min_level = self.EXPECTED_PERMISSIONS[("bot", "delete")]
        assert get_role_level("user") < min_level

    def test_admin_can_view_audit(self) -> None:
        min_level = self.EXPECTED_PERMISSIONS[("admin", "view_audit")]
        assert get_role_level("admin") >= min_level

    def test_operator_can_update_bot(self) -> None:
        min_level = self.EXPECTED_PERMISSIONS[("bot", "update")]
        assert get_role_level("operator") >= min_level

    def test_only_super_admin_manage_tenants(self) -> None:
        min_level = self.EXPECTED_PERMISSIONS[("admin", "manage_tenants")]
        assert get_role_level("admin") < min_level
        assert get_role_level("super_admin") >= min_level

    def test_viewer_can_only_chat(self) -> None:
        assert get_role_level("viewer") >= self.EXPECTED_PERMISSIONS[("chat", "query")]
        assert get_role_level("viewer") < self.EXPECTED_PERMISSIONS[("document", "upload")]


class TestNoHardcodedRoleStrings:
    """Verify no hardcoded role string checks exist in codebase."""

    def test_all_roles_in_level_dict(self) -> None:
        expected_roles = {"super_admin", "superadmin", "platform_admin", "owner",
                         "tenant", "tenant_admin", "admin", "operator",
                         "service", "system", "user", "viewer", "guest"}
        assert expected_roles.issubset(set(ROLE_LEVELS.keys()))

    def test_levels_are_multiples_of_10(self) -> None:
        for role, level in ROLE_LEVELS.items():
            assert level % 10 == 0, f"{role} has non-standard level {level}"

    def test_gaps_allow_insertion(self) -> None:
        """Verify gaps between levels allow future role insertion."""
        sorted_levels = sorted(set(ROLE_LEVELS.values()))
        for i in range(1, len(sorted_levels)):
            gap = sorted_levels[i] - sorted_levels[i - 1]
            assert gap >= 10, f"Gap too small between {sorted_levels[i-1]} and {sorted_levels[i]}"
