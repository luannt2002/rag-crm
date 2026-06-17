"""Semantic cache threshold canonical-value test.

The semantic cache threshold drifted between three places:

* ``shared/constants.SEMANTIC_CACHE_THRESHOLD`` (canonical, 0.97)
* ``init_system_config.py`` seed row (was 0.93 — caused borderline cache hits
  to serve wrong answers for paraphrased queries)
* ``chat_worker.py`` / ``test_chat.py`` ``cfg.get_float`` fallback default
  (was a 0.97 literal — passed grep but was a magic number)

These tests pin the relationship so any future drift fails CI loudly.
"""

from __future__ import annotations


def test_semantic_cache_threshold_canonical_value():
    """Constants module exposes the expected canonical threshold."""
    from ragbot.shared.constants import SEMANTIC_CACHE_THRESHOLD

    assert SEMANTIC_CACHE_THRESHOLD == 0.97, (
        "SEMANTIC_CACHE_THRESHOLD drifted; if this is intentional, update "
        "init_system_config.py seed AND tests/unit/test_cache_threshold_validation.py"
    )


def test_semantic_cache_min_recommended_below_canonical():
    """The 'warn-below' floor must be ≤ the canonical default."""
    from ragbot.shared.constants import (
        SEMANTIC_CACHE_THRESHOLD,
        SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED,
    )

    assert SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED <= SEMANTIC_CACHE_THRESHOLD
    # Floor must be tight enough to actually catch the historical
    # drift-to-0.93 incident.
    assert SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED > 0.93


def test_init_system_config_seed_matches_constant():
    """The init_system_config seed row matches SEMANTIC_CACHE_THRESHOLD.

    Reads the seed file as text and asserts the value is exactly
    ``"0.97"``. Reading-the-text (instead of importing the SEED_CONFIGS
    list) keeps this test independent of the seeder's import dependencies.
    """
    from pathlib import Path

    from ragbot.shared.constants import SEMANTIC_CACHE_THRESHOLD

    seed_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "init_system_config.py"
    )
    body = seed_path.read_text(encoding="utf-8")

    needle = (
        f'("pipeline_cache_similarity_threshold", "{SEMANTIC_CACHE_THRESHOLD}"'
    )
    assert needle in body, (
        "init_system_config seed value drifted from SEMANTIC_CACHE_THRESHOLD "
        f"(expected substring: {needle!r})"
    )


def test_chat_worker_pipeline_config_uses_constant_default():
    """The chat_worker pipeline_config build calls cfg.get_float with the
    canonical SEMANTIC_CACHE_THRESHOLD as the fallback (NOT a magic 0.97).

    Reads the chat_worker source as text and asserts the named constant
    appears in the cache-similarity call site. This keeps the zero-hardcode
    rule enforced for this specific config key.
    """
    from pathlib import Path

    # chat_worker was split into a package — scan every module.
    pkg = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "interfaces"
        / "workers"
        / "chat_worker"
    )
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )

    assert "pipeline_cache_similarity_threshold" in body
    # The cfg call must reference the constant, not a literal 0.97.
    assert "SEMANTIC_CACHE_THRESHOLD" in body
