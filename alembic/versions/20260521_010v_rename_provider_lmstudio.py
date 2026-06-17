"""[T3-Refactor] Rename ai_providers ``innocom_lmstudio`` → ``lmstudio``.

Revision ID: 010v
Revises: 010u
Create Date: 2026-05-21

Anh chốt 2026-05-21 cuối phiên: simplify provider name from
``innocom_lmstudio`` (vendor-bound) to ``lmstudio`` (generic). The host
is configurable via ``LMSTUDIO_BASE_URL`` env, so the row name does not
need to encode the vendor. Same provider can serve a different LM
Studio host later without DB rename.

Identity flow:
- Row ``id`` UUID unchanged → all ``ai_models.record_provider_id`` FK
  references stay intact (no cascade-rename needed).
- Row ``code`` stays ``"custom_openai"`` — that field controls LiteLLM
  routing prefix and must NOT change.
- Only the ``name`` column flips for human / admin-UI clarity.

Idempotent: ``WHERE name = 'innocom_lmstudio'`` guard. Re-running on an
already-renamed row is a no-op.
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "010v"
down_revision: str | None = "010u"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Rename ``innocom_lmstudio`` → ``lmstudio``."""
    op.execute(
        text(
            """
            UPDATE ai_providers SET
                name = 'lmstudio',
                updated_at = NOW()
            WHERE name = 'innocom_lmstudio'
            """,
        ),
    )


def downgrade() -> None:
    """Restore prior name for rollback symmetry."""
    op.execute(
        text(
            """
            UPDATE ai_providers SET
                name = 'innocom_lmstudio',
                updated_at = NOW()
            WHERE name = 'lmstudio'
            """,
        ),
    )
