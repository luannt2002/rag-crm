"""I-2 — get_pack() falls back to ENGLISH (not Vietnamese) for unseeded locales."""

from __future__ import annotations

from ragbot.shared.i18n import get_pack


def test_seeded_locales_return_their_own_pack() -> None:
    assert get_pack("vi").code == "vi"
    assert get_pack("en").code == "en"


def test_unseeded_locale_falls_back_to_english_not_vietnamese() -> None:
    # Khmer / French / Chinese have no in-memory pack → English lingua franca.
    assert get_pack("km").code == "en"
    assert get_pack("fr").code == "en"
    assert get_pack("zh").code == "en"


def test_default_arg_is_the_deployment_default() -> None:
    from ragbot.shared.constants import DEFAULT_LANGUAGE

    assert get_pack().code == DEFAULT_LANGUAGE
