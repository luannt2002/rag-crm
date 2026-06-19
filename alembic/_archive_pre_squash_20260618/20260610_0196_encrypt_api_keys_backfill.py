"""Encrypt-copy api_keys.value_plain → value_encrypted (ADR-W1-KEY, step A).

Revision: 0196
Prev:     0195

For every row WHERE value_plain IS NOT NULL AND value_encrypted IS NULL:
- value_encrypted = AES-256-GCM(value_plain) with KEK from env
  RAGBOT_CONFIG_KEK, envelope base64( nonce[12] || ciphertext+tag ) —
  byte-compatible with infrastructure/security/env_secrets.py.
- metadata_json gains {'fingerprint': sha256(value_plain)[:12]} so the
  admin list endpoint never needs the plaintext again.

value_plain is KEPT (NOT nulled) — the follow-up revision
(null_out_api_keys_value_plain) removes it after the dual-read soak
window, so each step rolls back independently.

Crypto code is inline self-contained (alembic convention: migrations do
not import src/). KEK missing → fail-loud RuntimeError with setup
guidance; the migration never runs blind.

downgrade(): NULL-out value_encrypted (value_plain untouched → fully
reversible without the KEK).
"""
from __future__ import annotations

import base64
import hashlib
import os

from alembic import op
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

revision: str = "0196"
down_revision: str | None = "0195"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEK_ENV = "RAGBOT_CONFIG_KEK"
_NONCE_LEN = 12
_FINGERPRINT_HEX_LEN = 12


def _require_kek() -> bytes:
    """Return the KEK bytes from env, fail-loud with guidance if missing."""
    kek_b64 = os.getenv(_KEK_ENV)
    if not kek_b64:
        raise RuntimeError(
            f"{_KEK_ENV} not set — cannot encrypt api_keys.value_plain. "
            "Generate a KEK with: python3 -c \"import base64,os; "
            "print(base64.b64encode(os.urandom(32)).decode())\" , set it in "
            ".env + every systemd unit (api, chat_worker, document_worker), "
            "back it up off-host, then re-run `alembic upgrade head`. The "
            "SAME value must be live for the app (env_secrets.py) or "
            "decryption will fail at runtime.",
        )
    return base64.b64decode(kek_b64)


def _encrypt_value(plain: str, kek: bytes) -> str:
    """AES-256-GCM, envelope base64(nonce[12] || ct+tag) — matches env_secrets.py."""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(kek).encrypt(nonce, plain.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def upgrade() -> None:
    kek = _require_kek()
    conn = op.get_bind()
    rows = conn.execute(
        text(
            """
            SELECT id, value_plain
            FROM api_keys
            WHERE value_plain IS NOT NULL
              AND value_encrypted IS NULL
            """,
        ),
    ).fetchall()
    for row_id, plain in rows:
        encrypted = _encrypt_value(plain, kek)
        fingerprint = hashlib.sha256(plain.encode()).hexdigest()[
            :_FINGERPRINT_HEX_LEN
        ]
        conn.execute(
            text(
                """
                UPDATE api_keys
                SET value_encrypted = :enc,
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                        || jsonb_build_object('fingerprint', :fp)
                WHERE id = :id
                """,
            ),
            {"enc": encrypted, "fp": fingerprint, "id": row_id},
        )


def downgrade() -> None:
    # value_plain was kept by upgrade() → dropping the ciphertext copy is
    # lossless and needs no KEK.
    op.get_bind().execute(
        text(
            """
            UPDATE api_keys
            SET value_encrypted = NULL
            WHERE value_plain IS NOT NULL
            """,
        ),
    )
