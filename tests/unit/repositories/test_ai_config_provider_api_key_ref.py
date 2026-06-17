"""Pin test for ``_to_provider`` — wire ``api_key_ref`` into ``credentials_vault_path``.

Before 2026-05-21 fix, ``ai_config_repository._to_provider`` hardcoded
``credentials_vault_path=None`` (commit 93b1258, 2026-05-12). The
``EnvSecretsAdapter.resolve()`` resolver only fires when the path
starts with ``"env:"``, so the hardcoded ``None`` made the secret
return ``""`` (empty) for every provider — at which point LiteLLM
silently fell back to ``OPENAI_API_KEY`` env. Native OpenAI /
Anthropic rows happened to work because the env var name matched the
LiteLLM default; any non-default provider (Innocom LM Studio with
``LMSTUDIO_API_KEY``) saw its key swapped for the OpenAI key and the
remote endpoint rejected it.

This test grep-asserts the wire so a future refactor that revisits
``credentials_vault_path=None`` re-introduces the silent swap.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ragbot.infrastructure.repositories import ai_config_repository


_REPO_FILE = Path(ai_config_repository.__file__)


def _make_row(api_key_ref: str | None) -> SimpleNamespace:
    """Build a minimal ``AIProviderModel`` stand-in for ``_to_provider``."""
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        name="test",
        code="test",
        type="llm",
        base_url="https://example.invalid",
        auth_type="api_key",
        enabled=True,
        metadata_json={},
        api_key_ref=api_key_ref,
        requires_prefix=True,
    )


def test_api_key_ref_wires_to_credentials_vault_path_env_prefix() -> None:
    """``api_key_ref='LMSTUDIO_API_KEY'`` must surface as
    ``credentials_vault_path='env:LMSTUDIO_API_KEY'`` so the
    ``EnvSecretsAdapter`` can resolve it via ``os.getenv``.
    """
    row = _make_row("LMSTUDIO_API_KEY")
    provider = ai_config_repository._to_provider(row)
    assert provider.credentials_vault_path == "env:LMSTUDIO_API_KEY", (
        f"expected 'env:LMSTUDIO_API_KEY', got {provider.credentials_vault_path!r}. "
        f"Regression of the 93b1258 (2026-05-12) hardcoded ``None`` bug."
    )


def test_api_key_ref_none_leaves_credentials_vault_path_none() -> None:
    """A provider row with no ``api_key_ref`` (DB column NULL) must still
    map cleanly to ``credentials_vault_path=None`` — the resolver then
    returns the empty string + LiteLLM falls back to its default env,
    preserving the historical behaviour for rows that intentionally
    have no secret slot.
    """
    row = _make_row(None)
    provider = ai_config_repository._to_provider(row)
    assert provider.credentials_vault_path is None


def test_api_key_ref_empty_string_treated_as_none() -> None:
    """Defence in depth — operator typo (``api_key_ref=''``) must not
    produce ``'env:'`` (which the resolver would try to ``os.getenv('')``).
    """
    row = _make_row("   ")
    provider = ai_config_repository._to_provider(row)
    assert provider.credentials_vault_path is None


def test_to_provider_does_not_re_introduce_hardcoded_none() -> None:
    """Source-level pin: the literal ``credentials_vault_path=None`` MUST
    NOT reappear without the ``env:{api_key_ref}`` ternary that the fix
    introduced. Catches a copy-paste regression before it lands on main.
    """
    body = _REPO_FILE.read_text(encoding="utf-8")
    # Allow the literal in the comment block (documenting the bug),
    # but not as the live assignment.
    bad = "credentials_vault_path=None,\n"
    assert bad not in body, (
        "Re-introduction detected: ``credentials_vault_path=None,`` literal "
        "assignment found in ai_config_repository.py. This is the exact "
        "regression that broke Innocom routing on 2026-05-12. Wire "
        "``api_key_ref`` instead, or delete the field if the schema changed."
    )
