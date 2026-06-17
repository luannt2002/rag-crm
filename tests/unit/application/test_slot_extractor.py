"""Unit tests for SlotExtractor — LLM JSON mode + Pydantic validate."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from ragbot.application.services.slot_extractor import SlotExtractor


SLOT_SCHEMA_BOOKING = {
    "booking": {
        "required": ["service", "name", "phone", "datetime"],
        "optional": ["note"],
    },
}


# --------------------------------------------------------------------------- #
# Helpers — patch call_with_schema in the slot_extractor module                #
# --------------------------------------------------------------------------- #
def _patch_call_with_schema(return_value):
    """Patch the imported call_with_schema in slot_extractor module."""
    return patch(
        "ragbot.application.services.slot_extractor.call_with_schema",
        AsyncMock(return_value=return_value),
    )


def _make_cfg(model_alias: str = "haiku") -> SimpleNamespace:
    return SimpleNamespace(get=AsyncMock(return_value=model_alias))


def _stub_litellm() -> SimpleNamespace:
    """Minimal litellm stub — not used directly since we patch call_with_schema."""
    return SimpleNamespace()


# --------------------------------------------------------------------------- #
# Empty inputs                                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_message_returns_empty() -> None:
    ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
    out = await ext.extract(user_message="", slot_schema=SLOT_SCHEMA_BOOKING)
    assert out == {}


@pytest.mark.asyncio
async def test_empty_schema_returns_empty() -> None:
    ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
    out = await ext.extract(user_message="anything", slot_schema={})
    assert out == {}


# --------------------------------------------------------------------------- #
# Slot extraction success                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_extract_full_slots() -> None:
    """LLM returns Pydantic instance with all slots populated."""
    # Build expected return: a model instance simulating LLM JSON validate
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(
        service="gội đầu", name="Luân", phone="0353988280",
        datetime="sáng thứ 7", note=None,
    )

    with _patch_call_with_schema(return_instance):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="Luân 0353988280 đặt gội đầu sáng thứ 7",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )

    assert out["service"] == "gội đầu"
    assert out["name"] == "Luân"
    assert out["phone"] == "0353988280"
    assert out["datetime"] == "sáng thứ 7"
    assert "note" not in out  # None filtered


@pytest.mark.asyncio
async def test_extract_partial_slots() -> None:
    """User provides only some slots → others None → scrubbed."""
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(
        service="gội đầu", name=None, phone=None,
        datetime="sáng mai 9h", note=None,
    )

    with _patch_call_with_schema(return_instance):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="đặt gội đầu sáng mai 9h",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )

    assert out == {"service": "gội đầu", "datetime": "sáng mai 9h"}


# --------------------------------------------------------------------------- #
# Schema selection                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_intent_picks_correct_sub_schema() -> None:
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(service="X")

    with _patch_call_with_schema(return_instance):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="đặt X",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )
    assert out == {"service": "X"}


@pytest.mark.asyncio
async def test_missing_intent_falls_back_to_first_key() -> None:
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(service="Y")

    with _patch_call_with_schema(return_instance):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="đặt Y",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="unknown_intent",
        )
    assert out == {"service": "Y"}


# --------------------------------------------------------------------------- #
# Failure handling                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_call_returns_none_yields_empty() -> None:
    with _patch_call_with_schema(None):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="anything",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )
    assert out == {}


@pytest.mark.asyncio
async def test_llm_exception_returns_empty() -> None:
    with patch(
        "ragbot.application.services.slot_extractor.call_with_schema",
        AsyncMock(side_effect=RuntimeError("LLM down")),
    ):
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=_make_cfg())
        out = await ext.extract(
            user_message="anything",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )
    assert out == {}


# --------------------------------------------------------------------------- #
# Model resolution from system_config                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_model_resolved_from_system_config_haiku() -> None:
    """Alias 'haiku' → openai/gpt-4.1-mini (alias name legacy; catalog locked to gpt-4.1)."""
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(service="X")
    cfg = _make_cfg(model_alias="haiku")

    with patch(
        "ragbot.application.services.slot_extractor.call_with_schema",
        AsyncMock(return_value=return_instance),
    ) as mock_call:
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=cfg)
        await ext.extract(
            user_message="đặt X",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )

    args, kwargs = mock_call.call_args
    assert kwargs["litellm_name"] == "openai/gpt-4.1-mini"
    assert kwargs["provider_code"] == "openai"


@pytest.mark.asyncio
async def test_model_resolved_from_system_config_sonnet() -> None:
    """Alias 'sonnet' → openai/gpt-4.1-mini (alias name legacy; catalog locked to gpt-4.1)."""
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(service="X")
    cfg = _make_cfg(model_alias="sonnet")

    with patch(
        "ragbot.application.services.slot_extractor.call_with_schema",
        AsyncMock(return_value=return_instance),
    ) as mock_call:
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=cfg)
        await ext.extract(
            user_message="đặt X",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )

    args, kwargs = mock_call.call_args
    assert kwargs["litellm_name"] == "openai/gpt-4.1-mini"


@pytest.mark.asyncio
async def test_config_failure_degrades_to_default_haiku() -> None:
    """Config service exception → default model wire (openai/gpt-4.1-mini)."""
    from ragbot.application.services.slot_extractor import SlotExtractor as SE
    SchemaModel = SE._build_pydantic_model(
        "booking", ["service", "name", "phone", "datetime", "note"],
    )
    return_instance = SchemaModel(service="X")
    cfg = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("redis down")))

    with patch(
        "ragbot.application.services.slot_extractor.call_with_schema",
        AsyncMock(return_value=return_instance),
    ) as mock_call:
        ext = SlotExtractor(litellm_module=_stub_litellm(), config_service=cfg)
        await ext.extract(
            user_message="đặt X",
            slot_schema=SLOT_SCHEMA_BOOKING,
            intent="booking",
        )

    args, kwargs = mock_call.call_args
    assert kwargs["litellm_name"] == "openai/gpt-4.1-mini"
