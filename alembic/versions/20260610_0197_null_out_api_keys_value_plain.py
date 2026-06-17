"""NULL-out api_keys.value_plain after encrypt backfill (ADR-W1-KEY, step B).

Revision: 0197
Prev:     0196

Apply ONLY after the encrypt_api_keys_backfill revision is verified and the
dual-read soak window passed (journal `api_key_plaintext_read` = 0 events) —
see ADR-W1-KEY §6 step f/g. After this revision:
  SELECT count(*) FROM api_keys WHERE value_plain IS NOT NULL  → must be 0.

downgrade(): truly reversible — decrypts value_encrypted with the KEK
(env RAGBOT_CONFIG_KEK) and writes the plaintext back, NOT a no-op.
Crypto inline self-contained, envelope base64( nonce[12] || ciphertext+tag )
byte-compatible with infrastructure/security/env_secrets.py.
"""
from __future__ import annotations

import base64
import os

from alembic import op
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

revision: str = "0197"
down_revision: str | None = "0196"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEK_ENV = "RAGBOT_CONFIG_KEK"
_NONCE_LEN = 12


def _require_kek() -> bytes:
    """Return the KEK bytes from env, fail-loud with guidance if missing."""
    kek_b64 = os.getenv(_KEK_ENV)
    if not kek_b64:
        raise RuntimeError(
            f"{_KEK_ENV} not set — cannot decrypt api_keys.value_encrypted "
            "for the downgrade. Export the SAME KEK the app uses "
            "(env_secrets.py) and re-run. If the KEK is lost, keys must be "
            "re-entered via PUT /admin/api-keys/{provider_code}.",
        )
    return base64.b64decode(kek_b64)


def _decrypt_value(encrypted: str, kek: bytes) -> str:
    raw = base64.b64decode(encrypted)
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(kek).decrypt(nonce, ct, None).decode()


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            UPDATE api_keys
            SET value_plain = NULL
            WHERE value_encrypted IS NOT NULL
            """,
        ),
    )


def downgrade() -> None:
    kek = _require_kek()
    conn = op.get_bind()
    rows = conn.execute(
        text(
            """
            SELECT id, value_encrypted
            FROM api_keys
            WHERE value_encrypted IS NOT NULL
              AND value_plain IS NULL
            """,
        ),
    ).fetchall()
    for row_id, encrypted in rows:
        conn.execute(
            text("UPDATE api_keys SET value_plain = :plain WHERE id = :id"),
            {"plain": _decrypt_value(encrypted, kek), "id": row_id},
        )
