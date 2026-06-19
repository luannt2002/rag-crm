"""Update language_pack VN/EN — remove 'đặt lịch'/'booking' from out_of_scope definition.

Revision: 0134
Prev:     0133

Root cause (verified 2026-05-29 from query_graph trace):
  Query "Cho mình thử 1 buổi gội đầu, đặt lịch sao?" → intent classifier
  returns "out_of_scope" → multi_query speculative cancelled → retrieve
  uses raw query → top-K miss → bot refuse oan.

  Reason: language_packs[code='vi', prompt_key='understand'].content
  contains: "out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, off-topic)"
  → LLM classifier sees "đặt lịch" listed under OOS examples → labels any
  query containing "đặt lịch" as out_of_scope.

  Fix file `src/ragbot/shared/i18n.py` is INSUFFICIENT because DB
  language_packs overrides in-memory fallback at runtime (per memory
  `project_v2_5_final` "i18n DB-driven").

Fix: UPDATE DB rows for code='vi' AND code='en' to remove the OOS example.

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (CLAUDE.md rule 7)
  ✅ Domain-neutral (intent classifier definition platform-wide)
  ✅ Reversible (downgrade restores old text)
"""

from alembic import op
from sqlalchemy import text

revision: str = "0134"
down_revision: str | None = "0133"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_OLD_VN = "- out_of_scope: nằm ngoài phạm vi (đặt lịch, thời tiết, off-topic)"
_NEW_VN = "- out_of_scope: nằm ngoài phạm vi (thời tiết, chuyện cười, off-topic ngoài corpus). LƯU Ý: 'đặt lịch' KHÔNG phải out_of_scope — đặt lịch là factoid/aggregation tùy ngữ cảnh dịch vụ"

_OLD_EN = "- out_of_scope: outside coverage (booking time, weather, jokes, off-topic)"
_NEW_EN = "- out_of_scope: outside coverage (weather, jokes, off-topic outside corpus). NOTE: 'booking/appointment' is NOT out_of_scope"


def upgrade() -> None:
    """Patch language_packs to remove booking from OOS examples."""
    op.execute(
        text(
            """
            UPDATE language_packs SET content = REPLACE(content, :old, :new),
              updated_at = NOW(), version = version + 1
            WHERE code='vi' AND prompt_key='understand' AND content LIKE '%' || :old || '%'
            """
        ).bindparams(old=_OLD_VN, new=_NEW_VN),
    )
    op.execute(
        text(
            """
            UPDATE language_packs SET content = REPLACE(content, :old, :new),
              updated_at = NOW(), version = version + 1
            WHERE code='en' AND prompt_key='understand' AND content LIKE '%' || :old || '%'
            """
        ).bindparams(old=_OLD_EN, new=_NEW_EN),
    )


def downgrade() -> None:
    """Restore original OOS definition with 'đặt lịch'/'booking'."""
    op.execute(
        text(
            """
            UPDATE language_packs SET content = REPLACE(content, :new, :old),
              updated_at = NOW(), version = version + 1
            WHERE code='vi' AND prompt_key='understand'
            """
        ).bindparams(old=_OLD_VN, new=_NEW_VN),
    )
    op.execute(
        text(
            """
            UPDATE language_packs SET content = REPLACE(content, :new, :old),
              updated_at = NOW(), version = version + 1
            WHERE code='en' AND prompt_key='understand'
            """
        ).bindparams(old=_OLD_EN, new=_NEW_EN),
    )
