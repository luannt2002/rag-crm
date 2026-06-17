"""Unit tests for shared.workspace_id_validator.

Coverage axes:
- Format regex (accept lowercase / uppercase / digits / hyphen).
- Reject space, accent, underscore, slash, dot, leading/trailing whitespace.
- Length bounds (min 1, max 64).
- Type guard (None / int / list).
- ``resolve_workspace_id`` UUID fallback for missing/empty input.
- Exception type contract: ``WorkspaceIdInvalid`` is a ``ValueError``-like
  ``InfrastructureError`` with ``http_status=422`` and a stable ``code``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from ragbot.shared.constants import WORKSPACE_ID_MAX_LEN
from ragbot.shared.errors import InfrastructureError, WorkspaceIdInvalid
from ragbot.shared.workspace_id_validator import (
    WorkspaceIdValidator,
    resolve_workspace_id,
)


# ---------------------------------------------------------------------------
# WorkspaceIdValidator.validate — accepts
# ---------------------------------------------------------------------------
class TestValidateAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "sales",
            "marketing",
            "Sales-Q4",
            "prod-ws-2024",
            "ABC",
            "abc123",
            "a",  # min length 1
            "0",  # digits only
            "Z" * WORKSPACE_ID_MAX_LEN,  # max length
            "tenant-uuid-12345678-1234-1234-1234-123456789012",
        ],
    )
    def test_accepts_valid_slugs(self, value: str) -> None:
        out = WorkspaceIdValidator.validate(value)
        assert out == value


# ---------------------------------------------------------------------------
# WorkspaceIdValidator.validate — rejects
# ---------------------------------------------------------------------------
class TestValidateRejects:
    @pytest.mark.parametrize(
        "value,reason",
        [
            ("sales team", "space"),
            ("sales_q4", "underscore"),
            ("dự án", "vietnamese accent"),
            ("ventas/q4", "slash"),
            ("ventas.q4", "dot"),
            ("ventas@q4", "at-sign"),
            ("ventas q4", "embedded space"),
            (" sales", "leading space"),
            ("sales ", "trailing space"),
            ("ñame", "accent"),
            ("café", "accent + e"),
            ("a b", "internal space"),
            ("foo+bar", "plus"),
            ("foo,bar", "comma"),
        ],
    )
    def test_rejects_format_violations(self, value: str, reason: str) -> None:
        with pytest.raises(WorkspaceIdInvalid) as exc_info:
            WorkspaceIdValidator.validate(value)
        assert "invalid format" in str(exc_info.value).lower() or \
            "empty" in str(exc_info.value).lower(), (
            f"unexpected message for case {reason!r}: {exc_info.value}"
        )

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(WorkspaceIdInvalid) as exc_info:
            WorkspaceIdValidator.validate("")
        assert "empty" in str(exc_info.value).lower()

    def test_rejects_too_long(self) -> None:
        too_long = "a" * (WORKSPACE_ID_MAX_LEN + 1)
        with pytest.raises(WorkspaceIdInvalid) as exc_info:
            WorkspaceIdValidator.validate(too_long)
        assert "too long" in str(exc_info.value).lower()
        assert str(WORKSPACE_ID_MAX_LEN) in str(exc_info.value)

    def test_rejects_none(self) -> None:
        with pytest.raises(WorkspaceIdInvalid) as exc_info:
            WorkspaceIdValidator.validate(None)
        assert "missing" in str(exc_info.value).lower() or \
            "none" in str(exc_info.value).lower()

    @pytest.mark.parametrize("bad_value", [123, 1.5, ["sales"], {"a": 1}, b"sales"])
    def test_rejects_non_string_type(self, bad_value: object) -> None:
        with pytest.raises(WorkspaceIdInvalid) as exc_info:
            WorkspaceIdValidator.validate(bad_value)
        assert "must be string" in str(exc_info.value).lower()

    def test_rejects_unicode_digits(self) -> None:
        # Arabic-Indic digit "٤" is a Unicode digit but not ASCII — must
        # be rejected by the re.ASCII flag.
        with pytest.raises(WorkspaceIdInvalid):
            WorkspaceIdValidator.validate("ws٤")


# ---------------------------------------------------------------------------
# resolve_workspace_id — fallback to tenant UUID
# ---------------------------------------------------------------------------
class TestResolveFallback:
    def test_fallback_on_none(self) -> None:
        tenant = uuid4()
        out = resolve_workspace_id(None, record_tenant_id=tenant)
        assert out == str(tenant)

    def test_fallback_on_empty_string(self) -> None:
        tenant = uuid4()
        out = resolve_workspace_id("", record_tenant_id=tenant)
        assert out == str(tenant)

    def test_passthrough_when_provided(self) -> None:
        tenant = uuid4()
        out = resolve_workspace_id("sales-q4", record_tenant_id=tenant)
        assert out == "sales-q4"

    def test_uuid_string_matches_format(self) -> None:
        # The fallback must always satisfy the slug regex by construction
        # (UUID string = digits + letters + hyphen, length 36).
        tenant = UUID("12345678-1234-1234-1234-123456789012")
        out = resolve_workspace_id(None, record_tenant_id=tenant)
        # Should be re-validatable.
        assert WorkspaceIdValidator.validate(out) == str(tenant)

    def test_invalid_slug_still_raises(self) -> None:
        tenant = uuid4()
        with pytest.raises(WorkspaceIdInvalid):
            resolve_workspace_id("bad slug", record_tenant_id=tenant)

    def test_fallback_emits_structured_warning_by_default(self, capsys) -> None:
        """Phase 4.5: ops needs visibility into callers still missing workspace_id."""
        tenant = uuid4()
        resolve_workspace_id(None, record_tenant_id=tenant)
        captured = capsys.readouterr()
        # structlog in test config writes the rendered event to stdout.
        # Either capture surface contains the event name + reason.
        combined = captured.out + captured.err
        assert "workspace_id_fallback_to_tenant_uuid" in combined
        assert "missing_or_empty" in combined

    def test_fallback_warn_can_be_silenced_for_known_admin_paths(self, capsys) -> None:
        """Tenant-level forensic queries pass warn_on_fallback=False to silence breadcrumb."""
        tenant = uuid4()
        resolve_workspace_id(None, record_tenant_id=tenant, warn_on_fallback=False)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "workspace_id_fallback_to_tenant_uuid" not in combined


# ---------------------------------------------------------------------------
# Exception contract
# ---------------------------------------------------------------------------
class TestExceptionContract:
    def test_workspace_id_invalid_is_infrastructure_error(self) -> None:
        # Lets generic infrastructure-error handlers catch it without
        # widening to bare Exception.
        assert issubclass(WorkspaceIdInvalid, InfrastructureError)

    def test_workspace_id_invalid_http_status_422(self) -> None:
        try:
            WorkspaceIdValidator.validate("")
        except WorkspaceIdInvalid as e:
            assert e.http_status == 422
            assert e.code == "WORKSPACE_ID_INVALID"
        else:
            pytest.fail("expected WorkspaceIdInvalid to be raised")

    def test_workspace_id_invalid_envelope(self) -> None:
        try:
            WorkspaceIdValidator.validate("bad slug")
        except WorkspaceIdInvalid as e:
            env = e.to_envelope()
            assert env["code"] == "WORKSPACE_ID_INVALID"
            assert "invalid format" in env["message"].lower()
