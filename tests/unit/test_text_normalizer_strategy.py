"""Text Normalizer Strategy registry — unit tests."""

from __future__ import annotations

import pytest

from ragbot.application.ports.text_normalizer_port import TextNormalizerPort
try:
    from ragbot.infrastructure.text_normalizer.null_normalizer import NullNormalizer
    from ragbot.infrastructure.text_normalizer.registry import (
        build_normalizer,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "text_normalizer subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


@pytest.mark.asyncio
async def test_null_normalizer_passthrough() -> None:
    n = NullNormalizer()
    assert await n.normalize("xin chao") == "xin chao"
    assert await n.normalize("") == ""
    # Tone-restored text must not be altered either.
    assert await n.normalize("xin chào bạn") == "xin chào bạn"
    assert n.get_provider_name() == "null"
    assert isinstance(n, TextNormalizerPort)


def test_registry_default_is_null() -> None:
    for prov in (None, "", "does_not_exist_xyz"):
        instance = build_normalizer(prov)
        assert isinstance(instance, NullNormalizer)
    providers = list_providers()
    assert "null" in providers
    assert "bartpho" in providers
    assert providers == sorted(providers)


def test_bartpho_stub_falls_back_to_null() -> None:
    # Stub raises NotImplementedError → registry catches → NullNormalizer.
    instance = build_normalizer("bartpho")
    assert isinstance(instance, NullNormalizer)


@pytest.mark.asyncio
async def test_registry_returned_normalizer_callable() -> None:
    n = build_normalizer("null")
    out = await n.normalize("hello world")
    assert out == "hello world"
