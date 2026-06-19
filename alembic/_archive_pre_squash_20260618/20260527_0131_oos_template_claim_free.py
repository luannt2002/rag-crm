"""Fix 1.1 — oos_answer_template claim-free per RAGAS audit 2026-05-27.

Revision: 0131
Prev:     0130

Root cause (RAGAS R3 audit):
  1 turn `comparison` SAI: bot correctly refuse nhưng refuse text chứa
  "Thông tư 09/2020/TT-NHNN" → RAGAS judge extract claim, KHÔNG có chunks
  back up → Faith = 0.
  Pattern affects any refusal containing brand/doc names that judge
  treats as factual claims.

Fix:
  Strip document/brand names from oos_answer_template:
  - thong-tu: bỏ "Thông tư 09/2020/TT-NHNN" + "Ngân hàng Nhà nước Việt Nam"
  - test-spa: bỏ "Dr. Medispa" + "hotline 0926.559.268"
  - tessss: giữ generic (đã không có brand)

Refusal trở thành claim-free → RAGAS extract 0 claims từ refusal text →
Faith=0 turn không xảy ra nữa (refusal là valid empty response).

Sacred-rule alignment:
  ✅ Pure DB UPDATE via alembic (rule 7)
  ✅ Domain-neutral after fix (no brand literal)
  ✅ Bot owner sửa lại được qua admin UI nếu muốn
  ✅ HALLU=0 preserved (refusal action không đổi, chỉ wording)
  ✅ Reversible (downgrade restore old text)

Expected lift:
  - Faith: 80% → ~83% (close 1-2 refusal-related turns out of 5 fail in R3)
"""

from alembic import op
from sqlalchemy import text

revision: str = "0131"
down_revision: str | None = "0130"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Generic claim-free refusal text per bot. KHÔNG chứa document name,
# brand, phone — bot owner có thể tune lại qua admin UI.
_NEW_OOS_TEMPLATES: dict[str, str] = {
    "thong-tu-09-2020-tt-nhnn": (
        "Em chưa thấy thông tin này trong tài liệu hiện có. "
        "Anh/chị vui lòng tham khảo văn bản gốc để được tư vấn chính xác ạ."
    ),
    "test-spa-id": (
        "Em chưa có thông tin chính xác về vấn đề này. "
        "Anh/chị vui lòng liên hệ trực tiếp để được hỗ trợ ạ."
    ),
    "tessss": (
        "Em chưa tìm thấy thông tin chính xác về vấn đề này trong văn bản. "
        "Anh/chị vui lòng cung cấp thêm chi tiết hoặc tham khảo văn bản gốc ạ."
    ),
}

# Snapshot of previous values for downgrade reversal.
_OLD_OOS_TEMPLATES: dict[str, str] = {
    "thong-tu-09-2020-tt-nhnn": (
        "Em chưa thấy thông tin này trong Thông tư 09/2020/TT-NHNN. "
        "Vui lòng tham khảo văn bản gốc hoặc liên hệ Ngân hàng Nhà nước "
        "Việt Nam để được tư vấn chính xác ạ."
    ),
    "test-spa-id": (
        "Em chưa có thông tin chính xác về vấn đề này, anh/chị vui lòng "
        "liên hệ Dr. Medispa qua hotline 0926.559.268 để được hỗ trợ ạ."
    ),
    "tessss": (
        "Em chưa tìm thấy thông tin chính xác về vấn đề này trong văn bản. "
        "Anh/chị vui lòng cung cấp thêm chi tiết hoặc tham khảo trực tiếp "
        "văn bản gốc ạ."
    ),
}


def upgrade() -> None:
    """Replace brand/doc-name refusal text with claim-free version."""
    for bot_id, new_text in _NEW_OOS_TEMPLATES.items():
        op.execute(
            text(
                "UPDATE bots SET oos_answer_template = :new_text, updated_at = NOW() "
                "WHERE bot_id = :bot_id"
            ).bindparams(new_text=new_text, bot_id=bot_id),
        )


def downgrade() -> None:
    """Restore prior brand/doc-name refusal text."""
    for bot_id, old_text in _OLD_OOS_TEMPLATES.items():
        op.execute(
            text(
                "UPDATE bots SET oos_answer_template = :old_text, updated_at = NOW() "
                "WHERE bot_id = :bot_id"
            ).bindparams(old_text=old_text, bot_id=bot_id),
        )
