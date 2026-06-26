"""Boot health-check: refuse a superuser / BYPASSRLS runtime role.

RLS layer 2 (a NOBYPASSRLS login role) is the only thing that makes the
RLS policies actually apply. If the runtime DSN is still pointed at a
superuser / ``rolbypassrls`` role, every policy is silently bypassed —
cross-tenant reads/writes succeed and nothing in the logs says so. This
check fails loud at boot unless the operator has explicitly opted into the
superuser-runtime escape (``RAGBOT_ALLOW_SUPERUSER_RUNTIME=1``).
"""

from __future__ import annotations

import pytest

from ragbot.interfaces.http.app import _evaluate_runtime_db_role
from ragbot.shared.constants import RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE


def test_nobypassrls_role_passes() -> None:
    # A least-privilege runtime role: no raise, no escape needed.
    _evaluate_runtime_db_role(
        rolname="ragbot_app",
        is_superuser=False,
        is_bypassrls=False,
        allow_superuser=False,
    )


def test_superuser_role_raises_without_escape() -> None:
    with pytest.raises(RuntimeError) as exc:
        _evaluate_runtime_db_role(
            rolname="postgres",
            is_superuser=True,
            is_bypassrls=False,
            allow_superuser=False,
        )
    assert "RAGBOT_ALLOW_SUPERUSER_RUNTIME" in str(exc.value)
    assert "postgres" in str(exc.value)


def test_bypassrls_role_raises_without_escape() -> None:
    with pytest.raises(RuntimeError):
        _evaluate_runtime_db_role(
            rolname="some_bypass_role",
            is_superuser=False,
            is_bypassrls=True,
            allow_superuser=False,
        )


def test_superuser_role_allowed_with_escape() -> None:
    # Escape active → no raise (operator opted in knowingly).
    _evaluate_runtime_db_role(
        rolname="postgres",
        is_superuser=True,
        is_bypassrls=True,
        allow_superuser=True,
    )


def test_escape_value_is_the_constant() -> None:
    # Guard: the opt-in value the check honours stays the SSoT constant.
    assert RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE == "1"
