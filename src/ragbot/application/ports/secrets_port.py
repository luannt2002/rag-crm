"""SecretsPort — resolves secret references (vault path / env) to plaintext.

Task C.2. Implementations live in `infrastructure.security.*`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsPort(Protocol):
    async def resolve(self, ref: str | None, encrypted: str | None) -> str: ...

    def encrypt(self, plain: str) -> str: ...


__all__ = ["SecretsPort"]
