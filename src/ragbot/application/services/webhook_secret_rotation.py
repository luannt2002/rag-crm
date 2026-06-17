"""Webhook HMAC secret rotation — versioned secrets + grace-period verify.

Security mindset (case-study upload-flow P1-4): a hard-coded webhook
HMAC secret is a permanent leak risk — once any consumer log captures
it the line is poisoned forever. This service implements a per-tenant,
per-webhook *versioned* secret with three properties:

1. **Hash-only storage** — the plain secret is generated server-side
   in :meth:`rotate`, returned ONCE to the caller, and only its bcrypt
   hash persists. The plain secret leaves the service exactly one time
   (the rotate response); the DB row is forever opaque.
2. **Grace-period verify chain** — :meth:`verify` accepts the current
   version PLUS any prior version whose ``revoked_at`` is still in the
   future. This lets a partner roll their consumer without dropping
   signed deliveries; once ``revoked_at`` passes, the old secret hard
   fails.
3. **Tenant-scoped lookup** — every query is filtered by both
   ``record_tenant_id`` AND ``webhook_id`` so two tenants can not
   verify each other's webhooks even if a webhook UUID is leaked.

The service does not own HTTP transport, audit logging, or HMAC
construction — those live at :mod:`infrastructure.security.hmac_signer`
and the admin route. We expose three coarse-grained methods (``rotate``
/ ``verify`` / ``list_versions``) and keep the SQL surface narrow.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ragbot.shared.constants import (
    DEFAULT_WEBHOOK_HMAC_ALGORITHM,
    DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS,
)

logger = structlog.get_logger(__name__)


# ── Module-level constants ──────────────────────────────────────────────────
# Length of the plain secret returned on rotate. 32 bytes hex → 64 chars,
# matches the 256-bit signing key recommendation for HMAC-SHA256.
_SECRET_BYTES = 32
# Length of the HMAC hex digest produced by the chosen algorithm; used to
# pre-validate caller-supplied signatures before reaching the constant-time
# compare (tight bound stops malformed input from polluting metrics).
_HMAC_HEX_LENGTH = 64  # SHA-256 → 32 bytes → 64 hex chars


@dataclass(frozen=True)
class _SecretRow:
    """In-memory view of one ``tenant_webhook_secrets`` row."""

    version: int
    secret_hash: str
    created_at: datetime
    revoked_at: datetime | None


# ── Port for secret hashing (Strategy + DI compliant) ───────────────────────


class SecretHashPort:
    """Strategy contract for hashing plain secrets to opaque digests.

    The default implementation uses :func:`hashlib.scrypt` — stdlib only,
    no extra install, parameters in line with OWASP 2025 password-storage
    guidance. Callers wire a different backend (bcrypt / argon2) by
    passing an alternate implementation to the service constructor.
    """

    def hash_secret(self, secret: str) -> str:  # pragma: no cover - Protocol
        raise NotImplementedError

    def verify_secret(self, secret: str, secret_hash: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class ScryptSecretHash(SecretHashPort):
    """Default :class:`SecretHashPort` — :func:`hashlib.scrypt` (stdlib).

    Output format: ``"scrypt$<salt_hex>$<hash_hex>"``. The two-token
    layout keeps the column self-describing (algorithm + parameters
    encoded in the prefix) so we can roll to argon2id later without
    breaking historical rows.
    """

    # OWASP-aligned scrypt params (N=2**14, r=8, p=1 → ~64 MiB, ~100 ms
    # on commodity hardware). Lifted to module scope so a future bump
    # is a one-line change.
    _N = 2**14
    _R = 8
    _P = 1
    _SALT_BYTES = 16
    _DKLEN = 32

    def hash_secret(self, secret: str) -> str:
        salt = secrets.token_bytes(self._SALT_BYTES)
        digest = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=salt,
            n=self._N,
            r=self._R,
            p=self._P,
            dklen=self._DKLEN,
        )
        return f"scrypt${salt.hex()}${digest.hex()}"

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        try:
            algo, salt_hex, digest_hex = secret_hash.split("$", 2)
        except ValueError:
            return False
        if algo != "scrypt":
            return False
        try:
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
        except ValueError:
            return False
        actual = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=salt,
            n=self._N,
            r=self._R,
            p=self._P,
            dklen=self._DKLEN,
        )
        return hmac.compare_digest(actual, expected)


# ── Service ────────────────────────────────────────────────────────────────


class WebhookSecretRotationService:
    """Manage versioned HMAC secrets per (tenant, webhook).

    Wiring: the admin route resolves a session via
    ``container.session_factory()`` and instantiates this service per
    request. The hash backend defaults to :class:`ScryptSecretHash`;
    tests inject a deterministic stub.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        hash_backend: SecretHashPort | None = None,
        grace_period_hours: int = DEFAULT_WEBHOOK_HMAC_GRACE_PERIOD_HOURS,
        algorithm: str = DEFAULT_WEBHOOK_HMAC_ALGORITHM,
    ) -> None:
        self._session = session
        self._hasher = hash_backend or ScryptSecretHash()
        self._grace_period_hours = grace_period_hours
        self._algorithm = algorithm

    # ── Public API ──────────────────────────────────────────────────────────

    async def rotate(
        self,
        *,
        record_tenant_id: UUID,
        webhook_id: UUID,
    ) -> dict[str, Any]:
        """Mint a new version, revoke the previous one with a grace period.

        Returns ``{"version": int, "secret": str, "created_at": datetime}``.
        The plain ``secret`` is the ONLY copy ever exposed; the caller MUST
        record it immediately. Subsequent calls return new secrets and never
        re-derive past ones.
        """
        # 1. Look up the current tail to compute next version + revoke it.
        tail = await self._fetch_current_version(record_tenant_id, webhook_id)
        next_version = (tail.version + 1) if tail else 1

        # 2. Generate the plain secret + hash. Plain never persists.
        plain_secret = secrets.token_hex(_SECRET_BYTES)
        secret_hash = self._hasher.hash_secret(plain_secret)

        # 3. Revoke prior tail with NOW() + grace if one exists.
        now = datetime.now(tz=timezone.utc)
        if tail is not None and tail.revoked_at is None:
            revoke_at = now + timedelta(hours=self._grace_period_hours)
            await self._session.execute(
                text(
                    "UPDATE tenant_webhook_secrets "
                    "SET revoked_at = :revoke_at "
                    "WHERE record_tenant_id = :tid "
                    "  AND webhook_id = :wid "
                    "  AND version = :ver "
                    "  AND revoked_at IS NULL",
                ),
                {
                    "revoke_at": revoke_at,
                    "tid": record_tenant_id,
                    "wid": webhook_id,
                    "ver": tail.version,
                },
            )

        # 4. Insert the new version.
        await self._session.execute(
            text(
                "INSERT INTO tenant_webhook_secrets "
                "(record_tenant_id, webhook_id, version, secret_hash, "
                " created_at, grace_period_hours) "
                "VALUES (:tid, :wid, :ver, :secret_hash, :created_at, :grace)",
            ),
            {
                "tid": record_tenant_id,
                "wid": webhook_id,
                "ver": next_version,
                "secret_hash": secret_hash,
                "created_at": now,
                "grace": self._grace_period_hours,
            },
        )

        logger.info(
            "webhook_secret_rotated",
            record_tenant_id=str(record_tenant_id),
            webhook_id=str(webhook_id),
            version_new=next_version,
            version_revoked=tail.version if tail else None,
            grace_period_hours=self._grace_period_hours,
        )

        return {
            "version": next_version,
            "secret": plain_secret,
            "created_at": now,
        }

    async def verify(
        self,
        *,
        record_tenant_id: UUID,
        webhook_id: UUID,
        signature: str,
        payload: bytes,
        timestamp: str,
        candidate_secret: str,
    ) -> bool:
        """Verify ``signature`` belongs to a currently-valid version.

        Caller flow (inbound webhook receiver):

        1. Receiver looks up the plain secret it last recorded for this
           ``webhook_id`` (it kept the secret from the last ``rotate``
           response — we never persist plain anywhere else).
        2. Receiver calls this method with that ``candidate_secret``,
           the wire ``signature`` (``X-Ragbot-Signature``), ``timestamp``
           (``X-Ragbot-Timestamp``), and the raw body bytes.
        3. We confirm ``candidate_secret`` matches one of our valid
           generation hashes (current OR previous-within-grace) AND
           ``HMAC(candidate, "timestamp.body")`` constant-time-equals
           ``signature``.

        Tenant isolation: queries filter by ``record_tenant_id``, so a
        caller from tenant B cannot pass tenant A's webhook id + secret
        and get a True. (Also: tenant B cannot reach tenant A's plain
        secret in the first place because ``rotate()`` returned it
        only to tenant A.)

        Returns ``False`` and never raises for: unknown webhook (no
        rows), malformed signature, expired secret past grace, secret
        belongs to revoked generation, HMAC mismatch.
        """
        if not self._signature_well_formed(signature):
            return False

        rows = await self._fetch_valid_secrets(
            record_tenant_id=record_tenant_id, webhook_id=webhook_id,
        )
        if not rows:
            return False

        # The candidate must belong to some still-valid generation. Iterate
        # newest first so the common case (active secret) exits the hash
        # compare on the first row; grace-period rows are tried last.
        belongs = any(
            self._hasher.verify_secret(candidate_secret, row.secret_hash)
            for row in rows
        )
        if not belongs:
            return False

        signing_input = self._build_signing_input(timestamp, payload)
        expected = hmac.new(
            candidate_secret.encode("utf-8"),
            signing_input,
            getattr(hashlib, self._algorithm),
        ).hexdigest()
        # The wire format may include the ``sha256=`` prefix; strip
        # before the constant-time compare so both forms verify.
        candidate_sig = signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, candidate_sig)

    async def list_versions(
        self,
        *,
        record_tenant_id: UUID,
        webhook_id: UUID,
    ) -> list[dict[str, Any]]:
        """Admin visibility: rows without the ``secret_hash`` column.

        The hash is omitted deliberately — even though scrypt is one-way,
        admin-trail leakage of hashes is a precursor to offline brute
        force. The list shows ``version`` / ``created_at`` / ``revoked_at``
        only.
        """
        result = await self._session.execute(
            text(
                "SELECT version, created_at, revoked_at, grace_period_hours "
                "FROM tenant_webhook_secrets "
                "WHERE record_tenant_id = :tid AND webhook_id = :wid "
                "ORDER BY version DESC",
            ),
            {"tid": record_tenant_id, "wid": webhook_id},
        )
        return [
            {
                "version": row.version,
                "created_at": row.created_at,
                "revoked_at": row.revoked_at,
                "grace_period_hours": row.grace_period_hours,
            }
            for row in result.fetchall()
        ]

    # ── Internals ───────────────────────────────────────────────────────────

    async def _fetch_current_version(
        self, record_tenant_id: UUID, webhook_id: UUID,
    ) -> _SecretRow | None:
        """Return the highest-version row (any state) or None."""
        result = await self._session.execute(
            text(
                "SELECT version, secret_hash, created_at, revoked_at "
                "FROM tenant_webhook_secrets "
                "WHERE record_tenant_id = :tid AND webhook_id = :wid "
                "ORDER BY version DESC LIMIT 1",
            ),
            {"tid": record_tenant_id, "wid": webhook_id},
        )
        row = result.first()
        if row is None:
            return None
        return _SecretRow(
            version=row.version,
            secret_hash=row.secret_hash,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
        )

    async def _fetch_valid_secrets(
        self, *, record_tenant_id: UUID, webhook_id: UUID,
    ) -> list[_SecretRow]:
        """Return rows whose ``revoked_at`` is NULL or still in the future.

        Strictly tenant-scoped. The ordering (version DESC) lets the
        verifier try the newest secret first, where most legitimate
        traffic lands.
        """
        result = await self._session.execute(
            text(
                "SELECT version, secret_hash, created_at, revoked_at "
                "FROM tenant_webhook_secrets "
                "WHERE record_tenant_id = :tid "
                "  AND webhook_id = :wid "
                "  AND (revoked_at IS NULL OR revoked_at > :now) "
                "ORDER BY version DESC",
            ),
            {
                "tid": record_tenant_id,
                "wid": webhook_id,
                "now": datetime.now(tz=timezone.utc),
            },
        )
        return [
            _SecretRow(
                version=row.version,
                secret_hash=row.secret_hash,
                created_at=row.created_at,
                revoked_at=row.revoked_at,
            )
            for row in result.fetchall()
        ]

    @staticmethod
    def _build_signing_input(timestamp: str, payload: bytes) -> bytes:
        """Reproduce the wire format used by :class:`CallbackDelivery`.

        Format: ``"{timestamp}." + payload`` (period separator). Keeping
        this method static + small lets both the outbound signer and
        the inbound verifier agree on the canonical bytes.
        """
        return f"{timestamp}.".encode("utf-8") + payload

    @staticmethod
    def _signature_well_formed(signature: str) -> bool:
        """Reject obviously malformed signatures before the DB roundtrip."""
        if not signature:
            return False
        # The current wire format prefixes the hex digest with ``sha256=``;
        # accept both the raw hex and the prefixed form so callers do not
        # have to strip on the way in.
        candidate = signature.removeprefix("sha256=")
        if len(candidate) != _HMAC_HEX_LENGTH:
            return False
        try:
            bytes.fromhex(candidate)
        except ValueError:
            return False
        return True


# ── Errors ──────────────────────────────────────────────────────────────────


class WebhookRotationError(SQLAlchemyError):
    """Raised when the rotate transaction fails (DB / constraint)."""


__all__ = [
    "ScryptSecretHash",
    "SecretHashPort",
    "WebhookRotationError",
    "WebhookSecretRotationService",
]
