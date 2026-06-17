"""Mega-sprint G3 (F3 NameError) regression — ``_maybe_validate_source_allowlist``.

Repro/guard for the live `NameError: name '_maybe_validate_source_allowlist'
is not defined` that fired at ``DocumentService.ingest()`` after a botched
merge stripped the helper while leaving its call site intact.

Three behavioural assertions (per CODER_AGENT_PROMPT_TEMPLATE.md "real
assertions, NOT ``assert True``"):

1. **defined**   — module exports the helper symbol; importing it MUST NOT
   ``NameError`` / ``ImportError``. This is the literal regression for G3.
2. **skip-no-url** — ``source_url=""`` is the manual-paste path; with no
   per-bot allow-list configured the helper MUST NOT raise.
3. **raise-not-in-allowlist** — feature flag ON + bot list populated +
   URL outside list MUST raise :class:`SourceNotAllowedError` so the
   ingest pipeline aborts BEFORE chunk/embed work runs (PoisonedRAG
   defence — arXiv 2402.07867 §6.1).

The exhaustive matrix (registry, regex, prefix, host, sub-domain,
case-insensitivity, graceful degradation) lives in
``tests/unit/test_source_allowlist.py``; this file is a *focused*
regression locking the three behaviours the F3 NameError stripped.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.document_service import (
    _maybe_validate_source_allowlist,
)
from ragbot.infrastructure.safety.domain_allowlist_validator import (
    DomainAllowlistValidator,
)
from ragbot.shared.errors import SourceNotAllowedError


class _StubBotRepo:
    """Minimal ``BotRepository`` stub returning a BotConfig-shaped object
    whose ``plan_limits`` mirrors the test scenario. Records the call so
    the test can assert 4-key isolation if needed."""

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


class _StubConfigService:
    def __init__(self, flags: dict[str, Any] | None = None) -> None:
        self._flags = flags or {}

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self._flags.get(key, default))


# ── 1) Regression for the NameError itself ───────────────────────────


def test_maybe_validate_source_allowlist_defined() -> None:
    """G3 root regression: ``DocumentService.ingest()`` calls this helper
    at the source-URL gate; if it goes missing again ingest crashes
    with ``NameError`` *after* the request has been queued — the most
    expensive failure mode. Lock the import."""
    assert callable(_maybe_validate_source_allowlist), (
        "_maybe_validate_source_allowlist must be a callable defined at "
        "module level of ragbot.application.services.document_service"
    )


# ── 2) Skip-path: empty source_url passes without raising ────────────


@pytest.mark.asyncio
async def test_skip_validation_if_no_url() -> None:
    """``source_url=""`` is the manual-paste / API-direct ingest path.
    With NO per-bot allow-list configured the helper is a no-op — the
    feature is opt-in, so the legacy behaviour (allow all empty-URL
    inserts) MUST be preserved."""
    repo = _StubBotRepo({"allowed_source_domains": ()})
    cfg = _StubConfigService({"source_allowlist_enabled": True})

    # Should NOT raise.
    await _maybe_validate_source_allowlist(
        "",
        source_validator=DomainAllowlistValidator(),
        bot_repo=repo,
        config_service=cfg,
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )

    # Helper went past the flag check (which would otherwise return
    # immediately) and consulted the bot repo, then bailed because the
    # per-bot list was empty. The single call proves the early-exit
    # ordering: flag-on + repo-consult + empty-list-passthrough.
    assert len(repo.calls) == 1, (
        "helper must consult bot_repo exactly once when flag is on; "
        "actual call count: %d" % len(repo.calls)
    )


# ── 3) Hard-reject path: URL outside allow-list raises ───────────────


@pytest.mark.asyncio
async def test_raise_if_url_not_in_allowlist() -> None:
    """Two-knob opt-in fully wired: feature flag ON + per-bot list
    populated. URL outside the list MUST raise SourceNotAllowedError
    so the rest of the ingest pipeline (chunking / embedding /
    persistence) is never invoked on adversary-controlled content."""
    repo = _StubBotRepo({"allowed_source_domains": ("trusted.com",)})
    cfg = _StubConfigService({"source_allowlist_enabled": True})

    with pytest.raises(SourceNotAllowedError) as exc_info:
        await _maybe_validate_source_allowlist(
            "https://malicious.example.com/poison",
            source_validator=DomainAllowlistValidator(),
            bot_repo=repo,
            config_service=cfg,
            record_bot_id=uuid.uuid4(),
            record_tenant_id=uuid.uuid4(),
        )

    # The rejection reason is encoded in the error message so audit /
    # operators can branch on it without re-running the validator.
    assert "domain_not_in_allowlist" in str(exc_info.value), (
        "raised SourceNotAllowedError must surface the validator's "
        "snake_case reason in its message; got: %s" % exc_info.value
    )
    # Stable error code matches the API envelope contract — anything
    # else means the wrong exception subclass was raised.
    assert exc_info.value.code == "SOURCE_NOT_ALLOWED"
