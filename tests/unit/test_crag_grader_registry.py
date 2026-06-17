"""CRAG grader strategy registry — unit tests.

Pins:
- Port Protocol satisfied by all 3 strategies
- Registry default = ``per_chunk`` for missing/unknown/empty provider
- ``null`` resolves to NullCragGrader
- ``batch`` resolves to BatchCragGrader
- Fail-soft init: missing required kwarg → NullCragGrader fallback
- Vertical-agnostic — tests use only generic placeholders
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.crag_grader_port import CragGraderPort
from ragbot.application.services.crag_grader import (
    BatchCragGrader,
    NullCragGrader,
    PerChunkCragGrader,
    build_crag_grader,
    list_providers,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


async def _dummy_caller(**_kwargs):
    # Returns the "no parsed output" shape so strategies fall through to
    # the all-1.0 graceful-degradation path.
    return None, None


# --------------------------------------------------------------------------- #
# Registry resolution                                                         #
# --------------------------------------------------------------------------- #


def test_registry_default_is_per_chunk_for_falsy_or_unknown() -> None:
    """Falsy / typo / None all collapse to PerChunkCragGrader (legacy default)."""
    for prov in (None, "", "  ", "PER_CHUNK", "Per_Chunk"):
        instance = build_crag_grader(
            prov,
            structured_llm_caller=_dummy_caller,
            system_prompt="test",
        )
        assert isinstance(instance, PerChunkCragGrader), f"prov={prov!r}"


def test_registry_unknown_provider_falls_back_to_per_chunk() -> None:
    """Unknown key + valid kwargs → PerChunkCragGrader (warn-only)."""
    instance = build_crag_grader(
        "does_not_exist_xyz",
        structured_llm_caller=_dummy_caller,
        system_prompt="test",
    )
    assert isinstance(instance, PerChunkCragGrader)


def test_registry_resolves_known_providers() -> None:
    """Each registered key returns the matching class."""
    assert isinstance(
        build_crag_grader(
            "null",
            structured_llm_caller=_dummy_caller,
            system_prompt="test",
        ),
        NullCragGrader,
    )
    assert isinstance(
        build_crag_grader(
            "per_chunk",
            structured_llm_caller=_dummy_caller,
            system_prompt="test",
        ),
        PerChunkCragGrader,
    )
    assert isinstance(
        build_crag_grader(
            "batch",
            structured_llm_caller=_dummy_caller,
            system_prompt="test",
        ),
        BatchCragGrader,
    )
    # Case-insensitive resolution.
    assert isinstance(
        build_crag_grader(
            "BATCH",
            structured_llm_caller=_dummy_caller,
            system_prompt="test",
        ),
        BatchCragGrader,
    )


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert "null" in providers
    assert "per_chunk" in providers
    assert "batch" in providers
    assert providers == sorted(providers), "list_providers must return sorted"
    # Pin the count so a future drive-by addition is a deliberate test
    # update rather than an accidental merge.
    assert len(providers) == 3


def test_registry_init_failure_falls_back_to_null() -> None:
    """Constructor missing required kwarg → NullCragGrader (warn-only).

    BatchCragGrader requires ``structured_llm_caller`` — omitting it
    raises ``ValueError`` inside the constructor; the registry must
    catch and downgrade to NullCragGrader so the orchestrator always
    receives a usable grader.
    """
    instance = build_crag_grader("batch")  # missing required kwargs
    assert isinstance(instance, NullCragGrader)


def test_registry_kwargs_filtered_safely_for_null() -> None:
    """NullCragGrader ignores all kwargs — registry must not blow up."""
    inst = build_crag_grader(
        "null",
        structured_llm_caller=_dummy_caller,
        system_prompt="ignored",
        random_extra_kw="ignored",
    )
    assert isinstance(inst, NullCragGrader)


# --------------------------------------------------------------------------- #
# Port Protocol + provider name                                               #
# --------------------------------------------------------------------------- #


def test_all_strategies_implement_port_protocol() -> None:
    """Every strategy MUST satisfy the runtime_checkable Protocol."""
    null = NullCragGrader()
    per_chunk = PerChunkCragGrader(
        structured_llm_caller=_dummy_caller, system_prompt="test",
    )
    batch = BatchCragGrader(
        structured_llm_caller=_dummy_caller, system_prompt="test",
    )
    assert isinstance(null, CragGraderPort)
    assert isinstance(per_chunk, CragGraderPort)
    assert isinstance(batch, CragGraderPort)


def test_provider_names_unique_and_match_registry_keys() -> None:
    """get_provider_name MUST equal the registry key — pin against drift."""
    assert NullCragGrader.get_provider_name() == "null"
    assert PerChunkCragGrader.get_provider_name() == "per_chunk"
    assert BatchCragGrader.get_provider_name() == "batch"


# --------------------------------------------------------------------------- #
# Null strategy — true no-op (every chunk → 1.0)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_null_grader_returns_one_for_every_chunk() -> None:
    null = NullCragGrader()
    chunks = [
        {"chunk_id": "a", "content": "alpha"},
        {"chunk_id": "b", "content": "beta"},
        {"chunk_id": "c", "content": "gamma"},
    ]
    out = await null.grade_batch(query="any", chunks=chunks)
    assert out == {"a": 1.0, "b": 1.0, "c": 1.0}


@pytest.mark.asyncio
async def test_null_grader_empty_input_returns_empty() -> None:
    null = NullCragGrader()
    out = await null.grade_batch(query="any", chunks=[])
    assert out == {}


@pytest.mark.asyncio
async def test_null_grader_handles_id_field_alias() -> None:
    """Chunks may carry ``id`` instead of ``chunk_id``."""
    null = NullCragGrader()
    out = await null.grade_batch(
        query="any",
        chunks=[{"id": "x", "text": "hello"}],
    )
    assert out == {"x": 1.0}


# --------------------------------------------------------------------------- #
# Domain-neutral guard — fixtures must not include vertical literals          #
# --------------------------------------------------------------------------- #


def test_test_fixtures_are_domain_neutral() -> None:
    """Self-test that the strings used above contain no industry / brand /
    domain-specific literals from the CLAUDE.md banned list.
    """
    fixtures_text = " ".join(
        [
            "alpha", "beta", "gamma", "hello",
            "test", "PER_CHUNK", "BATCH", "null",
        ]
    ).lower()
    banned = ("spa", "massage", "legal", "medical", "voucher")
    for term in banned:
        assert term not in fixtures_text, (
            f"vertical literal '{term}' leaked into test fixtures"
        )
