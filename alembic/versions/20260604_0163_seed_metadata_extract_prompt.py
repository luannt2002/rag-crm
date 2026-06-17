"""Seed language_packs.metadata_extract_default prompt template (vi + en).

Revision: 0163
Prev:     0162

Trigger (Plan 260604-metadata-aware-v4):
  Prompt template for GenericLLMMetadataExtractor MUST live trong DB
  (language_packs), KHÔNG hardcode trong constants.py. Owner có thể
  tune prompt per-locale qua admin route.

Sacred-rule alignment:
  ✅ Zero-hardcode (CLAUDE.md): prompt từ DB, không từ code
  ✅ Domain-neutral: prompt generic, không brand/tenant literal
  ✅ Localization: per-locale (vi, en)
  ✅ Owner self-service: admin update qua language_pack route
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0163"
down_revision: str | None = "0162"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


_VI_PROMPT = """Trích xuất từ câu hỏi sau các thông tin có cấu trúc dưới dạng JSON.
KHÔNG cần domain knowledge. Chỉ extract những gì user đã nói trong câu.

Schema:
{
  "entities": [tên riêng / khái niệm cụ thể],
  "topics": [chủ đề chính],
  "keywords": [2-5 từ khóa nội dung, loại stopwords],
  "numbers_or_years": [con số có ý nghĩa],
  "intent": "factoid|comparison|reasoning|listing|oos"
}

Câu hỏi: {query}

Chỉ trả về JSON object hợp lệ, KHÔNG markdown, KHÔNG giải thích."""


_EN_PROMPT = """Extract structured information from the following query as JSON.
NO domain knowledge required. Only extract what the user explicitly said.

Schema:
{
  "entities": [proper nouns / specific concepts],
  "topics": [main topics],
  "keywords": [2-5 content keywords, exclude stopwords],
  "numbers_or_years": [meaningful numbers],
  "intent": "factoid|comparison|reasoning|listing|oos"
}

Query: {query}

Return ONLY valid JSON object, NO markdown, NO explanation."""


def upgrade() -> None:
    """Seed prompt template VI + EN."""
    op.execute(
        text(
            """
            INSERT INTO language_packs (code, prompt_key, content, version, created_at, updated_at)
            VALUES ('vi', 'metadata_extract_default', :prompt, 1, NOW(), NOW())
            ON CONFLICT (code, prompt_key) DO UPDATE
              SET content = EXCLUDED.content,
                  version = language_packs.version + 1,
                  updated_at = NOW()
            """
        ).bindparams(prompt=_VI_PROMPT)
    )
    op.execute(
        text(
            """
            INSERT INTO language_packs (code, prompt_key, content, version, created_at, updated_at)
            VALUES ('en', 'metadata_extract_default', :prompt, 1, NOW(), NOW())
            ON CONFLICT (code, prompt_key) DO UPDATE
              SET content = EXCLUDED.content,
                  version = language_packs.version + 1,
                  updated_at = NOW()
            """
        ).bindparams(prompt=_EN_PROMPT)
    )


def downgrade() -> None:
    """Remove prompt template."""
    op.execute(
        text(
            """
            DELETE FROM language_packs
            WHERE prompt_key = 'metadata_extract_default'
              AND code IN ('vi', 'en')
            """
        )
    )
