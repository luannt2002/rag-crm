"""Unit tests for ``SchemaVersionMiddleware``.

Cover the negotiation surface end-to-end via Starlette ``TestClient`` so the
assertions land on real HTTP semantics (status code, JSON body, response
trace_id echo) rather than the middleware's internal control flow. Each
test stands up a minimal app with the middleware in front of a single echo
route so the production middleware chain is not a dependency here.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ragbot.interfaces.http.middlewares.schema_version import (
    SchemaVersionMiddleware,
)
from ragbot.shared.constants import (
    DEFAULT_SCHEMA_VERSION,
    SCHEMA_VERSION_HEADER,
    SUPPORTED_SCHEMA_VERSIONS,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SchemaVersionMiddleware)

    @app.get("/echo")
    async def _echo(request: Request) -> dict:
        return {
            "schema_version": getattr(
                request.state, "schema_version", "MISSING",
            ),
        }

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_build_app())


# ---------------------------------------------------------------------------
# Default / missing header
# ---------------------------------------------------------------------------


def test_missing_header_sets_default_schema_version(client: TestClient) -> None:
    """No header → request.state.schema_version == DEFAULT_SCHEMA_VERSION."""
    response = client.get("/echo")
    assert response.status_code == 200
    assert response.json() == {"schema_version": DEFAULT_SCHEMA_VERSION}


def test_empty_header_value_treated_as_missing(client: TestClient) -> None:
    """Empty string header is normalised to the default, not parsed as 0."""
    response = client.get("/echo", headers={SCHEMA_VERSION_HEADER: ""})
    assert response.status_code == 200
    assert response.json() == {"schema_version": DEFAULT_SCHEMA_VERSION}


# ---------------------------------------------------------------------------
# Valid header — every supported version is accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("supported_version", SUPPORTED_SCHEMA_VERSIONS)
def test_supported_header_lifts_onto_state(
    client: TestClient, supported_version: int,
) -> None:
    """Every value in SUPPORTED_SCHEMA_VERSIONS is lifted onto state as int."""
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: str(supported_version)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"schema_version": supported_version}
    # The lifted value MUST be an int (not the raw header string) — handlers
    # branch on integer comparison, not string compare.
    assert isinstance(body["schema_version"], int)


# ---------------------------------------------------------------------------
# Invalid header — short-circuit 400
# ---------------------------------------------------------------------------


def test_unsupported_integer_returns_400(client: TestClient) -> None:
    """Integer that is not in SUPPORTED_SCHEMA_VERSIONS → 400."""
    # Pick a sentinel that will never appear in any reasonable supported set
    # by leveraging max()+1; works whether SUPPORTED is (1,) or grows later.
    unsupported = max(SUPPORTED_SCHEMA_VERSIONS) + 999
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: str(unsupported)},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "SCHEMA_VERSION_UNSUPPORTED"
    assert str(unsupported) in body["error"]["message"]


def test_non_numeric_header_returns_400(client: TestClient) -> None:
    """Header that cannot be parsed as int → 400."""
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: "not-an-int"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "SCHEMA_VERSION_UNSUPPORTED"


def test_negative_number_rejected(client: TestClient) -> None:
    """Negative integers are not in the supported set → 400."""
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: "-1"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SCHEMA_VERSION_UNSUPPORTED"


def test_zero_rejected_when_not_in_supported_set(client: TestClient) -> None:
    """0 is not in SUPPORTED_SCHEMA_VERSIONS=(1,) → 400."""
    # Guard: if a future revision adds 0 to the supported set this test
    # would silently pass — assert the precondition explicitly.
    assert 0 not in SUPPORTED_SCHEMA_VERSIONS
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: "0"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Error response shape
# ---------------------------------------------------------------------------


def test_error_response_includes_trace_id_field(client: TestClient) -> None:
    """The 400 envelope carries a ``trace_id`` field for partner correlation.

    The standalone test app has no TraceContextMiddleware, so the value is
    the empty string — but the *field* must be present so partners can rely
    on the envelope shape regardless of upstream middleware ordering.
    """
    response = client.get(
        "/echo",
        headers={SCHEMA_VERSION_HEADER: "not-an-int"},
    )
    assert response.status_code == 400
    body = response.json()
    assert "trace_id" in body
    assert body["data"] is None


# ---------------------------------------------------------------------------
# Pass-through — middleware is transparent for routes that ignore the value
# ---------------------------------------------------------------------------


def test_route_that_ignores_schema_version_still_succeeds() -> None:
    """Middleware MUST NOT break handlers that never read schema_version."""
    app = FastAPI()
    app.add_middleware(SchemaVersionMiddleware)

    @app.get("/static")
    async def _static() -> dict:
        return {"ok": True}

    client = TestClient(app)
    # Both default and explicit header path must reach the route untouched.
    assert client.get("/static").json() == {"ok": True}
    assert client.get(
        "/static",
        headers={SCHEMA_VERSION_HEADER: str(DEFAULT_SCHEMA_VERSION)},
    ).json() == {"ok": True}


# ---------------------------------------------------------------------------
# Constants invariants — fail loud if the constants drift out of sync
# ---------------------------------------------------------------------------


def test_default_schema_version_is_in_supported_set() -> None:
    """DEFAULT must always be in SUPPORTED — otherwise the middleware would
    short-circuit every request that omits the header."""
    assert DEFAULT_SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_supported_schema_versions_is_non_empty() -> None:
    """An empty supported set would lock out every caller."""
    assert len(SUPPORTED_SCHEMA_VERSIONS) >= 1
    assert all(isinstance(v, int) for v in SUPPORTED_SCHEMA_VERSIONS)


def test_schema_version_header_uses_canonical_name() -> None:
    """The header name is the agreed REST convention; renaming breaks every
    deployed B2B caller."""
    assert SCHEMA_VERSION_HEADER == "X-Schema-Version"
