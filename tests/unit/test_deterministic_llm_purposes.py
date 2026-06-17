"""Pin the deterministic-temperature purpose policy.

Root cause measured 2026-06-09: query-transform + classification steps inherited
the ~0.3 model temperature, so the SAME multi-fact question intermittently
refused vs answered (spa Q7) — the reformulated sub-query shifted which chunks
reached generation. Forcing these purposes to temperature 0 makes retrieval (and
the final answer) reproducible. HyDE is deliberately excluded (light variation
aids recall); generation has its own explicit temperature path.
"""
from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_DETERMINISTIC_LLM_PURPOSES,
    DEFAULT_DETERMINISTIC_TEMPERATURE,
)


def test_deterministic_temperature_is_zero() -> None:
    assert DEFAULT_DETERMINISTIC_TEMPERATURE == 0.0


def test_retrieval_shaping_transforms_are_deterministic() -> None:
    # The steps that change WHAT gets retrieved must not be stochastic, or
    # retrieval — hence the answer — becomes non-reproducible run to run.
    for purpose in ("decompose", "rewrite", "multi_query", "condense", "routing",
                    "understand_query"):
        assert purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES, purpose


def test_classification_decisions_are_deterministic() -> None:
    # A grader/grounding judge flipping its verdict on temperature is a bug.
    for purpose in ("intent", "grade", "grading", "grounding"):
        assert purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES, purpose


def test_hyde_is_excluded() -> None:
    # HyDE benefits from variation — it must NOT be forced deterministic here.
    assert "hyde" not in DEFAULT_DETERMINISTIC_LLM_PURPOSES


def test_generation_not_in_set_uses_own_path() -> None:
    # generation has its own per-bot generation_temperature resolution.
    assert "generation" not in DEFAULT_DETERMINISTIC_LLM_PURPOSES
