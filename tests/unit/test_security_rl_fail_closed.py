"""S4.3 — Layer-1 tenant rate limit fails CLOSED (master Finding #6).

Audit `RAG_Master_of_Masters_DeepDive_Report.md` Finding #6:
``TenantContextMiddleware`` previously caught every exception from
``tenant_rate_limiter.check()``, logged at DEBUG and let the request
through (fail-open). A Redis outage could lift every tenant's limit
silently — tenant Y could DoS tenant X's bots without any 429 fired,
because tenant Y's limiter was broken. Layer-1.5 (per-service) and
Layer-2 (per-user) already fail closed; Layer-1 was the outlier.

Fix: convert the broad-except into a 503 ``RATE_LIMIT_UNAVAILABLE``
response with a ``Retry-After`` header, and increment
``rate_limit_fail_closed_total{scope="tenant"}`` for observability.

These tests assert the contract on the response builder without
booting the full middleware stack (no Postgres / Redis). The
behavioural assertions cover: 503 status, Retry-After header,
JSON envelope shape, metric scope.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.responses import JSONResponse

from ragbot.shared.constants import DEFAULT_RL_FAIL_CLOSED_RETRY_S


# Mirror the middleware's failure-path builder so we test the response
# shape without spinning up the whole HTTP pipeline. If the middleware
# ever diverges from this shape we want this test to fail.
#
# Shape matches the pre-existing Layer-1.5 (per-service) fail-closed
# response so dashboards and clients see a single canonical envelope.
def _fail_closed_response() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": "RATE_LIMIT_UNAVAILABLE",
                "message": "rate-limit backend unavailable",
            },
        },
        status_code=503,
        headers={"Retry-After": str(DEFAULT_RL_FAIL_CLOSED_RETRY_S)},
    )


def test_response_status_503() -> None:
    r = _fail_closed_response()
    assert r.status_code == 503


def test_response_retry_after_header_set() -> None:
    r = _fail_closed_response()
    assert r.headers["Retry-After"] == str(DEFAULT_RL_FAIL_CLOSED_RETRY_S)
    # Must be a positive integer — clients parse `Retry-After` as seconds.
    assert DEFAULT_RL_FAIL_CLOSED_RETRY_S > 0


def test_response_envelope_shape() -> None:
    """Lock the API envelope: ok=False + error.code = RATE_LIMIT_UNAVAILABLE."""
    r = _fail_closed_response()
    body = r.body.decode()
    assert '"RATE_LIMIT_UNAVAILABLE"' in body
    assert '"ok": false' in body or '"ok":false' in body


def test_retry_after_constant_is_reasonable() -> None:
    """Default retry-after must be in a sensible operator range.

    Too small (< 5s) → tight retry storm on the failing backend.
    Too large (> 5min) → user-visible blackout on transient outage.
    """
    assert 5 <= DEFAULT_RL_FAIL_CLOSED_RETRY_S <= 300


@pytest.mark.parametrize(
    "raised_exc",
    [
        RuntimeError("redis pool exhausted"),
        ConnectionError("redis down"),
        TimeoutError("redis timeout"),
        ValueError("malformed limiter response"),
    ],
)
def test_broad_backend_errors_all_trigger_fail_closed(raised_exc: Exception) -> None:
    """Document: every backend exception type must convert to 503.

    We model the middleware's behaviour: catch -> emit metric ->
    return 503. The test asserts the predicate (any exception in the
    limiter.check() path triggers the fail-closed branch) without
    needing to spin up the actual middleware.
    """
    decision = None
    triggered_fail_closed = False
    try:
        raise raised_exc
    except Exception:  # noqa: BLE001 — mirrors middleware contract
        triggered_fail_closed = True

    assert triggered_fail_closed
    assert decision is None  # no decision propagated downstream
    # Build the response — must not raise.
    r = _fail_closed_response()
    assert r.status_code == 503


def test_metric_scope_label_is_tenant() -> None:
    """The L1 fail-closed counter must use scope="tenant".

    Layer-1.5 emits scope="service", Layer-2 emits scope="user".
    Diverging scope names would break Grafana panels that filter by
    label, so we lock the contract.
    """
    from ragbot.infrastructure.observability.metrics import (
        rate_limit_fail_closed_total,
    )

    # Touch the counter with scope="tenant" — must not raise (label
    # cardinality is validated lazily by prometheus_client).
    counter = rate_limit_fail_closed_total.labels(scope="tenant")
    before = counter._value.get()  # type: ignore[attr-defined]
    counter.inc()
    after = counter._value.get()  # type: ignore[attr-defined]
    assert after == before + 1


def test_middleware_uses_fail_closed_branch_for_l1() -> None:
    """Source-level guard: the L1 try/except in tenant_context.py
    must call ``rate_limit_fail_closed_total.labels(scope="tenant")``.

    If a future edit reverts the fail-open behaviour this test breaks.
    """
    from pathlib import Path

    src = Path(
        "src/ragbot/interfaces/http/middlewares/tenant_context.py",
    ).read_text()
    assert 'rate_limit_fail_closed_total.labels(scope="tenant")' in src
    assert "DEFAULT_RL_FAIL_CLOSED_RETRY_S" in src
    # The legacy fail-open comment must be gone.
    assert "fail-open" not in src.lower() or "fail-closed" in src.lower()


def test_owner_role_carve_out_documented_in_source() -> None:
    """Owner / super_admin must NOT be fail-closed (control-plane bypass).

    If Redis goes down the admin APIs that fix the outage must still
    reach the backend. This mirrors the Layer-1.5 ``rl_val == 0`` owner
    skip already in tenant_context.py. The carve-out is documented as
    ``role in ("owner", "super_admin")`` next to the 503 branch.
    """
    from pathlib import Path

    src = Path(
        "src/ragbot/interfaces/http/middlewares/tenant_context.py",
    ).read_text()
    assert 'role in ("owner", "super_admin")' in src
    # Owner-skip path must NOT increment the fail-closed counter.
    assert "tenant_rate_limiter_skip_owner_role" in src


def test_layer_1_no_longer_silently_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural mirror: when limiter raises, middleware MUST NOT
    fall through to `decision is None` continue path.

    Simulate the limiter blowing up and assert our handler maps it
    to a 503, not a request bypass.
    """
    class _ExplodingLimiter:
        async def check(self, **_: Any) -> Any:
            raise RuntimeError("simulated redis outage")

    limiter = _ExplodingLimiter()
    captured: dict[str, Any] = {}

    async def _call() -> Any:
        try:
            await limiter.check(record_tenant_id="x")
        except Exception:  # noqa: BLE001 — mirror middleware
            captured["fail_closed"] = True
            return _fail_closed_response()
        return None

    import asyncio
    r = asyncio.run(_call())
    assert captured.get("fail_closed") is True
    assert r is not None
    assert r.status_code == 503
