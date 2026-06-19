"""Install unaccent extension for accent-insensitive stats keyword match.

``query_by_name_keyword`` powers the list/count/category route and its docstring
claims "accent-insensitive via ILIKE" — but Postgres ILIKE folds CASE only, not
ACCENTS. A corpus typo ("Tẩy đa chết body" — đ instead of d) therefore did NOT
match the query "tẩy da chết", so a count/list query returned 1 service when the
catalog has 2 (verified: document_service_index has both "Tẩy đa chết body" 450k
and "Tẩy da chết & ủ trắng body" 550k). ``unaccent()`` folds đ→d (verified:
unaccent('Tẩy đa chết body') = 'Tay da chet body'), making the match truly
accent-insensitive as the docstring already promised. Domain-neutral — helps
every bot whose corpus has diacritic / typo variants, no per-bot data fix.
"""
from alembic import op

revision = "0240"
down_revision = "0239"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    # Leave the extension in place on downgrade — other objects may come to
    # depend on it and dropping a shared extension is riskier than keeping it.
    pass
