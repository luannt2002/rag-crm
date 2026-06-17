"""Cost-cleanup: remove claude-haiku-4-5 + gpt-4.1 (full) from the model catalog.

Owner directive 2026-06-14: the platform LLM tier is locked to OpenAI
gpt-4.1-mini (primary) + gpt-4.1-nano (cheap small tasks). claude-haiku-4-5
(Anthropic) and gpt-4.1 (full, 5x mini cost) are removed entirely so they can
never be selected — full was the answer-model the 2026-06-11..13 spend spike
ran on; haiku was the narrate/slot fallback. ZeroEntropy zembed-1 (embedding)
and zerank-2 (reranker) are infrastructure and stay.

Runs LAST in the chain so a fresh ``alembic upgrade head`` on a clean DB ends
with the catalog already pruned, regardless of what earlier seed migrations
(0070 haiku, 0201 gpt-4.1-full) added. Idempotent + defensive: every binding
or system_config value still pointing at a removed model is repointed to
gpt-4.1-mini BEFORE the rows are deleted, so no FK can break.

Downgrade is a deliberate no-op: the models are intentionally retired; the
original seed rows are not restored (re-add via a new seed migration if ever
needed).
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0216"
down_revision = "0215"
branch_labels = None
depends_on = None

_REMOVED_MODELS = (
    "claude-haiku-4-5-20251001", "gpt-4.1",
    "gemma-4-e2b-it", "qwen3.6-35b-a3b-kimi-k2.6-reasoning-distilled",
)
# Providers retired with their models — catalog is OpenAI + ZeroEntropy only.
_REMOVED_PROVIDERS = ("jina_ai", "anthropic", "lmstudio", "infinity", "tei")
_TARGET = "gpt-4.1-mini"

# system_config keys whose VALUE may carry a model name/wire we must repoint.
_CONFIG_KEYS = (
    "default_answer_model", "llm_default_model", "contextual_retrieval_model",
    "metadata_extraction_model", "cascade_low_model", "cascade_high_model",
    "slot_extractor_model", "narrate_model", "decompose_model",
    "multi_query_model", "hyde_model",
)
# Stale values (bare name or provider-wire) that must become gpt-4.1-mini.
_STALE_VALUES = (
    "claude-haiku-4-5-20251001", "claude-haiku-4-5",
    "anthropic/claude-haiku-4-5", "anthropic/claude-haiku-4-5-20251001",
    "gpt-4.1", "openai/gpt-4.1",
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Repoint every binding on a removed model → gpt-4.1-mini (defensive;
    #    on a fresh upgrade 0212 already routed everything to mini, but a
    #    drifted DB may still hold a reference).
    for name in _REMOVED_MODELS:
        conn.execute(sa.text(
            "UPDATE bot_model_bindings SET record_model_id = "
            "(SELECT id FROM ai_models WHERE name = :tgt LIMIT 1) "
            "WHERE record_model_id = "
            "(SELECT id FROM ai_models WHERE name = :name LIMIT 1)"
        ), {"tgt": _TARGET, "name": name})

    # 2) Repoint system_config values that name a removed model.
    for key in _CONFIG_KEYS:
        for stale in _STALE_VALUES:
            conn.execute(sa.text(
                "UPDATE system_config SET value = CAST(:v AS jsonb) "
                "WHERE key = :k AND value::text = :stale"
            ), {"v": json.dumps(_TARGET), "k": key, "stale": json.dumps(stale)})

    # 3) Null out request_logs FK so the model rows can drop (forensic rows
    #    keep model_name text; the UUID FK is observability-only).
    for name in _REMOVED_MODELS:
        conn.execute(sa.text(
            "UPDATE request_logs SET record_model_id = NULL "
            "WHERE record_model_id = "
            "(SELECT id FROM ai_models WHERE name = :name LIMIT 1)"
        ), {"name": name})

    # 4) Delete the retired models.
    conn.execute(sa.text(
        "DELETE FROM ai_models WHERE name = ANY(:names)"
    ), {"names": list(_REMOVED_MODELS)})

    # 5) Delete retired providers once they hold no models (catalog =
    #    openai + ZeroEntropy only). NOT IN guard keeps it FK-safe.
    conn.execute(sa.text(
        "DELETE FROM ai_providers WHERE name = ANY(:names) "
        "AND id NOT IN (SELECT DISTINCT record_provider_id FROM ai_models "
        "WHERE record_provider_id IS NOT NULL)"
    ), {"names": list(_REMOVED_PROVIDERS)})


def downgrade() -> None:
    # Intentional no-op: retired models are not restored.
    pass
