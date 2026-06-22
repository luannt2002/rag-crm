"""Regression test for M22 — in-memory fallback attribute mismatch.

The ``LanguagePack`` dataclass names most fields ``prompt_<key>`` but the
OOS / sysprompt-rule fields are *not* prefixed (``refuse_message``,
``sysprompt_default_rules``). The resolver's ``_inmemory_fallback`` blindly
applied ``f"prompt_{key}"`` for every non-greeting key, so a missing-DB read
for ``refuse_message`` resolved to ``prompt_refuse_message`` — an attribute
that does not exist — and the bot's configured per-locale refusal text was
silently swallowed (returned ``""``).

These tests pin that the in-memory fallback returns the *configured* text for
every canonical prompt key, comparing against the live ``i18n`` pack field so
no refusal literal is hardcoded in the assertion (per CLAUDE.md: no hardcoded
i18n refusal text in shared code).
"""

from __future__ import annotations

import pytest

from ragbot.application.services.language_pack_service import LanguagePackService
from ragbot.shared import i18n
from ragbot.shared.constants import LANGUAGE_PACK_PROMPT_KEYS


def _expected_field(prompt_key: str) -> str:
    """Resolve the dataclass attribute name for a canonical prompt key.

    Mirrors the real ``LanguagePack`` field naming so the assertion is
    data-driven (reads the live pack) and never hardcodes refusal copy.
    """
    pack = i18n.get_pack("vi")
    # Fields whose dataclass attribute is *not* ``prompt_<key>``.
    if hasattr(pack, prompt_key):
        return str(getattr(pack, prompt_key) or "")
    return str(getattr(pack, f"prompt_{prompt_key}", "") or "")


def test_inmemory_fallback_surfaces_configured_refuse_message() -> None:
    """``refuse_message`` must return the seeded VI text, not ``""``."""
    expected = _expected_field("refuse_message")
    # The in-memory VI pack seeds a non-empty refusal — guards the test.
    assert expected, "VI in-memory pack should seed a refuse_message"

    got = LanguagePackService._inmemory_fallback("vi", "refuse_message")
    assert got == expected


def test_inmemory_fallback_surfaces_sysprompt_default_rules() -> None:
    """``sysprompt_default_rules`` resolves to its configured (possibly empty) value."""
    expected = _expected_field("sysprompt_default_rules")
    got = LanguagePackService._inmemory_fallback("vi", "sysprompt_default_rules")
    assert got == expected


@pytest.mark.parametrize("prompt_key", LANGUAGE_PACK_PROMPT_KEYS)
def test_inmemory_fallback_matches_pack_field_for_every_key(prompt_key: str) -> None:
    """Every canonical key resolves to its real dataclass field value."""
    expected = _expected_field(prompt_key)
    got = LanguagePackService._inmemory_fallback("vi", prompt_key)
    assert got == expected


def test_inmemory_fallback_empty_for_unset_key() -> None:
    """A key with no matching field stays ``""`` (never a hardcoded literal)."""
    assert LanguagePackService._inmemory_fallback("vi", "no_such_key") == ""


@pytest.mark.asyncio
async def test_get_surfaces_refuse_message_when_db_unseeded() -> None:
    """End-to-end: ``get`` over an empty DB serves the in-memory refuse text.

    This is the OosTemplateResolver tier-6 path — DB unseeded / fresh tenant.
    """

    class _EmptyRepo:
        async def get_pack(self, code: str, prompt_key: str) -> str | None:
            return None

        async def list_pack(self, code: str) -> dict[str, str]:
            return {}

    svc = LanguagePackService(repo=_EmptyRepo(), redis_client=None)
    out = await svc.get("vi", "refuse_message")
    assert out == _expected_field("refuse_message")
    assert out, "tier-6 refusal text must not be swallowed to empty"
