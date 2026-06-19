"""Seed ``system_config.default_vocabulary_vi`` from the platform Vietnamese
abbreviation dict.

Why
---
Multi-industry / multi-language audit (commit ``2fd9488``) found that
Vietnamese teencode + abbreviations were baked into Python source. That
made the platform behave incorrectly for non-Vietnamese tenants (an EN bot
would mis-expand the bare ASCII token ``"k"`` into ``"không"``) and made it
impossible for operators to tweak the dictionary without a code release.

This migration moves the platform-wide default into ``system_config`` as
``default_vocabulary_vi`` (JSONB dict ``{abbrev: expansion}``) so:

- VN tenants keep current behaviour (zero data loss; ``vi_tokenizer`` still
  uses the in-process seed when system_config is empty).
- Operators can extend the dict without redeploy
  (``UPDATE system_config SET value = ... WHERE key = 'default_vocabulary_vi'``).
- Per-bot ``custom_vocabulary.abbreviations`` overrides DB defaults at
  call time (already handled in the orchestrator).

Idempotency
-----------
``ON CONFLICT (key) DO NOTHING`` — safe to re-run. Operators who have
already customised the value keep their override.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

# Local copy of the seed — do NOT import from the application package
# (Alembic upgrade should not depend on app-side imports because partial
# refactors can break them and abort migrations).
_VI_ABBREVIATIONS_SEED: dict[str, str] = {
    # Teencode phổ biến
    "ko": "không", "k": "không", "hk": "không", "hem": "không",
    "dc": "được", "đc": "được",
    "mk": "mình", "mn": "mọi người", "ns": "nói",
    "j": "gì", "gj": "gì", "r": "rồi", "vs": "với",
    "ntn": "như thế nào", "bn": "bao nhiêu", "bnh": "bao nhiêu",
    "tks": "cảm ơn", "ib": "inbox", "rep": "trả lời", "tl": "trả lời",
    "fb": "facebook", "zl": "zalo", "oki": "ok", "okie": "ok",
    # Viết tắt phổ biến
    "sdt": "số điện thoại", "đt": "điện thoại",
    "nv": "nhân viên", "kh": "khách hàng",
    "sp": "sản phẩm", "dv": "dịch vụ",
    "ck": "chuyển khoản", "tk": "tài khoản",
    "km": "khuyến mãi", "gt": "giới thiệu",
    "lh": "liên hệ", "đk": "đăng ký", "cs": "chính sách",
    "dn": "doanh nghiệp", "cn": "chủ nhật",
    # Teencode nâng cao
    "vd": "ví dụ", "pm": "nhắn tin",
    "cx": "cũng", "cg": "cũng", "ms": "mới",
    "trc": "trước", "nc": "nói chuyện", "bt": "bình thường",
    "nma": "nhưng mà", "thik": "thích", "lm": "làm", "bik": "biết",
    "thui": "thôi", "nha": "nhé",
    "z": "vậy", "v": "vậy",
    # Viết tắt hành chính
    "bhxh": "bảo hiểm xã hội", "bhyt": "bảo hiểm y tế",
    "ubnd": "ủy ban nhân dân", "hdld": "hợp đồng lao động",
    "nld": "người lao động", "hđ": "hợp đồng",
}

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = json.dumps(_VI_ABBREVIATIONS_SEED, ensure_ascii=False)
    op.execute(
        text(
            """
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (
                'default_vocabulary_vi',
                CAST(:val AS jsonb),
                'json',
                'Vietnamese abbreviation/teencode expansion map. Used by '
                'vi_tokenizer.expand_abbreviations + VocabularyExpander when '
                'the bot.language = vi. Operator-editable; per-bot '
                'custom_vocabulary.abbreviations overrides this at call time.',
                now()
            )
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(val=payload)
    )


def downgrade() -> None:
    op.execute(
        text("DELETE FROM system_config WHERE key = 'default_vocabulary_vi'")
    )
