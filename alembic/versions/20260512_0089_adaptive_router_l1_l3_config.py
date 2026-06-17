"""[T1-Smartness] Seed Adaptive Router L1 (classifier) + L3 (decomposer) config.

Stream S6 Wave-2 fix for multi-entity PASS rate (baseline 30% → target ≥ 60%).
The pipeline ships Layer 1 (a domain-neutral regex/heuristic classifier) and
Layer 3 (a domain-neutral LLM decomposer). This migration seeds the runtime
knobs into ``system_config`` so bot owners flip behaviour without a redeploy.

Admin override 2026-05-12: ``decomposer.model`` defaults to ``gpt-4.1-mini``
(Haiku banned per user direction; decomposer is a task model, not the answer
LLM). Bot owner overrides per-tenant by updating the matching row.

Domain-neutral: the conjunction token list seeded here is purely linguistic.
DO NOT extend with domain words ("sản phẩm", "Điều", "Khoản") — those leak
domain assumptions into the platform classifier.

3-source sync:
- ``src/ragbot/shared/constants.py`` carries the default constants
  (``DEFAULT_QUERY_COMPLEXITY_*`` and ``DEFAULT_DECOMPOSER_*``).
- ``src/ragbot/shared/bootstrap_config.py::_ALLOWED_KEYS`` whitelist
  honours the same keys (TTL-cached read at hot path).
- This migration seeds the matching ``system_config`` rows.

Idempotent: re-running on an already-seeded DB is a no-op (ON CONFLICT
DO NOTHING preserves any operator overrides).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "query_complexity.weight_comma",
        "0.5",
        "float",
        (
            "Adaptive Router L1 — weight applied per extra comma "
            "(first comma is free; subsequent commas suggest a list)."
        ),
    ),
    (
        "query_complexity.weight_conjunction",
        "0.4",
        "float",
        (
            "Adaptive Router L1 — weight per conjunction token (from "
            "query_complexity.conjunctions). Padded space-match avoids "
            "substring false positives."
        ),
    ),
    (
        "query_complexity.weight_numbers",
        "0.3",
        "float",
        (
            "Adaptive Router L1 — weight per integer token "
            "(\\b\\d+\\b). Multi-entity hint (article numbers, "
            "prices, quantities)."
        ),
    ),
    (
        "query_complexity.weight_question",
        "0.6",
        "float",
        (
            "Adaptive Router L1 — weight per extra '?' (first '?' is "
            "free; subsequent '?'s mark explicit multi-part queries)."
        ),
    ),
    (
        "query_complexity.length_normalizer",
        "20",
        "float",
        (
            "Adaptive Router L1 — divisor on the word count length "
            "bonus. Larger normaliser shrinks length's contribution."
        ),
    ),
    (
        "query_complexity.complexity_threshold",
        "1.2",
        "float",
        (
            "Adaptive Router L1 — score threshold for 'complex' label. "
            "Score >= threshold → Layer 3 decomposer fires; else "
            "legacy router decision applies."
        ),
    ),
    (
        "query_complexity.conjunctions",
        '["và","hoặc","and","or","&","+","cùng","with"]',
        "json",
        (
            "Adaptive Router L1 — linguistic conjunction tokens. "
            "DOMAIN-NEUTRAL: only add words that are conjunctions in "
            "the user's natural language; never add brand or domain "
            "vocabulary."
        ),
    ),
    (
        "decomposer.enabled",
        "true",
        "bool",
        (
            "Adaptive Router L3 — master toggle. Default true. When "
            "false, the L3 decomposer node returns the original query "
            "unchanged so the retrieve path stays functional."
        ),
    ),
    (
        "decomposer.model",
        '"gpt-4.1-mini"',
        "string",
        (
            "Adaptive Router L3 — LLM model used for decomposition. "
            "Admin override 2026-05-12: 'gpt-4.1-mini' (Haiku banned "
            "per user direction; decomposer is a task model, not the "
            "answer LLM)."
        ),
    ),
    (
        "decomposer.max_tokens",
        "300",
        "int",
        (
            "Adaptive Router L3 — max completion tokens for the "
            "decomposer call. 300 fits ~8 sub-questions in JSON."
        ),
    ),
    (
        "decomposer.max_sub_queries",
        "8",
        "int",
        (
            "Adaptive Router L3 — hard cap on emitted sub-queries. "
            "Caps fanout cost; over-split fallback still safer than "
            "under-split."
        ),
    ),
)


def upgrade() -> None:
    for key, value, value_type, description in _SEEDS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, (:value)::jsonb, :value_type, :description)
                ON CONFLICT (key) DO NOTHING
                """
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    for key, _value, _value_type, _description in _SEEDS:
        op.execute(
            text("DELETE FROM system_config WHERE key = :key").bindparams(key=key)
        )
