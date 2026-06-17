"""Unit tests — :mod:`webhook_secret_rotation` (WA-6).

Covers:

* rotate() generates a new version, hashes the secret, revokes the
  prior tail with NOW() + grace.
* verify() accepts the current secret.
* verify() accepts the PREVIOUS secret while still within
  the grace window.
* verify() rejects the previous secret AFTER grace expires.
* verify() rejects unknown / malformed signatures.
* Cross-tenant: tenant B cannot validate against tenant A's webhook id.
* RBAC enforcement on the admin endpoint at the constant level.
* list_versions() omits ``secret_hash``.
* Plain secret is exposed exactly once (rotate response) — no DB column
  carries it.

The DB layer is mocked: we substitute a thin in-memory store for the
:class:`sqlalchemy.text` exec calls so the service logic (versioning,
grace math, tenant filter) is exercised without spinning up Postgres.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.webhook_secret_rotation import (
    DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS,
    ScryptSecretHash,
    WebhookSecretRotationService,
)


# ── In-memory SQL fake ──────────────────────────────────────────────────────


@dataclass
class _StoredRow:
    record_tenant_id: UUID
    webhook_id: UUID
    version: int
    secret_hash: str
    created_at: datetime
    revoked_at: datetime | None
    grace_period_hours: int


@dataclass
class _ResultStub:
    rows: list[Any] = field(default_factory=list)

    def first(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _FakeSession:
    """Mimic the slice of ``AsyncSession`` the service touches.

    Stores rows keyed by ``(tenant, webhook, version)``. Supports the
    handful of ``text("...")`` SQL statements the service issues by
    matching on the leading verb.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID, int], _StoredRow] = {}

    async def execute(self, statement, params: dict[str, Any] | None = None):
        sql = str(statement)
        params = params or {}
        head = sql.strip().split()[0].upper()

        if head == "SELECT":
            return self._select(sql, params)
        if head == "INSERT":
            self._insert(params)
            return _ResultStub()
        if head == "UPDATE":
            self._update(params)
            return _ResultStub()
        raise AssertionError(f"unexpected SQL: {sql[:60]}")

    async def commit(self) -> None:  # No-op in unit tests.
        pass

    # Convenience for tests --------------------------------------------------
    def force_revoked_at(
        self, *, tenant: UUID, webhook: UUID, version: int, when: datetime,
    ) -> None:
        self._rows[(tenant, webhook, version)].revoked_at = when

    def all_rows(self) -> list[_StoredRow]:
        return list(self._rows.values())

    # SELECT dispatch --------------------------------------------------------
    def _select(self, sql: str, params: dict[str, Any]) -> _ResultStub:
        # tenant + webhook always present in the service's queries.
        tid = params["tid"]
        wid = params["wid"]
        rows = [
            r for r in self._rows.values()
            if r.record_tenant_id == tid and r.webhook_id == wid
        ]

        if "ORDER BY version DESC LIMIT 1" in sql:
            rows.sort(key=lambda r: r.version, reverse=True)
            return _ResultStub(rows[:1])

        if "revoked_at IS NULL OR revoked_at >" in sql:
            now = params["now"]
            rows = [
                r for r in rows
                if r.revoked_at is None or r.revoked_at > now
            ]
            rows.sort(key=lambda r: r.version, reverse=True)
            return _ResultStub(rows)

        if "ORDER BY version DESC" in sql:
            rows.sort(key=lambda r: r.version, reverse=True)
            return _ResultStub(rows)

        return _ResultStub(rows)

    def _insert(self, params: dict[str, Any]) -> None:
        key = (params["tid"], params["wid"], params["ver"])
        self._rows[key] = _StoredRow(
            record_tenant_id=params["tid"],
            webhook_id=params["wid"],
            version=params["ver"],
            secret_hash=params["secret_hash"],
            created_at=params["created_at"],
            revoked_at=None,
            grace_period_hours=params["grace"],
        )

    def _update(self, params: dict[str, Any]) -> None:
        key = (params["tid"], params["wid"], params["ver"])
        row = self._rows.get(key)
        if row is not None and row.revoked_at is None:
            row.revoked_at = params["revoke_at"]


# ── Tests ──────────────────────────────────────────────────────────────────


def _service(session: _FakeSession) -> WebhookSecretRotationService:
    return WebhookSecretRotationService(session)


def test_rotate_creates_version_one_for_new_webhook() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant = uuid4()
    webhook = uuid4()

    result = asyncio.run(
        svc.rotate(record_tenant_id=tenant, webhook_id=webhook),
    )

    assert result["version"] == 1
    assert isinstance(result["secret"], str) and len(result["secret"]) == 64
    assert isinstance(result["created_at"], datetime)
    # Hash persisted, plain not.
    stored = session.all_rows()
    assert len(stored) == 1
    assert stored[0].version == 1
    assert stored[0].secret_hash.startswith("scrypt$")
    assert result["secret"] not in stored[0].secret_hash


def test_rotate_increments_and_revokes_prior_with_grace() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant = uuid4()
    webhook = uuid4()

    first = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    second = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    assert second["version"] == first["version"] + 1
    # Two rows: v1 revoked at NOW + grace, v2 active.
    rows = sorted(session.all_rows(), key=lambda r: r.version)
    assert len(rows) == 2
    assert rows[0].revoked_at is not None
    delta = rows[0].revoked_at - rows[0].created_at
    # Grace defaults to DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS h; allow
    # 5 s skew between INSERT NOW() and UPDATE NOW() in the fake.
    assert (
        timedelta(hours=DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS) - timedelta(seconds=5)
        <= delta
        <= timedelta(hours=DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS) + timedelta(seconds=5)
    )
    assert rows[1].revoked_at is None


def test_plain_secret_never_appears_in_stored_hash() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    res = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    plain = res["secret"]
    for row in session.all_rows():
        # The hex of the digest must not contain the plain secret as a
        # substring — scrypt collisions on a 64-hex random input are
        # negligible, this confirms no accidental plain leak.
        assert plain not in row.secret_hash


def test_verify_accepts_current_secret() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    res = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    plain = res["secret"]

    payload = b'{"event":"document.processed"}'
    timestamp = "1716100000"
    signing_input = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(
        plain.encode("utf-8"), signing_input, hashlib.sha256,
    ).hexdigest()

    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant,
            webhook_id=webhook,
            signature=sig,
            payload=payload,
            timestamp=timestamp,
            candidate_secret=plain,
        ),
    )
    assert ok is True


def test_verify_accepts_previous_secret_within_grace_window() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    res1 = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    old_plain = res1["secret"]
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    # The old row was revoked NOW + 24 h; it is still valid.
    payload = b'{"event":"document.processed"}'
    timestamp = "1716100000"
    signing_input = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(
        old_plain.encode("utf-8"), signing_input, hashlib.sha256,
    ).hexdigest()

    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant,
            webhook_id=webhook,
            signature=sig,
            payload=payload,
            timestamp=timestamp,
            candidate_secret=old_plain,
        ),
    )
    assert ok is True, "previous secret must verify while in grace window"


def test_verify_rejects_previous_secret_past_grace() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    res1 = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    old_plain = res1["secret"]
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    # Force the old row's revoked_at one second into the PAST.
    past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    session.force_revoked_at(
        tenant=tenant, webhook=webhook, version=1, when=past,
    )

    payload = b'x'
    timestamp = "1716100000"
    signing_input = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(
        old_plain.encode("utf-8"), signing_input, hashlib.sha256,
    ).hexdigest()

    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant,
            webhook_id=webhook,
            signature=sig,
            payload=payload,
            timestamp=timestamp,
            candidate_secret=old_plain,
        ),
    )
    assert ok is False


def test_verify_rejects_unknown_secret() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    payload = b'x'
    timestamp = "1716100000"
    fake_plain = "0" * 64
    signing_input = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(
        fake_plain.encode("utf-8"), signing_input, hashlib.sha256,
    ).hexdigest()

    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant,
            webhook_id=webhook,
            signature=sig,
            payload=payload,
            timestamp=timestamp,
            candidate_secret=fake_plain,
        ),
    )
    assert ok is False


def test_verify_rejects_malformed_signature() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    res = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant,
            webhook_id=webhook,
            signature="not-hex-at-all",
            payload=b"x",
            timestamp="1716100000",
            candidate_secret=res["secret"],
        ),
    )
    assert ok is False


def test_verify_cross_tenant_isolation() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant_a, tenant_b = uuid4(), uuid4()
    webhook = uuid4()
    res = asyncio.run(svc.rotate(record_tenant_id=tenant_a, webhook_id=webhook))
    plain = res["secret"]

    payload = b'x'
    timestamp = "1716100000"
    signing_input = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(
        plain.encode("utf-8"), signing_input, hashlib.sha256,
    ).hexdigest()

    # Tenant B presents A's webhook+secret+signature; service refuses.
    ok = asyncio.run(
        svc.verify(
            record_tenant_id=tenant_b,
            webhook_id=webhook,
            signature=sig,
            payload=payload,
            timestamp=timestamp,
            candidate_secret=plain,
        ),
    )
    assert ok is False, "tenant B must not validate tenant A's webhook"


def test_verify_returns_false_when_no_rows() -> None:
    session = _FakeSession()
    svc = _service(session)
    ok = asyncio.run(
        svc.verify(
            record_tenant_id=uuid4(),
            webhook_id=uuid4(),
            signature="a" * 64,
            payload=b"x",
            timestamp="1716100000",
            candidate_secret="anything",
        ),
    )
    assert ok is False


def test_list_versions_omits_secret_hash() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    versions = asyncio.run(
        svc.list_versions(record_tenant_id=tenant, webhook_id=webhook),
    )
    assert len(versions) == 2
    for row in versions:
        assert "secret_hash" not in row
        assert set(row.keys()) == {
            "version", "created_at", "revoked_at", "grace_period_hours",
        }


def test_list_versions_ordered_descending() -> None:
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))

    versions = asyncio.run(
        svc.list_versions(record_tenant_id=tenant, webhook_id=webhook),
    )
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_scrypt_secret_hash_roundtrip() -> None:
    h = ScryptSecretHash()
    digest = h.hash_secret("super-secret")
    assert digest.startswith("scrypt$")
    assert h.verify_secret("super-secret", digest) is True
    assert h.verify_secret("wrong", digest) is False


def test_scrypt_secret_hash_rejects_malformed_digest() -> None:
    h = ScryptSecretHash()
    assert h.verify_secret("x", "not-a-valid-digest") is False
    assert h.verify_secret("x", "scrypt$nothex$nothex") is False
    assert h.verify_secret("x", "argon2$abc$def") is False


def test_rotate_returns_plain_secret_only_once() -> None:
    """Subsequent calls return DIFFERENT plain secrets.

    The point of the once-only return contract: rotate() must not
    expose a way to re-derive a past secret. Two rotate calls on the
    same webhook produce two different plain secrets.
    """
    session = _FakeSession()
    svc = _service(session)
    tenant, webhook = uuid4(), uuid4()

    r1 = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    r2 = asyncio.run(svc.rotate(record_tenant_id=tenant, webhook_id=webhook))
    assert r1["secret"] != r2["secret"]
    # Neither secret matches the OTHER row's hash.
    rows = {r.version: r for r in session.all_rows()}
    h = ScryptSecretHash()
    assert h.verify_secret(r1["secret"], rows[1].secret_hash) is True
    assert h.verify_secret(r1["secret"], rows[2].secret_hash) is False
    assert h.verify_secret(r2["secret"], rows[2].secret_hash) is True


def test_admin_endpoint_requires_level_80() -> None:
    """The admin route must use ``require_min_level(... DEFAULT_TENANT_ADMIN_LEVEL)``.

    This is a static check at the constant level — the route module
    pulls ``DEFAULT_TENANT_ADMIN_LEVEL`` from constants, ``=80``. A
    refactor that lowers the gate (e.g. accidentally to 60) trips
    this test.
    """
    import ragbot.interfaces.http.routes.admin_webhooks as mod
    from ragbot.shared.constants import (
        DEFAULT_ADMIN_LEVEL,
        DEFAULT_TENANT_ADMIN_LEVEL,
    )

    assert DEFAULT_TENANT_ADMIN_LEVEL == 80
    src = inspect.getsource(mod._require_admin)
    assert "DEFAULT_TENANT_ADMIN_LEVEL" in src
    # And the operator level (60) MUST NOT be the gate.
    assert "DEFAULT_ADMIN_LEVEL" not in src
    _ = DEFAULT_ADMIN_LEVEL  # imported for clarity; not used in assertion


def test_rbac_helpers_block_level_below_80() -> None:
    """End-to-end RBAC sanity — level-60 admin token is rejected by
    ``require_min_level(80)``; level-80 token is allowed.
    """
    from starlette.requests import Request

    from ragbot.shared.errors import ForbiddenError
    from ragbot.shared.rbac import require_min_level

    def _req(role: str) -> Request:
        scope = {
            "type": "http", "method": "POST", "path": "/x", "headers": [],
        }
        req = Request(scope=scope)
        req.state.role = role
        return req

    with pytest.raises(ForbiddenError):
        require_min_level(_req("admin"), 80)  # level 60 < 80

    # Level 80 passes — no raise.
    require_min_level(_req("tenant_admin"), 80)
    require_min_level(_req("super_admin"), 80)


def test_no_plain_secret_in_insert_statement() -> None:
    """Static guarantee: the INSERT statement carries ``secret_hash``,
    not a plain ``secret`` column.

    Regression guard: a future refactor that adds a plain column would
    immediately fail this check.
    """
    import ragbot.application.services.webhook_secret_rotation as mod
    src = inspect.getsource(mod.WebhookSecretRotationService.rotate)
    # The insert must reference ``secret_hash`` (the hashed column) and
    # NEVER ``, secret,`` (plain column).
    assert "secret_hash" in src
    assert ", secret," not in src
    assert "(secret," not in src
