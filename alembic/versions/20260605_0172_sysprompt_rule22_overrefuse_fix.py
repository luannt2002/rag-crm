"""Fix shared sysprompt rule 22 (ALLOWED_FACTS) over-refuse + domain leak.

Revision: 0172
Prev:     0171

Two issues in the platform-shared ``language_packs[vi].sysprompt_default_rules``
rule 22 (ALLOWED_FACTS_PASSTHROUGH), found by the sysprompt-layer audit:

1. **Over-refuse**: "Nếu key không có trong allowed_facts → áp dụng rule 10
   PARTIAL_ANSWER (báo chưa có info)". This forces a refusal for
   address/hours/hotline questions whenever the bot owner has NOT populated
   ``allowed_facts`` — even when the answer IS present in the retrieved
   <documents>. Bots that never set allowed_facts refuse answerable questions.
   Fix: when the key is absent, ANSWER from <documents> if present; only
   partial-refuse when the info is in neither allowed_facts nor <documents>.

2. **Domain leak**: the rule names "spa" in a platform-tier (domain-neutral)
   rule sent to every bot. Genericise → "doanh nghiệp".

Surgical REPLACE on the two exact substrings (vi pack), reversible. Does NOT
restructure the rule. Sacred-rule 7 (config via alembic, not psql).

NOTE (out of scope, separate careful pass): rule 17's anti-conflate EXAMPLES
still embed spa service terms ("massage"/"triệt lông") — genericising woven
multi-line examples risks regressing every bot's behaviour, so it is deferred
to a reviewed rewrite rather than rushed here. The en pack rule 22 mirror is
also a follow-up (vi is the primary locale under test).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0172"
down_revision: str | None = "0171"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_OLD_HEADER = "ALLOWED_FACTS_PASSTHROUGH — Thông tin cơ bản về spa ("
_NEW_HEADER = "ALLOWED_FACTS_PASSTHROUGH — Thông tin cơ bản về doanh nghiệp ("

_OLD_REFUSE = (
    "Nếu key không có trong allowed_facts → áp dụng rule 10 PARTIAL_ANSWER "
    "(báo chưa có info, mời liên hệ)."
)
_NEW_REFUSE = (
    "Nếu key không có trong allowed_facts → TRẢ LỜI từ <documents> nếu thông tin "
    "có trong tài liệu; CHỈ áp dụng rule 10 PARTIAL_ANSWER khi thông tin không "
    "có trong cả allowed_facts lẫn <documents>."
)

_KEY = "sysprompt_default_rules"
_CODE = "vi"


def _swap(old: str, new: str) -> None:
    op.execute(
        text("""
            UPDATE language_packs
            SET content = REPLACE(content, :old, :new),
                version = version + 1, updated_at = NOW()
            WHERE prompt_key = :k AND code = :c
        """).bindparams(old=old, new=new, k=_KEY, c=_CODE)
    )


def upgrade() -> None:
    _swap(_OLD_HEADER, _NEW_HEADER)
    _swap(_OLD_REFUSE, _NEW_REFUSE)


def downgrade() -> None:
    _swap(_NEW_HEADER, _OLD_HEADER)
    _swap(_NEW_REFUSE, _OLD_REFUSE)
