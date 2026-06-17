"""Unit tests for ``infrastructure.text_normalizer.bartpho_accent_normalizer``.

Bartpho is a STUB: visible to the registry as an opt-in option, but the
constructor raises until ``transformers`` + the bartpho-syllable weights are
provisioned. These tests pin the stub contract WITHOUT loading any model.
"""

from __future__ import annotations

import pytest

try:
    from ragbot.infrastructure.text_normalizer.bartpho_accent_normalizer import (
        BartphoAccentNormalizer,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "bartpho_accent_normalizer is dead-code (body commented out)",
        allow_module_level=True,
    )


def test_constructor_raises_with_install_hint() -> None:
    with pytest.raises(NotImplementedError) as exc:
        BartphoAccentNormalizer()

    msg = str(exc.value).lower()
    # Hint must mention the missing deps so ops have an actionable trail.
    assert "transformers" in msg
    assert "bartpho" in msg


def test_constructor_swallows_extra_kwargs_then_raises() -> None:
    # The real ML wiring will accept a model-name kwarg; the stub takes
    # ``**_`` and still raises uniformly. Pin so callers can pass whatever
    # they would in production without breaking the stub contract.
    with pytest.raises(NotImplementedError):
        BartphoAccentNormalizer(model="vinai/bartpho-syllable", device="cpu")


def test_get_provider_name_is_stable_class_method() -> None:
    # Accessible without instantiation (instantiation raises).
    assert BartphoAccentNormalizer.get_provider_name() == "bartpho"


def test_class_exposes_async_normalize_method() -> None:
    # Confirms the Strategy contract surface even though we cannot construct.
    assert hasattr(BartphoAccentNormalizer, "normalize")
    import inspect
    assert inspect.iscoroutinefunction(BartphoAccentNormalizer.normalize)
