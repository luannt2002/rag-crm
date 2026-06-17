"""Seed test-spa-id allowed_facts whitelist (BP-5 wrong refuse fix).

Revision: 0153
Prev:     0152

Trigger (X2 BUNDLED ship step 12):
  BP-5 verified 2026-05-29 evening: bot refused địa chỉ + giờ mở cửa
  query in turn 10 of multi-turn dialogue, despite chunk #1 retrieve
  literal "Địa chỉ: Số 102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội" +
  "Giờ mở cửa: 9-21h, từ T2-CN". Cause: output guardrail / sysprompt
  over-conservative on shingle-overlap with confirm-booking template.

Fix via rule 22 ALLOWED_FACTS_PASSTHROUGH (alembic 0151) — bot quotes
literal from ``bots.custom_vocabulary.allowed_facts`` when user asks
basic info. This migration seeds spa's basic facts.

Sacred-rule alignment:
  ✅ Per-bot scope (only test-spa-id)
  ✅ Owner self-service — bot owner can edit via admin UI
  ✅ Multi-tenant — each tenant declares own allowed_facts; rule 22 generic
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0153"
down_revision: str | None = "0152"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Seed allowed_facts under bots.custom_vocabulary for test-spa-id."""
    op.execute(
        text(
            """
            UPDATE bots
            SET custom_vocabulary = jsonb_set(
                COALESCE(custom_vocabulary, '{}')::jsonb,
                '{allowed_facts}',
                '{
                  "address": "Số 102 Vũ Trọng Phụng, Thanh Xuân, Hà Nội (đi thang bộ lên tầng 2)",
                  "hours": "9-21h, từ Thứ Hai đến Chủ Nhật",
                  "hotline": "0926.559.268",
                  "maps": "https://maps.app.goo.gl/Vo5sw3iHtZZWbVN9A"
                }'::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE bot_id = 'test-spa-id'
              AND channel_type = 'web'
              AND is_deleted = false
            """,
        ),
    )


def downgrade() -> None:
    """Strip allowed_facts."""
    op.execute(
        text(
            """
            UPDATE bots
            SET custom_vocabulary = custom_vocabulary - 'allowed_facts',
                updated_at = NOW()
            WHERE bot_id = 'test-spa-id' AND channel_type = 'web'
            """,
        ),
    )
