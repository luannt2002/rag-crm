"""[T1-Smartness] Domain-neutral multi-language keys — register 6 system_config slots.

Why
---
Code-tracked Vietnamese-only constants (``_BOILERPLATE_PATTERNS`` in
``shared/prompt_compression.py``, ``_VI_ABBREVIATIONS_SEED`` in
``shared/vi_tokenizer.py``, the four ``_*_PROMPT`` blocks in
``application/services/multi_query_expansion.py``) violate the platform's
domain-neutral mandate the moment a non-VI tenant joins: adding ``en``
boilerplate or ``es`` stopwords would require shipping a release.

This migration reserves six DB rows in ``system_config`` so the runtime
resolver (boilerplate strip, stopword filter, abbreviation expansion,
section markers, legal-ref regex, knowledge-graph stopwords) reads
per-language dicts from JSONB:

    {
      "vi": [ ... ],
      "en": [ ... ],
      "es": [ ... ]
    }

The rows are seeded **empty** (``{}``) so this migration is a pure
schema-side reservation — no behaviour change until ``0099`` lands the
VI back-compat payload. Operators can then ``UPDATE system_config SET
value = jsonb_set(value, '{en}', ...)`` to add a new language without a
deploy.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so re-running on an
already-seeded DB skips silently. ``downgrade`` deletes only the six
keys this migration introduced.

Revision ID: 0098
Revises: 0097
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


_KEYS: tuple[tuple[str, str], ...] = (
    (
        "boilerplate_removal_patterns_by_language",
        "Per-language regex patterns stripped during ingest text normalization. "
        "Shape: {lang_code: [regex, ...]}. Empty = no stripping for that language.",
    ),
    (
        "stopwords_by_language",
        "Per-language stopword list used by BM25 / lexical retrieval. "
        "Shape: {lang_code: [word, ...]}.",
    ),
    (
        "default_abbreviations_by_language",
        "Platform-default abbreviation expansion map per language; per-bot "
        "overrides live in bots.custom_vocabulary.abbreviations. "
        "Shape: {lang_code: {abbr: expansion}}.",
    ),
    (
        "section_markers_by_language",
        "Per-language section / heading markers used by AdapChunk structural "
        "split (Điều / Chương / Article / Section, ...). "
        "Shape: {lang_code: [marker, ...]}.",
    ),
    (
        "legal_ref_patterns_by_language",
        "Per-language regex extracting legal references for citation linking "
        "(Điều N, Article N, Art. N, ...). Shape: {lang_code: [regex, ...]}.",
    ),
    (
        "knowledge_graph_stopwords_by_language",
        "Per-language stopwords excluded when building knowledge-graph entity "
        "links. Shape: {lang_code: [word, ...]}.",
    ),
)


_INSERT_SQL = text(
    """
    INSERT INTO system_config (key, value, value_type, description)
    VALUES (:key, '{}'::jsonb, 'json', :description)
    ON CONFLICT (key) DO NOTHING
    """
)


_DELETE_SQL = text("DELETE FROM system_config WHERE key = :key")


def upgrade() -> None:
    for key, description in _KEYS:
        op.execute(_INSERT_SQL.bindparams(key=key, description=description))


def downgrade() -> None:
    for key, _description in _KEYS:
        op.execute(_DELETE_SQL.bindparams(key=key))
