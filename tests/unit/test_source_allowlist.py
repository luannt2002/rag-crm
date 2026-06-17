"""T1-Safety — Source URL Allow-list tests.

Verifies:

1. Strategy registry contract (Null + DomainAllowlist providers).
2. Empty allow-list = allow-all (opt-in default; backward compat).
3. Bare host pattern matches exact + sub-domain, rejects look-alike.
4. URL-prefix pattern matches path-scoped allows.
5. ``re:<pattern>`` regex pattern works, malformed regex degrades safe.
6. Malformed / no source_url with non-empty allow-list rejects with
   correct reason codes.
7. Helper ``_maybe_validate_source_allowlist`` honours the two-knob
   opt-in (feature flag + per-bot list), gracefully degrades on
   bot_repo / config_service failures, and raises
   :class:`SourceNotAllowedError` only on hard reject.
8. Case-insensitive matching (URL case differences don't bypass list).
9. Port runtime-checkable conformance.

Tests follow the CLAUDE.md "real behavioural assertions" rule — no
``assert True`` filler.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.ports.source_validator_port import SourceValidatorPort
from ragbot.application.services.document_service import (
    _maybe_validate_source_allowlist,
)
from ragbot.infrastructure.safety.domain_allowlist_validator import (
    DomainAllowlistValidator,
)
from ragbot.infrastructure.safety.null_source_validator import NullSourceValidator
from ragbot.infrastructure.safety.registry import (
    build_source_validator,
    list_providers,
)
from ragbot.shared.errors import SourceNotAllowedError


# ── Registry contract ─────────────────────────────────────────────────


def test_registry_lists_both_providers() -> None:
    providers = list_providers()
    assert providers == sorted(providers), "registry must return sorted list"
    assert "null" in providers
    assert "domain_allowlist" in providers


def test_registry_default_is_null_for_unknown_provider() -> None:
    for prov in (None, "", "  ", "does_not_exist_xyz"):
        instance = build_source_validator(prov)
        assert isinstance(instance, NullSourceValidator), (
            f"unknown provider {prov!r} must fall back to NullSourceValidator"
        )


def test_registry_domain_allowlist_returns_real_impl() -> None:
    instance = build_source_validator("domain_allowlist")
    assert isinstance(instance, DomainAllowlistValidator)
    assert instance.get_provider_name() == "domain_allowlist"


def test_registry_provider_key_is_case_insensitive() -> None:
    instance = build_source_validator("DOMAIN_ALLOWLIST")
    assert isinstance(instance, DomainAllowlistValidator)


def test_port_runtime_checkable_conformance() -> None:
    null_v = NullSourceValidator()
    real_v = DomainAllowlistValidator()
    assert isinstance(null_v, SourceValidatorPort)
    assert isinstance(real_v, SourceValidatorPort)


# ── Null validator: passthrough ───────────────────────────────────────


def test_null_validator_allows_everything() -> None:
    v = NullSourceValidator()
    ok, reason = v.is_allowed("https://anywhere.example.com/x", [])
    assert ok is True
    assert reason is None
    ok, reason = v.is_allowed("https://malicious.example.com/poison", ["trusted.com"])
    assert ok is True, (
        "NullSourceValidator must ignore the list and allow even non-matching URLs"
    )
    assert reason is None
    assert v.get_provider_name() == "null"


# ── Empty allow-list = allow-all (opt-in default) ─────────────────────


def test_empty_allowlist_allows_all() -> None:
    v = DomainAllowlistValidator()
    for url in (
        "https://example.com/doc",
        "http://random.host/path",
        "ftp://weird-scheme/x",
        "",
    ):
        ok, reason = v.is_allowed(url, [])
        assert ok is True, f"empty allow-list must allow {url!r}"
        assert reason is None


def test_allowlist_with_only_whitespace_entries_is_treated_as_empty() -> None:
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("https://anywhere.com/x", ["   ", "\t", ""])
    assert ok is True
    assert reason is None


# ── Bare-host pattern: exact + sub-domain ─────────────────────────────


def test_bare_host_exact_match_allows() -> None:
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("https://example.com/doc", ["example.com"])
    assert ok is True
    assert reason is None


def test_bare_host_subdomain_match_allows() -> None:
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("https://docs.example.com/path", ["example.com"])
    assert ok is True
    assert reason is None
    ok2, _ = v.is_allowed("https://a.b.example.com/path", ["example.com"])
    assert ok2 is True


def test_bare_host_look_alike_rejected() -> None:
    """``evilexample.com`` must NOT match allow-list ``example.com``."""
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("https://evilexample.com/poison", ["example.com"])
    assert ok is False
    assert reason == "domain_not_in_allowlist"


def test_bare_host_strips_port_and_userinfo() -> None:
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed("https://user:pass@docs.example.com:8443/x", ["example.com"])
    assert ok is True


def test_bare_host_leading_dot_means_subdomains_only() -> None:
    """Bot owner can write ``.example.com`` for "sub-domains, treat apex as
    sub-domain too" — leading dot is stripped, so it behaves same as
    ``example.com`` (exact + sub-domain). This is the existing prefix
    grammar's most natural extension."""
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed("https://docs.example.com/x", [".example.com"])
    assert ok is True
    ok2, _ = v.is_allowed("https://example.com/x", [".example.com"])
    assert ok2 is True


# ── URL prefix pattern ────────────────────────────────────────────────


def test_url_prefix_pattern_matches_path() -> None:
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed(
        "https://example.com/wiki/page-1",
        ["https://example.com/wiki/"],
    )
    assert ok is True


def test_url_prefix_pattern_rejects_other_path() -> None:
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed(
        "https://example.com/admin/secrets",
        ["https://example.com/wiki/"],
    )
    assert ok is False
    assert reason == "domain_not_in_allowlist"


def test_url_prefix_pattern_rejects_other_scheme() -> None:
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed(
        "http://example.com/wiki/page",
        ["https://example.com/wiki/"],
    )
    assert ok is False


# ── Regex pattern ─────────────────────────────────────────────────────


def test_regex_pattern_matches() -> None:
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed(
        "https://api.example.com/v1/docs",
        [r"re:^https?://[a-z]+\.example\.com/v\d+/"],
    )
    assert ok is True


def test_regex_pattern_rejects_non_match() -> None:
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed(
        "https://api.malicious.com/v1/docs",
        [r"re:^https?://[a-z]+\.example\.com/v\d+/"],
    )
    assert ok is False
    assert reason == "domain_not_in_allowlist"


def test_malformed_regex_alone_returns_pattern_invalid_reject() -> None:
    """When every configured pattern is a malformed regex we must fail
    closed (reject) so the bot owner notices the config bug rather than
    silently degrade to allow-all."""
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("https://example.com/x", ["re:[unclosed"])
    assert ok is False
    assert reason == "regex_pattern_invalid"


def test_malformed_regex_mixed_with_valid_falls_through() -> None:
    """A malformed regex must NOT short-circuit — the other patterns
    still get a chance to match."""
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed(
        "https://example.com/x",
        ["re:[unclosed", "example.com"],
    )
    assert ok is True


# ── Empty / malformed source_url ──────────────────────────────────────


def test_empty_source_url_with_nonempty_list_rejected() -> None:
    v = DomainAllowlistValidator()
    for empty in ("", "   ", "\t\n"):
        ok, reason = v.is_allowed(empty, ["example.com"])
        assert ok is False
        assert reason == "no_source_url"


def test_relative_path_url_with_host_list_rejected() -> None:
    """A relative path (no scheme/netloc) cannot match a bare-host or
    URL-prefix entry, so it rejects. ``malformed_url`` rather than
    ``domain_not_in_allowlist`` because the URL has no extractable host
    AND does not look like a scheme-prefixed URL prefix candidate."""
    v = DomainAllowlistValidator()
    ok, reason = v.is_allowed("/wiki/page", ["example.com"])
    assert ok is False
    assert reason == "malformed_url"


# ── Case insensitivity ────────────────────────────────────────────────


def test_case_insensitive_host_match() -> None:
    v = DomainAllowlistValidator()
    ok, _ = v.is_allowed("https://DOCS.EXAMPLE.COM/x", ["example.com"])
    assert ok is True
    ok2, _ = v.is_allowed("https://docs.example.com/x", ["EXAMPLE.COM"])
    assert ok2 is True


# ── Wire-level helper (document_service _maybe_validate_source_allowlist) ──


class _StubBotRepo:
    """Minimal repo stub returning a BotConfig-shaped object with
    plan_limits matching the test scenario."""

    def __init__(self, plan_limits: dict[str, Any] | None = None) -> None:
        self._plan_limits = plan_limits or {}
        self.calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []

    async def get_by_id(
        self,
        record_bot_id: uuid.UUID,
        *,
        record_tenant_id: uuid.UUID | None = None,
    ) -> Any:
        self.calls.append((record_bot_id, record_tenant_id))
        return SimpleNamespace(
            plan_limits=self._plan_limits,
            threshold_overrides={},
        )


class _StubBotRepoMissing:
    async def get_by_id(self, *_args: Any, **_kwargs: Any) -> Any:
        return None


class _StubBotRepoRaising:
    async def get_by_id(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("simulated DB outage")


class _StubConfigService:
    def __init__(self, flags: dict[str, Any] | None = None) -> None:
        self._flags = flags or {}

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self._flags.get(key, default))


class _StubConfigServiceRaising:
    async def get_bool(self, *_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("simulated redis outage")


@pytest.mark.asyncio
async def test_helper_passthrough_when_validator_missing() -> None:
    """No validator wired → silent passthrough (legacy DI not yet
    upgraded)."""
    await _maybe_validate_source_allowlist(
        "https://malicious.example.com/x",
        source_validator=None,
        bot_repo=_StubBotRepo({"allowed_source_domains": ("example.com",)}),
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_passthrough_when_flag_off() -> None:
    """Feature flag OFF → no validation runs even if list is populated."""
    await _maybe_validate_source_allowlist(
        "https://malicious.example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepo({"allowed_source_domains": ("example.com",)}),
        config_service=_StubConfigService({"source_allowlist_enabled": False}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_passthrough_when_list_empty() -> None:
    """Flag ON but per-bot list empty → bot owner hasn't opted in for
    this bot → allow-all."""
    await _maybe_validate_source_allowlist(
        "https://anywhere.example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepo({"allowed_source_domains": ()}),
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_rejects_when_flag_on_and_url_outside_list() -> None:
    with pytest.raises(SourceNotAllowedError) as ei:
        await _maybe_validate_source_allowlist(
            "https://malicious.example.com/poison",
            source_validator=DomainAllowlistValidator(),
            bot_repo=_StubBotRepo({"allowed_source_domains": ("trusted.com",)}),
            config_service=_StubConfigService(
                {"source_allowlist_enabled": True},
            ),
            record_bot_id=uuid.uuid4(),
            record_tenant_id=uuid.uuid4(),
        )
    # Reason payload encoded in the message for caller convenience.
    assert "domain_not_in_allowlist" in str(ei.value)


@pytest.mark.asyncio
async def test_helper_allows_when_flag_on_and_url_matches() -> None:
    # MUST NOT raise — bot owner expects this URL to pass.
    await _maybe_validate_source_allowlist(
        "https://api.trusted.com/v1/doc",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepo({"allowed_source_domains": ("trusted.com",)}),
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_graceful_degradation_on_config_failure() -> None:
    """Config-service throwing must NOT 5xx the ingest — degrade silent."""
    # Should NOT raise.
    await _maybe_validate_source_allowlist(
        "https://malicious.example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepo({"allowed_source_domains": ("trusted.com",)}),
        config_service=_StubConfigServiceRaising(),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_graceful_degradation_on_bot_lookup_failure() -> None:
    """bot_repo.get_by_id throwing must NOT 5xx the ingest."""
    await _maybe_validate_source_allowlist(
        "https://malicious.example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepoRaising(),
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_skips_when_bot_not_found() -> None:
    """bot_repo returning None → passthrough (legacy bots without a row
    after a hard delete still see ingest succeed)."""
    await _maybe_validate_source_allowlist(
        "https://malicious.example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=_StubBotRepoMissing(),
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_helper_rejects_when_source_url_empty_and_list_populated() -> None:
    """Bot owner has populated allow-list → an untagged manual paste
    (empty source_url) cannot be assigned a provenance and must be
    rejected with reason=no_source_url."""
    with pytest.raises(SourceNotAllowedError) as ei:
        await _maybe_validate_source_allowlist(
            "",
            source_validator=DomainAllowlistValidator(),
            bot_repo=_StubBotRepo({"allowed_source_domains": ("example.com",)}),
            config_service=_StubConfigService(
                {"source_allowlist_enabled": True},
            ),
            record_bot_id=uuid.uuid4(),
            record_tenant_id=uuid.uuid4(),
        )
    assert "no_source_url" in str(ei.value)


@pytest.mark.asyncio
async def test_helper_4key_isolation_uses_record_tenant_id() -> None:
    """The bot lookup MUST scope by ``record_tenant_id`` to honour the
    4-key identity rule — two tenants can independently set the same
    ``record_bot_id`` shape (collision is theoretical for UUID but the
    isolation invariant still has to be observable)."""
    record_tenant_id = uuid.uuid4()
    record_bot_id = uuid.uuid4()
    repo = _StubBotRepo({"allowed_source_domains": ("example.com",)})
    await _maybe_validate_source_allowlist(
        "https://example.com/x",
        source_validator=DomainAllowlistValidator(),
        bot_repo=repo,
        config_service=_StubConfigService({"source_allowlist_enabled": True}),
        record_bot_id=record_bot_id,
        record_tenant_id=record_tenant_id,
    )
    assert repo.calls == [(record_bot_id, record_tenant_id)], (
        "helper must call bot_repo.get_by_id(record_bot_id, "
        "record_tenant_id=...) for 4-key isolation"
    )
