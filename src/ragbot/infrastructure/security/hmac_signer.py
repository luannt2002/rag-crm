"""HMAC signing for outbound webhooks (callbacks)."""

from __future__ import annotations

import hashlib
import hmac


def sign_payload(body: bytes, *, secret: str) -> str:
    """Return hex digest of HMAC-SHA256(body)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str, *, secret: str) -> bool:
    expected = sign_payload(body, secret=secret)
    return hmac.compare_digest(expected, signature)


__all__ = ["sign_payload", "verify_signature"]
