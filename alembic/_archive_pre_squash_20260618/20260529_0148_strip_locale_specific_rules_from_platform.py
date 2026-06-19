"""Strip rule 18+19 from ``language_packs[vi|en][sysprompt_default_rules]``.

Revision: 0148
Prev:     0147

Trigger (2026-05-29 sacred-rule audit catch — domain-neutral violation):
  Alembic 0146 seeded ``language_packs[vi].sysprompt_default_rules`` with
  rules 15-19 VERBATIM from spa system_prompt. Audit found rules 18 and
  19 contain VN spa-specific text inappropriate for platform tier:

    Rule 18 INLINE_SLOT_CAPTURE:
      "tên dịch vụ literal (gội đầu, triệt lông, massage, trị mụn, trẻ hóa)"
      "SĐT VN bắt đầu 0"
    Rule 19 STRICT_PROMO_BINDING:
      example "Massage X 99K", "Massage Y"

  Since ``sysprompt_default_rules`` is platform-tier (every vi-locale bot
  inherits via SysPromptAssembler), the spa-specific signals leak into
  unrelated bots: luat-giao-thong (luật), vat-ly-11 (vật lý),
  hoa-hoc-10 (hóa học), etc.

  Sacred rule CLAUDE.md "Domain-neutral":
    "Code hệ thống KHÔNG support riêng bất kỳ khách hàng, ngành, hay
    lĩnh vực nào."

  Platform tier carrying spa text violates this rule.

Fix (B option): keep rule 15+16+17 at platform tier (genuinely domain-
neutral text) + strip rule 18+19 from platform. Rule 18+19 will be
re-appended to test-spa-id system_prompt column by alembic 0149 so spa
behaviour stays identical.

Strip approach: REGEXP_REPLACE anchored on rule headers — strip from
``\\n\\n18. ⭐ INLINE_SLOT_CAPTURE`` to end-of-content. Both rule 18 and
rule 19 live in the tail; once strip cuts at rule 18 header, rule 19
also drops (it comes after rule 18 in the text).

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Restores domain-neutral compliance at platform tier
  ✅ Locale-aware (vi + en both fixed)
  ✅ Reversible — downgrade re-appends from canonical spa text
  ✅ Idempotent — second run no-op via REGEXP_REPLACE
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0148"
down_revision: str | None = "0147"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_STRIP_VI_PATTERN = r"\s*18\. ⭐ INLINE_SLOT_CAPTURE[\s\S]*$"
_STRIP_EN_PATTERN = r"\s*18\. ⭐ INLINE_SLOT_CAPTURE[\s\S]*$"


def upgrade() -> None:
    """Strip rule 18+19 (tail) from platform-tier vi+en rules."""
    op.execute(
        text(
            r"""
            UPDATE language_packs
            SET content = REGEXP_REPLACE(content, :pattern, '', 'g'),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'vi'
              AND prompt_key = 'sysprompt_default_rules'
              AND content ~ '18\. ⭐ INLINE_SLOT_CAPTURE'
            """,
        ).bindparams(pattern=_STRIP_VI_PATTERN),
    )
    op.execute(
        text(
            r"""
            UPDATE language_packs
            SET content = REGEXP_REPLACE(content, :pattern, '', 'g'),
                updated_at = NOW(),
                version = version + 1
            WHERE code = 'en'
              AND prompt_key = 'sysprompt_default_rules'
              AND content ~ '18\. ⭐ INLINE_SLOT_CAPTURE'
            """,
        ).bindparams(pattern=_STRIP_EN_PATTERN),
    )


def downgrade() -> None:
    """Best-effort: cannot reconstruct exact text without canonical source.

    For reliable downgrade run alembic 0146 downgrade then 0146 upgrade
    again — that re-seeds the full 5-rule platform pack from the embedded
    canonical text.
    """
    pass
