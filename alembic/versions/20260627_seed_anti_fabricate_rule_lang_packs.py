"""Append an ANTI-FABRICATE rule to ``language_packs[vi|en][sysprompt_default_rules]``.

Trigger (AG-A2 / S1-B): the HALLU net needs a sysprompt-side rule that forbids
fabricating a link / number / fact that is NOT present in the retrieved context.
The existing GROUNDING section already says "never fabricate prices/addresses",
but does not explicitly cover URLs/links and arbitrary data values, and does not
spell out the "say you don't have it yet" behaviour for that case.

This migration APPENDS a new ``# ANTI-FABRICATE`` section to the existing
``sysprompt_default_rules`` content for each locale. It is APPEND-ONLY: the new
text is concatenated to the END of the current content (never prepended, never
inserted mid-prompt) — owner ``bots.system_prompt`` always precedes the
platform-default rules at assembly time (``SysPromptAssembler``), so the owner
content stays authoritative.

Sacred-rule alignment (ADR-W1-S10 governed append-only exception):
  ✅ Text seed via tracked alembic (rule 7 — never psql).
  ✅ Domain-neutral: forbids fabricating link/number/fact generically — no
     brand / service / price literal.
  ✅ APPEND-only: concatenated at the END of the existing content.
  ✅ Per-bot opt-out: the assembler strips rules listed in
     ``bots.plan_limits.sysprompt_rules_disabled`` (rule-block stripping path).
  ✅ Idempotent: re-running is a no-op (guarded by the section marker).
  ✅ Reversible: downgrade removes exactly the appended block.

NOT an answer-override (sacred-rule 10): this only adds an instruction the LLM
self-applies; the application never edits the LLM answer.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "seed_anti_fabricate_rule_260627"
down_revision = "rls_missing_ok_setting_20260626"
branch_labels = None
depends_on = None


_PROMPT_KEY = "sysprompt_default_rules"

# Section marker: presence in the stored content means the block is already
# appended → idempotent guard + precise downgrade boundary.
_MARKER_EN = "# ANTI-FABRICATE"
_MARKER_VI = "# CHỐNG BỊA DỮ LIỆU"

# Appended block per locale. Leading blank line keeps it visually separated
# from the prior section. Domain-neutral: speaks of "link / number / data
# value", no brand or service literal.
_BLOCK_EN = (
    "\n\n# ANTI-FABRICATE\n"
    "Only provide a link, number, or data value that appears in the provided "
    "context. If the context does not contain it, say you do not have that "
    "information yet — never invent a URL, phone number, price, or any value."
)
_BLOCK_VI = (
    "\n\n# CHỐNG BỊA DỮ LIỆU\n"
    "Chỉ đưa ra đường link, con số hoặc dữ kiện có trong ngữ cảnh được cung "
    "cấp. Nếu ngữ cảnh không có, hãy nói rằng bạn chưa có thông tin đó — tuyệt "
    "đối KHÔNG bịa ra URL, số điện thoại, giá hay bất kỳ giá trị nào."
)

_APPENDS = (
    ("en", _MARKER_EN, _BLOCK_EN),
    ("vi", _MARKER_VI, _BLOCK_VI),
)


def upgrade() -> None:
    """Append the ANTI-FABRICATE block to each locale's default rules.

    Idempotent: skips a locale whose content already contains the marker so a
    re-run (or a partial prior run) cannot double-append.
    """
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
    """Strip the appended block from each locale's default rules.

    Removes the exact appended block (which starts with the separating blank
    line), restoring the pre-migration content byte-for-byte. Matching on the
    full block — not just the marker — guarantees the leading ``\\n\\n``
    separator is removed too (no dangling whitespace).
    """
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
