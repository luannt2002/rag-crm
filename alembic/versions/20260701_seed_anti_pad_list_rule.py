"""Append an ANTI-PAD-LIST rule to ``language_packs[vi|en][sysprompt_default_rules]``.

Trigger (Phase-3 QA deep-analysis, legal bot 2026-07-01): on SUMMARY questions
("quy định về gì", "phạm vi điều chỉnh") the bot padded the đối-tượng-áp-dụng list
with **"tổ chức kinh doanh vàng"** and **"tổ chức kinh doanh bảo hiểm"** — neither is
in the circular (corpus-verified absent). The DIRECT question ("đối tượng nào?")
answered the exact 8 orgs correctly; only the summary drifted, pulling plausible
extra list-items from the model's general knowledge of similar NHNN circulars. The
existing ANTI-FABRICATE / ANTI-INVENT-VARIANT rules cover links/numbers/product
variants but not **enumeration padding in a summary**.

APPENDS a ``# ANTI-PAD-LIST`` section (APPEND-only, after the owner prompt at
assembly). Sacred-rule aligned (ADR-W1-S10 governed append): tracked alembic (rule 7),
domain-neutral ("parties / members / scope / components" — no brand/law literal),
per-bot opt-out via ``plan_limits.sysprompt_rules_disabled``, idempotent, reversible.
NOT an answer-override (rule 10) — the LLM self-applies it.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "seed_anti_pad_list_260701"
down_revision = "reqlog_plaintext_260701"
branch_labels = None
depends_on = None


_PROMPT_KEY = "sysprompt_default_rules"

_MARKER_EN = "# ANTI-PAD-LIST"
_MARKER_VI = "# CHỐNG BỊA DANH SÁCH"

_BLOCK_EN = (
    "\n\n# ANTI-PAD-LIST\n"
    "When listing or summarizing a set (applicable parties, members, scope, "
    "components, categories…), include ONLY items that appear EXPLICITLY in the "
    "context. NEVER add an item from general or outside knowledge, even if it "
    "seems plausible — an incomplete list is better than one padded with items "
    "not in the document. If unsure a set is complete, say it is per the source "
    "rather than inventing extra entries."
)
_BLOCK_VI = (
    "\n\n# CHỐNG BỊA DANH SÁCH\n"
    "Khi liệt kê hoặc tóm tắt một tập hợp (đối tượng áp dụng, thành viên, phạm "
    "vi, thành phần, phân loại…), CHỈ nêu những mục XUẤT HIỆN TƯỜNG MINH trong "
    "ngữ cảnh. TUYỆT ĐỐI KHÔNG thêm mục từ kiến thức chung/bên ngoài dù nghe hợp "
    "lý — thà liệt kê thiếu còn hơn thêm mục không có trong tài liệu. Nếu không "
    "chắc danh sách đã đủ, nói theo đúng tài liệu thay vì tự bịa thêm mục."
)

_APPENDS = (
    ("en", _MARKER_EN, _BLOCK_EN),
    ("vi", _MARKER_VI, _BLOCK_VI),
)


def upgrade() -> None:
    conn = op.get_bind()
    for code, marker, block in _APPENDS:
        conn.execute(
            text(
                """
                UPDATE language_packs
                SET content = content || :block
                WHERE code = :code
                  AND prompt_key = :key
                  AND content NOT LIKE '%' || :marker || '%'
                """,
            ),
            {"block": block, "code": code, "key": _PROMPT_KEY, "marker": marker},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for code, _marker, block in _APPENDS:
        conn.execute(
            text(
                """
                UPDATE language_packs
                SET content = left(content, position(:block in content) - 1)
                WHERE code = :code
                  AND prompt_key = :key
                  AND position(:block in content) > 0
                """,
            ),
            {"code": code, "key": _PROMPT_KEY, "block": block},
        )
