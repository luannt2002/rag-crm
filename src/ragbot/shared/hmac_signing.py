"""HMAC-SHA256 request signature verification for the /sync upstream bridge.

Use case
--------
Pre-auth (or in addition to bearer auth) integrity check for callbacks
arriving from the legacy NestJS upstream. The upstream issues
``X-Signature: <hex>`` where ``hex = HMAC-SHA256(body, secret)`` and the
secret lives in ``tenants.config['sync_hmac_secret']`` looked up via the
body's ``upstream_tenant_id`` int.

Design notes
------------
* Constant-time compare via :pyfunc:`hmac.compare_digest` — must not leak
  per-byte timing.
* Empty / missing secret → ``ValueError`` so the caller can decide whether
  to fail-open (rolling rollout) or fail-closed (enforced tenants).
* Hex-encoded signature only; no base64 / urlsafe variants — the upstream
  emits hex and accepting both surfaces a downgrade vector.
* Domain-neutral: no tenant brand / vendor-specific format. Algorithm
  choice (sha256) is namespaced via ``DEFAULT_SYNC_HMAC_ALGORITHM`` so a
  future provider swap is one constant flip.
* Zero-hardcode: header name + algorithm both live in ``shared/constants``.

Logging
-------
Caller logs failures with ``signature_present=bool``, ``secret_configured
=bool`` and a *hashed* tenant identifier — NEVER the raw secret nor the
full signature value. The helper itself never logs.
"""

from __future__ import annotations

import hashlib
import hmac

from ragbot.shared.constants import DEFAULT_SYNC_HMAC_ALGORITHM

# Map algorithm name → hashlib constructor. Keep the list narrow so an
# upstream typo (e.g. "md5") cannot silently downgrade the integrity check.
_ALLOWED_ALGORITHMS = {
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


def compute_signature(
    body: bytes,
    secret: str,
    *,
    algorithm: str = DEFAULT_SYNC_HMAC_ALGORITHM,
) -> str:
    """Return hex-encoded HMAC-<algorithm> signature of ``body``.

    Args:
        body: Raw request body (bytes). Caller must pass the exact bytes
            the signer hashed — JSON re-serialisation will mismatch.
        secret: Pre-shared secret (str). Empty / blank → ValueError.
        algorithm: Hash family. Whitelisted to ``sha256`` / ``sha384`` /
            ``sha512`` to block silent MD5 downgrade.

    Raises:
        ValueError: empty secret or unsupported algorithm.
    """
    if not secret or not secret.strip():
        raise ValueError("hmac secret must be a non-empty string")
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise ValueError(f"unsupported hmac algorithm: {algorithm}")
    digestmod = _ALLOWED_ALGORITHMS[algorithm]
    mac = hmac.new(secret.encode("utf-8"), body, digestmod)
    return mac.hexdigest()


def verify_request_signature(
    body: bytes,
    signature: str,
    secret: str,
    *,
    algorithm: str = DEFAULT_SYNC_HMAC_ALGORITHM,
) -> bool:
    """Constant-time verify ``signature`` against ``HMAC(body, secret)``.

    Returns ``False`` on any inequality, malformed hex, or empty inputs;
    raises ``ValueError`` only if the *secret* is missing — caller must
    explicitly decide between fail-open (no secret configured for the
    tenant) and fail-closed (enforced tenant).

    Args:
        body: Raw request body bytes signed by the upstream.
        signature: Hex-encoded signature received in ``X-Signature``.
        secret: Per-tenant secret stored in ``tenants.config``.
        algorithm: Hash family — defaults to SHA-256.

    Returns:
        ``True`` iff signature matches the recomputed HMAC in constant
        time. ``False`` for any mismatch, malformed input, or empty
        signature.
    """
    if not signature:
        return False
    expected = compute_signature(body, secret, algorithm=algorithm)
    # Both inputs ASCII-hex of identical length → compare_digest is the
    # correct constant-time primitive. Length-mismatch returns False
    # without leaking which side is shorter.
    if len(expected) != len(signature):
        return False
    try:
        return hmac.compare_digest(expected, signature)
    except (TypeError, ValueError):
        return False


__all__ = ["compute_signature", "verify_request_signature"]
