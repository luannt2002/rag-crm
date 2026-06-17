"""Answer LLM → gpt-4.1 (full) + anti-self-compute sysprompt rule.

Root-cause (2026-06-11 debug): the answer LLM gpt-4.1-mini mis-reads / mis-computes
multi-step numbers (e.g. luat-giao-thong states a fine as 20-24M vs corpus 16-18M,
self-sums 4M+16M=20M not in any chunk). Retrieval/embedding/rerank are healthy.

Two correctness fixes (governed, reversible):
  1. Switch the ANSWER model from gpt-4.1-mini → gpt-4.1 (stronger reasoning).
     Only the answer/generation default — sub-roles (decomposer, multi-query,
     enrichment, judge) stay mini. Embedding (zembed-1) + reranker unchanged.
  2. Append a DOMAIN-NEUTRAL anti-self-compute rule to the platform sysprompt
     default rules (governed append, ADR-W1-S10): cite documented numbers
     verbatim; never present a self-computed total as a documented figure.
     Math/physics bots still show computation steps — the rule only forbids
     passing off an invented/rounded total as if it came from the documents.

Reversible: downgrade restores gpt-4.1-mini and strips the appended rule block.
"""
from alembic import op

revision = "0201"
down_revision = "0200"
branch_labels = None
depends_on = None

_RULE_MARK_BEGIN = "<!-- anti-self-compute:0201 -->"
_RULE_MARK_END = "<!-- /anti-self-compute:0201 -->"

_RULE_VI = (
    "\n\n" + _RULE_MARK_BEGIN + "\n"
    "- Khi nêu một con số có trong tài liệu (giá, số tiền, mức phạt, mã số, ngày tháng), "
    "phải dùng ĐÚNG NGUYÊN VĂN con số trong tài liệu — không làm tròn, không sửa, không ước lượng. "
    "Nếu câu hỏi cần một con số tổng/gộp mà tài liệu KHÔNG ghi sẵn, hãy nêu rõ từng con số nguồn kèm "
    "phép tính rõ ràng; TUYỆT ĐỐI không trình bày một con số tự tính như thể nó là số liệu có sẵn trong tài liệu.\n"
    + _RULE_MARK_END
)
_RULE_EN = (
    "\n\n" + _RULE_MARK_BEGIN + "\n"
    "- When stating a number found in the documents (price, amount, fine, code, date), use it "
    "EXACTLY as written — do not round, alter, or approximate. If a combined/total figure is requested "
    "but not stated in the documents, show each source number with explicit arithmetic; NEVER present a "
    "self-computed total as if it were a documented figure.\n"
    + _RULE_MARK_END
)


def upgrade() -> None:
    # 1. Add gpt-4.1 by cloning the gpt-4.1-mini row (same provider/caps), with
    #    full-model pricing ($2/$8 per 1M = 0.002/0.008 per 1k) + premium tier.
    op.execute(
        """
        INSERT INTO ai_models (
            id, record_provider_id, name, kind, context_window, max_output_tokens,
            input_price_per_1k_usd, output_price_per_1k_usd, supports_streaming,
            supports_tools, supports_vision, supports_json_mode, languages,
            metadata_json, enabled, model_id, default_temperature, default_top_p,
            default_max_tokens, quality_tier, supports_caching, supports_reasoning,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(), record_provider_id, 'gpt-4.1', kind, context_window,
            max_output_tokens, 0.002000, 0.008000, supports_streaming, supports_tools,
            supports_vision, supports_json_mode, languages, metadata_json, true,
            'gpt-4.1', default_temperature, default_top_p, default_max_tokens,
            'premium', supports_caching, supports_reasoning, now(), now()
        FROM ai_models
        WHERE name = 'gpt-4.1-mini'
          AND NOT EXISTS (SELECT 1 FROM ai_models WHERE name = 'gpt-4.1');
        """
    )

    # 2. Point the ANSWER model defaults at gpt-4.1 (jsonb value).
    op.execute(
        """
        UPDATE system_config
        SET value = to_jsonb('gpt-4.1'::text)
        WHERE key IN ('default_answer_model', 'llm_default_model');
        """
    )

    # 3. Append the domain-neutral anti-self-compute rule (idempotent via marker).
    op.execute(
        f"""
        UPDATE language_packs
        SET content = content || $rule${_RULE_VI}$rule$
        WHERE prompt_key = 'sysprompt_default_rules' AND code = 'vi'
          AND position('{_RULE_MARK_BEGIN}' in content) = 0;
        """
    )
    op.execute(
        f"""
        UPDATE language_packs
        SET content = content || $rule${_RULE_EN}$rule$
        WHERE prompt_key = 'sysprompt_default_rules' AND code = 'en'
          AND position('{_RULE_MARK_BEGIN}' in content) = 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE system_config
        SET value = to_jsonb('gpt-4.1-mini'::text)
        WHERE key IN ('default_answer_model', 'llm_default_model');
        """
    )
    # Strip the appended rule block (everything from the begin marker on this row).
    op.execute(
        f"""
        UPDATE language_packs
        SET content = left(content, position('{_RULE_MARK_BEGIN}' in content) - 1)
        WHERE prompt_key = 'sysprompt_default_rules'
          AND position('{_RULE_MARK_BEGIN}' in content) > 0;
        """
    )
    op.execute("DELETE FROM ai_models WHERE name = 'gpt-4.1';")
