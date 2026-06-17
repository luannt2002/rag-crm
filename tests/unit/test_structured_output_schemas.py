"""Tests guarding ``additionalProperties: false`` on structured-output schemas.

OpenAI strict ``json_schema`` mode requires every object node in the schema
to declare ``additionalProperties: false`` — without it the provider rejects
the call with::

    litellm.BadRequestError: Invalid schema for response_format
        'GradeOutput': 'additionalProperties' is required to be supplied
        and to be false.

The bug previously surfaced silently: ``model_invocations`` rows recorded
``status='success'`` with ``finish_reason='error'`` and zero tokens because
the orchestration layer swallowed the exception. These tests pin the
contract at the schema layer (Pydantic ``ConfigDict(extra='forbid')``) and
at the helper layer (``_force_additional_properties_false`` walk).

Discovered during  token-instrumentation audit (2026-04-28).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from ragbot.application.dto.llm_schemas import (
    DecomposeOutput,
    GradeOutput,
    ReflectOutput,
)
from ragbot.application.services.structured_output_helper import (
    _force_additional_properties_false,
    _force_required_all_properties,
    _harden_strict_json_schema,
    call_with_schema,
)


def _walk_object_nodes(node: Any) -> list[dict]:
    """Yield every ``type=='object'`` dict in a JSON-schema document."""
    found: list[dict] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for v in node.values():
            found.extend(_walk_object_nodes(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_object_nodes(item))
    return found


# --- Schema-level guards -----------------------------------------------------


def test_grade_output_has_additional_properties_false() -> None:
    schema = GradeOutput.model_json_schema()
    assert schema.get("additionalProperties") is False, (
        "GradeOutput must emit additionalProperties:false for OpenAI strict mode"
    )


def test_reflect_output_has_additional_properties_false() -> None:
    schema = ReflectOutput.model_json_schema()
    assert schema.get("additionalProperties") is False, (
        "ReflectOutput must emit additionalProperties:false for OpenAI strict mode"
    )


def test_decompose_output_has_additional_properties_false() -> None:
    schema = DecomposeOutput.model_json_schema()
    assert schema.get("additionalProperties") is False, (
        "DecomposeOutput must emit additionalProperties:false for OpenAI strict mode"
    )


def test_extra_field_rejected_by_grade_output() -> None:
    """``extra='forbid'`` must reject unknown fields at parse time too."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GradeOutput.model_validate({"grade": "yes", "reason": "x", "rogue": 1})


def test_extra_field_rejected_by_reflect_output() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReflectOutput.model_validate({"action": "keep", "rogue": True})


def test_extra_field_rejected_by_decompose_output() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DecomposeOutput.model_validate({"sub_queries": [], "rogue": "x"})


# --- Helper-level guards (defence in depth for nested objects) ---------------


def test_force_additional_properties_walks_nested_objects() -> None:
    """Helper must stamp ``additionalProperties: false`` on every nested
    ``type=='object'`` node, not just the root."""
    nested = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "inner": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                    }
                },
            },
            "items_of_objects": {
                "type": "array",
                "items": {"type": "object", "properties": {"y": {"type": "integer"}}},
            },
        },
    }
    out = _force_additional_properties_false(nested)
    nodes = _walk_object_nodes(out)
    # Root + outer + inner + items-of-objects = 4 object nodes.
    assert len(nodes) == 4
    for n in nodes:
        assert n.get("additionalProperties") is False


def test_force_additional_properties_preserves_explicit_true() -> None:
    """Explicit ``additionalProperties: true`` must NOT be overwritten —
    only fill in when missing. Caller's intent wins."""
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": True,
    }
    out = _force_additional_properties_false(schema)
    assert out["additionalProperties"] is True


def test_helper_schemas_serialize_to_json_safely() -> None:
    """Each schema must round-trip through json.dumps after the helper —
    OpenAI's HTTP layer requires a JSON-serialisable payload."""
    for cls in (GradeOutput, ReflectOutput, DecomposeOutput):
        forced = _harden_strict_json_schema(cls.model_json_schema())
        # Must not raise.
        json.dumps(forced)


def test_force_required_lists_every_property() -> None:
    """OpenAI strict mode rejects schemas where ``required`` omits a key.
    Helper must enumerate every property even when Pydantic marks it
    optional via a default value."""
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {"type": "boolean"},
        },
        "required": ["a"],
    }
    out = _force_required_all_properties(schema)
    assert sorted(out["required"]) == ["a", "b", "c"]


def test_grade_output_required_includes_all_keys_after_harden() -> None:
    """Combined harden walk must produce a ``required`` list covering both
    ``grade`` and ``reason`` (the latter has a Pydantic default but OpenAI
    strict mode demands it appear in ``required``)."""
    schema = _harden_strict_json_schema(GradeOutput.model_json_schema())
    assert sorted(schema["required"]) == ["grade", "reason"]
    assert schema["additionalProperties"] is False


def test_reflect_output_required_includes_all_keys_after_harden() -> None:
    schema = _harden_strict_json_schema(ReflectOutput.model_json_schema())
    assert sorted(schema["required"]) == ["action", "reason"]
    assert schema["additionalProperties"] is False


def test_decompose_output_required_includes_all_keys_after_harden() -> None:
    schema = _harden_strict_json_schema(DecomposeOutput.model_json_schema())
    assert schema["required"] == ["sub_queries"]
    assert schema["additionalProperties"] is False


# --- Real OpenAI smoke (skipped without API key) -----------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — real-API smoke skipped",
)
async def test_call_with_schema_real_openai() -> None:
    """End-to-end: ensure a real OpenAI call accepts the strict schema.

    Skipped automatically when ``OPENAI_API_KEY`` is unset so CI without
    secrets stays green. Uses ``gpt-4o-mini`` for cheap latency.
    """
    import litellm  # noqa: PLC0415 — only imported on the API path

    out = await call_with_schema(
        litellm_module=litellm,
        litellm_name="openai/gpt-4o-mini",
        provider_code="openai",
        messages=[
            {
                "role": "user",
                "content": (
                    "Grade this answer for the question 'what is 2+2': "
                    "'The answer is 4.' Reply with grade=yes if correct."
                ),
            }
        ],
        schema=GradeOutput,
        timeout=30.0,
    )
    assert isinstance(out, GradeOutput)
    assert out.grade in {"yes", "no", "partial"}
