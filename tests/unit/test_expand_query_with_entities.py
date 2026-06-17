"""T3 entity-grounded multi-query expansion — unit tests.

Pins:
- When extractor returns N entities + grounding ON, output =
  ``[original, ...paraphrases, ...entities]`` (capped at max_variants).
- When extractor is None, behaviour identical to ``expand_query()``.
- When ``entity_grounding_enabled=False``, behaviour identical to
  ``expand_query()`` even if extractor would have returned entities.
- Empty / whitespace query → ``[]`` (matches ``expand_query`` contract).
- Extractor exception → graceful fallback to paraphrase variants only.
- Domain-neutral fixtures only (no industry literals).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.multi_query_expansion import (
    expand_query,
    expand_query_with_entities,
)
from ragbot.shared.constants import (
    DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class _FakeExtractor:
    """Deterministic extractor used to pin variant ordering + dedup logic."""

    def __init__(self, entities: list[str], *, raises: bool = False) -> None:
        self._entities = entities
        self._raises = raises

    @staticmethod
    def get_provider_name() -> str:
        return "fake"

    async def extract(self, query: str, *, language: str) -> list[str]:
        if self._raises:
            raise RuntimeError("simulated extractor failure")
        return list(self._entities)


def _llm_returning(paraphrases: list[str]) -> AsyncMock:
    """Build an AsyncMock LLM that returns a JSON array of paraphrases."""
    return AsyncMock(return_value={"text": json.dumps(paraphrases)})


# --------------------------------------------------------------------------- #
# Backward-compat — extractor=None or grounding OFF                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_extractor_none_matches_plain_expand_query() -> None:
    """No extractor → byte-identical to expand_query()."""
    paraphrases = ["paraphrase 1", "paraphrase 2"]
    llm_a = _llm_returning(paraphrases)
    llm_b = _llm_returning(paraphrases)
    base = "user query"
    out_with = await expand_query_with_entities(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_a,
        entity_extractor=None,
        language="vi",
        entity_grounding_enabled=True,  # even with True, no extractor → bypass
    )
    out_plain = await expand_query(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_b,
    )
    assert out_with == out_plain
    assert out_with[0] == base  # variant-0 safety net intact


@pytest.mark.asyncio
async def test_grounding_disabled_matches_plain_expand_query() -> None:
    """grounding=False → bypass entity branch even with a real extractor."""
    extractor = _FakeExtractor(["entity_one", "entity_two"])
    llm_a = _llm_returning(["par 1", "par 2"])
    llm_b = _llm_returning(["par 1", "par 2"])
    base = "user query"
    out_with = await expand_query_with_entities(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_a,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=False,
    )
    out_plain = await expand_query(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_b,
    )
    assert out_with == out_plain


# --------------------------------------------------------------------------- #
# Happy path — entities appended in expected order                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_entities_appended_after_paraphrases() -> None:
    """Output shape:
       [original, paraphrase_1, paraphrase_2, entity_1, entity_2]"""
    extractor = _FakeExtractor(["entity_alpha", "entity_beta"])
    llm = _llm_returning(["paraphrase_one", "paraphrase_two"])
    base = "verbatim query string"
    out = await expand_query_with_entities(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
        max_entities=DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
        max_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    )
    assert out[0] == base, "variant-0 safety net broken"
    # Paraphrases preserved in order from the LLM mock.
    assert "paraphrase_one" in out
    assert "paraphrase_two" in out
    # Entities appended after paraphrases.
    idx_entity = out.index("entity_alpha")
    idx_paraphrase = out.index("paraphrase_one")
    assert idx_entity > idx_paraphrase, (
        "entities must come AFTER paraphrases in the merged variant list"
    )
    assert "entity_beta" in out


@pytest.mark.asyncio
async def test_max_entities_caps_entity_branch() -> None:
    """max_entities=1 must drop the second entity even when extractor
    returns 3 — paraphrases keep their slots."""
    extractor = _FakeExtractor(["e_one", "e_two", "e_three"])
    llm = _llm_returning(["p_one", "p_two"])
    out = await expand_query_with_entities(
        "the user query",
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
        max_entities=1,
        max_variants=DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    )
    assert "e_one" in out
    assert "e_two" not in out
    assert "e_three" not in out


@pytest.mark.asyncio
async def test_global_max_variants_caps_combined_list() -> None:
    """max_variants is the hard ceiling — entities cannot overshoot it."""
    extractor = _FakeExtractor(["e_one", "e_two", "e_three"])
    llm = _llm_returning(["p_one", "p_two"])
    out = await expand_query_with_entities(
        "the user query",
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
        max_entities=10,  # per-stage cap is permissive
        max_variants=4,  # global ceiling
    )
    assert len(out) <= 4
    # Original query must still be variant-0 even under the cap.
    assert out[0] == "the user query"


@pytest.mark.asyncio
async def test_entities_dedup_against_paraphrases_case_fold() -> None:
    """An entity that already appears in a paraphrase (case-folded) must
    not be re-emitted — keeps RRF input clean."""
    extractor = _FakeExtractor(["Brand_X", "second_entity"])
    # First paraphrase IS the entity (different case).
    llm = _llm_returning(["BRAND_X", "another paraphrase"])
    out = await expand_query_with_entities(
        "user input",
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
    )
    # Brand_X should appear only once (the first occurrence wins).
    lowered = [v.casefold() for v in out]
    assert lowered.count("brand_x") == 1
    assert "second_entity" in out


# --------------------------------------------------------------------------- #
# Empty / whitespace query                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_empty_query_returns_empty_list() -> None:
    extractor = _FakeExtractor(["e_one"])
    llm = _llm_returning(["p_one"])
    for q in ("", "   ", "\t\n"):
        out = await expand_query_with_entities(
            q,
            n_variants=3,
            model_id="m",
            timeout_s=5,
            llm_complete_fn=llm,
            entity_extractor=extractor,
            language="vi",
            entity_grounding_enabled=True,
        )
        assert out == [], f"empty query path broken for q={q!r}"


# --------------------------------------------------------------------------- #
# Extractor exception → graceful fallback                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_extractor_exception_falls_back_to_paraphrase() -> None:
    """Extractor that raises must NOT bubble — we keep the paraphrase
    variants and log a warning."""
    extractor = _FakeExtractor([], raises=True)
    llm = _llm_returning(["p_one", "p_two"])
    base = "user input"
    out = await expand_query_with_entities(
        base,
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
    )
    # Original + paraphrases preserved; entities skipped.
    assert out[0] == base
    assert "p_one" in out
    assert "p_two" in out


@pytest.mark.asyncio
async def test_empty_entity_list_matches_plain_expand() -> None:
    """Extractor returning [] must yield same result as no-extractor path."""
    extractor = _FakeExtractor([])
    llm_a = _llm_returning(["p_one", "p_two"])
    llm_b = _llm_returning(["p_one", "p_two"])
    out_a = await expand_query_with_entities(
        "user input",
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_a,
        entity_extractor=extractor,
        language="vi",
        entity_grounding_enabled=True,
    )
    out_b = await expand_query(
        "user input",
        n_variants=3,
        model_id="m",
        timeout_s=5,
        llm_complete_fn=llm_b,
    )
    assert out_a == out_b


# --------------------------------------------------------------------------- #
# Domain-neutral self-test                                                    #
# --------------------------------------------------------------------------- #


def test_fixtures_are_domain_neutral() -> None:
    """Self-test — fixtures used above must not contain vertical literals."""
    fixtures = " ".join(
        [
            "user query",
            "verbatim query string",
            "the user query",
            "user input",
            "paraphrase one",
            "paraphrase two",
            "Brand_X",
            "BRAND_X",
            "entity_alpha",
            "entity_beta",
            "second_entity",
        ]
    ).lower()
    banned = ("spa", "massage", "chăm sóc da", "triệt lông", "gội đầu")
    for term in banned:
        assert term not in fixtures, f"vertical literal {term!r} leaked"
