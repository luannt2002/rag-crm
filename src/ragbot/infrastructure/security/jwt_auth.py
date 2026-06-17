"""JWT verifier — supports HS256 (dev) + RS256 (prod).

For local dev without keys, falls back to HS256 with `TENANT_HMAC_SECRET`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jose import JWTError, jwt

from ragbot.shared.constants import DEFAULT_JWT_CLOCK_SKEW_S
from ragbot.shared.errors import UnauthorizedError


class JwtVerifier:
    def __init__(
        self,
        *,
        algorithm: str = "RS256",
        public_key_path: str | None = None,
        hmac_secret: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        leeway_s: int = DEFAULT_JWT_CLOCK_SKEW_S,
    ) -> None:
        self._alg = algorithm
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_s
        self._key: str | None = None
        if algorithm.startswith("RS") and public_key_path:
            self._key = Path(public_key_path).read_text(encoding="utf-8")
        elif algorithm.startswith("HS"):
            self._key = hmac_secret
        else:
            # Fall back to HS256 + secret if no RSA key provided
            self._alg = "HS256"
            self._key = hmac_secret

    def verify(self, token: str) -> dict[str, Any]:
        if self._key is None:
            raise UnauthorizedError("JWT verifier not configured")
        try:
            opts = {
                "verify_aud": bool(self._audience),
                "leeway": self._leeway,
            }
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[self._alg],
                issuer=self._issuer,
                audience=self._audience,
                options=opts,
            )
            return dict(payload)
        except JWTError as exc:
            raise UnauthorizedError(f"invalid jwt: {exc}") from exc


def decode_unverified(token: str) -> dict[str, Any]:
    """Decode WITHOUT signature verification — only for diagnostics."""
    try:
        return dict(jwt.get_unverified_claims(token))
    except JWTError as exc:
        raise UnauthorizedError(f"malformed jwt: {exc}") from exc


__all__ = ["JwtVerifier", "decode_unverified"]
