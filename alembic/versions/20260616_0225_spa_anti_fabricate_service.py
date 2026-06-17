"""spa sysprompt: anti-fabricate-service rule (HALLU=0 — found by load-test).

Load-test 2026-06-16 found a HALLU breach: asked "có dịch vụ phun xăm thẩm mỹ
chứ" (phun xăm = 0 chunks in corpus), the bot answered "Dạ, Dr. Medispa CÓ dịch
vụ phun xăm thẩm mỹ" — fabricating a service from world knowledge despite the
docs-only rule. Retrieval returned 5 unrelated price chunks @0.52; the LLM
invented the service. This is the "invent-service" HALLU class.

The base 0221 prompt says "KHÔNG bịa tên dịch vụ" but does not explicitly cover
the "có dịch vụ X không?" confirmation pattern, where an over-helpful model
confirms a plausible-but-absent service. This migration appends an explicit
rule: only CONFIRM a service when its name appears in <documents>; otherwise say
it is not in the catalog. Append-only (never prepend/insert), idempotent
(skips if already present), reversible (downgrade strips the exact block).

Governed via alembic per the no-psql-hotfix rule. Domain-neutral guard only
scans *.template, not migrations.
"""
from alembic import op

revision = "0225"
down_revision = "0224"
branch_labels = None
depends_on = None

_BOT = "test-spa-id"
_MARK = "CHỐNG BỊA DỊCH VỤ"
_RULE = (
    "\n\n═══ CHỐNG BỊA DỊCH VỤ (bắt buộc — HALLU=0) ═══\n"
    "Khi khách hỏi \"có dịch vụ X không\" / \"bên em có làm X không\" / \"có X chứ\" "
    "mà tên X KHÔNG xuất hiện trong <documents> → trả lời: \"Dạ dịch vụ này em chưa "
    "thấy trong danh mục bên em ạ, anh/chị vui lòng liên hệ hotline để được hỗ trợ "
    "thêm ạ.\" TUYỆT ĐỐI KHÔNG xác nhận \"có\", KHÔNG mô tả, KHÔNG báo giá dịch vụ "
    "không có trong tài liệu — kể cả khi nghe rất hợp lý hoặc spa thường có. CHỈ "
    "xác nhận \"có\" khi tên dịch vụ XUẤT HIỆN trong <documents>."
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = system_prompt || {op.inline_literal(_RULE)}
        WHERE bot_id = {op.inline_literal(_BOT)}
          AND position({op.inline_literal(_MARK)} in system_prompt) = 0
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = replace(system_prompt, {op.inline_literal(_RULE)}, '')
        WHERE bot_id = {op.inline_literal(_BOT)}
        """
    )
