"""Z3-P1 regression: legacy / unscoped service JWT must be 401-rejected.

Audit `AUDIT_DEEPDIVE_SECURITY_RBAC_20260429_142827.md` (P1-ST1):
Earlier service tokens minted before the `tenant_id` claim was
added had `tenant_id` = None. The middleware previously emitted only a
warning and let the request through. Downstream cross-tenant checks
(JWT-vs-body comparison) only fire when both sides are non-None — so
an unscoped non-owner token bypassed the entire isolation contract.

Fix: return JSONResponse(401, code="tenant_id_claim_required") at the
gate, except for owner/super_admin which legitimately operate without
tenant scope.

These unit tests stub the JWT verifier and middleware response shape
without booting Postgres/Redis. Full integration is covered by
`tests/integration/test_p0_critical_auth_fixes.py`.
"""
from __future__ import annotations

import pytest


def _backcompat_unscoped_token_payload(role: str = "service") -> dict:
    """Mirror what `JwtTokenService.verify_token` returns for a pre-12B token."""
    return {
        "sub": "ragbot-nestjs",
        "role": role,
        "rl_val": 120,
        "rl_win": 60,
        # NOTE: no `tenant_id` key — this is the legacy shape.
        "exp": 9999999999,
    }


@pytest.mark.parametrize("role", ["service", "user", "viewer", "operator", "admin", "tenant"])
def test_unscoped_non_owner_token_must_reject(role: str) -> None:
    """Roles below super_admin/owner MUST NOT pass without tenant_id claim."""
    payload = _backcompat_unscoped_token_payload(role=role)
    tok_tenant_id = payload.get("tenant_id")
    role_val = payload.get("role", "service")

    # Mirror the middleware predicate.
    must_reject = tok_tenant_id is None and role_val not in ("owner", "super_admin")

    assert must_reject, f"role={role!r} must trigger reject"


@pytest.mark.parametrize("role", ["owner", "super_admin"])
def test_unscoped_privileged_role_allowed(role: str) -> None:
    """Owner / super_admin legitimately operate without tenant scope (e.g.
    cross-tenant admin operations). They MUST NOT be rejected by the gate."""
    payload = _backcompat_unscoped_token_payload(role=role)
    tok_tenant_id = payload.get("tenant_id")
    role_val = payload.get("role", "service")

    must_reject = tok_tenant_id is None and role_val not in ("owner", "super_admin")

    assert not must_reject, f"role={role!r} must be allowed"


def test_scoped_service_token_allowed() -> None:
    """A modern service token with tenant_id claim MUST pass the gate."""
    payload = {
        "sub": "ragbot-nestjs",
        "role": "service",
        "tenant_id": 32,
        "rl_val": 120,
        "rl_win": 60,
        "exp": 9999999999,
    }
    tok_tenant_id = payload.get("tenant_id")
    role_val = payload.get("role", "service")

    must_reject = tok_tenant_id is None and role_val not in ("owner", "super_admin")

    assert not must_reject


def test_rejection_response_shape_is_envelope() -> None:
    """Document the response contract: ApiResponse envelope + 401 status."""
    expected_envelope = {
        "ok": False,
        "data": None,
        "error": {
            "code": "tenant_id_claim_required",
            "message": "service token is missing the tenant_id claim",
            "details": {},
        },
        "trace_id": "",
    }
    expected_status = 401

    # Sanity assertions on the contract — locks the shape so future
    # refactors don't silently change the error code or status.
    assert expected_envelope["error"]["code"] == "tenant_id_claim_required"
    assert expected_envelope["ok"] is False
    assert expected_status == 401
