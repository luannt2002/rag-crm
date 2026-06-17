"""Source URL Validator Strategy Port (T1-Safety).

Wraps per-bot source allow-list policy at the *ingest boundary*. Bot owner
configures a list of trusted domains / URL prefixes / regex patterns via
``bots.plan_limits.allowed_source_domains``. Documents whose ``source_url``
does NOT match are rejected before any chunking / embedding / persist work
runs — preventing prompt-injection or factual-poisoning attacks via
adversary-controlled URLs (PoisonedRAG arXiv 2402.07867 §3, 90% attack
success without filter).

Contract::

    is_allowed(source_url, allowed_patterns) -> tuple[bool, str | None]

Returns ``(True, None)`` for allow, ``(False, <reason>)`` for reject. The
reason string is logged into structlog + audit so operators can debug
"why was my doc rejected" without leaking the full URL into INFO logs.

Empty ``allowed_patterns`` means **allow-all** (legacy passthrough; the
feature is opt-in per-bot — bot owner has to populate the list to enable
filtering). This preserves backward compatibility for tenants who have
not yet configured the list.

Default implementation is :class:`NullSourceValidator` (passthrough). The
real allow-list logic lives in :class:`DomainAllowlistValidator` and is
selected via ``system_config.source_validator_provider``.

Proof citation: Zou et al. (2024) — "PoisonedRAG: Knowledge Corruption
Attacks to Retrieval-Augmented Generation of Large Language Models",
arXiv:2402.07867. Defence §6.1 — source allow-list filtering.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class SourceValidatorPort(Protocol):
    """Strategy interface for source-URL validators."""

    def is_allowed(
        self,
        source_url: str,
        allowed_patterns: Sequence[str],
    ) -> tuple[bool, str | None]:
        """Return ``(allowed, reason)``.

        @param source_url: URL of the inbound document (may be empty for
            manual paste; empty url falls under the "no_source_url" rule
            below).
        @param allowed_patterns: list of patterns sourced from the bot's
            ``plan_limits.allowed_source_domains``. Empty list = allow-all
            (feature opt-in default).
        @return: ``(True, None)`` when allowed, ``(False, reason)`` when
            rejected. ``reason`` is a short snake_case code (e.g.
            ``"domain_not_in_allowlist"``) suitable for structlog +
            audit. Implementations MUST NOT raise on malformed URL — log
            a warning + reject with ``reason="malformed_url"`` instead.
        """
        ...

    def get_provider_name(self) -> str:
        ...


__all__ = ["SourceValidatorPort"]
