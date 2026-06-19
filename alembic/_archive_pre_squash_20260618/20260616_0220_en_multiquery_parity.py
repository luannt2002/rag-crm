"""EN multi-query prompt parity — language_packs en gains the 4 keys vi has.

Multi-language gap (audit 2026-06-16): the ``vi`` pack carried 4 intent-specific
multi-query expansion prompts that ``en`` lacked, so an English bot whose intent
resolved to factoid/aggregation/comparison/multi_hop fell back to the generic
path with no English expansion guidance. This seeds faithful English equivalents
(``{n}`` placeholder preserved — substituted at runtime). Idempotent.

Domain-neutral platform text, tracked in alembic (no psql hot-fix to
language_packs.content — CLAUDE.md sacred rule 9).
"""
import sqlalchemy as sa
from alembic import op

revision = "0220"
down_revision = "0219"
branch_labels = None
depends_on = None

_KEYS = ("multi_query_factoid_prompt", "multi_query_aggregation_prompt",
         "multi_query_comparison_prompt", "multi_query_multi_hop_prompt")

_EN = {
    "multi_query_factoid_prompt": (
        "You are a query-rewriting assistant. Given the user question, produce {n} "
        "distinct paraphrases that express the same meaning with different wording. "
        "Goal: widen document-search coverage. Reply EXACTLY as a JSON array of {n} "
        "strings, no commentary.\n"
        'Example: ["question 1", "question 2", "question 3"]'
    ),
    "multi_query_aggregation_prompt": (
        "You are a multi-attribute aggregation query-expansion assistant. For a "
        "question that gathers several attributes/aspects, produce {n} variants — "
        "each phrased as an answer hypothesis (HyDE answer-template) focused on ONE "
        "specific attribute. Keep each hypothesis short, using the wording the "
        "reference documents would use. Reply EXACTLY as a JSON array of {n} strings, "
        "no commentary.\n"
        'Example: ["hypothesis about attribute 1", "hypothesis about attribute 2"]'
    ),
    "multi_query_comparison_prompt": (
        "You are a comparison query-expansion assistant. For a question contrasting "
        "multiple entities/options, produce {n} variants focused on EACH entity pair "
        "or EACH compared attribute. Each variant retrieves documents for one "
        "entity/attribute so the reranker can synthesise. Reply EXACTLY as a JSON "
        "array of {n} strings, no commentary.\n"
        'Example: ["query for entity-A", "query for entity-B", "query for attribute-X"]'
    ),
    "multi_query_multi_hop_prompt": (
        "You are a multi-hop decomposition assistant. For a user question that must "
        "combine several facts, decompose it into {n} SEPARATE sub-questions — each "
        "focused on ONE aspect or dependent step. Avoid restating the same idea; "
        "prefer sub-questions that differ in entity, attribute, or condition. Reply "
        "EXACTLY as a JSON array of {n} strings, no commentary.\n"
        'Example: ["sub-question 1", "sub-question 2", "sub-question 3"]'
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    for key in _KEYS:
        bind.execute(
            sa.text(
                "INSERT INTO language_packs (code, prompt_key, content, version) "
                "VALUES ('en', :k, :c, 1) ON CONFLICT (code, prompt_key) DO NOTHING"
            ),
            {"k": key, "c": _EN[key]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM language_packs WHERE code='en' AND prompt_key = ANY(:keys)"),
        {"keys": list(_KEYS)},
    )
