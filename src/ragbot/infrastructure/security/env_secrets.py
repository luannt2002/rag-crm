"""EnvSecretsAdapter — dev-mode SecretsPort using env-referenced keys or
AES-GCM ciphertext with KEK from env.

Task C.2. Format of `encrypted`: base64( nonce[12] || ciphertext+tag ).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EnvSecretsAdapter:
    """Dev mode: decrypt AES-GCM ciphertext with KEK from env RAGBOT_CONFIG_KEK.

    If `ref` is `env:VAR_NAME` → fetch directly from env (dev only).
    """

    def __init__(self, kek_env: str = "RAGBOT_CONFIG_KEK") -> None:
        self._kek_env = kek_env

    async def resolve(self, ref: str | None, encrypted: str | None) -> str:
        if ref and ref.startswith("env:"):
            return os.getenv(ref[4:], "")
        if encrypted:
            kek_b64 = os.getenv(self._kek_env)
            if not kek_b64:
                raise RuntimeError(
                    f"{self._kek_env} not set — cannot decrypt api key. "
                    "Set RAGBOT_CONFIG_KEK env (base64 32-byte AES key).",
                )
            kek = base64.b64decode(kek_b64)
            raw = base64.b64decode(encrypted)
            nonce, ct = raw[:12], raw[12:]
            aesgcm = AESGCM(kek)
            return aesgcm.decrypt(nonce, ct, None).decode()
        return ""

    @staticmethod
    def encrypt(plain: str, kek_env: str = "RAGBOT_CONFIG_KEK") -> str:
        kek_b64 = os.getenv(kek_env)
        if not kek_b64:
            raise RuntimeError(f"{kek_env} not set — cannot encrypt")
        kek = base64.b64decode(kek_b64)
        aesgcm = AESGCM(kek)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plain.encode(), None)
        return base64.b64encode(nonce + ct).decode()


__all__ = ["EnvSecretsAdapter"]
