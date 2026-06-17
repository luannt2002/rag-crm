"""Unit tests for the Pydantic JSON-schema build memo in
``structured_output_helper`` — verifies the ``lru_cache`` wrapping is
transparent (same return value), counts the underlying harden walk so a
second call on the same class is a cache hit, and confirms distinct
Pydantic classes occupy independent cache slots.
"""

from __future__ import annotations

from unittest.mock import patch

from pydantic import BaseModel

from ragbot.application.services import structured_output_helper as helper
from ragbot.shared.constants import DEFAULT_STRUCTURED_OUTPUT_SCHEMA_CACHE_SIZE


class _SchemaA(BaseModel):
    field_a: str
    count: int


class _SchemaB(BaseModel):
    label: str
    nested: list[str]


def _clear_cache() -> None:
    """Reset the module-level memo so each test starts from a cold cache."""
    helper._cached_hardened_schema.cache_clear()


def test_first_call_populates_cache_then_second_call_is_a_hit() -> None:
    _clear_cache()

    real_harden = helper._harden_strict_json_schema
    with patch.object(
        helper,
        "_harden_strict_json_schema",
        side_effect=real_harden,
    ) as spy:
        first = helper._cached_hardened_schema(_SchemaA)
        second = helper._cached_hardened_schema(_SchemaA)

    # Identity: lru_cache must hand back the exact same dict object so
    # downstream callers are not paying a deepcopy on every hit.
    assert first is second
    # Underlying harden pass ran exactly once across the two calls.
    assert spy.call_count == 1

    info = helper._cached_hardened_schema.cache_info()
    assert info.hits == 1
    assert info.misses == 1


def test_distinct_classes_get_independent_cache_slots() -> None:
    _clear_cache()

    schema_a = helper._cached_hardened_schema(_SchemaA)
    schema_b = helper._cached_hardened_schema(_SchemaB)

    # Distinct classes must produce distinct hardened schemas — a regression
    # here would mean the cache key collapsed across types.
    assert schema_a is not schema_b
    assert schema_a["properties"].keys() == {"field_a", "count"}
    assert schema_b["properties"].keys() == {"label", "nested"}

    info = helper._cached_hardened_schema.cache_info()
    assert info.misses == 2
    assert info.hits == 0
    assert info.currsize == 2


def test_cache_size_matches_constant_and_info_is_reachable() -> None:
    _clear_cache()
    # Touch the cache once so cache_info reports a populated state.
    helper._cached_hardened_schema(_SchemaA)

    info = helper._cached_hardened_schema.cache_info()
    # Maxsize is wired to the constant, not a hardcoded literal — proves
    # the zero-hardcode requirement is respected at runtime.
    assert info.maxsize == DEFAULT_STRUCTURED_OUTPUT_SCHEMA_CACHE_SIZE
    assert info.currsize == 1


def test_cached_schema_is_hardened_with_strict_mode_flags() -> None:
    _clear_cache()

    schema = helper._cached_hardened_schema(_SchemaA)

    # Hardening contract: every object node carries additionalProperties=false
    # and the required list enumerates every property key.
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"field_a", "count"}
